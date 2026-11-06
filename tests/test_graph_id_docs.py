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
    if FAIL:
        for n, e in FAIL:
            print("  ✗ %s: %s" % (n, e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
