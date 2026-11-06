# -*- coding: utf-8 -*-
"""API 真实数据层守卫测试。

守的是「接口返回的东西到底是真数据还是空壳」。原来的实现有六处问题，
每一条都能让前端在演示当天翻车：

  1. `meta.schema_version` 写死 `v1.0`，库已到 v1.3 —— 前端拿它做版本判断必错
  2. `/api/score/company/{id}` 依赖从未生成的 `dashboard_data.json`，缺失时
     直接 `raise HTTPException(404)`，违反「HTTP 恒 200，成败看 code」的契约
  3. `/api/company/diagnose` 返回 `{company_id, score:null, graph:null, explanation:null}`
     —— code=0、data 非 null，但只有一个标识字段有值，是**空壳**
  4. `/api/company/{id}/graph` 对所有企业返回同一张 demo 图（源码自己承认未过滤）
  5. `/api/overview/summary.kpi` 恒为 null
  6. `/api/data/pipeline` 透传 10 月旧台账，其中 P0-4 写着「专利完全缺失」，
     而库里已有 3 万件

八条断言：

  1. `VERSION_META.schema_version` == 库内 `meta_schema_version` 最新值
  2. 只读连接真的只读（尝试写入必须失败）
  3. 未知企业返回业务码 `NOT_FOUND`，且**不抛 HTTPException**
  4. 真实企业的 score 返回体：档案/特征/代理分非空，`score.value` 明确为 null
  5. diagnose 不是空壳（非空字段 >= 5），且 graph 已按企业过滤
  6. overview.kpi 非 null 且企业数/专利数与库一致
  7. pipeline 同时有 live 与 registry，且 P0-4 被检出 `contradicted=true`
  8. factors.importance 的 primary 属 B 套且 items 非空；legacy 属 A 套

用法：python tests/test_api_live.py
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
sys.path.insert(0, ROOT)

from src.app.backend import api, db_views          # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))


def _db():
    return db_views.connect(DB)


def _a_company():
    con = _db()
    try:
        return db_views.q1(con, "SELECT enterprise_id FROM dim_enterprise "
                                "ORDER BY enterprise_id LIMIT 1")
    finally:
        con.close()


def t_schema_version():
    con = _db()
    try:
        want = db_views.schema_version(con)
    finally:
        con.close()
    got = api.VERSION_META["schema_version"]
    assert got == want, "meta.schema_version=%s，库内实际=%s（写死常量会漂移）" % (got, want)


def t_readonly():
    con = _db()
    try:
        try:
            con.execute("CREATE TABLE _guard_should_fail (x)")
            con.rollback()
        except sqlite3.OperationalError:
            return
        raise AssertionError("只读连接竟然能写入，db_views.connect 的连接串被改坏了")
    finally:
        con.close()


def t_not_found_no_404():
    r = api.score_company("CN-330100-0001")      # 错误 ID 格式
    assert isinstance(r, dict), "返回了非 dict（HTTPException？）"
    assert r["code"] == "NOT_FOUND", "未知企业应返回 NOT_FOUND，实际 %s" % r["code"]
    assert r["data"] is None and "ENT" in (r["meta"].get("detail") or ""), \
        "错误提示没说明正确 ID 格式，前端会反复试错"


def t_score_real():
    eid = _a_company()
    r = api.score_company(eid)
    assert r["code"] == 0, "真实企业返回 %s" % r["code"]
    d = r["data"]
    for k in ("profile", "features", "patent", "proxy_score", "feature_source"):
        assert d.get(k), "字段 %s 为空" % k
    assert d["score"]["value"] is None, "没有验证过的模型却给了模型分"
    assert d["score"]["status"] == "MODEL_PENDING"
    assert d["proxy_score"]["validated"] is False, "代理分必须标注 validated=false"
    assert "caveat" in d["proxy_score"], "代理分缺 caveat 说明"


def t_diagnose_not_hollow():
    eid = _a_company()
    r = api._diagnose(eid)   # 同步核心；端点 company_diagnose 是 async 包装
    assert r["code"] == 0
    d = r["data"]
    nonempty = [k for k, v in d.items()
                if v not in (None, "", [], {})]
    assert len(nonempty) >= 5, "diagnose 仍是空壳：非空字段只有 %s" % nonempty
    assert d["graph"].get("nodes"), "图谱为空"
    ids = {n["id"] for n in d["graph"]["nodes"]}
    assert eid in ids, "图谱中心节点不是该企业（未按 company_id 过滤）"


def t_overview_kpi():
    r = api.overview_summary()
    assert r["code"] == 0
    kpi = r["data"].get("kpi")
    assert kpi and kpi.get("n_enterprise"), "kpi 为 null 或空"
    con = _db()
    try:
        n_ent = db_views.q1(con, "SELECT COUNT(*) FROM dim_enterprise")
        n_pat = db_views.q1(con, "SELECT COUNT(DISTINCT app_no) FROM v_patent_canonical")
    finally:
        con.close()
    assert kpi["n_enterprise"] == n_ent, "kpi 企业数与库不一致"
    assert kpi["n_patent"] == n_pat, "kpi 专利数口径应为申请号去重"


def t_pipeline_live():
    r = api.data_pipeline()
    assert r["code"] == 0
    d = r["data"]
    assert d["live"]["n_tables"] > 0 and d["live"]["stages"], "live 段为空"
    assert d["registry"] and d["registry"]["n_items"] > 0, "缺口登记册未接入"
    legacy = d.get("legacy_ledger") or {}
    assert legacy.get("deprecated") is True, "旧台账未标 deprecated，前端会当权威数据"
    p04 = [g for g in legacy.get("gaps", []) if g.get("id") == "P0-4"]
    assert p04 and p04[0].get("contradicted") is True, \
        "P0-4『专利完全缺失』未被检出矛盾，前端会照它画缺口图"


def t_importance_two_datasets():
    r = api.factors_importance()
    assert r["code"] == 0
    p = r["data"]["primary"]
    assert p["items"], "primary 无因子"
    assert "B：" in p["dataset"] or "500" in p["dataset"], "primary 数据集标注不对"
    assert p["method"].startswith("单变量"), "primary 方法应标明单变量 AUC"
    legacy = r["data"].get("legacy") or {}
    assert legacy.get("items"), "A 套 SHAP 未附带"
    assert "A：" in legacy["dataset"], "legacy 数据集标注不对"


def t_enterprise_profile():
    eid = _a_company()
    r = api.enterprise(eid)
    assert r["code"] == 0, "企业档案返回 %s（不应再恒 LABEL_PENDING）" % r["code"]
    d = r["data"]
    assert d["profile"] and d["features"], "档案或特征为空"
    assert d["official_label_pending"] is True, "正式 label 待确认的标记丢了"
    assert d["proxy_score"]["validated"] is False


def main():
    print("=" * 68)
    print("  API 真实数据层守卫测试")
    print("=" * 68)
    check("1. meta.schema_version 与库一致", t_schema_version)
    check("2. 数据库连接只读", t_readonly)
    check("3. 未知企业返回业务码而非 404", t_not_found_no_404)
    check("4. 单企业评分返回真实数据", t_score_real)
    check("5. diagnose 非空壳且图谱按企业过滤", t_diagnose_not_hollow)
    check("6. overview.kpi 与库一致", t_overview_kpi)
    check("7. pipeline 有 live 且旧台账被标矛盾", t_pipeline_live)
    check("8. 两套因子分开返回", t_importance_two_datasets)
    check("9. 企业档案不再整块 LABEL_PENDING", t_enterprise_profile)

    print("-" * 68)
    if FAIL:
        print("❌ API LIVE TESTS FAILED（%d 过 / %d 败）" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  · %s: %s" % (n, e))
        sys.exit(1)
    print("✅ API LIVE TESTS PASSED（%d 项）" % len(PASS))


if __name__ == "__main__":
    main()
