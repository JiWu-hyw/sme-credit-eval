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
  3. 核心分布数字与库内实测一致（总数 / 断档样本 / 断档样本占比 / excluded 分解）
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
    # 排除分解：打标家数取 ads_label，excluded 取 ads_model_dataset（两者必须自洽）
    n_excl = cur.execute(
        "SELECT COUNT(*) FROM ads_model_dataset WHERE split='excluded'").fetchone()[0]
    n_lab_ds = cur.execute(
        "SELECT COUNT(*) FROM ads_model_dataset WHERE split IN ('train','val','test')"
    ).fetchone()[0]
    n_ds = cur.execute("SELECT COUNT(*) FROM ads_model_dataset").fetchone()[0]
    con.close()
    assert n_lab_ds == n_label and n_lab_ds + n_excl == n_ds, (
        "库内自洽性破裂：ads_label %d / 入模 %d / excluded %d / 总 %d"
        % (n_label, n_lab_ds, n_excl, n_ds))

    def _sec7_counts():
        """解析第 7 节表格的企业数列，返回 (各类家数列表, 合计)。"""
        sec = text.split("## 7.")[1].split("## 8.")[0]
        rows = [l for l in sec.splitlines() if l.startswith("|")]
        body, total = [], None
        for r in rows[2:]:
            cells = [x.strip() for x in r.strip("|").split("|")]
            if len(cells) < 2:
                continue
            try:
                n = int(cells[1].replace("**", "").strip())
            except ValueError:
                continue
            if cells[0].startswith("合计"):
                total = n
            else:
                body.append(n)
        return body, total

    def a_not_stale():
        assert os.path.getsize(MD) > 3000, "说明书异常过小"
        assert os.path.getmtime(MD) >= os.path.getmtime(DB) - 1, (
            "说明书比库旧，请重跑 python -m src.data.export_label_spec")

    def b_version_consistent():
        assert vers == [DEFINITION_VER], (
            "库内 definition_ver=%s ≠ 代码常量 %s" % (vers, DEFINITION_VER))
        assert DEFINITION_VER in text, "说明书里没有出现当前定义版本 %s" % DEFINITION_VER

    def c_numbers_match():
        want = "| 有多少 | %d 家：断档样本 **%d**、续存样本 %d，断档样本占比 **%.2f%%** |" % (
            n_label, n_pos, n_neg, 100.0 * n_pos / n_label)
        assert want in text, "核心分布行与库内实测不一致，期望：\n    %s" % want
        assert "excluded" in text, "说明书未提 excluded"
        body, total = _sec7_counts()
        assert body and total is not None, "第 7 节排除分解表解析失败"
        assert sum(body) == n_firm == total, (
            "第 7 节分解 %s 合计 %d，与企业总数 %d / 表内合计 %s 不一致"
            % (body, sum(body), n_firm, total))
        assert n_label in body, "第 7 节缺打标家数 %d（库内 ads_label）" % n_label
        assert sum(body) - n_label == n_excl, (
            "第 7 节排除家数 %d ≠ 库内 excluded %d" % (sum(body) - n_label, n_excl))

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
