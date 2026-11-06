# -*- coding: utf-8 -*-
"""baseline_msme.py — 方案D：Kaggle MSME 对照建模（特征族迁移性验证）。

问题：本库弱标签（qual_survival）AUC 0.87 主要由 QCC 外部维度驱动——
     是特征真的有效，还是弱标签的伪相关？
对照：Kaggle MSME（1707 家 / 真实银行违约标签 / 87 特征）。其特征族与
     本库 QCC 维度高度同构（注册资本/股东数/资质证书数/社保参保/信用等级…）。

实验设计（三臂，同一协议 RepeatedStratifiedKFold(5,20) + LR(C=0.1,balanced)）：
  A1  MSME 全 87 特征              —— 该数据集本身的可建模上限（文献基准）
  A2  MSME「同构子集」              —— 与本库 QCC 18 维语义对应的特征
                                     （Registered_capital/SH_num/Certificate_num*/
                                     社保*/Credit_Grade*/Filing* 等）
  A3  置换对照                      —— 真打乱标签的零假设
若 A2 显著 > A3：说明「企查查式外部维度」对真实信贷违约确有判别力 →
支持本库 0.87 的外部驱动结论可迁移；若 A2 ≈ A3：外部维度对真实违约无信号，
本库 0.87 需重新解释（可能是弱标签特异性）。

同构映射（name_map 反查原始列名）：
  qcc_reg_capital_wan     ↔ Registered_capital (Ten thousand Yuan)
  shareholder_cnt         ↔ SH_num
  cert_cnt/cert_valid_cnt ↔ Certificate_num_*（t-1/2/3 年窗口合计）
  annual_social_sec       ↔ 社保参保人数类（*insurance* / *social*）
  qcc_credit_level_ord    ↔ Ratepaying_Credit_Grade_* / Credit_Grade
  honor_cnt               ↔ Honor/Filing_information_num_*
  规模                    ↔ MS_num / Establishment_Duration
输出：outputs/msme_control_auc.json + outputs/msme_control_report.md

用法：/c/Users/12734/anaconda3/python.exe -m src.model.baseline_msme
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB = BACKEND / "data/db/zjtech_credit.db"
OUT_DIR = BACKEND / "outputs"
N_SPLITS, N_REPEATS, SEED = 5, 20, 42

# 与本库 QCC 18 维同构的 MSME 特征（原始列名子串匹配）
ISO_PATTERNS = [
    "Registered_capital", "SH_num", "Certificate_num", "insurance",
    "Ratepaying_Credit_Grade", "Filing_information", "Honor",
    "MS_num", "Establishment_Duration", "Branch_num",
]


def load():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    row = cur.execute(
        "SELECT sample_id, label, name_map, %s FROM ext_msme_sample"
        % ",".join(f"f{i:02d}" for i in range(1, 88))
    ).fetchall()
    con.close()
    assert row, "ext_msme_sample 为空"
    import json as _j
    nm = _j.loads(row[0][2])
    feat_names = [nm["feat_names"][f"f{i:02d}"] for i in range(1, 88)]
    X = np.array([[float(v) if v is not None else np.nan
                   for v in r[3:]] for r in row], dtype=float)
    y = np.array([r[1] for r in row], dtype=int)
    return X, y, feat_names


def _standardize(X_tr, X_te):
    med = np.nanmedian(X_tr, axis=0)
    mu = np.nanmean(X_tr, axis=0)
    sd = np.nanstd(X_tr, axis=0)
    sd[sd == 0] = 1.0
    keep = np.isfinite(med) & np.isfinite(mu) & np.isfinite(sd)
    X_tr, X_te = X_tr[:, keep], X_te[:, keep]
    med, mu, sd = med[keep], mu[keep], sd[keep]
    return (np.where(np.isnan(X_tr), med, X_tr) - mu) / sd, \
           (np.where(np.isnan(X_te), med, X_te) - mu) / sd


def run_cv(X, y, shuffle=False):
    if shuffle:
        rng = np.random.default_rng(SEED)
        y = rng.permutation(y)
    keep = np.nanstd(X, axis=0) > 0
    X = X[:, keep]
    rskf = RepeatedStratifiedKFold(n_splits=N_SPLITS, n_repeats=N_REPEATS,
                                   random_state=SEED)
    aucs = []
    for tr, te in rskf.split(X, y):
        Ztr, Zte = _standardize(X[tr], X[te])
        lr = LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000)
        lr.fit(Ztr, y[tr])
        aucs.append(roc_auc_score(y[te], lr.predict_proba(Zte)[:, 1]))
    a = np.array(aucs)
    return {"auc_mean": round(float(a.mean()), 4),
            "auc_std": round(float(a.std()), 4), "n_folds": len(a)}


def main() -> int:
    X, y, feat_names = load()
    iso_idx = [i for i, c in enumerate(feat_names)
               if any(p.lower() in c.lower() for p in ISO_PATTERNS)]
    print(f"[load] n={len(y)} 正类={int(y.sum())} ({y.mean():.2%}) | "
          f"全特征 {X.shape[1]} | 同构子集 {len(iso_idx)}")
    print("       同构特征:", [feat_names[i] for i in iso_idx][:12], "…")

    res = {
        "msme_full87": run_cv(X, y),
        "msme_iso_subset": run_cv(X[:, iso_idx], y),
        "perm_iso": run_cv(X[:, iso_idx], y, shuffle=True),
    }
    print("\n=== MSME 对照（RepeatedStratifiedKFold 5×20, LR balanced）===")
    for k, v in res.items():
        print(f"  {k:16s} AUC={v['auc_mean']}±{v['auc_std']}")

    iso_feats = [feat_names[i] for i in iso_idx]
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "msme_control_auc.json").write_text(
        json.dumps({**res, "iso_features": iso_feats}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 报告
    f_, i_, p_ = res["msme_full87"], res["msme_iso_subset"], res["perm_iso"]
    verdict = ("同构特征族对真实违约有显著判别力 → 本库外部驱动结论可迁移"
               if i_["auc_mean"] - p_["auc_mean"] > 0.10
               else "同构特征族对真实违约判别力有限 → 本库 0.87 需谨慎解释")
    lines = [
        "# 方案D：Kaggle MSME 对照实验（特征族迁移性验证）\n",
        "> 数据：`ext_msme_sample` 1,707 家（Kaggle 30S-CR，真实银行违约标签，",
        "> 违约率 5.98% ≈ 本库弱标签正类率 6.8%）",
        "> 协议：RepeatedStratifiedKFold(5×20) + LR(C=0.1, balanced) + 置换对照\n",
        "## 结果\n",
        "| 臂 | 特征 | AUC |",
        "|---|---|---|",
        f"| A1 全量 | 87 特征 | {f_['auc_mean']}±{f_['auc_std']} |",
        f"| A2 同构子集 | {len(iso_idx)} 特征 | {i_['auc_mean']}±{i_['auc_std']} |",
        f"| A3 置换 | 同构子集 | {p_['auc_mean']}±{p_['auc_std']} |\n",
        f"**判定：{verdict}**（A2−A3 = {round(i_['auc_mean']-p_['auc_mean'],4)}）\n",
        "## 同构特征清单（%d 个）\n" % len(iso_feats),
    ]
    for c in iso_feats:
        lines.append(f"- {c}")
    lines.append("\n## 与本库结论的关系\n")
    lines.append("- 本库：QCC 外部维度 → 弱标签 AUC 0.59→0.87（方案C 生存口径 0.87）")
    lines.append("- 本对照：验证「企查查式外部维度」特征族在**真实违约**标签上的判别力，")
    lines.append("  回答『特征有效 vs 弱标签伪相关』的方法论问题。")
    (OUT_DIR / "msme_control_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[done] {OUT_DIR/'msme_control_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
