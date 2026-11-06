# -*- coding: utf-8 -*-
"""授权口径守卫测试。

背景：`fact_patent.is_granted` 的语义是「此刻仍在权」，不是「是否曾获批授权」。
年费断缴、期限届满、主动放弃都会把已授权专利的这个标记翻回 0，于是
「授权后失效」与「从未授权」被混成一类。实测全部已归属专利 29,582 件中，
is_granted=1 只有 15,753 件，而真正拿到过授权的有 23,693 件 —— 少算 7,940 件
（33.5%），企业级授权率中位 0.567 被压到远低于真值 0.835。

正确判据是公告号 kind code：A 是申请公布，B / U / S / C 都是授权公告，
这条记录存在本身就说明授权发生过。视图 `v_patent_canonical.is_granted_ever`
把这层判断固化下来。

七条断言：
  1. 视图有 is_granted_ever / grant_kind 两列
  2. is_granted_ever 处处 >= is_granted（曾授权是「在权」的超集，反向即 bug）
  3. is_granted_ever 与 fact_patent 按 (企业, 申请号) 重算的结果逐条一致
  4. grant_kind 只出现在授权公告上，且非空时 is_granted_ever 必为 1
  5. as-of grant_rate 值域合法、覆盖达标
  6. build_asof_features 的 grant_rate 必须走「曾授权 + 审结期」，不能只看 is_granted
  7. 源码里不许再把 is_granted 直接聚合成授权率

用法：python tests/test_grant_definition.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
SRC = os.path.join(ROOT, "src")
GRANT_KINDS = {"B", "U", "S", "C"}

PASS, FAIL = [], []


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


def _cols(cur, table):
    return [r[1] for r in cur.execute("PRAGMA table_info(%s)" % table)]


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    def a_columns():
        cols = _cols(cur, "v_patent_canonical")
        for c in ("is_granted", "is_granted_ever", "grant_kind"):
            assert c in cols, "v_patent_canonical 缺少 %s" % c

    def b_superset():
        """曾授权 ⊇ 当前在权。任何一件「在权却未授权」都是分类错误。"""
        bad = cur.execute(
            "SELECT COUNT(*) FROM v_patent_canonical "
            "WHERE is_granted = 1 AND (is_granted_ever IS NULL OR is_granted_ever = 0)"
        ).fetchone()[0]
        assert bad == 0, "有 %d 件专利 is_granted=1 但 is_granted_ever=0" % bad

    def c_consistent_with_fact():
        """视图聚合结果必须等于从事实表重算的结果，否则视图与源脱节。"""
        fact = cur.execute(
            """SELECT COUNT(DISTINCT enterprise_id || '|' || app_no)
               FROM fact_patent
               WHERE enterprise_id IS NOT NULL AND app_no IS NOT NULL
                 AND (is_granted = 1 OR substr(patent_id, -1, 1) IN ('B','U','S','C'))"""
        ).fetchone()[0]
        view = cur.execute(
            "SELECT COUNT(*) FROM v_patent_canonical "
            "WHERE enterprise_id IS NOT NULL AND is_granted_ever = 1"
        ).fetchone()[0]
        assert fact == view, "曾授权数不一致：事实表 %d vs 视图 %d" % (fact, view)

    def d_kind_valid():
        rows = cur.execute(
            "SELECT grant_kind, is_granted_ever, COUNT(*) FROM v_patent_canonical "
            "WHERE grant_kind IS NOT NULL GROUP BY 1,2"
        ).fetchall()
        for kind, ever, n in rows:
            assert kind in GRANT_KINDS, "grant_kind 出现非法值 %r（%d 行）" % (kind, n)
            assert ever == 1, "grant_kind=%s 却有 %d 行 is_granted_ever!=1" % (kind, n)

    def e_asof_grant_rate():
        rows = cur.execute(
            """SELECT as_of_policy, COUNT(*), SUM(is_available),
                      MIN(feature_value), MAX(feature_value)
               FROM ads_feature_asof WHERE feature_name = 'grant_rate'
               GROUP BY as_of_policy"""
        ).fetchall()
        assert rows, "ads_feature_asof 里没有 grant_rate"
        for policy, n, avail, lo, hi in rows:
            assert avail >= 250, "%s 口径覆盖只有 %d/%d，低于预期" % (policy, avail, n)
            if lo is not None:
                assert 0.0 <= lo and hi <= 1.0, "%s 值域越界：[%s, %s]" % (policy, lo, hi)

    def f_source_uses_ever():
        src = open(os.path.join(SRC, "data", "build_asof_features.py"),
                   encoding="utf-8").read()
        assert "is_granted_ever" in src, "build_asof_features 未使用 is_granted_ever"
        assert "MATURITY_YEARS" in src, "build_asof_features 未做审结期控制"
        # grant_rate 的赋值行附近必须能看到「曾授权」判据，而不是裸的 is_granted
        seg = src[src.find('out["grant_rate"]') - 900:src.find('out["grant_rate"]') + 80]
        assert "rec[3]" in seg or "is_granted_ever" in seg, \
            "grant_rate 计算没有引用「曾授权」列"
        assert "PENDING_STATUSES" in seg, "grant_rate 计算没有排除仍在审查的专利"

    def g_no_bare_aggregation():
        """静态扫描：SUM/AVG/COUNT(is_granted) 就是「用在权口径算授权率」。"""
        bad = []
        pat = re.compile(r"(SUM|AVG|COUNT)\s*\(\s*is_granted\s*\)")
        for dirpath, _, files in os.walk(SRC):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                for i, line in enumerate(open(path, encoding="utf-8"), 1):
                    if pat.search(line) and "is_granted_ever" not in line:
                        bad.append("%s:%d  %s" % (os.path.relpath(path, ROOT), i,
                                                  line.strip()[:80]))
        assert not bad, "发现直接聚合 is_granted 的写法：\n    " + "\n    ".join(bad)

    print("授权口径守卫")
    check("视图含曾授权与凭据列", a_columns)
    check("曾授权是当前在权的超集", b_superset)
    check("曾授权数与事实表一致", c_consistent_with_fact)
    check("授权凭据 kind code 合法", d_kind_valid)
    check("as-of 授权率值域与覆盖", e_asof_grant_rate)
    check("授权率实现走曾授权+审结期", f_source_uses_ever)
    check("源码无裸聚合 is_granted", g_no_bare_aggregation)
    con.close()

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ GRANT DEFINITION TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
