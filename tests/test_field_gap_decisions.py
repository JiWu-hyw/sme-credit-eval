# -*- coding: utf-8 -*-
"""缺口定稿落地守卫（评审 §6 T22 / §5.2）。

防的是：定稿结论只躺在 configs/field_gap_registry.json 里，
算法端交付目录看不到，评审反复判「8 列全空、未定稿」；
以及 dataset_card 又把「没再出现在名单」写成「复核未通过」。

用法：python tests/test_field_gap_decisions.py
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
REG = os.path.join(ROOT, "configs", "field_gap_registry.json")
REPORT = os.path.join(ROOT, "outputs", "field_gap_decisions.md")
REPORT_JSON = os.path.join(ROOT, "outputs", "field_gap_decisions.json")
D_ALG = os.path.join(ROOT, "deliverables", "01_算法端")
CARD = os.path.join(D_ALG, "dataset_card.md")
ALG_README = os.path.join(D_ALG, "README.md")
DELIVERY_PY = os.path.join(ROOT, "src", "data", "export_delivery.py")

PRODUCTS = (
    "graph_temporal_contract.md", "graph_temporal_contract.json",
    "field_gap_decisions.md", "field_gap_decisions.json",
    "shap_legacy_notice.md",
)
WIDE_EMPTY = (
    "patent_citation", "patent_triple_score", "rev_cagr", "emp_cagr",
    "funding_to_last_val", "m_team", "m_cite", "cap_tech_synergy",
)

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


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    src = read(REPORT_JSON) if os.path.exists(REPORT_JSON) else None

    def t_products_exist():
        for p in (REPORT, REPORT_JSON):
            assert os.path.exists(p), "缺产物 %s" % os.path.relpath(p, ROOT)
        for fn in PRODUCTS:
            assert os.path.exists(os.path.join(D_ALG, fn)), (
                "交付目录 01_算法端/ 缺 %s" % fn)

    def t_report_structure():
        R = json.loads(src)
        assert R["n_items"] == 17, "定稿条目数 %d（应 17）" % R["n_items"]
        assert len(R["substitutes"]) == 10, "替代特征条数异常"
        assert len(R["graph_gaps"]) == 3, "图侧定稿条数异常"
        assert set(R["by_status"]) >= {"null_by_design", "source_missing",
                                       "partial_insufficient"}, "状态枚举不全"

    def t_measurements_match_db():
        """报告里的实测覆盖率必须能在库里现场复算出来。"""
        R = json.loads(src)
        c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        try:
            for it in R["items"][:5] + R["items"][-3:]:
                t, col = it["table"], it["field"]
                cols = {r[1] for r in c.execute('PRAGMA table_info("%s")' % t)}
                assert col in cols, "登记册指向不存在的列 %s.%s" % (t, col)
                n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
                nn = c.execute('SELECT COUNT("%s") FROM "%s"' % (col, t)).fetchone()[0]
                cov = it["measured_coverage"]
                assert cov and cov["non_null"] == nn and cov["total"] == n, (
                    "%s.%s 覆盖率与库不一致" % (t, col))
        finally:
            c.close()

    def t_wide_empty_verified():
        R = json.loads(src)
        assert set(R["wide_table_empty_columns"]) == set(WIDE_EMPTY), (
            "主宽表 8 列清单不完整")
        for k, v in R["wide_table_empty_columns"].items():
            assert v["non_null"] == 0, "%s 实测 %s 个非空（应 0）" % (k, v["non_null"])

    def t_registry_is_authority():
        reg = json.load(open(REG, encoding="utf-8"))
        R = json.loads(src)
        assert R["registry_version"] == reg["meta"]["version"], "登记册版本不同步"
        reg_fields = {(i["table"], i["field"]) for i in reg["items"]}
        rep_fields = {(i["table"], i["field"]) for i in R["items"]}
        assert reg_fields == rep_fields, "报告条目与登记册不一致（报告可能被手改）"
        assert R["policy"] == reg["meta"]["policy"], "定稿原则未同步"

    def t_md_has_sections():
        s = read(REPORT)
        for key in ("主宽表 8 个全空列", "逐字段定稿", "替代特征清单",
                    "图侧缺口定稿", "使用规则", "DIRECTION_NOTICE"):
            assert key in s, "报告缺要点：%s" % key
        assert "null_by_design" in s and "source_missing" in s, "缺状态说明"

    def t_delivery_same_source():
        """按用户约定：散文件随时整合到最新，交付目录与 outputs 字节同源。"""
        for fn in PRODUCTS:
            a, b = os.path.join(ROOT, "outputs", fn), os.path.join(D_ALG, fn)
            assert os.path.exists(a) and os.path.exists(b), "缺 %s" % fn
            ha = hashlib.sha256(open(a, "rb").read()).hexdigest()
            hb = hashlib.sha256(open(b, "rb").read()).hexdigest()
            assert ha == hb, "交付目录的 %s 与 outputs 不同源" % fn

    def t_delivery_script_lists_products():
        s = read(DELIVERY_PY)
        for fn in PRODUCTS:
            assert fn in s, "export_delivery.py 复制清单漏了 %s（重生成会丢）" % fn
        assert "field_gap_decisions.md" in s and "graph_temporal_contract.md" in s, (
            "算法端 README 生成段未列出新产物")

    def t_card_label_wording_fixed():
        for p in (CARD, DELIVERY_PY):
            s = read(p).replace("**", "")
            assert "未再出现在后续任何公示名单" in s, (
                "%s 仍用「未通过复核」旧措辞" % os.path.relpath(p, ROOT))
            assert "不等于官方" in s, (
                "%s 缺「不等于复核未通过」澄清" % os.path.relpath(p, ROOT))
            assert "表示「高企资格 3 年到期后未通过复核」" not in s, (
                "%s 残留旧断言" % os.path.relpath(p, ROOT))

    def t_readme_lists_products():
        s = read(ALG_README)
        for fn in ("field_gap_decisions.md", "graph_temporal_contract.md"):
            assert fn in s, "算法端 README 清单缺 %s" % fn

    def t_regenerate_idempotent():
        before = hashlib.sha256(open(REPORT, "rb").read()).hexdigest()
        p = subprocess.run(
            [sys.executable, "-m", "src.data.export_field_gap_report"],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, "重生成失败：%s" % p.stdout.strip()[-300:]
        after = hashlib.sha256(open(REPORT, "rb").read()).hexdigest()
        assert before == after, "重生成改变了报告内容（不稳定，无法做同源校验）"

    for name, fn in [
        ("报告与交付目录产物齐备", t_products_exist),
        ("报告结构：17 定稿 / 10 替代 / 3 图缺口", t_report_structure),
        ("实测覆盖率可在库里现场复算", t_measurements_match_db),
        ("主宽表 8 列现场核对为全空", t_wide_empty_verified),
        ("登记册是唯一权威源", t_registry_is_authority),
        ("报告含全部章节与方向声明", t_md_has_sections),
        ("交付散文件与 outputs 字节同源", t_delivery_same_source),
        ("export_delivery.py 复制清单已登记", t_delivery_script_lists_products),
        ("dataset_card 标签措辞已纠偏", t_card_label_wording_fixed),
        ("算法端 README 清单已同步", t_readme_lists_products),
        ("重生成幂等", t_regenerate_idempotent),
    ]:
        check(name, fn)

    print("\n%d 项通过 / %d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  FAIL %s: %s" % (n, e))
        return 1
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
