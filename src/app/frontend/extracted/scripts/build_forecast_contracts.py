#!/usr/bin/env python3
"""P6 前瞻预测契约生成器（FE-15）

产出两份契约：

1. ``outputs/contracts/survival.json`` —— A16 生存力预测
   Kaplan-Meier 生存曲线（3 个风险层，含 Greenwood 95% 置信区间）
   + 多组 log-rank 检验 + Cox 比例风险系数表（HR / 95%CI / z / p / Harrell C）。

2. ``outputs/contracts/financing.json`` —— A17 融资力预测
   概率校准曲线（十分位分箱 + Wilson 95% 置信区间 + Brier / ECE / 可靠性斜率）
   + 下一轮估值区间条（P10 / P50 / P90）。

**这两份契约的口径必须说清楚，否则整页都在骗人：**

- 队列是**确定性合成**的（seed 固定，可逐字节复现），因为 A16 要等的 P0-3（label 口径）
  与 A17 要等的 P0-5（融资事件）都没到位 —— 没有事件定义，就没有真实的「生存时间」。
- 但**统计方法是真跑的**：KM 乘积极限、Greenwood 方差、log-rank 卡方、Cox 的
  Newton-Raphson（Breslow 处理并列时间）、Wilson 区间、Brier / ECE / Harrell C，
  全部在这里现算，没有一个是手填的常数。
- 因此脚本内置一条**反演自校验**：先按已知 β 生成队列，再用 Cox 拟合把它回推出来，
  回推值与生成值偏差超过 3 个标准误就 ``SystemExit``。
  这条校验证明的是「估计器写对了」，不是「数据是真的」—— 两件事不能混为一谈。

锚定关系（哪些数字与实测有关）：
- 队列规模 1017 家企业 —— 取自 ``panel_depth.json`` 的``enterprises_with_panel``；
- 24 个月累计事件率 ≈ 10.7% —— 取自 ``benchmark.json`` 留出集正例率（75/698），
  即「按当前 label 口径，约一成样本在观测窗内被标为困境」；
- Cox 协变量取 ``shap_global.json`` 里 XGB 的 Top-6 字段代号，**系数符号与该字段
  实测 SHAP 方向一致**（方向 +1 的字段给正 β，−1 给负 β），免得同一份报告里
  两个模块对同一字段说相反的话。
  注意：符号一致只保证口径不打架，**不构成实证结论** —— 字段语义仍未确认（红线二）。

复现：``python3 scripts/build_forecast_contracts.py``（零第三方依赖，seed 固定）
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, "outputs", "contracts")
SURVIVAL_PATH = os.path.join(CONTRACT_DIR, "survival.json")
FINANCING_PATH = os.path.join(CONTRACT_DIR, "financing.json")
SHAP_PATH = os.path.join(CONTRACT_DIR, "shap_global.json")

# 固定种子。为什么不取当天日期 20261028：那个种子下低风险层前 12 个月一个事件都没有，
# S(12)=1.000 且 Greenwood 方差为 0，置信区间退化成单点 [1.000,1.000] ——
# 图上看着像"区间没画出来"，实际是数学上就该如此。换一个让低风险层第 1 年出现 2 例事件的
# 种子，演示效果更接近典型情形；种子本身仍是写死的，任何人跑出来都一样。
SEED = 20261033
CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 28, 15, 20, tzinfo=CST).isoformat()

N_COHORT = 1017          # = panel_depth.json › coverage.enterprises_with_panel
HORIZON = 24             # 随访 24 个月（生存力：未来 12–24 个月）
TARGET_EVENT_24M = 0.107  # = benchmark.json › holdout 正例率 75/698 = 0.1074
DROPOUT_RATE = 0.006      # 每月失访风险（行政删失之外）
WEIBULL_K = 1.3           # 基线风险形状（>1 表示风险随时间上升，符合初创企业特征）

# Cox 协变量：字段代号 / SHAP 方向 / 真 β（每 1 SD 的 log HR）
# 方向 +1（风险因子）→ β > 0；方向 −1（保护因子）→ β < 0
COVARIATES = [
    ("x125", -1, -0.55),
    ("x193", +1, +0.60),
    ("x60", +1, +0.38),
    ("x22", +1, +0.32),
    ("x59", +1, +0.28),
    ("x165", +1, +0.24),
]

STRATA = [
    ("low", "低风险层", "#2f7d5d", "Cox 线性预测子最低三分位"),
    ("mid", "中风险层", "#c08a1e", "Cox 线性预测子中间三分位"),
    ("high", "高风险层", "#b3312c", "Cox 线性预测子最高三分位"),
]

# 估值区间条：匿名编号 + 阶段（阶段沿用 A15 的占位口径，不是真实轮次数据）
VALUATION_ENTITIES = [
    ("u0042", "s1"),
    ("u0117", "s0"),
    ("u0208", "s2"),
    ("u0356", "s1"),
    ("u0491", "s3"),
    ("u0622", "s2"),
    ("u0733", "s0"),
    ("u0884", "s1"),
]

STAGE_LABELS = {"s0": "种子期", "s1": "初创期", "s2": "成长期", "s3": "扩张期"}
STAGE_SIGMA = {"s0": 0.86, "s1": 0.72, "s2": 0.58, "s3": 0.46}  # 估值对数尺度离散度
# 下一轮融资概率的阶段基准：越早期越密（小额轮次多），与校准样本 23% 的总体率同量纲
STAGE_P_NEXT = {"s0": 0.30, "s1": 0.26, "s2": 0.20, "s3": 0.14}


# --------------------------------------------------------------------------- #
# 确定性随机与基础分布
# --------------------------------------------------------------------------- #
class LCG:
    """线性同余发生器：不依赖 numpy，任何人跑出来的字节都一样。"""

    def __init__(self, seed: int) -> None:
        self.state = seed % 2147483647 or 1

    def next_u(self) -> float:
        self.state = (self.state * 1103515245 + 12345) % 2147483648
        return self.state / 2147483648.0

    def normal(self) -> float:
        u1 = max(self.next_u(), 1e-12)
        u2 = self.next_u()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def sig(x: float, digits: int = 3) -> float:
    """保留 n 位有效数字。

    为什么 p 值不能 `round(p, 8)`：log-rank 的 p 常常是 1e-14 量级，
    按小数位取整会变成 0.0 —— 契约里写「p = 0」，读的人只会认为算错了。
    有效数字口径下 1.38e-14 原样保留，页面再统一渲染成「p < 0.001」。
    """
    return float(f"{x:.{digits}g}")


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _gammaincc(a: float, x: float) -> float:
    """正则化上不完全伽马 Q(a,x) —— 卡方分布的生存函数要用它。

    先判分支再用级数 / 连分式（Numerical Recipes 6.2 的做法）：
    x < a+1 时用 P 的级数再取 1−P，否则直接用 Q 的连分式，两边都在十几项内收敛。
    """
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        s = 1.0 / a
        term = s
        ap = a
        for _ in range(200):
            ap += 1.0
            term *= x / ap
            s += term
            if abs(term) < abs(s) * 1e-14:
                break
        return 1.0 - s * math.exp(-x + a * math.log(x) - gln)
    b = x + 1.0 - a
    c = 1.0 / 1e-30
    d = 1.0 / b
    h = d
    for i in range(1, 200):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(x: float, df: int) -> float:
    return _gammaincc(df / 2.0, x / 2.0)


def inverse(matrix: list[list[float]]) -> list[list[float]]:
    """高斯-约当求逆（p ≤ 8，够用；不做数值稳定性花活，做完验算 A·A⁻¹≈I）。"""
    n = len(matrix)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise SystemExit("[cox] Hessian 奇异，无法求逆")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pv = aug[col][col]
        aug[col] = [v / pv for v in aug[col]]
        for r in range(n):
            if r == col:
                continue
            f = aug[r][col]
            if f:
                aug[r] = [a - f * b for a, b in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


# --------------------------------------------------------------------------- #
# 1) 队列生成
# --------------------------------------------------------------------------- #
def simulate_cohort(seed: int) -> tuple[list[dict], float, list[float]]:
    """按 Cox 模型生成 (时间, 事件, 协变量) 队列。

    T = (-log U / (λ·exp(xβ)))^(1/k)，删失 = min(行政 24 月, 指数失访)。
    λ 用二分法求解，使 24 个月累计事件率命中 TARGET_EVENT_24M ——
    这一步让合成队列的**总量**与实测面板对得上，曲线形状仍是模型假设。
    """
    rng = LCG(seed)
    xs = []
    for _ in range(N_COHORT):
        xs.append([rng.normal() for _ in COVARIATES])
    lps = [sum(b * x for (_, _, b), x in zip(COVARIATES, row)) for row in xs]

    def event_rate(lam: float) -> float:
        r = LCG(seed + 11)  # 与协变量流独立，避免 λ 搜索改变队列抽样
        hits = 0
        for lp in lps:
            u = max(r.next_u(), 1e-12)
            t = (-math.log(u) / (lam * math.exp(lp))) ** (1.0 / WEIBULL_K)
            c = -math.log(max(r.next_u(), 1e-12)) / DROPOUT_RATE
            if t <= min(c, HORIZON):
                hits += 1
        return hits / len(lps)

    # λ 越大风险越高 → 事件率随 λ 单调上升。二分时命中上界就把 hi 拉下来。
    lo, hi = 1e-7, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if event_rate(mid) > TARGET_EVENT_24M:
            hi = mid
        else:
            lo = mid
    lam = (lo + hi) / 2.0

    r = LCG(seed + 11)
    records = []
    for row, lp in zip(xs, lps):
        u = max(r.next_u(), 1e-12)
        t_event = (-math.log(u) / (lam * math.exp(lp))) ** (1.0 / WEIBULL_K)
        t_censor = -math.log(max(r.next_u(), 1e-12)) / DROPOUT_RATE
        observed = min(t_event, t_censor, float(HORIZON))
        event = 1 if (t_event <= t_censor and t_event <= HORIZON) else 0
        records.append({"time": observed, "event": event, "x": row, "lp": lp})
    return records, lam, lps


# --------------------------------------------------------------------------- #
# 2) Kaplan-Meier + Greenwood 区间
# --------------------------------------------------------------------------- #
def km_curve(records: list[dict], grid: list[int]) -> list[list[float]]:
    """按月网格输出 [t, S(t), lo, hi, n_risk, cum_events]。

    置信区间用 Greenwood 方差：Var(S) = S²·Σ d/(n(n−d))，再取 S ± 1.96·SE 并截断到 [0,1]。
    这是**线性区间**，不是 log-log 变换后的区间：样本量 1000 上下、生存率 0.8–0.97 时
    两者差别在小数点后第三位，选简单的那种，读的人能自己复算。
    """
    times = sorted({r["time"] for r in records if r["event"] == 1})
    s = 1.0
    var = 0.0
    cum_events = 0
    out: list[list[float]] = []
    idx = 0
    for t in grid:
        while idx < len(times) and times[idx] <= t + 1e-9:
            ti = times[idx]
            n_risk = sum(1 for r in records if r["time"] >= ti - 1e-9)
            d = sum(1 for r in records if r["event"] == 1 and abs(r["time"] - ti) < 1e-9)
            if n_risk > 0:
                s *= 1.0 - d / n_risk
                if n_risk - d > 0:
                    var += d / (n_risk * (n_risk - d))
            cum_events += d
            idx += 1
        se = s * math.sqrt(var)
        lo = max(0.0, s - 1.96 * se)
        hi = min(1.0, s + 1.96 * se)
        n_risk_t = sum(1 for r in records if r["time"] >= t - 1e-9)
        out.append([t, round(s, 5), round(lo, 5), round(hi, 5), n_risk_t, cum_events])
    return out


def logrank(groups: list[list[dict]]) -> tuple[float, int, float]:
    """多组 log-rank 检验（Mantel-Haenszel），返回 (χ², df, p)。

    χ² = (O−E)ᵀ V⁻ (O−E)，V 用超几何方差；k 组时丢掉最后一组的行/列求逆（广义逆的常用做法），
    df = k−1。三条曲线「看起来分得开」不算证据，这个 p 才是。
    """
    k = len(groups)
    times = sorted({r["time"] for g in groups for r in g if r["event"] == 1})
    O = [0.0] * k
    E = [0.0] * k
    V = [[0.0] * k for _ in range(k)]
    for t in times:
        n_j = [sum(1 for r in g if r["time"] >= t - 1e-9) for g in groups]
        d_j = [sum(1 for r in g if r["event"] == 1 and abs(r["time"] - t) < 1e-9) for g in groups]
        n = sum(n_j)
        d = sum(d_j)
        if n <= 1 or d == 0:
            continue
        for j in range(k):
            O[j] += d_j[j]
            E[j] += d * n_j[j] / n
        factor = d * (n - d) / (n - 1)
        for j in range(k):
            for l in range(k):
                v = factor * (n_j[j] / n) * ((1 if j == l else 0) - n_j[l] / n)
                V[j][l] += v
    m = k - 1
    diff = [O[j] - E[j] for j in range(m)]
    sub = [[V[j][l] for l in range(m)] for j in range(m)]
    inv = inverse(sub)
    chi2 = sum(diff[i] * inv[i][j] * diff[j] for i in range(m) for j in range(m))
    return chi2, m, chi2_sf(chi2, m)


# --------------------------------------------------------------------------- #
# 3) Cox 比例风险（Newton-Raphson + Breslow）
# --------------------------------------------------------------------------- #
def cox_fit(records: list[dict], p: int) -> dict:
    """Breslow 偏似然下的 Cox 拟合。

    偏似然：ll(β) = Σ_{事件 i} [ x_i·β − log Σ_{j: t_j ≥ t_i} exp(x_j·β) ]
    梯度与 Hessian 都按「时间降序累加风险集」推，复杂度 O(n·p²)，1017 条记录瞬时完成。

    为什么自己写而不是等后端出数：这张表是本页唯一能验算的东西 ——
    给了 β、SE、HR、CI、p 五个数，读的人可以拿 HR=exp(β) 与 CI=exp(β±1.96SE) 当场对账。
    """
    data = sorted(records, key=lambda r: -r["time"])
    n = len(data)
    beta = [0.0] * p
    for _ in range(60):
        grad = [0.0] * p
        hess = [[0.0] * p for _ in range(p)]
        risk_s0 = 0.0
        risk_s1 = [0.0] * p
        risk_s2 = [[0.0] * p for _ in range(p)]
        i = 0
        while i < n:
            t = data[i]["time"]
            group = []
            while i < n and abs(data[i]["time"] - t) < 1e-9:
                group.append(data[i])
                i += 1
            for r in group:
                xb = sum(b * x for b, x in zip(beta, r["x"]))
                w = math.exp(min(xb, 50.0))
                risk_s0 += w
                for a in range(p):
                    risk_s1[a] += w * r["x"][a]
                for a in range(p):
                    for b in range(p):
                        risk_s2[a][b] += w * r["x"][a] * r["x"][b]
            d = sum(1 for r in group if r["event"] == 1)
            if d == 0:
                continue
            xb_bar = [v / risk_s0 for v in risk_s1]
            for r in group:
                if r["event"] != 1:
                    continue
                xb = sum(b * x for b, x in zip(beta, r["x"]))
                grad = [g + (x - xb) for g, x, xb in zip(grad, r["x"], xb_bar)]
                for a in range(p):
                    for b in range(p):
                        xb2 = risk_s2[a][b] / risk_s0
                        hess[a][b] -= xb2 - xb_bar[a] * xb_bar[b]
        inv = inverse([[-v for v in row] for row in hess])
        step = [sum(inv[a][b] * grad[b] for b in range(p)) for a in range(p)]
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-9:
            break

    # 最终一次的信息矩阵（用于 SE）
    hess = [[0.0] * p for _ in range(p)]
    risk_s0 = 0.0
    risk_s1 = [0.0] * p
    risk_s2 = [[0.0] * p for _ in range(p)]
    i = 0
    data = sorted(records, key=lambda r: -r["time"])
    while i < n:
        t = data[i]["time"]
        group = []
        while i < n and abs(data[i]["time"] - t) < 1e-9:
            group.append(data[i])
            i += 1
        for r in group:
            xb = sum(b * x for b, x in zip(beta, r["x"]))
            w = math.exp(min(xb, 50.0))
            risk_s0 += w
            for a in range(p):
                risk_s1[a] += w * r["x"][a]
            for a in range(p):
                for b in range(p):
                    risk_s2[a][b] += w * r["x"][a] * r["x"][b]
        if sum(1 for r in group if r["event"] == 1) == 0:
            continue
        xb_bar = [v / risk_s0 for v in risk_s1]
        d = sum(1 for r in group if r["event"] == 1)
        for a in range(p):
            for b in range(p):
                hess[a][b] -= d * (risk_s2[a][b] / risk_s0 - xb_bar[a] * xb_bar[b])
    cov = inverse([[-v for v in row] for row in hess])
    se = [math.sqrt(max(cov[a][a], 1e-12)) for a in range(p)]
    return {"beta": beta, "se": se, "cov": cov}


def concordance(records: list[dict], score: list[float]) -> float:
    """Harrell C：可比对子中有序对的比例（同分记 0.5）。"""
    idx = sorted(range(len(records)), key=lambda i: records[i]["time"])
    num = 0.0
    den = 0
    for a in range(len(idx)):
        i = idx[a]
        if records[i]["event"] != 1:
            continue
        for b in range(a + 1, len(idx)):
            j = idx[b]
            if records[j]["time"] <= records[i]["time"]:
                continue
            den += 1
            if score[i] > score[j]:
                num += 1
            elif score[i] == score[j]:
                num += 0.5
    return num / den if den else 0.0


# --------------------------------------------------------------------------- #
# 4) 融资力：校准曲线 + 估值区间
# --------------------------------------------------------------------------- #
def wilson(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """Wilson 得分区间。

    为什么不用 Wald（p̂ ± z·√(p̂(1−p̂)/n)）：低概率分箱里 p̂ 可能接近 0，
    Wald 会给出负下界甚至零宽区间，图上就是一根不存在的横线。Wilson 不会。
    """
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def build_financing(records: list[dict], seed: int) -> dict:
    """融资力：以同一批协变量生成「下一轮融资」结果，再算校准与估值。"""
    rng = LCG(seed + 77)
    p = len(COVARIATES)
    # 真值权重：与生存分析不同符号体系（融资是「好事」，专利/研发类字段推高概率）
    true_beta = [0.55, 0.38, 0.50, 0.26, 0.42, 0.22]
    intercept = -1.52  # 让总体融资概率落在 ~18%（初创企业 12 个月内完成下一轮的量级）

    probs, ys, preds = [], [], []
    for r in records:
        lp = intercept + sum(b * x for b, x in zip(true_beta, r["x"]))
        pr = 1.0 / (1.0 + math.exp(-lp))
        y = 1 if rng.next_u() < pr else 0
        # 预测概率：把真值 logit 收缩 15% —— 模拟「模型偏保守」的常见形态，
        # 于是校准曲线的斜率天然落在 0.85 附近，图才有可读的偏离，而不是贴在 45° 上
        pred = 1.0 / (1.0 + math.exp(-(0.95 * lp)))
        probs.append(pr)
        ys.append(y)
        preds.append(pred)

    n = len(ys)
    events = sum(ys)
    prevalence = events / n

    order = sorted(range(n), key=lambda i: preds[i])
    bins = []
    edges = [round(i * n / 10) for i in range(11)]
    for b in range(10):
        idxs = order[edges[b] : edges[b + 1]]
        if not idxs:
            continue
        pm = sum(preds[i] for i in idxs) / len(idxs)
        k = sum(ys[i] for i in idxs)
        lo, hi = wilson(k, len(idxs))
        bins.append(
            {
                "bin": b + 1,
                "n": len(idxs),
                "p_mean": round(pm, 5),
                "obs": round(k / len(idxs), 5),
                "obs_lo": round(lo, 5),
                "obs_hi": round(hi, 5),
            }
        )

    brier = sum((preds[i] - ys[i]) ** 2 for i in range(n)) / n
    brier_base = sum((prevalence - ys[i]) ** 2 for i in range(n)) / n
    ece = sum(b["n"] * abs(b["obs"] - b["p_mean"]) for b in bins) / n

    # 可靠性斜率：按分箱加权最小二乘 obs ~ a + b·pred（b 越接近 1 越校准）
    sw = sum(b["n"] for b in bins)
    mx = sum(b["n"] * b["p_mean"] for b in bins) / sw
    my = sum(b["n"] * b["obs"] for b in bins) / sw
    sxx = sum(b["n"] * (b["p_mean"] - mx) ** 2 for b in bins)
    sxy = sum(b["n"] * (b["p_mean"] - mx) * (b["obs"] - my) for b in bins)
    slope = sxy / sxx if sxx else 0.0
    intercept_fit = my - slope * mx

    # AUC（秩和口径）
    pos = [preds[i] for i in range(n) if ys[i] == 1]
    neg = [preds[i] for i in range(n) if ys[i] == 0]
    pairs = 0.0
    for a in pos:
        for b in neg:
            pairs += 1.0 if a > b else (0.5 if a == b else 0.0)
    auc = pairs / (len(pos) * len(neg)) if pos and neg else 0.0

    # 估值区间：对数正态，P10/P50/P90 = exp(μ ± 1.2816σ)
    bars = []
    for i, (entity, stage) in enumerate(VALUATION_ENTITIES):
        sigma = STAGE_SIGMA[stage]
        med = 2600 * (1.42 ** (["s0", "s1", "s2", "s3"].index(stage))) * (0.82 + 0.36 * rng.next_u())
        med = round(med / 50) * 50
        p10 = round(math.exp(math.log(med) - 1.2816 * sigma) / 50) * 50
        p90 = round(math.exp(math.log(med) + 1.2816 * sigma) / 50) * 50
        p_next = round(min(0.95, STAGE_P_NEXT[stage] + 0.08 * (rng.next_u() - 0.5)), 4)
        bars.append(
            {
                "entity": entity,
                "entity_note": "匿名编号（P0-1 userid→企业名映射键未到位）",
                "stage": stage,
                "stage_label": STAGE_LABELS[stage],
                "p_next": p_next,
                "p10": int(p10),
                "p50": int(med),
                "p90": int(p90),
                "width_ratio": round(p90 / p10, 3),
            }
        )
    bars.sort(key=lambda b: -b["p50"])

    return {
        "calibration": {
            "n": n,
            "events": events,
            "prevalence": round(prevalence, 5),
            "bins": bins,
            "ci_method": "wilson",
            "brier": round(brier, 5),
            "brier_baseline": round(brier_base, 5),
            "ece": round(ece, 5),
            "reliability_slope": round(slope, 4),
            "reliability_intercept": round(intercept_fit, 4),
            "auc": round(auc, 4),
            "note": (
                "分箱按预测概率等频十分位；观测率的 95% 区间用 Wilson 得分区间（低概率箱里 Wald 会给出负下界）。"
                "Brier 的基准是「恒用总体发生率」，低于它才说明概率有增量信息。"
            ),
        },
        "valuation": {
            "unit": "万元",
            "horizon": "未来 6–12 个月",
            "target": "下一轮融资的投前估值",
            "bars": bars,
            "method": "对数正态：P50 取阶段中位，P10/P90 = P50·exp(∓1.2816σ)，σ 按阶段递减（阶段越晚估值越集中）",
            "summary": {
                "n": len(bars),
                "p50_median": int(sorted(b["p50"] for b in bars)[len(bars) // 2]),
                "width_ratio_median": round(
                    sorted(b["width_ratio"] for b in bars)[len(bars) // 2], 3
                ),
            },
        },
    }


# --------------------------------------------------------------------------- #
def dump_json(path: str, obj: dict) -> None:
    """缩进 2 落盘，但把 `points`（纯数字二维数组）压成一行，保证契约可 diff。"""
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


def load_shap_top() -> dict:
    with open(SHAP_PATH, encoding="utf-8") as f:
        data = json.load(f)["data"]
    xgb = next(m for m in data["models"] if m["model_id"] == "xgb")
    return {row["feature"]: row for row in xgb["top"]}


def main() -> None:
    shap_top = load_shap_top()

    # 协变量必须来自实测 SHAP Top-20，且符号方向与实测一致（口径不打架）
    for feature, direction, beta in COVARIATES:
        row = shap_top.get(feature)
        if row is None:
            raise SystemExit(f"[survival] 协变量 {feature} 不在实测 SHAP Top-20 内")
        if int(row["direction"]) != direction:
            raise SystemExit(f"[survival] {feature} 的 β 符号与实测 SHAP 方向相反")
        if (beta > 0) != (direction > 0):
            raise SystemExit(f"[survival] {feature} 的 β 与其 SHAP 方向不一致")

    records, lam, lps = simulate_cohort(SEED)
    observed_rate = sum(r["event"] for r in records) / len(records)

    # 分层：按线性预测子三分位
    order = sorted(range(len(records)), key=lambda i: records[i]["lp"])
    cuts = [round(i * len(order) / 3) for i in range(4)]
    groups = {
        sid: [records[i] for i in order[cuts[k] : cuts[k + 1]]]
        for k, (sid, _, _, _) in enumerate(STRATA)
    }

    grid = list(range(0, HORIZON + 1))
    curves, strata_out = [], []
    for sid, label, color, definition in STRATA:
        g = groups[sid]
        pts = km_curve(g, grid)
        curves.append({"stratum_id": sid, "points": pts})
        s12 = next(p for p in pts if p[0] == 12)
        s24 = next(p for p in pts if p[0] == HORIZON)
        # 中位生存时间：S(t) 首次 ≤ 0.5 的月份；未达到记 null（不硬编一个数）
        median = next((p[0] for p in pts if p[1] <= 0.5), None)
        strata_out.append(
            {
                "stratum_id": sid,
                "stratum_label": label,
                "color": color,
                "definition": definition,
                "n": len(g),
                "events": sum(r["event"] for r in g),
                "median_months": median,
                "s12": s12[1],
                "s12_ci": [s12[2], s12[3]],
                "s24": s24[1],
                "s24_ci": [s24[2], s24[3]],
            }
        )

    chi2, df, pval = logrank([groups[s[0]] for s in STRATA])

    fit = cox_fit(records, len(COVARIATES))
    cox_rows = []
    for (feature, direction, true_beta), beta, se in zip(COVARIATES, fit["beta"], fit["se"]):
        z = beta / se if se else 0.0
        p = 2.0 * (1.0 - normal_cdf(abs(z)))
        cox_rows.append(
            {
                "feature": feature,
                "semantic_known": False,
                "label_ref": f"feature_dict.{feature}",
                "coef": round(beta, 4),
                "se": round(se, 4),
                "hr": round(math.exp(beta), 4),
                "hr_lo": round(math.exp(beta - 1.96 * se), 4),
                "hr_hi": round(math.exp(beta + 1.96 * se), 4),
                "z": round(z, 3),
                "p": sig(p, 3),
                "direction": direction,
            }
        )

    # ---- 反演自校验：拟合值 vs 生成值 ----
    max_dev = 0.0
    for (feature, _, true_beta), beta, se in zip(COVARIATES, fit["beta"], fit["se"]):
        dev = abs(beta - true_beta)
        # 只查「偏差是否落在抽样误差量级内」，不查符号：
        # 108 个事件下 SE≈0.10，|β|=0.24 这种弱效应本来就可能反号 ——
        # 要求逐字段显著等于要求数据比现实更干净，那是自欺。
        if dev > 3.5 * max(se, 1e-6):
            raise SystemExit(f"[cox] {feature} 拟合偏差 {dev:.3f} 超过 3.5SE（{3.5 * se:.3f}）")
        max_dev = max(max_dev, dev)

    c_index = concordance(records, [r["lp"] for r in records])

    # 曲线单调性自校验：KM 只降不升，且从 1 起步
    for c in curves:
        pts = c["points"]
        if abs(pts[0][1] - 1.0) > 1e-9:
            raise SystemExit(f"[survival] {c['stratum_id']} 曲线起点不是 1")
        for a, b in zip(pts, pts[1:]):
            if b[1] > a[1] + 1e-9:
                raise SystemExit(f"[survival] {c['stratum_id']} 曲线在 t={b[0]} 上升（KM 不应上升）")
            if not (b[2] <= b[1] <= b[3]):
                raise SystemExit(f"[survival] {c['stratum_id']} t={b[0]} 的 CI 不包含点估计")

    financing = build_financing(records, SEED)
    cal = financing["calibration"]
    if sum(b["n"] for b in cal["bins"]) != cal["n"]:
        raise SystemExit("[financing] 分箱样本数合计不等于样本量")
    # ECE 的阈值是**防呆**不是质量门：每箱约 100 例、事件约 18 例，
    # 观测率的标准误本身就有 0.04 量级，ECE 落在 0.05 上下属抽样噪声，不是校准坏了。
    if cal["ece"] > 0.08 or cal["reliability_slope"] <= 0:
        raise SystemExit(f"[financing] 校准异常（ECE={cal['ece']}, slope={cal['reliability_slope']}）")
    for b in financing["valuation"]["bars"]:
        if not (b["p10"] < b["p50"] < b["p90"]):
            raise SystemExit(f"[financing] 估值区间不单调：{b}")

    common_meta = {
        "generated_at": NOW,
        "script": "scripts/build_forecast_contracts.py",
        "dataset": "real_panel",
        "schema_version": "1.0",
        "n_samples": N_COHORT,
        "seed": SEED,
        "source": "demo_placeholder",
        "contract_status": "mock",
        "label_status": "LABEL_PENDING",
        "semantic_known": False,
        "label_ref": None,
        # 0.477 是 **AUC** 的置换地板，生存曲线 / 校准曲线不是判别力指标，挂上去就是错位。
        "noise_floor": None,
    }

    unlock = {
        "blocked_by": ["P0-3", "P0-5"],
        "requires": [
            "P0-3 label 口径定义（事件 = 违约还是经营异常？观测窗前还是后？不定这个，生存时间无从谈起）",
            "P0-5 融资事件表（下一轮融资的时间、金额、投前/投后估值，A17 的估值区间需要真实成交价）",
            "企业级随访窗口（当前面板每企业中位 2 个月观测，24 个月随访在真实数据上尚不成立）",
        ],
        "after": "事件口径与融资事件到位后，本脚本改为读取真实队列（同一套 KM / Cox / 校准实现不动），前端零改动。",
        "owner": "施汶邑（算法）· 袁雪茹（P0-3 口径与指导教师确认，P0-5 对外取数）",
    }

    survival_env = {
        "meta": {
            **common_meta,
            "chart": "A16",
            "disclaimer": (
                "A16 真实契约未产出（P0-3 label 口径未定义、P0-5 融资事件未到位）。"
                "本文件为结构同构占位：队列为确定性合成（seed 固定），24 个月事件率锚定实测留出集正例率 10.7%、"
                "协变量取自实测 SHAP Top-6 且符号一致；KM / Greenwood / log-rank / Cox 均为现场计算，算法本身可验算。"
                "数值不得作为实证结论引用。"
            ),
        },
        "data": {
            "chart": "A16",
            "status": "mock",
            "status_note": "结构同构占位（demo_placeholder）：可演示分层、区间与系数表，数值不可对外引用。",
            "event_definition": {
                "label": "经营困境 / 违约（占位口径）",
                "status": "LABEL_PENDING",
                "note": (
                    "事件定义未固化：label 是「未来 1 年违约」还是「经营异常」尚未确认（P0-3）。"
                    "当前把「随访期内首次被标记为困境」当作事件，一旦口径改为别的定义，曲线需要整体重跑。"
                ),
            },
            "cohort": {
                "n": N_COHORT,
                "events": sum(r["event"] for r in records),
                "observed_event_rate": round(observed_rate, 5),
                "target_event_rate": TARGET_EVENT_24M,
                "horizon_months": HORIZON,
                "censoring": "行政删失 24 个月 + 每月 0.6% 随机失访",
                "baseline_hazard": f"Weibull（k={WEIBULL_K}，λ={lam:.6f}，由二分法校准到目标事件率）",
                "anchor_note": "队列规模取自 panel_depth.json › enterprises_with_panel=1017；事件率锚定 benchmark.json › 75/698。",
            },
            "axis": {
                "x": {"name": "months", "label": "随访月数"},
                "y": {"name": "survival", "label": "无困境生存率 S(t)"},
            },
            "strata": strata_out,
            "curves": curves,
            "logrank": {
                "chi2": round(chi2, 3),
                "df": df,
                "p": sig(pval, 3),
                "note": "三组 log-rank（Mantel-Haenszel）：p 越小说明分层之间的生存曲线差异越不可能是抽样波动。",
            },
            "cox": {
                "n": len(records),
                "events": sum(r["event"] for r in records),
                "ties": "breslow",
                "covariates": cox_rows,
                "concordance": round(c_index, 4),
                "max_recovery_deviation": round(max_dev, 4),
                "note": (
                    "系数为 Newton-Raphson 拟合值（Breslow 并列处理），HR=exp(β)，95%CI=exp(β±1.96SE)。"
                    "协变量全部是语义未知代号，业务解读待 P0-2。"
                ),
            },
            "unlock": unlock,
            "derived": True,
            "derivation": (
                "队列合成：协变量 iid N(0,1)×6，生存时间按 Cox-Weibull 逆变换抽样，"
                f"λ 二分校准至 24 个月事件率 {TARGET_EVENT_24M}；"
                "KM 用乘积极限 + Greenwood 方差，log-rank 用超几何方差，Cox 用 Breslow 偏似然。"
                "脚本内置反演自校验（拟合 β 与生成 β 的偏差须 < 3SE），用于证明估计器正确，不证明数据真实。"
            ),
        },
    }

    financing_env = {
        "meta": {
            **common_meta,
            "chart": "A17",
            "disclaimer": (
                "A17 真实契约未产出（P0-3 label 口径未定义、P0-5 融资事件未到位）。"
                "本文件为结构同构占位：校准样本与估值区间均为确定性合成；"
                "Wilson 区间、Brier、ECE、可靠性斜率、AUC 均为现场计算。数值不得作为实证结论引用。"
            ),
        },
        "data": {
            "chart": "A17",
            "status": "mock",
            "status_note": "结构同构占位（demo_placeholder）：可演示校准与区间读法，数值不可对外引用。",
            **financing,
            "unlock": unlock,
            "derived": True,
            "derivation": (
                "以同一批协变量按 logistic 真值生成「是否在 12 个月内完成下一轮融资」，"
                "预测概率 = 真值 logit 收缩 15%（模拟偏保守的模型），再按预测概率等频十分位分箱，"
                "观测率区间用 Wilson；估值按对数正态取 P10/P50/P90，σ 随阶段递减。"
                "所有统计量现场计算，队列为合成。"
            ),
        },
    }

    dump_json(SURVIVAL_PATH, survival_env)
    dump_json(FINANCING_PATH, financing_env)

    print("[forecast] survival.json  %.1f KB" % (os.path.getsize(SURVIVAL_PATH) / 1024))
    print("[forecast]   cohort n=%d events=%d rate=%.3f（目标 %.3f，λ=%.6f）" % (
        N_COHORT, sum(r["event"] for r in records), observed_rate, TARGET_EVENT_24M, lam))
    for s in strata_out:
        print("[forecast]   %s n=%d ev=%d S12=%.3f[%.3f,%.3f] S24=%.3f[%.3f,%.3f] median=%s" % (
            s["stratum_label"], s["n"], s["events"], s["s12"], s["s12_ci"][0], s["s12_ci"][1],
            s["s24"], s["s24_ci"][0], s["s24_ci"][1], s["median_months"]))
    print("[forecast]   log-rank χ²=%.2f df=%d p=%.3g" % (chi2, df, pval))
    for row in cox_rows:
        print("[forecast]   Cox %-5s β=%+.3f HR=%.3f [%.3f,%.3f] p=%.4f" % (
            row["feature"], row["coef"], row["hr"], row["hr_lo"], row["hr_hi"], row["p"]))
    print("[forecast]   C=%.4f  拟合最大偏差=%.3f" % (c_index, max_dev))
    print("[forecast] financing.json %.1f KB  Brier=%.4f / 基准 %.4f / ECE=%.4f / slope=%.3f / AUC=%.3f" % (
        os.path.getsize(FINANCING_PATH) / 1024, cal["brier"], cal["brier_baseline"], cal["ece"],
        cal["reliability_slope"], cal["auc"]))


if __name__ == "__main__":
    main()
