#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FE-11 契约生成脚本：P2「数据与特征」四区块所需的四份契约
------------------------------------------------------------------
产出（均落在 outputs/contracts/，字段 snake_case，逐字对齐前端 src/types/contracts.ts）：
  data_pipeline.json  A01 数据接入链路
  data_quality.json   A02 数据质量诊断（缺失率热力图 + 缩尾前后箱线图数据）
  panel_depth.json    A03 面板观测纵深（每企业观测月数直方图）
  feature_tree.json   A04 特征体系三级树 + 字段详情

数据口径（两条必须遵守的红线，写死在 meta 里）：
  1) 真实信贷面板 = real_panel：字段语义未知（x21–x200 只有代号），行级 AUC≈0.59，
     置换对照噪声地板 0.477 —— 这些数字只能作为参考锚点出现，不得包装成业务结论。
  2) 合成异质图 = synthetic_graph：仅用于架构验证（AUC≈0.91），**不得与真实面板同图混排**。

当前真实面板尚未落盘到本仓库（backend 侧 data/processed 未就绪），脚本走 `--demo` 模式：
  用固定随机种子复现「结构同构」的占位数值，并把每份契约的 meta 标注为
  source='demo_placeholder' + disclaimer，前端必须原样透出（不得包装成实测结论）。
真实面板到位后，把 `load_real_panel()` 接上 data/processed/panel_*.parquet，
去掉 --demo 即产出实测契约，前端零改动（契约结构不变）。

用法：
  python scripts/build_data_contracts.py --demo
  python scripts/build_data_contracts.py --out outputs/contracts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
SEED = 20261022
TZ = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 口径常量
# 真实信贷面板的参考锚点（来自 docs/实验结果报告-基线SHAP与GAT.md，只作口径说明，不是本脚本的产出）
REAL_PANEL_ROW_AUC = 0.59
NOISE_FLOOR = 0.477

# 目前面板里「语义未知」的字段区间：x21–x200 只允许显示代号（红线二）
SEMANTIC_UNKNOWN_RANGE = (21, 200)


# ---------------------------------------------------------------- 工具函数
def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def round4(x: float) -> float:
    return round(float(x), 4)


def quantile(values: list[float], q: float) -> float:
    """线性插值分位数（不引 numpy，保证脚本零依赖可跑）。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return s[int(pos)]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def meta(dataset: str, script: str, n_samples: int, extra: dict[str, Any] | None = None) -> dict:
    """契约 §6.2 必备六字段 + 红线补充字段。"""
    base = {
        "generated_at": now_iso(),
        "script": script,
        "dataset": dataset,
        "n_samples": n_samples,
        "seed": SEED,
        "schema_version": SCHEMA_VERSION,
        "noise_floor": NOISE_FLOOR if dataset == "real_panel" else None,
        "semantic_known": False,
        "label_ref": None,
        "source": "demo_placeholder",
        "disclaimer": (
            "结构同构占位数据（demo_placeholder）：真实信贷面板尚未接入 data/processed，"
            "本契约仅用于前端页面结构与交互联调，不得作为任何实证结论引用。"
        ),
        "redline": {
            "no_mixed_dataset": True,
            "noise_floor": NOISE_FLOOR,
            "unknown_fields_shown_as_code": True,
        },
    }
    if extra:
        base.update(extra)
    return base


# ---------------------------------------------------------------- A01 数据接入链路
def build_data_pipeline() -> dict:
    nodes = [
        # ---- 数据源（真实 / 待接入 分开标注，避免「看成已经有了」）
        {"id": "src-credit", "name": "信贷面板", "kind": "source", "status": "ready",
         "description": "银行信贷台账月度观测（real_panel），企业 1,284 家 / 覆盖 1,017 家"},
        {"id": "src-patent", "name": "专利数据（CNIPA）", "kind": "source", "status": "pending",
         "description": "国家知识产权局开放数据，免费；对应数据缺口 P0-4"},
        {"id": "src-invest", "name": "融资与投资机构", "kind": "source", "status": "pending",
         "description": "企查查 / 天眼查 API，¥3,000 预算内；对应数据缺口 P0-5"},
        {"id": "src-reg", "name": "工商与资质变更", "kind": "source", "status": "pending",
         "description": "高新证书、经营异常名录；依赖 userid→统一社会信用代码映射键（P0-1）"},
        {"id": "src-news", "name": "新闻与招聘文本", "kind": "source", "status": "pending",
         "description": "非结构化文本，用于 SciBERT 语义向量化"},
        # ---- 处理节点
        {"id": "key-map", "name": "主键映射", "kind": "process", "status": "blocked",
         "description": "userid → 统一社会信用代码 / 企业名（缺口 P0-1，阻塞全部外部数据接入）"},
        {"id": "preprocess", "name": "预处理流水线", "kind": "process", "status": "ready",
         "description": "src/data/preprocess.py：按 userid 分组切分 → 训练集中位数填充 → 1%/99% 缩尾 → Z-score"},
        {"id": "text-embed", "name": "SciBERT 向量化", "kind": "process", "status": "pending",
         "description": "专利摘要 / 新闻切片 → 768 维语义向量"},
        {"id": "graph-build", "name": "异质图构建", "kind": "process", "status": "ready",
         "description": "src/graph/panel_graph.py 面板自建图；synthetic.py 六类实体合成图"},
        # ---- 数据集
        {"id": "panel-ds", "name": "企业级面板数据集", "kind": "dataset", "status": "ready",
         "description": "每企业平均 2 个月观测，纵深浅 → 长序列建模不可行"},
        {"id": "hetero-graph", "name": "六类实体异质图", "kind": "dataset", "status": "synthetic_only",
         "description": "当前仅合成图（架构验证用），真实实体数据未接入"},
        {"id": "feature-table", "name": "标准化特征表", "kind": "dataset", "status": "partial",
         "description": "技术要素 / 成长轨迹 / 融合特征三类；外部数据未接入前只有信贷面板字段"},
    ]

    edges = [
        {"from": "src-credit", "to": "preprocess", "label": "月度观测", "status": "ready"},
        {"from": "src-patent", "to": "key-map", "label": "申请号→企业", "status": "pending"},
        {"from": "src-invest", "to": "key-map", "label": "企业名→userid", "status": "pending"},
        {"from": "src-reg", "to": "key-map", "label": "信用代码", "status": "pending"},
        {"from": "src-news", "to": "text-embed", "label": "文本", "status": "pending"},
        {"from": "key-map", "to": "graph-build", "label": "实体对齐", "status": "blocked"},
        {"from": "preprocess", "to": "panel-ds", "label": "企业-月", "status": "ready"},
        {"from": "preprocess", "to": "feature-table", "label": "结构化特征", "status": "partial"},
        {"from": "text-embed", "to": "feature-table", "label": "语义特征", "status": "pending"},
        {"from": "graph-build", "to": "hetero-graph", "label": "节点/边", "status": "synthetic_only"},
    ]

    steps = [
        {"order": 1, "name": "按 userid 分组切分", "impl": "src/data/preprocess.py",
         "detail": "GroupShuffleSplit(userid) 7:1.5:1.5，禁止行级随机切分（会泄漏同企业跨月信息）",
         "status": "ready"},
        {"order": 2, "name": "缺失值填充", "impl": "src/data/preprocess.py",
         "detail": "训练集中位数填充，统计量只在训练集拟合", "status": "ready"},
        {"order": 3, "name": "缩尾", "impl": "src/data/preprocess.py",
         "detail": "1% / 99% 分位缩尾，边界取训练集", "status": "ready"},
        {"order": 4, "name": "标准化", "impl": "src/data/preprocess.py",
         "detail": "Z-score，均值与方差取训练集", "status": "ready"},
        {"order": 5, "name": "文本语义向量化", "impl": "src/data/text_embed.py（待建）",
         "detail": "SciBERT-base 768 维，句向量池化", "status": "pending"},
        {"order": 6, "name": "多源融合", "impl": "src/data/fusion.py（待建）",
         "detail": "按 userid + 月份左连接外部特征，缺测保留 NaN 交给第 2 步", "status": "blocked"},
    ]

    return {
        "meta": meta("real_panel", "scripts/build_data_contracts.py", 1017),
        "data": {"nodes": nodes, "edges": edges, "steps": steps},
    }


# ---------------------------------------------------------------- A02 数据质量
def build_data_quality() -> dict:
    rnd = random.Random(SEED)

    # 字段清单：真实面板字段全部语义未知，只用代号
    fields_spec = [
        ("x021", 0.02), ("x037", 0.12), ("x052", 0.31), ("x066", 0.07),
        ("x081", 0.04), ("x094", 0.18), ("x108", 0.04), ("x121", 0.58),
        ("x134", 0.09), ("x147", 0.26), ("x155", 0.22), ("x163", 0.06),
        ("x178", 0.41), ("x192", 0.13),
    ]
    fields = [
        {
            "field": f,
            "semantic_known": False,
            "label_ref": f"feature_dict.{f}",
            "missing_rate": miss,
            "winsorized": miss < 0.5,  # 缺失过半的字段缩尾意义不大，如实标注未做
            "dtype": "numeric",
            "n_non_null": int(round(1017 * 2.06 * (1 - miss))),
            "skew_after": round4(rnd.uniform(-0.4, 3.2)),
            "outlier_rate": round4(max(0.0, rnd.gauss(0.02, 0.012))),
        }
        for f, miss in fields_spec
    ]

    # 缺失率矩阵：字段 × 观测年（热力图数据源）
    years = ["2021", "2022", "2023"]
    missing_matrix = []
    for f, miss in fields_spec:
        for i, y in enumerate(years):
            # 逐年小幅下降（台账口径补录），用确定性扰动保证可复现
            drift = -0.03 * i + rnd.uniform(-0.02, 0.02)
            missing_matrix.append({
                "field": f,
                "semantic_known": False,
                "year": y,
                "missing_rate": round4(min(0.95, max(0.0, miss + drift))),
            })

    # 缩尾前后箱线图数据（q05 / q25 / q50 / q75 / q95）
    winsor = []
    for f, miss in fields_spec[:6]:
        base = rnd.uniform(2.0, 40.0)
        spread = rnd.uniform(1.5, 9.0)
        # 长尾：before 的上尾刻意夸张，after 收到 1/6 左右，模拟 1%/99% 缩尾
        winsor.append({
            "field": f, "semantic_known": False, "stage": "before",
            "q05": round4(base - 2.2 * spread),
            "q25": round4(base - 0.6 * spread),
            "q50": round4(base),
            "q75": round4(base + 0.9 * spread),
            "q95": round4(base + 22 * spread),
            "n_trimmed": int(round(1017 * 2.06 * 0.02)),
        })
        winsor.append({
            "field": f, "semantic_known": False, "stage": "after",
            "q05": round4(base - 1.7 * spread),
            "q25": round4(base - 0.55 * spread),
            "q50": round4(base),
            "q75": round4(base + 0.85 * spread),
            "q95": round4(base + 3.4 * spread),
            "n_trimmed": 0,
        })

    summary = {
        "n_fields": len(fields),
        "n_fields_missing_gt_30pct": sum(1 for _, m in fields_spec if m > 0.30),
        "mean_missing_rate": round4(sum(m for _, m in fields_spec) / len(fields_spec)),
        "semantic_known_ratio": 0.0,
        "rows_total": 2096,
        "enterprises": 1017,
        "winsor_bounds": {"lower_pct": 1, "upper_pct": 99, "fit_on": "train_only"},
        "split_rule": "GroupShuffleSplit by userid 7:1.5:1.5",
    }

    return {
        "meta": meta("real_panel", "scripts/build_data_contracts.py", 1017),
        "data": {"summary": summary, "fields": fields,
                 "missing_matrix": missing_matrix, "winsor": winsor},
    }


# ---------------------------------------------------------------- A03 面板纵深
def build_panel_depth() -> dict:
    # 口径：每企业观测月数。当前真实面板纵深极浅（平均 ≈2 个月），长序列建模不可行。
    bins_spec = [
        (1, 1, 412, 412),
        (2, 3, 358, 812),
        (4, 6, 171, 767),
        (7, 12, 58, 92),
        (13, 24, 18, 13),
    ]
    bins = [
        {"range": f"{lo}–{hi} 月" if lo != hi else f"{lo} 月",
         "lo": lo, "hi": hi, "enterprises": n, "observations": obs}
        for lo, hi, n, obs in bins_spec
    ]
    ent_total = sum(b["enterprises"] for b in bins)
    obs_total = sum(b["observations"] for b in bins)
    median_months = 2  # 由分布反推：1 月 412 / 2–3 月 358，中位数落在 2 月
    p90_months = 6

    coverage = {
        "months_total": 36,
        "enterprises_with_panel": ent_total,
        "median_months": median_months,
        "mean_months": round4(obs_total / ent_total),
        "p90_months": p90_months,
        "observations_total": obs_total,
        "long_sequence_feasible": False,
        "temporal_unit": "跨申请月图切片",
    }

    return {
        "meta": meta("real_panel", "scripts/build_data_contracts.py", ent_total),
        "data": {"bins": bins, "coverage": coverage},
    }


# ---------------------------------------------------------------- A04 特征体系三级树
def leaf(field: str, ftype: str, miss: float, used: bool, note: str = "") -> dict:
    return {
        "kind": "leaf",
        "field": field,
        "semantic_known": False,
        "label_ref": f"feature_dict.{field}",
        "type": ftype,
        "missing_rate": miss,
        "used_in_model": used,
        "note": note,
        "detail": {
            "dtype": ftype,
            "missing_rate": miss,
            "winsorized": miss < 0.5,
            "standardized": True,
            "fit_scope": "train_only",
            "in_graph": field in {"x037", "x052", "x163"},
            "shap_rank": None,
            "semantic_status": "unknown",
            "semantic_note": "字段字典（x21–x200）未补齐，仅显示原始代号；业务名一律经 label_ref 间接引用",
        },
    }


def build_feature_tree() -> dict:
    roots = [
        {
            "kind": "branch", "name": "技术要素", "code": "TECH", "level": 1,
            "hint": "专利 / 研发投入 / 核心团队 —— 对应项目三大目标之「技术要素」维度",
            "data_ready": False,
            "children": [
                {
                    "kind": "branch", "name": "知识产权", "code": "TECH-IP", "level": 2,
                    "hint": "发明专利占比、授权率、被引、技术广度、专利三值评分（待 CNIPA 数据接入）",
                    "data_ready": False,
                    "children": [
                        leaf("x037", "numeric", 0.12, True, "面板内字段，语义待字典确认"),
                        leaf("x052", "numeric", 0.31, True, "面板内字段，缺失率偏高"),
                    ],
                },
                {
                    "kind": "branch", "name": "研发投入", "code": "TECH-RD", "level": 2,
                    "hint": "研发费用率、研发人员占比（待工商/年报数据接入）",
                    "data_ready": False,
                    "children": [leaf("x108", "numeric", 0.04, True)],
                },
                {
                    "kind": "branch", "name": "核心团队", "code": "TECH-TEAM", "level": 2,
                    "hint": "核心人员稳定性、任职年限（待人员任职数据接入）",
                    "data_ready": False,
                    "children": [leaf("x066", "numeric", 0.07, False, "仅入图，未进模型")],
                },
            ],
        },
        {
            "kind": "branch", "name": "成长轨迹", "code": "GROW", "level": 1,
            "hint": "融资 / 里程碑 / 规模增长 —— 对应「成长轨迹」维度",
            "data_ready": False,
            "children": [
                {
                    "kind": "branch", "name": "融资历程", "code": "GROW-FIN", "level": 2,
                    "hint": "新获融资额、上轮估值比、投资机构知名度（待企查查/天眼查 API）",
                    "data_ready": False,
                    "children": [
                        leaf("x121", "numeric", 0.58, False, "缺失 58%，暂不进模型"),
                        leaf("x134", "category", 0.09, True),
                    ],
                },
                {
                    "kind": "branch", "name": "规模增长", "code": "GROW-SCALE", "level": 2,
                    "hint": "营收 / 员工 / 专利复合增长率",
                    "data_ready": False,
                    "children": [
                        leaf("x155", "numeric", 0.22, True),
                        leaf("x147", "numeric", 0.26, False),
                    ],
                },
                {
                    "kind": "branch", "name": "里程碑事件", "code": "GROW-MILE", "level": 2,
                    "hint": "高新资质、融资轮次、团队变动虚拟变量（待工商变更数据）",
                    "data_ready": False,
                    "children": [leaf("x094", "boolean", 0.18, False, "虚拟变量，未进模型")],
                },
            ],
        },
        {
            "kind": "branch", "name": "融合特征", "code": "FUSE", "level": 1,
            "hint": "技术转化效率 / 资本技术协同度 / 图谱结构 —— 前两类需双维数据齐备后才能算",
            "data_ready": False,
            "children": [
                {
                    "kind": "branch", "name": "图谱结构", "code": "FUSE-GRAPH", "level": 2,
                    "hint": "度中心性、邻居平均信用、技术相似度（GAT 聚合后的结构特征）",
                    "data_ready": "partial",
                    "children": [
                        leaf("x163", "numeric", 0.06, True, "入图且进模型"),
                        leaf("x178", "numeric", 0.41, False),
                    ],
                },
                {
                    "kind": "branch", "name": "技术-资本协同", "code": "FUSE-COOP", "level": 2,
                    "hint": "技术转化效率、资本技术协同度（双维数据齐备后计算，当前为占位节点）",
                    "data_ready": False,
                    "children": [],
                },
            ],
        },
    ]

    # 扁平索引，给「字段详情浮层」与前端检索用
    flat: list[dict] = []

    def walk(nodes: list[dict], path: list[str]) -> None:
        for n in nodes:
            p = path + [n["name"] if n["kind"] == "branch" else n["field"]]
            if n["kind"] == "leaf":
                flat.append({
                    "field": n["field"], "semantic_known": n["semantic_known"],
                    "path": " / ".join(p[:-1]), "type": n["type"],
                    "missing_rate": n["missing_rate"], "used_in_model": n["used_in_model"],
                })
            else:
                walk(n.get("children", []), p)

    walk(roots, [])

    summary = {
        "level1": 3,
        "level2": 9,
        "leaves": len(flat),
        "used_in_model": sum(1 for x in flat if x["used_in_model"]),
        "semantic_known_leaves": 0,
        "data_ready_branches": 0,
        "blocked_by": ["P0-1 userid→企业映射键", "P0-2 x21–x200 字段字典"],
    }

    return {
        "meta": meta("real_panel", "scripts/build_data_contracts.py", len(flat)),
        "data": {"roots": roots, "flat": flat, "summary": summary},
    }


# ---------------------------------------------------------------- 主流程
def main() -> None:
    ap = argparse.ArgumentParser(description="生成 P2 数据与特征页四份契约")
    ap.add_argument("--out", default="outputs/contracts", help="输出目录")
    ap.add_argument("--demo", action="store_true",
                    help="用结构同构占位数据（真实面板未接入 data/processed 时的默认行为）")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    payloads = {
        "data_pipeline.json": build_data_pipeline(),
        "data_quality.json": build_data_quality(),
        "panel_depth.json": build_panel_depth(),
        "feature_tree.json": build_feature_tree(),
    }

    manifest_rows = []
    for name, payload in payloads.items():
        path = out / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        n_leaves = len(payload["data"].get("flat", []) or payload["data"].get("fields", []) or [])
        manifest_rows.append((name, path.stat().st_size, n_leaves))
        print(f"[OK] {path}  ({path.stat().st_size:,} B)")

    print("\n字段数 / 记录数校验：")
    for name, size, n in manifest_rows:
        print(f"  - {name}: {n} 条")
    print(f"\n口径：dataset=real_panel，noise_floor={NOISE_FLOOR}，"
          f"x21–x200 一律 semantic_known=false；demo 模式={bool(args.demo)}")


if __name__ == "__main__":
    main()
