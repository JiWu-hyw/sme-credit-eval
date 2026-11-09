# -*- coding: utf-8 -*-
"""契约文件守卫测试 —— 守的是「契约里的数是不是真的」。

本轮把 12 个 CONTRACT_PENDING 接口补成了真实契约。补契约最危险的做法是
为了让前端有图可画而编数据，所以这里的断言全部是**反向比对**：
把契约里的数值拿去和 SQLite 现场算一遍，对不上就失败。

十项断言：

  1. 11 个契约文件都存在，且每个都带 provenance（缺了就是绕过防造假约束）
  2. provenance.kind 在允许的枚举内；displayable 与 kind 自洽
  3. data_quality 表行数与库内 COUNT(*) 一致（防拍脑袋）
  4. data_quality 矛盾检测抓到了「底表有数、视图全空」的 patent_cagr
  5. panel_depth 的 307 家 × 2 期与 v_model_dataset_asof_flat 的 614 行自洽
  6. feature_tree 的特征数与 v_model_train_ready 列一致，判别力特征为 5 个
  7. graph_structure 的节点/边数与 ads_graph_node/edge 严格相等（防合成图）
  8. graph_centrality 每个 top 节点的度数与边表现场 COUNT 一致
  9. roc_pr_ks 是 reconstructed 且 AUC/KS 双锚点残差 ≈ 0（不是随手画的曲线）
 10. train_curve 的 GAT 系列必须标 complete=false（5/41 点不能说成完整曲线）
 11. financing 必须 capability=descriptive_only 且 can_predict 全 false
 12. 全部接口经 api 函数调用能拿到 code=0（不再有 CONTRACT_PENDING）

用法：python tests/test_contracts.py
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
CDIR = os.path.join(ROOT, "outputs", "contracts")
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


def _c(name):
    p = os.path.join(CDIR, name)
    assert os.path.exists(p), "契约文件缺失：%s（先跑 build_contracts）" % name
    with open(p, encoding="utf-8") as f:
        return json.load(f)


CONTRACTS = [
    "data_quality.json", "panel_depth.json", "feature_tree.json",
    "graph_structure.json", "graph_slices.json", "graph_centrality.json",
    "stage_factor_weight.json", "survival.json", "financing.json",
    "train_curve.json", "roc_pr_ks.json", "enterprise_grade.json",
]
ALLOWED_KIND = {"measured", "derived", "partial", "reconstructed", "unavailable"}


# ---------------------------------------------------------------------------
def t_all_exist_with_provenance():
    for name in CONTRACTS:
        p = os.path.join(CDIR, name)
        assert os.path.exists(p), "缺失 %s" % name
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        pv = d.get("provenance")
        assert isinstance(pv, dict) and pv, "%s 没有 provenance —— 违反防造假硬约束" % name
        assert pv.get("kind"), "%s provenance.kind 为空" % name
        assert pv.get("source"), "%s provenance.source 为空" % name
        assert pv.get("generated_at"), "%s 缺 generated_at" % name


def t_kind_enum_and_displayable():
    for name in CONTRACTS:
        d = _c(name)
        kind = d["provenance"]["kind"]
        assert kind in ALLOWED_KIND, "%s kind=%s 不在枚举内" % (name, kind)
        want = kind != "unavailable"
        got = d["provenance"].get("displayable")
        assert got == want, "%s displayable=%s 与 kind=%s 不自洽" % (name, got, kind)
        # partial / reconstructed 必须交代缺什么，否则等于隐瞒
        if kind in ("partial", "reconstructed"):
            has_gap = bool(d.get("missing") or d.get("warnings")
                           or d["provenance"].get("missing_dimensions"))
            assert has_gap, "%s 标了 %s 却没说明缺什么" % (name, kind)


def t_quality_rows_match_db():
    d = _c("data_quality.json")
    con = _db()
    try:
        for t in d["tables"]:
            if not t.get("exists"):
                continue
            n = db_views.q1(con, 'SELECT COUNT(*) FROM "%s"' % t["table"])
            assert t["n_rows"] == n, "%s 行数 %s != 库内 %s" % (t["table"], t["n_rows"], n)
    finally:
        con.close()


def t_quality_catches_contradiction():
    d = _c("data_quality.json")
    cols = {c["column"]: c for c in d["contradictions"]}
    assert "patent_cagr" in cols, "未检出 patent_cagr 的底表/视图矛盾"
    c = cols["patent_cagr"]
    assert "dws_growth_feature" in c["issue"], "矛盾描述未指到底表"
    assert c.get("action"), "矛盾项缺修复建议"
    # 至少要有 5 条矛盾（含无底表列的那几个）
    assert len(d["contradictions"]) >= 5, "矛盾检测只报了 %d 条，疑似漏检" % len(d["contradictions"])
    reg = d["gap_registry"]["summary"]
    # 期望值取自单一事实源 configs/field_gap_registry.json，不写死数字
    # （v1.5 登记册 15→17：补 post_valuation / amount_cny，写死会误报）
    with open(os.path.join(ROOT, "configs", "field_gap_registry.json"),
              encoding="utf-8") as f:
        n_expect = len(json.load(f)["items"])
    assert reg["total"] == n_expect, (
        "登记册应有 %d 项，实得 %s" % (n_expect, reg["total"]))
    assert (reg["null_by_design"] + reg["source_missing"]
            + reg["partial_insufficient"]) == n_expect, \
        "三态之和应等于 %d" % n_expect


def t_join_key_health():
    """关联键空值必须单独可见 —— 766 件专利没匹配到企业，不能淹没在空值排行里。"""
    d = _c("data_quality.json")
    jk = {x["table"]: x for x in d.get("join_key_health") or []}
    assert jk, "缺 join_key_health 段"
    assert "fact_patent" in jk, "fact_patent 未纳入关联键检查"
    con = _db()
    try:
        real = db_views.q1(con, "SELECT COUNT(*) FROM fact_patent WHERE enterprise_id IS NULL")
        n_rows = db_views.q1(con, "SELECT COUNT(*) FROM fact_patent")
    finally:
        con.close()
    assert jk["fact_patent"]["n_null"] == real, \
        "fact_patent 关联键空值 %s != 库内 %s" % (jk["fact_patent"]["n_null"], real)
    assert jk["fact_patent"]["n_rows"] == n_rows
    if real:
        assert jk["fact_patent"]["severity"] == "high", "有未匹配行时严重度应为 high"
        assert jk["fact_patent"]["impact"], "缺影响说明"


def t_panel_depth_consistent():
    d = _c("panel_depth.json")
    ap = d["asof_panel"]
    con = _db()
    try:
        n_rows = db_views.q1(con, "SELECT COUNT(*) FROM v_model_dataset_asof_flat")
        n_ent = db_views.q1(con, "SELECT COUNT(DISTINCT enterprise_id) FROM v_model_dataset_asof_flat")
    finally:
        con.close()
    assert ap["n_rows"] == n_rows, "面板行数 %s != %s" % (ap["n_rows"], n_rows)
    assert ap["n_enterprises"] == n_ent, "企业家数 %s != %s" % (ap["n_enterprises"], n_ent)
    # 平衡面板：所有企业期数相同
    depths = {x["periods_per_enterprise"] for x in ap["depth_distribution"]}
    assert len(depths) == 1, "depth_distribution 出现多种取值 %s，balanced 标记会误导" % depths
    assert ap["balanced"] == (len(depths) == 1)
    assert d.get("missing"), "panel_depth 必须写明缺失（财务年度面板等）"


def t_feature_tree_matches_view():
    d = _c("feature_tree.json")
    con = _db()
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(v_model_train_ready)")]
    finally:
        con.close()
    # 标识/标签列不是特征，不该出现在特征树里
    META_COLS = {"enterprise_id", "name_display", "region_code", "cohort_year",
                 "split", "label"}
    feat_cols = [c for c in cols if c not in META_COLS]
    feats = {f["id"] for f in d["features"]}
    missing = [c for c in feat_cols if c not in feats]
    assert not missing, "v_model_train_ready 的特征列没进特征树：%s" % missing
    assert len(feat_cols) == 11, "训练视图应有 11 个特征列，实得 %d" % len(feat_cols)
    # 反向：特征树里声称 in_train_ready 的必须真的在视图里
    claimed = {f["id"] for f in d["features"] if f["in_train_ready"]}
    assert claimed == set(feat_cols), \
        "in_train_ready 标记与视图列不一致：多 %s / 少 %s" % (
            claimed - set(feat_cols), set(feat_cols) - claimed)
    s = d["summary"]
    assert s["n_in_train_ready"] == 11, "进训练的应为 11 个特征，实得 %s" % s["n_in_train_ready"]
    assert s["n_discriminative"] == 5, \
        "有判别力特征应为 5 个（已知 4 负 1 正），实得 %s" % s["n_discriminative"]
    assert s["reference"]["n_pos"] == 21, "参考集断档样本应为 21"


def t_graph_structure_exact():
    d = _c("graph_structure.json")
    con = _db()
    try:
        n_node = db_views.q1(con, "SELECT COUNT(*) FROM ads_graph_node")
        n_edge = db_views.q1(con, "SELECT COUNT(*) FROM ads_graph_edge")
        types = {r[0]: r[1] for r in con.execute(
            "SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY 1")}
    finally:
        con.close()
    assert d["summary"]["n_nodes"] == n_node, "节点数 %s != %s" % (d["summary"]["n_nodes"], n_node)
    assert d["summary"]["n_edges"] == n_edge, "边数 %s != %s" % (d["summary"]["n_edges"], n_edge)
    for nt in d["node_types"]:
        assert nt["n"] == types[nt["type"]], "%s 节点数不符" % nt["type"]
    # 真图：企业 500、专利 3 万级、IPC 1 万级 —— 数量级不对说明是合成图
    assert types.get("enterprise") == 500, "企业节点应为 500，实得 %s" % types.get("enterprise")
    assert (types.get("patent") or 0) > 30000, "专利节点应为 3 万级，实得 %s" % types.get("patent")


def t_centrality_matches_edges():
    d = _c("graph_centrality.json")
    con = _db()
    try:
        for item in d["top"][:10]:
            eid = item["enterprise_id"]
            real = db_views.q1(con, """
                SELECT COUNT(*) FROM ads_graph_edge
                WHERE edge_type='tech_similar' AND (src_id=? OR dst_id=?)""", (eid, eid))
            assert item["degree"] == real, \
                "%s 度数 %s != 边表 %s" % (eid, item["degree"], real)
    finally:
        con.close()
    assert d["top"], "top 为空"
    assert d["top"][0]["rank"] == 1
    # 排序必须按 weighted_degree 降序
    w = [x["weighted_degree"] for x in d["top"]]
    assert w == sorted(w, reverse=True), "top 未按加权度降序"


def t_roc_anchor_residual():
    d = _c("roc_pr_ks.json")
    assert d["provenance"]["kind"] == "reconstructed", "ROC 必须标 reconstructed"
    assert d["curves"], "无曲线"
    for c in d["curves"]:
        err = c.get("ks_error_vs_measured")
        assert err is not None, "%s 未给重建误差" % c["model"]
        assert err <= 0.005, "%s KS 残差 %.4f 过大，双锚点拟合失效" % (c["model"], err)
        imp = c["params"]["auc_implied"]
        anc = c["anchor"]["auc"]
        assert abs(imp - anc) < 0.01, "%s AUC 未被保住：%.4f vs %.4f" % (c["model"], imp, anc)
        assert c["roc"][0] == {"fpr": 0.0, "tpr": 0.0}, "ROC 起点不是 (0,0)"
        assert c["roc"][-1] == {"fpr": 1.0, "tpr": 1.0}, "ROC 终点不是 (1,1)"
        # tpr 单调不减
        tprs = [p["tpr"] for p in c["roc"]]
        assert tprs == sorted(tprs), "%s ROC 的 tpr 不单调" % c["model"]
    warn = " ".join(d["warnings"])
    assert "重建" in warn, "必须警告曲线是重建的"


def t_train_curve_honest():
    d = _c("train_curve.json")
    gat = [s for s in d["series"] if s["model"] == "HeteroGAT"]
    assert gat, "缺 GAT 系列"
    for s in gat:
        assert s["complete"] is False, "GAT 只有 5/41 点，complete 不能为 true"
        assert s["n_points"] < 10, "GAT 落盘点数异常"
    assert "41" in d.get("caveat", "") or "落盘" in d.get("caveat", ""), \
        "caveat 未说明 GAT 只落盘部分 epoch"
    assert d["provenance"]["kind"] == "partial"


def t_financing_no_prediction():
    d = _c("financing.json")
    assert d["capability"] == "descriptive_only", "融资契约不能声称可预测"
    cp = d["can_predict"]
    assert cp["next_round"] is False and cp["revenue_forecast"] is False, \
        "can_predict 必须全 false（数据不支持）"
    assert cp.get("reason"), "不做预测必须给理由"
    con = _db()
    try:
        n = db_views.q1(con, "SELECT COUNT(*) FROM fact_funding_event")
    finally:
        con.close()
    assert d["funding_activity"]["n_events"] == n, "融资事件数不符"
    cov = d["financial_coverage"]
    assert cov["latest_rev_wan"]["coverage"] < 0.1, \
        "latest_rev_wan 覆盖率应极低（<10%），若变高说明底层变了、结论要重算"


def t_survival_has_leakage_warning():
    d = _c("survival.json")
    assert d.get("leakage_diagnosis"), "survival 缺泄漏诊断，0.87 的高 AUC 会被误读"
    leak = " ".join(d["warnings"])
    assert "泄漏" in leak or "同源" in leak, "warnings 未提示高 AUC 的泄漏来源"
    assert d["summary"]["permutation_floor"], "缺置换地板，无法判断真实信号"


def t_stage_factor_descriptive():
    d = _c("stage_factor_weight.json")
    assert d["capability"] == "descriptive_only", "阶段因子不能声称是权重模型"
    stages = {s["period_t"]: s for s in d["stages"]}
    assert set(stages) == {1, 2, 3}, "期数应覆盖 1/2/3，实得 %s" % sorted(stages)
    # 已知：事件全落在第 3 期
    assert stages[1]["n_event"] == 0 and stages[2]["n_event"] == 0, \
        "期 1/2 的事件数应为 0（已知事实），若变了要重审"
    assert stages[3]["n_event"] == 21, "末期事件应为 21"


def t_endpoints_all_zero_code():
    """全部契约接口必须 code=0 —— 这是「补齐」的验收标准。"""
    calls = [
        ("data/quality", api.data_quality),
        ("data/panel-depth", api.data_panel_depth),
        ("features/tree", api.features_tree),
        ("graph/structure", api.graph_structure),
        ("graph/slices", api.graph_slices),
        ("graph/centrality", api.graph_centrality),
        ("model/train-curve", api.model_train_curve),
        ("model/roc-pr-ks", api.model_roc_pr_ks),
        ("explain/stage-factor", api.explain_stage_factor),
        ("forecast/survival", api.forecast_survival),
        ("forecast/financing", api.forecast_financing),
        ("enterprise/grade", api.enterprise_grade),
    ]
    bad = []
    for name, fn in calls:
        r = fn()
        if r.get("code") != 0:
            bad.append("%s -> %s (%s)" % (name, r.get("code"), r.get("message")))
        elif not r.get("data"):
            bad.append("%s -> data 为空" % name)
        else:
            pv = (r.get("meta") or {}).get("provenance") or {}
            if not pv:
                bad.append("%s -> meta 缺 provenance" % name)
    assert not bad, "未就绪接口：%s" % "; ".join(bad)


def t_explain_local_dynamic():
    """explain/local 必须按企业动态生成，且不同企业结果不同。"""
    con = _db()
    try:
        ids = [r[0] for r in con.execute(
            "SELECT enterprise_id FROM v_model_train_ready LIMIT 2")]
    finally:
        con.close()
    assert len(ids) == 2, "训练视图企业不足"
    a = api.explain_local(ids[0])
    b = api.explain_local(ids[1])
    assert a["code"] == 0 and b["code"] == 0, "explain/local 返回 %s" % a["code"]
    assert a["data"]["features"], "无逐特征贡献"
    assert a["data"]["model_based"] is False and a["data"]["is_shap"] is False, \
        "必须标明不是模型归因 / 不是 SHAP"
    assert a["data"]["caveat"], "缺口径说明"
    va = [f["value"] for f in a["data"]["features"]]
    vb = [f["value"] for f in b["data"]["features"]]
    assert va != vb, "两家企业返回同一份解释，说明没按企业取数"
    # 未知企业 -> NOT_FOUND 业务码，不抛异常
    r = api.explain_local("ENT000000000000")
    assert r["code"] == "NOT_FOUND", "未知企业应返回 NOT_FOUND，实得 %s" % r["code"]


def t_import_validation_works():
    """导入接口真的做字段级校验：坏数据必须被拦住。"""
    from src.app.backend import import_validate as iv

    # 1) 识别目标表：用 fact_patent 的真实列名
    con = _db()
    try:
        tbl = iv.detect_target_table(con, ["enterprise_id", "app_no", "app_date",
                                           "patent_type", "ipc_main"])
        assert tbl["table"] == "fact_patent", "目标表识别错：%s" % tbl["table"]
        # 2) 坏数据必须报错
        rep = iv.validate_rows(con, "fact_patent",
                               ["enterprise_id", "app_no", "app_date"],
                               [["NOT-AN-ID", "CN1", "not-a-date"],
                                ["ENT2803CAE1D888", "CN2", "2023-01-01"]])
        codes = {e["code"] for e in rep["errors"]}
        assert "BAD_ENTERPRISE_ID" in codes, "未拦住非法企业 ID：%s" % codes
        assert rep["summary"]["acceptable"] is False, "坏数据竟被判为可接受"
        assert rep["summary"]["n_errors"] >= 1
        # 3) 好数据应通过
        rep2 = iv.validate_rows(con, "fact_patent",
                                ["enterprise_id", "app_no"],
                                [["ENT2803CAE1D888", "CN999999"]])
        assert rep2["summary"]["acceptable"] is True, \
            "干净数据被误判：%s" % rep2["errors"][:3]
        # 4) 无法识别的表头要拒绝，不硬猜
        bad = iv.detect_target_table(con, ["完全无关的列A", "另一个怪列B"])
        assert bad["table"] is None, "乱表头竟识别出了表 %s" % bad["table"]
    finally:
        con.close()


def t_staging_does_not_touch_core_db():
    """导入落库必须写独立 staging 库 —— 主库结构口径（33 表 / 698 列）不能漂移。

    没这条守卫时踩过一次：stage_rows 在主库 CREATE TABLE stg_import，
    表数 33→34、列数 698→703，数据字典/说明书/PPT 的数字全部对不上。
    """
    from src.app.backend import import_validate as iv

    def shape():
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")]
            views = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='view'")]
            ncol = 0
            for t in tabs + views:
                ncol += len(con.execute(f'PRAGMA table_info("{t}")').fetchall())
            return len(tabs), len(views), ncol
        finally:
            con.close()

    before = shape()
    iv.stage_rows(DB, "fact_patent", ["enterprise_id", "app_no"],
                  [["ENT2803CAE1D888", "CN-GUARD-1"]])
    after = shape()
    assert before == after, \
        "导入落库改变了主库结构：%s → %s（必须写独立 staging 库）" % (before, after)
    # staging 库要真的收到数据
    sp = os.path.join(os.path.dirname(DB), "import_staging.db")
    assert os.path.exists(sp), "staging 库未创建：%s" % sp
    con = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM stg_import").fetchone()[0]
    finally:
        con.close()
    assert n >= 1, "staging 库没有数据"


def t_enterprise_grade_matches_db():
    """v1.8 新增契约：五档家数必须能从 v_enterprise_grade_l0 现场复算。"""
    d = _c("enterprise_grade.json")
    con = _db()
    try:
        want = dict(con.execute(
            "SELECT grade, COUNT(*) FROM v_enterprise_grade_l0 GROUP BY grade").fetchall())
        total = con.execute("SELECT COUNT(*) FROM v_enterprise_grade_l0").fetchone()[0]
    finally:
        con.close()
    got = {x["grade"]: x["n"] for x in d["grade_dist"]}
    assert got == want, "五档家数与库内不符：%r vs %r" % (got, want)
    assert d["n_enterprise"] == total == 500
    assert d["provenance"]["kind"] == "measured"
    assert sum(x["n"] for x in d["hit_rule_dist"]) == total, "命中规则分布未闭合"


def main():
    print("=" * 68)
    print("  契约文件守卫测试（反向比对：契约里的数 vs 库内现场算）")
    print("=" * 68)
    check("1. 12 个契约齐全且带 provenance", t_all_exist_with_provenance)
    check("2. kind 枚举合法且 displayable 自洽", t_kind_enum_and_displayable)
    check("3. data_quality 行数与库一致", t_quality_rows_match_db)
    check("4. 矛盾检测抓到 patent_cagr 且登记册项数对账", t_quality_catches_contradiction)
    check("4b. 关联键空值单独可见（766 件专利未匹配）", t_join_key_health)
    check("5. panel_depth 与 614 行面板自洽", t_panel_depth_consistent)
    check("6. feature_tree 与训练视图列一致", t_feature_tree_matches_view)
    check("7. graph_structure 与节点/边表严格相等", t_graph_structure_exact)
    check("8. graph_centrality 度数与边表现场一致", t_centrality_matches_edges)
    check("9. roc_pr_ks 双锚点残差 ≈ 0", t_roc_anchor_residual)
    check("10. train_curve 如实标注不完整", t_train_curve_honest)
    check("11. financing 不产出预测值", t_financing_no_prediction)
    check("12. survival 带泄漏诊断", t_survival_has_leakage_warning)
    check("13. stage_factor 是描述性且期数正确", t_stage_factor_descriptive)
    check("14. 12 个契约接口全部 code=0", t_endpoints_all_zero_code)
    check("15. explain/local 按企业动态生成", t_explain_local_dynamic)
    check("16. 导入接口字段级校验生效", t_import_validation_works)
    check("17. 导入落库不污染主库结构", t_staging_does_not_touch_core_db)
    check("18. 企业分档契约与库内现场对账", t_enterprise_grade_matches_db)

    print("-" * 68)
    if FAIL:
        print("❌ CONTRACT TESTS FAILED（%d 过 / %d 败）" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  · %s: %s" % (n, e))
        sys.exit(1)
    print("✅ CONTRACT TESTS PASSED（%d 项）" % len(PASS))


if __name__ == "__main__":
    main()
