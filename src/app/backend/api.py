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

import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Union

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.app.backend import db_views
from src.app.backend import import_validate as iv

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DB_PATH = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
CONTRACT_DIR = os.path.join(ROOT, "outputs", "contracts")


def _detect_schema_version() -> str:
    """从 meta_schema_version 读实际版本。

    原来这里写死 "v1.0"，而库已到 v1.3 —— 前端拿它做判断会一直以为是初版。
    读不到时返回 "unknown"，不猜。
    """
    try:
        con = db_views.connect(DB_PATH)
        try:
            return db_views.schema_version(con)
        finally:
            con.close()
    except Exception:
        return "unknown"


# 契约版本锚点（《完整答复》§3.3.4，前端启动断言）
VERSION_META = {
    "contract_ver": "v2.0",
    "enums_version": "v1.0",
    "schema_version": _detect_schema_version(),
}

# 噪声地板（置换对照 AUC 行级：LR 0.492 / XGB 0.477，取较低者作最保守零信号基线）
NOISE_FLOOR = 0.477
NOISE_FLOOR_NOTE = "置换对照 AUC（行级）LR 0.492 / XGB 0.477，取较低者 0.477 为保守零信号基线"

# 两套数据：A=10 月信贷面板（旧），B=500 家高企弱标签（主）。
# 两者样本、特征、标签完全不同，禁止跨集合并排序 / 拼接训练 / 共用同一 AUC 坐标。
DATASET_IDS = ("A_credit_panel", "B_proxy_label_500")
DATASET_ALLOWED = ("all",) + DATASET_IDS
DATASET_JOIN_RULE = (
    "A（1,714 家信贷面板 / 匿名 x1–x200 / 75 正例）与 B（500 家高企 / 资质存续弱标签）"
    "不是同一套数据；禁止跨集合并排序、拼接训练或共用同一 AUC 坐标。"
)

# 未就绪接口 → 依赖的契约文件（文件缺失时才回落到 CONTRACT_PENDING）
PENDING_CONTRACT = {
    "/api/data/quality": "data_quality.json",
    "/api/data/panel-depth": "panel_depth.json",
    "/api/features/tree": "feature_tree.json",
    "/api/graph/structure": "graph_structure.json",
    "/api/graph/slices": "graph_slices.json",
    "/api/graph/centrality": "graph_centrality.json",
    "/api/model/train-curve": "train_curve.json",
    "/api/model/roc-pr-ks": "roc_pr_ks.json",
    "/api/explain/stage-factor": "stage_factor_weight.json",
    "/api/explain/local": "动态生成（依赖 v_model_train_ready 的 cohort 特征）",
    "/api/forecast/survival": "survival.json",
    "/api/forecast/financing": "financing.json",
    "/api/enterprise/grade": "enterprise_grade.json",
}

# 已就绪接口（code=0 且 data 非空，且不含空壳）。这份清单必须与实现同步：
# 新增就绪接口时加到这里，降级时从这里移除并归入 PENDING_CONTRACT。
READY_ENDPOINTS = [
    "/health",
    "/api/overview/summary",
    "/api/data/pipeline",
    "/api/data/datasets",
    "/api/enterprise/grade",
    "/api/data/quality",
    "/api/data/panel-depth",
    "/api/features/tree",
    "/api/graph/structure",
    "/api/graph/slices",
    "/api/graph/centrality",
    "/api/model/train-curve",
    "/api/model/roc-pr-ks",
    "/api/explain/stage-factor",
    "/api/explain/local/{userid}",
    "/api/forecast/survival",
    "/api/forecast/financing",
    "/api/enums",
    "/api/factors/importance",
    "/api/score/company/{company_id}",
    "/api/company/{company_id}/graph",
    "/api/company/{company_id}/explanation",
    "/api/company/diagnose",
    "/api/batch-evaluate",
    "/api/batch/{job_id}",
    "/api/batch/{job_id}/export",
    "/api/tech-graph/summary",
    "/api/tech-graph/enterprises",
    "/api/tech-graph/enterprise/{enterprise_id}/profile",
    "/api/tech-graph/enterprise/{enterprise_id}/neighborhood",
]


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
# 契约文件（outputs/contracts/，由 src/data/build_contracts.py 产出）
# ---------------------------------------------------------------------------
_contract_cache: dict[str, tuple[float, Any]] = {}


def _contract(fname: str) -> Optional[dict]:
    """读契约文件；按 mtime 失效。

    进程常驻时不重读是性能考虑，但脚本重跑后必须能拿到新值，所以缓存键带 mtime。
    """
    p = os.path.join(CONTRACT_DIR, fname)
    if not os.path.exists(p):
        return None
    try:
        mtime = os.path.getmtime(p)
        hit = _contract_cache.get(fname)
        if hit and hit[0] == mtime:
            return hit[1]
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        _contract_cache[fname] = (mtime, payload)
        return payload
    except (OSError, json.JSONDecodeError):
        return None


def contract_ok(fname: str, message: str = "ok") -> dict:
    """统一从契约文件取数，按 provenance 三态分流。

    - 文件缺失      → ``CONTRACT_PENDING``（脚本没跑，属工程问题）
    - displayable=false → ``DATA_UNAVAILABLE``（数据本身没有，属事实问题）
    - 否则          → ``code=0``，且 meta / data 都带 provenance 供前端标注
    """
    payload = _contract(fname)
    if payload is None:
        return pending("CONTRACT_PENDING", "契约文件未生成",
                       detail=f"outputs/contracts/{fname} 缺失，"
                              f"请运行 python -m src.data.build_contracts")
    pv = payload.get("provenance") or {}
    if pv.get("displayable") is False:
        return {
            "code": "DATA_UNAVAILABLE",
            "message": "数据源不存在，本契约只说明缺口",
            "data": None,
            "meta": _meta({"detail": pv.get("source"), "provenance": pv,
                           "contract_file": fname}),
        }
    return ok(payload, message,
              meta={"provenance": pv, "contract_file": fname,
                    "displayable": pv.get("displayable", True)})


def contract_meta(fname: str) -> dict:
    """只取 provenance（供其他端点复用，避免重复读文件）。"""
    payload = _contract(fname)
    return (payload or {}).get("provenance") or {}


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
    """已废弃：仅 /health 用它报告文件是否存在。

    评分/诊断/因子接口全部改读 SQLite（见 db_views），本函数不再参与取数。
    """
    return _load("dashboard", os.path.join("web", "dashboard_data.json"))


# 以下三个 helper（_candidate_ids / _find_company / _find_explanation）已删除：
# 它们只服务于 dashboard_data.json，而该文件从未生成，正是 404 与空壳的根因。
# 单企业数据统一走 src/app/db_views.py（只读 SQLite）。


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
    """健康检查。

    原先报 ``dashboard_ready: false``，因为 web/dashboard_data.json 从未生成。
    现在评分/诊断接口已全部改读 SQLite，该文件不再被需要；字段保留但语义
    改为「是否需要」（required=false），避免前端把它当阻塞条件。
    """
    live = {"db_exists": os.path.exists(DB_PATH)}
    try:
        con = db_views.connect(DB_PATH)
        try:
            live["schema_version"] = db_views.schema_version(con)
            live["n_enterprise"] = db_views.q1(con, "SELECT COUNT(*) FROM dim_enterprise")
        finally:
            con.close()
    except Exception as e:
        live["db_error"] = str(e)
    return ok(data={
        "status": "ok" if live.get("db_exists") else "degraded",
        **live,
        "score_source": "sqlite",
        "dashboard_data": {"present": _dashboard() is not None, "required": False,
                           "note": "评分/诊断接口已改读 SQLite，此文件不再必需"},
        "time": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# 就绪接口（真实读契约文件 / 过渡读 dashboard_data.json）
# ---------------------------------------------------------------------------
@app.get("/api/overview/summary")
def overview_summary():
    """#1 概览：KPI 直接读库 + 契约版本 + 数据缺口 + 接口就绪清单。

    原实现把 ``kpi`` 取成 ``dashboard_data.json.meta``，而该文件不存在，
    于是 kpi 恒为 null、前端概览页空一片。现在所有数字来自 SQL，可复算。
    """
    mirror = _load("gaps", os.path.join("configs", "data_gaps.mirror.json"))
    kpi = None
    registry = None
    try:
        con = db_views.connect(DB_PATH)
        try:
            kpi = db_views.kpi(con)
            registry = db_views.pipeline(con).get("registry")
        finally:
            con.close()
    except Exception as e:
        kpi = {"error": str(e)}

    return ok(data={
        "contract": dict(VERSION_META),
        "kpi": kpi,
        "datasets": {
            "B_primary": {
                "name": "浙江 500 家高企（本项目主数据）",
                "n_enterprise": (kpi or {}).get("n_enterprise"),
                "n_labeled": (kpi or {}).get("n_labeled"),
                "n_positive": (kpi or {}).get("n_positive"),
                "label": "代理标签（qual_survival_proxy），非官方结论",
                "endpoints": ["/api/tech-graph/*", "/api/score/company/*",
                              "/api/company/*", "/api/factors/importance"],
            },
            "A_legacy": {
                "name": "10 月信贷面板（1,714 家 / 匿名特征 x1–x200）",
                "n_samples": 3465,
                "n_positive": 75,
                "note": "与 B 无关，仅 benchmark / shap / noise-floor 使用",
                "endpoints": ["/api/model/benchmark", "/api/model/noise-floor",
                              "/api/explain/global"],
            },
        },
        "data_gaps": (mirror or {}).get("gap_summary"),
        "gap_registry": None if not registry else {
            "version": registry.get("version"),
            "n_items": registry.get("n_items"),
            "by_status": registry.get("by_status"),
            "n_substitutes": registry.get("n_substitutes"),
        },
        "ready_endpoints": READY_ENDPOINTS,
        "pending_endpoints": sorted(PENDING_CONTRACT.keys()),
    }, meta={"kpi_source": "sqlite", "generated_at": datetime.now(timezone.utc).isoformat()})


@app.get("/api/data/pipeline")
def data_pipeline(dataset: str = "all"):
    """#2 数据管道与缺口台账：实时管道 + 当前登记册 + 10 月旧台账（留档）。

    原实现直接透传 ``configs/data_gaps.mirror.json``，那是 10 月的静态台账，
    P0-4 的 why 还写着「技术要素维度完全缺失」，而库里已有 3 万件专利。
    现在 live 为库内实测，registry 为当前缺口登记册，旧台账降级为留档并
    逐条做矛盾检测。

    v1.8 起支持 ``dataset`` 参数做 A/B 两套数据隔离：
      all              默认，保持原返回结构（向后兼容），meta 明确未隔离
      A_credit_panel   只返回 legacy_ledger（10 月旧面板留档）
      B_proxy_label_500 只返回 live + registry（本项目 500 家高企主数据）
    任何取值都在 meta 标 ``allow_cross_join=false``；非法值返回业务码
    INVALID_DATASET 而不是抛 HTTPException（HTTP 恒 200 的既有约定）。
    """
    if dataset not in DATASET_ALLOWED:
        return pending("INVALID_DATASET", "dataset 参数非法：%s" % dataset,
                       detail="允许值：%s" % ", ".join(DATASET_ALLOWED))
    try:
        con = db_views.connect(DB_PATH)
        try:
            payload = db_views.pipeline(con)
        finally:
            con.close()
    except FileNotFoundError:
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)

    if dataset == "A_credit_panel":
        data = {"legacy_ledger": payload.get("legacy_ledger")}
    elif dataset == "B_proxy_label_500":
        data = {"live": payload.get("live"), "registry": payload.get("registry")}
    else:
        data = payload
    return ok(data=data, meta={
        "source": "sqlite + registry",
        "legacy_deprecated": True,
        "dataset": dataset,
        "isolated": dataset != "all",
        "allow_cross_join": False,
        "join_rule": DATASET_JOIN_RULE,
        "dataset_ids": list(DATASET_IDS),
    })


@app.get("/api/data/datasets")
def data_datasets():
    """两套数据的登记台账与禁止混用规则（v1.8）。

    B 套规模从库内实测取数，A 套是 10 月留档常量（不在本库里）。
    本端点只做说明，不做任何跨集计算。
    """
    n_ent = n_label = None
    try:
        con = db_views.connect(DB_PATH)
        try:
            n_ent = db_views.q1(con, "SELECT COUNT(*) FROM dim_enterprise")
            n_label = db_views.q1(con, "SELECT COUNT(*) FROM ads_label "
                                       "WHERE label_task='qual_survival'")
        finally:
            con.close()
    except (FileNotFoundError, sqlite3.Error):
        pass
    return ok(data={
        "datasets": [
            {"id": "A_credit_panel", "role": "legacy", "in_this_db": False,
             "n_entity": 1714, "n_rows": 3465, "n_positive": 75,
             "features": "匿名特征 x1–x200",
             "label": "10 月信贷面板的信贷违约标签",
             "backing": "outputs/baseline_results.json / outputs/shap_global.json"},
            {"id": "B_proxy_label_500", "role": "primary", "in_this_db": True,
             "n_entity": n_ent, "n_labeled": n_label,
             "features": "专利 / 资质 / 融资 as-of 特征（29 列口径）",
             "label": "高企资质存续失败弱标签 qual_survival（21 正例 / 286 负例）",
             "backing": "zjtech_credit.db / v_model_dataset_asof_flat"},
        ],
        "join_rule": DATASET_JOIN_RULE,
        "isolation": {
            "endpoint": "/api/data/pipeline",
            "param": "dataset",
            "allowed": list(DATASET_ALLOWED),
            "note": "dataset=all 为向后兼容默认，仍会并列返回两块；"
                    "前端展示与任何实验都应显式选定单一数据集",
        },
        "cross_dataset_endpoints": {
            "/api/model/benchmark": "A_credit_panel",
            "/api/factors/importance": "同一响应内并列展示 primary(B) 与 legacy(A)，禁止合并排序",
        },
    }, meta={"allow_cross_join": False, "dataset_ids": list(DATASET_IDS)})


@app.get("/api/model/benchmark")
def model_benchmark():
    """#10 基准对比实验（读 outputs/baseline_results.json）。

    **注意数据集**：这是 10 月信贷面板（1,714 家 / 匿名特征 x1–x200 / 75 正例），
    与本项目 500 家高企不是同一套数据。之前接口没写这一点，前端容易把面板的
    AUC 挂到高企页面上。现在在 meta 里显式标注。
    """
    bench = _load("benchmark", os.path.join("outputs", "baseline_results.json"))
    if not bench:
        return pending("CONTRACT_PENDING", "baseline_results.json 未生成")
    return ok(data=bench, meta={
        "noise_floor": NOISE_FLOOR,
        "dataset": "A_credit_panel",
        "dataset_note": "1,714 家企业 / 3,465 行 / 匿名特征 x1–x200；"
                        "与 /api/tech-graph/* 的 500 家高企数据集无对应关系",
        "cross_dataset": True,
        "dataset_scope": "A_credit_panel",
        "allow_cross_join": False,
        "join_rule": DATASET_JOIN_RULE,
    })


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
    """核心影响因子：本项目主数据集（单变量 AUC）+ 10 月面板（SHAP）分开返回。

    原实现读 dashboard_data.json.importance，文件不存在就恒返回 pending，
    而前端概览页把它当首屏图表。现在改为真实可复算的秩相关排序，并在返回体
    里标明 primary / legacy 属两套不同数据，禁止合并排序。
    """
    try:
        con = db_views.connect(DB_PATH)
        try:
            data = db_views.importance(con)
        finally:
            con.close()
    except FileNotFoundError:
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    if not data["primary"]["items"]:
        return pending("CONTRACT_PENDING", "无可算特征", detail="v_model_train_ready 缺失")
    return ok(data=data, meta={
        "primary_dataset": "B_proxy_label_500",
        "legacy_dataset": "A_credit_panel",
        "dataset_scope": ["B_proxy_label_500", "A_credit_panel"],
        "mixed_in_one_response": True,
        "allow_cross_join": False,
        "join_rule": DATASET_JOIN_RULE,
    })


def _snapshot_or_none(company_id: str):
    con = db_views.connect(DB_PATH)
    try:
        return db_views.company_snapshot(con, company_id)
    finally:
        con.close()


@app.get("/api/score/company/{company_id}")
def score_company(company_id: str):
    """单企业评分。

    **原实现的错误**：读 dashboard_data.json，该文件不存在时直接
    ``raise HTTPException(404)``。但本 API 的契约是「HTTP 恒 200，成败看 code」，
    404 会让前端整个请求失败，而不是显示"暂无评分"。

    现在改读库：真实档案 + 真实特征 + 可复算代理分（validated=false）。
    ``score.value`` 仍为 null —— 没有已验证的模型，就不给模型分。
    """
    try:
        snap = _snapshot_or_none(company_id)
    except FileNotFoundError:
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    if snap is None:
        return pending("NOT_FOUND", f"企业 {company_id} 不存在",
                       detail="企业 ID 形如 ENT + 12 位 hex（共 15 字符）；"
                              "也可传统一社会信用代码或全称精确匹配")
    proxy = db_views.proxy_score(snap["features"])
    return ok(data={
        "company_id": snap["enterprise_id"],
        "name": snap["name"],
        "score": {
            "value": None,
            "status": "MODEL_PENDING",
            "reason": "无已验证模型：弱标签正例仅 21 个，不足以产出可对外解释的模型分",
        },
        "proxy_score": proxy,
        "profile": snap["profile"],
        "patent": snap["patent"],
        "features": snap["features"],
        "feature_source": snap["feature_source"],
        "label": snap["label"],
        "graph": snap["graph"],
    }, meta={"score_source": "sqlite",
             "label_is_proxy": snap["label"] is not None})


@app.get("/api/company/{company_id}/graph")
def company_graph(company_id: str, top_k: int = 10):
    """单企业技术相似子图（真实读 ads_graph_edge）。

    原实现返回 dashboard 里的整张 demo 图，并在 note 里承认「未按 company_id
    过滤」—— 等于所有企业看到同一张图。现在按企业取真实 tech_similar 连边。
    """
    top_k = max(1, min(int(top_k), 50))
    if not os.path.exists(DB_PATH):
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    con = db_views.connect(DB_PATH)
    try:
        g = db_views.ego_graph(con, company_id, top_k=top_k)
    finally:
        con.close()
    if not g["ready"]:
        return pending("GRAPH_PENDING", g["reason"], detail="先运行 load_500family 建图")
    return ok(data=g, meta={"source": "ads_graph_edge/tech_similar"})


@app.get("/api/company/{company_id}/explanation")
def company_explanation(company_id: str):
    """单企业局部解释：返回代理分的逐特征贡献（可复算），非 SHAP。

    原实现依赖 local_explain.py 产物（不存在）→ 恒 pending。现在把代理分的
    贡献表作为解释，并显式标注 ``model_based=false``：这是规则分解，不是
    模型归因，别写进报告当 SHAP 用。
    """
    try:
        snap = _snapshot_or_none(company_id)
    except FileNotFoundError:
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    if snap is None:
        return pending("NOT_FOUND", f"企业 {company_id} 不存在")
    proxy = db_views.proxy_score(snap["features"])
    if not proxy.get("contributions"):
        return pending("EXPLAIN_PENDING", "该企业无可解释特征",
                       detail="特征全空或全部无判别力")
    return ok(data={
        "company_id": snap["enterprise_id"],
        "method": "proxy_rule_contrib",
        "model_based": False,
        "note": "按特征分位与 |AUC-0.5| 权重分解，非 SHAP；不能与 "
                "/api/explain/global（面板数据 XGBoost）交叉对比",
        "proxy_score": proxy.get("value"),
        "contributions": proxy["contributions"],
        "skipped": proxy.get("skipped", []),
        "caveat": proxy.get("caveat"),
    })


def _diagnose(company_id: str) -> dict:
    """单企业诊断的同步核心（供端点与守卫测试共用，避免 async 难以直接调用）。"""
    try:
        snap = _snapshot_or_none(company_id)
    except FileNotFoundError:
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    if snap is None:
        return pending("NOT_FOUND", f"企业 {company_id} 不存在")
    proxy = db_views.proxy_score(snap["features"])
    con = db_views.connect(DB_PATH)
    try:
        g = db_views.ego_graph(con, snap["enterprise_id"], top_k=10)
    finally:
        con.close()
    return ok(data={
        "company_id": snap["enterprise_id"],
        "name": snap["name"],
        "profile": snap["profile"],
        "score": {"value": None, "status": "MODEL_PENDING"},
        "proxy_score": proxy,
        "explanation": {"method": "proxy_rule_contrib", "model_based": False,
                        "contributions": proxy.get("contributions", [])},
        "graph": g,
        "patent": snap["patent"],
        "label": snap["label"],
        "note": "score.value 为 null 是设计选择（21 个正例不足以支撑模型分）；"
                "proxy_score 仅供原型展示排序，validated=false",
    }, meta={"source": "sqlite", "label_is_proxy": snap["label"] is not None})


@app.post("/api/company/diagnose")
async def company_diagnose(request: Request, company_id: Optional[str] = None):
    """单企业深度诊断（§6 模式二）：档案 + 代理分 + 局部解释 + 相似企业。

    参数位置：原来只声明 ``company_id: str``，FastAPI 会把它当 **query 参数**，
    前端按 POST + JSON body 调用会直接 422。现在 query 与 body 都接受：
    ``?company_id=X``、``{"company_id": "X"}``、``{"userid": "X"}`` 三种写法都行。
    """
    cid = company_id
    if not cid:
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            cid = body.get("company_id") or body.get("userid") or body.get("enterprise_id")
    if not cid:
        return pending("BAD_REQUEST", "缺少 company_id",
                       detail="可用 query（?company_id=ENT...）或 JSON body（"
                              "{\"company_id\":\"ENT...\"}）；ID 格式为 ENT+12位hex")
    return _diagnose(str(cid).strip())


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
                   COUNT(DISTINCT p.app_no) AS patent_cnt   -- 按 app_no 去重（A/B 两版算一件）
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
# 契约驱动的只读接口（本轮由 build_contracts.py 真实产出）
# ---------------------------------------------------------------------------
@app.get("/api/data/quality")
def data_quality():
    """数据质量：22 张核心表逐列空值率 + 登记册三态 + 底表/视图矛盾检测。"""
    return contract_ok("data_quality.json", "数据质量（库内实测）")


@app.get("/api/data/panel-depth")
def data_panel_depth():
    """面板深度：as-of 面板为 307 家 × 2 期平衡面板；另给原始专利年度覆盖。"""
    return contract_ok("panel_depth.json", "面板深度（库内实测）")


@app.get("/api/features/tree")
def features_tree():
    """特征体系树：29 个特征分 5 组，附覆盖率与单变量 AUC。"""
    return contract_ok("feature_tree.json", "特征体系")


@app.get("/api/graph/structure")
def graph_structure():
    """图谱结构：45,077 节点 / 101,491 边，真实异质图（非合成）。"""
    return contract_ok("graph_structure.json", "图谱结构")


@app.get("/api/graph/slices")
def graph_slices():
    """图谱切片：按地区 / 认定年 / 标签。"""
    return contract_ok("graph_slices.json", "图谱切片")


@app.get("/api/graph/centrality")
def graph_centrality():
    """中心性：加权度 / 度 / PageRank（技术相似子图）。"""
    return contract_ok("graph_centrality.json", "图谱中心性")


@app.get("/api/model/train-curve")
def model_train_curve():
    """训练曲线：GAT 只落盘 5/41 点（partial），baseline 为每折一点。"""
    return contract_ok("train_curve.json", "训练曲线（部分）")


@app.get("/api/model/roc-pr-ks")
def model_roc_pr_ks():
    """ROC/PR/KS：由实测 AUC+KS 双锚点重建，非逐点实测。"""
    return contract_ok("roc_pr_ks.json", "ROC/PR/KS（锚点重建）")


@app.get("/api/explain/stage-factor")
def explain_stage_factor():
    """阶段因子：按 period_t 的描述统计，不是因子权重模型。"""
    return contract_ok("stage_factor_weight.json", "阶段因子（描述性）")


@app.get("/api/forecast/survival")
def forecast_survival():
    """生存预测：100 折 AUC + 前瞻名单；含泄漏诊断说明高 AUC 的来源。"""
    return contract_ok("survival.json", "生存预测")


@app.get("/api/forecast/financing")
def forecast_financing():
    """融资画像：506 条真实事件；明确标注不能做融资/营收预测。"""
    return contract_ok("financing.json", "融资画像（非预测）")


@app.get("/api/enterprise/grade")
def enterprise_grade():
    """企业技术与资质梯队分档（v1.7 L0 规则基线）：实测 44/95/275/83/3 = 500 家。

    档次是规则输出，不是标签、不是信用分；契约里带 usage_boundary，
    禁止拿它报准确率或入模验证。
    """
    return contract_ok("enterprise_grade.json", "企业分档（库内实测）")


@app.get("/api/explain/local/{userid}")
def explain_local(userid: str):
    """单企业局部解释 —— 动态生成，不预落 500 个文件。

    与 ``/api/company/{id}/explanation`` 的区别：
    - 本接口给**逐特征贡献**（percentile + 方向 + 权重），面向"为什么这个分"
    - 后者给规则分解摘要

    两者都**不是 SHAP**：没有训练好的模型可归因。method 固定为
    ``proxy_rule_based``，``model_based=false``，前端必须显示该口径。
    """
    con = db_views.connect(DB_PATH)
    try:
        snap = db_views.company_snapshot(con, userid)
        if snap is None:
            return pending("NOT_FOUND", "企业不存在",
                           detail=f"未知企业 ID：{userid}（格式应为 ENT+12位hex，共 15 字符）")
        feats = snap.get("features") or {}
        score = db_views.proxy_score(feats)
        return ok(data={
            "enterprise_id": snap["enterprise_id"],
            "name": snap["name"],
            "method": "proxy_rule_based",
            "model_based": False,
            "is_shap": False,
            "feature_source": snap.get("feature_source"),
            "base_value": 50.0,
            "score": score.get("value"),
            "band": score.get("band"),
            "features": [{
                "feature": c["feature"], "cn": c["cn"], "value": c["value"],
                "percentile": c["percentile"], "contribution": c["contribution"],
                "direction": c["direction"], "direction_note": c["direction_note"],
                "auc": c["auc"], "weight": c["weight"],
            } for c in score.get("contributions", [])],
            "skipped": score.get("skipped", []),
            "caveat": score.get("caveat"),
        }, message="局部解释（规则代理，非模型归因）",
            meta={"reference": score.get("reference"), "validated": False})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 企业档案（#16）
# ---------------------------------------------------------------------------
@app.get("/api/enterprise/{userid}")
def enterprise(userid: str):
    """#16 企业档案 + 仪表盘。

    原先恒返回 LABEL_PENDING（「label 口径未确认」）。但代理标签已定义并成文
    （outputs/label_spec.md，definition_ver=qual_survival_proxy_v0.x），单企业
    档案与特征也已可读 —— 继续整个拦住反而与 /api/score/company 自相矛盾。
    现在返回真实档案，同时显式标注 ``official_label_pending: true``：
    代理标签可用，正式口径仍待指导教师确认。
    """
    try:
        snap = _snapshot_or_none(userid)
    except FileNotFoundError:
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    if snap is None:
        return pending("NOT_FOUND", f"企业 {userid} 不存在",
                       detail="ID 形如 ENT + 12 位 hex；也接受统一社会信用代码或全称")
    proxy = db_views.proxy_score(snap["features"])
    lab = snap["label"] or {}
    return ok(data={
        "enterprise_id": snap["enterprise_id"],
        "name": snap["name"],
        "profile": snap["profile"],
        "patent": snap["patent"],
        "features": snap["features"],
        "feature_source": snap["feature_source"],
        "label": snap["label"],
        "label_definition_ver": lab.get("definition_ver"),
        "official_label_pending": True,
        "proxy_score": proxy,
        "note": "label 为代理标签（qual_survival_proxy），非官方违约结论；"
                "详见 outputs/label_spec.md",
    }, meta={"label_is_proxy": True, "proxy_score_validated": False})


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


def _do_batch(ids: Optional[list], note: str = "") -> dict:
    """批量评估的实际执行体（batch-evaluate 与 batch/upload 共用）。"""
    if not os.path.exists(DB_PATH):
        return pending("DB_PENDING", "数据库不存在：%s" % DB_PATH)
    con = db_views.connect(DB_PATH)
    try:
        rows = db_views.batch_rows(con, ids)
    finally:
        con.close()

    scored = [r for r in rows if r["proxy_score"] is not None]
    not_found = [r for r in rows if r["status"] == "NOT_FOUND"]
    job_id = _new_job("batch_evaluate",
                      "同步评估完成：%d 家" % len(scored) if scored else "无可评分企业")
    _JOBS[job_id].update({
        "status": "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "source": note or "api",
        "summary": {
            "n_requested": len(rows),
            "n_scored": len(scored),
            "n_not_found": len(not_found),
            "mean_proxy_score": round(sum(r["proxy_score"] for r in scored) / len(scored), 1)
            if scored else None,
            "band_dist": {b: sum(1 for r in scored if r["band"] == b)
                          for b in ("领先", "高于中位", "低于中位", "落后")},
            "note": "proxy_score 为代理分（validated=false），非模型输出",
        },
    })
    return ok(data={
        "job_id": job_id,
        "status": "done",
        "n_requested": len(rows),
        "n_scored": len(scored),
        "n_not_found": len(not_found),
        "summary": _JOBS[job_id]["summary"],
    }, message="batch done", meta={"next": f"/api/batch/{job_id}/export"})


@app.post("/api/data/import")
async def data_import(file: Optional[UploadFile] = File(None),
                      dry_run: bool = True):
    """数据导入：解析 → 识别目标表 → 字段级校验 → （可选）staging 落库。

    默认 ``dry_run=true`` 只返回校验报告，不写库；传 ``?dry_run=false`` 才写
    ``stg_import`` 缓冲区（**绝不直接写核心表**，合表另走 ETL）。

    校验未通过（``summary.acceptable=false``）时即便 ``dry_run=false`` 也拒绝落库，
    避免脏数据进仓。
    """
    if file is None:
        return pending("IMPORT_REJECTED", "缺少上传文件",
                       detail="请求需带 multipart 文件字段 file（CSV / TSV / XLSX）")
    raw = await file.read()
    if not raw:
        return pending("IMPORT_REJECTED", "文件为空")

    try:
        headers, rows, fmt = iv.parse_upload(file.filename or "", raw)
    except ValueError as e:
        return pending("IMPORT_REJECTED", str(e), detail=f"收到 {file.filename}")

    if not rows:
        return pending("IMPORT_REJECTED", "文件只有表头、没有数据行")

    con = db_views.connect(DB_PATH)
    try:
        det = iv.detect_target_table(con, headers)
        if det["table"] is None:
            return pending("IMPORT_REJECTED", det["reason"],
                           detail=f"未识别目标表；表头前 8 列：{headers[:8]}；"
                                  f"最接近的候选：{[c['table'] for c in det['candidates']]}")
        report = iv.validate_rows(con, det["table"], headers, rows)
    finally:
        con.close()

    staged = None
    if not dry_run:
        if not report["summary"]["acceptable"]:
            return pending("IMPORT_REJECTED",
                           f"校验未通过（{report['summary']['n_errors']} 个错误），拒绝落库",
                           detail="先修正错误，或用 dry_run=true 查看完整报告")
        staged = iv.stage_rows(DB_PATH, det["table"], headers, rows)

    job_id = _new_job("data_import",
                      f"{file.filename} → {det['table']}：{report['n_rows']} 行，"
                      f"{report['summary']['n_errors']} 错 / "
                      f"{report['summary']['n_warnings']} 警")
    _JOBS[job_id].update({
        "status": "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "report": report,
        "staged": staged,
        "source": f"upload:{file.filename}",
    })
    return ok(data={
        "job_id": job_id,
        "format": fmt,
        "target_table": det["table"],
        "detection": {"ratio": det["ratio"], "matched": det["matched"],
                      "unknown_columns": det["unknown"],
                      "candidates": det["candidates"]},
        "validation": report["summary"],
        "errors_sample": report["errors"][:20],
        "warnings_sample": report["warnings"][:20],
        "dry_run": dry_run,
        "staged": staged,
    }, message="导入校验完成" + ("（未落库）" if dry_run else ""),
        meta={"next": f"/api/batch/{job_id}"})


# batch/upload 的企业 ID 列名候选（按优先级）
_ID_HEADER_CANDIDATES = ["enterprise_id", "userid", "user_id", "企业id", "企业ID",
                         "企业编号", "统一社会信用代码", "credit_code", "id", "name"]


@app.post("/api/batch/upload")
async def batch_upload(file: Optional[UploadFile] = File(None)):
    """批量评估上传：解析名单 → 校验企业 ID → **真正跑完评估**。

    原来只回 ``queued`` 然后永不执行，``/export`` 永远 BATCH_RUNNING。
    现在上传即算完，返回的 ``job_id`` 可直接查详情与导出。
    """
    if file is None:
        return pending("IMPORT_REJECTED", "缺少上传文件",
                       detail="请求需带 multipart 文件字段 file（CSV/XLSX，含企业 ID 列）")
    raw = await file.read()
    if not raw:
        return pending("IMPORT_REJECTED", "文件为空")
    try:
        headers, rows, fmt = iv.parse_upload(file.filename or "", raw)
    except ValueError as e:
        return pending("IMPORT_REJECTED", str(e), detail=f"收到 {file.filename}")

    # 找 ID 列：先按候选名，再按"看起来像 ENT 开头的值"兜底
    col_idx, col_name = None, None
    low = [h.strip().lower() for h in headers]
    for cand in _ID_HEADER_CANDIDATES:
        if cand.lower() in low:
            col_idx = low.index(cand.lower())
            col_name = headers[col_idx]
            break
    if col_idx is None:
        for j, h in enumerate(headers):
            vals = [r[j] for r in rows[:50] if j < len(r)]
            if vals and sum(1 for v in vals if iv.ENT_ID_RE.match((v or "").strip())) >= len(vals) * 0.6:
                col_idx, col_name = j, h
                break
    if col_idx is None:
        return pending("IMPORT_REJECTED", "找不到企业 ID 列",
                       detail=f"表头为 {headers[:10]}；可接受的列名："
                              f"{_ID_HEADER_CANDIDATES[:6]}")

    ids, bad = [], []
    for i, r in enumerate(rows):
        v = (r[col_idx] if col_idx < len(r) else "").strip()
        if not v:
            continue
        if iv.ENT_ID_RE.match(v):
            ids.append(v)
        else:
            bad.append({"row": i + 2, "value": v[:60],
                        "reason": "不是 ENT+12位hex 格式"})
    if not ids:
        return pending("IMPORT_REJECTED", "文件中没有合法的企业 ID",
                       detail=f"列 {col_name} 共 {len(rows)} 行，全部格式不符；"
                              f"前几条：{[b['value'] for b in bad[:3]]}")

    resp = _do_batch(ids, note=f"upload:{file.filename}")
    if resp.get("code") == 0:
        resp["data"]["source_file"] = file.filename
        resp["data"]["id_column"] = col_name
        resp["data"]["n_ids_parsed"] = len(ids)
        resp["data"]["n_ids_rejected"] = len(bad)
        if bad:
            resp["data"]["rejected_sample"] = bad[:10]
        resp["meta"]["format"] = fmt
    return resp


@app.post("/api/batch-evaluate")
def batch_evaluate(payload: Optional[dict] = None):
    """批量评估（§6 模式一）。

    原实现只回一个 ``job_id`` 就置 ``queued``，之后永远不跑，``/export`` 因此
    永远返回 BATCH_RUNNING —— 是个假骨架。现在同步算完并落结果：
    传 ``{"userids": [...]}`` 只算指定企业，不传则跑全库 500 家。
    """
    payload = payload or {}
    ids = None
    if isinstance(payload, dict):
        ids = payload.get("userids") or payload.get("enterprise_ids")
    if ids is not None and not isinstance(ids, list):
        return pending("BAD_REQUEST", "userids 必须是字符串数组",
                       detail="例：{\"userids\": [\"ENT2803CAE1D888\"]}")
    return _do_batch(ids, note="api:batch-evaluate")


@app.get("/api/batch/{job_id}")
def batch_status(job_id: str):
    """批量任务状态查询。未知 job 返回业务码，不抛 404（契约要求 HTTP 恒 200）。"""
    job = _JOBS.get(job_id)
    if not job:
        return pending("JOB_NOT_FOUND", f"任务 {job_id} 不存在",
                       detail="_JOBS 为进程内存储，重启后清空；请重新提交")
    return ok(data=job)


@app.get("/api/batch/{job_id}/export")
def batch_export(job_id: str):
    """批量结果导出为 CSV（真实文件流，非骨架）。"""
    job = _JOBS.get(job_id)
    if not job:
        return pending("JOB_NOT_FOUND", f"任务 {job_id} 不存在")
    if job.get("status") != "done":
        return pending("BATCH_RUNNING", "任务尚未完成，暂无导出",
                       detail=f"status={job.get('status')}")
    cols = ["enterprise_id", "name", "status", "proxy_score", "band",
            "label", "cohort_year", "n_patent_app", "n_patent_grant"]
    buf = io.StringIO()
    buf.write("\ufeff")                      # BOM，Excel 直开不乱码
    w = csv.writer(buf)
    w.writerow(cols)
    for r in job.get("rows", []):
        w.writerow([r.get(c) if r.get(c) is not None else "" for c in cols])
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="batch_%s.csv"' % job_id,
                 "X-Total-Rows": str(len(job.get("rows", []))),
                 "X-Note": "proxy_score is a proxy (validated=false), not a model score"},
    )
