"""信用评估原型系统后端 API（FastAPI 骨架）。

对齐《后端完整答复 v2.0》§3.1（21 接口 + 统一响应包）与《后端工作总览 v1.1》§6（双模式）。

设计原则：
- 统一响应包：``{code, message, data, meta}``；``code=0`` 成功，非 0 为业务/阻塞码
  （``CONTRACT_PENDING`` / ``LABEL_PENDING`` / ``BATCH_RUNNING`` 等）；HTTP 状态码与业务码分离。
- 就绪的契约文件直接读并透传；未就绪的返回 CONTRACT_PENDING（待算法/数据脚本产出）。
- 只读 json + sqlite（原型期，零重型 ORM）。

启动：``uvicorn src.app.api:app`` 或 ``python run_api.py``。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Union

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(ROOT, "data", "db", "zjtech_credit.db")

# 契约版本锚点（《完整答复》§3.3.4，前端启动断言）
VERSION_META = {
    "contract_ver": "v2.0",
    "enums_version": "v1.0",
    "schema_version": "v1.0",
}

# 噪声地板（置换对照 AUC 行级：LR 0.492 / XGB 0.477，取较低者作最保守零信号基线）
NOISE_FLOOR = 0.477
NOISE_FLOOR_NOTE = "置换对照 AUC（行级）LR 0.492 / XGB 0.477，取较低者 0.477 为保守零信号基线"

# 未就绪接口 → 依赖的契约文件（用于返回清晰提示）
PENDING_CONTRACT = {
    "/api/data/quality": "data_quality.json",
    "/api/data/panel-depth": "panel_depth.json",
    "/api/features/tree": "feature_tree.json (configs/feature_dict.yaml)",
    "/api/graph/structure": "graph_structure.json",
    "/api/graph/slices": "graph_slices.json",
    "/api/graph/centrality": "graph_centrality.json",
    "/api/model/train-curve": "train_curve.json",
    "/api/model/roc-pr-ks": "roc_pr_ks.json",
    "/api/explain/stage-factor": "stage_factor_weight.json",
    "/api/explain/local": "local_explain_{userid}.json (依赖 P0-1 映射键)",
    "/api/forecast/survival": "survival.json",
    "/api/forecast/financing": "financing.json",
}


# ---------------------------------------------------------------------------
# 统一响应包
# ---------------------------------------------------------------------------
class ApiResponse(BaseModel):
    code: Union[int, str]
    message: str
    data: Optional[Any] = None
    meta: Optional[dict] = None


def _meta(extra: Optional[dict] = None) -> dict:
    """组装统一 meta（含版本锚点）。"""
    m = dict(VERSION_META)
    if extra:
        m.update(extra)
    return m


def ok(data: Any = None, message: str = "ok", meta: Optional[dict] = None) -> dict:
    return {"code": 0, "message": message, "data": data, "meta": _meta(meta)}


def pending(code: str, message: str, detail: Optional[str] = None) -> dict:
    """业务/阻塞码：HTTP 仍 200，成功与否看 code。"""
    return {"code": code, "message": message, "data": None,
            "meta": _meta({"detail": detail} if detail else None)}


# ---------------------------------------------------------------------------
# 数据文件惰性加载（只读，原型期直接读 json）
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {}


def _load(name: str, relpath: str) -> Optional[Any]:
    if name not in _cache:
        p = os.path.join(ROOT, relpath)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                _cache[name] = json.load(f)
        else:
            _cache[name] = None
    return _cache[name]


def _dashboard() -> Optional[dict]:
    return _load("dashboard", os.path.join("web", "dashboard_data.json"))


def _candidate_ids(raw_id: str) -> set[str]:
    """把查询 id 展开为候选形式，兼容旧版 int id（0/1/2…）与新版 company_{i} 字符串。"""
    s = str(raw_id)
    out = {s, f"company_{s}"}
    if s.startswith("company_"):
        out.add(s[len("company_"):])
    return out


def _find_company(raw_id: str) -> Optional[dict]:
    dash = _dashboard()
    if not dash:
        return None
    keys = _candidate_ids(raw_id)
    for c in dash.get("companies", []):
        cid = str(c.get("id"))
        if cid in keys or f"company_{cid}" in keys:
            return c
    return None


def _explanations() -> dict:
    dash = _dashboard()
    if not dash:
        return {}
    return dash.get("explanations", {}) or {}


def _find_explanation(raw_id: str) -> Optional[dict]:
    exp = _explanations()
    for k in _candidate_ids(raw_id):
        if k in exp:
            return exp[k]
    return None


# ---------------------------------------------------------------------------
# 应用与中间件
# ---------------------------------------------------------------------------
app = FastAPI(
    title="中小科技企业信用智能评估 · 后端 API",
    description="双模式（批量评估 / 单企业深度诊断）原型系统 API 骨架。",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期放开，生产走反向代理收紧
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return ok(
        data={"name": "信用评估后端 API", "docs": "/docs", "health": "/health",
              "contract_ver": VERSION_META["contract_ver"]},
        message="credit-eval backend api",
    )


@app.get("/health")
def health():
    return ok(data={"status": "ok", "db_exists": os.path.exists(DB_PATH),
                    "dashboard_ready": _dashboard() is not None,
                    "time": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# 就绪接口（真实读契约文件 / 过渡读 dashboard_data.json）
# ---------------------------------------------------------------------------
@app.get("/api/overview/summary")
def overview_summary():
    """#1 概览：聚合各契约 meta + 数据缺口 + KPI。"""
    dash = _dashboard()
    mirror = _load("gaps", os.path.join("configs", "data_gaps.mirror.json"))
    data = {
        "contract": dict(VERSION_META),
        "kpi": (dash or {}).get("meta"),
        "data_gaps": (mirror or {}).get("gap_summary"),
        "ready_endpoints": [
            "/api/overview/summary", "/api/data/pipeline", "/api/model/benchmark",
            "/api/model/noise-floor", "/api/explain/global", "/api/enums",
            "/api/factors/importance", "/api/score/company/{id}",
            "/api/company/{id}/graph", "/api/company/{id}/explanation",
            "/api/tech-graph/summary", "/api/tech-graph/enterprises",
            "/api/tech-graph/enterprise/{id}/profile",
            "/api/tech-graph/enterprise/{id}/neighborhood",
        ],
        "pending_endpoints": sorted(PENDING_CONTRACT.keys()),
    }
    return ok(data=data)


@app.get("/api/data/pipeline")
def data_pipeline():
    """#2 数据管道与缺口台账（读 data_gaps.mirror.json）。"""
    mirror = _load("gaps", os.path.join("configs", "data_gaps.mirror.json"))
    if not mirror:
        return pending("CONTRACT_PENDING", "data_gaps.mirror.json 未生成")
    return ok(data=mirror, meta={"source": mirror.get("meta")})


@app.get("/api/model/benchmark")
def model_benchmark():
    """#10 基准对比实验（读 outputs/baseline_results.json）。"""
    bench = _load("benchmark", os.path.join("outputs", "baseline_results.json"))
    if not bench:
        return pending("CONTRACT_PENDING", "baseline_results.json 未生成")
    return ok(data=bench, meta={"noise_floor": NOISE_FLOOR})


@app.get("/api/model/noise-floor")
def model_noise_floor():
    """#12 噪声地板（置换对照）。"""
    return ok(data={
        "noise_floor": NOISE_FLOOR,
        "permutation_control": {"logistic_regression": 0.492, "xgboost": 0.477},
        "note": NOISE_FLOOR_NOTE,
    })


@app.get("/api/explain/global")
def explain_global():
    """#13 全局特征重要性（读 outputs/shap_global.json，已就绪）。"""
    shap = _load("shap", os.path.join("outputs", "shap_global.json"))
    if not shap:
        return pending("CONTRACT_PENDING", "shap_global.json 未生成")
    return ok(data=shap)


@app.get("/api/enums")
def enums():
    """实体枚举中文（方案 B+，读 configs/enums.json）。"""
    e = _load("enums", os.path.join("configs", "enums.json"))
    if not e:
        return pending("CONTRACT_PENDING", "enums.json 未生成")
    return ok(data=e, meta={"enums_version": e.get("version", VERSION_META["enums_version"])})


@app.get("/api/factors/importance")
def factors_importance():
    """核心影响因子（全局，过渡读 dashboard_data.json.importance）。"""
    dash = _dashboard()
    if not dash:
        return pending("CONTRACT_PENDING", "dashboard_data.json 未生成")
    return ok(data=dash.get("importance", []))


@app.get("/api/score/company/{company_id}")
def score_company(company_id: str):
    """单企业评分查询（过渡读 dashboard_data.json.companies）。"""
    comp = _find_company(company_id)
    if not comp:
        raise HTTPException(status_code=404, detail=f"企业 {company_id} 不存在")
    return ok(data=comp)


@app.get("/api/company/{company_id}/graph")
def company_graph(company_id: str):
    """单企业因子可视化力导向图（过渡：返回 dashboard graph 全量子图）。"""
    dash = _dashboard()
    if not dash:
        return pending("CONTRACT_PENDING", "dashboard_data.json 未生成")
    return ok(data=dash.get("graph"), meta={"note": "过渡版：返回 demo 样例子图，未按 company_id 过滤"})


@app.get("/api/company/{company_id}/explanation")
def company_explanation(company_id: str):
    """单企业局部解释（过渡读 dashboard_data.json.explanations）。"""
    exp = _find_explanation(company_id)
    if exp is None:
        return pending("CONTRACT_PENDING", f"企业 {company_id} 局部解释未生成",
                       detail="依赖 local_explain.py 产出")
    return ok(data=exp)


@app.post("/api/company/diagnose")
def company_diagnose(company_id: str):
    """单企业深度诊断（§6 模式二）：聚合评分 + 图谱 + 局部解释。"""
    comp = _find_company(company_id)
    exp = _find_explanation(company_id)
    dash = _dashboard()
    return ok(data={
        "company_id": company_id,
        "score": comp,
        "explanation": exp,
        "graph": (dash or {}).get("graph"),
        "note": "过渡版：图谱为 demo 样例，未按 company_id 过滤；真实版依赖 P0-1 映射键",
    })


# ---------------------------------------------------------------------------
# 技术相似图谱接口（真实读 ads_graph_*，非契约文件透传）
#
# 与《完整答复》里的「时空图谱」/api/graph/* 契约接口互不冲突，独立前缀
# /api/tech-graph。数据由统一 ETL（load_500family.build_tech_graph）落库：
#   enterprise / patent / ipc 三类节点 + files_patent / belongs_ipc /
#   tech_similar 三类边（企业间相似度 = IPC 小类集合 Jaccard）。
#
# 全部返回 code=0 的统一信封；图谱未构建时 data.ready=false + 空集合，
# 前端可据此渲染「待建图」态，不会因未知业务码而崩溃。
# ---------------------------------------------------------------------------
GRAPH_META = {"source": "sqlite:ads_graph_node/ads_graph_edge", "time_slice": 0}


def _graph_connect():
    """只读连接图谱库（按列名取值）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _graph_state(conn):
    """返回 (ready, reason)：图谱节点表存在且有数据才视为就绪。"""
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM ads_graph_node WHERE time_slice=0").fetchone()[0]
        if n > 0:
            return True, ""
        return False, "图谱未构建：请运行 python -m src.data.load_500family"
    except sqlite3.Error as e:
        return False, "ads_graph_* 表不可用：%s" % e


def _graph_not_ready(reason: str) -> dict:
    return ok(data={"ready": False, "reason": reason},
              message="tech graph not ready", meta=GRAPH_META)


@app.get("/api/tech-graph/summary")
def tech_graph_summary():
    """图谱总览：节点/边按类型计数、参与企业数、相似度 Top10 企业对。"""
    if not os.path.exists(DB_PATH):
        return _graph_not_ready("数据库不存在：%s" % DB_PATH)
    conn = _graph_connect()
    try:
        ready, reason = _graph_state(conn)
        if not ready:
            return _graph_not_ready(reason)
        nodes = {r[0]: r[1] for r in conn.execute(
            "SELECT node_type, COUNT(*) FROM ads_graph_node "
            "WHERE time_slice=0 GROUP BY node_type")}
        edges = {r[0]: r[1] for r in conn.execute(
            "SELECT edge_type, COUNT(*) FROM ads_graph_edge "
            "WHERE time_slice=0 GROUP BY edge_type")}
        top_pairs = [dict(r) for r in conn.execute("""
            SELECT e.src_id, a.display_name AS src_name,
                   e.dst_id, b.display_name AS dst_name, e.weight
            FROM ads_graph_edge e
            JOIN ads_graph_node a
              ON a.node_type='enterprise' AND a.node_id=e.src_id AND a.time_slice=0
            JOIN ads_graph_node b
              ON b.node_type='enterprise' AND b.node_id=e.dst_id AND b.time_slice=0
            WHERE e.edge_type='tech_similar' AND e.time_slice=0
            ORDER BY e.weight DESC, e.src_id LIMIT 10
        """)]
        n_part = conn.execute(
            "SELECT COUNT(*) FROM ("
            " SELECT src_id AS eid FROM ads_graph_edge "
            "  WHERE edge_type='tech_similar' AND time_slice=0"
            " UNION"
            " SELECT dst_id AS eid FROM ads_graph_edge "
            "  WHERE edge_type='tech_similar' AND time_slice=0)").fetchone()[0]
        return ok(data={
            "ready": True,
            "n_nodes": sum(nodes.values()),
            "n_edges": sum(edges.values()),
            "nodes_by_type": nodes,
            "edges_by_type": edges,
            "n_similar_pairs": edges.get("tech_similar", 0),
            "n_enterprise_participated": n_part,
            "jaccard_min": 0.10,
            "shared_min": 2,
            "top_pairs": top_pairs,
        }, meta=GRAPH_META)
    finally:
        conn.close()


@app.get("/api/tech-graph/enterprises")
def tech_graph_enterprises(q: str = "", limit: int = 50):
    """企业检索（供前端下拉选择）：可按名称模糊匹配，按相似连边数降序。"""
    limit = max(1, min(int(limit), 200))
    if not os.path.exists(DB_PATH):
        return _graph_not_ready("数据库不存在：%s" % DB_PATH)
    conn = _graph_connect()
    try:
        ready, reason = _graph_state(conn)
        if not ready:
            return _graph_not_ready(reason)
        items = [dict(r) for r in conn.execute("""
            SELECT n.node_id AS enterprise_id, n.display_name AS name,
                   (SELECT COUNT(*) FROM ads_graph_edge x
                     WHERE x.edge_type='tech_similar' AND x.time_slice=0
                       AND (x.src_id=n.node_id OR x.dst_id=n.node_id)) AS similar_cnt,
                   (SELECT COUNT(*) FROM ads_graph_edge f
                     WHERE f.edge_type='files_patent' AND f.time_slice=0
                       AND f.src_id=n.node_id) AS patent_cnt
            FROM ads_graph_node n
            WHERE n.node_type='enterprise' AND n.time_slice=0
              AND (?='' OR n.display_name LIKE '%'||?||'%')
            ORDER BY similar_cnt DESC, n.node_id
            LIMIT ?
        """, (q, q, limit))]
        return ok(data={"ready": True, "q": q, "total": len(items), "items": items},
                  meta=GRAPH_META)
    finally:
        conn.close()


@app.get("/api/tech-graph/enterprise/{enterprise_id}/profile")
def tech_graph_profile(enterprise_id: str):
    """某企业技术画像：专利数、相似邻居数、IPC 小类 Top10。"""
    if not os.path.exists(DB_PATH):
        return _graph_not_ready("数据库不存在：%s" % DB_PATH)
    conn = _graph_connect()
    try:
        ready, reason = _graph_state(conn)
        if not ready:
            return _graph_not_ready(reason)
        center = conn.execute(
            "SELECT display_name FROM ads_graph_node "
            "WHERE node_type='enterprise' AND node_id=? AND time_slice=0",
            (enterprise_id,)).fetchone()
        if center is None:
            return ok(data={"ready": True, "exists": False,
                            "enterprise_id": enterprise_id, "name": None,
                            "patent_cnt": 0, "similar_cnt": 0, "ipc_top": []},
                      meta=GRAPH_META)
        patent_cnt = conn.execute(
            "SELECT COUNT(*) FROM ads_graph_edge "
            "WHERE edge_type='files_patent' AND time_slice=0 AND src_id=?",
            (enterprise_id,)).fetchone()[0]
        similar_cnt = conn.execute(
            "SELECT COUNT(*) FROM ads_graph_edge "
            "WHERE edge_type='tech_similar' AND time_slice=0 "
            "  AND (src_id=? OR dst_id=?)",
            (enterprise_id, enterprise_id)).fetchone()[0]
        ipc_top = [dict(r) for r in conn.execute("""
            SELECT b.ipc_subclass AS subclass, MAX(d.title) AS title,
                   COUNT(DISTINCT b.patent_id) AS patent_cnt
            FROM fact_patent p
            JOIN bridge_patent_ipc b ON b.patent_id=p.patent_id
            LEFT JOIN dim_ipc d ON d.ipc_subclass=b.ipc_subclass
            WHERE p.enterprise_id=? AND b.ipc_subclass IS NOT NULL
              AND b.ipc_subclass<>''
            GROUP BY b.ipc_subclass
            ORDER BY patent_cnt DESC, b.ipc_subclass LIMIT 10
        """, (enterprise_id,))]
        return ok(data={"ready": True, "exists": True,
                        "enterprise_id": enterprise_id, "name": center[0],
                        "patent_cnt": patent_cnt, "similar_cnt": similar_cnt,
                        "ipc_top": ipc_top}, meta=GRAPH_META)
    finally:
        conn.close()


@app.get("/api/tech-graph/enterprise/{enterprise_id}/neighborhood")
def tech_graph_neighborhood(enterprise_id: str, top_k: int = 10):
    """技术相似子图：中心企业 + TopK 相似邻居 + 连边（ECharts graph 可直接消费）。

    tech_similar 边按 (a<b) 只存单向，故邻居查询用 UNION ALL 补双向。
    """
    top_k = max(1, min(int(top_k), 50))
    if not os.path.exists(DB_PATH):
        return _graph_not_ready("数据库不存在：%s" % DB_PATH)
    conn = _graph_connect()
    try:
        ready, reason = _graph_state(conn)
        if not ready:
            return _graph_not_ready(reason)
        center = conn.execute(
            "SELECT display_name FROM ads_graph_node "
            "WHERE node_type='enterprise' AND node_id=? AND time_slice=0",
            (enterprise_id,)).fetchone()
        center_name = center[0] if center else enterprise_id
        neighbors = [dict(r) for r in conn.execute("""
            SELECT t.other_id, t.weight, n.display_name AS name
            FROM (
              SELECT dst_id AS other_id, weight FROM ads_graph_edge
               WHERE edge_type='tech_similar' AND time_slice=0 AND src_id=?
              UNION ALL
              SELECT src_id AS other_id, weight FROM ads_graph_edge
               WHERE edge_type='tech_similar' AND time_slice=0 AND dst_id=?
            ) t
            LEFT JOIN ads_graph_node n
              ON n.node_type='enterprise' AND n.node_id=t.other_id AND n.time_slice=0
            ORDER BY t.weight DESC, t.other_id
            LIMIT ?
        """, (enterprise_id, enterprise_id, top_k))]
        nodes = [{"id": enterprise_id, "name": center_name, "category": 0,
                  "symbolSize": 32, "value": len(neighbors)}]
        links = []
        for nb in neighbors:
            nodes.append({
                "id": nb["other_id"], "name": nb["name"] or nb["other_id"],
                "category": 1, "symbolSize": round(8 + 24 * nb["weight"], 1),
                "value": nb["weight"],
            })
            links.append({"source": enterprise_id, "target": nb["other_id"],
                          "weight": nb["weight"], "value": nb["weight"]})
        return ok(data={
            "ready": True, "exists": center is not None,
            "enterprise_id": enterprise_id, "name": center_name, "top_k": top_k,
            "categories": [{"name": "中心企业"}, {"name": "技术相似企业"}],
            "nodes": nodes, "links": links,
            "neighbors": [{"enterprise_id": nb["other_id"], "name": nb["name"],
                           "weight": nb["weight"]} for nb in neighbors],
            "n_nodes": len(nodes), "n_links": len(links),
        }, meta=GRAPH_META)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 未就绪接口（统一 CONTRACT_PENDING）
# ---------------------------------------------------------------------------
@app.get("/api/data/quality")
def data_quality():
    return pending("CONTRACT_PENDING", "契约文件待数据管道脚本产出",
                   detail=PENDING_CONTRACT["/api/data/quality"])


@app.get("/api/data/panel-depth")
def data_panel_depth():
    return pending("CONTRACT_PENDING", "契约文件待数据管道脚本产出",
                   detail=PENDING_CONTRACT["/api/data/panel-depth"])


@app.get("/api/features/tree")
def features_tree():
    return pending("CONTRACT_PENDING", "契约文件待特征工程产出",
                   detail=PENDING_CONTRACT["/api/features/tree"])


@app.get("/api/graph/structure")
def graph_structure():
    return pending("CONTRACT_PENDING", "契约文件待图谱组产出",
                   detail=PENDING_CONTRACT["/api/graph/structure"])


@app.get("/api/graph/slices")
def graph_slices():
    return pending("CONTRACT_PENDING", "契约文件待图谱组产出",
                   detail=PENDING_CONTRACT["/api/graph/slices"])


@app.get("/api/graph/centrality")
def graph_centrality():
    return pending("CONTRACT_PENDING", "契约文件待图谱组产出",
                   detail=PENDING_CONTRACT["/api/graph/centrality"])


@app.get("/api/model/train-curve")
def model_train_curve():
    return pending("CONTRACT_PENDING", "契约文件待模型组产出",
                   detail=PENDING_CONTRACT["/api/model/train-curve"])


@app.get("/api/model/roc-pr-ks")
def model_roc_pr_ks():
    return pending("CONTRACT_PENDING", "契约文件待模型组产出",
                   detail=PENDING_CONTRACT["/api/model/roc-pr-ks"])


@app.get("/api/explain/stage-factor")
def explain_stage_factor():
    return pending("CONTRACT_PENDING", "契约文件待可解释组产出",
                   detail=PENDING_CONTRACT["/api/explain/stage-factor"])


@app.get("/api/explain/local/{userid}")
def explain_local(userid: str):
    return pending("CONTRACT_PENDING", "单企业局部解释未生成",
                   detail=PENDING_CONTRACT["/api/explain/local"])


@app.get("/api/forecast/survival")
def forecast_survival():
    return pending("CONTRACT_PENDING", "契约文件待预测组产出",
                   detail=PENDING_CONTRACT["/api/forecast/survival"])


@app.get("/api/forecast/financing")
def forecast_financing():
    return pending("CONTRACT_PENDING", "契约文件待预测组产出",
                   detail=PENDING_CONTRACT["/api/forecast/financing"])


# ---------------------------------------------------------------------------
# LABEL_PENDING（阻塞于 P0-3 label 口径）
# ---------------------------------------------------------------------------
@app.get("/api/enterprise/{userid}")
def enterprise(userid: str):
    """#16 企业档案 + 信用评分仪表盘。阻塞于 P0-3 label 口径未确认。"""
    return pending("LABEL_PENDING",
                   "P0-3 label 口径未确认，企业评分/风险分档暂不对外",
                   detail="先与指导教师明确 label 口径（逾期天数/观察窗），再落 configs/label_spec.yaml")


# ---------------------------------------------------------------------------
# 批量任务骨架（§6 模式一；落库复用 collect_pool 既有逻辑）
# ---------------------------------------------------------------------------
_JOBS: dict[str, dict] = {}


def _new_job(kind: str, note: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {
        "job_id": job_id, "kind": kind, "status": "queued",
        "note": note, "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": None,
    }
    return job_id


@app.post("/api/data/import")
async def data_import(file: Optional[UploadFile] = File(None)):
    """数据导入（骨架）：文件级校验 + 入队，字段级校验与落库接 collect_pool 既有逻辑。"""
    if file is None:
        return pending("IMPORT_REJECTED", "缺少上传文件")
    fname = (file.filename or "").lower()
    if not (fname.endswith(".csv") or fname.endswith(".xlsx") or fname.endswith(".xls")):
        return pending("IMPORT_REJECTED", "仅支持 CSV / XLSX 文件", detail=f"收到 {file.filename}")
    job_id = _new_job("data_import",
                      f"文件 {file.filename} 已受理；字段级校验与落库复用 collect_pool 逻辑（TODO）")
    return ok(data={"job_id": job_id, "status": "queued"},
              message="import accepted", meta={"next": f"/api/batch/{job_id}"})


@app.post("/api/batch/upload")
async def batch_upload(file: Optional[UploadFile] = File(None)):
    """批量评估上传（骨架）。"""
    if file is None:
        return pending("IMPORT_REJECTED", "缺少上传文件")
    job_id = _new_job("batch_evaluate", f"文件 {file.filename} 已受理（批量评估骨架，TODO 接评分）")
    return ok(data={"job_id": job_id, "status": "queued"},
              message="batch accepted", meta={"next": f"/api/batch/{job_id}"})


@app.post("/api/batch-evaluate")
def batch_evaluate(payload: Optional[dict] = None):
    """批量评估（§6 模式一，骨架）。"""
    userids = (payload or {}).get("userids") if isinstance(payload, dict) else None
    job_id = _new_job("batch_evaluate",
                      f"批量评估骨架：userids={userids if userids else '（未提供）'}（TODO 接模型评分）")
    return ok(data={"job_id": job_id, "status": "queued"}, message="batch queued")


@app.get("/api/batch/{job_id}")
def batch_status(job_id: str):
    """批量任务状态查询。"""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return ok(data=job)


@app.get("/api/batch/{job_id}/export")
def batch_export(job_id: str):
    """批量结果导出（骨架，TODO CSV 流）。"""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    if job.get("status") != "done":
        return pending("BATCH_RUNNING", "任务尚未完成，暂无导出", detail=f"status={job.get('status')}")
    return ok(data={"job_id": job_id, "note": "CSV 流导出骨架（TODO）"})
