# -*- coding: utf-8 -*-
"""训练入口视图 `v_model_train_ready` 守卫测试。

这层视图的唯一职责是把三个结构性陷阱在物理上挡掉，本测试就是守这三件事：

  A. **假负例隔离（P1-4）** —— ads_model_dataset 有 193 行 split='excluded'
     （cohort 2023+、观察窗未闭合，label 填 0 只是占位）。谁直接
     `SELECT * FROM ads_model_dataset` 训练，正例率就被从 6.8% 稀释到 4.2%。

  B. **伪重复（一家两行）** —— v_model_dataset_asof_flat 是 614 行 =
     307 家 × 2 种 as_of_policy。按行训练同一企业被算两次权重，正例 21 虚增成 30。

  C. **常量列** —— cohort 切面下 m_cert / qual_rank / qual_rank_delta /
     qual_upgrade_cum 四列去重值恒为 1，单变量 AUC 全 = 0.5000。带进模型
     只会污染重要性排序。

八条断言：
  1. 视图存在，且列集合与预期一致（多一列少一列都算口径漂移）
  2. 行数 == 企业去重数（无伪重复），且 == 307
  3. 零 excluded 行
  4. split 只含 train/val/test，三集齐全
  5. 每个 enterprise_id 只出现在一个 split（无跨集泄漏）
  6. 正例数 == 21，且与 ads_label 口径一致
  7. 所有特征列在 cohort 内去重值 > 1（无常量列）
  8. schema.sql 里有 [TRAINREADY] 块，且视图 DDL 用 as_of_policy='cohort'

用法：python tests/test_train_ready.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
SCHEMA = os.path.join(ROOT, "src", "data", "schema.sql")

# 视图列口径：标识 6 列 + 特征 11 列。改动即需同步本测试与数据字典。
ID_COLS = ["enterprise_id", "name_display", "region_code",
           "cohort_year", "split", "label"]
FEAT_COLS = ["n_patent_app", "n_patent_invention", "n_patent_grant",
             "inv_patent_ratio", "tech_conversion",
             "n_ipc_subclass", "tech_breadth_entropy", "tech_breadth_ipc_class",
             "grant_rate", "core_staff_stability", "n_inventor"]
EXPECT_COLS = ID_COLS + FEAT_COLS

EXPECT_ROWS = 307
EXPECT_POS = 21

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))


def connect():
    if not os.path.exists(DB):
        sys.exit("找不到数据库：%s" % DB)
    return sqlite3.connect(DB)


def q1(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


# ---------------------------------------------------------------------------
# 1. 存在性与列口径
# ---------------------------------------------------------------------------
def t_view_exists():
    con = connect()
    n = q1(con, "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='view' AND name='v_model_train_ready'")
    assert n == 1, "视图 v_model_train_ready 不存在"

    cols = [r[1] for r in con.execute("PRAGMA table_info(v_model_train_ready)")]
    assert cols == EXPECT_COLS, (
        "列口径漂移。\n  期望 %s\n  实际 %s" % (EXPECT_COLS, cols))


# ---------------------------------------------------------------------------
# 2. 伪重复：行数必须等于企业去重数
# ---------------------------------------------------------------------------
def t_no_pseudo_duplicate():
    con = connect()
    rows = q1(con, "SELECT COUNT(*) FROM v_model_train_ready")
    firms = q1(con, "SELECT COUNT(DISTINCT enterprise_id) FROM v_model_train_ready")
    assert rows == firms, (
        "存在伪重复：%d 行但只有 %d 家企业。视图必须固定 as_of_policy='cohort'，"
        "否则同一企业会出现两行、权重翻倍。" % (rows, firms))
    assert rows == EXPECT_ROWS, "行数应为 %d，实际 %d" % (EXPECT_ROWS, rows)


# ---------------------------------------------------------------------------
# 3. 假负例必须为 0
# ---------------------------------------------------------------------------
def t_no_excluded():
    con = connect()
    n = q1(con, "SELECT COUNT(*) FROM v_model_train_ready WHERE split='excluded'")
    assert n == 0, (
        "混入 %d 行 split='excluded' 假负例。这些是 cohort 2023+ 观察窗未闭合的"
        "企业，label=0 只是占位，不能训练。" % n)

    # 反证：源表里确实有这批行，说明过滤是真生效而非数据本身没有
    src = q1(con, "SELECT COUNT(*) FROM ads_model_dataset WHERE split='excluded'")
    assert src > 0, (
        "源表 ads_model_dataset 里 excluded 行数为 0 —— 过滤条件可能失效或"
        "数据未就绪，本测试失去意义")


# ---------------------------------------------------------------------------
# 4. split 取值合法且三集齐全
# ---------------------------------------------------------------------------
def t_split_values():
    con = connect()
    got = {r[0] for r in con.execute(
        "SELECT DISTINCT split FROM v_model_train_ready")}
    bad = got - {"train", "val", "test"}
    assert not bad, "split 出现非法取值：%s" % bad
    assert got == {"train", "val", "test"}, "三集不全，实际：%s" % got


# ---------------------------------------------------------------------------
# 5. 企业级切分：一家企业只能在一个集里
# ---------------------------------------------------------------------------
def t_no_cross_split_leak():
    con = connect()
    dup = con.execute(
        "SELECT enterprise_id, COUNT(DISTINCT split) n FROM v_model_train_ready "
        "GROUP BY enterprise_id HAVING n > 1").fetchall()
    assert not dup, (
        "%d 家企业同时出现在多个 split，造成跨集泄漏：%s"
        % (len(dup), dup[:5]))


# ---------------------------------------------------------------------------
# 6. 正例数与标签口径
# ---------------------------------------------------------------------------
def t_positive_count():
    con = connect()
    pos = q1(con, "SELECT SUM(label) FROM v_model_train_ready")
    assert pos == EXPECT_POS, "正例应为 %d，实际 %s" % (EXPECT_POS, pos)

    # 与 ads_label 对齐：视图里的 label 必须等于 label_value
    mismatch = q1(con, """
        SELECT COUNT(*) FROM v_model_train_ready v
        JOIN ads_label l ON l.enterprise_id = v.enterprise_id
        WHERE v.label IS NOT l.label_value""")
    assert mismatch == 0, (
        "%d 行 label 与 ads_label.label_value 不一致，标签口径被改过" % mismatch)


# ---------------------------------------------------------------------------
# 7. 无常量列
# ---------------------------------------------------------------------------
def t_no_constant_feature():
    con = connect()
    bad = []
    for f in FEAT_COLS:
        d = q1(con, "SELECT COUNT(DISTINCT %s) FROM v_model_train_ready" % f)
        if d <= 1:
            bad.append((f, d))
    assert not bad, (
        "以下特征在 cohort 切面内是常量，必须从视图剔除（否则只会污染"
        "重要性排序）：%s" % bad)


# ---------------------------------------------------------------------------
# 8. schema.sql 同步
# ---------------------------------------------------------------------------
def t_schema_sql_sync():
    src = open(SCHEMA, encoding="utf-8").read()
    m = re.search(r"-- \[TRAINREADY:BEGIN\](.*?)-- \[TRAINREADY:END\]", src, re.S)
    assert m, "schema.sql 缺少 [TRAINREADY] 区块，新建库将没有本视图"
    ddl = m.group(1)
    assert "CREATE VIEW v_model_train_ready" in ddl or \
           "CREATE VIEW IF NOT EXISTS v_model_train_ready" in ddl, \
        "[TRAINREADY] 区块内没有 CREATE VIEW 语句"
    assert "as_of_policy" in ddl and "cohort" in ddl, (
        "视图 DDL 未固定 as_of_policy='cohort'，会产生伪重复")
    assert "excluded" in ddl or "split IN" in ddl, (
        "视图 DDL 未过滤 split，假负例会进入训练集")


def main():
    print("=" * 68)
    print("  v_model_train_ready 训练入口守卫测试")
    print("=" * 68)
    for name, fn in [
        ("视图存在且列口径一致", t_view_exists),
        ("无伪重复（行数=企业数）", t_no_pseudo_duplicate),
        ("零 excluded 假负例", t_no_excluded),
        ("split 取值合法且三集齐全", t_split_values),
        ("无跨集企业泄漏", t_no_cross_split_leak),
        ("正例数与标签口径", t_positive_count),
        ("无常量特征列", t_no_constant_feature),
        ("schema.sql 同步", t_schema_sql_sync),
    ]:
        check(name, fn)

    print("-" * 68)
    if FAIL:
        print("❌ TRAIN READY TESTS FAILED（%d 过 / %d 败）" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  · %s: %s" % (n, e))
        sys.exit(1)
    print("✅ TRAIN READY TESTS PASSED（%d 项）" % len(PASS))


if __name__ == "__main__":
    main()
