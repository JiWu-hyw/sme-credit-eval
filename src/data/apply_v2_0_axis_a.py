# -*- coding: utf-8 -*-
"""把 schema 从 v1.9 升到 v2.0：A 轴司法违约 + 主要人员并列层。

数据来源：_data_collect/out/
  - A轴_司法违约明细.csv         34 行 / 6 户（失信 1 + 被执行 1 + 终本 32）
  - 高管_主要人员明细.csv        2569 行，含 490 行「每户表头」脏行 → 清洗后 2079 行 / 490 户
  - 合并_500家_企业主表.csv      500 行，本轮新增 17 列原始快照

纪律：只新增 3 张表 + 4 个视图，严格不改 ads_label / v_enterprise_label_matrix /
      v_enterprise_multilabel_grade / v_model_train_ready / dim_enterprise 任何列。
      复合负面事件以 *并列候选* 视图表达，正式切换需另一次升版拍板。

幂等：重复执行先 DROP 本轮对象再重建，数据按同一份 CSV 全量重灌；
      meta 登记按固定键 REPLACE；schema.sql 的 [V20AXISA] 块幂等替换。
执行前自动留一份升级前快照 data/db/zjtech_credit_pre_v20.db 作回滚点。

用法：
    python -m src.data.apply_v2_0_axis_a            # 执行迁移
    python -m src.data.apply_v2_0_axis_a --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_v20.db")
SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "migrations", "v2_0_axis_a.sql")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
COLLECT = os.path.join(os.path.dirname(ROOT), "_data_collect", "out")

JUD_CSV = os.path.join(COLLECT, "A轴_司法违约明细.csv")
PERSON_CSV = os.path.join(COLLECT, "高管_主要人员明细.csv")
MAIN_CSV = os.path.join(COLLECT, "合并_500家_企业主表.csv")

VERSION = "v2.0"
BEGIN = "-- [V20AXISA:BEGIN]"
END = "-- [V20AXISA:END]"
NOTE = (
    "v2.0 A 轴司法违约 + 主要人员并列层（企查查批量查询回传，零新增采购）："
    "① 新增 ods_qcc_judicial（34 条 / 6 户：失信 1、被执行 1、终本 32）、"
    "ods_qcc_person（主要人员清洗后 2079 行 / 490 户）、"
    "ods_qcc_axis_a_snapshot（用户主表本轮 17 列原始快照 500 行）三张表；"
    "② 清洗三处口径问题：主要人员原始 2569 行含 490 行表头脏行已剔除（用户主表"
    "「高管_人数」仍为未清洗口径，保留快照对照）；三类金额语义不同（涉案金额 / "
    "执行标的 / 未履行金额）分列保存并带 amount_kind，禁止无条件相加；1 条 '-' "
    "日期清洗为 NULL，1 户企业名经别名归一映射；"
    "③ 新增 v_axis_a_judicial / v_enterprise_person_summary / "
    "v_axis_a_compound_event / v_axis_a_grade_cross 四个并列视图，"
    "复合负面事件为 *候选* 锚点；"
    "④ 严格未改 ads_label、v_enterprise_label_matrix、"
    "v_enterprise_multilabel_grade、v_model_train_ready、dim_enterprise 任何列，"
    "未改 29 列主宽表、未改 ASOF_VER、未改 DEFINITION_VER；"
    "正式把司法事件纳入评级需另一次升版拍板。"
)

TABLES = ("ods_qcc_judicial", "ods_qcc_person", "ods_qcc_axis_a_snapshot")
VIEWS = ("v_axis_a_judicial", "v_enterprise_person_summary",
         "v_axis_a_compound_event", "v_axis_a_grade_cross")

# 迁移前实测基线，迁移后必须逐项不变（守「已发布口径未动」）
EXPECT = {
    "judicial_rows": 34,
    "judicial_firms": 6,
    "judicial_by_type": {"失信被执行人": 1, "被执行人": 1, "终本案件": 32},
    "person_rows_clean": 2079,
    "person_rows_raw": 2569,
    "person_header_dirty": 490,
    "person_firms": 490,
    "snapshot_rows": 500,
    "ads_label": (307, 21, 286),
    "grades": {"A 引领": 81, "B 优质": 176, "C 稳健": 157,
               "D 观察": 83, "E 预警": 3},
    "compound_adverse": 6,       # v1 硬结果 3 家 + 纯司法新增 3 家
    "new_e_candidate": 3,       # 原本不在 E 档、复合事件判坏的企业
}


def norm(x: str) -> str:
    """企业名归一：去全半角括号 / 中点 / 空白。"""
    return re.sub(r"[（()）·\s]", "", str(x or ""))


def sql_block() -> str:
    with open(SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0:
        raise RuntimeError("迁移 SQL 缺少 [V20AXISA] 标记")
    return text[i:j + len(END)] + "\n"


def sync_schema_sql() -> None:
    block = sql_block()
    with open(SCHEMA_SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i >= 0 and j >= 0:
        text = text[:i] + block.rstrip("\n") + text[j + len(END):]
    else:
        text = text.rstrip("\n") + "\n\n" + block
    with open(SCHEMA_SQL, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(x) -> float | None:
    s = str(x or "").strip()
    if s in ("", "NAN", "-", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build_alias_map(main: list[dict], con: sqlite3.Connection) -> dict[str, str]:
    """归一企业名 → dim_enterprise.enterprise_id。"""
    ent = {norm(r[0]): r[1] for r in con.execute(
        "SELECT name_display, enterprise_id FROM dim_enterprise")}
    out = dict(ent)
    for r in main:
        n = r["企业名称"]
        eid = ent.get(norm(n))
        if not eid:
            continue
        for f in ("QCC_现用名称", "QCC_曾用名"):
            v = (r.get(f) or "").strip()
            if v and v != "NAN":
                for part in re.split(r"[;；、\n]", v):
                    if len(part.strip()) >= 6:
                        out.setdefault(norm(part.strip()), eid)
    return out


def batch_names(work_dir: str) -> dict[str, str]:
    """按关键字找企查查回传 zip 文件名，作为 source_batch。"""
    out = {}
    try:
        files = os.listdir(work_dir)
    except OSError:
        return out
    for key, kind in (("失信被执行人", "失信被执行人"), ("被执行人", "被执行人"),
                      ("终本案件", "终本案件"), ("主要人员", "主要人员")):
        hit = sorted(f for f in files if f.endswith(".zip") and key in f
                     and (key != "被执行人" or "失信被执行人" not in f))
        if hit:
            out[kind] = hit[0]
    return out


AMOUNT_KIND = {"失信被执行人": "涉案金额", "被执行人": "执行标的",
               "终本案件": "未履行金额"}
SNAPSHOT_COLS = [
    ("A轴_失信被执行人_条数", "n_dishonest_raw", "int"),
    ("A轴_被执行人_条数", "n_executed_raw", "int"),
    ("A轴_终本案件_条数", "n_terminal_raw", "int"),
    ("A轴_未履行金额_万元", "amount_wan_raw", "float"),
    ("A轴_是否有违约事件", "has_event_raw", "enum_yesno"),
    ("A轴_违约类型", "event_types_raw", "str"),
    ("A轴_执行法院数", "n_court_raw", "int"),
    ("A轴_案号样例", "case_sample_raw", "str"),
    ("高管_人数", "n_person_raw", "int"),
    ("高管_姓名", "person_names_raw", "str"),
    ("高管_董事长数", "n_chairman_raw", "int"),
    ("高管_董事数", "n_director_raw", "int"),
    ("高管_监事数", "n_supervisor_raw", "int"),
    ("高管_经理数", "n_manager_raw", "int"),
    ("高管_总经理数", "n_gm_raw", "int"),
    ("高管_财务负责人数", "n_cfo_raw", "int"),
    ("高管_法人是否在列", "legal_in_list_raw", "enum_yesno_nan"),
]


def cast(v, kind):
    s = str(v or "").strip()
    if s in ("", "NAN", "-"):
        return None
    if kind == "int":
        x = num(s)
        return int(x) if x is not None else None
    if kind == "float":
        return num(s)
    if kind == "enum_yesno":
        return s if s in ("是", "否") else None
    if kind == "enum_yesno_nan":
        return s if s in ("是", "否", "NAN") else None
    return s


def load(con: sqlite3.Connection, built_at: str) -> dict[str, int]:
    main, jud, ppl = read_csv(MAIN_CSV), read_csv(JUD_CSV), read_csv(PERSON_CSV)
    alias = build_alias_map(main, con)
    batches = batch_names(os.path.join(os.path.dirname(ROOT), "_data_collect",
                                       "raw", "qcc_return"))
    legal = {r[0]: r[1] for r in con.execute(
        "SELECT enterprise_id, legal_rep FROM dim_enterprise")}

    # ---- 1) 司法违约明细 ----
    jrows, jmiss = [], []
    for r in jud:
        eid = alias.get(norm(r["企业名称"]))
        if not eid:
            jmiss.append(r["企业名称"])
            continue
        d = r["日期"].strip()
        jrows.append((
            eid, r["企业名称"].strip(), r["类别"].strip(),
            r["案号"].strip() or None, r["执行法院"].strip() or None,
            num(r["金额_元"]), AMOUNT_KIND.get(r["类别"].strip()),
            d if d not in ("-", "", "NAN") else None,
            r["申请执行人"].strip() or None,
            batches.get(r["类别"].strip(), "qcc_return_20261111"), built_at,
        ))
    if jmiss:
        raise RuntimeError("司法明细有企业无法映射：%r" % sorted(set(jmiss))[:5])
    con.executemany(
        "INSERT INTO ods_qcc_judicial "
        "(enterprise_id, company_name, event_type, case_no, court, amount_yuan, "
        " amount_kind, event_date, applicant, source_batch, built_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", jrows)

    # ---- 2) 主要人员（剔除表头脏行） ----
    prows, dirty, pmiss = [], 0, []
    for r in ppl:
        nm, pos = (r.get("姓名") or "").strip(), (r.get("职务") or "").strip()
        if nm in ("", "姓名", "序号") or pos in ("", "职务"):
            dirty += 1
            continue
        eid = alias.get(norm(r["企业名称"]))
        if not eid:
            pmiss.append(r["企业名称"])
            continue
        prows.append((
            eid, r["企业名称"].strip(), nm, pos or None,
            1 if legal.get(eid) and legal.get(eid) == nm else 0,
            batches.get("主要人员", "qcc_return_20261111"), built_at,
        ))
    if pmiss:
        raise RuntimeError("人员明细有企业无法映射：%r" % sorted(set(pmiss))[:5])
    con.executemany(
        "INSERT INTO ods_qcc_person "
        "(enterprise_id, company_name, person_name, position, is_legal_rep, "
        " source_batch, built_at) VALUES (?,?,?,?,?,?,?)", prows)

    # ---- 3) 用户主表新增 17 列原始快照 ----
    srows = []
    for r in main:
        eid = alias.get(norm(r["企业名称"]))
        if not eid:
            raise RuntimeError("主表企业无法映射：%s" % r["企业名称"])
        vals = [cast(r.get(src), kind) for src, _, kind in SNAPSHOT_COLS]
        srows.append((eid, r["企业名称"].strip(), *vals,
                      "合并_500家_企业主表.csv", built_at))
    col_names = ", ".join(c for _, c, _ in SNAPSHOT_COLS)
    marks = ", ".join("?" for _ in range(len(SNAPSHOT_COLS) + 4))
    con.executemany(
        "INSERT INTO ods_qcc_axis_a_snapshot "
        "(enterprise_id, company_name, %s, source_file, built_at) VALUES (%s)"
        % (col_names, marks), srows)

    return {"judicial_rows": len(jrows), "person_rows_clean": len(prows),
            "person_rows_raw": len(ppl), "person_header_dirty": dirty,
            "snapshot_rows": len(srows),
            "judicial_by_type": dict(Counter(x[2] for x in jrows))}


def register_meta(con: sqlite3.Connection, built_at: str, stat: dict) -> None:
    # UPSERT 而非 REPLACE：REPLACE 会先 DELETE，触发 meta_ingest_log 的外键约束
    con.execute(
        "INSERT INTO meta_data_source "
        "(source_id, source_name, source_type, url, license, fetch_mode, cost_cny, "
        " quota_note, owner, coverage, first_fetch, last_fetch, is_active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "source_name=excluded.source_name, coverage=excluded.coverage, "
        "last_fetch=excluded.last_fetch, quota_note=excluded.quota_note",
        ("qcc_judicial_person",
         "企查查批量查询回传·失信被执行人 / 被执行人 / 终本案件 / 主要人员",
         "market_api", "_data_collect/raw/qcc_return/*.zip（4 份回传包）",
         "commercial", "manual", 0.0,
         "2026-11-11 回传 4 份 zip；本轮零新增采购；分发口径 2026-11-12 定稿为全部公开，随完整库原样发布",
         "施汶邑", "A 轴司法违约 6 户 / 主要人员 490 户",
         "2026-11-11", "2026-11-11", 1))
    for lid, target, rows_in, rows_ok, rows_bad, msg in (
        (9001, "ods_qcc_judicial", stat["judicial_rows"], stat["judicial_rows"], 0,
         "34 条 / 6 户；三类金额分列保存；1 条 '-' 日期清洗为 NULL；1 户经别名归一映射"),
        (9002, "ods_qcc_person+ods_qcc_axis_a_snapshot",
         stat["person_rows_raw"], stat["person_rows_clean"] + stat["snapshot_rows"],
         stat["person_header_dirty"],
         "主要人员 2569 行剔除 490 行表头脏行后入库 2079 行；另存主表 17 列原始快照 500 行"),
    ):
        con.execute(
            "INSERT OR REPLACE INTO meta_ingest_log "
            "(log_id, source_id, stage, target_table, file_path, file_sha256, "
            " rows_in, rows_ok, rows_bad, started_at, finished_at, status, message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lid, "qcc_judicial_person", "parse", target,
             "_data_collect/out/A轴_司法违约明细.csv + 高管_主要人员明细.csv",
             None, rows_in, rows_ok, rows_bad, built_at, built_at,
             "warn" if rows_bad else "ok", msg))
    con.execute("DELETE FROM meta_schema_version WHERE version=?", (VERSION,))
    con.execute(
        "INSERT INTO meta_schema_version (version, applied_at, author, note) "
        "VALUES (?,?,?,?)", (VERSION, built_at, "WorkBuddy(施汶邑)", NOTE))


def check(con: sqlite3.Connection, verify_schema: bool = True) -> list[str]:
    """只读校验：新增对象齐备、清洗正确、已发布口径逐项未动。"""
    bad: list[str] = []
    cur = con.cursor()
    for t in TABLES:
        if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (t,)).fetchone():
            return ["%s 表不存在" % t]
    for v in VIEWS:
        if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
                           (v,)).fetchone():
            return ["%s 视图不存在" % v]

    n = {t: cur.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in TABLES}
    if n["ods_qcc_judicial"] != EXPECT["judicial_rows"]:
        bad.append("司法明细 %d 行，期望 %d" % (n["ods_qcc_judicial"], EXPECT["judicial_rows"]))
    if n["ods_qcc_person"] != EXPECT["person_rows_clean"]:
        bad.append("人员明细 %d 行，期望 %d" % (n["ods_qcc_person"], EXPECT["person_rows_clean"]))
    if n["ods_qcc_axis_a_snapshot"] != EXPECT["snapshot_rows"]:
        bad.append("主表快照 %d 行，期望 %d" % (n["ods_qcc_axis_a_snapshot"], EXPECT["snapshot_rows"]))

    by = dict(cur.execute(
        "SELECT event_type, COUNT(*) FROM ods_qcc_judicial GROUP BY 1"))
    if by != EXPECT["judicial_by_type"]:
        bad.append("司法类别分布漂移：%r" % by)
    if cur.execute("SELECT COUNT(*) FROM ods_qcc_judicial "
                   "WHERE enterprise_id IS NULL OR amount_kind IS NULL").fetchone()[0]:
        bad.append("司法明细存在未映射企业或缺失金额口径")
    if cur.execute("SELECT COUNT(*) FROM ods_qcc_judicial WHERE event_date = '-'").fetchone()[0]:
        bad.append("司法明细 '-' 日期未清洗")
    if cur.execute("SELECT COUNT(*) FROM ods_qcc_person "
                   "WHERE person_name IN ('姓名','序号') OR position = '职务'").fetchone()[0]:
        bad.append("人员明细表头脏行未剔除")
    if cur.execute("SELECT COUNT(*) FROM ods_qcc_person p "
                   "LEFT JOIN dim_enterprise d USING(enterprise_id) "
                   "WHERE d.enterprise_id IS NULL").fetchone()[0]:
        bad.append("人员明细存在孤儿 enterprise_id")
    if cur.execute("SELECT COUNT(*) FROM v_axis_a_judicial").fetchone()[0] != 500:
        bad.append("司法聚合视图不是 500 行")
    if cur.execute("SELECT COUNT(*) FROM v_enterprise_person_summary "
                   "WHERE n_person_clean > 0").fetchone()[0] != EXPECT["person_firms"]:
        bad.append("人员覆盖户数漂移")

    adv = cur.execute("SELECT COUNT(*) FROM v_axis_a_compound_event "
                      "WHERE y_compound_v2 = 0").fetchone()[0]
    if adv != EXPECT["compound_adverse"]:
        bad.append("复合负面事件 %d 家，期望 %d" % (adv, EXPECT["compound_adverse"]))
    cand = cur.execute("SELECT SUM(n_new_e_candidate) FROM v_axis_a_grade_cross").fetchone()[0]
    if cand != EXPECT["new_e_candidate"]:
        bad.append("新增 E 档候选 %s 家，期望 %d" % (cand, EXPECT["new_e_candidate"]))

    lab = tuple(cur.execute(
        "SELECT COUNT(*), SUM(label_value=1), SUM(label_value=0) FROM ads_label").fetchone())
    if lab != EXPECT["ads_label"]:
        bad.append("ads_label 被改动：%r" % (lab,))
    grades = dict(cur.execute(
        "SELECT grade_multilabel, COUNT(*) FROM v_enterprise_multilabel_grade GROUP BY 1"))
    if grades != EXPECT["grades"]:
        bad.append("已发布五档被改动：%r" % grades)
    if cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view' "
                   "AND name='v_enterprise_label_matrix'").fetchone()[0] != 1:
        bad.append("v_enterprise_label_matrix 缺失")

    vers = [r[0] for r in cur.execute("SELECT version FROM meta_schema_version")]
    if VERSION not in vers:
        bad.append("meta_schema_version 未登记 %s" % VERSION)
    if cur.execute("SELECT COUNT(*) FROM meta_data_source WHERE source_id="
                   "'qcc_judicial_person'").fetchone()[0] != 1:
        bad.append("meta_data_source 未登记 qcc_judicial_person")
    if verify_schema and sql_block() not in open(SCHEMA_SQL, encoding="utf-8").read():
        bad.append("schema.sql 未同步 [V20AXISA] DDL 块")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读校验，不改库")
    args = ap.parse_args()

    if args.check:
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        bad = check(con)
        con.close()
        print("只读校验：%s" % ("通过" if not bad else "失败"))
        for b in bad:
            print("  x", b)
        return 0 if not bad else 1

    for p in (JUD_CSV, PERSON_CSV, MAIN_CSV):
        if not os.path.exists(p):
            print("缺数据源：%s" % p)
            return 1
    if not os.path.exists(BACKUP):
        shutil.copy2(DB, BACKUP)
        print("[backup] 升级前快照 -> %s" % BACKUP)

    built_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(DB)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        # 幂等：先删本轮对象（视图先于表）
        for v in reversed(list(VIEWS)):
            con.execute("DROP VIEW IF EXISTS %s" % v)
        for t in TABLES:
            con.execute("DROP TABLE IF EXISTS %s" % t)
        with open(SQL, encoding="utf-8") as f:
            con.executescript(f.read())
        stat = load(con, built_at)
        register_meta(con, built_at, stat)
        con.commit()
        # 文件同步在库校验通过之后，故本次校验先跳过 schema.sql 项
        bad = check(con, verify_schema=False)
        if bad:
            print("校验失败（DDL 已由 executescript 提交，请从备份回滚）：")
            for b in bad:
                print("  x", b)
            return 1
        sync_schema_sql()
        print("迁移完成 %s" % VERSION)
        print("  司法明细 %d 行 / %d 户 %r" % (
            stat["judicial_rows"],
            con.execute("SELECT COUNT(DISTINCT enterprise_id) FROM ods_qcc_judicial").fetchone()[0],
            stat["judicial_by_type"]))
        print("  主要人员 原始 %d → 清洗 %d（剔除 %d 表头脏行）/ %d 户" % (
            stat["person_rows_raw"], stat["person_rows_clean"],
            stat["person_header_dirty"],
            con.execute("SELECT COUNT(DISTINCT enterprise_id) FROM ods_qcc_person").fetchone()[0]))
        print("  主表快照 %d 行；复合负面事件 %d 家，其中 %d 家原本不在 E 档" % (
            stat["snapshot_rows"],
            con.execute("SELECT COUNT(*) FROM v_axis_a_compound_event "
                        "WHERE y_compound_v2=0").fetchone()[0],
            con.execute("SELECT SUM(n_new_e_candidate) FROM v_axis_a_grade_cross").fetchone()[0]))
        print("  schema.sql 已同步 [V20AXISA] 块")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
