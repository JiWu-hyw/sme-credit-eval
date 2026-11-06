# -*- coding: utf-8 -*-
"""代理标签说明书守卫测试。

防两类事故：
  1) **口径漂移**：`build_qual_dataset.py` 里的定义版本 / 有效期变了，说明书还写着旧口径，
     评审照着文档理解标签，理解错的就是整篇结论。
  2) **说明书被当营销材料改写**：把「代理标签」「不等同官方结论」这类限定语删掉，
     只留下好看的分布数字 —— 那这份文档就从防护变成误导。

六条断言：
  1. 说明书存在，且不早于库文件（不陈旧）
  2. `definition_ver` 与代码常量、库内存值三者一致，且出现在文档里
  3. 核心分布数字与库内实测一致（总数 / 正例 / 正例率 / excluded 分解）
  4. 所有 Markdown 表格行结构完整（首尾都有竖线，防渲染塌行）
  5. 限定语齐全（代理标签 / 不等同 / 结构占位 / 禁止入模）
  6. 建模契约齐全（时点视图 / 分组切分 / 排除 excluded）

用法：python tests/test_label_spec.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.data.build_qual_dataset import (          # noqa: E402
    DEFINITION_VER,
    OBS_END,
    VALID_YEARS,
)

DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
MD = os.path.join(ROOT, "outputs", "label_spec.md")

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


def main():
    print("代理标签说明书守卫")
    assert os.path.exists(MD), "说明书不存在：%s" % MD
    text = open(MD, encoding="utf-8").read()

    con = sqlite3.connect(DB)
    cur = con.cursor()
    n_label = cur.execute("SELECT COUNT(*) FROM ads_label").fetchone()[0]
    n_pos = cur.execute("SELECT COUNT(*) FROM ads_label WHERE label_value=1").fetchone()[0]
    n_neg = n_label - n_pos
    n_firm = cur.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    vers = [r[0] for r in cur.execute("SELECT DISTINCT definition_ver FROM ads_label")]
    # 排除分解
    first = {}
    for eid, y in cur.execute(
            "SELECT enterprise_id, year FROM fact_qualification WHERE qual_type='high_tech'"):
        y = int(y)
        first[eid] = min(y, first.get(eid, y))
    n_no_qual = n_firm - len(first)
    n_due_after = sum(1 for v in first.values() if v + VALID_YEARS > OBS_END)
    con.close()

    def a_not_stale():
        assert os.path.getsize(MD) > 3000, "说明书异常过小"
        assert os.path.getmtime(MD) >= os.path.getmtime(DB) - 1, (
            "说明书比库旧，请重跑 python -m src.data.export_label_spec")

    def b_version_consistent():
        assert vers == [DEFINITION_VER], (
            "库内 definition_ver=%s ≠ 代码常量 %s" % (vers, DEFINITION_VER))
        assert DEFINITION_VER in text, "说明书里没有出现当前定义版本 %s" % DEFINITION_VER

    def c_numbers_match():
        want = "| 有多少 | %d 家：正例 **%d**、负例 %d，正例率 **%.2f%%** |" % (
            n_label, n_pos, n_neg, 100.0 * n_pos / n_label)
        assert want in text, "核心分布行与库内实测不一致，期望：\n    %s" % want
        assert "excluded" in text, "说明书未提 excluded"
        assert "| 首次认定 ≥ %d，到期年 > %d（窗口未闭合） | %d |" % (
            OBS_END - VALID_YEARS + 1, OBS_END, n_due_after) in text, (
            "排除分解（窗口未闭合 %d 家）与库内实测不一致" % n_due_after)
        assert "| 完全没有 high_tech 记录 | %d |" % n_no_qual in text, (
            "无 high_tech 记录家数 %d 与文档不一致" % n_no_qual)

    def d_tables_wellformed():
        bad = [l for l in text.splitlines() if l.startswith("|") and not l.rstrip().endswith("|")]
        assert not bad, "%d 行表格缺收尾竖线，渲染会塌：\n    %s" % (
            len(bad), "\n    ".join(b[:70] for b in bad[:5]))

    def e_disclaimers_present():
        need = {
            "代理标签": "必须明确声明这是代理标签",
            "不等同": "必须声明不等同官方复核结论",
            "结构占位": "必须解释 excluded 的 label=0 是占位",
            "禁止入模": "必须写明 excluded 禁止入模",
        }
        miss = [v for k, v in need.items() if k not in text]
        assert not miss, "限定语缺失：" + "、".join(miss)

    def f_modeling_contract():
        need = {
            "v_model_dataset_asof_flat": "必须指明唯一无泄漏入口",
            "enterprise_id": "必须写明按企业分组切分",
            "as_of_policy": "必须写明时点口径不可混用",
        }
        miss = [v for k, v in need.items() if k not in text]
        assert not miss, "建模契约缺失：" + "、".join(miss)

    check("说明书存在且不早于库", a_not_stale)
    check("定义版本三处一致", b_version_consistent)
    check("核心数字与库一致", c_numbers_match)
    check("表格结构完整", d_tables_wellformed)
    check("限定语齐全", e_disclaimers_present)
    check("建模契约齐全", f_modeling_contract)

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ LABEL SPEC TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
