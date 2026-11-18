# -*- coding: utf-8 -*-
"""v3.0 融资事实表质量修复（幂等）。

问题（v3.0 扩样合并引入）：
1. fact_funding_event 有 1476 行 source_id=NULL（来自 3000家_融资明细.csv），
   违反行级血缘完整性。→ 补 source_id='qcc_funding_3000'。
2. 29 行 post_valuation 被 to_float("4亿元") 错解成 4.0（单位丢失，语义错误；
   "数亿美元"等模糊值更不能当精确估值）。→ 清空，维持 v1.8「设计性 NULL」
   口径（field_gap_registry G4）。生成源 apply_v3_0_merge.merge_funding 已同步修正。

用法：python -m src.data.fix_v3_0_funding_quality [--check]
"""
import argparse
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")
SOURCE = "qcc_funding_3000"


def _open(check):
    if check:
        return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    return sqlite3.connect(DB)


def scan(con):
    c = con.cursor()
    null_src = c.execute(
        "SELECT COUNT(*) FROM fact_funding_event WHERE source_id IS NULL").fetchone()[0]
    bad_val = c.execute(
        "SELECT COUNT(*) FROM fact_funding_event WHERE post_valuation IS NOT NULL").fetchone()[0]
    src_registered = c.execute(
        "SELECT COUNT(*) FROM meta_data_source WHERE source_id=?", (SOURCE,)).fetchone()[0]
    return {"null_src": null_src, "bad_val": bad_val, "src_registered": src_registered}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    con = _open(args.check)
    try:
        before = scan(con)
        print("修复前:", before)
        if args.check:
            return
        c = con.cursor()
        # 登记血缘 source（source_type 受 CHECK 约束，用 internal）
        c.execute("""INSERT OR IGNORE INTO meta_data_source
            (source_id,source_name,source_type,url,license,fetch_mode,cost_cny,
             quota_note,owner,coverage,first_fetch,last_fetch,is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),1)""",
                  (SOURCE, "企查查3000家融资明细", "internal", "", "", "manual", 0,
                   "v3.0 融资合并行级血缘", "项目组", "1476 笔"))
        # 补 source_id
        c.execute("UPDATE fact_funding_event SET source_id=? WHERE source_id IS NULL", (SOURCE,))
        n_src = c.rowcount
        # 清空错误估值
        c.execute("UPDATE fact_funding_event SET post_valuation=NULL WHERE post_valuation IS NOT NULL")
        n_val = c.rowcount
        con.commit()
        print("补 source_id: %d 行；清空错误 post_valuation: %d 行" % (n_src, n_val))
        print("修复后:", scan(con))
    finally:
        con.close()


if __name__ == "__main__":
    main()
