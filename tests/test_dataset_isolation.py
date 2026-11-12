# -*- coding: utf-8 -*-
"""A/B 两套数据隔离 + 企业分档端点守卫（v1.8）。

守的是评审报告 §7.1(3) 那条红线：A（10 月信贷面板）与 B（500 家高企）
样本、特征、标签完全不同，混在同一 API 里最容易被前端画成同一条曲线。
隔离做成显式 dataset 参数，默认 all 保持向后兼容，但任何取值都标
allow_cross_join=false。

八项断言，全部直接调用 api 函数（不依赖服务是否启动）。

用法：python tests/test_dataset_isolation.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
CDIR = os.path.join(ROOT, "outputs", "contracts")
sys.path.insert(0, ROOT)

from src.app.backend import api          # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))


def con():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def contract(name):
    p = os.path.join(CDIR, name)
    assert os.path.exists(p), "契约缺失：%s（先跑 build_contracts）" % p
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
def a_default_backward_compatible():
    r = api.data_pipeline()
    assert r["code"] == 0, "默认调用失败：%s" % r["code"]
    d = r["data"]
    for k in ("live", "registry", "legacy_ledger"):
        assert k in d, "默认返回结构缺 %s（向后兼容被破坏）" % k
    assert r["meta"]["dataset"] == "all"
    assert r["meta"]["isolated"] is False, "默认 all 不应标成已隔离"


def b_isolate_a():
    r = api.data_pipeline("A_credit_panel")
    assert r["code"] == 0
    d = r["data"]
    assert set(d) == {"legacy_ledger"}, "A 档仍混入 B 套数据：%s" % sorted(d)
    assert d["legacy_ledger"] is not None and "gaps" in d["legacy_ledger"]
    assert r["meta"]["isolated"] is True


def c_isolate_b():
    r = api.data_pipeline("B_proxy_label_500")
    assert r["code"] == 0
    d = r["data"]
    assert set(d) == {"live", "registry"}, "B 档仍混入 A 套数据：%s" % sorted(d)
    assert "legacy_ledger" not in d
    assert d["live"]["n_tables"] > 0
    assert r["meta"]["isolated"] is True


def d_invalid_dataset_is_business_code():
    r = api.data_pipeline("NOT_A_DATASET")
    assert r["code"] == "INVALID_DATASET", "非法参数未返回业务码：%s" % r["code"]
    assert r["data"] is None
    assert "允许值" in (r["meta"].get("detail") or "")
    # 直接调用不抛 HTTPException，即 HTTP 恒 200 的约定保持


def e_no_cross_join_ever():
    for ds in ("all", "A_credit_panel", "B_proxy_label_500"):
        r = api.data_pipeline(ds)
        assert r["meta"]["allow_cross_join"] is False, "%s 允许了跨集合并" % ds
        assert "1,714" in r["meta"]["join_rule"] and "500" in r["meta"]["join_rule"]
        assert r["meta"]["dataset_ids"] == ["A_credit_panel", "B_proxy_label_500"]
    # 非法取值走 pending()，其 meta 不含业务字段，但绝不能返回 code=0 的成功包
    assert api.data_pipeline("NOT_A_DATASET")["code"] != 0
    assert api.DATASET_ALLOWED == ("all", "A_credit_panel", "B_proxy_label_500")


def f_datasets_registry_matches_db():
    r = api.data_datasets()
    assert r["code"] == 0 and r["meta"]["allow_cross_join"] is False
    ds = {x["id"]: x for x in r["data"]["datasets"]}
    assert set(ds) == {"A_credit_panel", "B_proxy_label_500"}
    assert ds["A_credit_panel"]["in_this_db"] is False
    assert ds["B_proxy_label_500"]["in_this_db"] is True
    c = con()
    try:
        n_ent = c.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
        n_lab = c.execute("SELECT COUNT(*) FROM ads_label "
                          "WHERE label_task='qual_survival'").fetchone()[0]
    finally:
        c.close()
    assert ds["B_proxy_label_500"]["n_entity"] == n_ent
    assert ds["B_proxy_label_500"]["n_labeled"] == n_lab
    assert r["data"]["isolation"]["param"] == "dataset"


def g_cross_dataset_endpoints_labeled():
    b = api.model_benchmark()
    assert b["meta"]["dataset_scope"] == "A_credit_panel"
    assert b["meta"]["allow_cross_join"] is False
    f = api.factors_importance()
    assert f["meta"]["dataset_scope"] == ["B_proxy_label_500", "A_credit_panel"]
    assert f["meta"]["mixed_in_one_response"] is True
    assert f["meta"]["allow_cross_join"] is False
    assert "/api/enterprise/grade" in api.READY_ENDPOINTS
    assert "/api/data/datasets" in api.READY_ENDPOINTS


def h_grade_contract_matches_db():
    r = api.enterprise_grade()
    assert r["code"] == 0, "分档端点未就绪：%s" % r["code"]
    d = contract("enterprise_grade.json")
    assert d["provenance"]["kind"] == "measured"
    assert d["provenance"]["schema_version"] in (
        "v1.8", "v1.9", "v2.0", "v2.1", "v2.2", "unknown") or \
        d["provenance"]["schema_version"].startswith(("v1.", "v2.")), (
        "契约 schema_version 未回填：%r" % d["provenance"]["schema_version"])
    c = con()
    try:
        want = dict(c.execute(
            "SELECT grade, COUNT(*) FROM v_enterprise_grade_l0 GROUP BY grade").fetchall())
        total = c.execute("SELECT COUNT(*) FROM v_enterprise_grade_l0").fetchone()[0]
    finally:
        c.close()
    got = {x["grade"]: x["n"] for x in d["grade_dist"]}
    assert got == want, "契约五档家数与库内不符：%r vs %r" % (got, want)
    assert d["n_enterprise"] == total == 500
    ub = d["usage_boundary"]
    for k in ("is_label", "is_credit_score", "can_report_accuracy",
              "can_enter_model_training"):
        assert ub[k] is False, "usage_boundary.%s 未标 False" % k
    assert "qcc_tech_level_ord" in ub["forbidden_validation_fields"]


def main():
    print("=" * 68)
    print("  A/B 数据隔离 + 企业分档端点守卫")
    print("=" * 68)
    check("默认 all 保持向后兼容结构", a_default_backward_compatible)
    check("A 档只返回旧面板留档", b_isolate_a)
    check("B 档只返回主数据 live + registry", c_isolate_b)
    check("非法 dataset 返回业务码而非异常", d_invalid_dataset_is_business_code)
    check("任何取值都禁止跨集合并", e_no_cross_join_ever)
    check("两套数据台账与库内实测一致", f_datasets_registry_matches_db)
    check("跨数据集端点均标注 scope", g_cross_dataset_endpoints_labeled)
    check("分档契约家数与使用边界对账", h_grade_contract_matches_db)
    print("-" * 68)
    if FAIL:
        print("❌ DATASET ISOLATION TESTS FAILED（%d 过 / %d 败）" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  · %s: %s" % (n, e))
        sys.exit(1)
    print("✅ DATASET ISOLATION TESTS PASSED（%d 项）" % len(PASS))


if __name__ == "__main__":
    main()
