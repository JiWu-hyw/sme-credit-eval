# -*- coding: utf-8 -*-
"""把 schema 从 v2.0 升到 v2.1：商标明细 + 产品节点并列层。

数据来源：_data_collect/out/
  - 商标明细.csv                 8004 条 / 444 家 / 尼斯 1–45 类
  - 产品节点_尼斯分类.csv         45 行（35 产品类 + 10 服务类）
  - 合并_500家_企业主表.csv       500 行，用于企业名称桥接与 9 列原始口径对照

纪律：与 v2.0 同构，**只新增 3 张表 + 4 个视图**。
      产品节点是「尼斯核定类别」粒度，**不并入 ads_graph_node 主图**，
      29 帧年度时序切面 / 图结构契约 / 守恒校验 / 任何标签与已发布视图逐项不动。
      立项六类实体只能登记为「类别粒度部分闭合，SKU 级仍缺」。

幂等：重复执行先 DROP 本轮对象再重建，数据按同一份 CSV 全量重灌；
      meta 登记按固定键 UPSERT；schema.sql 的 [V21PRODUCT] 块幂等替换。
执行前自动留一份升级前快照 data/db/zjtech_credit_pre_v21.db 作回滚点。

用法：
    python -m src.data.apply_v2_1_product_node            # 执行迁移
    python -m src.data.apply_v2_1_product_node --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime

from src.data.apply_v2_0_axis_a import build_alias_map, norm, read_csv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_v21.db")
SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "migrations", "v2_1_product_node.sql")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
COLLECT = os.path.join(os.path.dirname(ROOT), "_data_collect", "out")

TM_CSV = os.path.join(COLLECT, "商标明细.csv")
PRODUCT_CSV = os.path.join(COLLECT, "产品节点_尼斯分类.csv")
MAIN_CSV = os.path.join(COLLECT, "合并_500家_企业主表.csv")

VERSION = "v2.1"
BEGIN = "-- [V21PRODUCT:BEGIN]"
END = "-- [V21PRODUCT:END]"
NOTE = (
    "v2.1 商标明细 + 产品节点并列层（企查查批量查询回传，零新增采购）："
    "① 新增 ods_qcc_trademark（8004 条 / 444 家 / 尼斯 1–45 类）、"
    "dim_product_nice_class（45 行＝35 产品类 + 10 服务类）、"
    "bridge_enterprise_product（1418 行 / 433 户 / 6102 条产品类商标）三张表；"
    "② 清洗：1 条申请日期 '-'、2245 条注册公告日期 '-' 置 NULL；"
    "商标状态 39 种保留原文，另给 is_registered（以「已注册」开头，合计 5257，"
    "与用户主表「商标_有效件数」守恒）；企业名称经主表 QCC 现用名/曾用名桥接，"
    "444 家 100% 命中 dim_enterprise，company_name 保留回传原文；"
    "③ 新增 v_product_node（35 行）/ v_enterprise_trademark_summary（500 行）/ "
    "v_six_entity_coverage（6 行）/ v_product_grade_cross 四个并列视图；"
    "④ 口径纠正：产品节点为尼斯 35 类而非 45 类（35–39、41–45 十个服务类"
    "虽有 1902 条商标但不作产品节点）、非 SKU 级产品；六类实体覆盖里"
    "企查查主要人员清洗后为 2079 人 / 490 户（原 xlsx 写 2569 含 490 行表头脏行），"
    "且它不是图节点（图内人员＝发明人 inventor）；"
    "⑤ 严格未改 ads_graph_node / ads_graph_edge / v_graph_slice_annual*，"
    "产品节点**未并入主图**，29 帧时序切面与守恒校验不动；"
    "未改 ads_label、v_enterprise_label_matrix、v_enterprise_multilabel_grade、"
    "v_model_train_ready*、dim_enterprise 任何列，未改 ASOF_VER / DEFINITION_VER；"
    "⑥ 主表「商标_品牌数」与明细精确去重有 68 家口径差异，库内一律以贴源明细"
    "重算 n_brand_exact，主表 9 列只作外部对照，禁止混用。"
)

TABLES = ("ods_qcc_trademark", "dim_product_nice_class",
          "bridge_enterprise_product")
VIEWS = ("v_product_node", "v_enterprise_trademark_summary",
         "v_six_entity_coverage", "v_product_grade_cross")

# 迁移前实测基线，迁移后必须逐项不变（守「已发布口径未动」）
EXPECT = {
    "trademark_rows": 8004,
    "trademark_firms": 444,
    "nice_class_rows": 45,
    "product_nodes": 35,
    "service_classes": 10,
    "bridge_rows": 1418,
    "bridge_firms": 433,
    "bridge_trademarks": 6102,
    "product_trademarks": 6102,
    "service_trademarks": 1902,
    "registered_total": 5257,
    "apply_date_null": 1,
    "reg_announce_null": 2245,
    "ads_label": (307, 21, 286),
    "grades": {"A 引领": 81, "B 优质": 176, "C 稳健": 157,
               "D 观察": 83, "E 预警": 3},
}


def sql_block() -> str:
    with open(SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0:
        raise RuntimeError("迁移 SQL 缺少 [V21PRODUCT] 标记")
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


def clean_date(x: str) -> str | None:
    s = str(x or "").strip()
    return None if s in ("", "-", "NAN", "None") else s


def parse_class(x: str) -> int:
    m = re.match(r"^\s*(\d+)\s*类", str(x or "").strip())
    if not m:
        raise RuntimeError("无法解析尼斯分类：%r" % x)
    return int(m.group(1))


def load(con: sqlite3.Connection, built_at: str) -> dict[str, int]:
    main, tm, prod = read_csv(MAIN_CSV), read_csv(TM_CSV), read_csv(PRODUCT_CSV)
    alias = build_alias_map(main, con)

    # ---- 1) 尼斯分类维表：45 行，35 产品类 + 10 服务类 ----
    prod_rows, product_classes = [], set()
    for p in prod:
        k = int(p["尼斯分类号"])
        is_service = 1 if p["是否服务类"].strip() == "是" else 0
        is_node = 0 if is_service else 1
        if is_node:
            product_classes.add(k)
        prod_rows.append((
            p["product_id"].strip(), p["product_name"].strip(), k,
            p["类别名"].strip(), int(p["覆盖企业数"]), is_service, is_node,
            "service" if is_service else "product", (p.get("说明") or "").strip(),
        ))
    con.executemany(
        "INSERT INTO dim_product_nice_class "
        "(product_id, product_name, nice_class, class_name, covered_firms_stated, "
        " is_service, is_product_node, class_kind, note) VALUES (?,?,?,?,?,?,?,?,?)",
        prod_rows)

    # ---- 2) 商标贴源明细：8004 行 ----
    tm_rows, miss = [], []
    for r in tm:
        nm = r["企业名称"].strip()
        eid = alias.get(norm(nm))
        if not eid:
            miss.append(nm)
            continue
        k = parse_class(r["国际分类"])
        status = r["商标状态"].strip()
        tm_rows.append((
            eid, nm, (r["商标名称"] or "").strip() or None,
            k, (r["国际分类"] or "").strip(),
            "product" if k in product_classes else "service",
            status, 1 if status.startswith("已注册") else 0,
            (r["申请/注册号"] or "").strip(),
            clean_date(r["申请日期"]), clean_date(r["注册公告日期"]),
            "商标明细.csv", built_at,
        ))
    if miss:
        raise RuntimeError("商标明细有企业无法映射：%r" % sorted(set(miss))[:5])
    con.executemany(
        "INSERT INTO ods_qcc_trademark "
        "(enterprise_id, company_name, trademark_name, nice_class, nice_class_raw, "
        " class_kind, trademark_status, is_registered, reg_no, apply_date, "
        " reg_announce_date, source_file, built_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tm_rows)

    # ---- 3) 企业—产品类桥接：只保留 35 个产品类 ----
    agg = defaultdict(lambda: [0, 0, None])
    for row in tm_rows:
        k, eid, is_reg, d = row[3], row[0], row[7], row[9]
        if k not in product_classes:
            continue
        a = agg[(eid, k)]
        a[0] += 1
        a[1] += is_reg
        if d and (a[2] is None or d < a[2]):
            a[2] = d
    pid_by_class = {p[2]: p[0] for p in prod_rows if p[6] == 1}
    bridge_rows = [(eid, pid_by_class[k], k, n, nr, first, built_at)
                   for (eid, k), (n, nr, first) in sorted(agg.items())]
    con.executemany(
        "INSERT INTO bridge_enterprise_product "
        "(enterprise_id, product_id, nice_class, n_trademark, n_registered, "
        " first_apply_date, built_at) VALUES (?,?,?,?,?,?,?)", bridge_rows)

    return {
        "trademark_rows": len(tm_rows),
        "nice_class_rows": len(prod_rows),
        "bridge_rows": len(bridge_rows),
        "trademark_firms": len({x[0] for x in tm_rows}),
        "bridge_firms": len({x[0] for x in bridge_rows}),
        "bridge_trademarks": sum(x[3] for x in bridge_rows),
        "registered_total": sum(x[7] for x in tm_rows),
        "product_trademarks": sum(1 for x in tm_rows if x[5] == "product"),
        "service_trademarks": sum(1 for x in tm_rows if x[5] == "service"),
    }


def register_meta(con: sqlite3.Connection, built_at: str, stat: dict) -> None:
    con.execute(
        "INSERT INTO meta_data_source "
        "(source_id, source_name, source_type, url, license, fetch_mode, cost_cny, "
        " quota_note, owner, coverage, first_fetch, last_fetch, is_active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "source_name=excluded.source_name, coverage=excluded.coverage, "
        "last_fetch=excluded.last_fetch, quota_note=excluded.quota_note",
        ("qcc_trademark",
         "企查查批量查询回传·商标明细（尼斯分类 1–45 类）",
         "market_api", "_data_collect/out/商标明细.csv + 产品节点_尼斯分类.csv",
         "commercial", "manual", 0.0,
         "2026-11-12 回传；本轮零新增采购，服务条款与公开分发范围待确认",
         "施汶邑", "商标 8004 条 / 444 家；产品类节点 35 个 / 433 户",
         "2026-11-12", "2026-11-12", 1))
    for lid, target, rows_in, rows_ok, rows_bad, msg in (
        (9101, "ods_qcc_trademark", stat["trademark_rows"], stat["trademark_rows"], 0,
         "8004 条 / 444 家；1 条申请日期 '-' 与 2245 条注册公告日期 '-' 清洗为 NULL；"
         "is_registered 口径合计 5257，与主表「商标_有效件数」守恒"),
        (9102, "dim_product_nice_class+bridge_enterprise_product",
         stat["nice_class_rows"] + stat["trademark_rows"],
         stat["nice_class_rows"] + stat["bridge_rows"], 0,
         "45 个尼斯类＝35 产品类 + 10 服务类；服务类 1902 条商标不作产品节点；"
         "桥接 1418 行 / 433 户 / 6102 条产品类商标；产品节点未并入 ads_graph_node"),
    ):
        con.execute(
            "INSERT OR REPLACE INTO meta_ingest_log "
            "(log_id, source_id, stage, target_table, file_path, file_sha256, "
            " rows_in, rows_ok, rows_bad, started_at, finished_at, status, message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lid, "qcc_trademark", "parse", target,
             "_data_collect/out/商标明细.csv + 产品节点_尼斯分类.csv",
             None, rows_in, rows_ok, rows_bad, built_at, built_at, "ok", msg))
    con.execute("DELETE FROM meta_schema_version WHERE version=?", (VERSION,))
    con.execute(
        "INSERT INTO meta_schema_version (version, applied_at, author, note) "
        "VALUES (?,?,?,?)", (VERSION, built_at, "WorkBuddy(施汶邑)", NOTE))


def check(con: sqlite3.Connection, verify_schema: bool = True) -> list[str]:
    """只读校验：新增对象齐备、清洗正确、已发布口径逐项未动、产品节点未入主图。"""
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
    for key, tab in (("trademark_rows", "ods_qcc_trademark"),
                     ("nice_class_rows", "dim_product_nice_class"),
                     ("bridge_rows", "bridge_enterprise_product")):
        if n[tab] != EXPECT[key]:
            bad.append("%s %d 行，期望 %d" % (tab, n[tab], EXPECT[key]))

    if cur.execute("SELECT COUNT(*) FROM ods_qcc_trademark WHERE enterprise_id IS NULL "
                   "OR reg_no IS NULL OR reg_no=''").fetchone()[0]:
        bad.append("商标明细存在未映射企业或注册号空缺")
    if cur.execute("SELECT COUNT(*) FROM ods_qcc_trademark WHERE apply_date='-' "
                   "OR reg_announce_date='-'").fetchone()[0]:
        bad.append("商标明细 '-' 日期未清洗为 NULL")
    for key, col, val in (("apply_date_null", "apply_date", EXPECT["apply_date_null"]),
                          ("reg_announce_null", "reg_announce_date",
                           EXPECT["reg_announce_null"])):
        got = cur.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                          "WHERE %s IS NULL" % col).fetchone()[0]
        if got != val:
            bad.append("%s NULL 数 %d，期望 %d" % (col, got, val))
    reg = cur.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                      "WHERE is_registered=1").fetchone()[0]
    if reg != EXPECT["registered_total"]:
        bad.append("已注册商标 %d 条，期望 %d" % (reg, EXPECT["registered_total"]))
    kinds = dict(cur.execute(
        "SELECT class_kind, COUNT(*) FROM ods_qcc_trademark GROUP BY 1"))
    if kinds.get("product") != EXPECT["product_trademarks"] or \
       kinds.get("service") != EXPECT["service_trademarks"]:
        bad.append("产品/服务类商标条数漂移：%r" % kinds)

    pn = cur.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                     "WHERE is_product_node=1").fetchone()[0]
    sv = cur.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                     "WHERE is_service=1").fetchone()[0]
    if (pn, sv) != (EXPECT["product_nodes"], EXPECT["service_classes"]):
        bad.append("产品/服务类个数 %r，期望 %r" % (
            (pn, sv), (EXPECT["product_nodes"], EXPECT["service_classes"])))
    if cur.execute("SELECT COUNT(*) FROM v_product_node").fetchone()[0] != \
       EXPECT["product_nodes"]:
        bad.append("v_product_node 不是 35 行")
    if cur.execute("SELECT COUNT(*) FROM v_product_node WHERE stated_match=0").fetchone()[0]:
        bad.append("产品节点覆盖企业数与用户 CSV 不一致（stated_match=0）")
    if cur.execute("SELECT COUNT(*) FROM bridge_enterprise_product b "
                   "JOIN dim_product_nice_class p USING(product_id) "
                   "WHERE p.is_product_node=0").fetchone()[0]:
        bad.append("服务类被写进了企业—产品桥接表")
    if cur.execute("SELECT COUNT(DISTINCT enterprise_id) FROM "
                   "bridge_enterprise_product").fetchone()[0] != EXPECT["bridge_firms"]:
        bad.append("产品节点覆盖户数漂移")
    if cur.execute("SELECT SUM(n_trademark) FROM bridge_enterprise_product").fetchone()[0] \
       != EXPECT["bridge_trademarks"]:
        bad.append("桥接表产品类商标条数漂移")

    if cur.execute("SELECT COUNT(*) FROM v_enterprise_trademark_summary").fetchone()[0] != 500:
        bad.append("企业商标汇总视图不是 500 行")
    if cur.execute("SELECT SUM(n_trademark) FROM v_enterprise_trademark_summary").fetchone()[0] \
       != EXPECT["trademark_rows"]:
        bad.append("企业汇总商标总数与贴源明细不守恒")
    if cur.execute("SELECT COUNT(*) FROM v_enterprise_trademark_summary "
                   "WHERE has_trademark=1").fetchone()[0] != EXPECT["trademark_firms"]:
        bad.append("有商标企业数不是 444 家")

    # 六类实体覆盖：产品未入主图、人员来源明细为清洗后 2079
    cov = {r[0]: r for r in cur.execute("SELECT * FROM v_six_entity_coverage")}
    if len(cov) != 6:
        bad.append("v_six_entity_coverage 不是 6 行")
    if "产品" in cov:
        r = cov["产品"]
        if r[2] != 0 or r[3] != 35 or r[4] != 433 or abs(r[5] - 0.866) > 1e-9:
            bad.append("六类实体覆盖·产品行口径错误：%r" % (r,))
        if "部分闭合" not in r[6]:
            bad.append("产品行未标注「类别粒度部分闭合」")
    if "人员" in cov and cov["人员"][3] != 2079:
        bad.append("六类实体覆盖·人员来源行数不是清洗后 2079：%r" % (cov["人员"][3],))
    # 主图不得出现产品节点
    if cur.execute("SELECT COUNT(*) FROM ads_graph_node "
                   "WHERE node_type LIKE '%product%'").fetchone()[0]:
        bad.append("产品节点被并入 ads_graph_node 主图，违反并列层约定")

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
    slices = cur.execute("SELECT COUNT(DISTINCT time_slice) FROM "
                         "v_graph_slice_annual").fetchone()[0]
    if slices != 29:
        bad.append("年度时序切面帧数变为 %d（应为 29）" % slices)

    vers = [r[0] for r in cur.execute("SELECT version FROM meta_schema_version")]
    if VERSION not in vers:
        bad.append("meta_schema_version 未登记 %s" % VERSION)
    if cur.execute("SELECT COUNT(*) FROM meta_data_source WHERE source_id="
                   "'qcc_trademark'").fetchone()[0] != 1:
        bad.append("meta_data_source 未登记 qcc_trademark")
    if verify_schema and sql_block() not in open(SCHEMA_SQL, encoding="utf-8").read():
        bad.append("schema.sql 未同步 [V21PRODUCT] DDL 块")
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

    for p in (TM_CSV, PRODUCT_CSV, MAIN_CSV):
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
        for v in reversed(list(VIEWS)):
            con.execute("DROP VIEW IF EXISTS %s" % v)
        # 逆序删表：bridge_enterprise_product 外键指向 dim_product_nice_class，
        # 必须先删子表，否则 PRAGMA foreign_keys=ON 下 DROP 会失败。
        for t in reversed(TABLES):
            con.execute("DROP TABLE IF EXISTS %s" % t)
        with open(SQL, encoding="utf-8") as f:
            con.executescript(f.read())
        stat = load(con, built_at)
        register_meta(con, built_at, stat)
        con.commit()
        bad = check(con, verify_schema=False)
        if bad:
            print("校验失败（DDL 已提交，请从备份回滚）：")
            for b in bad:
                print("  x", b)
            return 1
        sync_schema_sql()
        print("迁移完成 %s" % VERSION)
        print("  商标明细 %d 条 / %d 家；已注册 %d 条（与主表守恒）" % (
            stat["trademark_rows"], stat["trademark_firms"], stat["registered_total"]))
        print("  尼斯类 %d（产品 %d / 服务 %d）；产品类商标 %d 条 / 服务类 %d 条" % (
            stat["nice_class_rows"], EXPECT["product_nodes"],
            EXPECT["service_classes"], stat["product_trademarks"],
            stat["service_trademarks"]))
        print("  企业—产品桥接 %d 行 / %d 户 / %d 条产品类商标" % (
            stat["bridge_rows"], stat["bridge_firms"], stat["bridge_trademarks"]))
        print("  产品节点仍为并列层，未并入 ads_graph_node；29 帧时序切面未动")
        print("  schema.sql 已同步 [V21PRODUCT] 块")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
