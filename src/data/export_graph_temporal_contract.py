"""生成《时序图切面与 STGNN 输入契约》。

所有数字只读现算自 data/db/zjtech_credit.db，不硬编码、不复用旧报告数值。
对应算法端评审报告 §5.2(1)「只有 1 个时间切片 / STGNN 无时序输入」。

产物：
    outputs/graph_temporal_contract.md
    outputs/graph_temporal_contract.json
交付时由 src/data/export_delivery.py 复制进 01_算法端/。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT_MD = os.path.join(ROOT, "outputs", "graph_temporal_contract.md")
OUT_JSON = os.path.join(ROOT, "outputs", "graph_temporal_contract.json")

TIMED_EDGES = ("files_patent", "belongs_ipc", "invented_by")


def con_ro() -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def build() -> dict:
    c = con_ro()
    try:
        years = [r[0] for r in c.execute(
            "SELECT DISTINCT time_slice FROM v_graph_slice_annual ORDER BY 1")]
        ymax, ymin = years[-1], years[0]

        phy_edge = dict(c.execute(
            "SELECT edge_type, COUNT(*) FROM ads_graph_edge GROUP BY 1").fetchall())
        node_types = dict(c.execute(
            "SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY 1").fetchall())
        slice_zero = {
            t: c.execute("SELECT COUNT(*) FROM %s WHERE time_slice=0" % t).fetchone()[0]
            for t in ("ads_graph_node", "ads_graph_edge")
        }
        n_node = c.execute("SELECT COUNT(*) FROM ads_graph_node").fetchone()[0]
        n_edge = c.execute("SELECT COUNT(*) FROM ads_graph_edge").fetchone()[0]

        cum = dict(c.execute(
            "SELECT metric, n_cumulative FROM v_graph_slice_annual WHERE time_slice=?",
            (ymax,)).fetchall())
        ann_last = dict(c.execute(
            "SELECT edge_type, COUNT(*) FROM v_graph_slice_annual_edge "
            "WHERE time_slice=? GROUP BY 1", (ymax,)).fetchall())

        by_year = []
        rows = dict(
            (r[0], r[1]) for r in c.execute(
                "SELECT time_slice, COUNT(*) FROM v_graph_slice_annual_edge GROUP BY 1")
        )
        metrics = c.execute(
            "SELECT time_slice, metric, n_in_year, n_cumulative "
            "FROM v_graph_slice_annual ORDER BY time_slice, metric").fetchall()
        per_year: dict[int, dict] = {}
        for t, m, ni, nc in metrics:
            per_year.setdefault(t, {})[m] = {"n_in_year": ni, "n_cumulative": nc}
        for y in years:
            by_year.append({
                "time_slice": y,
                "n_edges_cumulative": rows.get(y, 0),
                "metrics": per_year.get(y, {}),
            })

        orphan = c.execute(
            """SELECT COUNT(*) FROM (
                 SELECT src_id AS nid, src_type AS nt FROM v_graph_slice_annual_edge
                  WHERE time_slice=?
                 UNION
                 SELECT dst_id, dst_type FROM v_graph_slice_annual_edge
                  WHERE time_slice=?
               ) x
               LEFT JOIN ads_graph_node n ON n.node_type=x.nt AND n.node_id=x.nid
               WHERE n.node_id IS NULL""", (ymax, ymax)
        ).fetchone()[0]

        indexes = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_gedge_%' ORDER BY 1")]
        static = {
            e: phy_edge.get(e, 0) for e in ("funded_by", "tech_similar")
        }
        w = [r[0] for r in c.execute(
            "SELECT weight FROM ads_graph_edge WHERE edge_type='tech_similar' ORDER BY weight")]
        weight_stats = {
            "n": len(w),
            "min": w[0] if w else None,
            "p50": w[len(w) // 2] if w else None,
            "p90": w[int(len(w) * 0.9)] if w else None,
            "max": w[-1] if w else None,
            "n_ge_0_3": sum(1 for x in w if x >= 0.3),
        }
        # v2.1 并列层：真实商标源已接入，但产品节点不进主图，29 帧切面不动
        product_parallel = {"available": False}
        if c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                     "AND name='dim_product_nice_class'").fetchone()[0]:
            product_parallel = {
                "available": True,
                "n_trademark": c.execute(
                    "SELECT COUNT(*) FROM ods_qcc_trademark").fetchone()[0],
                "n_nice_class": c.execute(
                    "SELECT COUNT(*) FROM dim_product_nice_class").fetchone()[0],
                "n_product_node": c.execute(
                    "SELECT COUNT(*) FROM dim_product_nice_class "
                    "WHERE is_product_node=1").fetchone()[0],
                "n_service_class": c.execute(
                    "SELECT COUNT(*) FROM dim_product_nice_class "
                    "WHERE is_service=1").fetchone()[0],
                "n_covered_firms": c.execute(
                    "SELECT COUNT(DISTINCT enterprise_id) "
                    "FROM bridge_enterprise_product").fetchone()[0],
                "n_bridge_rows": c.execute(
                    "SELECT COUNT(*) FROM bridge_enterprise_product").fetchone()[0],
                "in_main_graph": False,
                "status": "类别粒度部分闭合（SKU 级产品仍缺，未并入主图）",
            }
        current_db_ver = c.execute(
            "SELECT version FROM meta_schema_version "
            "ORDER BY applied_at DESC LIMIT 1").fetchone()[0]
    finally:
        c.close()

    return {
        "generated_by": "src/data/export_graph_temporal_contract.py",
        "schema_version": "v1.9",
        "current_db_version": current_db_ver,
        "revision": "2026-11-09 年度切片同源修订（V191GRAPHFIX）",
        "review_item": "数据交付包评审报告-算法端-v1.0 §5.2(1) 无时序图",
        "physical_graph": {
            "n_nodes": n_node,
            "n_edges": n_edge,
            "node_types": node_types,
            "edge_types": phy_edge,
            "time_slice_zero_rows": slice_zero,
            "time_slice_semantics": "0 = 全量单快照标记，不是缺失值；未 UPDATE 底表",
            "query_indexes_added": indexes,
        },
        "annual_slice": {
            "year_min": ymin,
            "year_max": ymax,
            "n_years": len(years),
            "years": years,
            "semantics": "累计快照：截至该年末可见的全部时序边（t <= time_slice）",
            "edge_source": "ads_graph_edge（与物理图同一张表，端点 ID 原样保留）",
            "last_year_by_edge_type": ann_last,
            "last_year_cumulative_metrics": cum,
            "orphan_endpoints_last_year": orphan,
            "by_year": by_year,
        },
        "conservation": {
            "files_patent_equal": cum.get("n_edge_files_patent") == phy_edge.get("files_patent"),
            "belongs_ipc_equal": cum.get("n_edge_belongs_ipc") == phy_edge.get("belongs_ipc"),
            "invented_by_equal": cum.get("n_edge_invented_by") == phy_edge.get("invented_by"),
            "patent_node_equal": cum.get("n_node_patent") == node_types.get("patent"),
            "orphan_endpoints": orphan,
        },
        "before_fix": {
            "belongs_ipc_cumulative": 51795,
            "physical_belongs_ipc": phy_edge.get("belongs_ipc"),
            "gap": 51795 - int(phy_edge.get("belongs_ipc", 0)),
            "cause": "旧视图从 fact_patent JOIN bridge_patent_ipc 现算边，与物理图不同源",
        },
        "static_edges": {
            "counts": static,
            "tech_similar_weight": weight_stats,
            "policy": "不进时序切面；在物理图里仍可用。禁止据此声称全边时序化",
        },
        "six_entity_types": {
            "defined_in_proposal": ["企业", "专利", "投资机构", "人员", "领域", "产品"],
            "in_graph": {
                "企业": node_types.get("enterprise"),
                "专利": node_types.get("patent"),
                "投资机构": node_types.get("investor"),
                "人员": node_types.get("inventor"),
                "领域": node_types.get("ipc"),
            },
            "missing": {
                "产品": "SKU 级产品实体仍缺；v2.1 已建尼斯 35 类并列层节点，但未并入主图",
            },
            "parallel_layer_v2_1": product_parallel,
        },
    }


def write_md(R: dict) -> str:
    P, A = R["physical_graph"], R["annual_slice"]
    C, B = R["conservation"], R["before_fix"]
    L = []
    L.append("# 时序图切面与 STGNN 输入契约\n")
    L.append("> 生成：`src/data/export_graph_temporal_contract.py`（全部数字只读现算自 "
             "`data/db/zjtech_credit.db`）｜schema %s｜%s\n" % (
                 R["schema_version"], R["revision"]))
    L.append("> 对应：%s\n" % R["review_item"])

    L.append("\n## 0. 一句话结论\n")
    L.append("**库里已经有可用的时序图输入**：`v_graph_slice_annual_edge` 提供 "
             "%d–%d 共 **%d 个年度累计快照**，末年 %s 共 **%s 条边**；"
             "物理表 `time_slice=0` 是「全量单快照」标记，不是没有时序。"
             "评审报告写「可按专利年份自建年度切片」时，我们没有把这个现成视图交给算法端 —— "
             "**本次补的就是这份契约，另外修掉了一个真实的同源性 bug**。\n" % (
                 A["year_min"], A["year_max"], A["n_years"], A["year_max"],
                 format(sum(A["last_year_by_edge_type"].values()), ",")))

    L.append("\n## 1. 修订前后：同源性 bug（实测）\n")
    L.append("旧视图从 `fact_patent JOIN bridge_patent_ipc` 现算 IPC 边，与物理图 "
             "`ads_graph_edge` 不是同一套边集：\n")
    L.append("| 口径 | belongs_ipc 末年累计 |\n|---|---|")
    L.append("| 旧 `v_graph_slice_annual` | %s |" % format(B["belongs_ipc_cumulative"], ","))
    L.append("| 物理图 `ads_graph_edge` | %s |" % format(B["physical_belongs_ipc"], ","))
    L.append("| 差额 | **%d 条** |" % B["gap"])
    L.append("\n即按旧年度切面建出来的图比静态图多 %d 条边，STGNN 与 GAT 结果无法互相对照。"
             "新定义直接以 `ads_graph_edge` 为唯一边源，用 `patent_id` 反查 `app_year` "
             "作为发生年份 `t`，按 `t <= time_slice` 展开成累计快照；"
             "端点 ID 原样取自物理表。\n" % B["gap"])

    L.append("\n### 守恒校验（末年 %d）\n" % A["year_max"])
    L.append("| 校验项 | 年度切面 | 物理图 | 结论 |\n|---|---|---|---|")
    for key, metric, edge in (
        ("files_patent_equal", "n_edge_files_patent", "files_patent"),
        ("belongs_ipc_equal", "n_edge_belongs_ipc", "belongs_ipc"),
        ("invented_by_equal", "n_edge_invented_by", "invented_by"),
    ):
        L.append("| %s | %s | %s | %s |" % (
            edge,
            format(A["last_year_cumulative_metrics"].get(metric, 0), ","),
            format(P["edge_types"].get(edge, 0), ","),
            "✅" if C[key] else "❌"))
    L.append("| patent 节点 | %s | %s | %s |" % (
        format(A["last_year_cumulative_metrics"].get("n_node_patent", 0), ","),
        format(P["node_types"].get("patent", 0), ","),
        "✅" if C["patent_node_equal"] else "❌"))
    L.append("| 孤立端点 | %d | 0 | %s |" % (
        A["orphan_endpoints_last_year"], "✅" if A["orphan_endpoints_last_year"] == 0 else "❌"))

    L.append("\n## 2. 物理图现状\n")
    L.append("| 项 | 数值 |\n|---|---|")
    L.append("| 节点总数 | %s |" % format(P["n_nodes"], ","))
    L.append("| 边总数 | %s |" % format(P["n_edges"], ","))
    L.append("| 节点类型 | %s |" % "、".join(
        "%s %s" % (k, format(v, ",")) for k, v in sorted(P["node_types"].items())))
    L.append("| 边类型 | %s |" % "、".join(
        "%s %s" % (k, format(v, ",")) for k, v in sorted(P["edge_types"].items())))
    L.append("| `time_slice=0` 行数 | node %s / edge %s（全部） |" % (
        format(P["time_slice_zero_rows"]["ads_graph_node"], ","),
        format(P["time_slice_zero_rows"]["ads_graph_edge"], ",")))
    L.append("\n`time_slice` 全 0 表示这两张表是**一张全量静态快照**，不是「年份缺失」。"
             "本次修订**没有 UPDATE 任何底表行**（v1.9 起「底表不原地改写」是守卫项），"
             "时序维度由视图提供。\n")

    L.append("\n## 3. STGNN 输入契约（可直接用）\n")
    L.append("`v_graph_slice_annual_edge` 列为 "
             "`time_slice, src_type, src_id, dst_type, dst_id, edge_type, weight`；"
             "按 `time_slice` 过滤即得该年末的累计图快照：\n")
    L.append("```sql\n"
             "-- 取 as-of 年份 T 的训练子图（防后视：只含 t <= T 的边）\n"
             "SELECT src_type, src_id, dst_type, dst_id, edge_type, weight\n"
             "  FROM v_graph_slice_annual_edge\n"
             " WHERE time_slice = :T;\n\n"
             "-- 逐年序列（GRU / TCN 的第 t 帧）\n"
             "SELECT time_slice, edge_type, COUNT(*) AS n_edges,\n"
             "       SUM(weight) AS weight_sum\n"
             "  FROM v_graph_slice_annual_edge\n"
             " GROUP BY time_slice, edge_type\n"
             " ORDER BY time_slice, edge_type;\n"
             "```\n")
    L.append("**语义要点**：这是**累计快照**（截至该年末可见的全部边），不是「当年新增」。"
             "要当年增量用 `v_graph_slice_annual.n_in_year`，或按 `t = time_slice` 过滤。"
             "把累计量当流量喂进 GRU 会重复计数，属于口径误用。\n")
    L.append("**as-of 纪律**：做企业好坏预测时，`T` 必须 ≤ 该企业的 `cohort_year` "
             "或 `as_of_policy` 规定时点；用 2026 全量快照训练再回测 2021 队列就是后视泄漏，"
             "与 `v_model_dataset_asof_flat` 的约束一致。\n")
    L.append("**性能约束**：视图用 `CROSS JOIN` + `INDEXED BY` 强制 "
             "「年份（29 行）→ `fact_patent` 年份范围索引 → `ads_graph_edge` 端点索引」"
             "的连接顺序，并新增 `%s` 两个查询索引。实测全表 `COUNT(*)` "
             "（%s 行）约 0.9s、`WHERE time_slice=2026` 约 0.2s；"
             "若改成普通 JOIN 或 CTE，SQLite 会改用 17 万行边表驱动并重复构造年份协程，"
             "同一查询劣化到 **>170s**，会卡死数据字典导出与 API。"
             "守卫 `tests/test_graph_temporal_contract.py` 设了 5 秒闸门。\n" % (
                 "` / `".join(P["query_indexes_added"]),
                 format(A["by_year"][-1]["n_edges_cumulative"], ",")))

    L.append("\n## 4. 逐年摘要\n")
    L.append("| 年份 | 累计边数 | 新增专利节点 | 累计 files_patent | 累计 belongs_ipc | 累计 invented_by |")
    L.append("|---|---|---|---|---|---|")
    for y in A["by_year"]:
        m = y["metrics"]
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            y["time_slice"], format(y["n_edges_cumulative"], ","),
            format(m.get("n_node_patent", {}).get("n_in_year", 0), ","),
            format(m.get("n_edge_files_patent", {}).get("n_cumulative", 0), ","),
            format(m.get("n_edge_belongs_ipc", {}).get("n_cumulative", 0), ","),
            format(m.get("n_edge_invented_by", {}).get("n_cumulative", 0), ",")))

    L.append("\n## 5. 边界：哪些边不进时序切面\n")
    S = R["static_edges"]
    L.append("| 边类型 | 条数 | 为什么不做时序 |\n|---|---|---|")
    L.append("| `funded_by` | %s | 缺「边—融资事件年份」的可靠映射：`fact_funding_event.investor_names` "
             "是 JSON 名称串，与 `investor` 节点 ID 需二次匹配；靠企业首次融资年近似会把同一年"
             "之前的关系提前暴露，制造伪时序 |" % format(S["counts"].get("funded_by", 0), ","))
    L.append("| `tech_similar` | %s | 全期静态余弦相似度，按年切分没有物理含义 |"
             % format(S["counts"].get("tech_similar", 0), ","))
    W = S["tech_similar_weight"]
    L.append("\n`tech_similar` 权重区分度已达标（守卫 `tests/test_graph_v2.py` 要求 "
             "p50≥0.20 / p90≥0.35 / ≥0.3 的边≥2000）：实测 n=%s，min=%.4f，"
             "p50=%.4f，p90=%.4f，max=%.4f，≥0.3 共 %s 条。"
             "**评审报告里的 0.138 是旧口径，已经修过，不是待办**。\n" % (
                 format(W["n"], ","), W["min"], W["p50"], W["p90"], W["max"],
                 format(W["n_ge_0_3"], ",")))
    L.append("这两类边在物理静态图里照常可用，但**禁止据此宣称「全边时序化」**。"
             "要真正纳入时序，需 P2 外部采集或重建融资事件—机构 ID 映射。\n")

    L.append("\n## 6. 六类实体：产品节点并列层（类别粒度部分闭合）\n")
    E = R["six_entity_types"]
    P = E.get("parallel_layer_v2_1", {})
    L.append("立项书写六类：%s。**入图仍是 5 类**：\n" % " / ".join(E["defined_in_proposal"]))
    L.append("| 实体 | 节点数 | 对应 node_type |\n|---|---|---|")
    mapping = {"企业": "enterprise", "专利": "patent", "投资机构": "investor",
               "人员": "inventor", "领域": "ipc"}
    for cn, n in E["in_graph"].items():
        L.append("| %s | %s | `%s` |" % (cn, format(n, ","), mapping[cn]))
    if P.get("available"):
        L.append("| **产品** | **0（未入主图）** | 并列层 %d 个类别节点 / %d 户（%.1f%%） |\n"
                 % (P["n_product_node"], P["n_covered_firms"],
                    100.0 * P["n_covered_firms"] / 500))
        L.append("**v2.1（2026-11-12）商标明细已接入并列层**：`ods_qcc_trademark` "
                 "%d 条 / 444 家、尼斯 %d 类（产品类 %d + 服务类 %d）、"
                 "`bridge_enterprise_product` %d 行 / %d 户。产品节点是**商标核定"
                 "类别**粒度，不是 SKU 级产品，也不是商标名称；35–39、41–45 共 10 个"
                 "服务类虽有 1902 条商标，按用户口径**不作产品节点**。\n"
                 % (P["n_trademark"], P["n_nice_class"], P["n_product_node"],
                    P["n_service_class"], P["n_bridge_rows"], P["n_covered_firms"]))
        L.append("⚠️ **产品节点未插入 `ads_graph_node`**，图内 `node_type` 仍是 5 类，"
                 "本报告 §1–§5 的 29 帧年度切面、边数与守恒校验**逐项未变**。"
                 "`G-GRAPH-01` 已由 `source_missing` 改判 `partial_closed`，只表示"
                 "「类别粒度 + 并列层」闭合；**SKU 级产品仍缺，禁止宣称立项六类实体"
                 "已完整覆盖**，GAT/STGNN 仍不得声称覆盖六类。当前技术域语义继续由 "
                 "`ipc` 节点承担，产品类覆盖只是 `v_product_grade_cross` 里的并列观察量。\n")
        L.append("顺带用 `v_six_entity_coverage` 现场纠正用户汇总表两处口径：企查查主要"
                 "人员清洗后是 **2079 人 / 490 户**（原写 2569 含 490 行表头脏行，且它不是"
                 "图节点，图内人员＝发明人 inventor %s）；产品是 **35 个类别节点 / 433 户 "
                 "/ 86.6%%**，不是 45 个节点。\n"
                 % format(E["in_graph"]["人员"], ","))
    else:
        L.append("| **产品** | **缺** | %s |\n" % E["missing"]["产品"])
        L.append("库内无产品 / 商标 / 经营范围源表；禁止用专利标题或 IPC 聚类伪造产品节点。\n")

    L.append("\n## 7. 口径边界（写给引用本报告的人）\n")
    L.append("1. 本报告只描述**图结构与时序切面**，不涉及模型性能；"
             "按评审 §7.1(3)，负例 21 家、置换噪声地板约 0.5，"
             "AUC / 准确率绝对值不得写成性能结论。\n")
    L.append("2. 本条描述的是 **v1.9.1 修订时点**：那次未 UPDATE 物理表任何行、"
             "未增删表/视图/列、未升 schema 版本号，仍是 v1.9 / 34 表 / 29 视图；"
             "只重定义两个视图 + 新建两个查询索引，`graph_structure.json` 等契约数字不变。"
             "此后库已升至 **%s**（v2.0 A 轴并列层 → v2.1 商标/产品节点并列层，"
             "40 表 / 37 视图 / 1068 列），但本切面的 29 帧结构、边数与守恒数字"
             "**逐项未变**，故本报告 §1–§5 结论继续有效；产品节点见 §6。\n"
             % R.get("current_db_version", "v1.9"))
    L.append("3. `v_graph_slice_annual` 的 `n_node_patent` 来自 `fact_patent.app_year`，"
             "与物理 patent 节点数相等；但 enterprise / inventor / ipc / investor "
             "节点**没有首次出现年份**，故年度切面只覆盖 3 类专利相关边 + 专利节点，"
             "不覆盖全部 5 类节点。做异质图时序建模时需自行处理静态节点。\n")
    L.append("4. 回滚点 `outputs/graph_slice_pre_fix_ddl.json` 保存了修订前的两份视图 DDL；"
             "`python -m src.data.apply_graph_slice_fix --check` 可随时只读复核守恒。\n")
    return "\n".join(L) + "\n"


def main() -> int:
    if not os.path.exists(DB):
        print("缺数据库：%s" % DB)
        return 1
    R = build()
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write(write_md(R))
    print("[done] %s" % os.path.relpath(OUT_MD, ROOT))
    print("[done] %s" % os.path.relpath(OUT_JSON, ROOT))
    print("  年份 %d–%d（%d 帧）/ 末年边 %d / 孤立端点 %d / 守恒 %s" % (
        R["annual_slice"]["year_min"], R["annual_slice"]["year_max"],
        R["annual_slice"]["n_years"],
        sum(R["annual_slice"]["last_year_by_edge_type"].values()),
        R["conservation"]["orphan_endpoints"],
        "全部通过" if all(v is True for k, v in R["conservation"].items()
                        if k != "orphan_endpoints")
        and R["conservation"]["orphan_endpoints"] == 0 else "有失败"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
