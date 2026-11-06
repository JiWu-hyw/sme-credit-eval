# -*- coding: utf-8 -*-
"""专利去重守卫测试。

防的是同一类事故：同一件专利在源数据里落了两条 —— 申请公布文本（公告号 …A，
带 IPC 分类号、无法律状态）与授权公告文本（…B，带授权状态、无 IPC 分类号）。
任何按 `fact_patent` 行数做的聚合都会重复计数，实测让专利数虚增约 8%，
把手算授权率硬压向 0.5（406 家企业平均低估 5.7pp，最大 50pp）。

五条断言：
  1. `v_patent_canonical` 存在，且行数 = 去重后的（专利 × 企业）数
  2. 视图行数守恒：Σ n_records 必须等于 fact_patent 总行数（不允许悄悄丢记录）
  3. 合并没丢信息：A 版才有 IPC、B 版才有授权状态，两者都要在视图里留下
  4. as-of 特征的 n_patent_app 必须等于按 app_no 去重数（按行数算就是 bug）
  5. 源码里不许再出现「按行计数专利」的写法

用法：python tests/test_patent_dedup.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
SRC = os.path.join(ROOT, "src")

PASS, FAIL = [], []
WINDOW_START = 2019          # 与 build_asof_features.WINDOW_START 一致


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:                       # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ✗ %s —— %s: %s" % (name, type(e).__name__, e))


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    def _cols(table):
        return [r[1] for r in cur.execute('PRAGMA table_info("%s")' % table)]

    def a_view_exists():
        v = cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='v_patent_canonical'"
        ).fetchone()[0]
        assert v == 1, "v_patent_canonical 视图不存在"
        need = {"app_no", "enterprise_id", "patent_id", "patent_ids", "n_records",
                "is_granted", "legal_status", "ipc_main", "patent_type",
                "app_year", "n_distinct_titles"}
        miss = need - set(_cols("v_patent_canonical"))
        assert not miss, "视图缺列 %s" % ",".join(sorted(miss))

    def b_row_conserved():
        tot = cur.execute("SELECT COUNT(*) FROM fact_patent").fetchone()[0]
        s = cur.execute("SELECT SUM(n_records) FROM v_patent_canonical").fetchone()[0]
        # fact_patent 里 app_no 为空的记录不可能被视图收进来，单独扣掉
        null_app = cur.execute(
            "SELECT COUNT(*) FROM fact_patent WHERE app_no IS NULL").fetchone()[0]
        assert s == tot - null_app, "Σn_records=%s ≠ 有 app_no 的行数 %s" % (s, tot - null_app)

    def c_no_info_lost():
        """合并必须做字段级并集：IPC 来自 A 版，授权状态来自 B 版。"""
        # 组内只有 A 版有 ipc_main，合并后视图必须有值
        lost_ipc = cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT app_no FROM fact_patent WHERE app_no IS NOT NULL
                GROUP BY 1 HAVING COUNT(*)>1 AND COUNT(ipc_main)>0) d
            WHERE NOT EXISTS (SELECT 1 FROM v_patent_canonical c
                              WHERE c.app_no=d.app_no AND c.ipc_main IS NOT NULL)
        """).fetchone()[0]
        assert lost_ipc == 0, "%d 组原本有 IPC 的专利在合并后丢了 ipc_main" % lost_ipc

        # 组内有已授权记录的，合并后 is_granted 必须是 1
        lost_grant = cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT app_no FROM fact_patent WHERE app_no IS NOT NULL
                GROUP BY 1 HAVING COUNT(*)>1 AND SUM(is_granted)>0) d
            WHERE NOT EXISTS (SELECT 1 FROM v_patent_canonical c
                              WHERE c.app_no=d.app_no AND c.is_granted=1)
        """).fetchone()[0]
        assert lost_grant == 0, "%d 组原本已授权的专利在合并后变成了未授权" % lost_grant

    def d_asof_uses_canonical():
        rows = cur.execute("""
            SELECT a.enterprise_id, a.as_of_year, a.feature_value,
                   (SELECT COUNT(*) FROM v_patent_canonical p
                    WHERE p.enterprise_id = a.enterprise_id
                      AND p.app_year BETWEEN ? AND a.as_of_year) AS uniq
            FROM ads_feature_asof a
            WHERE a.feature_name='n_patent_app' AND a.is_available=1
        """, (WINDOW_START,)).fetchall()
        assert rows, "as-of 特征表里没有 n_patent_app，无法校验"
        bad = [(e, y, int(v), u) for e, y, v, u in rows
               if v is not None and int(v) != u]
        assert not bad, ("%d 个时点的 n_patent_app 与去重数不符（按行计数？）"
                         "示例 %s" % (len(bad), bad[:3]))

    def e_no_row_count_in_src():
        """静态检查：源码里不许出现按行数统计专利的写法。

        允许：COUNT(DISTINCT app_no) / 走 v_patent_canonical / COUNT(*) 用于非专利表。
        禁止：`COUNT(*) FROM fact_patent`、`COUNT(DISTINCT ...patent_id)`。
        """
        pat_bad1 = re.compile(r"COUNT\s*\(\s*\*\s*\)\s*FROM\s+fact_patent", re.I)
        pat_bad2 = re.compile(r"COUNT\s*\(\s*DISTINCT\s+[\w.]*patent_id\s*\)", re.I)
        # 有意保留的例外：桥表自身行数统计、原始层核对
        # build_graph_v2：统计的是「已匹配企业的专利行数」，用于与 files_patent
        #   边数逐条对账（fact_patent 的 patent_id 唯一，行数即件数），
        #   不是对外报的专利件数 —— 对外报数一律走 v_patent_master。
        allow_files = {"fill_patent_pubdate.py", "load_aux_tables.py",
                       "rematch_unmatched_patents.py", "load_500family.py",
                       "build_graph_v2.py"}
        hits = []
        for dirpath, _dirs, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith(".py") or fn in allow_files:
                    continue
                path = os.path.join(dirpath, fn)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                for i, line in enumerate(text.splitlines(), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    if pat_bad1.search(line) or pat_bad2.search(line):
                        hits.append("%s:%d %s" % (fn, i, line.strip()[:70]))
        assert not hits, "发现按行计数专利的写法（应改去重或走 v_patent_canonical）：\n    " + "\n    ".join(hits[:6])

    print("专利去重守卫")
    check("规范视图存在且列齐全", a_view_exists)
    check("行数守恒（无记录丢失）", b_row_conserved)
    check("字段级合并没有丢信息", c_no_info_lost)
    check("as-of 特征按 app_no 去重", d_asof_uses_canonical)
    check("源码无按行计数专利写法", e_no_row_count_in_src)
    con.close()

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ PATENT DEDUP TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
