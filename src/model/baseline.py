"""LR / XGBoost 基线模型（真实面板，对应 T33 的基线部分）。

划分与标准化**全部复用** src/data/preprocess.py 的产物（`split` 列 + 训练集统计量），
与 GAT（src/model/gat_panel.py）严格同口径，结果可直接横向对比。

方法要点（针对本数据"弱信号 + 高维 + 小样本"的特点）
--------------------------------------------------
1. **分组交叉验证**：`GroupKFold(5)` 按 userid 分组，给出 mean±std 的稳定估计；
   单次 80/20 的 AUC 在 30 个正例下标准误约 0.055，不足以支撑结论。
2. **验证集早停**：从每折训练集内再按 userid 切出 15% 作为验证集，XGBoost 用
   `early_stopping_rounds=50` 防过拟合（诊断显示无早停时训练集 AUC=1.0、测试仅 0.58）。
3. **单变量特征筛选**：按点二列相关在**折内训练集**上打分，保留 top-k 维，
   缓解 217 维对 ~1,100 家训练企业过参数化的问题。
4. **置换对照**：用打乱标签跑同一流程，给出"无信号基线"AUC，防止把噪声当成果。

用法
----
    .venv\\Scripts\\python.exe -m src.model.baseline
"""

import json
import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.stats import pointbiserialr
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from xgboost import XGBClassifier

from src.model.metrics import compute_metrics

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROC_DIR = os.path.join(ROOT, "data", "processed")
PANEL = os.path.join(PROC_DIR, "zhejiang_panel_clean.parquet")
COMPANY = os.path.join(PROC_DIR, "zhejiang_company_clean.parquet")
OUT_DIR = os.path.join(ROOT, "outputs")
RESULTS_OUT = os.path.join(OUT_DIR, "baseline_results.json")

SEED = 42
K_FEATURES = 40      # 单变量筛选后保留的特征维数
N_SPLITS = 5         # 分组交叉验证折数
VAL_FRACTION = 0.15  # 折内再切出的验证集比例（用于早停）

NON_FEATURE = {"userid", "apply_time", "month_index", "label", "region_code", "split", "n_records"}


# --------------------------------------------------------------------------- #
# 数据与工具
# --------------------------------------------------------------------------- #
def prepare(level: str = "row"):
    """读取指定粒度，返回 (原表, 特征名, X, y, 分组键, split 列)。"""
    path = PANEL if level == "row" else COMPANY
    df = pd.read_parquet(path)
    feats = [c for c in df.columns if c not in NON_FEATURE]
    X = df[feats].values.astype(np.float32)
    y = df["label"].values.astype(int)
    groups = df["userid"].values
    return df, feats, X, y, groups, df["split"].values


def univariate_rank(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """按 |点二列相关| 降序返回特征下标（零方差/常量列排最后）。"""
    scores = np.zeros(X.shape[1])
    for j in range(X.shape[1]):
        v = X[:, j].astype(float)
        if np.std(v) == 0 or len(np.unique(y)) < 2:
            scores[j] = 0.0
            continue
        r, _ = pointbiserialr(y, v)
        scores[j] = 0.0 if np.isnan(r) else abs(r)
    return np.argsort(-scores)


def make_logreg() -> LogisticRegression:
    return LogisticRegression(
        C=0.1,  # 弱信号小样本 → 加强正则
        penalty="l2",
        solver="lbfgs",
        max_iter=3000,
        class_weight="balanced",
        random_state=SEED,
    )


def make_xgb(y_train: np.ndarray) -> XGBClassifier:
    n_pos = max(int(y_train.sum()), 1)
    n_neg = int(len(y_train) - n_pos)
    return XGBClassifier(
        n_estimators=1000,
        max_depth=3,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.5,
        min_child_weight=5,
        reg_lambda=5.0,
        gamma=0.5,
        scale_pos_weight=float(n_neg) / float(n_pos),
        objective="binary:logistic",
        eval_metric="auc",
        tree_method="hist",
        early_stopping_rounds=50,
        random_state=SEED,
        n_jobs=4,
    )


def _fit_fold(X_tr, y_tr, X_va, y_va, X_te, k, return_models=False):
    """在折内训练集上筛特征并拟合两个模型。

    返回 (LR 概率, XGB 概率, 选中下标, XGB 树数)；return_models=True 时额外返回
    (lr, xgb) 两个已拟合模型，供 SHAP 解释复用（保证解释的就是评估的那个模型）。
    """
    idx = univariate_rank(X_tr, y_tr)[:k]
    Ztr, Zva, Zte = X_tr[:, idx], X_va[:, idx], X_te[:, idx]

    lr = make_logreg()
    lr.fit(Ztr, y_tr)
    p_lr = lr.predict_proba(Zte)[:, 1]

    xgb = make_xgb(y_tr)
    xgb.fit(Ztr, y_tr, eval_set=[(Zva, y_va)], verbose=False)
    p_xgb = xgb.predict_proba(Zte)[:, 1]
    n_trees = int(getattr(xgb, "best_iteration", xgb.n_estimators) or xgb.n_estimators) + 1
    if return_models:
        return p_lr, p_xgb, idx, n_trees, lr, xgb
    return p_lr, p_xgb, idx, n_trees


def _aggregate(fold_metrics: List[Dict[str, float]], keys=("auc", "ks", "accuracy", "recall", "f1")) -> Dict[str, float]:
    out = {}
    for k in keys:
        vals = np.array([m[k] for m in fold_metrics], dtype=float)
        vals = vals[~np.isnan(vals)]
        out["%s_mean" % k] = float(vals.mean()) if vals.size else float("nan")
        out["%s_std" % k] = float(vals.std(ddof=0)) if vals.size else float("nan")
    return out


# --------------------------------------------------------------------------- #
# 交叉验证
# --------------------------------------------------------------------------- #
def run_cv(level: str = "row", k: int = K_FEATURES, n_splits: int = N_SPLITS,
           shuffle_y: bool = False, verbose: bool = True) -> Dict[str, object]:
    """分组交叉验证。shuffle_y=True 时用打乱标签做置换对照。"""
    df, feats, X, y, groups, _ = prepare(level)
    folders = GroupKFold(n_splits=n_splits)
    folds = list(folders.split(X, y, groups))

    lr_ms, xgb_ms, n_trees, used = [], [], [], []
    for i, (tr_idx, te_idx) in enumerate(folds):
        X_tr_full, y_tr_full = X[tr_idx], y[tr_idx]
        if shuffle_y:
            rng = np.random.default_rng(1000 + i)
            y_tr_full = rng.permutation(y_tr_full)
        # 折内验证集（按 userid 再切）
        gss = GroupShuffleSplit(n_splits=1, test_size=VAL_FRACTION, random_state=SEED + i)
        sub_tr, sub_va = next(gss.split(X_tr_full, y_tr_full, groups[tr_idx]))
        p_lr, p_xgb, idx, nt = _fit_fold(
            X_tr_full[sub_tr], y_tr_full[sub_tr],
            X_tr_full[sub_va], y_tr_full[sub_va],
            X[te_idx], k,
        )
        lr_ms.append(compute_metrics(y[te_idx], p_lr))
        xgb_ms.append(compute_metrics(y[te_idx], p_xgb))
        n_trees.append(nt)
        used.append(len(idx))

    out = {
        "level": level,
        "protocol": "GroupKFold(%d) by userid%s" % (n_splits, "，标签已打乱（置换对照）" if shuffle_y else ""),
        "k_features": k,
        "n_splits": n_splits,
        "n_samples": int(len(y)),
        "n_groups": int(len(np.unique(groups))),
        "logistic_regression": _aggregate(lr_ms),
        "xgboost": _aggregate(xgb_ms),
        "xgb_mean_trees_used": float(np.mean(n_trees)),
        "fold_metrics": {"logistic_regression": lr_ms, "xgboost": xgb_ms},
    }
    if verbose:
        tag = "（置换对照：标签已打乱）" if shuffle_y else ""
        print("\n[baseline:%s] GroupKFold(%d) 分组交叉验证%s" % (level, n_splits, tag))
        print("  样本 %d 行 / %d 企业，特征 %d 维 → 筛至 top-%d" % (len(y), len(np.unique(groups)), len(feats), k))
        for name, agg in (("LR", out["logistic_regression"]), ("XGBoost", out["xgboost"])):
            print("  %-8s AUC=%.4f±%.4f  KS=%.4f±%.4f  Acc=%.4f  Recall=%.4f  F1=%.4f"
                  % (name, agg["auc_mean"], agg["auc_std"], agg["ks_mean"], agg["ks_std"],
                     agg["accuracy_mean"], agg["recall_mean"], agg["f1_mean"]))
        print("  XGBoost 平均早停于第 %.0f 棵树" % out["xgb_mean_trees_used"])
    return out


# --------------------------------------------------------------------------- #
# 固定留出集（供 SHAP 与 GAT 复用）
# --------------------------------------------------------------------------- #
def run_holdout(level: str = "row", k: int = K_FEATURES, verbose: bool = True) -> Dict[str, object]:
    """用 preprocess 的 80/20 分组留出集训练，返回模型、预测与指标（供 SHAP/GAT 对比）。"""
    df, feats, X, y, groups, split = prepare(level)
    tr = split == "train"
    te = split == "test"

    gss = GroupShuffleSplit(n_splits=1, test_size=VAL_FRACTION, random_state=SEED)
    sub_tr, sub_va = next(gss.split(X[tr], y[tr], groups[tr]))
    X_tr, y_tr = X[tr], y[tr]
    p_lr, p_xgb, idx, nt, lr, xgb = _fit_fold(
        X_tr[sub_tr], y_tr[sub_tr], X_tr[sub_va], y_tr[sub_va], X[te], k, return_models=True
    )

    out = {
        "level": level,
        "k_features": k,
        "selected_idx": idx.tolist(),
        "selected_features": [feats[i] for i in idx],
        "n_train": int(tr.sum()),
        "n_test": int(te.sum()),
        "xgb_trees_used": nt,
        "logistic_regression": compute_metrics(y[te], p_lr),
        "xgboost": compute_metrics(y[te], p_xgb),
    }
    if verbose:
        print("\n[baseline:%s] 固定留出集（80/20 分组）" % level)
        print("  LR      AUC=%.4f  KS=%.4f  Acc=%.4f  Recall=%.4f  F1=%.4f"
              % (out["logistic_regression"]["auc"], out["logistic_regression"]["ks"],
                 out["logistic_regression"]["accuracy"], out["logistic_regression"]["recall"],
                 out["logistic_regression"]["f1"]))
        print("  XGBoost AUC=%.4f  KS=%.4f  Acc=%.4f  Recall=%.4f  F1=%.4f（早停于第 %d 棵）"
              % (out["xgboost"]["auc"], out["xgboost"]["ks"], out["xgboost"]["accuracy"],
                 out["xgboost"]["recall"], out["xgboost"]["f1"], nt))
    # 供下游复用（不写入 json）
    out["_artifacts"] = {"X": X, "y": y, "feats": feats, "tr": tr, "te": te,
                         "groups": groups, "idx": idx, "p_lr": p_lr, "p_xgb": p_xgb,
                         "model_lr": lr, "model_xgb": xgb}
    return out


# --------------------------------------------------------------------------- #
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {
        "cv_row": run_cv("row"),
        "cv_company": run_cv("company"),
        "cv_row_shuffled_label": run_cv("row", shuffle_y=True, verbose=False),
    }
    hold = run_holdout("row")
    hold.pop("_artifacts", None)
    results["holdout_row"] = hold

    results["protocol"] = {
        "split": "preprocess 的 split 列（GroupShuffleSplit by userid, test_size=0.2, seed=42）",
        "cv": "GroupKFold(5) by userid；折内再按 userid 切 15% 做早停验证集",
        "feature_selection": "折内训练集上按 |点二列相关| 排序，保留 top-%d（无跨折泄漏）" % K_FEATURES,
        "imbalance": "LR: class_weight='balanced'; XGBoost: scale_pos_weight=负/正",
        "permutation_control": "打乱标签后跑同一 CV 流程，AUC 即为“无信号基线”，用于判断真实 AUC 是否超出噪声",
        "no_leakage": "同企业记录不跨训练/测试集；特征筛选与标准化统计量均只在训练侧拟合",
    }

    with open(RESULTS_OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n[baseline] 结果已写出: %s" % RESULTS_OUT)


if __name__ == "__main__":
    main()
