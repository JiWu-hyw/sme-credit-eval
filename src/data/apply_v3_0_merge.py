# -*- coding: utf-8 -*-
"""v3.0 合并版：3000 家数据并入现有表，不留独立 _3000 表。

JOIN 策略（关键修正 2026-11-18）：
  SQL 端 replace() 归一连接 49k×3000 = 1.5 亿次字符串运算会超时，
  且明细表用的是企查查"现用名"（cur_name），与 profile.company_name 有 126 家改名差异。
  改为 Python 端一次性建 keymap：company_name + cur_name + former_name 三种归一名
  → enterprise_id（3000 企业全覆盖），逐行 O(1) 查表，明细表零漏配。

目标形态（用户拍板 2026-11-18）：
  - 库结构维持 42 表 + 37 视图不变（dim 增 is_sample 列 + 1 扩样统计视图）；
  - 2500 家进 dim_enterprise（is_sample='0'），特征列按 profile 回填，原 500 一行不动；
  - 10 张 ods_*_3000 表数据合并进对应现有表后 DROP；
  - 9 个跨 dim 已发布视图用子查询注入 is_sample='1' 限定，v1.7~v2.2 数字零漂移。

幂等：_3000 表已 DROP 则直接进入 check。
用法：python -m src.data.apply_v3_0_merge [--check]
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DB_PATH = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")

TECH_ORD = {"入门": 1, "普通": 2, "良好": 3, "优秀": 4, "卓越": 5}
SCALE_ORD = {"XS(微型)": 1, "S(小型)": 2, "M(中型)": 3, "L(大型)": 4}
TYPE_MAP = {"发明公布": "invention", "发明授权": "invention",
            "实用新型": "utility", "外观设计": "design"}
LEGAL_MAP = {"授权": "granted", "有效": "granted", "在审": "pending", "审中": "pending",
             "实质审查": "pending", "公布": "pending", "失效": "expired",
             "撤回": "withdrawn", "主动放弃": "withdrawn", "视为放弃": "withdrawn",
             "避重放弃": "withdrawn", "公布撤回": "withdrawn", "公布视为撤回": "withdrawn",
             "公布驳回": "withdrawn", "全部无效": "expired", "部分无效": "expired",
             "权利终止": "expired", "期限届满专利权终止": "expired", "未缴年费专利权终止": "expired"}
JUDICIAL_KIND = {"失信被执行人": "失信被执行人", "被执行人": "被执行人", "终本案件": "终本案件"}

V30_NOTE = (
    "v3.0 名单扩样 500→3000 合并入库（零新增采购，用户本地企查查整合回传）："
    "① dim_enterprise 扩容至 3000（原500 is_sample='1' 一行未动，新增2500 is_sample='0'，"
    "特征列按企查查画像回填，枚举口径与原500同构）；"
    "② 名单批次并入 fact_qualification（qual_type='specialized'，2023/2024/2025 按批次起算）；"
    "③ 专利14.08万条并入 fact_patent（申请号去重）、商标/资质/股东/高管/荣誉/司法并入各自 ods_qcc_*、"
    "融资1476笔并入 fact_funding_event；改名企业经 company_name/cur_name/former_name 三键归一桥接；"
    "④ 9 个跨 dim 已发布视图限定 is_sample='1'，历史数字零漂移；新增 v_enterprise_expansion_3000；"
    "⑤ 库结构保持 42 表 + 38 视图；资质存续标签扩样层待观察期满后另立迁移。"
)
VIEWS_TO_PIN = [
    "v_enterprise_label_matrix", "v_enterprise_grade_l0", "v_qual_history",
    "v_enterprise_profile", "v_graph_coverage", "v_data_coverage",
    "v_axis_a_judicial", "v_enterprise_person_summary", "v_enterprise_trademark_summary",
]
T3000 = ["ods_roster_3000", "ods_qcc_profile_3000", "ods_patent_3000", "ods_cert_3000",
         "ods_tm_3000", "ods_shareholder_3000", "ods_person_3000", "ods_honor_3000",
         "ods_funding_3000", "ods_judicial_3000"]


def norm(s):
    return re.sub(r"[（）()　\s]", "", s or "")


def to_int(v):
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if t in ("", "-", "NAN", "无"):
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def to_float(v):
    if v is None:
        return None
    t = str(v).strip()
    if t in ("", "-", "NAN", "无"):
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*万", t)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    m = re.search(r"([\d,]+(?:\.\d+)?)", t)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def yes_no(v):
    return {"是": 1, "否": 0}.get(str(v).strip() if v else None)


def amt_yuan(v):
    if v is None:
        return None
    t = str(v).strip()
    if t in ("", "-", "NAN", "无", "未披露", "数百万", "数千万", "数亿元", "约亿元", "约千万美元"):
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(亿|万)", t)
    if not m:
        return None
    x = float(m.group(1).replace(",", ""))
    return x * 1e8 if m.group(2) == "亿" else x * 1e4


def smart_insert(con, table, columns, rows):
    """按目标表实际列裁剪后批量 INSERT（STRICT 表友好，缺列自动跳过）。"""
    if not rows:
        return 0
    have = {r[1] for r in con.execute(f"PRAGMA table_info('{table}')")}
    keep = [(i, c) for i, c in enumerate(columns) if c in have]
    if not keep:
        return 0
    cols = [c for _, c in keep]
    con.executemany(
        "INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(cols), ",".join("?" * len(cols))),
        [tuple(r[i] for i, _ in keep) for r in rows])
    return len(rows)


def build_keymap(con):
    """company_name + cur_name + former_name 三种归一名 → enterprise_id。"""
    km = {}
    q = con.execute("SELECT enterprise_id,company_name,cur_name,former_name FROM tmp_bridge_v30")
    for eid, cn, cur_n, form in q:
        for n in (cn, cur_n, form):
            k = norm(n)
            if k:
                km.setdefault(k, eid)
    return km


def eid_of(km, name):
    return km.get(norm(name))


def prep(con):
    """建桥接（profile↔dim），供 fill_dim 回填特征列。"""
    con.execute("DROP TABLE IF EXISTS tmp_bridge_v30")
    con.execute("""
        CREATE TABLE tmp_bridge_v30 AS
        SELECT d.enterprise_id, d.is_sample, p.*
        FROM dim_enterprise d
        JOIN ods_qcc_profile_3000 p
          ON replace(replace(replace(p.company_name,'（',''),'）',''),' ','')
           = replace(replace(replace(d.name_display,'（',''),'）',''),' ','')
    """)
    return con.execute("SELECT COUNT(*) FROM tmp_bridge_v30").fetchone()[0]


def fill_dim(con):
    """扩样行特征列回填（只动 is_sample='0'）。"""
    cur = con.cursor()
    cur.execute("SELECT * FROM tmp_bridge_v30 WHERE is_sample='0'")
    cols = [d[0] for d in cur.description]
    n = 0
    for tup in cur.fetchall():
        r = dict(zip(cols, tup))
        cl, tg, sc = r["credit_level"], r["kechuang_grade"], r["ent_scale"]
        cur.execute(
            """UPDATE dim_enterprise SET
                 found_date=?, biz_status=?, legal_rep=?, reg_capital=?, insured_num=?,
                 qcc_score=?, qcc_credit_level=?, qcc_credit_level_ord=?,
                 qcc_tech_level=?, qcc_tech_level_ord=?,
                 qcc_enterprise_scale=?, qcc_scale_ord=?,
                 qcc_industry_section=?, qcc_industry_class=?,
                 is_high_tech_union=?, is_little_giant=?, is_tech_sme=?, qcc_is_micro=?,
                 patent_cnt=?, invention_union_cnt=?, cert_cnt=?, cert_valid_cnt=?,
                 shareholder_cnt=?, serious_dishonesty_cnt=?, biz_anomaly_cnt=?,
                 risk_negative_cnt=?, honor_cnt=?, updated_at=?
               WHERE enterprise_id=? AND is_sample='0'""",
            (r["est_date"], r["reg_status"], r["legal_rep"], to_float(r["reg_capital"]),
             to_int(r["insure_num"]), to_int(r["qcc_score"]),
             cl, (int(cl[2:]) if cl and cl.startswith("L-") and cl[2:].isdigit() else None),
             tg, TECH_ORD.get(tg), sc, SCALE_ORD.get(sc),
             r["gb_industry_l1"], r["gb_industry_l2"],
             yes_no(r["is_hn_tech"]), yes_no(r["is_giant"]), yes_no(r["is_sme_tech"]),
             yes_no(r["is_micro"]),
             to_int(r["patent_cnt"]), to_int(r["inv_patent_cnt"]),
             to_int(r["cert_cnt"]), to_int(r["cert_valid_cnt"]),
             to_int(r["shareholder_cnt"]),
             to_int(r["judge_dishonest_cnt"]), to_int(r["judge_exec_cnt"]),
             (1 if (to_int(r["judge_default_flag"]) or 0) > 0 else 0),
             to_int(r["exec_cnt"]),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["enterprise_id"]))
        n += cur.rowcount
    return n


def merge_roster(con, km):
    """名单批次 → fact_qualification（qual_type='specialized'）。"""
    have = {r[0] for r in con.execute(
        "SELECT enterprise_id||'|'||batch_no FROM fact_qualification WHERE batch_no IS NOT NULL")}
    ins, miss = [], 0
    for cn, batch in con.execute("SELECT company_name,batch FROM ods_roster_3000"):
        eid = eid_of(km, cn)
        m = re.match(r"(20\d{2})", batch or "")
        if not eid or not m:
            miss += 1
            continue
        key = "%s|%s" % (eid, batch)
        if key in have:
            continue
        have.add(key)
        ins.append((eid, "specialized", batch, int(m.group(1)), "announced", 1.0))
    smart_insert(con, "fact_qualification",
                 ["enterprise_id", "qual_type", "batch_no", "year", "status", "match_score"], ins)
    return len(ins), miss


def merge_patents(con, km):
    have = {r[0] for r in con.execute("SELECT app_no FROM fact_patent WHERE app_no IS NOT NULL")}
    ins, seen, miss = [], set(), 0
    q = ("SELECT company_name,title,patent_type,legal_status,app_no,app_date,pub_no,pub_date "
         "FROM ods_patent_3000")
    for cn, title, ptype, legal, app_no, app_date, pub_no, pub_date in con.execute(q):
        eid = eid_of(km, cn)
        if not eid or not app_no or app_no in have or app_no in seen:
            miss += 1
            continue
        seen.add(app_no)
        lt = LEGAL_MAP.get(str(legal or "").strip())
        yr = to_int((app_date or "")[:4])
        ins.append((pub_no or app_no, app_no, title, app_date, yr, pub_date,
                    None, None, TYPE_MAP.get(str(ptype or "").strip()), lt,
                    1 if lt == "granted" else 0, "CN", eid, cn, 1.0, "exact", None))
    smart_insert(con, "fact_patent",
                 ["patent_id", "app_no", "title", "app_date", "app_year", "pub_date",
                  "grant_date", "grant_year", "patent_type", "legal_status", "is_granted",
                  "country_code", "enterprise_id", "assignee_raw", "match_confidence",
                  "match_method", "source_id"], ins)
    return len(ins), miss


def merge_cert(con, km):
    ins, miss = [], 0
    q = ("SELECT company_name,cert_name,cert_detail,cert_no,issuer,issue_date,valid_to,cert_status "
         "FROM ods_cert_3000")
    for cn, name, detail, no, issuer, issue, valid, status in con.execute(q):
        eid = eid_of(km, cn)
        if not eid:
            miss += 1
            continue
        ins.append((eid, cn, name, detail, no, issuer, issue, valid, status))
    smart_insert(con, "ods_qcc_cert",
                 ["enterprise_id", "company_name", "cert_name", "cert_category", "cert_no",
                  "issuer", "issue_date", "valid_to", "cert_status"], ins)
    return len(ins), miss


def merge_tm(con, km):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ins, miss = [], 0
    q = ("SELECT company_name,tm_name,nice_class,tm_status,reg_no,app_date,announce_date "
         "FROM ods_tm_3000")
    for cn, tname, nc_raw, status, reg_no, app_date, announce in con.execute(q):
        eid = eid_of(km, cn)
        m = re.match(r"(\d+)", nc_raw or "")
        nc = int(m.group(1)) if m else None
        if not eid or not nc or not (1 <= nc <= 45) or not status or not reg_no:
            miss += 1
            continue
        ins.append((eid, cn, tname, nc, nc_raw, "service" if nc >= 35 else "product",
                    status, 1 if str(status).startswith("已注册") else 0,
                    reg_no, app_date, announce, "3000_expansion", now))
    smart_insert(con, "ods_qcc_trademark",
                 ["enterprise_id", "company_name", "trademark_name", "nice_class",
                  "nice_class_raw", "class_kind", "trademark_status", "is_registered",
                  "reg_no", "apply_date", "reg_announce_date", "source_file", "built_at"], ins)
    return len(ins), miss


def merge_shareholder(con, km):
    ins, miss = [], 0
    q = ("SELECT company_name,holder_name,holder_type,share_type,shares,subscribed,sub_date,"
         "indirect_pct,first_date,related FROM ods_shareholder_3000")
    for cn, holder, htype, skind, shares, sub, subdate, indirect, first, related in con.execute(q):
        eid = eid_of(km, cn)
        if not eid:
            miss += 1
            continue
        ins.append((eid, cn, holder, htype, skind, to_int(shares), to_float(sub), subdate,
                    to_float(indirect), first, related))
    smart_insert(con, "ods_qcc_shareholder",
                 ["enterprise_id", "company_name", "shareholder_name", "shareholder_type",
                  "share_kind", "shares", "subscription_wan", "subscription_date",
                  "indirect_ratio", "first_hold_date", "related_product"], ins)
    return len(ins), miss


def merge_person(con, km):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ins, miss = [], 0
    for cn, pname, pos in con.execute(
            "SELECT company_name,person_name,position FROM ods_person_3000"):
        eid = eid_of(km, cn)
        if not eid or not pname or str(pname).strip() in ("", "姓名", "Name"):
            miss += 1
            continue
        ins.append((eid, cn, pname, pos, None, "3000_expansion", now))
    smart_insert(con, "ods_qcc_person",
                 ["enterprise_id", "company_name", "person_name", "position",
                  "is_legal_rep", "source_batch", "built_at"], ins)
    return len(ins), miss


def merge_honor(con, km):
    ins, miss = [], 0
    q = ("SELECT company_name,honor_name,honor_type,level,issuer,cert_year,pub_date,"
         "valid_from,valid_to,src FROM ods_honor_3000")
    for cn, hname, htype, level, issuer, cyear, pub, vf, vt, src in con.execute(q):
        eid = eid_of(km, cn)
        if not eid:
            miss += 1
            continue
        ins.append((eid, cn, hname, htype, level, issuer, to_int(cyear), pub, vf, vt, src))
    smart_insert(con, "ods_qcc_honor",
                 ["enterprise_id", "company_name", "honor_name", "honor_type", "level",
                  "publisher", "cert_year", "publish_date", "valid_from", "valid_to", "source"], ins)
    return len(ins), miss


def merge_funding(con, km):
    ins, miss = [], 0
    q = "SELECT company_name,fund_date,round,amount,valuation,investors FROM ods_funding_3000"
    for cn, fdate, round_, amount, valuation, investors in con.execute(q):
        eid = eid_of(km, cn)
        if not eid or not fdate:
            miss += 1
            continue
        # post_valuation 恒 NULL：企查查融资明细的「企业估值」是带单位中文串
        # （如「4亿元」「数亿美元」），无可靠单位解析能力；错误数值比缺失更危险，
        # 与 v1.8「设计性 NULL」口径保持一致（见 field_gap_registry G4）。
        # source_id 填行级血缘，避免 1476 行断链。
        ins.append((eid, fdate, to_int(str(fdate)[:4]), "private_placement",
                    amt_yuan(amount), round_, None, investors, "qcc_funding_3000"))
    smart_insert(con, "fact_funding_event",
                 ["enterprise_id", "event_date", "event_year", "event_type", "amount_cny",
                  "round_label", "post_valuation", "investor_names", "source_id"], ins)
    return len(ins), miss


def merge_judicial(con, km):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ins, miss = [], 0
    for cn, kind, case_no, court, amount in con.execute(
            "SELECT company_name,kind,case_no,court,amount FROM ods_judicial_3000"):
        eid = eid_of(km, cn)
        if not eid:
            miss += 1
            continue
        ins.append((eid, cn, JUDICIAL_KIND.get(str(kind or "").strip(), str(kind or "").strip()),
                    case_no, court, to_float(amount), None, None, None,
                    "3000_expansion", now))
    smart_insert(con, "ods_qcc_judicial",
                 ["enterprise_id", "company_name", "event_type", "case_no", "court",
                  "amount_yuan", "amount_kind", "event_date", "applicant",
                  "source_batch", "built_at"], ins)
    return len(ins), miss


def pin_views(con):
    """把 FROM dim_enterprise [别名] 包成子查询加 is_sample='1'，不改动视图其余结构。"""
    done = []
    for v in VIEWS_TO_PIN:
        row = con.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (v,)).fetchone()
        if not row or not row[0]:
            continue
        sql = row[0]
        if "is_sample" in sql:
            done.append((v, "already"))
            continue
        m = re.search(r"FROM\s+dim_enterprise(\s+[a-zA-Z_]\w*)?", sql, re.I)
        if not m:
            done.append((v, "NO-MATCH"))
            continue
        tail = m.group(1) or ""
        repl = "FROM (SELECT * FROM dim_enterprise WHERE is_sample='1')" + tail
        new_sql = sql[:m.start()] + repl + sql[m.end():]
        con.execute("DROP VIEW IF EXISTS %s" % v)
        con.execute(new_sql)
        done.append((v, "pinned"))
    return done


def build_expansion_view(con):
    con.execute("DROP VIEW IF EXISTS v_enterprise_expansion_3000")
    con.execute("""
        CREATE VIEW v_enterprise_expansion_3000 AS
        SELECT COUNT(*)                                            AS n_total,
               SUM(CASE WHEN d.is_sample='1' THEN 1 ELSE 0 END)    AS n_core_500,
               SUM(CASE WHEN d.is_sample='0' THEN 1 ELSE 0 END)    AS n_expansion_2500,
               SUM(CASE WHEN d.is_sample='0' AND COALESCE(d.patent_cnt,0)>0 THEN 1 ELSE 0 END)
                                                                 AS n_exp_with_patent,
               SUM(CASE WHEN d.is_sample='0' AND COALESCE(d.risk_negative_cnt,0)>0 THEN 1 ELSE 0 END)
                                                                 AS n_exp_risk_negative,
               SUM(CASE WHEN d.is_sample='0' AND COALESCE(d.serious_dishonesty_cnt,0)>0 THEN 1 ELSE 0 END)
                                                                 AS n_exp_dishonest,
               (SELECT COUNT(DISTINCT f.enterprise_id) FROM fact_funding_event f
                  JOIN dim_enterprise d2 ON d2.enterprise_id=f.enterprise_id
                 WHERE d2.is_sample='0')                           AS n_exp_funded
        FROM dim_enterprise d
    """)


def register_meta(con):
    con.execute(
        "INSERT INTO meta_schema_version(version,applied_at,author,note) VALUES(?,?,?,?)"
        " ON CONFLICT(version) DO UPDATE SET applied_at=excluded.applied_at,note=excluded.note",
        ("v3.0", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "WorkBuddy(施汶邑)", V30_NOTE))


def drop_t3000(con):
    for t in T3000:
        con.execute("DROP TABLE IF EXISTS %s" % t)
    con.execute("DROP TABLE IF EXISTS tmp_bridge_v30")


def check(con):
    cur = con.cursor()
    res = {}
    cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    res["tables"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
    res["views"] = cur.fetchone()[0]
    cur.execute("SELECT is_sample,COUNT(*) FROM dim_enterprise GROUP BY 1")
    res["dim(is_sample,n)"] = cur.fetchall()
    for t in ["fact_patent", "ods_qcc_trademark", "ods_qcc_cert", "ods_qcc_shareholder",
              "ods_qcc_person", "ods_qcc_honor", "fact_funding_event", "ods_qcc_judicial",
              "fact_qualification"]:
        cur.execute("SELECT COUNT(*) FROM %s" % t)
        res[t] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM v_enterprise_label_matrix")
    res["label_matrix(应500)"] = cur.fetchone()[0]
    cur.execute("SELECT grade_multilabel,COUNT(*) FROM v_enterprise_multilabel_grade GROUP BY 1")
    res["五档(应原数字)"] = dict(cur.fetchall())
    try:
        cur.execute("SELECT * FROM v_enterprise_expansion_3000")
        res["expansion_3000"] = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    except Exception as e:
        res["expansion_3000"] = "ERR:%s" % e
    cur.execute("PRAGMA integrity_check")
    res["integrity"] = cur.fetchone()[0]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(DB_PATH)
    try:
        if args.check:
            for k, v in check(con).items():
                print("  %s: %s" % (k, v))
            return 0
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='ods_qcc_profile_3000'")
        if cur.fetchone()[0] == 0:
            print("_3000 表已并入（幂等），直接校验：")
            for k, v in check(con).items():
                print("  %s: %s" % (k, v))
            return 0
        nb = prep(con)
        print("桥接 profile↔dim %d 家（应3000）" % nb)
        km = build_keymap(con)
        print("三名称 keymap %d 键（≥3000）" % len(km))
        print("dim 特征列回填 %d 行" % fill_dim(con))
        nq, mq = merge_roster(con, km)
        print("fact_qualification +%d（未配%d）" % (nq, mq))
        np_, mp = merge_patents(con, km)
        print("fact_patent +%d（跳过%d，多因申请号已存在）" % (np_, mp))
        for label, fn in [("ods_qcc_cert", merge_cert), ("ods_qcc_trademark", merge_tm),
                          ("ods_qcc_shareholder", merge_shareholder), ("ods_qcc_person", merge_person),
                          ("ods_qcc_honor", merge_honor), ("fact_funding_event", merge_funding),
                          ("ods_qcc_judicial", merge_judicial)]:
            nn, mm = fn(con, km)
            print("%s +%d（未配%d）" % (label, nn, mm))
        for v, st in pin_views(con):
            print("  view %s: %s" % (v, st))
        build_expansion_view(con)
        register_meta(con)
        drop_t3000(con)
        con.commit()
        print("=== 合并完成，最终校验 ===")
        for k, v in check(con).items():
            print("  %s: %s" % (k, v))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
