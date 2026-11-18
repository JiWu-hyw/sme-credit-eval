# -*- coding: utf-8 -*-
"""v3.0 第二步：给 2500 家扩样行回填 dim_enterprise 特征列 + 已发布视图限定主样本层。

前置：apply_v3_0_roster_3000 已执行（dim 3000 家、is_sample 列已加）。

纪律：
  1. 原 500 家（is_sample='1'）任何列**一行不动**——旧数字/旧视图语义零漂移。
  2. 2500 家扩样行（is_sample='0'）按 ods_qcc_profile_3000 回填特征列，口径对齐原 500：
     - reg_status '存续' → biz_status '存续'（dim 原词表里 2497 家就是'存续'，
       视图 `biz_status<>'开业'` 对'存续'也成立——注意这会把扩样层的 y_hard_outcome
       全判为 0（坏）→ 所以第三步把已发布视图全部限定 is_sample='1'，
       扩样层单独走 v_enterprise_*_full 口径，绝不混算。
     - 信用等级 L-N → qcc_credit_level + qcc_credit_level_ord=N
     - 科创等级（入门/普通/良好/优秀/卓越）→ qcc_tech_level + qcc_tech_level_ord=1..5
     - 企业规模 XS/S/M/L → qcc_enterprise_scale + qcc_scale_ord=1..4
     - 是/否 旗标 → 1/0（is_high_tech_union/is_little_giant/is_tech_sme/qcc_is_micro）
     - 数值列直接转（patent_cnt/insured_num/shareholder_cnt 等）
  3. 已发布 9 个跨 dim 视图加 `WHERE is_sample='1'`：保证 v1.7~v2.2 所有已发布数字
     在 v3.0 库上**逐字节不变**；扩样层另建 v3.0 全量口径视图（本步不建，留 v3.1）。

用法：
    python -m src.data.apply_v3_0_fill_dim          # 执行
    python -m src.data.apply_v3_0_fill_dim --check  # 只读校验
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DB_PATH = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")

TECH_ORD = {"入门": 1, "普通": 2, "良好": 3, "优秀": 4, "卓越": 5}
SCALE_ORD = {"XS(微型)": 1, "S(小型)": 2, "M(中型)": 3, "L(大型)": 4}

# 已发布视图 → 需要加 is_sample='1' 过滤（对 dim_enterprise 的直接引用）
VIEWS_TO_PIN = [
    "v_qual_history", "v_enterprise_profile", "v_graph_coverage", "v_data_coverage",
    "v_enterprise_grade_l0", "v_enterprise_label_matrix", "v_axis_a_judicial",
    "v_enterprise_person_summary", "v_enterprise_trademark_summary",
]

SET_CLAUSE = """
UPDATE dim_enterprise SET
  found_date = (SELECT est_date FROM ods_qcc_profile_3000 p WHERE norm(p.company_name)=norm(d.name_display)),
  ...
"""


def norm(s):
    import re
    return re.sub(r"[（）()　\s]", "", s or "")


def build_bridge(con):
    """建临时桥接表：norm(name) → profile 行，供 UPDATE 关联。"""
    con.execute("DROP TABLE IF EXISTS tmp_bridge_v30")
    con.execute("""
        CREATE TABLE tmp_bridge_v30 AS
        SELECT d.enterprise_id,
               p.credit_code, p.cur_name, p.est_date, p.reg_status, p.legal_rep,
               p.reg_capital, p.insure_num, p.ent_type, p.district, p.city_qcc,
               p.ent_scale, p.qcc_score, p.credit_level, p.kechuang_grade,
               p.kechuang_score, p.gb_industry_l1, p.gb_industry_l2,
               p.is_micro, p.is_giant, p.is_hn_tech, p.is_sme_tech, p.is_zjtx,
               p.hn_year, p.hn_valid_to, p.cert_cnt, p.cert_valid_cnt,
               p.patent_cnt, p.inv_patent_cnt, p.tm_cnt, p.tm_valid_cnt,
               p.brand_cnt, p.shareholder_cnt, p.exec_cnt,
               p.judge_dishonest_cnt, p.judge_exec_cnt, p.judge_end_exec_cnt,
               p.judge_default_flag, p.fund_event_cnt
        FROM dim_enterprise d
        JOIN ods_qcc_profile_3000 p
          ON replace(replace(replace(p.company_name,'（',''),'）',''),' ','')
           = replace(replace(replace(d.name_display,'（',''),'）',''),' ','')
        WHERE d.is_sample='0'
    """)
    cur = con.execute("SELECT COUNT(*) FROM tmp_bridge_v30")
    return cur.fetchone()[0]


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
    """金额字符串 → 数值（'5,000万人民币' → 5000.0；仅取主数字，万元口径与 dim 现状一致）。"""
    if v is None:
        return None
    import re as _re
    t = str(v).strip()
    if t in ("", "-", "NAN", "无"):
        return None
    m = _re.search(r"([\d,]+(?:\.\d+)?)\s*万", t)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    m = _re.search(r"([\d,]+(?:\.\d+)?)", t)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def yes_no(v):
    if v is None:
        return None
    t = str(v).strip()
    if t == "是":
        return 1
    if t == "否":
        return 0
    return None


def credit_ord(v):
    if v and str(v).startswith("L-"):
        try:
            return int(str(v)[2:])
        except ValueError:
            return None
    return None


def fill(con):
    cur = con.cursor()
    n = 0
    cur.execute("SELECT * FROM tmp_bridge_v30")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        cl = r["credit_level"]
        tg = r["kechuang_grade"]
        sc = r["ent_scale"]
        cur.execute(
            """UPDATE dim_enterprise SET
                 found_date=?, biz_status=?, legal_rep=?, reg_capital=?, insured_num=?,
                 qcc_score=?, qcc_credit_level=?, qcc_credit_level_ord=?,
                 qcc_tech_level=?, qcc_tech_level_ord=?,
                 qcc_enterprise_scale=?, qcc_scale_ord=?,
                 qcc_industry_section=?, qcc_industry_class=?,
                 is_high_tech_union=?, is_little_giant=?, is_tech_sme=?, qcc_is_micro=?,
                 patent_cnt=?, invention_union_cnt=?, cert_cnt=?, cert_valid_cnt=?,
                 shareholder_cnt=?,
                 serious_dishonesty_cnt=?, biz_anomaly_cnt=?, risk_negative_cnt=?,
                 honor_cnt=?, updated_at=?
               WHERE enterprise_id=?""",
            (r["est_date"], r["reg_status"], r["legal_rep"], to_float(r["reg_capital"]),
             to_int(r["insure_num"]),
             to_int(r["qcc_score"]),
             cl, credit_ord(cl),
             tg, TECH_ORD.get(tg),
             sc, SCALE_ORD.get(sc),
             r["gb_industry_l1"], r["gb_industry_l2"],
             yes_no(r["is_hn_tech"]), yes_no(r["is_giant"]), yes_no(r["is_sme_tech"]),
             yes_no(r["is_micro"]),
             to_int(r["patent_cnt"]), to_int(r["inv_patent_cnt"]),
             to_int(r["cert_cnt"]), to_int(r["cert_valid_cnt"]),
             to_int(r["shareholder_cnt"]),
             to_int(r["judge_dishonest_cnt"]), to_int(r["judge_exec_cnt"]),
             to_int(r["judge_default_flag"]),
             to_int(r["exec_cnt"]),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             r["enterprise_id"]))
        n += cur.rowcount
    return n


def pin_views(con):
    """给已发布视图的 dim 引用加 is_sample='1'（语义=主样本层快照不变）。"""
    done = []
    for v in VIEWS_TO_PIN:
        row = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (v,)).fetchone()
        if not row or not row[0]:
            continue
        sql = row[0]
        if "is_sample" in sql:
            done.append((v, "already"))
            continue
        new_sql = sql.replace(
            "FROM dim_enterprise d",
            "FROM dim_enterprise d /* v3.0: 主样本层限定 */ CROSS JOIN (SELECT 1 WHERE d.is_sample='1')")
        if new_sql == sql:
            # 没匹配到别名形式，试无别名
            new_sql2 = sql.replace(
                "FROM dim_enterprise",
                "FROM dim_enterprise WHERE is_sample='1'")
            if new_sql2 == sql:
                done.append((v, "NO-MATCH (manual)"))
                continue
            new_sql = new_sql2
        con.execute(f"DROP VIEW IF EXISTS {v}")
        con.execute(new_sql)
        done.append((v, "pinned"))
    return done


def check(con):
    cur = con.cursor()
    res = {}
    cur.execute("SELECT COUNT(*) FROM dim_enterprise WHERE is_sample='0' AND patent_cnt IS NOT NULL")
    res["expansion_filled"] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dim_enterprise WHERE is_sample='1' AND patent_cnt IS NOT NULL")
    res["orig500_patent_filled"] = cur.fetchone()[0]  # 应保持原状（原 500 有 patent_cnt）
    cur.execute("SELECT grade_multilabel, COUNT(*) FROM v_enterprise_multilabel_grade GROUP BY 1")
    res["multilabel_grade"] = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM v_enterprise_label_matrix")
    res["label_matrix_rows"] = cur.fetchone()[0]
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
                print(f"  {k}: {v}")
            return 0
        nb = build_bridge(con)
        print(f"桥接 {nb} 家（应 2500）")
        n = fill(con)
        print(f"特征列回填 {n} 行")
        done = pin_views(con)
        for v, st in done:
            print(f"  view {v}: {st}")
        con.execute("DROP TABLE IF EXISTS tmp_bridge_v30")
        con.commit()
        for k, v in check(con).items():
            print(f"  {k}: {v}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
