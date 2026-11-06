# -*- coding: utf-8 -*-
"""异质图 v2 守卫测试（对照评审报告 §5.1 / §5.2）。

防的是四类事故：
  1. 图只有 3 类节点 —— 人员 / 投资机构始终没入图，STGNN 拿不到「人才流动」信号；
  2. 图边陈旧 —— rematch_unmatched_patents 补匹配后没重建图，
     实测 files_patent 只有 29,751 条而实际匹配 32,203 件，差 2,452 条；
  3. 相似边权重挤在 0.10~0.25，当注意力权重时图消息传递退化成平均池化；
  4. 边指向不存在的节点（悬空边），转 PyG HeteroData 时直接崩。

断言：
  1. 节点类型 = 5 类（enterprise / patent / ipc / inventor / investor）
  2. files_patent 边数 == fact_patent 中已匹配企业数（0 缺口）
  3. invented_by 边数 == bridge_patent_inventor 行数
  4. funded_by 边数 > 0，且机构节点数 >= 300
  5. tech_similar 权重 p50 >= 0.20、p90 >= 0.35、>=0.3 的边 >= 2000
  6. tech_similar 覆盖企业 >= 480，单企业度数 <= SIM_TOPK
  7. 无悬空边（每条边的两端都在节点表里）
  8. 孤儿专利节点 == 未匹配专利数（允许存在，但必须与 NULL 行严格对账）

用法：python tests/test_graph_v2.py
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")

PASS, FAIL = [], []

NODE_TYPES = {"enterprise", "patent", "ipc", "inventor", "investor"}
SIM_TOPK = 20


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

    def t_node_types():
        got = {r[0] for r in cur.execute(
            "SELECT DISTINCT node_type FROM ads_graph_node")}
        assert got == NODE_TYPES, "节点类型 %s != 期望 %s" % (sorted(got), sorted(NODE_TYPES))

    def t_files_patent_fresh():
        matched = one("SELECT COUNT(*) FROM fact_patent WHERE enterprise_id IS NOT NULL "
                      "AND enterprise_id <> ''")
        edges = one("SELECT COUNT(*) FROM ads_graph_edge WHERE edge_type='files_patent'")
        assert edges == matched, "files_patent 边 %d vs 已匹配专利 %d（图陈旧，需重跑 build_graph_v2）" % (
            edges, matched)

    def t_invented_by():
        bridge = one("SELECT COUNT(*) FROM bridge_patent_inventor")
        edges = one("SELECT COUNT(*) FROM ads_graph_edge WHERE edge_type='invented_by'")
        assert edges == bridge, "invented_by 边 %d vs 专利-发明人关系 %d" % (edges, bridge)

    def t_funded_by():
        edges = one("SELECT COUNT(*) FROM ads_graph_edge WHERE edge_type='funded_by'")
        n_inv = one("SELECT COUNT(*) FROM ads_graph_node WHERE node_type='investor'")
        assert edges > 0, "funded_by 边为 0 —— 投资机构没入图"
        assert n_inv >= 300, "投资机构节点只有 %d 个（源数据 349+ 家）" % n_inv

    def t_sim_weight_spread():
        w = [r[0] for r in cur.execute(
            "SELECT weight FROM ads_graph_edge WHERE edge_type='tech_similar' ORDER BY weight")]
        assert len(w) >= 3000, "tech_similar 边只有 %d 条" % len(w)
        p50 = w[len(w) // 2]
        p90 = w[int(len(w) * 0.9)]
        n_hi = sum(1 for x in w if x >= 0.3)
        assert p50 >= 0.20, "权重中位 %.3f < 0.20，区分度不足（旧口径 0.138）" % p50
        assert p90 >= 0.35, "权重 p90 %.3f < 0.35" % p90
        assert n_hi >= 2000, ">=0.3 的相似边只有 %d 条（旧口径 663）" % n_hi

    def t_sim_coverage():
        n_ent = one("SELECT COUNT(DISTINCT src_id) FROM ads_graph_edge "
                    "WHERE edge_type='tech_similar'")
        assert n_ent >= 480, "有技术相似边的企业只有 %d 家（旧口径 294）" % n_ent
        deg = one("SELECT MAX(d) FROM (SELECT src_id, COUNT(*) d FROM ads_graph_edge "
                  "WHERE edge_type='tech_similar' GROUP BY 1)")
        assert deg <= SIM_TOPK, "单企业相似度 %d > top-K %d，top-K 截断失效" % (deg, SIM_TOPK)

    def t_no_dangling_edge():
        src_bad = one("""
            SELECT COUNT(*) FROM ads_graph_edge e
            WHERE NOT EXISTS (SELECT 1 FROM ads_graph_node n
                              WHERE n.node_type = e.src_type AND n.node_id = e.src_id)""")
        dst_bad = one("""
            SELECT COUNT(*) FROM ads_graph_edge e
            WHERE NOT EXISTS (SELECT 1 FROM ads_graph_node n
                              WHERE n.node_type = e.dst_type AND n.node_id = e.dst_id)""")
        assert src_bad == 0 and dst_bad == 0, "悬空边：src %d / dst %d" % (src_bad, dst_bad)

    def t_orphan_patent():
        unmatched = one("SELECT COUNT(*) FROM fact_patent WHERE enterprise_id IS NULL "
                        "OR enterprise_id = ''")
        orphan = one("""
            SELECT COUNT(*) FROM ads_graph_node n
            WHERE n.node_type='patent'
              AND NOT EXISTS (SELECT 1 FROM ads_graph_edge e
                              WHERE e.edge_type='files_patent' AND e.dst_id = n.node_id)""")
        assert orphan == unmatched, "孤儿专利节点 %d vs 未匹配专利 %d" % (orphan, unmatched)

    print("== 异质图 v2 守卫 ==")
    check("节点类型 5 类", t_node_types)
    check("files_patent 与匹配专利对账（0 缺口）", t_files_patent_fresh)
    check("invented_by 边与关系表对账", t_invented_by)
    check("funded_by 与投资机构节点", t_funded_by)
    check("tech_similar 权重区分度", t_sim_weight_spread)
    check("tech_similar 覆盖与 top-K 截断", t_sim_coverage)
    check("无悬空边", t_no_dangling_edge)
    check("孤儿专利 == 未匹配专利", t_orphan_patent)

    print("\n通过 %d / 共 %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
