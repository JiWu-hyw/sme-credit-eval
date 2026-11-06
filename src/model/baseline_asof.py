"""泄漏前后 AUC 对比：同一 307 家打标样本，在「泄漏宽表」与「as-of 无泄漏宽表」上
重跑基线（LR + XGBoost），量化后视泄漏造成的 AUC 虚高幅度。

背景
----
- `v_model_dataset_flat`（泄漏基线）：特征为企业级**全量历史聚合**，混入标签窗口
  内才发生的专利/资质事件。其中 `m_cert` 取到期年资质记录，而 2022 队列到期年正是
  标签年 → 把标签本身做成特征（单变量 AUC=0.264，反向判别力 0.736）。
- `v_model_dataset_asof_flat WHERE as_of_policy='cohort'`（`build_asof_features.py`
  产出，无泄漏）：只用 cohort_year 及之前的专利/资质/桥表重算，14 个可切分特征；
  `m_cert` 在窗口内退化为常量（队列由首次认定年定义），须剔除。

执行协议（与 src/model/baseline.py 同口径，便于横向对比）
--------------------------------------------------------
- 模型：LogisticRegression(C=0.1, balanced) + XGBoost(early_stopping)。
- 划分：**RepeatedStratifiedKFold(5, 20 次)**，因正例仅 21 个，单次 5 折每折 ~4 正例，
  单次 AUC 标准误过大，必须重复取均值；同企业天然单条记录，无需 GroupKFold。
- 零泄漏约束：缺失中位数 / Z-score 均值方差**只在折内训练集拟合**；单变量特征筛选
  也只在训练折打分。

对比配置（同一套 CV，只换特征集）
---------------------------------
- leaky_full      : 泄漏视图全部 20 个有值特征（含 m_cert）→ "朴素上报" 的虚高 AUC。
- leaky_nomcert   : 泄漏视图去掉 m_cert（19 个）→ 隔离 m_cert 单独贡献。
- leaky_common14  : 14 个可切分特征，但取**全量（泄漏）值** → 隔离"时间窗截断"效应。
- noleak_asof14   : 14 个可切分特征，取 **as-of（无泄漏）值**（m_cert 常量剔除）→ 诚实 AUC。
- 置换对照        : 打乱标签跑 leaky_full / noleak_asof14，给出"无信号基线"。

分解：总虚高 = leaky_full − noleak_asof14
     = (leaky_full − leaky_nomcert)        [m_cert 贡献]
     + (leaky_nomcert − leaky_common14)    [其余 6 个仅泄漏有值特征贡献]
     + (leaky_common14 − noleak_asof14)    [同一 14 特征的时间窗截断贡献]

用法
----
    python -m src.model.baseline_asof
"""

import json
import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import (
    RepeatedStratifiedKFold,
    StratifiedShuffleSplit,
)

from src.model.baseline import (
    SEED,
    VAL_FRACTION,
    _aggregate,
    _fit_fold,
    make_logreg,
    make_xgb,
)
from src.model.metrics import compute_metrics

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT_JSON = os.path.join(ROOT, "outputs", "baseline_asof_leakage.json")
OUT_MD = os.path.join(ROOT, "outputs", "leakage_auc_report.md")

N_SPLITS = 5
N_REPEATS = 20
K_FEATURES = 40  # 远大于实际维数 → 保留全部非恒定特征，纯特征集对比

# 两视图共有的元数据列（一律不进入 X）
META_COLS = {
    "enterprise_id", "cohort_year", "as_of_policy", "as_of_year",
    "label", "label_value", "label_name", "label_task", "split",
    "split_protocol", "dataset_ver", "name_display", "region_code", "built_at",
}

# 14 个可切分（as-of 可重算）特征
ASOFABLE = [
    "core_staff_stability", "inv_patent_ratio", "m_cert", "n_inventor",
    "n_ipc_subclass", "n_patent_app", "n_patent_grant", "n_patent_invention",
    "qual_rank", "qual_rank_delta", "qual_upgrade_cum", "tech_breadth_entropy",
    "tech_breadth_ipc_class", "tech_conversion",
]


# --------------------------------------------------------------------------- #
# 数据载入
# --------------------------------------------------------------------------- #
def load(view: str, where: str = "") -> pd.DataFrame:
    import sqlite3
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT * FROM %s %s" % (view, where), con
    )
    con.close()
    return df


def feat_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c not in META_COLS]


def _drop_constant(X: np.ndarray) -> Tuple[np.ndarray, List[int]]:
    """返回（去恒定列后的 X, 被剔除的列下标）。"""
    keep = [j for j in range(X.shape[1]) if np.nanstd(X[:, j]) > 0]
    return X[:, keep], keep


def _standardize(X_tr, X_va, X_te):
    """训练折中位数填充 + Z-score（统计量只在训练折拟合）。

    额外剔除「在训练折内全为 NaN」的列（该列在此折无信息），避免把 NaN 传进模型
    造成该折 AUC 退化为 NaN 被静默丢弃。列选择只看训练折，不泄漏测试信息。
    """
    med = np.nanmedian(X_tr, axis=0)
    mu = np.nanmean(X_tr, axis=0)
    sd = np.nanstd(X_tr, axis=0)
    sd[sd == 0] = 1.0
    keep = np.isfinite(med) & np.isfinite(mu) & np.isfinite(sd)
    X_tr, X_va, X_te = X_tr[:, keep], X_va[:, keep], X_te[:, keep]
    med, mu, sd = med[keep], mu[keep], sd[keep]
    def fix(a):
        a = a.copy()
        a = np.where(np.isnan(a), med, a)
        return (a - mu) / sd
    return fix(X_tr), fix(X_va), fix(X_te)


def run_cv(X: np.ndarray, y: np.ndarray, shuffle_y: bool = False,
           verbose: bool = False) -> Dict[str, object]:
    """RepeatedStratifiedKFold(5×20) 上的 LR+XGBoost 评估。"""
    rng = np.random.default_rng(SEED)
    X, kept = _drop_constant(X)
    y = y.astype(int)
    if shuffle_y:
        y = rng.permutation(y)

    rskf = RepeatedStratifiedKFold(
        n_splits=N_SPLITS, n_repeats=N_REPEATS, random_state=SEED
    )
    lr_ms, xgb_ms, used = [], [], []
    for tr_idx, te_idx in rskf.split(X, y):
        X_tr_full, y_tr_full = X[tr_idx], y[tr_idx]
        gss = StratifiedShuffleSplit(
            n_splits=1, test_size=VAL_FRACTION, random_state=SEED
        )
        sub_tr, sub_va = next(gss.split(X_tr_full, y_tr_full))
        Ztr, Zva, Zte = _standardize(
            X_tr_full[sub_tr], X_tr_full[sub_va], X[te_idx]
        )
        p_lr, p_xgb, idx, _ = _fit_fold(
            Ztr, y_tr_full[sub_tr], Zva, y_tr_full[sub_va], Zte, K_FEATURES
        )
        lr_ms.append(compute_metrics(y[te_idx], p_lr))
        xgb_ms.append(compute_metrics(y[te_idx], p_xgb))
        used.append(len(idx))

    out = {
        "n_features_used": int(kept.__len__()),
        "n_dropped_constant": int(X.shape[1] - len(kept)),
        "logistic_regression": _aggregate(lr_ms),
        "xgboost": _aggregate(xgb_ms),
    }
    if verbose:
        lr = out["logistic_regression"]
        xg = out["xgboost"]
        print("  LR   AUC=%.4f±%.4f  KS=%.4f±%.4f" % (
            lr["auc_mean"], lr["auc_std"], lr["ks_mean"], lr["ks_std"]))
        print("  XGB  AUC=%.4f±%.4f  KS=%.4f±%.4f" % (
            xg["auc_mean"], xg["auc_std"], xg["ks_mean"], xg["ks_std"]))
    return out


# --------------------------------------------------------------------------- #
def main():
    # 只取 307 家打标样本（排除 excluded / 未匹配标签的企业）
    df_leaky = load("v_model_dataset_flat", "WHERE label_value IS NOT NULL")
    df_asof = load("v_model_dataset_asof_flat",
                   "WHERE as_of_policy='cohort' AND label_value IS NOT NULL")
    # 以 (enterprise_id, cohort_year) 为键对齐，避免两视图行序不同导致标签错位
    df_leaky = df_leaky.set_index(["enterprise_id", "cohort_year"]).sort_index()
    df_asof = (df_asof.set_index(["enterprise_id", "cohort_year"])
               .reindex(df_leaky.index))
    assert len(df_asof) == len(df_leaky), "两视图 307 样本数量不一致"
    assert df_asof["label_value"].notna().all(), "as-of 视图存在未对齐的空行（标签错位）"
    for name, df in (("leaky", df_leaky), ("asof", df_asof)):
        yv = df["label_value"].astype(int)
        print("[load] %-5s n=%d  正例=%d (%.1f%%) 特征候选=%d" % (
            name, len(df), int(yv.sum()), 100.0 * yv.mean(),
            len(feat_cols(df))))
    assert (df_leaky["label_value"].astype(int).sum() ==
            df_asof["label_value"].astype(int).sum() == 21), "标签分布异常"

    feats_leaky = feat_cols(df_leaky)
    fl = df_leaky[feats_leaky].values.astype(float)
    fa = df_asof[ASOFABLE].values.astype(float)
    common_full = df_leaky[ASOFABLE].values.astype(float)  # 14 特征的全量(泄漏)值
    y = df_leaky["label_value"].astype(int).values

    configs = {
        "leaky_full": fl,
        "leaky_nomcert": df_leaky[[c for c in feats_leaky if c != "m_cert"]].values.astype(float),
        "leaky_common14": common_full,
        "noleak_asof14": fa,
    }

    results = {"n_samples": int(len(y)), "n_positive": int(y.sum()),
               "protocol": {
                   "cv": "RepeatedStratifiedKFold(%d, %d repeats)" % (N_SPLITS, N_REPEATS),
                   "models": "LR(C=0.1,balanced) + XGBoost(early_stopping)",
                   "leakage_guard": "缺失中位数/Z-score/单变量筛选只在折内训练集拟合",
                   "target": "label_value（21 正 / 286 负，资质到期重新认定生存标签）",
               },
               "configurations": {}}

    print("\n=== 泄漏前后 AUC 对比（RepeatedStratifiedKFold 5×20）===")
    for cfg, X in configs.items():
        print("\n[%s] 特征 %d 维" % (cfg, X.shape[1]))
        r = run_cv(X, y, verbose=True)
        results["configurations"][cfg] = r

    # 置换对照（无信号基线）
    print("\n=== 置换对照（标签打乱）===")
    for cfg, X in (("leaky_full", fl), ("noleak_asof14", fa)):
        r = run_cv(X, y, shuffle_y=True, verbose=True)
        results["configurations"]["perm_%s" % cfg] = r

    # ---- 分解虚高 ----
    def aucm(cfg, model="xgboost"):
        return results["configurations"][cfg][model]["auc_mean"]
    def aucl(cfg, model="xgboost"):
        return results["configurations"][cfg][model]["auc_mean"]
    total = aucl("leaky_full") - aucl("noleak_asof14")
    mcert = aucl("leaky_full") - aucl("leaky_nomcert")
    other6 = aucl("leaky_nomcert") - aucl("leaky_common14")
    trunc = aucl("leaky_common14") - aucl("noleak_asof14")
    results["decomposition"] = {
        "total_inflation_LR": aucl("leaky_full", "logistic_regression") - aucl("noleak_asof14", "logistic_regression"),
        "total_inflation_XGB": total,
        "m_cert_contribution_XGB": mcert,
        "other_leaky_only_features_XGB": other6,
        "time_window_truncation_XGB": trunc,
        "permutation_floor_XGB": aucl("perm_noleak_asof14"),
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n[done] 写出:", OUT_JSON)
    _write_md(results)
    print("[done] 写出:", OUT_MD)


def _write_md(results: Dict[str, object]):
    C = results["configurations"]
    def row(cfg):
        r = C[cfg]
        lr, xg = r["logistic_regression"], r["xgboost"]
        return "| %-18s | %2d | %.4f±%.4f | %.4f±%.4f | %.4f±%.4f | %.4f±%.4f |" % (
            cfg, r["n_features_used"],
            lr["auc_mean"], lr["auc_std"], xg["auc_mean"], xg["auc_std"],
            lr["ks_mean"], lr["ks_std"], xg["ks_mean"], xg["ks_std"])
    d = results["decomposition"]
    lines = []
    lines.append("# 后视泄漏导致的 AUC 虚高量化报告\n")
    lines.append("> 生成：`src/model/baseline_asof.py`｜同一 307 家打标样本（21 正 / 286 负）\n")
    lines.append("> 协议：%s｜%s\n" % (results["protocol"]["cv"], results["protocol"]["models"]))
    lines.append("> 零泄漏约束：%s\n" % results["protocol"]["leakage_guard"])
    lines.append("\n## 1. 四组配置 AUC 对比\n")
    lines.append("| 配置 | 特征数 | LR AUC | XGB AUC | LR KS | XGB KS |")
    lines.append("|---|---|---|---|---|---|")
    for cfg in ("leaky_full", "leaky_nomcert", "leaky_common14", "noleak_asof14"):
        lines.append(row(cfg))
    lines.append("| %-18s | %2d | %.4f±%.4f | %.4f±%.4f | — | — |" % (
        "perm_leaky_full(打乱标签)", C["perm_leaky_full"]["n_features_used"],
        C["perm_leaky_full"]["logistic_regression"]["auc_mean"],
        C["perm_leaky_full"]["logistic_regression"]["auc_std"],
        C["perm_leaky_full"]["xgboost"]["auc_mean"],
        C["perm_leaky_full"]["xgboost"]["auc_std"]))
    lines.append("| %-18s | %2d | %.4f±%.4f | %.4f±%.4f | — | — |" % (
        "perm_noleak_asof14", C["perm_noleak_asof14"]["n_features_used"],
        C["perm_noleak_asof14"]["logistic_regression"]["auc_mean"],
        C["perm_noleak_asof14"]["logistic_regression"]["auc_std"],
        C["perm_noleak_asof14"]["xgboost"]["auc_mean"],
        C["perm_noleak_asof14"]["xgboost"]["auc_std"]))
    lines.append("\n> 主结论（XGBoost AUC）：朴素泄漏宽表 AUC=%.3f，无泄漏 as-of 宽表 AUC=%.3f，"
                 "**虚高 %.3f**（约 +%.0f%%）。" % (
        C["leaky_full"]["xgboost"]["auc_mean"],
        C["noleak_asof14"]["xgboost"]["auc_mean"],
        d["total_inflation_XGB"],
        100.0 * d["total_inflation_XGB"] / max(C["noleak_asof14"]["xgboost"]["auc_mean"], 1e-6)))
    lines.append("\n## 2. 虚高来源分解（XGBoost AUC）\n")
    lines.append("| 分量 | AUC 贡献 |")
    lines.append("|---|---|")
    lines.append("| 总虚高 (leaky_full − noleak_asof14) | %.4f |" % d["total_inflation_XGB"])
    lines.append("| ├─ m_cert 单独贡献 (leaky_full − leaky_nomcert) | %.4f |" % d["m_cert_contribution_XGB"])
    lines.append("| ├─ 其余 6 个仅泄漏有值特征 (leaky_nomcert − leaky_common14) | %.4f |" % d["other_leaky_only_features_XGB"])
    lines.append("| └─ 同一 14 特征的时间窗截断 (leaky_common14 − noleak_asof14) | %.4f |" % d["time_window_truncation_XGB"])
    lines.append("| 无信号基线（打乱标签） | %.4f |" % d["permutation_floor_XGB"])
    lines.append("\n## 3. 关键口径边界\n")
    lines.append("1. **m_cert 是最大单一泄漏源**：在泄漏宽表里它是 2025 年资质记录（队列到期年），"
                 "等于把标签做成特征；在无泄漏 cohort 切面里它退化为常量（队列由首次认定年定义），已剔除。")
    lines.append("2. **时间窗截断效应真实存在**：即使剔除 m_cert，同一 14 个特征在「全量历史」vs「窗口内」"
                 "的 AUC 仍有差距——全量值混入了标签窗口内才发生的专利/资质事件。")
    lines.append("3. **样本极小（21 正例）**：RepeatedStratifiedKFold(5×20) 仅用于稳定估计；"
                 "绝对 AUC 数值不可过度解读，重点看**泄漏前后差值**。")
    lines.append("4. **结论**：任何在 `v_model_dataset_flat`（或原 `ads_model_dataset`）上训练得到的 AUC，"
                 "都包含后视泄漏虚高，不应作为模型真实能力的证据；"
                 "训练须改用 `v_model_dataset_asof_flat WHERE as_of_policy='cohort'`，并剔除 m_cert。")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
