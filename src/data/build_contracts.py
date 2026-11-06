"""把「未就绪」的 12 个接口契约，从真实数据源产出到 ``outputs/contracts/``。

设计纪律（与 field_gap_registry 一致）
--------------------------------------
1. **能实测的实测**：直接查 SQLite，或复用 ``outputs/`` 下已落盘的实测 json。
2. **不能实测的分级标注**，绝不补零、绝不造点。每个文件顶层强制带 ``provenance``：

   ``measured``       有直接实测来源，数值可展示、可引用。
   ``derived``        由实测数据算出来的（AUC / 分布 / 中心性），可展示，附计算方法。
   ``partial``        只覆盖部分维度，``missing`` 里逐条列出缺什么、为什么缺。
   ``reconstructed``  由实测锚点反推曲线，形状可信、非逐点实测，禁止当作逐点数据。
   ``unavailable``    数据源根本不存在；只给原因和补救条件，不给任何数值。

3. **矛盾检测**：底表有数但视图全空时，写进 ``data_quality.contradictions``，
   而不是让下游看见空列就以为「没采」。

运行::

    python -m src.data.build_contracts          # 写入 outputs/contracts/
    python -m src.data.build_contracts --check  # 只报告，不写盘
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT_DIR = os.path.join(ROOT, "outputs")
CONTRACT_DIR = os.path.join(OUT_DIR, "contracts")
REGISTRY_PATH = os.path.join(ROOT, "configs", "field_gap_registry.json")

TRAIN_VIEW = "v_model_train_ready"
ASOF_VIEW = "v_model_dataset_asof_flat"

# 训练就绪视图里的 11 个特征（剔除 4 个 cohort 常量列：m_cert / qual_rank /
# qual_rank_delta / qual_upgrade_cum —— 它们在 cohort 切面下恒为定值，AUC=0.5）
TRAIN_FEATURES = [
    "n_patent_app", "n_patent_invention", "n_patent_grant", "inv_patent_ratio",
    "tech_conversion", "n_ipc_subclass", "tech_breadth_entropy",
    "tech_breadth_ipc_class", "grant_rate", "core_staff_stability", "n_inventor",
]

# 特征语义分组（人工定义，依据列名与计算口径；不是从数据里"猜"出来的聚类）
FEATURE_GROUPS: dict[str, dict] = {
    "规模产出": {
        "cn": "创新规模产出", "features": ["n_patent_app", "n_patent_invention", "n_patent_grant"],
        "note": "按申请/发明/授权的绝对件数度量创新体量",
    },
    "产出质量": {
        "cn": "创新产出质量", "features": ["inv_patent_ratio", "grant_rate", "tech_conversion"],
        "note": "发明占比、授权率、转化率；衡量「量」之外的结构与落地",
    },
    "技术广度": {
        "cn": "技术领域广度", "features": ["n_ipc_subclass", "tech_breadth_entropy",
                                      "tech_breadth_ipc_class"],
        "note": "IPC 子类数量与熵值，反映技术分散度",
    },
    "团队稳定": {
        "cn": "团队与稳定性", "features": ["n_inventor", "core_staff_stability"],
        "note": "发明人规模与核心人员留存",
    },
    "资质等级": {
        "cn": "资质与荣誉", "features": ["m_cert", "qual_rank", "qual_rank_delta",
                                    "qual_upgrade_cum"],
        "note": "高企资格、资质层级及其变动；**cohort 切面下为常量，无判别力**",
    },
}


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def q1(con: sqlite3.Connection, sql: str, params=()) -> Any:
    r = con.execute(sql, params).fetchone()
    return r[0] if r else None


def qa(con: sqlite3.Connection, sql: str, params=()) -> list[tuple]:
    return con.execute(sql, params).fetchall()


def cols_of(con: sqlite3.Connection, obj: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{obj}")')]


def has_obj(con: sqlite3.Connection, name: str) -> bool:
    return q1(con, "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (name,)) > 0


def prov(kind: str, source: str, method: str, **extra) -> dict:
    """统一的来源声明块 —— 前端可据此决定「能不能展示数值」。"""
    block = {
        "kind": kind,
        "displayable": kind in ("measured", "derived", "partial", "reconstructed"),
        "source": source,
        "method": method,
        "generated_at": _now(),
        "schema_version": None,  # 由 write() 回填
    }
    block.update(extra)
    return block


def _schema_version(con: sqlite3.Connection) -> str:
    v = q1(con, "SELECT version FROM meta_schema_version ORDER BY rowid DESC LIMIT 1")
    return str(v) if v else "unknown"


def write(name: str, payload: dict, con: Optional[sqlite3.Connection] = None,
          dry_run: bool = False) -> str:
    """落盘。强制校验 provenance 存在，缺了直接报错而不是静默写出。"""
    if "provenance" not in payload:
        raise ValueError(f"{name}: 缺少 provenance，拒绝写出（这是防造假的硬约束）")
    if con is not None and payload["provenance"].get("schema_version") is None:
        payload["provenance"]["schema_version"] = _schema_version(con)
    os.makedirs(CONTRACT_DIR, exist_ok=True)
    path = os.path.join(CONTRACT_DIR, name)
    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  {'[dry]' if dry_run else '写'} {name:28s} {len(json.dumps(payload)):>7,} B"
          f"  provenance={payload['provenance']['kind']}")
    return path


# ---------------------------------------------------------------------------
# 1. data_quality.json
# ---------------------------------------------------------------------------
# 核心表清单：这些表的下游用途明确，全部逐列算空值率
QUALITY_CORE_TABLES = [
    "dim_enterprise", "dim_inventor", "dim_ipc", "dim_qual_type",
    "fact_patent", "fact_qualification", "fact_qualification_event", "fact_funding_event",
    "bridge_patent_inventor", "bridge_patent_ipc",
    "dws_tech_feature", "dws_growth_feature", "dws_fusion_feature",
    "ads_label", "ads_model_dataset", "ads_survival_period",
    "ads_graph_node", "ads_graph_edge",
    "ods_qcc_annual", "ods_qcc_cert", "ods_qcc_honor", "ods_qcc_shareholder",
]


def _col_nulls(con: sqlite3.Connection, table: str) -> dict:
    """一次扫描算全表每列空值数（避免 698 次全表扫描）。"""
    cols = cols_of(con, table)
    if not cols:
        return {"n_rows": 0, "columns": []}
    exprs = ", ".join(f'SUM(CASE WHEN "{c}" IS NULL OR "{c}"=\'\' THEN 1 ELSE 0 END)'
                      for c in cols)
    row = con.execute(f'SELECT COUNT(*), {exprs} FROM "{table}"').fetchone()
    n = row[0] or 0
    out = []
    for c, nulls in zip(cols, row[1:]):
        nulls = nulls or 0
        out.append({
            "column": c,
            "n_null": nulls,
            "null_rate": round(nulls / n, 4) if n else None,
        })
    return {"n_rows": n, "columns": out}


def build_data_quality(con: sqlite3.Connection) -> dict:
    tables = []
    total_cells = total_null = 0
    for t in QUALITY_CORE_TABLES:
        if not has_obj(con, t):
            tables.append({"table": t, "exists": False})
            continue
        info = _col_nulls(con, t)
        full = sum(1 for c in info["columns"] if c["null_rate"] == 0.0)
        empty = sum(1 for c in info["columns"] if c["null_rate"] == 1.0 and info["n_rows"])
        total_cells += info["n_rows"] * len(info["columns"])
        total_null += sum(c["n_null"] for c in info["columns"])
        tables.append({
            "table": t, "exists": True, "n_rows": info["n_rows"],
            "n_columns": len(info["columns"]),
            "columns_full": full,
            "columns_empty": empty,
            "completeness": round(1 - sum(c["null_rate"] or 0 for c in info["columns"])
                                  / max(len(info["columns"]), 1), 4),
            "empty_columns": [c["column"] for c in info["columns"] if c["null_rate"] == 1.0],
            "worst_columns": sorted(
                [c for c in info["columns"] if (c["null_rate"] or 0) > 0],
                key=lambda c: -(c["null_rate"] or 0))[:8],
        })

    # 登记册三态汇总（真实结构：顶层 items / substitutes / meta）
    reg_summary = {"total": 0, "null_by_design": 0, "source_missing": 0,
                   "partial_insufficient": 0, "substitutes": 0, "traps": 0}
    registry_detail = []
    reg_meta: dict = {}
    reg: dict = {}
    if os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            reg = json.load(f)
        items = reg.get("items") or []
        for it in items:
            st = it.get("status") or "unknown"
            reg_summary["total"] += 1
            if st in reg_summary:
                reg_summary[st] += 1
            registry_detail.append({
                "table": it.get("table"), "field": it.get("field"),
                "cn": it.get("cn"), "indicator": it.get("indicator"),
                "priority": it.get("priority"), "status": st,
                "decision": it.get("decision"),
                "reason": it.get("reason") or it.get("note"),
            })
        reg_summary["substitutes"] = len(reg.get("substitutes") or [])
        reg_summary["traps"] = len(reg.get("traps") or [])
        reg_meta = reg.get("meta") or {}

    # 矛盾检测：底表有数、视图全空
    contradictions = []
    asof_empty = [c["column"] for c in _col_nulls(con, ASOF_VIEW)["columns"]
                  if c["null_rate"] == 1.0]
    growth_map = {
        "rev_cagr": None, "emp_cagr": None, "patent_cagr": "patent_cagr",
        "funding_to_last_val": "funding_net_increase_wan",
        "investor_prestige": "investor_prestige", "rd_intensity": None,
    }
    for vcol, base_col in growth_map.items():
        if vcol not in asof_empty:
            continue
        if base_col and has_obj(con, "dws_growth_feature"):
            n_base = q1(con, f'SELECT COUNT("{base_col}") FROM dws_growth_feature') or 0
            if n_base:
                contradictions.append({
                    "view": ASOF_VIEW, "column": vcol,
                    "issue": "视图列全空，但底表 dws_growth_feature.%s 有 %d 行有值"
                             % (base_col, n_base),
                    "impact": "下游看到全空会误判为「未采集」；实为视图未把该底表列带出",
                    "action": "在视图定义里补 JOIN dws_growth_feature，或改用底表取数",
                })
        else:
            contradictions.append({
                "view": ASOF_VIEW, "column": vcol,
                "issue": "视图列全空且无对应底表列",
                "impact": "该特征在本数据集不可用",
                "action": "登记为 source_missing，需新增数据采购",
            })

    # 关联键健康度：enterprise_id 为空不会违反表约束，但会让该行在建模时无法归属企业。
    # 这类问题容易被 worst_columns 的 Top-N 淹没（fact_patent 的相关键空值率仅 2.3%，
    # 排不进前 8），所以单独列一段 —— 「766 件专利未匹配到企业」是必须看见的事实。
    join_key_health = []
    for t in QUALITY_CORE_TABLES:
        if not has_obj(con, t) or "enterprise_id" not in cols_of(con, t):
            continue
        n_rows = q1(con, f'SELECT COUNT(*) FROM "{t}"') or 0
        n_null = q1(con, f'SELECT COUNT(*) FROM "{t}" WHERE enterprise_id IS NULL') or 0
        rate = round(n_null / n_rows, 4) if n_rows else None
        join_key_health.append({
            "table": t, "column": "enterprise_id",
            "n_rows": n_rows, "n_null": n_null, "null_rate": rate,
            "severity": "high" if (rate or 0) > 0.01 else "ok",
            "impact": ("这些行无法归属企业，企业级特征与建模时会整行丢弃"
                       if n_null else "全部可归属企业"),
        })

    # 逐层汇总
    layer_rows = {}
    for t in QUALITY_CORE_TABLES:
        if not has_obj(con, t):
            continue
        layer = t.split("_")[0].upper()
        layer_rows[layer] = layer_rows.get(layer, 0) + (q1(con, f'SELECT COUNT(*) FROM "{t}"') or 0)

    return {
        "summary": {
            "tables_checked": len([t for t in tables if t.get("exists")]),
            "total_rows": sum(t.get("n_rows", 0) for t in tables),
            "cell_completeness": round(1 - total_null / total_cells, 4) if total_cells else None,
            "rows_by_layer": layer_rows,
        },
        "tables": tables,
        "join_key_health": join_key_health,
        "gap_registry": {
            "meta": reg_meta if os.path.exists(REGISTRY_PATH) else None,
            "summary": reg_summary, "fields": registry_detail,
            "substitutes": (reg.get("substitutes") or []) if os.path.exists(REGISTRY_PATH) else [],
        },
        "contradictions": contradictions,
        "provenance": prov(
            "measured",
            "SQLite zjtech_credit.db 全表逐列扫描 + configs/field_gap_registry.json",
            "SUM(CASE WHEN col IS NULL OR col='' ...) 单次全表扫描，按表汇总",
            note="空值率来自库内实测；contradictions 是自动比对底表与视图得出的矛盾，未人工干预",
        ),
    }


# ---------------------------------------------------------------------------
# 2. panel_depth.json
# ---------------------------------------------------------------------------
def build_panel_depth(con: sqlite3.Connection) -> dict:
    # as-of 面板结构
    asof = qa(con, f"""
        SELECT as_of_policy, as_of_year, COUNT(*) AS n_rows, COUNT(DISTINCT enterprise_id) AS n_ent
        FROM {ASOF_VIEW} GROUP BY 1,2 ORDER BY 2
    """)
    depth = qa(con, f"""
        SELECT k, COUNT(*) FROM (
            SELECT enterprise_id, COUNT(DISTINCT as_of_year) k FROM {ASOF_VIEW} GROUP BY 1
        ) GROUP BY 1 ORDER BY 1
    """)
    n_ent = q1(con, f"SELECT COUNT(DISTINCT enterprise_id) FROM {ASOF_VIEW}") or 0

    # 原始观测覆盖：企业有专利活动的年数（不是 as-of 快照年）
    patyr = qa(con, """
        SELECT k, COUNT(*) FROM (
            SELECT enterprise_id, COUNT(DISTINCT substr(app_date,1,4)) k
            FROM fact_patent WHERE app_date IS NOT NULL AND app_date<>'' GROUP BY 1
        ) GROUP BY 1 ORDER BY 1
    """)
    year_cover = qa(con, """
        SELECT substr(app_date,1,4) y, COUNT(DISTINCT enterprise_id) n
        FROM fact_patent WHERE app_date IS NOT NULL AND app_date<>''
        GROUP BY 1 ORDER BY 1
    """)

    # 资质年限跨度
    qual_span = qa(con, """
        SELECT k, COUNT(*) FROM (
            SELECT enterprise_id,
                   (CAST(MAX(substr(first_qual_year,1,4)) AS INT)
                    - CAST(MIN(substr(first_qual_year,1,4)) AS INT) + 1) k
            FROM dim_enterprise
            WHERE first_qual_year IS NOT NULL AND first_qual_year<>'' GROUP BY 1
        ) WHERE k IS NOT NULL GROUP BY 1 ORDER BY 1
    """)

    balanced = len(depth) == 1
    return {
        "asof_panel": {
            "n_enterprises": n_ent,
            "n_periods": len(asof),
            "n_rows": sum(r[2] for r in asof),
            "balanced": balanced,
            "periods": [{"as_of_policy": r[0], "as_of_year": r[1],
                         "n_rows": r[2], "n_enterprises": r[3]} for r in asof],
            "depth_distribution": [{"periods_per_enterprise": r[0], "n_enterprises": r[1]}
                                   for r in depth],
        },
        "raw_observation": {
            "patent_years_per_enterprise": [
                {"years": r[0], "n_enterprises": r[1]} for r in patyr],
            "yearly_coverage": [{"year": r[0], "n_enterprises": r[1]} for r in year_cover],
            "qualification_span_years": [
                {"span_years": r[0], "n_enterprises": r[1]} for r in qual_span],
        },
        "missing": [
            {"item": "财务年度面板", "reason": "dws_growth_feature 每家仅 1 行 stat_year，"
                                             "无连续年度财务；latest_rev_wan 仅 12/500 有值",
             "impact": "无法做营收/利润的面板回归与增长预测"},
            {"item": "更长观测窗", "reason": "名单只到 2021 年，as-of 切面仅 2 期",
             "impact": "无法构成 t≥3 的事件史，生存模型只能用 3 期"},
        ],
        "provenance": prov(
            "measured",
            f"{ASOF_VIEW} + fact_patent + dim_enterprise",
            "按企业统计 distinct as_of_year / 专利活动年数 / 资质年限跨度，分组计数",
            note="depth_distribution 全为 1 种取值时 balanced=true，说明是平衡面板而非缺失",
        ),
    }


# ---------------------------------------------------------------------------
# 3. feature_tree.json
# ---------------------------------------------------------------------------
def build_feature_tree(con: sqlite3.Connection) -> dict:
    # 单变量 AUC（复用 db_views 的参考集，保证与 proxy_score 同一口径）
    sys.path.insert(0, ROOT)
    from src.app.backend import db_views as dv
    ref = dv._ref()

    asof_cols = cols_of(con, ASOF_VIEW)
    n_asof = q1(con, f"SELECT COUNT(*) FROM {ASOF_VIEW}") or 0

    def coverage(col: str) -> Optional[float]:
        if col not in asof_cols:
            return None
        n = q1(con, f'SELECT COUNT("{col}") FROM {ASOF_VIEW}') or 0
        return round(n / n_asof, 4) if n_asof else None

    nodes, groups = [], []
    listed = set()
    for gkey, g in FEATURE_GROUPS.items():
        gnodes = []
        for f in g["features"]:
            spec = (ref.get("features") or {}).get(f)
            node = {
                "id": f, "label": f, "group": gkey,
                "cn": dv.FEATURE_CN.get(f, f),
                "in_train_ready": f in TRAIN_FEATURES,
                "coverage_asof": coverage(f),
                "univariate_auc": round(spec["auc"], 4) if spec and spec.get("auc") else None,
                "discriminative": bool(spec and spec.get("auc") and abs(spec["auc"] - 0.5) >= 0.05),
            }
            nodes.append(node)
            gnodes.append(f)
            listed.add(f)
        groups.append({"id": gkey, "label": g["cn"], "note": g["note"],
                       "n_features": len(gnodes), "children": gnodes})

    # 视图里存在但未分组的列，单列一块，避免"看不见"
    ungrouped = [c for c in asof_cols
                 if c not in listed
                 and c not in ("enterprise_id", "name_display", "region_code", "cohort_year",
                               "split", "label", "label_name", "label_value", "dataset_ver",
                               "label_task", "built_at", "split_protocol", "as_of_policy",
                               "as_of_year")]
    if ungrouped:
        groups.append({
            "id": "ungrouped", "label": "未分组（待特征工程归类）",
            "note": "视图有列但尚未纳入语义分组；覆盖率为 0 的是空壳列",
            "n_features": len(ungrouped),
            "children": ungrouped,
        })
        for f in ungrouped:
            nodes.append({
                "id": f, "label": f, "group": "ungrouped", "cn": dv.FEATURE_CN.get(f, f),
                "in_train_ready": False, "coverage_asof": coverage(f),
                "univariate_auc": None, "discriminative": False,
            })

    return {
        "root": {"id": "root", "label": "特征体系", "n_features": len(nodes)},
        "groups": groups,
        "features": nodes,
        "summary": {
            "n_features_total": len(nodes),
            "n_in_train_ready": len([n for n in nodes if n["in_train_ready"]]),
            "n_discriminative": len([n for n in nodes if n["discriminative"]]),
            "reference": {"view": TRAIN_VIEW, "n": ref.get("n"),
                          "n_pos": ref.get("n_pos"), "n_neg": ref.get("n_neg")},
            "auc_threshold": 0.05,
        },
        "caveat": "univariate_auc 是单变量秩统计量，不控制共线性、未做交叉验证；"
                  "coverage_asof 为 0 的特征在 as-of 视图里是空壳列，不可用于建模。",
        "provenance": prov(
            "derived",
            f"{TRAIN_VIEW}（307 行 21 正例）+ {ASOF_VIEW}（614 行）",
            "单变量 AUC 用 Mann-Whitney 秩公式；覆盖率 = COUNT(col)/总行数；"
            "分组为人工按列口径定义，非聚类结果",
        ),
    }


# ---------------------------------------------------------------------------
# 4. graph_structure.json
# ---------------------------------------------------------------------------
def build_graph_structure(con: sqlite3.Connection) -> dict:
    node_types = qa(con, "SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY 1 ORDER BY 2 DESC")
    edge_types = qa(con, """
        SELECT edge_type, src_type, dst_type, COUNT(*), SUM(weight)
        FROM ads_graph_edge GROUP BY 1,2,3 ORDER BY 4 DESC
    """)
    slices = qa(con, "SELECT time_slice, COUNT(*) FROM ads_graph_node GROUP BY 1 ORDER BY 1")

    # 度分布（全部边，按节点 id 汇总出入度）
    # 不能用相关子查询 `(SELECT COUNT(*) ... WHERE src_id=n.node_id OR dst_id=n.node_id)`：
    # 45,077 节点 × 101,491 边会退化成数十亿次比较（实测直接超时）。
    # 改成两次 GROUP BY 预聚合再 LEFT JOIN。
    deg = qa(con, """
        SELECT node_type, k, COUNT(*) FROM (
            SELECT n.node_type AS node_type,
                   COALESCE(s.c, 0) + COALESCE(d.c, 0) AS k
            FROM ads_graph_node n
            LEFT JOIN (SELECT src_id AS nid, COUNT(*) c FROM ads_graph_edge GROUP BY 1) s
                   ON s.nid = n.node_id
            LEFT JOIN (SELECT dst_id AS nid, COUNT(*) c FROM ads_graph_edge GROUP BY 1) d
                   ON d.nid = n.node_id
        ) GROUP BY 1,2 ORDER BY 1,2
    """)
    deg_by_type: dict[str, list] = {}
    for t, k, c in deg:
        deg_by_type.setdefault(t, []).append({"degree": k, "n_nodes": c})

    # 连通分量（仅在 enterprise-enterprise tech_similar 子图上，用并查集）
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ents = [r[0] for r in qa(con, "SELECT node_id FROM ads_graph_node WHERE node_type='enterprise'")]
    for e in ents:
        find(e)
    for a, b in qa(con, "SELECT src_id, dst_id FROM ads_graph_edge WHERE edge_type='tech_similar'"):
        if a in parent and b in parent:
            union(a, b)
    comp: dict[str, int] = {}
    for e in ents:
        comp[find(e)] = comp.get(find(e), 0) + 1
    comp_sizes = sorted(comp.values(), reverse=True)

    # 边权分布
    wdist = qa(con, """
        SELECT CAST(weight*10 AS INT)/10.0 AS wb, COUNT(*)
        FROM ads_graph_edge WHERE edge_type='tech_similar' GROUP BY 1 ORDER BY 1
    """)

    n_nodes = sum(r[1] for r in node_types)
    n_edges = q1(con, "SELECT COUNT(*) FROM ads_graph_edge") or 0
    return {
        "summary": {
            "n_nodes": n_nodes, "n_edges": n_edges,
            "n_node_types": len(node_types), "n_edge_types": len(edge_types),
            "density_approx": round(n_edges / max(n_nodes, 1), 4),
            "n_enterprise_components": len(comp_sizes),
            "largest_component": comp_sizes[0] if comp_sizes else 0,
        },
        "node_types": [{"type": r[0], "n": r[1],
                        "share": round(r[1] / n_nodes, 4) if n_nodes else None}
                       for r in node_types],
        "edge_types": [{"type": r[0], "src_type": r[1], "dst_type": r[2],
                        "n": r[3], "weight_sum": round(r[4], 2) if r[4] else None}
                       for r in edge_types],
        "time_slices": [{"time_slice": r[0], "n_nodes": r[1]} for r in slices],
        "degree_distribution": [{"node_type": t, "bins": v} for t, v in deg_by_type.items()],
        "enterprise_components": {
            "n_components": len(comp_sizes),
            "sizes": comp_sizes[:30],
            "n_isolated": sum(1 for s in comp_sizes if s == 1),
        },
        "tech_similar_weight_hist": [{"weight_bucket": r[0], "n_edges": r[1]} for r in wdist],
        "provenance": prov(
            "measured",
            "ads_graph_node (45,077) + ads_graph_edge (101,491)",
            "节点/边按类型聚合；度=该节点在 ads_graph_edge 的出入边总数；"
            "连通分量用并查集在 enterprise-enterprise tech_similar 子图上计算",
            note="本图谱为本项目真实构建（企业-专利-IPC 异质图），非合成图",
        ),
    }


# ---------------------------------------------------------------------------
# 5. graph_slices.json
# ---------------------------------------------------------------------------
REGION_CN = {
    "330100": "杭州市", "330200": "宁波市", "330300": "温州市", "330400": "嘉兴市",
    "330500": "湖州市", "330600": "绍兴市", "330700": "金华市", "330800": "衢州市",
    "330900": "舟山市", "331000": "台州市", "331100": "丽水市",
}


def build_graph_slices(con: sqlite3.Connection) -> dict:
    def stats_for(eids: list[str], key, label) -> Optional[dict]:
        """给定企业集合，回连专利与 tech_similar 边，算这一片的密度指标。"""
        if not eids:
            return None
        ph = ",".join("?" * len(eids))
        # 必须 DISTINCT app_no：fact_patent 有同一申请号多行（32,969 行 → 30,103 件），
        # 按行数会把切片里的专利数虚高约 9%。测试 test_patent_dedup 会拦这种写法。
        n_pat = q1(con, f"""SELECT COUNT(DISTINCT app_no) FROM fact_patent
                            WHERE enterprise_id IN ({ph}) AND app_no IS NOT NULL""",
                   eids) or 0
        n_edge = q1(con, f"""SELECT COUNT(*) FROM ads_graph_edge
                             WHERE edge_type='tech_similar'
                               AND (src_id IN ({ph}) OR dst_id IN ({ph}))""",
                    eids + eids) or 0
        n = len(eids)
        return {
            "key": key, "label": label,
            "n_enterprises": n, "n_patents": n_pat,
            "n_tech_similar_edges": n_edge,
            "edges_per_enterprise": round(n_edge / n, 2) if n else None,
            "patents_per_enterprise": round(n_pat / n, 2) if n else None,
        }

    # 地区切片：dim_enterprise 有 region_code
    by_region = []
    for g, _n in qa(con, "SELECT region_code, COUNT(*) FROM dim_enterprise "
                         "GROUP BY 1 ORDER BY 2 DESC"):
        eids = [r[0] for r in qa(
            con, "SELECT enterprise_id FROM dim_enterprise WHERE region_code=?", (g,))]
        s = stats_for(eids, g, REGION_CN.get(str(g), str(g)))
        if s:
            by_region.append(s)

    # 认定年切片：cohort_year 在 ads_label，不在 dim_enterprise
    by_cohort = []
    for g, _n in qa(con, "SELECT cohort_year, COUNT(*) FROM ads_label "
                         "GROUP BY 1 ORDER BY 1"):
        eids = [r[0] for r in qa(
            con, "SELECT enterprise_id FROM ads_label WHERE cohort_year=?", (g,))]
        s = stats_for(eids, g, f"{g} 年认定")
        if s:
            by_cohort.append(s)

    # 标签切片（仅 307 家打标样本）
    by_label = []
    for l, n in qa(con, f"""SELECT l, COUNT(*) FROM (
                                SELECT enterprise_id, MAX(label) l
                                FROM {TRAIN_VIEW} GROUP BY 1) GROUP BY 1 ORDER BY 1"""):
        eids = [r[0] for r in qa(
            con, f"SELECT enterprise_id FROM {TRAIN_VIEW} WHERE label=? GROUP BY 1", (l,))]
        s = stats_for(eids, "positive" if l == 1 else "negative",
                      "到期未通过（正例）" if l == 1 else "正常续存（负例）")
        if s:
            s["n_enterprises"] = n
            by_label.append(s)

    # 行业切片可行性如实检查
    n_ind = q1(con, "SELECT COUNT(qcc_industry_mid) FROM dim_enterprise") or 0
    n_ent = q1(con, "SELECT COUNT(*) FROM dim_enterprise") or 0

    return {
        "by_region": by_region,
        "by_cohort_year": by_cohort,
        "by_label": by_label,
        "slices_available": ["region_code", "cohort_year", "label"],
        "missing": [
            {"item": "行业切片",
             "reason": f"qcc_industry_mid 覆盖 {n_ind}/{n_ent}",
             "impact": "覆盖率过低时切片会大量落空，故不产出"},
            {"item": "时间切片", "reason": "graph 的 time_slice 只有单一取值，图未按年重建",
             "impact": "无法做图谱演化动画"},
        ],
        "provenance": prov(
            "measured",
            "dim_enterprise + fact_patent + ads_graph_edge",
            "按 region_code / cohort_year / label 分组，每组回连企业集合再统计专利与 tech_similar 边",
            note="每片的企业集合是精确 IN 查询，不是估算；n_tech_similar_edges 会跨片重复计数",
        ),
    }


# ---------------------------------------------------------------------------
# 6. graph_centrality.json
# ---------------------------------------------------------------------------
def build_graph_centrality(con: sqlite3.Connection, top_k: int = 50) -> dict:
    edges = qa(con, """
        SELECT src_id, dst_id, weight FROM ads_graph_edge
        WHERE edge_type='tech_similar' AND src_type='enterprise' AND dst_type='enterprise'
    """)
    names = dict(qa(con, "SELECT node_id, display_name FROM ads_graph_node WHERE node_type='enterprise'"))

    deg: dict[str, int] = {}
    wdeg: dict[str, float] = {}
    adj: dict[str, list[str]] = {}
    for a, b, w in edges:
        w = float(w or 1.0)
        for x, y in ((a, b), (b, a)):
            deg[x] = deg.get(x, 0) + 1
            wdeg[x] = wdeg.get(x, 0.0) + w
            adj.setdefault(x, []).append(y)

    # PageRank（无权，damping 0.85，40 次迭代）—— 纯 python，节点仅 500
    nodes = list(names.keys())
    N = len(nodes) or 1
    pr = {n: 1.0 / N for n in nodes}
    for _ in range(40):
        nxt = {n: (1 - 0.85) / N for n in nodes}
        dangling = sum(pr[n] for n in nodes if not adj.get(n))
        for n in nodes:
            nb = adj.get(n) or []
            if not nb:
                continue
            share = 0.85 * pr[n] / len(nb)
            for m in nb:
                if m in nxt:
                    nxt[m] += share
        for n in nodes:
            nxt[n] += 0.85 * dangling / N
        pr = nxt

    ranked = sorted(nodes, key=lambda n: (-wdeg.get(n, 0), -pr.get(n, 0)))[:top_k]
    top = [{
        "rank": i + 1, "enterprise_id": n, "name": names.get(n),
        "degree": deg.get(n, 0), "weighted_degree": round(wdeg.get(n, 0), 3),
        "pagerank": round(pr.get(n, 0), 6),
    } for i, n in enumerate(ranked)]

    def pct(vals: list[float], q: float) -> Optional[float]:
        if not vals:
            return None
        s = sorted(vals)
        i = min(int(q * (len(s) - 1)), len(s) - 1)
        return round(s[i], 4)

    dv = [deg.get(n, 0) for n in nodes]
    wv = [wdeg.get(n, 0) for n in nodes]
    return {
        "summary": {
            "metric": "weighted_degree（主）/ degree / pagerank",
            "n_nodes": len(nodes), "n_edges": len(edges),
            "degree": {"min": min(dv) if dv else None, "max": max(dv) if dv else None,
                       "median": pct(dv, 0.5), "p90": pct(dv, 0.9),
                       "n_isolated": sum(1 for x in dv if x == 0)},
            "weighted_degree": {"min": round(min(wv), 3) if wv else None,
                                "max": round(max(wv), 3) if wv else None,
                                "median": pct(wv, 0.5), "p90": pct(wv, 0.9)},
        },
        "top": top,
        "definition": {
            "degree": "该企业与其他企业存在 tech_similar 边的条数（无向，边算两次都已展开）",
            "weighted_degree": "边权之和（边权=技术相似度）",
            "pagerank": "无权 PageRank，damping=0.85，40 次迭代收敛",
            "note": "中心性只说明「在技术相似图上连接多」，不等于信用或经营质量",
        },
        "provenance": prov(
            "derived",
            "ads_graph_edge（edge_type='tech_similar'，19,945 条）",
            "度/加权度直接聚合；PageRank 用标准幂迭代实现",
            note="图形结构由本项目真实专利共现构建，非合成",
        ),
    }


# ---------------------------------------------------------------------------
# 7. train_curve.json
# ---------------------------------------------------------------------------
def build_train_curve() -> dict:
    gat_path = os.path.join(OUT_DIR, "gat_panel_results.json")
    base_path = os.path.join(OUT_DIR, "baseline_results.json")
    series = []
    caveats = []

    if os.path.exists(gat_path):
        with open(gat_path, encoding="utf-8") as f:
            gat = json.load(f)
        tail = gat.get("history_tail") or []
        epochs_run = (gat.get("train") or {}).get("epochs_run")
        series.append({
            "name": "HeteroGAT · val_auc", "model": "HeteroGAT",
            "dataset": "A 信贷面板", "metric": "val_auc",
            "points": [{"epoch": p.get("epoch"), "val_auc": round(p["val_auc"], 6)}
                       for p in tail if p.get("val_auc") is not None],
            "n_points": len(tail),
            "complete": False,
            "note": f"训练跑满 {epochs_run} epoch，但只落盘了尾部 {len(tail)} 个点",
        })
        caveats.append(f"GAT 曲线只有 {len(tail)}/{epochs_run} 个 epoch 被持久化，"
                       "不是完整训练曲线，不能用来判断收敛形态")

    if os.path.exists(base_path):
        with open(base_path, encoding="utf-8") as f:
            base = json.load(f)
        for arm in ("cv_row", "cv_company"):
            blk = base.get(arm) or {}
            for model in ("logistic_regression", "xgboost"):
                folds = (blk.get("fold_metrics") or {}).get(model) or []
                if not folds:
                    continue
                series.append({
                    "name": f"{model} · {arm} · fold_auc", "model": model,
                    "dataset": "A 信贷面板", "metric": "auc",
                    "points": [{"fold": i + 1,
                                "auc": round(f.get("auc", f.get("test_auc", 0)), 6)}
                               for i, f in enumerate(folds)
                               if f.get("auc") is not None or f.get("test_auc") is not None],
                    "n_points": len(folds), "complete": True,
                    "note": "5 折 GroupKFold，每折一个点（不是逐 epoch）",
                })
        caveats.append("baseline 只保留每折最终指标，无逐 epoch 曲线；折间点是不同折，横轴不可当时间")

    return {
        "series": series,
        "summary": {
            "n_series": len(series),
            "n_partial_series": len([s for s in series if not s["complete"]]),
            "x_axis_note": "GAT 系列横轴为 epoch，baseline 系列横轴为 fold —— 两者不可画在同一张折线图",
        },
        "missing": [
            {"item": "基线逐 epoch 曲线", "reason": "训练脚本未持久化每轮指标",
             "impact": "无法看早停过程与过拟合拐点"},
            {"item": "B 套（浙江 500 家）训练曲线", "reason": "B 套无深度学习训练，"
                                                          "只有 100 折重复交叉验证的 AUC 汇总",
             "impact": "B 套曲线需用 qcc_signal_auc 的折分布代替"},
        ],
        "caveat": "；".join(caveats),
        "provenance": prov(
            "partial",
            "outputs/gat_panel_results.json + outputs/baseline_results.json",
            "直接透传已落盘的逐点指标，未插值、未平滑、未补点",
            missing_dimensions=["GAT 完整 epoch 序列", "baseline 逐 epoch 指标"],
        ),
    }


# ---------------------------------------------------------------------------
# 8. roc_pr_ks.json
# ---------------------------------------------------------------------------
def _phi_inv(p: float) -> float:
    """标准正态分位（Acklam 有理逼近，误差 <1.15e-9）。避免引入 scipy。"""
    if p <= 0 or p >= 1:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _binormal_curve(auc: float, prevalence: float, ks_target: Optional[float] = None,
                    n: int = 101) -> dict:
    """不等方差 binormal ROC 重建。

    模型：``tpr = Φ(a + b·z)``，``fpr = Φ(z)``。此时

    * ``AUC = Φ(a / √(1+b²))``  —— 由 AUC 反解出 ``a``，**AUC 被精确保住**；
    * ``KS  = max_z [Φ(a+bz) - Φ(z)]`` —— 由 ``b`` 决定。

    只有 AUC 一个锚点时取 ``b=1``（等方差，经典假设）。
    若同时给了实测 KS，则在 b 上做网格搜索，令重建 KS 命中实测 KS —— 这样
    **两个锚点都被精确匹配**，曲线形状比等方差假设更贴近实测。

    仍然不是逐样本实测曲线：给定 (AUC, KS) 仍有一族可能的 ROC，
    binormal 只是其中最标准的一支。因此文件一律标 ``reconstructed``。
    """
    auc = min(max(auc, 1e-6), 1 - 1e-6)
    z_lo, z_hi, z_n = -7.0, 7.0, 1401

    def ks_of(b: float) -> tuple[float, float]:
        """返回 (KS, 取得 KS 时的 fpr)。"""
        a = math.sqrt(1 + b * b) * _phi_inv(auc)
        best_ks, best_fpr = -1.0, 0.0
        for i in range(z_n):
            z = z_lo + (z_hi - z_lo) * i / (z_n - 1)
            fpr = _phi(z)
            tpr = _phi(a + b * z)
            d = tpr - fpr
            if d > best_ks:
                best_ks, best_fpr = d, fpr
        return best_ks, best_fpr

    b = 1.0
    fit_mode = "equal_variance(b=1)"
    if ks_target is not None and ks_target > 0:
        # 对数网格粗搜 → 线性细化
        best = (abs(ks_of(1.0)[0] - ks_target), 1.0)
        for i in range(240):
            cand = math.exp(math.log(0.05) + (math.log(12.0) - math.log(0.05)) * i / 239)
            err = abs(ks_of(cand)[0] - ks_target)
            if err < best[0]:
                best = (err, cand)
        b0 = best[1]
        lo, hi = max(b0 * 0.9, 0.01), b0 * 1.1
        for _ in range(40):
            m1 = lo + (hi - lo) / 3
            m2 = hi - (hi - lo) / 3
            if abs(ks_of(m1)[0] - ks_target) < abs(ks_of(m2)[0] - ks_target):
                hi = m2
            else:
                lo = m1
        b = (lo + hi) / 2
        fit_mode = "two_anchor(auc+ks)"

    a = math.sqrt(1 + b * b) * _phi_inv(auc)
    roc, pr = [], []
    for i in range(n):
        fpr = i / (n - 1)
        fpr_c = min(max(fpr, 1e-9), 1 - 1e-9)
        tpr = _phi(a + b * _phi_inv(fpr_c))
        roc.append({"fpr": round(fpr, 6), "tpr": round(tpr, 6)})
        denom = tpr * prevalence + fpr * (1 - prevalence)
        prec = (tpr * prevalence / denom) if denom > 0 else 1.0
        pr.append({"recall": round(tpr, 6), "precision": round(prec, 6)})
    roc[0] = {"fpr": 0.0, "tpr": 0.0}
    roc[-1] = {"fpr": 1.0, "tpr": 1.0}

    ks_recon, ks_fpr = ks_of(b)
    return {
        "a": round(a, 6), "b": round(b, 6), "fit_mode": fit_mode,
        "auc_implied": round(_phi(a / math.sqrt(1 + b * b)), 6),
        "roc": roc, "pr": pr,
        "ks_reconstructed": round(ks_recon, 4),
        "ks_at_fpr": round(ks_fpr, 4),
    }


def build_roc_pr_ks() -> dict:
    curves = []
    base_path = os.path.join(OUT_DIR, "baseline_results.json")
    anchors_used = []

    if os.path.exists(base_path):
        with open(base_path, encoding="utf-8") as f:
            base = json.load(f)
        hold = base.get("holdout_row") or {}
        n_pos = (hold.get("logistic_regression") or {}).get("n_pos") or 75
        n_samp = (hold.get("logistic_regression") or {}).get("n_samples") or 698
        prev = n_pos / n_samp if n_samp else 0.1
        for model in ("logistic_regression", "xgboost"):
            m = hold.get(model) or {}
            if m.get("auc") is None:
                continue
            c = _binormal_curve(m["auc"], prev, ks_target=m.get("ks"))
            measured_ks = m.get("ks")
            curves.append({
                "name": f"{model} · holdout_row", "model": model,
                "dataset": "A 信贷面板（1,714 家 / 3,465 行）",
                "anchor": {"auc": round(m["auc"], 4), "ks_measured": measured_ks,
                           "n_samples": n_samp, "n_pos": n_pos,
                           "prevalence": round(prev, 4)},
                "fit_mode": c["fit_mode"],
                "params": {"a": c["a"], "b": c["b"], "auc_implied": c["auc_implied"]},
                "ks_reconstructed": c["ks_reconstructed"],
                "ks_error_vs_measured": (round(abs(c["ks_reconstructed"] - measured_ks), 4)
                                         if measured_ks else None),
                "roc": c["roc"], "pr": c["pr"],
                "n_points": len(c["roc"]),
            })
            anchors_used.append({"model": model, "auc": m["auc"], "ks": measured_ks})

    multi = []
    sig_path = os.path.join(OUT_DIR, "qcc_signal_auc.json")
    if os.path.exists(sig_path):
        with open(sig_path, encoding="utf-8") as f:
            sig = json.load(f)
        prev = (sig.get("n_positive") or 21) / (sig.get("n_samples") or 307)
        for cfg, blk in (sig.get("configurations") or {}).items():
            for model in ("logistic_regression", "hist_gradient_boosting"):
                m = (blk or {}).get(model) or {}
                if m.get("auc_mean") is None:
                    continue
                multi.append({
                    "configuration": cfg, "model": model,
                    "dataset": "B 浙江高企（307 家 / 21 正例）",
                    "auc_mean": m.get("auc_mean"), "auc_std": m.get("auc_std"),
                    "n_folds": m.get("n_folds"),
                    "note": "B 套只有 100 折重复交叉验证的 AUC 分布，无单一 holdout ROC 可重建",
                })

    return {
        "curves": curves,
        "b_dataset_auc": multi,
        "summary": {
            "n_curves": len(curves),
            "reconstruction": "不等方差 binormal：tpr=Φ(a+b·z)，fpr=Φ(z)；由 AUC 定 a、由实测 KS 定 b",
            "ks_error_max": max([c["ks_error_vs_measured"] for c in curves
                                 if c["ks_error_vs_measured"] is not None], default=None),
        },
        "warnings": [
            "roc / pr 的点位是**由 AUC 锚点重建**，不是逐条样本实测；形状可信、点位不可引用为实测",
            "ks_reconstructed 与 ks_measured 的差值即重建误差，已逐条列出；本轮已用双锚点拟合，残差应接近 0",
            "B 套（本项目主数据集）只有 100 折 AUC 分布，**没有**可重建的单一 ROC —— 21 个正例不足以支撑性能结论",
        ],
        "provenance": prov(
            "reconstructed",
            "outputs/baseline_results.json（holdout_row）+ outputs/qcc_signal_auc.json",
            "binormal 双锚点反推：AUC 定 (a/b 的比值)、实测 KS 定 b，再拓扑导出 PR；"
            "B 套仅透传折分布，不重建",
            anchors=anchors_used,
            note="该文件不得用于声称「实测 ROC 曲线」",
        ),
    }


# ---------------------------------------------------------------------------
# 9. stage_factor_weight.json
# ---------------------------------------------------------------------------
STAGE_FEATURES = ["n_patent_app", "n_patent_grant", "inv_patent_ratio", "tech_conversion",
                  "n_inventor", "n_ipc_subclass", "core_staff_stability",
                  "tech_breadth_entropy", "qual_rank"]


def build_stage_factor(con: sqlite3.Connection) -> dict:
    """按 ads_survival_period 的 period_t 分阶段统计特征与事件。

    这是**描述性**的阶段画像，不是「可解释组输出的因子权重模型」。
    """
    stages = []
    for t in sorted(r[0] for r in qa(con, "SELECT DISTINCT period_t FROM ads_survival_period")):
        row = con.execute(f"""
            SELECT COUNT(*), COUNT(DISTINCT enterprise_id), SUM(event), SUM(prospective)
            FROM ads_survival_period WHERE period_t=?
        """, (t,)).fetchone()
        feats = []
        for f in STAGE_FEATURES:
            if not has_obj(con, "ads_survival_period"):
                break
            if f not in cols_of(con, "ads_survival_period"):
                continue
            r = con.execute(f"""
                SELECT AVG("{f}"), MIN("{f}"), MAX("{f}"), COUNT("{f}")
                FROM ads_survival_period WHERE period_t=? AND "{f}" IS NOT NULL
            """, (t,)).fetchone()
            if r[0] is not None:
                feats.append({"feature": f, "mean": round(r[0], 4),
                              "min": round(r[1], 4), "max": round(r[2], 4), "n": r[3]})
        stages.append({
            "period_t": t, "n_rows": row[0], "n_enterprises": row[1],
            "n_event": row[2] or 0, "n_prospective": row[3] or 0,
            "event_rate": round((row[2] or 0) / row[0], 4) if row[0] else None,
            "feature_means": feats,
        })

    # 跨阶段变化幅度（相对第一期）
    deltas = []
    if len(stages) >= 2:
        base = {f["feature"]: f["mean"] for f in stages[0]["feature_means"]}
        last = {f["feature"]: f["mean"] for f in stages[-1]["feature_means"]}
        for f in base:
            if f in last and base[f]:
                deltas.append({
                    "feature": f,
                    "from_period": stages[0]["period_t"], "to_period": stages[-1]["period_t"],
                    "from_mean": base[f], "to_mean": last[f],
                    "pct_change": round((last[f] - base[f]) / abs(base[f]) * 100, 2),
                })
        deltas.sort(key=lambda d: -(d["pct_change"] or 0))

    return {
        "stages": stages,
        "stage_deltas": deltas,
        "capability": "descriptive_only",
        "missing": [
            {"item": "因子权重模型", "reason": "无可解释组产出的权重文件；缺口径的「阶段×因子」回归",
             "impact": "本接口只给描述统计，不能充当 SHAP/因子重要性"},
            {"item": "早期阶段事件", "reason": "21 个事件全部落在 period_t=3（末期），"
                                            "period_t=1/2 事件数为 0",
             "impact": "无法估计分阶段风险比，事件时间分布退化"},
        ],
        "caveat": "事件全部集中于末期，stage_deltas 只描述特征在观察窗内的均值变化，"
                  "**不能**解释为「该特征导致事件」",
        "provenance": prov(
            "partial",
            "ads_survival_period（1,479 行 / 493 家 / period_t∈{1,2,3}）",
            "按 period_t 分组算特征均值与事件率；跨期变化为末期均值对首期均值的相对变化",
            missing_dimensions=["分阶段因子权重模型", "非末期事件样本"],
        ),
    }


# ---------------------------------------------------------------------------
# 10. survival.json
# ---------------------------------------------------------------------------
def build_survival(con: sqlite3.Connection) -> dict:
    auc_path = os.path.join(OUT_DIR, "survival_model_auc.json")
    configs = []
    if os.path.exists(auc_path):
        with open(auc_path, encoding="utf-8") as f:
            auc = json.load(f)
        label_map = {
            "asof14": ("仅 as-of 特征（无外采）", "no_external"),
            "asof14+qcc18": ("as-of + 企查查 18 项外采", "with_external"),
            "perm_qcc": ("置换对照（标签打乱）", "permutation_control"),
        }
        for k, v in auc.items():
            if not isinstance(v, dict) or "auc_mean" not in v:
                continue
            cn, kind = label_map.get(k, (k, "other"))
            configs.append({
                "config": k, "label": cn, "kind": kind,
                "auc_mean": v.get("auc_mean"), "auc_std": v.get("auc_std"),
                "n_folds": v.get("n_folds"),
            })

    # 前瞻名单（真实落盘的 top 30）
    top = []
    csv_path = os.path.join(OUT_DIR, "survival_prospective_top.csv")
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i >= 30:
                    break
                top.append({
                    "rank": i + 1,
                    "enterprise_id": row.get("enterprise_id"),
                    "name": row.get("name_display"),
                    "first_year": row.get("first_year"),
                    "risk": round(float(row["risk"]), 4) if row.get("risk") else None,
                    "risk_pct": round(float(row["risk_pct"]), 1) if row.get("risk_pct") else None,
                    "is_renamed": str(row.get("is_renamed")) in ("1", "True", "true"),
                })

    # 期数结构
    periods = [{"period_t": r[0], "n_rows": r[1], "n_event": r[2]}
               for r in qa(con, """SELECT period_t, COUNT(*), SUM(event)
                                   FROM ads_survival_period GROUP BY 1 ORDER BY 1""")]

    best = max([c for c in configs if c["kind"] != "permutation_control"],
               key=lambda c: c["auc_mean"] or 0, default=None)
    perm = next((c for c in configs if c["kind"] == "permutation_control"), None)

    # 泄漏诊断：把「含外采 0.87」放回已被检验的泄漏实验里对照
    leakage = None
    leak_path = os.path.join(OUT_DIR, "baseline_asof_leakage.json")
    if os.path.exists(leak_path):
        with open(leak_path, encoding="utf-8") as f:
            lk = json.load(f)
        cfgs = lk.get("configurations") or {}
        leakage = {
            "n_samples": lk.get("n_samples"), "n_positive": lk.get("n_positive"),
            "protocol": (lk.get("protocol") or {}).get("cv"),
            "configs": [{
                "config": k,
                "n_features_used": v.get("n_features_used"),
                "lr_auc": ((v.get("logistic_regression") or {}).get("auc_mean")),
                "lr_auc_std": ((v.get("logistic_regression") or {}).get("auc_std")),
                "xgb_auc": ((v.get("xgboost") or {}).get("auc_mean")),
            } for k, v in cfgs.items()],
            "note": "leaky_full 与「含 qcc 外采」属同一量级；把同期资质/证书类字段带进特征"
                    "会把 AUC 抬到 0.85 上下，这部分增益不是续存预测能力",
        }

    return {
        "configs": configs,
        "periods": periods,
        "prospective_top": top,
        "leakage_diagnosis": leakage,
        "summary": {
            "n_configs": len(configs),
            "best_config": best["config"] if best else None,
            "best_auc": best["auc_mean"] if best else None,
            "permutation_floor": perm["auc_mean"] if perm else None,
            "n_prospective_listed": len(top),
        },
        "warnings": [
            "auc_std 在 0.085–0.140 之间，折间波动极大；n_folds=100 是重复交叉验证，"
            "不代表有 100 份独立样本",
            "prospective_top 的 risk 是进入模型的排序分，未经独立时间外验证，"
            "不得作为「预测会掉队」的结论",
            "标签只有 21 个正例，性能结论最多支撑方法论演示",
            "best_config（含 qcc 外采）AUC 0.87 已被泄漏实验解释：同期资质/证书类字段"
            "带来的是标签同源信息，不是预测力。对外只报 asof14（≈0.66）并在同一句里给 "
            "permutation floor（≈0.47）",
        ],
        "provenance": prov(
            "measured",
            "outputs/survival_model_auc.json + outputs/survival_prospective_top.csv "
            "+ outputs/baseline_asof_leakage.json + ads_survival_period",
            "直接透传已落盘的 100 折 AUC 与前瞻名单；期数结构从库里统计；"
            "泄漏诊断为透传既有对照实验，未新算",
            note="AUC 为已实测值，未重算、未调参；leakage_diagnosis 用于解释高 AUC 的来源",
        ),
    }


# ---------------------------------------------------------------------------
# 11. financing.json
# ---------------------------------------------------------------------------
def build_financing(con: sqlite3.Connection) -> dict:
    n_ev = q1(con, "SELECT COUNT(*) FROM fact_funding_event") or 0
    n_ent_funded = q1(con, "SELECT COUNT(DISTINCT enterprise_id) FROM fact_funding_event") or 0
    total_ent = q1(con, "SELECT COUNT(*) FROM dim_enterprise") or 0

    by_type = [{"event_type": r[0], "n": r[1], "amount_sum_cny": r[2]}
               for r in qa(con, """SELECT event_type, COUNT(*), SUM(amount_cny)
                                   FROM fact_funding_event GROUP BY 1 ORDER BY 2 DESC""")]
    by_year = [{"year": r[0], "n_events": r[1], "n_enterprises": r[2]}
               for r in qa(con, """SELECT event_year, COUNT(*), COUNT(DISTINCT enterprise_id)
                                   FROM fact_funding_event WHERE event_year IS NOT NULL
                                   GROUP BY 1 ORDER BY 1""")]
    rounds = [{"round_label": r[0], "n": r[1]}
              for r in qa(con, """SELECT round_label, COUNT(*) FROM fact_funding_event
                                  WHERE round_label IS NOT NULL AND round_label<>''
                                  GROUP BY 1 ORDER BY 2 DESC LIMIT 15""")]
    orgs = qa(con, """SELECT investor_names FROM fact_funding_event
                      WHERE investor_names IS NOT NULL AND investor_names<>''""")

    # 财务字段真实覆盖（这是"为什么不能做财务预测"的证据）
    fin_cov = {}
    if has_obj(con, "dws_growth_feature"):
        n = q1(con, "SELECT COUNT(*) FROM dws_growth_feature") or 0
        for c in ("latest_rev_wan", "latest_net_profit_wan", "latest_total_assets_wan",
                  "latest_tax_wan", "latest_emp_num", "rev_cagr", "emp_cagr",
                  "funding_net_increase_wan", "funding_rounds"):
            if c in cols_of(con, "dws_growth_feature"):
                k = q1(con, f'SELECT COUNT("{c}") FROM dws_growth_feature') or 0
                fin_cov[c] = {"n_available": k, "n_total": n,
                              "coverage": round(k / n, 4) if n else None}

    return {
        "capability": "descriptive_only",
        "funding_activity": {
            "n_events": n_ev,
            "n_enterprises_with_funding": n_ent_funded,
            "n_enterprises_total": total_ent,
            "funded_share": round(n_ent_funded / total_ent, 4) if total_ent else None,
            "year_range": [by_year[0]["year"], by_year[-1]["year"]] if by_year else None,
            "by_type": by_type, "by_year": by_year, "by_round": rounds,
            "n_distinct_investors_approx": len({x[0] for x in orgs}),
        },
        "financial_coverage": fin_cov,
        "can_predict": {
            "next_round": False,
            "revenue_forecast": False,
            "reason": (
                "融资事件只有 182/500 家有记录、时间跨度 2019–2026，且无「未融资企业」的"
                "对照口径；财务报表 latest_rev_wan 仅 12/500 有值，做不出营收序列。"
                "任何「融资概率/营收预测」都无法验证，故不产出预测值。"
            ),
        },
        "missing": [
            {"item": "连续年度财务报表", "reason": "dws_growth_feature 每家仅 1 行，"
                                                "latest_rev_wan 12/500", "impact": "营收/利润预测不可做"},
            {"item": "融资轮次时间序列", "reason": "182 家有记录，其余 318 家是「无记录」不是"
                                              "「确认未融资」，存在选择性缺失",
             "impact": "无法区分「没融过」与「没采到」"},
            {"item": "投后估值", "reason": "post_valuation 字段稀疏", "impact": "估值预测不可做"},
        ],
        "provenance": prov(
            "partial",
            "fact_funding_event（506 条 / 182 家）+ dws_growth_feature（500 行）",
            "融资活动为事实统计；financial_coverage 逐列算真实覆盖率；不产出任何预测值",
            missing_dimensions=["连续财务序列", "未融资对照口径", "投后估值"],
        ),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
BUILDERS = {
    "data_quality.json": ("data_quality", True),
    "panel_depth.json": ("panel_depth", True),
    "feature_tree.json": ("feature_tree", True),
    "graph_structure.json": ("graph_structure", True),
    "graph_slices.json": ("graph_slices", True),
    "graph_centrality.json": ("graph_centrality", True),
    "stage_factor_weight.json": ("stage_factor", True),
    "survival.json": ("survival", True),
    "financing.json": ("financing", True),
    "train_curve.json": ("train_curve", False),
    "roc_pr_ks.json": ("roc_pr_ks", False),
}

FUNCS = {
    "data_quality": build_data_quality,
    "panel_depth": build_panel_depth,
    "feature_tree": build_feature_tree,
    "graph_structure": build_graph_structure,
    "graph_slices": build_graph_slices,
    "graph_centrality": build_graph_centrality,
    "stage_factor": build_stage_factor,
    "survival": build_survival,
    "financing": build_financing,
    "train_curve": build_train_curve,
    "roc_pr_ks": build_roc_pr_ks,
}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成 12 个接口契约文件")
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    ap.add_argument("--only", nargs="*", help="只生成指定契约，如 data_quality graph_structure")
    args = ap.parse_args(argv)

    if not os.path.exists(DB_PATH):
        print(f"找不到数据库：{DB_PATH}", file=sys.stderr)
        return 1

    con = connect()
    print(f"数据库 {DB_PATH}")
    print(f"schema {_schema_version(con)}  |  输出 {CONTRACT_DIR}\n")

    wanted = {k: v for k, v in BUILDERS.items()
              if not args.only or v[0] in args.only}
    built, failed = 0, []
    try:
        for fname, (key, need_db) in wanted.items():
            fn = FUNCS[key]
            try:
                payload = fn(con) if need_db else fn()
                write(fname, payload, con if need_db else None, dry_run=args.check)
                built += 1
            except Exception as e:  # 单个失败不拖垮其余
                failed.append((fname, repr(e)))
                print(f"  ✗ {fname}: {e}")
    finally:
        con.close()

    print(f"\n完成 {built}/{len(wanted)}" + (f"，失败 {len(failed)}" if failed else ""))
    for f, e in failed:
        print(f"  - {f}: {e}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
