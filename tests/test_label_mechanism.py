# -*- coding: utf-8 -*-
"""标签机制与切分守卫测试（对照评审报告 §3.2 / §3.4）。

评审发现的两个「不是 bug 但必须钉住」的事实：
  · 标签与 n_events 完全分离：n_events=1 的 21 家 100% 断档样本，
    n_events>=2 的 286 家 0% 断档样本 —— 标签 ≈「是否只被认定过一次」。
    一旦有人把 n_events 加进特征，模型就是抄答案，AUC 会瞬间飙到 1.0；
  · 原切分 val 只有 2 个断档样本，早停 / 阈值 / 模型比较全部不成立。

断言：
  1. n_events 不在 v_model_train_ready 的特征列里（也不在其它任何 leak 列）
  2. 断档样本总数 21，切分 15 / 2 / 4 与交付一致（变了就要同步改文档）
  3. v_label_mechanism：n_events=1 → 断档样本占比 1.0；n_events>=2 → 0.0
  4. v_model_train_ready_cv 五折每折断档样本 >= 4（修掉「val 只有 2 个」）
  5. 企业不跨折：每个 enterprise_id 只出现在一个 fold_id
  6. v_train_ready_window_fixed 两队列窗口等长后，专利数均值差收窄

用法：python tests/test_label_mechanism.py
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")

PASS, FAIL = [], []

# 这些列一旦混进特征就是标签泄漏 —— 它们是标签的定义变量或切分变量
LEAK_COLUMNS = {"n_events", "label", "label_value", "split", "fold_id",
                "label_name", "label_task", "observation_start", "observation_end"}
# 非特征列（元数据 / 目标 / 切分），其余列一律视为喂给模型的特征
META_COLUMNS = {"enterprise_id", "name_display", "region_code", "cohort_year",
                "split", "label"}


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

    def t_no_leak_column():
        cols = {r[1] for r in cur.execute("PRAGMA table_info(v_model_train_ready)")}
        features = cols - META_COLUMNS
        hit = features & LEAK_COLUMNS
        assert not hit, "v_model_train_ready 的特征列里出现泄漏列：%s" % sorted(hit)
        assert "n_events" in {r[1] for r in cur.execute("PRAGMA table_info(ads_label)")}, \
            "ads_label 里没有 n_events，标签机制无法复核"

    def t_positive_total():
        n_pos = one("SELECT SUM(label) FROM v_model_train_ready")
        n_all = one("SELECT COUNT(*) FROM v_model_train_ready")
        assert n_all == 307, "训练集 %d 行 != 307" % n_all
        assert n_pos == 21, "断档样本 %d != 21（变化需同步 label_spec / 报告）" % n_pos

    def t_split_frozen():
        got = dict(((s, l), n) for s, l, n in cur.execute(
            "SELECT split, label, COUNT(*) FROM v_model_train_ready GROUP BY 1,2"))
        want = {("train", 0): 199, ("train", 1): 15,
                ("val", 0): 52, ("val", 1): 2,
                ("test", 0): 35, ("test", 1): 4}
        assert got == want, "切分变了：%s" % got

    def t_label_mechanism():
        rows = list(cur.execute(
            "SELECT n_events, SUM(n_positive), SUM(n_enterprise) "
            "FROM v_label_mechanism GROUP BY n_events"))
        assert rows, "v_label_mechanism 为空"
        for n_ev, n_pos, n_ent in rows:
            if n_ev == 1:
                assert n_pos == n_ent, "n_events=1 的断档样本占比不再是 100%%（%d/%d）" % (n_pos, n_ent)
            else:
                assert n_pos == 0, "n_events=%d 出现断档样本 %d 家 —— 标签机制变了，必须重写文档" % (
                    n_ev, n_pos)

    def t_cv_min_positive():
        rows = list(cur.execute(
            "SELECT fold_id, COUNT(*), SUM(label) FROM v_model_train_ready_cv GROUP BY 1"))
        assert len(rows) == 5, "折数 %d != 5" % len(rows)
        worst = min(r[2] for r in rows)
        assert worst >= 4, "最少的一折只有 %d 个断档样本（原 val 只有 2 个）" % worst
        assert sum(r[1] for r in rows) == 307, "五折行数合计 != 307"

    def t_cv_grouped():
        dup = one("SELECT COUNT(*) FROM (SELECT enterprise_id FROM v_model_train_ready_cv "
                  "GROUP BY 1 HAVING COUNT(DISTINCT fold_id) > 1)")
        assert dup == 0, "%d 家企业跨折，group 切分失效" % dup

    def t_window_fixed():
        rows = dict((r[0], r[1]) for r in cur.execute(
            "SELECT cohort_year, AVG(n_patent_window) FROM v_train_ready_window_fixed GROUP BY 1"))
        fixed = dict((r[0], r[1]) for r in cur.execute(
            "SELECT cohort_year, AVG(n_patent_3y) FROM v_train_ready_window_fixed GROUP BY 1"))
        gap_full = abs(rows[2022] - rows[2021])
        gap_fix = abs(fixed[2022] - fixed[2021])
        assert gap_fix < gap_full, "等长窗口没有收窄队列差距（%.1f -> %.1f）" % (gap_full, gap_fix)

    print("== 标签机制与切分守卫 ==")
    check("特征列无泄漏（n_events 等）", t_no_leak_column)
    check("断档样本总数 21 / 307 行", t_positive_total)
    check("原切分 15/2/4 冻结", t_split_frozen)
    check("标签与 n_events 完全分离（登记事实）", t_label_mechanism)
    check("5 折每折断档样本 >= 4", t_cv_min_positive)
    check("企业不跨折", t_cv_grouped)
    check("等长窗口收窄队列差距", t_window_fixed)

    print("\n通过 %d / 共 %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
