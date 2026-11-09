# -*- coding: utf-8 -*-
"""融资特征「旁路补算 + 双键 LEFT JOIN」接入口径守卫。

防的是这类事故：v1.6 把 m_funding / investor_prestige 补算到了
ads_feature_asof_funding，但主宽表 v_model_dataset_asof_flat 与交付 CSV
里这两列仍恒 NULL。算法端照评审报告 §4.1 复算会得出「14 个零覆盖、
融资没补」的结论；也可能有人为了「看起来补上了」去原地 UPDATE 主宽表，
或只按 enterprise_id 单键连接，把 614 行撑成 1228 行伪重复。

本守卫把「这是设计、不是漏补」写成可执行的契约：
  · 文档（00_先看这个 / 两份数据字典 / 说明源码 / 交付清单）必须写明接入口径
  · 库里主宽表两列必须仍为 NULL（有人偷偷回写就失败）
  · 29 列结构与 17 列训练入口不得改动
  · 双键 JOIN 基数不变，单键 JOIN 必须被证明会产生伪重复
  · 旁路表的覆盖数用独立 SQL 从 fact_funding_event 重算对账

前置（与 test_data_dictionary.py 相同）：交付目录与 outputs 下的字典是本地生成物，
按 .gitignore 约定不进 Git，干净检出后先跑
  python -m src.data.export_data_dictionary
  python -m src.data.export_delivery --only doc
并把 outputs/data_dictionary.{md,csv} 复制到 deliverables/04_数据字典与数据集/。

用法：python tests/test_funding_join_contract.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
DOC0 = os.path.join(ROOT, "deliverables", "00_先看这个.md")
D_DATA = os.path.join(ROOT, "deliverables", "04_数据字典与数据集")
OUT = os.path.join(ROOT, "outputs")
MD_OUT = os.path.join(OUT, "data_dictionary.md")
MD_DEL = os.path.join(D_DATA, "数据字典.md")
CSV_OUT = os.path.join(OUT, "data_dictionary.csv")
CSV_DEL = os.path.join(D_DATA, "数据字典.csv")
NOTES = os.path.join(ROOT, "src", "data", "column_notes.py")
EXPORT = os.path.join(ROOT, "src", "data", "export_delivery.py")

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


def read(path):
    assert os.path.exists(path), "文件不存在：%s" % path
    return open(path, encoding="utf-8-sig").read()


def con():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def one(sql):
    c = con()
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


JOIN_TWO_KEYS = """
SELECT COUNT(*) FROM v_model_dataset_asof_flat d
LEFT JOIN v_feature_asof_funding f
  ON f.enterprise_id = d.enterprise_id
 AND f.as_of_policy  = d.as_of_policy
"""

JOIN_ONE_KEY = """
SELECT COUNT(*) FROM v_model_dataset_asof_flat d
JOIN v_feature_asof_funding f
  ON f.enterprise_id = d.enterprise_id
"""


def main():
    print("融资 LEFT JOIN 接入口径守卫")

    def a_doc_contract():
        # README 是版本库内必在的契约载体；交付说明按 .gitignore 不进 Git，存在时一并校验
        readme = read(os.path.join(ROOT, "README.md"))
        for kw in ("LEFT JOIN v_feature_asof_funding", "恒 NULL", "双键",
                   "1,228", "MNAR", "test_funding_join_contract.py", "禁止原地 UPDATE"):
            assert kw in readme, "README.md 缺契约关键词：%s" % kw
        if os.path.exists(DOC0):
            t = read(DOC0)
            for kw in ("LEFT JOIN v_feature_asof_funding", "恒 NULL", "双键",
                       "1,228", "MNAR", "test_funding_join_contract.py"):
                assert kw in t, "00_先看这个.md 缺契约关键词：%s" % kw
            assert "不要" in t and "当成「已补算」" in t, "第六节缺「别做的事」条目"

    def b_dict_md():
        a, b = read(MD_OUT), read(MD_DEL)
        assert a == b, "outputs/data_dictionary.md 与交付目录数据字典.md 不同源"
        for kw in ("恒 NULL", "LEFT JOIN v_feature_asof_funding", "双键",
                   "禁止原地 UPDATE"):
            assert kw in a, "数据字典缺契约关键词：%s" % kw
        assert "v_model_dataset_asof_flat" in a, "字典缺主宽表章节"

    def c_dict_csv():
        a, b = read(CSV_OUT), read(CSV_DEL)
        assert a == b, "outputs/data_dictionary.csv 与交付目录数据字典.csv 不同源"
        assert "LEFT JOIN v_feature_asof_funding" in a, "CSV 对象说明缺 JOIN 口径"
        assert "恒 NULL" in a, "CSV 字段说明缺「恒 NULL」警示"

    def d_notes_source():
        t = read(NOTES)
        for kw in ("恒 NULL", "LEFT JOIN v_feature_asof_funding",
                   "禁止原地 UPDATE", "enterprise_id, as_of_policy"):
            assert kw in t, "column_notes.py 缺契约关键词：%s" % kw
        assert t.count("恒 NULL") >= 2, "主宽表两列应分别标注恒 NULL"

    def e_main_still_null():
        n = one("SELECT COUNT(*) FROM v_model_dataset_asof_flat")
        a = one("SELECT COUNT(m_funding) FROM v_model_dataset_asof_flat")
        b = one("SELECT COUNT(investor_prestige) FROM v_model_dataset_asof_flat")
        assert n == 614, "主宽表行数变了：%d（应为 614）" % n
        assert a == 0 and b == 0, (
            "主宽表融资两列被回写（m_funding %d / investor_prestige %d 非空）"
            "——按契约应保持 NULL，补算只落旁路表" % (a, b))

    def f_structure_untouched():
        cols = [r[1] for r in con().execute(
            "PRAGMA table_info('v_model_train_ready')").fetchall()]
        assert len(cols) == 17, "训练入口列数变为 %d（应为 17）" % len(cols)
        assert not any(x.startswith(("m_funding", "investor_prestige")) for x in cols), (
            "融资列被塞进 v_model_train_ready")
        nf = one("SELECT COUNT(DISTINCT feature_name) FROM ads_feature_asof")
        assert nf == 29, "ads_feature_asof 特征数变为 %d（应为 29）" % nf
        ddl = one("SELECT sql FROM sqlite_master WHERE name='v_model_dataset_asof_flat'")
        assert "ads_feature_asof_funding" not in ddl, (
            "主宽表视图被改成引用旁路表，29 列结构契约被破坏")

    def g_bypass_table():
        n = one("SELECT COUNT(*) FROM ads_feature_asof_funding")
        assert n == 614, "旁路表行数 %d（应为 614）" % n
        pair = con().execute(
            "SELECT as_of_policy, COUNT(*) FROM ads_feature_asof_funding "
            "GROUP BY 1 ORDER BY 1").fetchall()
        assert pair == [("cohort", 307), ("due_minus_1", 307)], (
            "两口径行数异常：%s" % (pair,))
        uniq = one("SELECT COUNT(DISTINCT enterprise_id || '|' || as_of_policy) "
                   "FROM ads_feature_asof_funding")
        assert uniq == 614, "JOIN 双键不唯一：%d" % uniq
        bad = one("SELECT COUNT(*) FROM ads_feature_asof_funding "
                  "WHERE m_funding NOT IN (0,1) "
                  "  OR investor_prestige_best NOT BETWEEN 1 AND 4 "
                  "  OR investor_prestige_latest NOT BETWEEN 1 AND 4")
        assert bad == 0, "值域越界 %d 行" % bad

    def h_join_cardinality():
        two, one_key = one(JOIN_TWO_KEYS), one(JOIN_ONE_KEY)
        assert two == 614, "双键 LEFT JOIN 后行数 %d（应为 614，不得产生伪重复）" % two
        assert one_key == 1228, (
            "单键 JOIN 行数 %d（应为 1228）——伪重复证据消失，需复核口径" % one_key)

    def i_independent_recompute():
        # 不依赖旁路表，直接从融资事件 + 队列年重算两个口径的覆盖率
        q = ("SELECT COUNT(DISTINCT e.enterprise_id) FROM fact_funding_event e "
             "JOIN ads_label l ON l.enterprise_id=e.enterprise_id "
             "AND l.label_task='qual_survival' WHERE e.event_year <= l.cohort_year + %d")
        ind_cohort, ind_due = one(q % 0), one(q % 2)
        got = con().execute(
            "SELECT as_of_policy, SUM(CASE WHEN m_funding=1 THEN 1 ELSE 0 END) "
            "FROM ads_feature_asof_funding GROUP BY 1 ORDER BY 1").fetchall()
        assert got == [("cohort", ind_cohort), ("due_minus_1", ind_due)], (
            "旁路表覆盖 %s 与独立重算 cohort=%d / due_minus_1=%d 不一致"
            % (got, ind_cohort, ind_due))
        assert ind_cohort == 60 and ind_due == 95, (
            "独立重算基准漂移：cohort %d / due_minus_1 %d（应 60 / 95）"
            % (ind_cohort, ind_due))

    def j_delivery_checklist():
        t = read(EXPORT)
        assert "test_funding_join_contract.py" in t, "export_delivery.py 未登记新测试"
        assert "test_funding_provenance.py" in t, "export_delivery.py 未登记 A7 溯源守卫"
        assert "222 项" in t, "export_delivery.py 合计项数未同步为 206"
        assert "20 个测试文件" in t, "export_delivery.py 文件数未同步为 19"
        assert "test_schema.py" in t and "test_collect_pool.py" in t, (
            "export_delivery.py 未登记两个已纳入的历史自测")
        assert "222 项" in read(os.path.join(ROOT, "README.md")), "README 项数未同步"
        if os.path.exists(DOC0):
            d = read(DOC0)
            assert "test_funding_join_contract.py" in d and "222 项" in d, (
                "00_先看这个.md 的守卫清单未同步")

    check("README 与交付说明写明接入口径", a_doc_contract)
    check("两份数据字典同源且含契约", b_dict_md)
    check("两份字典 CSV 同源且含契约", c_dict_csv)
    check("说明源码含契约文本", d_notes_source)
    check("主宽表两列仍恒 NULL", e_main_still_null)
    check("29 列结构与 17 列入口未改", f_structure_untouched)
    check("旁路表 614 行 / 键唯一 / 值域", g_bypass_table)
    check("双键 JOIN 基数不变", h_join_cardinality)
    check("独立 SQL 对账覆盖 60 / 95", i_independent_recompute)
    check("交付清单同步 222 项", j_delivery_checklist)

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ FUNDING JOIN CONTRACT TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
