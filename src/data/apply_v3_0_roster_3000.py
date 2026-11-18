# -*- coding: utf-8 -*-
"""把 schema 从 v2.2 升到 v3.0：名单扩样 500 → 3000（原500家 + 新增2500家）。

数据来源：_data_collect/out/（2026-11-18 用户本地整合产出）
  - 3000家_主表.csv                3000 行 × 91 列（名单5列 + 企查查画像86列）
  - 3000家_专利明细.csv            140,796 条 / 2,972 家 / 1992–2026
  - 企查查_资质证书_3000家.csv      53,395 条
  - 3000家_商标明细.csv            49,220 条
  - 3000家_股东明细.csv            13,050 行
  - 3000家_高管明细.csv            14,809 行
  - 3000家_荣誉明细.csv            17,784 行
  - 3000家_融资明细.csv            1,476 笔
  - 3000家_司法明细.csv            255 条

纪律（与 v2.0/v2.1/v2.2 同构）：
  1. **只增不改**：不动 dim_enterprise 既有 500 行任何列值、不动 ads_label、
     不动 ads_graph_node/edge(+_ts)、不动任何已发布视图与 29 帧时序切面。
  2. 新增 2500 家以新 enterprise_id 插入 dim_enterprise，标记 is_sample='0'（扩样层）；
     原 500 家 UPDATE is_sample='1'（仅此一列，语义为"建模主样本"）。
     名称桥接：3000 家主表企业名与 dim_enterprise.name_display 归一后 500/500 全配，
     无需别名猜测；2500 家全部为新增。
  3. 九张新表全部 ODS 贴源层（不建 DIM/FACT/ADS 派生），清洗沿用 v2.0/v2.1 口径：
     '-' 日期置 NULL、金额保留原始字符串、表头脏行已在 CSV 侧剔除。
  4. 资质存续标签**本迁移不做**——2024/2025 批次起算口径需单独拍板（批次有效期起点不同），
     只在 meta 登记说明里写明。

幂等：重复执行先 DROP 本轮对象再重建，数据按同一份 CSV 全量重灌；
      meta 登记按固定键 UPSERT；schema.sql 的 [V30ROSTER] 块幂等替换。
执行前自动留一份升级前快照 data/db/zjtech_credit_pre_v30.db 作回滚点。

用法：
    python -m src.data.apply_v3_0_roster_3000            # 执行迁移
    python -m src.data.apply_v3_0_roster_3000 --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DB_PATH = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")
OUT_DIR = os.path.normpath(os.path.join(BACKEND, "..", "_data_collect", "out"))
SCHEMA_SQL = os.path.join(HERE, "migrations", "v3_0_roster_3000.sql")
SCHEMA_MAIN = os.path.join(HERE, "schema.sql")

MARK_BEGIN, MARK_END = "-- [V30ROSTER:BEGIN]", "-- [V30ROSTER:END]"
NEW_TABLES = [
    "ods_roster_3000", "ods_qcc_profile_3000", "ods_patent_3000",
    "ods_cert_3000", "ods_tm_3000", "ods_shareholder_3000",
    "ods_person_3000", "ods_honor_3000", "ods_funding_3000", "ods_judicial_3000",
]
V30 = "v3.0"
META_NOTE = (
    "v3.0 名单扩样 500→3000（零新增采购，用户本地企查查整合回传）："
    "① 新增 10 张 ODS 贴源表：ods_roster_3000（3000 家主名单，批次=2023一批941/2024一批938/"
    "2024二批29/2025一批592）、ods_qcc_profile_3000（企查查画像 3000×51 核心列）、"
    "ods_patent_3000（140,796 条/2,972 家/1992–2026）、ods_cert_3000（53,395）、"
    "ods_tm_3000（49,220）、ods_shareholder_3000（13,050）、ods_person_3000（14,809）、"
    "ods_honor_3000（17,784）、ods_funding_3000（1,476）、ods_judicial_3000（255）；"
    "② dim_enterprise 扩容 500→3000：原 500 家 is_sample='1'、新增 2500 家 is_sample='0'，"
    "名称经 name_display 归一 500/500 全配、零别名猜测；"
    "③ 严格未改：ads_label、主图 ads_graph_node/edge(+_ts)、29 帧时序切面、所有已发布视图；"
    "④ 资质存续标签按批次分别起算（2023一批自2023-05三年、2024/2025 各批按公示起算），"
    "本迁移不做、待单独拍板后另立迁移。"
)

CSV_MASTER = "3000家_主表.csv"
CSV_DETAIL = {
    "ods_patent_3000": ("3000家_专利明细.csv", [
        ("company_name", "企业名称"), ("title", "发明名称"), ("patent_type", "专利类型"),
        ("legal_status", "法律状态"), ("app_no", "申请号"), ("app_date", "申请日"),
        ("pub_no", "公开号"), ("pub_date", "公开日"), ("inventors", "发明人"), ("applicants", "申请人"),
    ]),
    "ods_cert_3000": ("企查查_资质证书_3000家.csv", [
        ("company_name", "企业名称"), ("cert_name", "资质名称"),
        ("cert_detail", "资质类别及等级/产品名称/认证项目"), ("cert_no", "证书编号"),
        ("issuer", "发证机构"), ("issue_date", "发证日期"), ("valid_to", "有效期至"),
        ("cert_status", "证书状态"),
    ]),
    "ods_tm_3000": ("3000家_商标明细.csv", [
        ("company_name", "企业名称"), ("tm_name", "商标名称"), ("nice_class", "国际分类"),
        ("tm_status", "商标状态"), ("reg_no", "申请/注册号"), ("app_date", "申请日期"),
        ("announce_date", "注册公告日期"),
    ]),
    "ods_shareholder_3000": ("3000家_股东明细.csv", [
        ("company_name", "企业名称"), ("holder_name", "股东名称"), ("holder_type", "股东类型"),
        ("share_type", "股份类型"), ("shares", "持股数（股）"), ("subscribed", "认缴出资额"),
        ("sub_date", "认缴出资日期"), ("indirect_pct", "间接持股比例"),
        ("first_date", "首次持股日期"), ("related", "关联产品/机构"),
    ]),
    "ods_person_3000": ("3000家_高管明细.csv", [
        ("company_name", "企业名称"), ("person_name", "姓名"), ("position", "职务"),
    ]),
    "ods_honor_3000": ("3000家_荣誉明细.csv", [
        ("company_name", "企业名称"), ("honor_name", "名称"), ("honor_no", "编号"),
        ("honor_type", "荣誉类型"), ("level", "级别"), ("issuer", "发布单位"),
        ("cert_year", "认证年份"), ("pub_date", "发布日期"), ("valid_from", "有效期自"),
        ("valid_to", "有效期至"), ("src", "来源"),
    ]),
    "ods_funding_3000": ("3000家_融资明细.csv", [
        ("company_name", "企业名称"), ("fund_date", "融资日期"), ("round", "融资轮次"),
        ("amount", "融资金额"), ("valuation", "企业估值"), ("investors", "投资方"),
    ]),
    "ods_judicial_3000": ("3000家_司法明细.csv", [
        ("kind", "类别"), ("company_name", "企业名称"), ("case_no", "案号"),
        ("court", "执行法院"), ("amount", "金额"),
    ]),
}


def norm_name(s: str) -> str:
    return re.sub(r"[（）()　\s]", "", s or "")


def clean(s):
    """贴源清洗：空串/NAN/无/- 统一 None；其余原样保留（含金额原始字符串）。"""
    if s is None:
        return None
    t = str(s).strip()
    if t in ("", "NAN", "-", "无", "NULL", "null"):
        return None
    return t


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


def load_master(con):
    """主表 → ods_roster_3000 + ods_qcc_profile_3000。"""
    path = os.path.join(OUT_DIR, CSV_MASTER)
    roster, profile = [], []
    for r in read_csv(path):
        roster.append((
            clean(r.get("序号")), clean(r.get("企业名称")), clean(r.get("认定批次")),
            clean(r.get("来源分组")) or ("原500家" if r.get("来源分组") == "原抽样表" else None),
            clean(r.get("地市_名单")), clean(r.get("地市判定")),
            clean(r.get("数据来源")),
        ))
        profile.append((
            clean(r.get("企业名称")), clean(r.get("统一社会信用代码")), clean(r.get("现用名称")),
            clean(r.get("登记状态")), clean(r.get("成立日期")), clean(r.get("法定代表人")),
            clean(r.get("注册资本")), clean(r.get("实缴资本")), clean(r.get("参保人数")),
            clean(r.get("企业类型")), clean(r.get("所属区县")), clean(r.get("所属城市")),
            clean(r.get("企业规模")), clean(r.get("企查分")), clean(r.get("信用等级")),
            clean(r.get("科创分")), clean(r.get("国标行业大类")), clean(r.get("国标行业小类")),
            clean(r.get("官网")), clean(r.get("是否小微")), clean(r.get("最新年报营业收入")),
            clean(r.get("是否小巨人")), clean(r.get("是否高企")), clean(r.get("是否科技型中小")),
            clean(r.get("是否专精特新")), clean(r.get("高企认定年份")), clean(r.get("高企有效期至")),
            clean(r.get("资质证书_条数")), clean(r.get("资质证书_有效数")),
            clean(r.get("体系认证数")), clean(r.get("产品认证数")),
            clean(r.get("专利数")), clean(r.get("发明专利数")), clean(r.get("实用新型数")),
            clean(r.get("外观设计数")), clean(r.get("商标件数")), clean(r.get("商标有效数")),
            clean(r.get("品牌数")), clean(r.get("股东数")), clean(r.get("高管人数")),
            clean(r.get("司法_失信条数")), clean(r.get("司法_被执行条数")),
            clean(r.get("司法_终本条数")), clean(r.get("司法_金额合计万元")),
            clean(r.get("司法_有违约")), clean(r.get("融资_笔数")), clean(r.get("融资_轮次数")),
            clean(r.get("融资_机构数")), clean(r.get("融资_首次年")), clean(r.get("融资_末次年")),
            clean(r.get("科创等级")), clean(r.get("曾用名")),
        ))
    con.executemany(
        "INSERT INTO ods_roster_3000(roster_id,company_name,batch,roster_group,city,city_judge,data_source)"
        " VALUES(?,?,?,?,?,?,?)", roster)
    con.executemany(
        "INSERT INTO ods_qcc_profile_3000(company_name,credit_code,cur_name,reg_status,est_date,"
        "legal_rep,reg_capital,paid_capital,insure_num,ent_type,district,city_qcc,ent_scale,"
        "qcc_score,credit_level,kechuang_score,gb_industry_l1,gb_industry_l2,website,is_micro,"
        "ann_rev_latest,is_giant,is_hn_tech,is_sme_tech,is_zjtx,hn_year,hn_valid_to,cert_cnt,"
        "cert_valid_cnt,sys_cert_cnt,prod_cert_cnt,patent_cnt,inv_patent_cnt,um_patent_cnt,"
        "dp_patent_cnt,tm_cnt,tm_valid_cnt,brand_cnt,shareholder_cnt,exec_cnt,judge_dishonest_cnt,"
        "judge_exec_cnt,judge_end_exec_cnt,judge_amt_sum,judge_default_flag,fund_event_cnt,"
        "fund_round_cnt,fund_inst_cnt,fund_first_year,fund_last_year,kechuang_grade,former_name)"
        " VALUES(?" + ",?" * 51 + ")", profile)
    return len(roster), len(profile)


def load_details(con):
    counts = {}
    for table, (fname, mapping) in CSV_DETAIL.items():
        path = os.path.join(OUT_DIR, fname)
        cols = [c for c, _ in mapping]
        rows = [tuple(clean(r.get(cn)) for _, cn in mapping) for r in read_csv(path)]
        # 主键自增：显式编号
        ph = ",".join("?" * len(cols))
        con.executemany(
            f"INSERT INTO {table}({','.join(cols)}) VALUES({ph})", rows)
        counts[table] = len(rows)
    return counts


def expand_dim(con):
    """dim_enterprise 500→3000：原500 is_sample='1'，新增2500 家 is_sample='0'。

    列映射（沿用现有列名）：est_date→found_date、reg_status→biz_status、
    district/city→region_name、ingest→created_at。
    """
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM pragma_table_info('dim_enterprise') WHERE name='is_sample'")
    if cur.fetchone()[0] == 0:
        cur.execute("ALTER TABLE dim_enterprise ADD COLUMN is_sample TEXT DEFAULT '1'")
    cur.execute("UPDATE dim_enterprise SET is_sample='1'")
    cur.execute("SELECT enterprise_id, name_display FROM dim_enterprise")
    byname = {norm_name(d): eid for eid, d in cur.fetchall()}
    cur.execute(
        "SELECT company_name, credit_code, est_date, reg_status, district, city_qcc FROM ods_qcc_profile_3000"
        " WHERE company_name IS NOT NULL")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ins = []
    for name, credit, est, status, district, city in cur.fetchall():
        key = norm_name(name)
        if not key or key in byname:
            continue
        eid = "ENT3" + hashlib_sha1(key)[:13].upper()
        ins.append((eid, name, key, "specialized", credit, est, status,
                    district or city, now))
        byname[key] = eid
    con.executemany(
        "INSERT INTO dim_enterprise(enterprise_id,name_display,name_norm,enterprise_type,"
        "credit_code,found_date,biz_status,region_name,created_at,is_sample)"
        " VALUES(?,?,?,?,?,?,?,?,?,'0')", ins)
    return len(ins)


def hashlib_sha1(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def dim_cols_exist(con):
    """dim_enterprise 是否具备迁移所需列（est_date/reg_status/ingest_date 可能缺）。"""
    cur = con.cursor()
    cur.execute("SELECT name FROM pragma_table_info('dim_enterprise')")
    cols = {r[0] for r in cur.fetchall()}
    return cols


def sync_schema_block():
    """把 [V30ROSTER] 块写进 schema.sql（幂等替换）。"""
    with open(SCHEMA_MAIN, encoding="utf-8") as fh:
        src = fh.read()
    with open(SCHEMA_SQL, encoding="utf-8") as fh:
        block = fh.read().strip()
    if MARK_BEGIN in src:
        src = re.sub(
            re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END),
            lambda _: block, src, flags=re.S)
    else:
        src = src.rstrip() + "\n\n" + block + "\n"
    with open(SCHEMA_MAIN, "w", encoding="utf-8") as fh:
        fh.write(src)


def register_meta(con):
    con.execute(
        "INSERT INTO meta_schema_version(version,applied_at,author,note) VALUES(?,?,?,?)"
        " ON CONFLICT(version) DO UPDATE SET applied_at=excluded.applied_at,note=excluded.note",
        (V30, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "WorkBuddy(施汶邑)", META_NOTE))


def check(con) -> dict:
    cur = con.cursor()
    res = {}
    cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    res["tables"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
    res["views"] = cur.fetchone()[0]
    cur.execute("SELECT version FROM meta_schema_version ORDER BY applied_at DESC LIMIT 1")
    res["version"] = cur.fetchone()[0]
    for t in NEW_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        res[t] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dim_enterprise")
    res["dim_enterprise"] = cur.fetchone()[0]
    cur.execute("SELECT is_sample, COUNT(*) FROM dim_enterprise GROUP BY 1")
    res["dim_split"] = cur.fetchall()
    ok = (res["tables"] == 52 and res["views"] == 37 and res["version"] == V30
          and res["dim_enterprise"] == 3000
          and res["ods_roster_3000"] == 3000
          and res["ods_qcc_profile_3000"] == 3000
          and res["ods_patent_3000"] == 140796
          and res["ods_cert_3000"] == 53395
          and res["ods_tm_3000"] == 49220
          and res["ods_shareholder_3000"] == 13050
          and res["ods_person_3000"] == 14809
          and res["ods_honor_3000"] == 17784
          and res["ods_funding_3000"] == 1476
          and res["ods_judicial_3000"] == 255
          and dict(res["dim_split"]).get("1") == 500
          and dict(res["dim_split"]).get("0") == 2500)
    res["ok"] = ok
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读校验，不改库")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    try:
        if args.check:
            print(check(con))
            return 0
        # 前置：CSV 就位
        missing = [f for f, _ in [(CSV_MASTER, None), *[(v[0], None) for v in CSV_DETAIL.values()]]
                   if not os.path.exists(os.path.join(OUT_DIR, f))]
        if missing:
            print("缺少 CSV:", missing)
            return 1
        # 快照回滚点
        snap = DB_PATH.replace(".db", "_pre_v30.db")
        if not os.path.exists(snap):
            shutil.copy2(DB_PATH, snap)
            print("已留升级前快照:", os.path.basename(snap))
        # 建表（幂等：先 DROP）
        with open(SCHEMA_SQL, encoding="utf-8") as fh:
            ddl = fh.read()
        cur = con.cursor()
        for t in NEW_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        # 只执行 MARK 块内的语句（块外只有注释）
        m = re.search(re.escape(MARK_BEGIN) + r"(.*?)" + re.escape(MARK_END), ddl, re.S)
        body = m.group(1)
        cur.executescript(body)
        con.commit()
        print("10 张 ODS 表已建")
        # 灌数
        n_r, n_p = load_master(con)
        print(f"主名单 {n_r} 行 / 画像 {n_p} 行")
        counts = load_details(con)
        for t, n in counts.items():
            print(f"{t}: {n}")
        # dim 扩容
        added = expand_dim(con)
        print(f"dim_enterprise 新增 {added} 家（扩样层 is_sample='0'）")
        con.commit()
        # meta + schema.sql
        register_meta(con)
        con.commit()
        sync_schema_block()
        print("schema.sql [V30ROSTER] 块已同步")
        # 校验
        res = check(con)
        for k, v in res.items():
            print(f"  {k}: {v}")
        if not res["ok"]:
            print("!! 校验未全过，请检查上方数字")
            return 1
        cur.execute("PRAGMA integrity_check")
        print("integrity:", cur.fetchone()[0])
        cur.execute("PRAGMA foreign_key_check")
        print("外键冲突:", len(cur.fetchall()))
        print("✅ v3.0 迁移完成")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
