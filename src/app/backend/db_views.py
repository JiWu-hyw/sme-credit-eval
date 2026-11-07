"""只读数据库视图层：给 API 提供真实数据。

替代原来的 ``web/dashboard_data.json`` 过渡实现。那个文件从未生成，导致
``/api/score/company`` 直接 404、``/api/company/diagnose`` 返回空壳、
``/api/overview/summary`` 的 kpi 为 null。本模块把这些问题统一改为读库。

三条纪律：

1. **只读**：连接走 ``mode=ro``，任何调用都不可能改库。
2. **不造数**：拿不到的值一律留在 ``null`` 并在 ``unavailable`` 里写明原因，
   绝不填 0 或估算。
3. **代理分数必须自证**：``proxy_score`` 逐特征给出百分位、方向与权重，
   公式可从返回值本身复算；同时标注 ``validated=false``，禁止当模型分用。
"""

from __future__ import annotations

import bisect
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DB_PATH = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
REGISTRY_PATH = os.path.join(ROOT, "configs", "field_gap_registry.json")
LEGACY_LEDGER = os.path.join(ROOT, "configs", "data_gaps.mirror.json")
SHAP_PATH = os.path.join(ROOT, "outputs", "shap_global.json")
ASOF_FLAT_VIEW = "v_model_dataset_asof_flat"
TRAIN_VIEW = "v_model_train_ready"

LAYER_ORDER = ["META", "ODS", "DIM", "FACT", "BRIDGE", "DWS", "ADS", "EXT"]

FEATURE_CN = {
    "n_patent_app": "专利申请数",
    "n_patent_invention": "发明专利数",
    "n_patent_grant": "专利授权数",
    "inv_patent_ratio": "发明占比",
    "tech_conversion": "技术转化率",
    "n_ipc_subclass": "IPC 小类数",
    "tech_breadth_entropy": "技术广度熵",
    "tech_breadth_ipc_class": "技术广度（大类）",
    "grant_rate": "授权率（as-of）",
    "core_staff_stability": "核心人员稳定性",
    "n_inventor": "发明人数",
}


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------
def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """只读连接（uri mode=ro）。库不存在时抛 FileNotFoundError，由调用方转成业务码。"""
    p = db_path or DB_PATH
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    return con


def q1(con: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Any:
    row = con.execute(sql, params).fetchone()
    return None if row is None else row[0]


def has_object(con: sqlite3.Connection, name: str) -> bool:
    return q1(con, "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (name,)) > 0


def has_col(con: sqlite3.Connection, obj: str, col: str) -> bool:
    """先验列再统计。别用 COUNT("不存在列") —— SQLite 会把它当字符串字面量，
    返回总行数，覆盖率假性 100%（见 field_gap_registry.meta.traps[0]）。"""
    if not has_object(con, obj):
        return False
    return any(r[1] == col for r in con.execute('PRAGMA table_info("%s")' % obj))


def schema_version(con: sqlite3.Connection) -> str:
    """取库内实际最新版本号，而不是写死的常量。"""
    try:
        v = q1(con, "SELECT version FROM meta_schema_version "
                    "ORDER BY applied_at DESC, rowid DESC LIMIT 1")
        return v or "unknown"
    except sqlite3.Error:
        return "unknown"


def db_size_mb(path: Optional[str] = None) -> float:
    p = path or DB_PATH
    return round(os.path.getsize(p) / 1024 / 1024, 1) if os.path.exists(p) else 0.0


# ---------------------------------------------------------------------------
# 全局 KPI
# ---------------------------------------------------------------------------
def kpi(con: sqlite3.Connection) -> dict:
    """概览页 KPI。全部为库内实测，能在 SQL 里复算。"""
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    views = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")]
    n_cols = 0
    n_rows = 0
    for t in tables:
        n_cols += len(con.execute('PRAGMA table_info("%s")' % t).fetchall())
        n_rows += q1(con, 'SELECT COUNT(*) FROM "%s"' % t) or 0
    for v in views:
        n_cols += len(con.execute('PRAGMA table_info("%s")' % v).fetchall())

    n_ent = q1(con, "SELECT COUNT(*) FROM dim_enterprise")
    n_label = q1(con, "SELECT COUNT(*) FROM ads_label") if has_object(con, "ads_label") else 0
    n_pos = q1(con, "SELECT COALESCE(SUM(label_value),0) FROM ads_label") \
        if has_object(con, "ads_label") else 0
    if has_object(con, "v_patent_canonical"):
        # 「件数」按申请号去重；canonical 行数会更大，因为同一专利可能归到多个企业
        n_patent = q1(con, "SELECT COUNT(DISTINCT app_no) FROM v_patent_canonical")
        n_records = q1(con, "SELECT COUNT(*) FROM v_patent_canonical")
        n_granted = q1(con, "SELECT COUNT(DISTINCT app_no) FROM v_patent_canonical "
                            "WHERE is_granted_ever=1")
        n_inforce = q1(con, "SELECT COUNT(DISTINCT app_no) FROM v_patent_canonical "
                            "WHERE is_granted=1")
    else:
        n_patent = n_records = n_granted = n_inforce = None

    n_graph_n = q1(con, "SELECT COUNT(*) FROM ads_graph_node WHERE time_slice=0") \
        if has_object(con, "ads_graph_node") else 0
    n_graph_e = q1(con, "SELECT COUNT(*) FROM ads_graph_edge WHERE time_slice=0") \
        if has_object(con, "ads_graph_edge") else 0
    n_asof = q1(con, f"SELECT COUNT(*) FROM {ASOF_FLAT_VIEW}") \
        if has_object(con, ASOF_FLAT_VIEW) else 0
    n_train = q1(con, f"SELECT COUNT(*) FROM {TRAIN_VIEW}") \
        if has_object(con, TRAIN_VIEW) else 0

    return {
        "n_enterprise": n_ent,
        "n_labeled": n_label,
        "n_positive": n_pos or 0,
        "positive_rate": round(100.0 * (n_pos or 0) / n_label, 2) if n_label else None,
        "n_patent": n_patent,
        "n_patent_records": n_records,
        "n_patent_granted_ever": n_granted,
        "n_patent_in_force": n_inforce,
        "n_asof_rows": n_asof,
        "n_train_ready": n_train,
        "n_graph_nodes": n_graph_n,
        "n_graph_edges": n_graph_e,
        "n_tables": len(tables),
        "n_views": len(views),
        "n_columns": n_cols,
        "n_rows_total": n_rows,
        "db_size_mb": db_size_mb(),
        "schema_version": schema_version(con),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# 特征参考表（百分位基准）
# ---------------------------------------------------------------------------
_REF_CACHE: Optional[dict] = None


def _ref() -> dict:
    """参考集：v_model_train_ready（307 行）。给出每个特征的排序值、单变量 AUC。

    AUC 用 Mann-Whitney rank 法，并列取平均秩。方向语义：
    AUC = P(正例值 > 负例值)，正例=1 表示「到期未通过复核」。
    所以 AUC < 0.5 意味着「值越大越可能续存」。
    """
    global _REF_CACHE
    if _REF_CACHE is not None:
        return _REF_CACHE
    if not os.path.exists(DB_PATH):
        _REF_CACHE = {"ok": False, "reason": "库不存在", "features": {}}
        return _REF_CACHE
    con = connect()
    try:
        if not has_object(con, TRAIN_VIEW):
            _REF_CACHE = {"ok": False, "reason": "视图 %s 不存在" % TRAIN_VIEW,
                          "features": {}}
            return _REF_CACHE
        cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % TRAIN_VIEW)]
        rows = con.execute("SELECT %s, label FROM %s"
                           % (",".join('"%s"' % f for f in FEATURE_CN if f in cols),
                              TRAIN_VIEW)).fetchall()
        vals: dict[str, list] = {f: [] for f in FEATURE_CN if f in cols}
        pairs: dict[str, list] = {f: [] for f in FEATURE_CN if f in cols}
        n_pos = n_neg = 0
        for r in rows:
            lab = r["label"]
            if lab == 1:
                n_pos += 1
            elif lab == 0:
                n_neg += 1
            for f in vals:
                v = r[f]
                if v is not None:
                    vals[f].append(float(v))
                    if lab in (0, 1):
                        pairs[f].append((float(v), lab))
        feats = {}
        for f, arr in vals.items():
            arr.sort()
            auc = _rank_auc(pairs[f])
            feats[f] = {"sorted": arr, "n": len(arr), "auc": auc}
        _REF_CACHE = {"ok": True, "n": len(rows), "n_pos": n_pos, "n_neg": n_neg,
                      "features": feats}
        return _REF_CACHE
    finally:
        con.close()


def _rank_auc(pairs: list) -> Optional[float]:
    pos = [v for v, l in pairs if l == 1]
    neg = [v for v, l in pairs if l == 0]
    if not pos or not neg:
        return None
    order = sorted(pairs, key=lambda t: t[0])
    ranks, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
            j += 1
        ranks[order[i][0]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    rp = sum(ranks[v] for v in pos)
    return (rp - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def _pct(sorted_arr: list, v: float) -> float:
    """分位（0–1）：<=v 的比例，并列按中位处理。"""
    if not sorted_arr:
        return 0.0
    lo = bisect.bisect_left(sorted_arr, v)
    hi = bisect.bisect_right(sorted_arr, v)
    return round((lo + hi) / 2.0 / len(sorted_arr), 4)


def proxy_score(feats: dict, min_weight: float = 0.05) -> dict:
    """可复算的代理分数（0–100，越大越偏「续存」）。

    这不是模型分：权重取自单变量 AUC 与 0.5 的偏离，只反映「排名相关」，
    不控制变量间共线性，也没有任何交叉验证或校准。字段 ``validated`` 恒 false。
    """
    ref = _ref()
    if not ref.get("ok"):
        return {"value": None, "status": "REF_UNAVAILABLE", "reason": ref.get("reason"),
                "contributions": [], "validated": False}
    contribs, wsum, acc, skipped = [], 0.0, 0.0, []
    for f, cn in FEATURE_CN.items():
        spec = ref["features"].get(f)
        v = feats.get(f)
        if spec is None or v is None:
            skipped.append({"feature": f, "reason": "特征缺失"})
            continue
        auc = spec["auc"]
        if auc is None:
            skipped.append({"feature": f, "reason": "参考集无正负例"})
            continue
        w = abs(auc - 0.5)
        if w < min_weight:
            skipped.append({"feature": f, "reason": "|AUC-0.5|<%.2f，无判别力" % min_weight})
            continue
        p = _pct(spec["sorted"], float(v))
        direction = 1 if auc < 0.5 else -1     # +1: 值大→更可能续存
        aligned = p if direction > 0 else 1 - p
        wsum += w
        acc += w * aligned
        contribs.append({
            "feature": f, "cn": cn, "value": round(float(v), 4),
            "percentile": p, "auc": round(auc, 4), "weight": round(w, 4),
            "direction": direction,
            "direction_note": "值大→偏续存" if direction > 0 else "值大→偏掉队",
            "contribution": round(w * aligned, 4),
        })
    if not wsum:
        return {"value": None, "status": "NO_SIGNAL_FEATURE", "contributions": [],
                "skipped": skipped, "validated": False}
    score = round(100.0 * acc / wsum, 1)
    band = ("领先" if score >= 75 else "高于中位" if score >= 50
            else "低于中位" if score >= 25 else "落后")
    return {
        "value": score, "band": band, "validated": False,
        "status": "PROXY_RULE_BASED",
        "method": "特征分位对齐后按 |AUC-0.5| 加权平均；方向由单变量 AUC 决定",
        "reference": {"view": TRAIN_VIEW, "n": ref["n"],
                      "n_pos": ref["n_pos"], "n_neg": ref["n_neg"]},
        "contributions": sorted(contribs, key=lambda c: -c["contribution"]),
        "skipped": skipped,
        "caveat": "代理分只反映与该弱标签的单变量秩相关，未经交叉验证与校准，"
                  "不得作为信贷决策依据；正例仅 %d 个。" % ref["n_pos"],
    }


# ---------------------------------------------------------------------------
# 单企业快照
# ---------------------------------------------------------------------------
ENT_FIELDS = ["enterprise_id", "name_display", "credit_code", "region_code", "region_name",
              "enterprise_type", "first_qual_year", "last_qual_year", "max_qual_rank",
              "biz_status", "found_date", "found_years", "reg_capital", "insured_num",
              "is_listed", "is_renamed", "is_little_giant", "is_tech_sme",
              "qcc_score", "qcc_tech_level", "qcc_enterprise_scale", "qcc_credit_level",
              "qcc_industry_mid", "admin_penalty_cnt", "serious_dishonesty_cnt",
              "biz_anomaly_cnt", "honor_cnt", "cert_cnt"]


def company_snapshot(con: sqlite3.Connection, eid: str) -> Optional[dict]:
    """企业真实档案：维度 + 专利 + as-of 特征 + 标签 + 图谱连边。

    找不到返回 None（调用方转 NOT_FOUND 业务码，不要抛 HTTP 404）。
    注意企业 ID 是 ENT + 12 位 hex，共 15 字符；传 CN-330100-0001 之类的
    编码不会报错，只会查不到。
    """
    row = con.execute("SELECT * FROM dim_enterprise WHERE enterprise_id=?", (eid,)).fetchone()
    if row is None:
        # 兜底：按统一社会信用代码或名称精确匹配
        row = con.execute(
            "SELECT * FROM dim_enterprise WHERE credit_code=? OR name_display=? LIMIT 1",
            (eid, eid)).fetchone()
    if row is None:
        return None

    ent = {k: row[k] for k in ENT_FIELDS if k in row.keys()}

    pat = {"n_patent": None, "n_invention": None, "n_granted_ever": None,
           "n_in_force": None, "grant_rate_ever": None, "n_ipc_subclass": None}
    if has_object(con, "v_patent_canonical"):
        r = con.execute("""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN patent_type='invention' THEN 1 ELSE 0 END) AS n_inv,
                   SUM(COALESCE(is_granted_ever,0)) AS n_ever,
                   SUM(COALESCE(is_granted,0)) AS n_force,
                   COUNT(DISTINCT substr(ipc_main,1,4)) AS n_ipc
            FROM v_patent_canonical WHERE enterprise_id=?""", (eid,)).fetchone()
        if r and r["n"]:
            pat = {"n_patent": r["n"], "n_invention": r["n_inv"],
                   "n_granted_ever": r["n_ever"], "n_in_force": r["n_force"],
                   "grant_rate_ever": round((r["n_ever"] or 0) / r["n"], 4),
                   "n_ipc_subclass": r["n_ipc"]}

    feats, feat_src = {}, None
    if has_object(con, TRAIN_VIEW):
        r = con.execute("SELECT * FROM %s WHERE enterprise_id=?" % TRAIN_VIEW,
                        (eid,)).fetchone()
        if r is not None:
            feats = {f: r[f] for f in FEATURE_CN if f in r.keys()}
            feat_src = TRAIN_VIEW + "（as_of_policy=cohort）"
    if not feats and has_object(con, "ads_feature_asof"):
        rows = con.execute("""
            SELECT feature_name, feature_value FROM ads_feature_asof
            WHERE enterprise_id=? AND as_of_policy='cohort'
              AND feature_value IS NOT NULL
            ORDER BY as_of_year DESC""", (eid,)).fetchall()
        for r in rows:
            if r["feature_name"] in FEATURE_CN and r["feature_name"] not in feats:
                feats[r["feature_name"]] = r["feature_value"]
        if feats:
            feat_src = "ads_feature_asof（cohort）"

    label = None
    if has_object(con, "ads_label"):
        r = con.execute("""
            SELECT enterprise_id, cohort_year, label_value, label_name,
                   definition_ver, observation_start, observation_end, n_events, note
            FROM ads_label WHERE enterprise_id=?""", (eid,)).fetchone()
        if r is not None:
            label = dict(r)
            label["is_proxy"] = True

    graph = {"n_similar": 0, "top_similar": []}
    if has_object(con, "ads_graph_edge"):
        graph["n_similar"] = q1(con, """
            SELECT COUNT(*) FROM ads_graph_edge
            WHERE edge_type='tech_similar' AND time_slice=0
              AND (src_id=? OR dst_id=?)""", (eid, eid)) or 0
        graph["top_similar"] = [
            {"enterprise_id": r["other"], "name": r["name"], "weight": round(r["w"], 4)}
            for r in con.execute("""
                SELECT CASE WHEN e.src_id=? THEN e.dst_id ELSE e.src_id END AS other,
                       n.display_name AS name, e.weight AS w
                FROM ads_graph_edge e
                JOIN ads_graph_node n
                  ON n.node_type='enterprise' AND n.node_id=
                     (CASE WHEN e.src_id=? THEN e.dst_id ELSE e.src_id END)
                 AND n.time_slice=0
                WHERE e.edge_type='tech_similar' AND e.time_slice=0
                  AND (e.src_id=? OR e.dst_id=?)
                ORDER BY e.weight DESC LIMIT 5""", (eid, eid, eid, eid))]

    return {
        "enterprise_id": ent.get("enterprise_id"),
        "name": ent.get("name_display"),
        "profile": ent,
        "patent": pat,
        "features": {k: (round(float(v), 4) if isinstance(v, (int, float)) else v)
                     for k, v in feats.items()},
        "feature_source": feat_src,
        "label": label,
        "graph": graph,
    }


def ego_graph(con: sqlite3.Connection, eid: str, top_k: int = 10) -> dict:
    """以企业为中心的真实子图：中心节点 + Top-K 技术相似企业。

    边权为 IPC 小类 Jaccard（ETL 落库口径：jaccard>=0.10 且共享小类>=2）。
    只取 tech_similar，不掺入合成节点。
    """
    if not has_object(con, "ads_graph_edge"):
        return {"ready": False, "reason": "图谱未构建", "nodes": [], "edges": []}
    center = con.execute("""
        SELECT display_name FROM ads_graph_node
        WHERE node_type='enterprise' AND node_id=? AND time_slice=0""",
        (eid,)).fetchone()
    if center is None:
        return {"ready": False, "reason": "该企业不在图谱中（仅 500 家高企入图）",
                "nodes": [], "edges": []}
    rows = con.execute("""
        SELECT CASE WHEN e.src_id=? THEN e.dst_id ELSE e.src_id END AS other,
               n.display_name AS name, e.weight AS w
        FROM ads_graph_edge e
        JOIN ads_graph_node n
          ON n.node_type='enterprise' AND n.node_id=
             (CASE WHEN e.src_id=? THEN e.dst_id ELSE e.src_id END)
         AND n.time_slice=0
        WHERE e.edge_type='tech_similar' AND e.time_slice=0
          AND (e.src_id=? OR e.dst_id=?)
        ORDER BY e.weight DESC LIMIT ?""", (eid, eid, eid, eid, top_k)).fetchall()
    nodes = [{"id": eid, "name": center["display_name"], "role": "center"}]
    edges = []
    for r in rows:
        nodes.append({"id": r["other"], "name": r["name"], "role": "similar"})
        edges.append({"source": eid, "target": r["other"],
                      "weight": round(r["w"], 4), "type": "tech_similar"})
    return {"ready": True, "nodes": nodes, "edges": edges,
            "note": "边权=IPC 小类 Jaccard；仅 tech_similar，无合成节点"}


# ---------------------------------------------------------------------------
# 全局因子
# ---------------------------------------------------------------------------
def importance(con: sqlite3.Connection) -> dict:
    """两套数据的因子**分开返回**，不合并排序。

    primary = 本项目 500 家高企（弱标签，单变量 AUC 法，11 个可算特征）
    legacy  = 10 月信贷面板（x1–x200 匿名特征，XGBoost mean|SHAP|）
    这两套的样本、特征、标签完全不同，混在一起排序没有意义 —— 上一版
    ``/api/factors/importance`` 只返回 dashboard 的占位值，等于什么都没说。
    """
    ref = _ref()
    items = []
    if ref.get("ok"):
        for f, spec in ref["features"].items():
            auc = spec["auc"]
            if auc is None:
                continue
            items.append({
                "feature": f, "cn": FEATURE_CN.get(f, f),
                "auc": round(auc, 4), "abs_dev": round(abs(auc - 0.5), 4),
                "direction": 1 if auc < 0.5 else -1,
                "direction_note": "值大→偏续存" if auc < 0.5 else "值大→偏掉队",
                "coverage": "%d/%d" % (spec["n"], ref["n"]),
            })
        items.sort(key=lambda x: -x["abs_dev"])

    legacy = None
    if os.path.exists(SHAP_PATH):
        with open(SHAP_PATH, encoding="utf-8") as fh:
            shap = json.load(fh)
        legacy = {
            "dataset": "A：10 月信贷面板（1,714 家 / 匿名特征 x1–x200）",
            "method": "XGBoost mean|SHAP|（holdout）",
            "explained_set": shap.get("explain_set"),
            "n_features_used": shap.get("n_features_used"),
            "items": shap.get("xgb_top20", [])[:10],
        }

    return {
        "primary": {
            "dataset": "B：浙江 500 家高企（弱标签，正例 %d）"
                       % (ref.get("n_pos") or 0),
            "method": "单变量 AUC（Mann-Whitney rank，并列取平均秩）",
            "n_samples": ref.get("n"),
            "n_positive": ref.get("n_pos"),
            "items": items,
        },
        "legacy": legacy,
        "note": "primary 与 legacy 为两套互不相干的数据，禁止合并排序或跨表引用"
                "（样本量、特征命名、标签定义均不同）。",
    }


# ---------------------------------------------------------------------------
# 数据管道
# ---------------------------------------------------------------------------
def _layer_of(table: str) -> str:
    t = table.lower()
    for p in ("meta", "ods", "dim", "fact", "bridge", "dws", "ads", "ext"):
        if t.startswith(p + "_"):
            return p.upper()
    return "OTHER"


def pipeline(con: sqlite3.Connection) -> dict:
    """实时管道状态 + 当前缺口登记册 + 10 月旧台账（带矛盾检测）。

    旧台账 ``configs/data_gaps.mirror.json`` 写于 10 月，其中 P0-4 的说明
    仍是「技术要素维度完全缺失」，而库里已有 3 万件专利。这里逐条核对，
    被库内事实推翻的标 ``contradicted=true`` 并附实测值，避免前端照旧文案
    画「专利缺失」的缺口图。
    """
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    stages = {}
    for t in tables:
        lay = _layer_of(t)
        st = stages.setdefault(lay, {"layer": lay, "tables": 0, "rows": 0})
        st["tables"] += 1
        st["rows"] += q1(con, 'SELECT COUNT(*) FROM "%s"' % t) or 0
    ordered = sorted(stages.values(),
                     key=lambda s: LAYER_ORDER.index(s["layer"])
                     if s["layer"] in LAYER_ORDER else 99)

    registry = None
    if os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, encoding="utf-8") as fh:
            reg = json.load(fh)
        by_status: dict[str, int] = {}
        for it in reg.get("items", []):
            by_status[it.get("status", "unknown")] = \
                by_status.get(it.get("status", "unknown"), 0) + 1
        registry = {
            "version": reg.get("meta", {}).get("version"),
            "updated_at": reg.get("meta", {}).get("updated_at"),
            "n_items": len(reg.get("items", [])),
            "by_status": by_status,
            "n_substitutes": len(reg.get("substitutes", [])),
            "items": reg.get("items", []),
            "source": "configs/field_gap_registry.json",
        }

    k = kpi(con)
    legacy = None
    if os.path.exists(LEGACY_LEDGER):
        with open(LEGACY_LEDGER, encoding="utf-8") as fh:
            old = json.load(fh)
        checks = {
            "P0-4": (k.get("n_patent") or 0) > 0,
        }
        for g in old.get("gaps", []):
            if g.get("id") in checks:
                g["contradicted"] = checks[g["id"]]
                g["live_evidence"] = {
                    "metric": "v_patent_canonical 行数（按申请号去重后）",
                    "value": k.get("n_patent"),
                }
                if checks[g["id"]]:
                    g["live_note"] = ("说明已过期：库里已有 %s 件专利记录，"
                                      "但 why 仍写「完全缺失」" % f"{k.get('n_patent'):,}")
                    g["status"] = "ready"
        legacy = {
            "deprecated": True,
            "version": old.get("version"),
            "updated_at": old.get("updated_at"),
            "why_deprecated": "10 月维护的静态台账，state 与 why 均可能过期；"
                              "现以 registry(live) 为准，本块仅留档",
            "gaps": old.get("gaps", []),
            "gap_summary": old.get("gap_summary"),
        }

    return {
        "live": {
            "generated_at": k["generated_at"],
            "schema_version": k["schema_version"],
            "n_tables": k["n_tables"], "n_views": k["n_views"],
            "n_columns": k["n_columns"], "n_rows": k["n_rows_total"],
            "stages": ordered,
        },
        "registry": registry,
        "legacy_ledger": legacy,
        "meta": {"source": "sqlite + configs/field_gap_registry.json",
                 "note": "以 live/registry 为准；legacy_ledger 仅供追溯"},
    }


# ---------------------------------------------------------------------------
# 批量
# ---------------------------------------------------------------------------
def batch_rows(con: sqlite3.Connection, eids: Optional[Sequence[str]] = None) -> list:
    """对给定企业批量算代理分；eids 为空则跑全库 500 家。

    走三条批量查询（维度 / 特征 / 标签）而不是逐家 ``company_snapshot``：
    后者每家企业要跑 4 次 SQL（含图谱连边），500 家会把接口拖到超时。
    """
    ids = [str(e) for e in eids] if eids else \
        [r[0] for r in con.execute(
            "SELECT enterprise_id FROM dim_enterprise ORDER BY enterprise_id")]
    if not ids:
        return []

    names: dict[str, Any] = {}
    for r in con.execute("SELECT enterprise_id, name_display FROM dim_enterprise"):
        names[r["enterprise_id"]] = r["name_display"]

    feats: dict[str, dict] = {}
    if has_object(con, TRAIN_VIEW):
        for r in con.execute("SELECT * FROM %s" % TRAIN_VIEW):
            eid = r["enterprise_id"]
            if eid in names:
                feats[eid] = {f: r[f] for f in FEATURE_CN if f in r.keys()}
    if has_object(con, "ads_feature_asof"):
        # 无 cohort 行的企业（194 家）退回 as_of 长表里最新一年的取值
        for r in con.execute("""
                SELECT enterprise_id, feature_name, feature_value
                FROM ads_feature_asof
                WHERE as_of_policy='cohort' AND feature_value IS NOT NULL
                ORDER BY enterprise_id, as_of_year"""):
            eid = r["enterprise_id"]
            if eid in feats or eid not in names:
                continue
            if r["feature_name"] in FEATURE_CN:
                feats.setdefault(eid, {})[r["feature_name"]] = r["feature_value"]

    labels: dict[str, Any] = {}
    if has_object(con, "ads_label"):
        for r in con.execute("SELECT enterprise_id, label_value, cohort_year FROM ads_label"):
            labels[r["enterprise_id"]] = (r["label_value"], r["cohort_year"])

    out = []
    for eid in ids:
        if eid not in names:
            out.append({"enterprise_id": eid, "name": None, "status": "NOT_FOUND",
                        "proxy_score": None, "band": None, "label": None,
                        "cohort_year": None, "n_patent_app": None,
                        "n_patent_grant": None})
            continue
        f = feats.get(eid, {})
        sc = proxy_score(f)
        lab, cohort = labels.get(eid, (None, None))
        out.append({
            "enterprise_id": eid, "name": names[eid], "status": "ok",
            "proxy_score": sc.get("value"), "band": sc.get("band"),
            "label": lab, "cohort_year": cohort,
            "n_patent_app": f.get("n_patent_app"),
            "n_patent_grant": f.get("n_patent_grant"),
        })
    return out
