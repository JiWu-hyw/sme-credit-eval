# -*- coding: utf-8 -*-
"""图节点 ID 口径 + 分队列诊断视图 + 字典说明一致性守卫。

防的是三类事故（都真实发生过或差一点发生）：

  1. **IPC 节点 ID 分裂**：源表 bridge_patent_ipc.ipc_code 里混入 20 个带
     WIPO 版本号后缀的串（'A61K31/426(2006.01)'），而 dim_ipc 的其余 11,588 个
     都不带。未归一前同一分类在图里是两个节点、belongs_ipc 边被拆成两处，
     GAT 聚合时把一个分类算了两遍。v1.5 已归一（31 条边并入）。

  2. **数据字典手写说明与库内枚举脱节**：column_notes 里 ads_graph_node.node_type
     曾写作 'enterprise / inventor / ipc / qual_type'（既漏 patent、又多了已废弃的
     qual_type），ads_graph_edge.edge_type 曾写作 v1.2 的四个旧名字
     'patent_cooccur / inventor_serve / qual_hierarchy / ipc_similar'。
     这两种错误在 md 里看起来最像"权威口径"，算法端照着建图就会错。
     枚举列是 schema.sql CHECK 自动解析的，说明列是手写的——只有测试能钉住后者。

  3. **分队列效应回退**：评审报告 §3.3/§4.2 要求的 v_feature_discrimination /
     v_feature_cohort_gap 若被误删或口径漂移，全样本 AUC 又会被当成单一证据引用。

用法：python tests/test_graph_id_docs.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

# 合法 IPC 形态：部(1)+大类(2)+小类(1)+大组/小组，或仅小类（源串截断，保留原样）
IPC_FULL = re.compile(r"^[A-H]\d{2}[A-Z]\d+/\d+$")
IPC_SUBCLASS_ONLY = re.compile(r"^[A-H]\d{2}[A-Z]$")
IPC_VERSION_SUFFIX = re.compile(r"\(\s*\d{4}\.\d{2}\s*\)$")

NODE_ID_PATTERNS = {
    "enterprise": re.compile(r"^ENT[0-9A-F]{12}$"),
    "inventor": re.compile(r"^INV[0-9A-F]{10}$"),
    "investor": re.compile(r"^IVS[0-9A-F]{10}$"),
}

# 已废弃、绝不该再出现在说明里的旧枚举名
OBSOLETE_NODE_TYPES = ("qual_type",)
OBSOLETE_EDGE_TYPES = ("patent_cooccur", "inventor_serve", "qual_hierarchy", "ipc_similar")


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  [OK] %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  [FAIL] %s -> %s" % (name, e))


def main() -> int:
    if not os.path.exists(DB):
        print("找不到数据库：%s" % DB)
        return 2
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    cur = con.cursor()

    def one(sql, args=()):
        return cur.execute(sql, args).fetchone()[0]

    def rows(sql, args=()):
        return cur.execute(sql, args).fetchall()

    node_types = [r[0] for r in rows(
        "SELECT DISTINCT node_type FROM ads_graph_node")]
    edge_types = [r[0] for r in rows(
        "SELECT DISTINCT edge_type FROM ads_graph_edge")]

    # ---------------- 1. IPC 节点 ID 口径 ----------------
    def t_ipc_no_version_suffix():
        bad = one("SELECT COUNT(*) FROM ads_graph_node WHERE node_type='ipc' "
                  "AND (node_id LIKE '%(%' OR node_id LIKE '%)')")
        assert bad == 0, (
            "IPC 节点仍带版本号后缀 %d 个（如 A61K31/426(2006.01)）："
            "build_graph_v2._norm_ipc 没生效或未重建图" % bad)

    def t_ipc_format_valid():
        import re as _re
        ids = [r[0] for r in rows(
            "SELECT node_id FROM ads_graph_node WHERE node_type='ipc'")]
        bad = [x for x in ids
               if not (IPC_FULL.match(x) or IPC_SUBCLASS_ONLY.match(x))]
        assert not bad, "IPC 节点 ID 形态非法 %d 个：%s" % (len(bad), bad[:5])
        only_sub = [x for x in ids if IPC_SUBCLASS_ONLY.match(x)]
        assert len(only_sub) <= 10, (
            "仅到小类粒度的 IPC 节点异常增多（%d 个）：源数据清洗可能回退"
            % len(only_sub))

    def t_ipc_merge_effective():
        """归一后同一小类下不应出现「大组号 + 小类号」并存的重复计价。"""
        n_ipc = one("SELECT COUNT(*) FROM ads_graph_node WHERE node_type='ipc'")
        assert 11_000 < n_ipc < 12_500, (
            "IPC 节点数 %d 超出合理区间（11.5k 左右）；明显少了说明归并过度、"
            "多了说明版本号后缀又回来了" % n_ipc)

    # ---------------- 2. 其余节点 ID 格式 ----------------
    def t_node_id_formats():
        problems = []
        for nt, pat in NODE_ID_PATTERNS.items():
            ids = [r[0] for r in rows(
                "SELECT node_id FROM ads_graph_node WHERE node_type=?", (nt,))]
            bad = [x for x in ids if not pat.match(x)]
            if bad:
                problems.append("%s: %d 个不符（样例 %s）" % (nt, len(bad), bad[:3]))
        assert not problems, "；".join(problems)

    def t_patent_id_resolves():
        orphan = one(
            "SELECT COUNT(*) FROM ads_graph_node n WHERE n.node_type='patent' "
            "AND NOT EXISTS (SELECT 1 FROM fact_patent p WHERE p.patent_id=n.node_id)")
        assert orphan == 0, (
            "%d 个专利节点 ID 在 fact_patent.patent_id 里找不到；"
            "注意专利节点用 patent_id（CN 公开号）而非 app_no" % orphan)

    # ---------------- 3. 数据字典说明与库内枚举一致 ----------------
    def t_node_type_doc_matches():
        from src.data.column_notes import TABLE_NOTES
        note = TABLE_NOTES["ads_graph_node"]["node_type"]
        missing = [t for t in node_types if t not in note]
        assert not missing, (
            "数据字典 node_type 说明漏了实际存在的类型 %s；"
            "实际枚举：%s" % (missing, sorted(node_types)))
        stale = [t for t in OBSOLETE_NODE_TYPES if t in note]
        assert not stale, "数据字典 node_type 说明仍含已废弃类型 %s" % stale

    def t_edge_type_doc_matches():
        from src.data.column_notes import TABLE_NOTES
        note = TABLE_NOTES["ads_graph_edge"]["edge_type"]
        missing = [t for t in edge_types if t not in note]
        assert not missing, (
            "数据字典 edge_type 说明漏了实际产出的边 %s；实际枚举：%s"
            % (missing, sorted(edge_types)))
        stale = [t for t in OBSOLETE_EDGE_TYPES if t in note]
        assert not stale, (
            "数据字典 edge_type 说明仍含 v1.2 的旧边名 %s —— 这是最容易被当成"
            "权威口径照抄的错误" % stale)

    def t_edge_enum_vs_actual_declared():
        """CHECK 枚举位与实际产出必须能区分开，文档里不能说「扩到 8 类」就算完。"""
        from src.data.column_notes import TABLE_NOTES
        note = TABLE_NOTES["ads_graph_edge"]["edge_type"]
        assert "保留" in note or "实际产出" in note, (
            "edge_type 说明没有区分「CHECK 枚举位」与「实际产出的边」，"
            "读者会以为 8 个枚举位都有数据")

    # ---------------- 4. 分队列诊断视图 ----------------
    for v in ("v_feature_discrimination", "v_feature_cohort_gap"):
        def _mk(view=v):
            def t():
                got = one("SELECT COUNT(*) FROM sqlite_master "
                          "WHERE type='view' AND name=?", (view,))
                assert got == 1, "视图 %s 不存在（评审报告 §3.3/§4.2 要求）" % view
            return t
        check("%s 存在" % v, _mk())

    def t_auc_matches_report():
        """数值必须与评审报告表 4-1 / 4-2 一致，否则口径已漂移。"""
        expect = {"all": 0.619, "2021": 0.784, "2022": 0.503}
        got = {r[0]: r[1] for r in rows(
            "SELECT cohort_scope, auc FROM v_feature_discrimination "
            "WHERE feature_name='inv_patent_ratio'")}
        for scope, exp in expect.items():
            assert scope in got, "v_feature_discrimination 缺 scope=%s" % scope
            assert abs(got[scope] - exp) <= 0.002, (
                "inv_patent_ratio 在 %s 的 AUC=%.4f，与报告的 %.3f 不符"
                % (scope, got[scope], exp))

    def t_cohort_gap_flags_main_feature():
        v = one("SELECT verdict FROM v_feature_cohort_gap "
                "WHERE feature_name='inv_patent_ratio'")
        assert v and "不可单独引用" in v, (
            "inv_patent_ratio 队列差距 0.281 应判为「全样本 AUC 不可单独引用」，"
            "实际 verdict=%r" % (v,))

    def t_all_scopes_present():
        scopes = {r[0] for r in rows(
            "SELECT DISTINCT cohort_scope FROM v_feature_discrimination")}
        assert scopes == {"all", "2021", "2022"}, (
            "cohort_scope 应为 all/2021/2022，实际 %s" % sorted(scopes))

    # ---------------- 5. 缺口登记册覆盖融资估值 ----------------
    def t_registry_covers_valuation():
        import json
        reg = json.load(open(os.path.join(ROOT, "configs",
                                          "field_gap_registry.json"),
                             encoding="utf-8"))
        keys = {(i["table"], i["field"]) for i in reg["items"]}
        need = [("fact_funding_event", "post_valuation"),
                ("fact_funding_event", "amount_cny")]
        miss = [k for k in need if k not in keys]
        assert not miss, (
            "缺口登记册缺融资字段 %s（评审报告 §2.3(3)：投后估值 0 条）" % miss)

    check("IPC 节点无版本号后缀", t_ipc_no_version_suffix)
    check("IPC 节点 ID 形态合法", t_ipc_format_valid)
    check("IPC 节点数在合理区间", t_ipc_merge_effective)
    check("企业/发明人/机构节点 ID 格式", t_node_id_formats)
    check("专利节点 ID 可在 fact_patent 解析", t_patent_id_resolves)
    check("字典 node_type 说明与库一致", t_node_type_doc_matches)
    check("字典 edge_type 说明与库一致", t_edge_type_doc_matches)
    check("字典区分枚举位与实际产出", t_edge_enum_vs_actual_declared)
    check("分队列 AUC 与报告一致", t_auc_matches_report)
    check("队列反差已标记主力特征", t_cohort_gap_flags_main_feature)
    check("分队列范围齐全", t_all_scopes_present)
    check("登记册覆盖融资估值", t_registry_covers_valuation)

    con.close()
    print("\n图 ID 与口径文档守卫：%d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
    # 融资特征补算守卫一并跑（v1.6）
    rc_f = funding_guard()
    if FAIL:
        for n, e in FAIL:
            print("  ✗ %s: %s" % (n, e))
        return 1
    return rc_f


def funding_guard() -> int:
    """v1.6 融资特征补算守卫（独立入口，不与上面的 main 混跑）。"""
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    cur = con.cursor()
    P, F = [], []

    def chk(name, fn):
        try:
            fn(); P.append(name); print("  [OK] %s" % name)
        except AssertionError as e:
            F.append((name, str(e))); print("  [FAIL] %s -> %s" % (name, e))

    def t_table_exists():
        got = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                          "AND name='ads_feature_asof_funding'").fetchone()[0]
        assert got == 1, "表不存在，需跑 python -m src.data.build_asof_funding"

    def t_rows_complete():
        n = cur.execute("SELECT COUNT(*) FROM ads_feature_asof_funding").fetchone()[0]
        n_anchor = cur.execute("SELECT COUNT(DISTINCT enterprise_id||as_of_policy) "
                               "FROM ads_feature_asof").fetchone()[0]
        assert n == n_anchor == 614, "应覆盖 307 家 × 2 口径 = 614 行，实得 %d（锚点 %d）" % (n, n_anchor)

    def t_cohort_matches_manual_sql():
        """与独立 SQL 交叉对账：窗口内 m_funding=1 的家数必须与手工算一致。"""
        n_sql = cur.execute("""
            SELECT COUNT(*) FROM (
              SELECT DISTINCT f.enterprise_id FROM fact_funding_event f
              JOIN ads_model_dataset t ON t.enterprise_id=f.enterprise_id
                AND t.label_task='qual_survival' AND t.split<>'excluded'
              WHERE f.event_year <= t.cohort_year)""").fetchone()[0]
        n_tbl = cur.execute("SELECT SUM(m_funding) FROM ads_feature_asof_funding "
                            "WHERE as_of_policy='cohort'").fetchone()[0] or 0
        assert n_sql == n_tbl == 60, "手工 SQL %d vs 表内 %d（应为 60）" % (n_sql, n_tbl)

    def t_no_future_leakage():
        """最硬的一条：任何一行的窗口内事件都必须 event_year <= as_of_year。"""
        bad = cur.execute("""
            SELECT COUNT(*) FROM ads_feature_asof_funding a
            WHERE EXISTS (SELECT 1 FROM fact_funding_event f
                          WHERE f.enterprise_id=a.enterprise_id
                            AND f.event_year <= a.as_of_year
                            AND a.m_funding = 0)""").fetchone()[0]
        assert bad == 0, "%d 家窗口内有事件但 m_funding=0 —— as-of 边界算错" % bad
        bad2 = cur.execute("""
            SELECT COUNT(*) FROM ads_feature_asof_funding a
            WHERE a.n_funding_events <
                  (SELECT COUNT(*) FROM fact_funding_event f
                    WHERE f.enterprise_id=a.enterprise_id
                      AND f.event_year <= a.as_of_year)""").fetchone()[0]
        assert bad2 == 0, "%d 行事件计数偏小" % bad2

    def t_zero_semantics():
        """0 = 有观测无事件（合法），不允许 NULL 出现在 m_funding/n_events 上。"""
        bad = cur.execute("SELECT COUNT(*) FROM ads_feature_asof_funding "
                          "WHERE m_funding IS NULL OR n_funding_events IS NULL").fetchone()[0]
        assert bad == 0, "%d 行 m_funding/n_events 为 NULL——0 才是「无事件」" % bad

    def t_prestige_range():
        bad = cur.execute("SELECT COUNT(*) FROM ads_feature_asof_funding "
                          "WHERE investor_prestige_best IS NOT NULL "
                          "AND investor_prestige_best NOT IN (1,2,3,4)").fetchone()[0]
        assert bad == 0, "机构等级越界 %d 行（应为 1-4）" % bad

    def t_missing_reason_reclassified():
        rows = {r[0]: r[1] for r in cur.execute(
            "SELECT feature_name, missing_reason FROM v_feature_missing_reason "
            "WHERE feature_name IN ('patent_cagr','m_funding','investor_prestige')")}
        assert rows.get("patent_cagr") == "definition_mismatch", \
            "patent_cagr 应归「口径不可比」，实际 %r" % rows.get("patent_cagr")
        assert rows.get("m_funding") == "computable_not_computed_v1.6", \
            "m_funding 应标注已补算，实际 %r" % rows.get("m_funding")

    def t_registry_substitutes():
        import json
        reg = json.load(open(os.path.join(ROOT, "configs", "field_gap_registry.json"),
                             encoding="utf-8"))
        fields = {s.get("field", "") for s in reg.get("substitutes", [])}
        assert any("m_funding" in f for f in fields), "登记册替身未记录 m_funding 补算"

    chk("融资特征表存在", t_table_exists)
    chk("614 行覆盖完整", t_rows_complete)
    chk("cohort 家数与手工 SQL 对账(60)", t_cohort_matches_manual_sql)
    chk("无未来事件泄漏", t_no_future_leakage)
    chk("0/NULL 语义正确", t_zero_semantics)
    chk("机构等级 1-4", t_prestige_range)
    chk("缺失原因已重分类", t_missing_reason_reclassified)
    chk("登记册替身已记录", t_registry_substitutes)

    con.close()
    print("融资特征补算守卫：%d 项通过，%d 项失败" % (len(P), len(F)))
    return 1 if F else 0


if __name__ == "__main__":
    import os as _os
    if _os.environ.get("FUNDING_GUARD"):
        raise SystemExit(funding_guard())
    raise SystemExit(main())
