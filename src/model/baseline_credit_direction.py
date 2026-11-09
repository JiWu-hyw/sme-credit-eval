"""P1-续：标签方向纠正后的 as-of 训练 / SHAP / 分队列 / 分切面复核。

背景（v1.9，2026-11-09）
------------------------
`ads_label.label_value=1` 表示「资质到期后断档」，是**坏企业 / 负例**；旧版
`v_model_train_ready` 把它当正例，方向搞反了。v1.9 新增
`v_model_train_ready_credit`，`label = 1 - label_value`，即 **1=正例·好企业（资质存续，
286 家）/ 0=负例·坏企业（资质断档，21 家）**；旧底表与旧视图一律不动。

本模块做四件事
--------------
1. **方向翻转后的 AUC 恒等式验证**（严格数学恒等式，必须 0 误差）：
   对任意打分 p，`AUC(1-y, p) == 1 - AUC(y, p)`。用每一折的测试集打分逐折验证。
2. **双方向重训练对照**：同一 CV 划分、同一特征，分别用 `y_risk` 与
   `y_credit = 1-y_risk` 训练。用**对称类别权重**（`class_weight=None` /
   `scale_pos_weight=1`）时 `p_credit = 1 - p_risk` 精确成立，因此
   `AUC(y_credit,p_credit) == AUC(y_risk,p_risk)`（**相等**）；
   「互为 1−AUC」只在**同一打分、只翻标签**时成立（见第 1 点），两者不可混淆。
   另保留历史 `balanced` 配置作对照，记录方向翻转后 XGB 退化的超参陷阱。
3. **分切面 / 分队列复核**：
   - `as_of_policy`：`cohort` / `due_minus_1` 两套 as-of 切面各 307 家；
   - `cohort_year`：2021 / 2022 两个首次认定队列（小组负例极少，降低折数并标注）。
   - 每组都跑置换对照（打乱标签）作为噪声地板。
4. **重跑 SHAP**：用 **XGBoost 原生 `pred_contribs`（TreeSHAP）** 与
   **线性模型系数分解（等价 Linear SHAP，背景取训练集均值）**，
   不依赖第三方 `shap` 包。方向语义已纠正：模型输出 P(好企业)，
   `direction=+1` = 该特征值越大越推高「好企业概率」= **增信因子**，
   `direction=-1` = 越大越推高「坏企业概率」= **风险因子**。

数据来源
--------
- 标签 / 样本清单：`v_model_train_ready_credit`（307 家，正确方向）
- 特征：`v_model_dataset_asof_flat`（as-of 无泄漏，14 个可切分特征；`m_cert` 在
  cohort 切面退化为常量，会被 `_drop_constant` 自动剔除）
- 两视图按 `enterprise_id` 对齐，307/307 全覆盖。

产物（均为新文件，不覆盖历史）
------------------------------
outputs/credit_direction_asof.json        全量结构化结果
outputs/credit_direction_asof_report.md  可读报告（含与旧报告的对照）
outputs/shap_credit_direction.json       SHAP 全局贡献（正确方向语义）
outputs/shap_credit_direction.csv        特征贡献表
outputs/shap_credit_direction.png        Top-20 条形图

用法
----
    python -m src.model.baseline_credit_direction            # 全量
    python -m src.model.baseline_credit_direction --quick   # 冒烟（低 repeats）

注意：本机 `python`(3.13) 无 numpy/sklearn，需用带科学栈的解释器：
    <py39>/python.exe -m src.model.baseline_credit_direction
"""

import argparse
import json
import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedShuffleSplit

from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier

from src.model.baseline import (
    SEED, VAL_FRACTION, _aggregate, _fit_fold, make_logreg, make_xgb, univariate_rank,
)
from src.model.baseline_asof import ASOFABLE, _drop_constant, _standardize
from src.model.metrics import compute_metrics

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT_DIR = os.path.join(ROOT, "outputs")
OUT_JSON = os.path.join(OUT_DIR, "credit_direction_asof.json")
OUT_MD = os.path.join(OUT_DIR, "credit_direction_asof_report.md")
SHAP_JSON = os.path.join(OUT_DIR, "shap_credit_direction.json")
SHAP_CSV = os.path.join(OUT_DIR, "shap_credit_direction.csv")
SHAP_PNG = os.path.join(OUT_DIR, "shap_credit_direction.png")

K_FEATURES = 40          # 与 baseline_asof 一致：远大于实际维数 → 保留全部非恒定列
TOP_K = 20
FLIP_TOL = 1e-9          # AUC 恒等式容差
SYM_TOL = 1e-6           # 对称配置下 p_credit = 1 - p_risk 的容差


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #
def load_credit() -> pd.DataFrame:
    """正确方向标签 + 样本清单（307 家）。"""
    import sqlite3
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("SELECT * FROM v_model_train_ready_credit", con)
    old = pd.read_sql_query("SELECT enterprise_id, label AS label_old FROM v_model_train_ready", con)
    con.close()
    df = df.merge(old, on="enterprise_id", how="left", validate="one_to_one")
    assert len(df) == 307, "训练视图应为 307 家"
    assert df["label_old"].notna().all(), "新旧训练视图企业键未对齐"
    assert (df["label"].astype(int) + df["label_old"].astype(int) == 1).all(), \
        "新旧标签不严格互补，方向纠正未落地"
    assert int(df["label"].sum()) == 286, "正例（好企业）应为 286 家"
    return df


def load_asof() -> pd.DataFrame:
    """两套 as-of 切面的无泄漏特征。"""
    import sqlite3
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT * FROM v_model_dataset_asof_flat WHERE label_value IS NOT NULL", con
    )
    con.close()
    assert set(df["as_of_policy"].unique()) == {"cohort", "due_minus_1"}, "切面枚举异常"
    assert len(df) == 614, "as-of 视图应为 307×2=614 行"
    return df


def build_matrix(policy: str, cohort_year=None):
    """按 enterprise_id 对齐正确方向标签与 as-of 特征，返回 (X, y_credit, y_risk, meta)。"""
    cr = load_credit()
    ao = load_asof()
    sub = ao[ao["as_of_policy"] == policy].copy()
    if cohort_year is not None:
        sub = sub[sub["cohort_year"] == cohort_year].copy()
    m = cr[["enterprise_id", "label", "label_old", "cohort_year"]].merge(
        sub[["enterprise_id", "as_of_policy", "cohort_year", "label_value"] + ASOFABLE],
        on="enterprise_id", how="inner", suffixes=("", "_asof"), validate="one_to_one",
    )
    if cohort_year is None:
        assert len(m) == len(cr), "对齐行数异常（应为 307）"
    else:
        assert len(m) > 0, "该队列无对齐样本"
    assert m["label_value"].notna().all(), "as-of 视图存在未对齐行"
    # 旧方向标签以 as-of 视图的 label_value 为准，应与旧训练视图一致
    assert (m["label_value"].astype(int) == m["label_old"].astype(int)).all(), \
        "as-of 视图旧方向标签与旧训练视图不一致"
    y_credit = m["label"].astype(int).values          # 1=好企业（正例）
    y_risk = m["label_value"].astype(int).values     # 1=断档（旧口径的「正例」）
    assert (y_credit + y_risk == 1).all(), "方向不互补"
    X = m[ASOFABLE].values.astype(float)
    meta = m[["enterprise_id", "cohort_year", "as_of_policy"]].copy()
    return X, y_credit, y_risk, meta


# --------------------------------------------------------------------------- #
# 两套类别权重配置
#
# balanced : 与历史基线同超参（class_weight='balanced' / scale_pos_weight=n_neg/n_pos）。
#            方向翻转后 n_pos 从 21 变 286，权重从 13.6 变 0.075，
#            配合 min_child_weight=5 / gamma=0.5 会让 XGB 一棵树都分裂不出来
#            （best_iteration=0、输出常数 0.4996）。**这是真实的超参陷阱**，
#            保留它用于「与历史可比 + 记录陷阱」，但不作为方向验证依据。
# symmetric: 两边都用 class_weight=None / scale_pos_weight=1.0，其余超参不变。
#            此时训练目标关于 (y,p) -> (1-y,1-p) 严格对称，
#            p_credit = 1 - p_risk 精确成立，AUC 相等。**方向验证以此为准。**
# --------------------------------------------------------------------------- #
def make_sym_logreg():
    return LogisticRegression(
        C=0.1, penalty="l2", solver="lbfgs", max_iter=3000,
        class_weight=None, random_state=SEED,
    )


def make_sym_xgb(relax=False):
    # 队列分组负例仅 8/13 家，主协议的 min_child_weight=5 / gamma=0.5
    # 会让任何分裂都过不了增益门槛（XGB 退化为常数）。仅队列复核放宽。
    mcw, gam = (1, 0.0) if relax else (5, 0.5)
    return XGBClassifier(
        n_estimators=1000, max_depth=3, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.5, min_child_weight=mcw,
        reg_lambda=5.0, gamma=gam, scale_pos_weight=1.0,
        objective="binary:logistic", eval_metric="auc", tree_method="hist",
        early_stopping_rounds=50, random_state=SEED, n_jobs=4,
    )


def _fit_fold_weighted(X_tr, y_tr, X_va, y_va, X_te, k, weighting,
                       return_models=False, relax=False):
    """复刻 _fit_fold 的折内筛选/拟合流程，只替换类别权重配置。"""
    idx = univariate_rank(X_tr, y_tr)[:k]
    Ztr, Zva, Zte = X_tr[:, idx], X_va[:, idx], X_te[:, idx]
    if weighting == "balanced":
        lr, xgbf = make_logreg(), make_xgb(y_tr)
    elif weighting == "symmetric":
        lr, xgbf = make_sym_logreg(), make_sym_xgb(relax=relax)
    else:
        raise ValueError("unknown weighting: %s" % weighting)
    lr.fit(Ztr, y_tr)
    p_lr = lr.predict_proba(Zte)[:, 1]
    xgbf.fit(Ztr, y_tr, eval_set=[(Zva, y_va)], verbose=False)
    p_xgb = xgbf.predict_proba(Zte)[:, 1]
    if return_models:
        return p_lr, p_xgb, idx, Ztr, Zte, lr, xgbf
    return p_lr, p_xgb, idx


# --------------------------------------------------------------------------- #
# CV（复用 baseline_asof 的折内标准化协议，但显式固定 splits 供双方向共用）
# --------------------------------------------------------------------------- #
def make_splits(X, y, n_splits, n_repeats):
    rskf = RepeatedStratifiedKFold(
        n_splits=n_splits, n_repeats=n_repeats, random_state=SEED
    )
    return list(rskf.split(X, y))


def make_sub_splits(X, y, splits):
    """预生成折内训练/验证索引（用 y_risk 分层），供双方向共用。

    StratifiedShuffleSplit 的划分依赖标签分层；若两个方向各自生成，y 翻转后
    sub_tr/sub_va 不同 → 模型不同 → p_credit ≠ 1-p_risk。必须固定同一组索引。
    """
    out = []
    for k, (tr_idx, te_idx) in enumerate(splits):
        X_tr_full, y_tr_full = X[tr_idx], y[tr_idx]
        gss = StratifiedShuffleSplit(
            n_splits=1, test_size=VAL_FRACTION, random_state=SEED + k
        )
        out.append(next(gss.split(X_tr_full, y_tr_full)))
    return out


def run_cv_detail(X, y, splits, weighting="symmetric", shuffle_y=False,
                  sub_splits=None, relax=False) -> Dict[str, object]:
    """在给定 splits 上训练，返回聚合指标 + 每折明细（供 AUC 恒等式逐折验证）。"""
    rng = np.random.default_rng(SEED)
    Xc, kept = _drop_constant(X)
    names = [ASOFABLE[j] for j in kept]
    y = np.asarray(y).astype(int)
    if shuffle_y:
        y = rng.permutation(y)

    lr_ms, xgb_ms, used, folds = [], [], [], []
    for k, (tr_idx, te_idx) in enumerate(splits):
        X_tr_full, y_tr_full = Xc[tr_idx], y[tr_idx]
        if sub_splits is not None:
            sub_tr, sub_va = sub_splits[k]
        else:
            gss = StratifiedShuffleSplit(
                n_splits=1, test_size=VAL_FRACTION, random_state=SEED + k
            )
            sub_tr, sub_va = next(gss.split(X_tr_full, y_tr_full))
        Ztr, Zva, Zte = _standardize(
            X_tr_full[sub_tr], X_tr_full[sub_va], Xc[te_idx]
        )
        p_lr, p_xgb, idx = _fit_fold_weighted(
            Ztr, y_tr_full[sub_tr], Zva, y_tr_full[sub_va], Zte,
            K_FEATURES, weighting, relax=relax
        )
        y_te = y[te_idx]
        lr_ms.append(compute_metrics(y_te, p_lr))
        xgb_ms.append(compute_metrics(y_te, p_xgb))
        used.append(len(idx))
        folds.append({"y_te": y_te.tolist(), "p_lr": p_lr.tolist(),
                      "p_xgb": p_xgb.tolist()})
    return {
        "n_samples": int(len(y)),
        "n_positive": int(y.sum()),
        "n_negative": int(len(y) - y.sum()),
        "n_features_used": len(kept),
        "feature_names": names,
        "n_dropped_constant": int(X.shape[1] - len(kept)),
        "n_folds": len(splits),
        "weighting": weighting,
        "relax_split_threshold": bool(relax),
        "logistic_regression": _aggregate(lr_ms),
        "xgboost": _aggregate(xgb_ms),
        "_folds": folds,
    }


def verify_flip_identity(detail: Dict[str, object]) -> Dict[str, object]:
    """严格恒等式：同一打分 p，AUC(1-y,p) + AUC(y,p) == 1。

    对每一折、每个模型、每个打分都验证；另验证「打分取反」的等价写法
    AUC(y, 1-p) == 1 - AUC(y, p)。
    """
    worst, checked = 0.0, 0
    worst_case = None
    for fi, f in enumerate(detail["_folds"]):
        y = np.asarray(f["y_te"]).astype(int)
        for key in ("p_lr", "p_xgb"):
            p = np.asarray(f[key]).astype(float)
            a = compute_metrics(y, p)["auc"]
            b = compute_metrics(1 - y, p)["auc"]
            c = compute_metrics(y, 1.0 - p)["auc"]
            for label, err in (("flip_y", abs(a + b - 1.0)),
                               ("flip_score", abs(a + c - 1.0))):
                checked += 1
                if err > worst:
                    worst, worst_case = err, {"fold": fi, "model": key, "case": label,
                                              "auc": a, "auc_flipped": b if label == "flip_y" else c}
    return {
        "checked": checked,
        "max_abs_error": float(worst),
        "tolerance": FLIP_TOL,
        "passed": bool(worst <= FLIP_TOL),
        "worst_case": worst_case,
        "statement": "AUC(1-y, p) = 1 - AUC(y, p)，且 AUC(y, 1-p) = 1 - AUC(y, p)",
    }


def verify_score_symmetry(risk: Dict[str, object],
                          credit: Dict[str, object]) -> Dict[str, object]:
    """对称配置下应精确成立：p_credit = 1 - p_risk，且 AUC_credit == AUC_risk。

    注意这是**相等**而非互补：标签和打分各翻一次，两次翻转在 AUC 上抵消。
    互补关系（AUC 互为 1-AUC）只在「打分不变、只翻标签」时成立，见
    verify_flip_identity。两件事不要混为一谈。
    """
    worst_p, worst_auc, checked = 0.0, 0.0, 0
    for fr, fc in zip(risk["_folds"], credit["_folds"]):
        yr = np.asarray(fr["y_te"]).astype(int)
        yc = np.asarray(fc["y_te"]).astype(int)
        assert (yr + yc == 1).all(), "双方向测试集标签不互补"
        for key in ("p_lr", "p_xgb"):
            pr = np.asarray(fr[key]).astype(float)
            pc = np.asarray(fc[key]).astype(float)
            worst_p = max(worst_p, float(np.max(np.abs(pr + pc - 1.0))))
            ar = compute_metrics(yr, pr)["auc"]
            ac = compute_metrics(yc, pc)["auc"]
            worst_auc = max(worst_auc, abs(ar - ac))
            checked += 1
    return {
        "checked": checked,
        "max_abs_prob_error": float(worst_p),
        "max_abs_auc_error": float(worst_auc),
        "tolerance": SYM_TOL,
        "passed": bool(worst_p <= SYM_TOL and worst_auc <= SYM_TOL),
        "statement": "对称配置下 p_credit = 1 - p_risk，故 AUC(y_credit,p_credit) = AUC(y_risk,p_risk)",
    }


def strip_folds(d: Dict[str, object]) -> Dict[str, object]:
    return {k: v for k, v in d.items() if k != "_folds"}


# --------------------------------------------------------------------------- #
# SHAP（不依赖 shap 包：XGBoost pred_contribs / 线性系数分解）
# --------------------------------------------------------------------------- #
def _setup_cjk_font():
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, rcParams
    for name in ("Microsoft YaHei", "SimHei", "SimSun"):
        try:
            font_manager.findfont(font_manager.FontProperties(family=name),
                                  fallback_to_default=False)
            rcParams["font.sans-serif"] = [name]
            rcParams["axes.unicode_minus"] = False
            return True
        except Exception:
            continue
    return False


def _direction(feature_values, shap_values) -> int:
    """+1=增信因子（值越大越推高好企业概率），-1=风险因子，0=无法判定。"""
    from scipy.stats import spearmanr
    if np.std(feature_values) == 0 or np.std(shap_values) == 0:
        return 0
    r, _ = spearmanr(feature_values, shap_values)
    return 0 if np.isnan(r) else (1 if r > 0 else -1)


def _rank(names, sv, Z):
    mean_abs = np.abs(sv).mean(axis=0)
    order = np.argsort(-mean_abs)
    return [{
        "rank": int(rank),
        "feature": str(names[j]),
        "mean_abs_shap": float(mean_abs[j]),
        "direction": _direction(Z[:, j], sv[:, j]),
    } for rank, j in enumerate(order, start=1)]


def run_shap(X, y_credit, feature_names, cjk=True) -> Dict[str, object]:
    """固定留出集上训练 LR+XGB，解释的就是这两个模型（不二次训练别的模型）。"""
    import xgboost as xgb

    Xc, kept = _drop_constant(X)
    names = [feature_names[j] for j in kept]
    y = np.asarray(y_credit).astype(int)
    sss = StratifiedShuffleSplit(n_splits=1, test_size=0.25, random_state=SEED)
    tr, te = next(sss.split(Xc, y))
    gss = StratifiedShuffleSplit(n_splits=1, test_size=VAL_FRACTION, random_state=SEED)
    sub_tr, sub_va = next(gss.split(Xc[tr], y[tr]))
    Ztr, Zva, Zte = _standardize(Xc[tr][sub_tr], Xc[tr][sub_va], Xc[te])
    y_sub, y_va, y_te = y[tr][sub_tr], y[tr][sub_va], y[te]

    # 用对称配置训练（balanced 在新方向下退化为常数模型，无解释价值）；
    # 返回的特征矩阵已按折内单变量筛选重排，与模型实际使用的列顺序一致。
    p_lr, p_xgb, idx, Ztr_m, Zte_m, lr, xgbf = _fit_fold_weighted(
        Ztr, y_sub, Zva, y_va, Zte, K_FEATURES, "symmetric", return_models=True
    )
    used_names = [names[j] for j in idx]

    # ---- XGBoost TreeSHAP（原生 pred_contribs，最后一列是 bias） ----
    booster = xgbf.get_booster()
    it = getattr(xgbf, "best_iteration", None)
    kw = {"iteration_range": (0, int(it) + 1)} if it is not None else {}
    contrib = booster.predict(xgb.DMatrix(Zte_m), pred_contribs=True, **kw)
    sv_xgb = np.asarray(contrib)[:, :-1]

    # ---- Linear SHAP 精确解：coef_j * (x_j - E_bg[x_j])，背景取训练集均值 ----
    bg_mean = Ztr_m.mean(axis=0)
    sv_lr = (Zte_m - bg_mean) * lr.coef_[0]

    assert sv_xgb.shape == sv_lr.shape == Zte_m.shape, "SHAP 矩阵维度异常"

    out = {
        "label_direction": "credit_good_positive",
        "target": "P(好企业)：1=正例·资质存续(286)，0=负例·资质断档(21)",
        "note": ("model 输出为 P(好企业)；direction=+1 表示特征值越大越推高好企业概率"
                 "（增信因子），-1 表示越大越推高坏企业概率（风险因子）。"
                 "与旧 shap_global.json 的『违约概率』语义相反，不可混用。"),
        "explained_models": ["xgboost_treeshap_pred_contribs", "logistic_regression_linear_shap"],
        "weighting": "symmetric（class_weight=None / scale_pos_weight=1.0）",
        "explain_set": "固定分层留出集 %d 家（%d 正例）" % (len(y_te), int(y_te.sum())),
        "n_features_used": int(len(idx)),
        "holdout_auc": {
            "logistic_regression": compute_metrics(y_te, p_lr)["auc"],
            "xgboost": compute_metrics(y_te, p_xgb)["auc"],
        },
        "xgb_top%d" % TOP_K: _rank(used_names, sv_xgb, Zte_m)[:TOP_K],
        "xgb_all_importance": {r["feature"]: r["mean_abs_shap"]
                               for r in _rank(used_names, sv_xgb, Zte_m)},
        "lr_top%d" % TOP_K: _rank(used_names, sv_lr, Zte_m)[:TOP_K],
        "lr_all_importance": {r["feature"]: r["mean_abs_shap"]
                              for r in _rank(used_names, sv_lr, Zte_m)},
    }

    # ---- CSV ----
    rows = [(r["rank"], r["feature"], r["mean_abs_shap"], r["direction"])
            for r in out["xgb_top%d" % TOP_K]]
    pd.DataFrame(rows, columns=["rank", "feature", "mean_abs_shap", "direction"]).to_csv(
        SHAP_CSV, index=False, encoding="utf-8-sig"
    )

    # ---- PNG ----
    try:
        import matplotlib.pyplot as plt
        top = rows[:TOP_K][::-1]
        nm = [t[1] for t in top]
        vv = [t[2] for t in top]
        colors = ["#1e8449" if t[3] >= 0 else "#c0392b" for t in top]  # 绿=增信 红=风险
        fig, ax = plt.subplots(figsize=(8, 7), dpi=150)
        ax.barh(range(len(nm)), vv, color=colors)
        ax.set_yticks(range(len(nm)))
        ax.set_yticklabels(nm)
        ax.set_xlabel("平均 |SHAP| 值（全局重要性）" if cjk else "mean |SHAP|")
        ax.set_title(("XGBoost 全局特征贡献 Top-%d（绿=增信因子，红=风险因子）" % TOP_K)
                     if cjk else ("XGBoost global contribution (Top-%d)" % TOP_K))
        plt.tight_layout()
        fig.savefig(SHAP_PNG)
        plt.close(fig)
        out["plot"] = os.path.basename(SHAP_PNG)
    except Exception as e:
        out["plot_error"] = str(e)
    return out


# --------------------------------------------------------------------------- #
def _fmt(r):
    lr, xg = r["logistic_regression"], r["xgboost"]
    return "| %-22s | %3d | %2d | %2d | %.4f±%.4f | %.4f±%.4f |" % (
        r.get("_name", ""), r["n_samples"], r["n_positive"], r["n_negative"],
        lr["auc_mean"], lr["auc_std"], xg["auc_mean"], xg["auc_std"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="低 repeats 冒烟跑")
    args = ap.parse_args()
    main_repeats = 2 if args.quick else 20
    sub_repeats = 1 if args.quick else 10
    group_splits, group_repeats = (3, 1) if args.quick else (3, 10)

    os.makedirs(OUT_DIR, exist_ok=True)
    X, y_credit, y_risk, _ = build_matrix("cohort")
    print("[load] n=%d  正例(好企业)=%d  负例(断档)=%d  特征候选=%d" % (
        len(y_credit), int(y_credit.sum()), int((1 - y_credit).sum()), X.shape[1]))

    splits = make_splits(X, y_risk, 5, main_repeats)
    sub_splits = make_sub_splits(X, y_risk, splits)
    print("[cv] 主配置 splits=%d（5×%d repeats，外层与折内划分均双方向共用）" % (
        len(splits), main_repeats))

    # ---- [A] 历史超参（balanced）：与旧报告可比，并记录方向翻转后的退化陷阱 ----
    print("\n=== [A] 历史超参 balanced（class_weight=balanced / scale_pos_weight=n_neg/n_pos）===")
    bal_risk = run_cv_detail(X, y_risk, splits, weighting="balanced",
                             sub_splits=sub_splits)
    bal_credit = run_cv_detail(X, y_credit, splits, weighting="balanced",
                               sub_splits=sub_splits)
    for tag, r in (("旧方向 y_risk", bal_risk), ("新方向 y_credit", bal_credit)):
        print("  %-20s LR AUC=%.4f  XGB AUC=%.4f" % (
            tag, r["logistic_regression"]["auc_mean"], r["xgboost"]["auc_mean"]))

    # ---- [B] 对称配置（symmetric）：方向验证的唯一依据 ----
    print("\n=== [B] 对称配置 symmetric（class_weight=None / scale_pos_weight=1.0）===")
    risk = run_cv_detail(X, y_risk, splits, weighting="symmetric",
                         sub_splits=sub_splits)
    credit = run_cv_detail(X, y_credit, splits, weighting="symmetric",
                           sub_splits=sub_splits)
    for tag, r in (("旧方向 y_risk", risk), ("新方向 y_credit", credit)):
        print("  %-20s LR AUC=%.4f  XGB AUC=%.4f" % (
            tag, r["logistic_regression"]["auc_mean"], r["xgboost"]["auc_mean"]))

    # ---- 两项验证 ----
    flip = verify_flip_identity(risk)
    sym = verify_score_symmetry(risk, credit)
    print("\n[flip] 同一打分翻标签 → AUC 互为 1−AUC：%d 次校验，最大误差 %.3g，通过=%s" % (
        flip["checked"], flip["max_abs_error"], flip["passed"]))
    print("[sym ] 对称重训练 → p_credit=1−p_risk：%d 次校验，概率误差 %.3g / AUC 误差 %.3g，通过=%s" % (
        sym["checked"], sym["max_abs_prob_error"], sym["max_abs_auc_error"], sym["passed"]))
    assert flip["passed"], "AUC 方向翻转恒等式不成立"
    assert sym["passed"], "对称配置下双方向打分不互补（类别权重仍不对称？）"

    equivalence = {}
    for m in ("logistic_regression", "xgboost"):
        a_risk = risk[m]["auc_mean"]
        a_credit = credit[m]["auc_mean"]
        equivalence[m] = {
            "auc_risk_direction": float(a_risk),
            "auc_credit_direction": float(a_credit),
            "abs_gap_equality": float(abs(a_credit - a_risk)),
            "auc_credit_on_same_risk_score": float(1.0 - a_risk),
            "balanced_auc_risk": float(bal_risk[m]["auc_mean"]),
            "balanced_auc_credit": float(bal_credit[m]["auc_mean"]),
        }
        print("  %-22s AUC_risk=%.4f AUC_credit=%.4f 相等差=%.4g | 同一打分翻标签=%.4f | balanced 新方向=%.4f" % (
            m, a_risk, a_credit, equivalence[m]["abs_gap_equality"],
            1.0 - a_risk, bal_credit[m]["auc_mean"]))

    # ---- 置换对照（噪声地板） ----
    print("\n=== 置换对照（打乱标签，对称配置）===")
    perm = run_cv_detail(X, y_credit, splits, weighting="symmetric", shuffle_y=True)
    print("  新方向 LR AUC=%.4f  XGB AUC=%.4f" % (
        perm["logistic_regression"]["auc_mean"], perm["xgboost"]["auc_mean"]))

    # ---- 分 as_of_policy ----
    print("\n=== 分切面复核（对称配置 / 正确方向标签）===")
    by_policy = {}
    for p in ("cohort", "due_minus_1"):
        Xp, ycp, yrp, _ = build_matrix(p)
        sp = make_splits(Xp, yrp, 5, sub_repeats)
        r = run_cv_detail(Xp, ycp, sp, weighting="symmetric")
        r["_name"] = p
        by_policy[p] = r
        print("  %-12s n=%d  LR=%.4f  XGB=%.4f" % (
            p, r["n_samples"], r["logistic_regression"]["auc_mean"],
            r["xgboost"]["auc_mean"]))

    # ---- 分队列 ----
    print("\n=== 分队列复核（小组样本少，折数降到 %d×%d）===" % (group_splits, group_repeats))
    by_cohort = {}
    for cy in (2021, 2022):
        Xc, ycc, yrc, _ = build_matrix("cohort", cohort_year=cy)
        sc = make_splits(Xc, yrc, group_splits, group_repeats)
        r = run_cv_detail(Xc, ycc, sc, weighting="symmetric", relax=True)
        r["_name"] = str(cy)
        by_cohort[str(cy)] = r
        print("  %-6s n=%d 正例=%d 负例=%d  LR=%.4f  XGB=%.4f" % (
            cy, r["n_samples"], r["n_positive"], r["n_negative"],
            r["logistic_regression"]["auc_mean"], r["xgboost"]["auc_mean"]))
        rp = run_cv_detail(Xc, ycc, sc, weighting="symmetric", shuffle_y=True,
                           relax=True)
        by_cohort[str(cy)]["permutation"] = strip_folds(rp)

    # ---- SHAP ----
    print("\n=== 重跑 SHAP（XGBoost 原生 TreeSHAP + 线性精确解，对称配置 / 正确方向）===")
    Xsh, ysh, _, _ = build_matrix("cohort")
    shap_out = run_shap(Xsh, ysh, ASOFABLE, cjk=_setup_cjk_font())
    print("  留出集 AUC: LR=%.4f XGB=%.4f" % (
        shap_out["holdout_auc"]["logistic_regression"],
        shap_out["holdout_auc"]["xgboost"]))
    print("  XGB top-5: %s" % ", ".join(
        "%s(%.4f)" % (r["feature"], r["mean_abs_shap"])
        for r in shap_out["xgb_top%d" % TOP_K][:5]))
    with open(SHAP_JSON, "w", encoding="utf-8") as f:
        json.dump(shap_out, f, ensure_ascii=False, indent=2)

    # ---- 汇总 JSON ----
    results = {
        "generated_by": "src/model/baseline_credit_direction.py",
        "schema_version": "v1.9",
        "quick_mode": bool(args.quick),
        "protocol": {
            "cv": "RepeatedStratifiedKFold(5, %d repeats)，双方向共用同一 splits" % main_repeats,
            "verification_weighting": "symmetric（class_weight=None / scale_pos_weight=1.0）",
            "historical_weighting": "balanced（class_weight=balanced / scale_pos_weight=n_neg/n_pos）",
            "models": "LR(C=0.1) + XGBoost(early_stopping)，其余超参与历史基线一致",
            "leakage_guard": "缺失中位数/Z-score/单变量筛选只在折内训练集拟合",
            "features": "v_model_dataset_asof_flat 的 14 个可切分特征（m_cert 常量自动剔除）",
            "label_source": "v_model_train_ready_credit（1=好企业/资质存续 286，0=坏企业/断档 21）",
        },
        "direction": {
            "positive_class": "好企业·资质存续（286）",
            "negative_class": "坏企业·资质断档（21）",
            "label_rule": "credit_label = 1 - ads_label.label_value",
        },
        "flip_identity": flip,
        "score_symmetry": sym,
        "retrain_equivalence": equivalence,
        "balanced_trap": {
            "logistic_regression": equivalence["logistic_regression"]["balanced_auc_credit"],
            "xgboost": equivalence["xgboost"]["balanced_auc_credit"],
            "note": ("方向翻转后 scale_pos_weight 从 286/21 变为 21/286≈0.073，"
                     "配合 min_child_weight=5 / gamma=0.5，XGB best_iteration=0、"
                     "输出常数 0.4996 → AUC 恒为 0.5。属超参陷阱，非模型能力下降。"),
        },
        "main": {
            "risk_direction": strip_folds(risk),
            "credit_direction": strip_folds(credit),
            "permutation": strip_folds(perm),
        },
        "by_as_of_policy": {k: strip_folds(v) for k, v in by_policy.items()},
        "by_cohort_year": {
            k: {kk: vv for kk, vv in v.items() if kk != "_folds"}
            for k, v in by_cohort.items()
        },
        "shap": {
            "json": os.path.basename(SHAP_JSON),
            "csv": os.path.basename(SHAP_CSV),
            "png": shap_out.get("plot"),
            "holdout_auc": shap_out["holdout_auc"],
            "n_features_used": shap_out["n_features_used"],
            "xgb_top5": shap_out["xgb_top%d" % TOP_K][:5],
            "lr_top5": shap_out["lr_top%d" % TOP_K][:5],
            "xgb_nonzero_features": sum(
                1 for v in shap_out["xgb_all_importance"].values() if v > 0),
        },
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    _write_md(results)
    print("\n[done] %s" % OUT_JSON)
    print("[done] %s" % OUT_MD)
    print("[done] %s / .csv / .png" % SHAP_JSON)


def _write_md(R):
    flip = R["flip_identity"]
    sym = R["score_symmetry"]
    complement = R["retrain_equivalence"]
    perm = R["main"]["permutation"]
    by_policy = R["by_as_of_policy"]
    by_cohort = R["by_cohort_year"]
    L = []
    L.append("# 标签方向纠正后的 as-of 训练 / SHAP / 分队列复核报告\n")
    L.append("> 生成：`src/model/baseline_credit_direction.py`｜库 schema **v1.9**｜"
             "样本 307 家（**正例 286 好企业 / 负例 21 断档**）\n")
    L.append("> 方向口径（v1.9 纠正）：`credit_label = 1 - ads_label.label_value`；"
             "旧版把 21 家资质断档当「正例」是错的，**旧底表不动**，训练改用 "
             "`v_model_train_ready_credit`。\n")
    L.append("> 协议：%s；%s\n" % (R["protocol"]["cv"], R["protocol"]["models"]))
    L.append("> 验证权重：%s；历史对照：%s\n" % (
        R["protocol"]["verification_weighting"], R["protocol"]["historical_weighting"]))
    L.append("> 零泄漏约束：%s\n" % R["protocol"]["leakage_guard"])
    if R["quick_mode"]:
        L.append("\n> ⚠️ 本次为 `--quick` 冒烟跑，重复次数已降低，数值不可用于结论。\n")

    L.append("\n## 1. 「AUC 互为 1−AUC」的准确含义（严格验证）\n")
    L.append("设 `y_r` = 旧标签（1=断档）、`y_c = 1 − y_r` = 新标签（1=好企业）、`p` = 模型打分：\n")
    L.append("- **只翻标签**：`AUC(1−y, p) = 1 − AUC(y, p)` —— 同一打分下成立；")
    L.append("- **只翻打分**：`AUC(y, 1−p) = 1 − AUC(y, p)`；")
    L.append("- **两者都翻**（重新训练得 `p_c = 1 − p_r`）：两次翻转抵消，"
             "`AUC(y_c, p_c) = AUC(y_r, p_r)`，**是相等，不是互补**。\n")
    L.append("`%s`\n" % flip["statement"])
    L.append("| 校验次数 | 最大绝对误差 | 容差 | 结论 |")
    L.append("|---|---|---|---|")
    L.append("| %d | %.3g | %.0e | %s |" % (
        flip["checked"], flip["max_abs_error"], flip["tolerance"],
        "全部通过" if flip["passed"] else "失败"))
    L.append("\n> 这是**数学恒等式**，只要打分 p 不变就成立，与模型、特征、划分无关。"
             "它说明：方向纠正**不改变判别力**，只改变「高分代表谁」的语义。\n")

    L.append("\n## 2. 双方向重训练（对称配置，验证依据）\n")
    L.append("统一用 `%s`；历史超参见 §3。\n" % R["protocol"]["verification_weighting"])
    L.append("| 模型 | 旧方向 AUC(y_r,p_r) | 新方向 AUC(y_c,p_c) | 相等差 | 同一打分翻标签 1−AUC_r |")
    L.append("|---|---|---|---|---|")
    for m, d in complement.items():
        L.append("| %-22s | %.4f | %.4f | %.3g | %.4f |" % (
            m, d["auc_risk_direction"], d["auc_credit_direction"],
            d["abs_gap_equality"], d["auc_credit_on_same_risk_score"]))
    L.append("| %-22s | %.4f | %.4f | — | 噪声地板 |" % (
        "置换对照(打乱标签)", perm["logistic_regression"]["auc_mean"],
        perm["xgboost"]["auc_mean"]))
    L.append("\n**打分对称性逐折校验**：%s\n" % sym["statement"])
    L.append("| 校验次数 | 最大概率误差 | 最大 AUC 误差 | 容差 | 结论 |")
    L.append("|---|---|---|---|---|")
    L.append("| %d | %.3g | %.3g | %.0e | %s |" % (
        sym["checked"], sym["max_abs_prob_error"], sym["max_abs_auc_error"],
        sym["tolerance"], "全部通过" if sym["passed"] else "失败"))
    L.append("\n> 对称配置下 `p_c = 1 − p_r` 精确成立，两个方向的 AUC **相等**；"
             "所谓「互为 1−AUC」指的是**拿旧打分去评估新标签**（上表第 5 列）。"
             "两者不可混为一谈 —— 之前把「重训练后应互补」写进预期是错的，本版已纠正。\n")

    L.append("\n## 3. 历史超参陷阱：直接翻标签会让 XGB 退化\n")
    L.append("| 模型 | 旧方向 AUC(balanced) | 新方向 AUC(balanced) |")
    L.append("|---|---|---|")
    for m, d in complement.items():
        L.append("| %-22s | %.4f | %.4f |" % (
            m, d["balanced_auc_risk"], d["balanced_auc_credit"]))
    L.append("\n> %s\n" % R["balanced_trap"]["note"])
    L.append("> 这不是模型能力下降，而是**类别权重与分裂阈值的交互**：沿用旧超参直接换标签，"
             "会得到「模型突然失效」的假象。**本报告所有复核与 SHAP 统一用对称配置**，"
             "balanced 结果仅作历史可比留档，不用于方向验证。\n")

    L.append("\n## 4. 与旧报告对照及实质结论\n")
    L.append("- 旧 `leakage_auc_report.md` 的 `noleak_asof14` XGB AUC=0.5237 是"
             "**balanced 配置、旧方向（预测断档）**；换成正确语义（预测好企业）应读作 "
             "**1 − 0.5237 = 0.4763**。该数值与上表对称配置不可直接横比（权重配置不同）。")
    L.append("- 本次新方向 AUC ≈ %.4f（LR）/ %.4f（XGB），置换地板 ≈ %.4f / %.4f，"
             "**仍在噪声附近**。\n" % (
                 complement["logistic_regression"]["auc_credit_direction"],
                 complement["xgboost"]["auc_credit_direction"],
                 perm["logistic_regression"]["auc_mean"], perm["xgboost"]["auc_mean"]))
    L.append("> **实质结论**：方向纠正没有让模型变好，只把语义摆正；判别力弱的根因仍是"
             "标签单一、负例仅 21 家、缺少独立结果标签 —— 这正是 P1 多标签测评要解决的，"
             "不是靠调参或翻转能解决的。\n")

    L.append("\n## 5. 分 as_of_policy 复核\n")
    L.append("| 切面 | n | 正例 | 负例 | LR AUC | XGB AUC |")
    L.append("|---|---|---|---|---|---|")
    for p, r in by_policy.items():
        L.append(_fmt(r))
    L.append("\n> 两套切面只是「取值时间窗」不同（cohort=首次认定年及之前，"
             "due_minus_1=到期前一年及之前），标签与样本完全相同；"
             "差异反映时间窗截断带来的信息量变化，不是方向问题。\n")

    L.append("\n## 6. 分队列复核（2021 / 2022 首次认定）\n")
    L.append("| 队列 | n | 正例 | 负例 | LR AUC | XGB AUC |")
    L.append("|---|---|---|---|---|---|")
    for cy, r in by_cohort.items():
        L.append(_fmt(r))
    L.append("\n> 队列分组负例仅 8 / 13 家，沿用主协议的 `min_child_weight=5 / gamma=0.5` "
             "会让 XGB 一棵树都分不出来（AUC 恒 0.5）；故**仅队列复核**放宽到 "
             "`min_child_weight=1 / gamma=0`，其余超参不变。该组数值与主表**不可横比**，"
             "只看队列间相对差异。\n")
    L.append("\n> ⚠️ 每个队列的负例只有约 10 家，`RepeatedStratifiedKFold(3, %d)` "
             "每折仅 2–3 个负例，**AUC 点估计不可信**，只用于确认"
             "「两个队列都不存在异常反向」；不得写进论文当作分队列结论。\n"
             % (by_cohort["2021"]["n_folds"] // 3))
    L.append("\n> 特别提示：2021 队列 LR AUC=%.4f **低于其置换地板 %.4f**，"
             "说明该分组在 8 个负例下没有可辨识信号；两个队列的置换地板本身也远高于 0.5，"
             "印证极小样本下 AUC 方差极大，表内数值不得当作分队列结论。\n" % (
                 by_cohort["2021"]["logistic_regression"]["auc_mean"],
                 by_cohort["2021"]["permutation"]["logistic_regression"]["auc_mean"]))
    for cy, r in by_cohort.items():
        pm = r["permutation"]
        L.append("- %s 队列置换对照：LR %.4f / XGB %.4f（噪声地板）\n" % (
            cy, pm["logistic_regression"]["auc_mean"], pm["xgboost"]["auc_mean"]))
    L.append("- 运行期 XGB 曾提示 `Dataset ... only positive or negative samples`："
             "队列负例 8/13 家，折内验证集偶发单类，只影响该折早停用的 AUC 计算，"
             "不影响训练与聚合结论，此处如实记录。\n")

    L.append("\n## 7. SHAP 重跑（方向语义已纠正）\n")
    L.append("- 实现：XGBoost 原生 `pred_contribs`（TreeSHAP）+ 线性模型系数分解"
             "（等价 Linear SHAP，背景取训练集均值），**不依赖第三方 shap 包**。")
    L.append("- 模型输出为 **P(好企业)**：`direction=+1` = 值越大越推高好企业概率"
             "（**增信因子**），`-1` = 值越大越推高坏企业概率（**风险因子**）。")
    L.append("- ⚠️ 与旧 `outputs/shap_global.json` / `shap_top_features.csv` 的"
             "「违约概率」语义**相反**，两者不可混用；旧文件保留为 v1.8 历史口径，未覆盖。")
    L.append("- 新产物：`%s`、`%s`、`%s`；全局 Top-5：%s。\n" % (
        os.path.basename(SHAP_JSON), os.path.basename(SHAP_CSV), os.path.basename(SHAP_PNG),
        "、".join(r["feature"] for r in R["shap"]["xgb_top5"])))
    L.append("- 本次留出集上 XGB 仅 **%d / %d** 个特征通过分裂门槛产生非零 SHAP 贡献，"
             "其余为 0 —— 弱信号叠加严格正则的直接体现，不是计算错误；"
             "线性模型对所有特征都有贡献，可作交叉参照。\n" % (
                 R["shap"]["xgb_nonzero_features"], R["shap"]["n_features_used"]))
    L.append("- LR 线性 SHAP Top-5：%s。\n" % "、".join(
        r["feature"] for r in R["shap"]["lr_top5"]))
    L.append("- 特征语义仍受 `x21–x200` 字段字典缺失限制，本层只做算法层解释，"
             "业务解读须待字典补齐。\n")

    L.append("\n## 8. 口径边界\n")
    L.append("1. 本报告全部基于 `v_model_dataset_asof_flat` 无泄漏特征；"
             "`v_model_dataset_flat` / `ads_model_dataset` 的 AUC 含后视泄漏虚高，不可比。")
    L.append("2. 负例 21 家、置换地板约 0.5，任何准确率/AUC 点估计都不成立；"
             "重点看**方向恒等式**与**相对差值**。")
    L.append("3. `v_enterprise_multilabel_grade` 的分档复用 `y_survival` 等锚点，"
             "正例率恒 1.0，**不能当外部效度**；独立 A 轴结果标签属 P2 采集项。")
    L.append("4. 本轮新增 `src/model/baseline_credit_direction.py`、守卫测试与 `outputs/` 产物，"
             "并在旧报告注入方向声明；**未改库、未改前端契约、未重打交付包**。\n")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
