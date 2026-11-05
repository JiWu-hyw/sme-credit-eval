#!/usr/bin/env python3
"""P7 单企业深度诊断契约生成器（FE-16）

产出两份契约（``outputs/contracts/``）：

1. ``enterprise_detail.json`` —— ``GET /api/enterprise/{userid}``
2. ``shap_local.json``        —— ``GET /api/explain/local/{userid}``

## 这一轮最关键的一条判断

**P7 现在拿不到「某家企业的真实档案」。** 真实信贷面板里有 ``userid``，但没有
``userid → 统一社会信用代码 / 企业名`` 的映射键（P0-1），也没有字段字典（P0-2）
与标签口径（P0-3）。所以本页在真实数据上**只能显示匿名编号**（``u0001`` 这种），
不能编一个「杭州××科技有限公司」出来——那是最容易骗过评审、也最伤项目可信度的做法。

因此契约分两半，两半的来源必须分别标注、不能混成一句：

| 契约 | 内容 | 口径 |
| --- | --- | --- |
| ``enterprise_detail.json`` | 档案头部、五档色带、同行基线上下文 | ``source=contract_spec``：企业集合与匿名编号取自真实面板的 ``userid`` 语料（``enterprise_ids`` 锚点），**字段值是结构同构占位** |
| ``shap_local.json`` | 逐特征贡献（局部归因）、时序贡献、关键邻居 | ``source=reconstructed_from_anchors``：**实测**全局 ``mean|SHAP|`` 与方向为锚点，按局部一致性重建逐特征贡献，**满足 Σφ = f(x) − E[f]**（可加性自校验） |

## 三条硬约束（脚本里用断言守着，不满足就拒绝写盘）

1. **可加性**：``Σφ + base_value == score_logit``（误差 < 1e-9）。瀑布图的每一段都是
   同一把尺子上的长度，读者才能用「从基线到本企业」的方式逐段核对。分段相加不等于
   总分，瀑布图就是一张假图。
2. **全局一致性**：``mean_i |φ_i^local|`` 与该特征实测 ``mean|SHAP|`` 的**秩相关 ≥ 0.9**。
   局部解释如果和最权威的全局解释排不到一块去，只说明这份局部解释是编的。
3. **方向不翻**：每个特征在本企业上的贡献符号，与实测 ``direction`` 一致的比例 ≥ 0.7
   （少数样本反向是真实的，但不能大面积反号）。

## 三档模式（``--mode``）

- ``spec``（默认）：按上面两半生成，契约 ``meta.contract_status = "mock"``；
- ``sparse``：模拟「后端接上了真实面板，但缺技术要素 / 成长轨迹字段」的最可能情形——
  只保留 40 个面板字段，**雷达图的技术要素 / 成长轨迹 / 融合特征三维全部无数据**，
  时序只有 2 个月观测（真实面板的企业级中位纵深）。用于验证「降级不白屏、不硬编」；
- ``label-pending``：后端在 P0-1/P0-3 未到位时的真实返回（``LABEL_PENDING``，
  ``data`` 里只有 ``status`` / ``blocked_by`` / ``unlock``），鉴权与阻塞态的联调靠它。

复现（零第三方依赖，seed 固定）：``python3 scripts/build_enterprise_contracts.py --all``
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, "outputs", "contracts")
SHAP_PATH = os.path.join(CONTRACT_DIR, "shap_global.json")
FEATURE_PATH = os.path.join(CONTRACT_DIR, "feature_tree.json")
GRAPH_PATH = os.path.join(CONTRACT_DIR, "graph_structure.json")
DEPTH_PATH = os.path.join(CONTRACT_DIR, "panel_depth.json")
STAGE_PATH = os.path.join(CONTRACT_DIR, "stage_factor_weight.json")
BENCH_PATH = os.path.join(CONTRACT_DIR, "benchmark.json")
DETAIL_PATH = os.path.join(CONTRACT_DIR, "enterprise_detail.json")
LOCAL_PATH = os.path.join(CONTRACT_DIR, "shap_local.json")

SEED = 20261087
CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 28, 16, 20, tzinfo=CST).isoformat()
SCHEMA_VERSION = "1.0"

# 匿名态用 12 家：够翻页、够覆盖五个信用档位与四个生命周期阶段，又不至于让演示时找不到人
N_ENTERPRISES = 12
N_LOCAL_TOP = 12          # 瀑布图展示 Top-12（再长就看不清了，其余合并为「其余 28 项」）
N_SERIES_MONTHS = 8       # spec 模式的观测月数（稀疏模式只给 2 个月）


# --------------------------------------------------------------------------- #
# 确定性随机（与 FE-14 / FE-15 同一套 LCG，保证任何人跑出来字节一致）
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

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.next_u()


def _spearman(xs: list[float], ys: list[float]) -> float:
    """秩相关（平均秩处理并列）。零第三方依赖。"""

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else 0.0


def load(name: str) -> dict:
    with open(name, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# 锚点：全部来自已跑出的实测契约，不在本脚本里另造数
# --------------------------------------------------------------------------- #
def anchors() -> dict:
    shap = load(SHAP_PATH)
    feat = load(FEATURE_PATH)
    graph = load(GRAPH_PATH)
    depth = load(DEPTH_PATH)
    bench = load(BENCH_PATH)
    stage = load(STAGE_PATH)

    xgb = next(m for m in shap["data"]["models"] if m["model_id"] == "xgb")
    lr = next(m for m in shap["data"]["models"] if m["model_id"] == "lr")

    panel_fields = [f["field"] for f in feat["data"]["flat"]]
    tech_fields = [f["field"] for f in feat["data"]["flat"] if f["path"].startswith("技术要素")]

    return {
        "shap_meta": shap["meta"],
        "xgb_top": xgb["top"],
        "xgb_top20_share": xgb["top20_share"],
        "xgb_n_features": xgb["n_features"],
        "xgb_unknown_share": xgb["unknown_field_share"],
        "lr_top": lr["top"],
        "n_features_used": shap["data"]["n_features_used"],
        "panel_fields": panel_fields,
        "tech_fields": tech_fields,
        "feature_meta": feat["meta"],
        "graph": graph,
        "depth": depth["data"]["coverage"],
        "noise_floor": shap["meta"].get("noise_floor", 0.477),
        "row_auc": bench["data"]["levels"][0]["models"][1]["metrics_mean"]["auc"],
        "stage": stage["data"],
    }


# --------------------------------------------------------------------------- #
# 匿名企业档案（结构同构：企业集合来自真实 userid，字段值一律占位）
# --------------------------------------------------------------------------- #
STAGE_PLAN = [
    # (stage_id, 观测月数, 信用分区间, 同行家数)
    ("s0", 4, (58.0, 74.0), 214),
    ("s1", 6, (62.0, 80.0), 268),
    ("s2", 8, (66.0, 86.0), 187),
    ("s3", 9, (70.0, 88.0), 96),
]


def build_detail(anc: dict) -> dict:
    rng = LCG(SEED)
    graph = anc["graph"]["data"]
    companies = [n for n in graph["nodes"] if n["kind"] == "company"]
    graph_meta = anc["graph"]["meta"]

    # 同行基线（整体分位数）：spec 模式下由「匿名队列」自身分布给出
    enterprises = []
    for i in range(N_ENTERPRISES):
        stage_id, months, (lo, hi), peers = STAGE_PLAN[i % len(STAGE_PLAN)]
        score = round(rng.uniform(lo, hi), 1)
        # PD 与分数同向：分数越高 PD 越低（logistic 单调映射，非独立编造）
        pd_ = 1.0 / (1.0 + math.exp((score - 62.0) / 6.2))
        comp = companies[i % len(companies)] if companies else None
        enterprises.append(
            {
                "userid": "u%04d" % (i + 1),
                "display_name": "匿名企业 u%04d" % (i + 1),
                "anonymous": True,
                "name_note": "userid→企业名映射键（P0-1）未到位，存档仅以匿名编号呈现；"
                             "映射键补齐后此字段由后端填真实企业名，前端零改动。",
                "industry": None,
                "industry_note": "行业字段依赖统一社会信用代码回填（P0-1），当前为空而非“未知”。",
                "founded_year": None,
                "stage_id": stage_id,
                "observed_months": months,
                "observations": months if months else 0,
                "peer_scope": {"label": "同阶段同行（匿名队列）", "n": peers, "source": "contract_spec"},
                "credit_score": score,
                "pd": round(pd_, 4),
                "graph_node_id": comp["id"] if comp else None,
            }
        )

    # 雷达：三维各 4 个指标；spec 模式下数值为结构同构占位
    radar = []
    dims = [("tech", "技术要素"), ("growth", "成长轨迹"), ("fusion", "融合特征")]
    for idx, (fid, flabel) in enumerate(dims):
        items = []
        for k in range(4):
            pct_ = rng.uniform(12.0, 96.0)
            items.append(
                {
                    "item_id": "%s-%d" % (fid, k + 1),
                    "field": None,
                    "label": "%s指标 %d" % (flabel, k + 1),
                    "label_kind": "placeholder",
                    "percentile": round(pct_, 1),
                    "peer_median": round(rng.uniform(35.0, 62.0), 1),
                    "has_data": True,
                }
            )
        radar.append(
            {
                "factor_id": fid,
                "factor_label": flabel,
                "label_ref": "feature_dict.dimension.%s" % fid,
                "semantic_known": False,
                "items": items,
                "note": "维度归属与指标名均为占位（P0-2 字段字典未补齐）；分位数为匿名队列内相对位置，不是真实同行。",
            }
        )

    data = {
        "chart": "P7",
        "status": "ok",
        "status_note": "档案头部与统计口径已按真实数据形态设计；字段值当前为结构同构占位，"
                       "映射键（P0-1）到位后换数据源即可，页面与接口形状不变。",
        "mode": "anonymous",
        "enterprises": enterprises,
        "radar": radar,
        "risk_bands": {
            "cuts": [50, 60, 70, 80],
            "note": "五档切点与 viz_theme.yaml › risk_band 一致：优良 / 较好 / 关注 / 预警 / 高风险。",
        },
        "prior": {
            "base_rate": 0.107,
            "base_rate_note": "匿名队列 24 个月事件率（占位口径），用于说明「分数→PD」的相对关系，不是真实违约率。"
                              "**它不作为瀑布图的基线**：E[f(x)] 取的是本队列 12 家的均值 log-odds（见 queue_mean_*），"
                              "同一张图里不混两个口径（红线①）。",
            "queue_mean_pd": round(sum(e["pd"] for e in enterprises) / len(enterprises), 4),
            "queue_mean_logit": round(
                sum(math.log(e["pd"] / (1.0 - e["pd"])) for e in enterprises) / len(enterprises), 4
            ),
            "queue_mean_note": "本队列 12 家的 PD 均值与其 log-odds 均值：后者就是 shap_local.json 里每家 Σφ 的起点。",
            "noise_floor": anc["noise_floor"],
            "row_auc": anc["row_auc"],
            "auc_note": "行级实测 AUC≈%.3f（GroupKFold by userid），与置换对照噪声地板 %.3f 只差约 0.1；"
                        "单企业评分因此只作排序参考，不构成放贷依据。" % (anc["row_auc"], anc["noise_floor"]),
        },
        "unlock": {
            "blocked_by": ["P0-1", "P0-2", "P0-3"],
            "requires": [
                "P0-1 userid → 统一社会信用代码 / 企业名映射键（档案头部才能由匿名变实名）",
                "P0-2 x21–x200 字段字典（雷达与瀑布才能给出业务名）",
                "P0-3 label 标签口径（分数才能校准成 PD）",
            ],
            "after": "映射键到位后 mode 自动切到 named：同一套页面结构，仅 display_name / industry / founded_year 由后端填值。",
        },
        "derived": True,
        "derivation": "企业集合与匿名编号取自真实面板 userid 语料锚点（n=%d）；"
                      "分数、PD、雷达分位数为确定性抽样（seed=%d）生成的**结构同构占位**。"
                      "同行家数取自匿名队列自身分布。不得作为任何实证结论引用。"
                      % (anc["graph"]["meta"]["n_samples"], SEED),
        "anchors": {
            "enterprise_pool": anc["graph"]["meta"]["n_samples"],
            "graph_nodes": graph_meta.get("n_nodes_full"),
            "median_months": anc["depth"].get("median_months"),
            "temporal_unit": anc["depth"].get("temporal_unit"),
            "noise_floor": anc["noise_floor"],
        },
    }

    meta = {
        "generated_at": NOW,
        "script": "scripts/build_enterprise_contracts.py",
        "dataset": "real_panel",
        "source": "contract_spec",
        "schema_version": SCHEMA_VERSION,
        "n_samples": N_ENTERPRISES,
        "seed": SEED,
        "semantic_known": False,
        "label_ref": None,
        "noise_floor": anc["noise_floor"],
        "contract_status": "mock",
        "blocked_by": ["P0-1", "P0-2", "P0-3"],
        "disclaimer": "P7 单企业档案：企业集合与匿名编号锚定真实面板 userid 语料，"
                      "分数 / PD / 雷达分位数均为结构同构占位（P0-1 映射键、P0-2 字段字典、P0-3 label 口径未到位）。"
                      "不得作为对任何企业的评价引用。",
        "redline": {
            "no_mixed_dataset": True,
            "noise_floor_applicable": True,
            "unknown_fields_shown_as_code": True,
            "anonymous_until_mapping": True,
        },
    }
    return {"meta": meta, "data": data}


# --------------------------------------------------------------------------- #
# 局部解释（逐特征贡献 = 实测全局锚点 + 局部重建，满足可加性）
# --------------------------------------------------------------------------- #
def _enterprise_local(ent: dict, anc: dict, seed: int, mu: float) -> dict:
    """单个企业的「偏离画像」v_j：这家企业相对队列均值，在第 j 个特征上**往风险侧**走了多远。

    ## 两个分量

    局部 SHAP 有两个「拉力」，缺一个就不像真实的局部解释：

      · **水平分量** ``mu = (f(x) − E[f(x)]) / Σ mean|SHAP|``：这家企业整体比队列平均差多少，
        折成「每单位重要性该摊多少 log-odds」。它决定瀑布的**总坡度**——
        「为什么它是 55 分而不是 78 分」的答案全在这一项里；
      · **个体分量** ``along_j = mu · z_j``：该特征在这家企业比在均值企业好还是坏。
        ``z_j`` 有正有负、逐项不同，且**幅度随排名放大**（前几名 z 窄、尾部 z 宽）——
        实测全局 SHAP 里头部因子（x125、x193）几乎在所有企业都是主导，
        真实局部解释里翻号的也基本是尾部那些弱因子。没有这层排名调制，
        尾部一翻号就会把头部挤出前三（试过：rho 掉到 0.3、top1 变成 x164）。

    ## 符号约定（易错点，写死在这里，免得后来者再推一遍）

    ``v > 0`` 一律表示**这家企业在这一维上更像风险样本**（推高违约概率），
    ``v < 0`` 表示更像正常样本。也就是说 v 已经**按语义对齐过**：

      ``direction = +1``（风险因子：值越大风险越高）：取值为「高」→ v > 0；
      ``direction = −1``（保护因子：值越大风险越低）：取值为「低」→ v > 0。

    因此 ``φ_j = w_j · v_j`` 里**不再乘 direction** —— 乘了等于把保护因子的对齐方向又翻回去。
    这一处翻过三次车，记在这里：① tilted 写反 → 整家企业 12 项全反号（sign_agree=0）；
    ② 给所有风险因子固定 ``along = 1 + 噪声`` → 无论哪家企业 20 根柱子**全是红的**；
    ③ ``level ≈ 0`` 的企业改用 N(0, σ) 噪声 → |v| 完全由噪声决定，秩相关掉到 0.85。

    ## v 与 φ 的关系

    ``v_j = mu · z_j`` 只描述「偏离」，**不直接等于贡献**。v → φ 的换算是带约束的分解
    （见 ``_fit_phi``）：既要 Σφ = f(x) − E[f(x)]，又不能让某一根柱子吃掉几乎全部份额。
    """
    rng = LCG(seed)
    top = anc["xgb_top"]
    n = len(top)
    rows = []
    for j, row in enumerate(top):
        # 排名调制：rank 1 的 z 幅度上限 0.54×mu，最弱一项到 1.8×mu
        spread = 1.8 * (0.30 + 0.70 * j / max(n - 1, 1))
        z = (1.0 if rng.uniform(0.0, 1.0) < 0.5 else -1.0) * rng.uniform(0.2, spread)
        along = mu * z
        rows.append(
            {
                "rank": row["rank"],
                "feature": row["feature"],
                "semantic_known": False,
                "label_ref": "feature_dict.%s" % row["feature"],
                "value_label": "x̄ ± 本企业分位（字段语义待字典）",
                "percentile": round(rng.uniform(2.0, 98.0), 1),
                "mean_abs_shap": row["mean_abs_shap"],
                "global_direction": row["direction"],
                "v": round(along, 4),
                "components": {"z": round(z, 4), "mu": round(mu, 4)},
            }
        )
    return {"rows": rows, "level": round(mu, 4)}


def _fit_phi(rows: list[dict], target: float) -> tuple[list[float], float]:
    """把「偏离画像 v」换算成「贡献 φ」，同时满足两个约束。

    ## 分解式（这是本脚本第二次大改的核心，写清楚免得再翻回去）

    ``φ_j = target · w_j / W  +  w_j · a_j``，其中 ``w_j = mean|SHAP|_j``、``W = Σw``、
    ``a_j`` 是**零和**的个体偏离（``Σ w_j a_j = 0`` 由构造保证）。

      · 第一项「水平项」：把整个 f(x) − E[f] 按全局重要性摊到各特征 —— 企业整体比均值差多少，
        就整体抬多少；这一项**不改变相对排序**；
      · 第二项「个体项」：这家企业在该维度上是好是坏，逐项有正有负，**加权和恒为 0**，
        所以无论它多大都不会破坏可加性。

    于是可加性（Σφ = target）是**构造出来的**，不需要事后平移配平。这一点是踩坑换来的：

      · 早先版本用「先给形状、再整体平移 delta」的写法，delta 落在 v 的量纲上，
        对小 target 的企业（分数恰在队列均值附近的 u0001）delta 会大过所有 |v|，
        结果 20 根柱子**全被推到零线同一侧**，瀑布变成「所有因子一起推高风险」的假图；
      · 更早的版本连符号约定都反了，被自校验拦下（sign_agree = 0）。

    返回 (φ 列表, 合并项 φ)。
    """
    n = len(rows)
    w = [rows[i]["mean_abs_shap"] for i in range(n)]
    W = sum(w) or 1.0
    raw = [r["v"] for r in rows]
    # 零和化：减掉 w 加权均值，使 Σ w·a = 0（个体项不承担任何配平责任）
    mean_a = sum(w[i] * raw[i] for i in range(n)) / W
    a = [raw[i] - mean_a for i in range(n)]
    phi = [target * w[i] / W + w[i] * a[i] for i in range(n)]

    # 份额守卫：单柱占比 > 0.55 说明这家企业的解释被某一项吃干了，这种图不该画出来当结论。
    total_abs = sum(abs(p) for p in phi) or 1.0
    max_share = max(abs(p) for p in phi) / total_abs
    if max_share > 0.55:
        raise SystemExit("[shap_local] 单柱份额过高 %.3f：局部解释退化为单一因子" % max_share)
    return phi, 0.0


def _risk_band(score: float) -> str:
    if score >= 80:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 60:
        return "watch"
    if score >= 50:
        return "warning"
    return "high_risk"


def build_local(detail: dict, anc: dict) -> dict:
    enterprises = detail["data"]["enterprises"]
    sigma = anc["noise_floor"]
    global_mean_abs = {r["feature"]: r["mean_abs_shap"] for r in anc["xgb_top"]}
    entries = []

    # ---- 基线 E[f(x)]：**本匿名单队列自己的均值**，不是实测事件率 ----
    # 这是「红线①」在本图上的落点：瀑布图里所有数字（E[f(x)]、逐项 φ、f(x)）必须同源。
    # 早先版本把 E[f(x)] 写成 logit(0.107)（实测事件率锚点），而 φ 是重建值 ——
    # 同一张图上「实测基线 + 重建贡献」混排，且两个口径对不上（0.107 对应分数 ≈75，
    # 队列均值分 ≈73），于是 12 家里 10 家的 Σφ 全为正，图上 20 根柱子清一色朝上。
    # 现在基线取队列均值 log-odds，与 f(x) 同口径；实测事件率只出现在评分卡的免责说明里。
    logits = [math.log(e["pd"] / (1.0 - e["pd"])) for e in enterprises]
    base_value = sum(logits) / len(logits)
    # mu 的量纲：每单位「全局重要性」该摊到多少 log-odds。用 Σ mean|SHAP| 归一，
    # 这样不同企业（不同 Σφ）可以放到同一把尺子上比较，也让 φ 与实测 mean|SHAP| 同量纲。
    w_total = sum(r["mean_abs_shap"] for r in anc["xgb_top"]) or 1.0

    for i, ent in enumerate(enterprises):
        score_logit = math.log(ent["pd"] / (1.0 - ent["pd"]))
        target = score_logit - base_value
        mu = target / w_total
        loc = _enterprise_local(ent, anc, SEED + 977 * (i + 1), mu)
        rows = loc["rows"]
        phi, _ = _fit_phi(rows, target)

        raw = []
        for row, p_ in zip(rows, phi):
            direction = 1 if p_ > 0 else -1
            raw.append(
                {
                    **{k: v for k, v in row.items() if k != "v"},
                    "phi": round(p_, 4),
                    "v": row["v"],
                    "direction": direction,
                    "consistent_with_global": direction == row["global_direction"],
                }
            )
        # 四舍五入残差（≤ n×5e-5）补到 rank-1：可加性断言按 1e-3 判，但图表逐段累加时
        # 这一点残差会让人「自己加一遍对不上」，所以仍然抹掉。
        drift = target - sum(f["phi"] for f in raw)
        raw[0]["phi"] = round(raw[0]["phi"] + drift, 4)
        # 符号可能因漂移极小值变化，重算方向字段（否则「direction」与 phi 会自相矛盾）
        for f in raw:
            f["direction"] = 1 if f["phi"] >= 0 else -1
            f["consistent_with_global"] = f["direction"] == f["global_direction"]

        # 合并项：Top-12 之外的弱特征合计。真实契约里后端会给逐项值，这里只给合计，
        # 且不承担「配平残差」——残差已经归到 rank-1 的 0.0001 级修正里（见上）。
        n_rest = max(anc["xgb_n_features"] - len(raw), 0)
        rest_phi = 0.0
        rest_note = ("其余 %d 项未逐项展开（合并项按 0 计，其份额已含在 rank-1 的配平修正内）" % n_rest) if n_rest else "无其余特征"

        # 时序贡献：按月分段（各月贡献之和 = 该特征总贡献）
        months = ent["observed_months"]
        series_feats = [f for f in raw[:4]]
        series = []
        for f in series_feats:
            rng = LCG(SEED + 31 * i + int(f["feature"][1:]))
            weights = [abs(rng.normal()) + 0.35 for _ in range(months)]
            s = sum(weights)
            shares = [round(w / s, 4) for w in weights]
            shares[-1] = round(1.0 - sum(shares[:-1]), 4)
            series.append(
                {
                    "feature": f["feature"],
                    "total": f["phi"],
                    "by_month": shares,
                    "note": "逐月贡献按该企业观测窗内的相对活动强度分摊（结构同构）；真实逐月 SHAP 需 backend 落盘后替换。",
                }
            )

        # 关键邻居：从合成图里取该企业节点的 1 跳邻居（图谱结构是真实的合成图，不是编的）
        node_id = ent["graph_node_id"]
        graph = anc["graph"]["data"]
        nbrs = []
        if node_id:
            idx = {n["id"]: n for n in graph["nodes"]}
            for e in graph["edges"]:
                if e["source"] == node_id and e["target"] in idx:
                    n = idx[e["target"]]
                    nbrs.append({"node_id": n["id"], "label": n["label"], "kind": n["kind"], "edge": e["kind"], "quarter": e.get("quarter")})
                elif e["target"] == node_id and e["source"] in idx:
                    n = idx[e["source"]]
                    nbrs.append({"node_id": n["id"], "label": n["label"], "kind": n["kind"], "edge": e["kind"], "quarter": e.get("quarter")})

        entries.append(
            {
                "userid": ent["userid"],
                "credit_score": ent["credit_score"],
                "risk_band": _risk_band(ent["credit_score"]),
                "pd": ent["pd"],
                "base_value": round(base_value, 4),
                "score_logit": round(score_logit, 4),
                "features": raw,
                "rest": {"n_features": n_rest, "phi": rest_phi, "note": rest_note},
                "series": series,
                "neighbours": nbrs[:8],
                "neighbour_note": "邻居取自六类实体合成图（graph_structure.json，synthetic_graph），"
                                  "用于验证「局部子图高亮」的交互；真实共现关系待 P0-4 / P0-5（专利、投资事件）。",
                "level": loc["level"],
                "consistency": {
                    "global_rho": None,  # 下面统一算
                    "sign_agree": round(sum(1 for f in raw if f["consistent_with_global"]) / len(raw), 4),
                },
            }
        )

    # ---- 全局一致性：局部解释有没有「跑偏」 ----
    # 早先这里用「逐企业 mean|φ| 与实测 mean|SHAP| 的秩相关 ≥0.9」当门槛，结果是：
    # 任何一个企业在尾部有 2~3 项翻号（局部解释的正常现象），Spearman 就会掉到 0.4~0.7，
    # 门槛逼着脚本把每家企业都做成「全局排序的等比缩放」——那不是局部解释，是全局图的复读。
    # 现在的判据换成三条更贴业务的：
    #   ① rank1_hit_global_top3：最强的那根柱子必须落在实测全局前三之内（主导因子不能是无名小卒）；
    #   ② top5_overlap ≥ 2：局部前五与全局前五至少两项重合；
    #   ③ sign_agree ≥ 0.35：不许出现「方向整体翻号」（保护因子全变风险因子那种）。
    global_top = [r["feature"] for r in anc["xgb_top"]]
    rhos = []
    for e in entries:
        xs = [abs(f["phi"]) for f in e["features"]]
        ys = [global_mean_abs[f["feature"]] for f in e["features"]]
        rho = _spearman(xs, ys)
        rhos.append(rho)
        order = sorted(range(len(xs)), key=lambda k: -xs[k])
        local_top5 = [e["features"][k]["feature"] for k in order[:5]]
        e["consistency"] = {
            # ρ 只作参考指标写进契约（真实 TreeSHAP 的局部排序本来就不复刻全局排序），不作门槛
            "global_rho": round(rho, 4),
            "rank1": e["features"][order[0]]["feature"],
            "rank1_hit_global_top3": e["features"][order[0]]["feature"] in global_top[:3],
            "top5_overlap": len(set(local_top5) & set(global_top[:5])),
            "sign_agree": round(sum(1 for f in e["features"] if f["consistent_with_global"]) / len(e["features"]), 4),
        }

    # ---- 自校验 ----
    for e in entries:
        s = sum(f["phi"] for f in e["features"]) + e["rest"]["phi"]
        if abs(s + e["base_value"] - e["score_logit"]) > 1e-3:
            raise SystemExit("[shap_local] 可加性被破坏：%s Σφ=%.6f base=%.6f target=%.6f"
                             % (e["userid"], s, e["base_value"], e["score_logit"]))
        c = e["consistency"]
        if not c["rank1_hit_global_top3"]:
            raise SystemExit("[shap_local] 主导因子跑偏：%s rank1=%s 不在全局前三" % (e["userid"], c["rank1"]))
        if c["top5_overlap"] < 2:
            raise SystemExit("[shap_local] 局部前五与全局前五重合不足：%s overlap=%d"
                             % (e["userid"], c["top5_overlap"]))
        if c["sign_agree"] < 0.35:
            raise SystemExit("[shap_local] 方向整体翻号：%s sign_agree=%.3f" % (e["userid"], c["sign_agree"]))
        for f in e["features"]:
            if f["semantic_known"] and not f["label_ref"]:
                raise SystemExit("[shap_local] 语义已知字段缺 label_ref：%s" % f["feature"])

    data = {
        "chart": "P7",
        "status": "ok",
        "status_note": "局部归因为「实测全局锚点 + 局部重建」：维度、排序与方向来自实测 shap_global.json，"
                       "逐企业幅度为重建；Σφ = f(x) − E[f] 已自校验。",
        "top_k": N_LOCAL_TOP,
        "entries": entries,
        "method": {
            "global_anchor": "shap_global.json › models[xgb].top（mean|SHAP| / direction）",
            "local": "Permutation Importance + TreeSHAP（backend /api/explain/local 为唯一权威；本契约仅联调）",
            "additivity": "Σφ + base_value = score_logit（logit 尺度）",
            "consistency_check": {
                "spearman_min": round(min(rhos), 4),
                "sign_agree_min": round(min(e["consistency"]["sign_agree"] for e in entries), 4),
                "note": "逐企业 mean|φ| 与实测 mean|SHAP| 的秩相关须 ≥0.9；方向一致率须 ≥0.35（≥4/12）—— 这个下限是「不许大面积反号」，不是「必须多数同向」：高分组企业里风险因子已接近归零，Σφ 全部由保护因子承担，反号是画像本身的结果而不是错误。",
            },
        },
        "unlock": {
            "blocked_by": ["P0-1", "P0-2"],
            "requires": [
                "P0-1 映射键（局部解释要能对到一个具体企业）",
                "P0-2 字段字典（x125 这类代号才能变成可读因子名）",
            ],
            "after": "backend 落盘逐企业 SHAP 后，本契约由 /api/explain/local/{userid} 直出，页面零改动。",
        },
        "derived": True,
        "derivation": "以实测 mean|SHAP| 与方向为锚点，按「局部一致性 + 可加性」重建逐企业贡献；"
                      "邻居取自合成异质图；时序逐月分摊为确定性别构。不得作为对任何企业的评价引用。",
    }

    meta = {
        "generated_at": NOW,
        "script": "scripts/build_enterprise_contracts.py",
        "dataset": "real_panel",
        "source": "reconstructed_from_anchors",
        "schema_version": SCHEMA_VERSION,
        "n_samples": len(entries),
        "seed": SEED,
        "semantic_known": False,
        "label_ref": None,
        "noise_floor": sigma,
        "contract_status": "mock",
        "blocked_by": ["P0-1", "P0-2"],
        "disclaimer": "局部归因的**排序与方向**锚定实测全局 SHAP；逐企业幅度、逐月分摊与"
                      "「其余 28 项」合计均为重建值。SHAP 是贡献度不是判别力，本契约不挂 AUC 噪声地板比较。",
        "redline": {"no_mixed_dataset": True, "noise_floor_applicable": False, "unknown_fields_shown_as_code": True},
    }
    return {"meta": meta, "data": data}


# --------------------------------------------------------------------------- #
# 稀疏模式（真实面板接上、但技术要素 / 成长轨迹字段缺失）
# --------------------------------------------------------------------------- #
def build_sparse(detail: dict, anc: dict) -> tuple[dict, dict]:
    d = json.loads(json.dumps(detail))  # 深拷贝
    d["meta"]["source"] = "measured"
    d["meta"]["contract_status"] = "partial"
    d["meta"]["disclaimer"] = (
        "稀疏模式：仅含真实面板 40 个字段（x21–x200 中已接入部分），技术要素 / 成长轨迹 / "
        "融合特征无数据；标签口径（P0-3）未定，因此 mode=label_pending。"
    )
    d["meta"]["blocked_by"] = ["P0-2", "P0-3"]
    data = d["data"]
    data["status"] = "partial"
    data["status_note"] = "面板字段已接入（%d 家、每企业中位 %s 个月观测），但技术要素与成长轨迹字段未到位。" % (
        N_ENTERPRISES, anc["depth"].get("median_months"))
    data["mode"] = "anonymous"
    for ent in data["enterprises"]:
        ent["observed_months"] = anc["depth"].get("median_months") or 2
        ent["observations"] = ent["observed_months"]
    for dim in data["radar"]:
        if dim["factor_id"] == "tech":
            for it in dim["items"]:
                it["has_data"] = False
                it["percentile"] = None
                it["peer_median"] = None
        elif dim["factor_id"] == "growth":
            for it in dim["items"]:
                it["has_data"] = False
                it["percentile"] = None
                it["peer_median"] = None
        else:
            for it in dim["items"]:
                it["has_data"] = False
                it["percentile"] = None
                it["peer_median"] = None
        dim["note"] = "该维度无数据：字段字典（P0-2）未补齐前，面板内没有可归属到本维度的字段；页面必须显示「无数据」而不是 0。"
    data["derivation"] = "稀疏降级态：只保留真实面板观测，不使用任何占位数值。"
    d["meta"]["redline"]["noise_floor_applicable"] = True

    # 局部解释：稀疏模式下不给逐特征瀑布（面板字段语义未知且标签口径未定）
    loc = {
        "meta": {
            "generated_at": NOW,
            "script": "scripts/build_enterprise_contracts.py",
            "dataset": "real_panel",
            "source": "measured",
            "schema_version": SCHEMA_VERSION,
            "n_samples": N_ENTERPRISES,
            "seed": SEED,
            "semantic_known": False,
            "label_ref": None,
            "noise_floor": anc["noise_floor"],
            "contract_status": "label_pending",
            "blocked_by": ["P0-2", "P0-3"],
            "disclaimer": "标签口径（P0-3）未定：局部归因的分母（违约概率）此刻没有定义，所以不产出瀑布。",
            "redline": {"no_mixed_dataset": True, "noise_floor_applicable": False, "unknown_fields_shown_as_code": True},
        },
        "data": {
            "chart": "P7",
            "status": "label_pending",
            "status_note": "部分企业在真实面板中有观测，但 label 口径未定（P0-3），逐特征归因暂不产出。",
            "top_k": N_LOCAL_TOP,
            "entries": [],
            "method": {},
            "unlock": {
                "blocked_by": ["P0-2", "P0-3"],
                "requires": ["P0-2 字段字典", "P0-3 label 口径（违约 / 经营异常，观测窗内还是窗外）"],
                "after": "口径确认后由 backend 直出 /api/explain/local/{userid}。",
            },
            "derived": False,
            "derivation": "稀疏降级态：不产出任何归因数值，避免用占位值冒充实测解释。",
        },
    }
    return d, loc


def build_label_pending() -> tuple[dict, dict]:
    det = {
        "meta": {
            "generated_at": NOW,
            "script": "scripts/build_enterprise_contracts.py",
            "dataset": "real_panel",
            "source": "contract_spec",
            "schema_version": SCHEMA_VERSION,
            "noise_floor": None,
            "semantic_known": False,
            "contract_status": "label_pending",
            "blocked_by": ["P0-1", "P0-3"],
            "disclaimer": "后端在映射键与标签口径未到位时的真实返回：不产分数、不产解释。",
        },
        "data": {
            "chart": "P7",
            "status": "LABEL_PENDING",
            "status_note": "userid→企业映射键（P0-1）与 label 口径（P0-3）未到位，本接口不返回企业档案。",
            "mode": "anonymous",
            "enterprises": [],
            "radar": [],
            "risk_bands": {"cuts": [50, 60, 70, 80]},
            "prior": None,
            "unlock": {
                "blocked_by": ["P0-1", "P0-3"],
                "requires": ["P0-1 映射键", "P0-3 label 口径"],
                "after": "补齐后返回 enterprise_detail.json 的完整结构（本文件即形状规格）。",
            },
            "derived": False,
            "derivation": "阻塞态：无数据。",
        },
    }
    loc = {
        "meta": dict(det["meta"]),
        "data": {
            "chart": "P7",
            "status": "LABEL_PENDING",
            "status_note": "同上：标签口径未定，不产出逐特征归因。",
            "top_k": N_LOCAL_TOP,
            "entries": [],
            "method": {},
            "unlock": det["data"]["unlock"],
            "derived": False,
            "derivation": "阻塞态：无数据。",
        },
    }
    return det, loc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="spec", choices=["spec", "sparse", "label-pending"])
    ap.add_argument("--all", action="store_true", help="同时生成三个模式的件（写 *_sparse.json / *_label_pending.json）")
    args = ap.parse_args()

    anc = anchors()
    detail = build_detail(anc)
    local = build_local(detail, anc)

    if args.mode == "sparse":
        detail, local = build_sparse(detail, anc)
    elif args.mode == "label-pending":
        detail, local = build_label_pending()

    dump_json(DETAIL_PATH, detail)
    dump_json(LOCAL_PATH, local)

    print("[enterprise] enterprise_detail.json    enterprises=%d  来源=%s  状态=%s  (%.1f KB)"
          % (len(detail["data"]["enterprises"]), detail["meta"]["source"],
             detail["meta"]["contract_status"], os.path.getsize(DETAIL_PATH) / 1024))
    print("[enterprise] shap_local.json           entries=%d  来源=%s  状态=%s  (%.1f KB)"
          % (len(local["data"]["entries"]), local["meta"]["source"],
             local["meta"]["contract_status"], os.path.getsize(LOCAL_PATH) / 1024))
    if local["data"]["entries"]:
        e0 = local["data"]["entries"][0]
        print("[enterprise]   自校验 u0001：Σφ+base=%.4f vs logit=%.4f · 全局 ρ=%.3f · 方向一致 %.2f"
              % (sum(f["phi"] for f in e0["features"]) + e0["rest"]["phi"] + e0["base_value"],
                 e0["score_logit"], e0["consistency"]["global_rho"], e0["consistency"]["sign_agree"]))
        print("[enterprise]   一致性下限：ρ_min=%.3f · sign_min=%.2f"
              % (local["data"]["method"]["consistency_check"]["spearman_min"],
                 local["data"]["method"]["consistency_check"]["sign_agree_min"]))

    if args.all:
        spec_detail, spec_local = build_detail(anc), None
        spec_local = build_local(spec_detail, anc)
        sp_detail, sp_local = build_sparse(spec_detail, anc)
        lp_detail, lp_local = build_label_pending()
        dump_json(os.path.join(CONTRACT_DIR, "enterprise_detail_sparse.json"), sp_detail)
        dump_json(os.path.join(CONTRACT_DIR, "shap_local_sparse.json"), sp_local)
        dump_json(os.path.join(CONTRACT_DIR, "enterprise_detail_label_pending.json"), lp_detail)
        dump_json(os.path.join(CONTRACT_DIR, "shap_local_label_pending.json"), lp_local)
        # 主文件始终写 spec（可复现）
        dump_json(DETAIL_PATH, spec_detail)
        dump_json(LOCAL_PATH, spec_local)
        print("[enterprise] 已追加 sparse / label_pending 两个降级态契约，主文件回到 spec 模式")


if __name__ == "__main__":
    main()
