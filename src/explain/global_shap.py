"""SHAP 全局解释（对应 T40 —— 全局解释：Permutation Importance + SHAP 特征贡献排序）。

解释对象是 `src/model/baseline.py` 固定留出集上训练好的 **XGBoost**（TreeExplainer）
与 **LR**（LinearExplainer）——保证"解释的就是评估的那个模型"，不做二次训练。

产出
----
outputs/shap_global.json              全局贡献排序（JSON，供前端消费）
outputs/shap_global_summary.png       SHAP 全局重要性条形图（Top-20）
outputs/shap_top_features.csv         特征贡献表（CSV，便于贴进报告）

口径说明
--------
- 全局重要性 = 测试集上 |SHAP 值| 的均值（mean|SHAP|）。
- 方向：特征值与 SHAP 值的 Spearman 相关符号。
  `+` = 该特征取值越大越推高违约概率（风险因子）；`-` = 越大越降低违约概率（保护因子）。
- 业务提示沿用 xlsx 附录中提供方标注的 x1–x20 语义，其余列为“未知语义”，
  须待字段字典补齐后再解读。

用法
----
    .venv\\Scripts\\python.exe -m src.explain.global_shap
"""

import json
import os
from typing import Dict, List

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src.data.preprocess import FIELD_HINTS
from src.model.baseline import run_holdout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "outputs")
JSON_OUT = os.path.join(OUT_DIR, "shap_global.json")
PNG_OUT = os.path.join(OUT_DIR, "shap_global_summary.png")
CSV_OUT = os.path.join(OUT_DIR, "shap_top_features.csv")
TOP_K = 20


def _setup_cjk_font():
    """让 matplotlib 能正常渲染中文（找不到中文字体则回退英文标签）。"""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, rcParams
    for name in ["Microsoft YaHei", "SimHei", "SimSun"]:
        try:
            font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
            rcParams["font.sans-serif"] = [name]
            rcParams["axes.unicode_minus"] = False
            return True
        except Exception:
            continue
    return False


def _direction(feature_values: np.ndarray, shap_values: np.ndarray) -> int:
    """特征值与 SHAP 值的相关方向：+1 风险因子，-1 保护因子，0 无法判定。"""
    if np.std(feature_values) == 0 or np.std(shap_values) == 0:
        return 0
    r, _ = spearmanr(feature_values, shap_values)
    if np.isnan(r):
        return 0
    return 1 if r > 0 else -1


def compute_shap(model, X_explain: np.ndarray, model_kind: str, X_background=None) -> np.ndarray:
    """返回 shape=(n_samples, n_features) 的 SHAP 值矩阵。"""
    import shap
    if model_kind == "xgb":
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(X_explain)
    else:
        bg = X_background if X_background is not None else shap.sample(X_explain, min(100, len(X_explain)))
        explainer = shap.LinearExplainer(model, bg)
        sv = explainer.shap_values(X_explain)
    sv = np.asarray(sv)
    if sv.ndim == 3:  # 某些版本返回 (n, f, classes)
        sv = sv[:, :, -1]
    return sv


def _rank(feats: List[str], idx: np.ndarray, sv: np.ndarray, Z: np.ndarray) -> List[Dict[str, object]]:
    """按 mean|SHAP| 排序，返回带方向与业务提示的列表。"""
    mean_abs = np.abs(sv).mean(axis=0)
    order = np.argsort(-mean_abs)
    rows = []
    for rank, j in enumerate(order, start=1):
        fname = feats[idx[j]]
        rows.append({
            "rank": rank,
            "feature": fname,
            "mean_abs_shap": float(mean_abs[j]),
            "direction": _direction(Z[:, j], sv[:, j]),
            "hint": FIELD_HINTS.get(fname, "未知语义（待字段字典）"),
        })
    return rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cjk = _setup_cjk_font()

    print("[shap] 复用基线留出集模型（不做二次训练）...")
    res = run_holdout("row", verbose=False)
    art = res["_artifacts"]
    feats, idx = art["feats"], art["idx"]
    X, y, te = art["X"], art["y"], art["te"]

    Z_te = X[te][:, idx]      # 测试集 × 选中特征（模型就是在这些列上训练的）
    Z_tr = X[art["tr"]][:, idx]
    y_te = y[te]

    # 载体样本：用测试集本身解释（模型未见过的样本，更能反映泛化行为）
    n_bg = min(200, len(Z_tr))
    rng = np.random.default_rng(42)
    bg_idx = rng.choice(len(Z_tr), size=n_bg, replace=False)

    out = {
        "explained_models": ["xgboost", "logistic_regression"],
        "explain_set": "holdout test set (%d samples, %d positives)" % (len(y_te), int(y_te.sum())),
        "n_features_used": int(len(idx)),
        "note": "mean|SHAP| 为全局重要性；direction：+1=风险因子（值越大越推高违约概率），-1=保护因子。",
    }

    summary_rows = []
    for kind, model_key in (("xgb", "model_xgb"), ("lr", "model_lr")):
        sv = compute_shap(art[model_key], Z_te, kind, X_background=Z_tr[bg_idx])
        ranked = _rank(feats, idx, sv, Z_te)
        out["%s_top%d" % (kind, TOP_K)] = ranked[:TOP_K]
        out["%s_all_importance" % kind] = {r["feature"]: r["mean_abs_shap"] for r in ranked}
        print("[shap] %s: top-5 = %s" % (
            kind, ", ".join("%s(%.4f)" % (r["feature"], r["mean_abs_shap"]) for r in ranked[:5])))
        if kind == "xgb":
            for r in ranked:
                summary_rows.append((r["rank"], r["feature"], r["mean_abs_shap"], r["direction"], r["hint"]))

    # ---- CSV ----
    pd.DataFrame(summary_rows, columns=["rank", "feature", "mean_abs_shap", "direction", "hint"]).to_csv(
        CSV_OUT, index=False, encoding="utf-8-sig"
    )

    # ---- PNG 条形图 ----
    try:
        import matplotlib.pyplot as plt
        top = summary_rows[:TOP_K][::-1]
        names = [t[1] for t in top]
        vals = [t[2] for t in top]
        colors = ["#c0392b" if t[3] >= 0 else "#1e8449" for t in top]  # 红=风险因子 绿=保护因子
        fig, ax = plt.subplots(figsize=(8, 7), dpi=150)
        ax.barh(names, vals, color=colors)
        ax.set_xlabel("mean |SHAP|" if not cjk else "平均 |SHAP| 值（全局重要性）")
        ax.set_title(("XGBoost 全局特征贡献 Top-%d（红=风险因子，绿=保护因子）" % TOP_K)
                     if cjk else ("XGBoost global feature importance (Top-%d)" % TOP_K))
        for i, (v, t) in enumerate(zip(vals, top)):
            ax.text(v * 1.01, i, "  " + t[1], va="center", fontsize=7)
        plt.tight_layout()
        fig.savefig(PNG_OUT)
        plt.close(fig)
        print("[shap] 图已写出: %s（中文渲染=%s）" % (PNG_OUT, cjk))
    except Exception as e:
        out["plot_error"] = str(e)
        print("[shap] 绘图失败（不影响 JSON/CSV）: %s" % e)

    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[shap] 结果已写出: %s" % JSON_OUT)
    print("[shap] 表格已写出: %s" % CSV_OUT)


if __name__ == "__main__":
    main()
