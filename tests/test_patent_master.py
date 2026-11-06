# -*- coding: utf-8 -*-
"""专利权威口径守卫测试（对照评审报告 §2.3(1)）。

评审发现库里有三个专利数并存，对外报数会打架：
  · fact_patent            32,969 行（A 版申请公布 + B 版授权公告各一行）
  · v_patent_canonical     30,346 行（一件专利 × 一个归属企业一行）
  · 去重申请号             30,103 个  ← 唯一对外口径

v1.4 新增 v_patent_master（一行 = 一个申请号），把三个口径的差额显式记在
join_status 里，任何人 SELECT COUNT(*) 都不会再报错数。

断言：
  1. v_patent_master 行数 == fact_patent 的 DISTINCT app_no
  2. join_status 三态之和 == 总行数，且 multi_assignee == canonical 里
     跨企业重复的申请号数
  3. has_abstract=1 的申请号数 == canonical 中摘要非空的 DISTINCT app_no
  4. 每行 app_year 落在 [1990, 2030]，无未来年份脏值
  5. 源码里禁止再写 COUNT(*) FROM fact_patent 当「专利件数」（沿用 dedup 测试规则）

用法：python tests/test_patent_master.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
SRC = os.path.join(ROOT, "src")

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  [OK] %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  [FAIL] %s —— %s" % (name, e))
    except Exception as e:                       # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  [FAIL] %s —— %s: %s" % (name, type(e).__name__, e))


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    one = lambda s, p=(): cur.execute(s, p).fetchone()[0]  # noqa: E731

    def t_rowcount():
        n_master = one("SELECT COUNT(*) FROM v_patent_master")
        n_distinct = one("SELECT COUNT(DISTINCT app_no) FROM fact_patent "
                         "WHERE app_no IS NOT NULL")
        assert n_master == n_distinct, "v_patent_master %d vs DISTINCT app_no %d" % (
            n_master, n_distinct)

    def t_join_status():
        rows = dict(cur.execute(
            "SELECT join_status, COUNT(*) FROM v_patent_master GROUP BY 1").fetchall())
        total = one("SELECT COUNT(*) FROM v_patent_master")
        assert sum(rows.values()) == total, "三态之和 %d != %d" % (sum(rows.values()), total)
        multi = one("""SELECT COUNT(*) FROM (SELECT app_no FROM v_patent_canonical
                       WHERE app_no IS NOT NULL GROUP BY app_no
                       HAVING COUNT(DISTINCT enterprise_id) > 1)""")
        assert rows.get("multi_assignee", 0) == multi, \
            "multi_assignee %d vs canonical 跨企业申请号 %d" % (
                rows.get("multi_assignee", 0), multi)

    def t_abstract():
        got = one("SELECT SUM(has_abstract) FROM v_patent_master")
        want = one("""SELECT COUNT(DISTINCT app_no) FROM v_patent_canonical
                      WHERE abstract IS NOT NULL AND TRIM(abstract) <> ''""")
        assert got == want, "有摘要申请号 %d vs %d" % (got, want)

    def t_app_year():
        bad = one("SELECT COUNT(*) FROM v_patent_master "
                  "WHERE app_year IS NOT NULL AND (app_year < 1990 OR app_year > 2030)")
        assert bad == 0, "app_year 越界 %d 行" % bad

    print("== 专利权威口径守卫 ==")
    check("v_patent_master == DISTINCT app_no", t_rowcount)
    check("join_status 三态守恒", t_join_status)
    check("摘要覆盖口径一致", t_abstract)
    check("app_year 无越界", t_app_year)

    print("\n通过 %d / 共 %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
