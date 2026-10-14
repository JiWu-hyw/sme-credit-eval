"""统一评估指标（基线 / GAT / 后续模型共用，保证口径一致）。

口径约定
--------
- 主指标：**AUC**（与阈值无关）、**KS**（风控行业标准区分度指标）。
- 辅指标：Accuracy / Precision / Recall / F1，取默认阈值 0.5，另附 best-F1 阈值下的结果。
- 正例仅 9.5%，**不得只看 Accuracy**（全预测 0 也有 90.5%），故 Accuracy 仅作参考。

用法
----
    from src.model.metrics import compute_metrics
    m = compute_metrics(y_true, y_prob)
"""

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def ks_statistic(y_true, y_prob):
    """KS = max|CDF(正例) - CDF(负例)|，衡量模型把好坏样本分开的能力。"""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    order = np.argsort(y_prob)
    y = y_true[order]
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    cum_pos = np.cumsum(y) / float(n_pos)
    cum_neg = np.cumsum(1 - y) / float(n_neg)
    return float(np.max(np.abs(cum_pos - cum_neg)))


def best_f1_threshold(y_true, y_prob):
    """在候选阈值中搜索 F1 最大者，返回 (阈值, F1)。"""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    candidates = np.unique(np.round(y_prob, 4))
    if candidates.size > 300:  # 控制开销
        idx = np.linspace(0, candidates.size - 1, 300).astype(int)
        candidates = candidates[idx]
    best_t, best_f1 = 0.5, -1.0
    for t in candidates:
        pred = (y_prob >= t).astype(int)
        f1 = f1_score(y_true, pred, zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = float(f1), float(t)
    return best_t, best_f1


def compute_metrics(y_true, y_prob, threshold=0.5):
    """返回完整指标字典。"""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    pred = (y_prob >= threshold).astype(int)
    bf_t, bf = best_f1_threshold(y_true, y_prob)
    return {
        "auc": float(roc_auc_score(y_true, y_prob)),
        "ks": ks_statistic(y_true, y_prob),
        "accuracy": float(accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "threshold": float(threshold),
        "best_f1": float(bf),
        "best_f1_threshold": float(bf_t),
        "n_samples": int(len(y_true)),
        "n_pos": int(y_true.sum()),
    }
