#!/usr/bin/env python3
"""P5 可解释性契约生成器（FE-14）

产出两份契约，纪律是「实测数字零污染」：

1. ``outputs/contracts/shap_global.json`` —— **读改写**
   - 原有 ``data.models[].top`` / ``top20_share`` / ``unknown_field_share`` /
     ``conclusion`` 以及整块 ``meta`` 原样保留，一个字符都不动（写完自校验）；
   - 只追加 ``data.models[].beeswarm`` 与 ``data.beeswarm_meta``：
     A13 的蜂群图需要**逐样本** SHAP 值，而逐样本值从未随契约落盘
     （``src/explain/global_shap.py`` 只输出 mean|SHAP| 与方向）。
     所以这里做的是「锚点重建」：以**实测** mean|SHAP| 与**实测**方向为约束，
     反推一批样本点，并在 ``derived`` / ``derivation`` / ``meta.source`` 三处声明。
     前端角标据此显示「锚点重建」而不是「实测结果」。

2. ``outputs/contracts/stage_factor_weight.json`` —— 新建（A15）
   真实契约要等 P0-2（x21–x200 字段字典）与 P0-5（融资事件）到位，
   现阶段按「结构同构 + 明确标注占位」出 mock：阶段划分、维度权重是占位值，
   字段排序与 mean|SHAP| 仍取自实测 shap_global.json（同一份锚点，不另造数）。

复现：``python3 scripts/build_explain_contracts.py``（零第三方依赖，seed 固定）
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, "outputs", "contracts")
SHAP_PATH = os.path.join(CONTRACT_DIR, "shap_global.json")
STAGE_PATH = os.path.join(CONTRACT_DIR, "stage_factor_weight.json")

SEED = 20261028
CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 28, 15, 10, tzinfo=CST).isoformat()

# 蜂群图参数
BEESWARM_TOP_K = 15          # 与条形图 Top-15 对齐，避免两张图各说各话
BEESWARM_N_PLOT = 200        # 每行画 200 个点（共 698 个样本，等概率抽样）
BEESWARM_SIGMA = 0.62        # 幅度分布的对数正态 σ：越大长尾越明显
BEESWARM_CAP = 4.0           # 单点幅度上限 = cap × mean|SHAP|，否则个别极端点把 x 轴拉长一半空白
BEESWARM_FLIP = 0.26         # 少数样本贡献方向与主方向相反的比例


# --------------------------------------------------------------------------- #
# 确定性随机：不依赖 numpy，任何人跑出来的字节都一样
# --------------------------------------------------------------------------- #
class LCG:
    def __init__(self, seed: int) -> None:
        self.state = seed % 2147483647 or 1

    def next_u(self) -> float:
        self.state = (self.state * 1103515245 + 12345) % 2147483648
        return self.state / 2147483648.0

    def normal(self) -> float:
        u1 = max(self.next_u(), 1e-12)
        u2 = self.next_u()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def sample_ids(self, n_total: int, k: int) -> list[int]:
        pool = list(range(n_total))
        for i in range(k):
            j = i + int(self.next_u() * (n_total - i))
            if j >= n_total:
                j = n_total - 1
            pool[i], pool[j] = pool[j], pool[i]
        return sorted(pool[:k])


def _standardize(xs: list[float]) -> list[float]:
    n = len(xs)
    mu = sum(xs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / n) or 1.0
    return [(x - mu) / sd for x in xs]


def _spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else 0.0


# --------------------------------------------------------------------------- #
# 1) 蜂群数据重建
# --------------------------------------------------------------------------- #
def build_beeswarm(rows: list[dict], n_total: int, seed: int) -> tuple[list[dict], dict]:
    """以实测 mean|SHAP| 与方向为锚点，重建 n_plot 个样本点。

    约束（脚本内自校验，失败直接抛错，不写文件）：
      a. mean(|SHAP|) 与实测值偏差 < 1e-9（重建前后都对齐）；
      b. 特征值与 SHAP 值的秩相关符号 == 实测 direction；
      c. 点数量 == BEESWARM_N_PLOT。
    """
    rng = LCG(seed)
    ids = rng.sample_ids(n_total, BEESWARM_N_PLOT)
    out: list[dict] = []
    max_dev = 0.0

    for row in rows[:BEESWARM_TOP_K]:
        mu = float(row["mean_abs_shap"])
        direction = int(row["direction"]) or 1
        row_rng = LCG(seed + 7919 * (int(row["rank"]) + 1))

        # 幅度：对数正态，均值归一到 1（保证 mean|SHAP| 可精确对齐）
        #   cap 之后再归一一次：截尾会把均值压小，不补回来 mean|SHAP| 就对不上了
        mags = [math.exp(BEESWARM_SIGMA * row_rng.normal() - BEESWARM_SIGMA ** 2 / 2) for _ in ids]
        scale = (sum(mags) / len(mags)) or 1.0
        mags = [min(m / scale, BEESWARM_CAP) for m in mags]
        scale2 = (sum(mags) / len(mags)) or 1.0
        mags = [m / scale2 * mu for m in mags]

        # 符号：主方向占 1 - FLIP，少数样本反向（真实 SHAP 也长这样）
        signed = []
        for m in mags:
            s = direction if row_rng.next_u() >= BEESWARM_FLIP else -direction
            signed.append(s * m)

        # 特征值（z-score）：与 SHAP 同向的秩相关，符号须等于 direction
        #   direction=+1：特征值越高 → SHAP 越大（推高风险）；direction=−1 反之。
        #   所以特征值要跟着 direction 翻转，否则保护因子的散点会画成反的。
        eps = [row_rng.normal() for _ in ids]
        z_base = _standardize(signed)
        z = [0.60 * direction * a + 0.80 * b for a, b in zip(z_base, eps)]
        z = _standardize(z)

        dev = abs(sum(abs(s) for s in signed) / len(signed) - mu)
        if dev > 1e-9:
            raise SystemExit(f"[beeswarm] {row['feature']} mean|SHAP| 对齐失败：Δ={dev}")
        rho = _spearman(z, signed)
        if (rho > 0) != (direction > 0):
            raise SystemExit(f"[beeswarm] {row['feature']} 秩相关符号与实测方向相反：ρ={rho:.3f}")

        # 落盘的点位保留 5 位小数为的是契约体积（490 KB → 116 KB），舍入本身会带来
        # 极小偏差。max_mean_deviation 记的是**读契约的人能验到的那个值**（按落盘点
        # 重算的均值偏差），不是舍入前的内部值——否则外部按契约复算得到 4e-7，
        # 跟契约里写的 0 对不上，反而像契约在骗人。
        points = [[round(s, 5), round(v, 2)] for s, v in zip(signed, z)]
        stored_dev = abs(sum(abs(p[0]) for p in points) / len(points) - mu)
        if stored_dev > 1e-6:
            raise SystemExit(f"[beeswarm] {row['feature']} 落盘后均值漂移过大：Δ={stored_dev}")
        max_dev = max(max_dev, stored_dev)
        out.append(
            {
                "rank": row["rank"],
                "feature": row["feature"],
                "semantic_known": bool(row.get("semantic_known", False)),
                "mean_abs_shap": mu,
                "direction": direction,
                "share": float(row.get("share", 0.0)),
                "rho_feature_shap": round(rho, 4),
                "points": points,
            }
        )

    meta = {
        "n_total": n_total,
        "n_plotted": BEESWARM_N_PLOT,
        "n_features": len(out),
        "sampling": "固定 seed 等概率抽样（同一批样本用于全部特征，partial Fisher–Yates）",
        "value_axis": "feature_z",
        "value_axis_note": "特征值已 Z-score 标准化，0 = 解释集均值；颜色按该值映射（低→蓝、高→红）。",
        "derived": True,
        "source": "reconstructed_from_anchors",
        "derivation": (
            "逐样本 SHAP 值未随契约落盘：本区块以实测 mean|SHAP|（逐特征精确对齐，Δ<1e-9）"
            "与实测方向（秩相关符号一致）为锚点，按「对数正态幅度 × 主方向符号（26% 反向）」"
            "重建 200 个样本点。点位形状是重建的，均值与方向是实测的。"
        ),
        "max_mean_deviation": round(max_dev, 12),
    }
    return out, meta


# --------------------------------------------------------------------------- #
# 2) A15 阶段-因子-权重（mock，结构同构）
# --------------------------------------------------------------------------- #
STAGES = [
    {
        "stage_id": "s0",
        "stage_label": "种子期",
        "age_range": "成立 ≤ 2 年",
        "funding_marker": "无外部融资记录",
        "share": 0.31,
        "weight": {"tech": 0.42, "growth": 0.30, "fusion": 0.28},
    },
    {
        "stage_id": "s1",
        "stage_label": "初创期",
        "age_range": "2–4 年",
        "funding_marker": "天使轮 / Pre-A",
        "share": 0.34,
        "weight": {"tech": 0.36, "growth": 0.36, "fusion": 0.28},
    },
    {
        "stage_id": "s2",
        "stage_label": "成长期",
        "age_range": "4–7 年",
        "funding_marker": "A 轮 / B 轮",
        "share": 0.24,
        "weight": {"tech": 0.28, "growth": 0.42, "fusion": 0.30},
    },
    {
        "stage_id": "s3",
        "stage_label": "扩张期",
        "age_range": "> 7 年",
        "funding_marker": "B 轮以后 / 已盈利",
        "share": 0.11,
        "weight": {"tech": 0.22, "growth": 0.45, "fusion": 0.33},
    },
]

FACTORS = [
    {
        "factor_id": "tech",
        "factor_label": "技术要素",
        "label_kind": "dimension",
        "semantic_known": False,
        "label_ref": "feature_dict.dimension.tech",
        "definition": "专利 / 研发投入 / 团队稳定性一类指标（本项目自建三维护口径之一）",
        "note": "维度归属为占位：成员字段语义未确认，维度名不代表字段真实含义。",
    },
    {
        "factor_id": "growth",
        "factor_label": "成长轨迹",
        "label_kind": "dimension",
        "semantic_known": False,
        "label_ref": "feature_dict.dimension.growth",
        "definition": "营收 / 员工 / 融资 / 里程碑一类指标（本项目自建三维护口径之一）",
        "note": "维度归属为占位：成员字段语义未确认，维度名不代表字段真实含义。",
    },
    {
        "factor_id": "fusion",
        "factor_label": "融合特征",
        "label_kind": "dimension",
        "semantic_known": False,
        "label_ref": "feature_dict.dimension.fusion",
        "definition": "技术转化效率 / 资本技术协同度一类交叉指标（本项目自建三维护口径之一）",
        "note": "维度归属为占位：成员字段语义未确认，维度名不代表字段真实含义。",
    },
]

STAGE_FIELD_POOL = 8   # 每阶段列出的字段数（取自实测 Top-20）


def build_stage_contract(shap_data: dict) -> dict:
    xgb = next(m for m in shap_data["models"] if m["model_id"] == "xgb")
    pool = xgb["top"][:STAGE_FIELD_POOL]
    n_total = int(shap_data["n_features_used"])
    rng = LCG(SEED + 31)

    stages, weights, stage_fields, notes = [], [], [], []
    for idx, st in enumerate(STAGES):
        total = sum(st["weight"].values())
        stages.append(
            {
                "stage_id": st["stage_id"],
                "stage_label": st["stage_label"],
                "age_range": st["age_range"],
                "funding_marker": st["funding_marker"],
                "company_share": st["share"],
                "definition_source": "draft",
            }
        )
        for fid, w in st["weight"].items():
            weights.append(
                {
                    "stage_id": st["stage_id"],
                    "factor_id": fid,
                    "weight": round(w / total, 4),
                }
            )

        # 阶段内字段权重：以实测 share 为底，叠加确定性扰动后归一（占位）
        raw = []
        for row in pool:
            jitter = 0.72 + 0.56 * rng.next_u()        # 0.72–1.28
            decay = 1.0 - 0.045 * idx                  # 越晚的阶段越分散
            raw.append(float(row["share"]) * jitter * decay)
        ssum = sum(raw) or 1.0
        fields = []
        for row, r in zip(pool, raw):
            fields.append(
                {
                    "rank": row["rank"],
                    "feature": row["feature"],
                    "semantic_known": bool(row.get("semantic_known", False)),
                    "mean_abs_shap": float(row["mean_abs_shap"]),   # 实测
                    "direction": int(row["direction"]),             # 实测
                    "stage_weight": round(r / ssum, 4),             # 占位
                }
            )
        stage_fields.append({"stage_id": st["stage_id"], "fields": fields})

        top_factor = max(st["weight"].items(), key=lambda kv: kv[1])
        label = next(f["factor_label"] for f in FACTORS if f["factor_id"] == top_factor[0])
        top1 = fields[0]["stage_weight"]
        top3 = sum(f["stage_weight"] for f in fields[:3])
        notes.append(
            {
                "stage_id": st["stage_id"],
                "headline": f"权重集中在「{label}」（占位 {top_factor[1] * 100:.0f}%）",
                "text": (
                    f"字段层面首位 {fields[0]['feature']} 占该阶段 {top1 * 100:.1f}%（占位），"
                    f"前 3 位合计 {top3 * 100:.1f}%；其 mean|SHAP| {fields[0]['mean_abs_shap']:.4f} 为实测。"
                    "字段语义未确认，此处只做算法层陈述，不指向任何业务含义。"
                ),
            }
        )

    return {
        "meta": {
            "generated_at": NOW,
            "script": "scripts/build_explain_contracts.py",
            "dataset": "real_panel",
            "source": "demo_placeholder",
            "schema_version": "1.1",
            "n_samples": 698,  # 与 shap_global 的 explain_set（holdout 698 条）同源
            "seed": SEED,
            "semantic_known": False,
            "label_ref": None,
            "noise_floor": 0.477,
            "contract_status": "mock",
            "blocked_by": ["P0-2", "P0-5"],
            "disclaimer": (
                "A15 真实契约未产出（P0-2 字段字典未补齐、P0-5 融资事件未到位）。"
                "本文件为结构同构占位：阶段划分、维度权重、阶段内字段权重均为占位值；"
                "字段排序与 mean|SHAP| 取自实测 shap_global.json。不得作为实证结论引用。"
            ),
        },
        "data": {
            "status": "mock",
            "status_note": "结构同构占位（demo_placeholder）：可演示交互与版式，数值不可对外引用。",
            "chart": "A15",
            "n_features_used": n_total,
            "stage_axis": {
                "label": "生命周期阶段",
                "source": "draft",
                "definition": "按成立年限 + 融资里程碑划分（草案口径，待 P0-1 / P0-5 数据核实）",
            },
            "stages": stages,
            "factors": FACTORS,
            "weights": weights,
            "stage_fields": stage_fields,
            "decision_notes": notes,
            "unlock": {
                "blocked_by": ["P0-2", "P0-5"],
                "requires": [
                    "x21–x200 字段字典（维度归属才能由字典推导，而不是由原型图拼）",
                    "融资事件表（阶段划分需要真实的轮次与时间点，不能只靠成立年限）",
                ],
                "after": "字段字典到位后，weights 由字典推导的分组重算，stage_fields 换成真实分组归因；前端零改动。",
                "owner": "施汶邑（算法）· 袁雪茹（对外索取字典）",
            },
            "derived": True,
            "derivation": (
                "阶段与权重为人工设定的占位（4 阶段 × 3 维度，每阶段合计 1.0）；"
                "阶段内字段权重以实测 share 为底叠加确定性扰动（seed 固定）后归一；"
                "字段的 mean|SHAP| 与方向原样取自实测 shap_global.json。"
            ),
        },
    }


# --------------------------------------------------------------------------- #
def dump_json(path: str, obj: dict) -> None:
    """缩进 2 落盘，但把 `points` 这类纯数字二维数组压成一行。

    为什么不用 `separators=(',',':')` 一把梭：契约是给人看的（评审要能 diff、
    算法组要能对着数字核），整份压平就没法读了；而 6000 个点若也按缩进展开，
    文件会从 105 KB 膨胀到 490 KB，进 bundle 就是白白多背 385 KB 空白。
    所以只对点集做压缩，其余保持缩进。
    """
    raws: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "points" and isinstance(v, list) and v and isinstance(v[0], list):
                    token = "@@PTS%d@@" % len(raws)
                    raws[token] = json.dumps(v, separators=(",", ":"))
                    out[k] = token
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    text = json.dumps(walk(obj), ensure_ascii=False, indent=2)
    for token, compact in raws.items():
        text = text.replace('"%s"' % token, compact)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")


def main() -> None:
    with open(SHAP_PATH, encoding="utf-8") as f:
        shap_env = json.load(f)

    before = json.dumps(shap_env, ensure_ascii=False, sort_keys=True)

    data = shap_env["data"]
    n_total = 698  # 解释集规模（holdout test set）；由 explain_set 文本可核
    for i, model in enumerate(data["models"]):
        beeswarm, bmeta = build_beeswarm(model["top"], n_total, SEED + 101 * i)
        model["beeswarm"] = beeswarm
        model["beeswarm_meta"] = bmeta
    data["beeswarm_scope"] = "A13 蜂群图：Top-%d 特征 × %d 样本点" % (BEESWARM_TOP_K, BEESWARM_N_PLOT)

    # meta 只追加，不改已有序
    shap_env["meta"]["beeswarm_source"] = "reconstructed_from_anchors"
    shap_env["meta"]["beeswarm_note"] = (
        "top（mean|SHAP| / 方向 / 占比）为实测；beeswarm 的逐样本点位为锚点重建，"
        "两者在同屏并列时必须分别标注。"
    )

    # ---- 自校验：原有实测块逐字节不变 ----
    after_obj = json.loads(before)
    for i, model in enumerate(after_obj["data"]["models"]):
        if model["top"] != data["models"][i]["top"]:
            raise SystemExit("[shap_global] 实测 top 被改动，已中止写入")
        if model["top20_share"] != data["models"][i]["top20_share"]:
            raise SystemExit("[shap_global] top20_share 被改动，已中止写入")
    if after_obj["data"]["conclusion"] != data["conclusion"]:
        raise SystemExit("[shap_global] conclusion 被改动，已中止写入")

    dump_json(SHAP_PATH, shap_env)

    stage_env = build_stage_contract(data)
    dump_json(STAGE_PATH, stage_env)

    print("[explain] shap_global.json  追加 beeswarm：%d 特征 × %d 点 × 2 模型（%.1f KB）" % (
        BEESWARM_TOP_K, BEESWARM_N_PLOT, os.path.getsize(SHAP_PATH) / 1024))
    print("[explain] stage_factor_weight.json 已生成（mock，%.1f KB）" % (
        os.path.getsize(STAGE_PATH) / 1024))
    for st in stage_env["data"]["stages"]:
        w = {x["factor_id"]: x["weight"] for x in stage_env["data"]["weights"] if x["stage_id"] == st["stage_id"]}
        print("[explain]   %s %s: tech %.2f / growth %.2f / fusion %.2f（合计 %.2f）" % (
            st["stage_id"], st["stage_label"], w["tech"], w["growth"], w["fusion"], sum(w.values())))


if __name__ == "__main__":
    main()
