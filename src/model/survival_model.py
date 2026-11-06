# -*- coding: utf-8 -*-
"""survival_model.py — 方案C：离散时间危险率模型（person-period LR）。

数据：ads_survival_period（1479 行 = 493 企业 × 最多 3 期）。
模型：logit(hazard_t) = α_t + β·x（期次哑变量 α_t 吸收基线风险）。
    协变量：as-of 14 特征（期初锚点，无泄漏）+ QCC 外部 18 维（静态，
    载入时 LEFT JOIN dim_enterprise，与 baseline_qcc 同口径；已剔除同义
    is_high_tech_union）。
评估：
  * 企业级分组 RepeatedStratifiedKFold(5×20)（按企业分组、按结局分层，
    同企业所有期同折，防跨期泄漏）；折内中位数填充 + Z-score。
  * 主指标：t=3 屏障 AUC（以企业为单位，event=(t=3 event)，score=模型
    对该企业 t=3 期的 1−hazard 累积存续概率…实际用 t=3 期 hazard 直接
    作为「未续风险分」，AUC 对 21 个事件）。
  * 对照1：仅 as-of 14；对照2：置换（打乱企业结局）。
  * 同时报 C-index（Harrell，右删失并入——此处事件均在 t=3，与屏障
    AUC 等价，仅作稳健性报告）。
前瞻输出：对 171 家 2023 队列（2026 到期）按模型打分，Top-N 风险名单
    写 outputs/survival_prospective_top.csv。

用法：/c/Users/12734/anaconda3/python.exe -m src.model.survival_model
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from src.data.db import norm_company_name  # noqa: E402,F401（占位，防误删 import 区）

DB = BACKEND / "data/db/zjtech_credit.db"
OUT_DIR = BACKEND / "outputs"

ASOF14 = [
    "core_staff_stability", "inv_patent_ratio", "m_cert", "n_inventor",
    "n_ipc_subclass", "n_patent_app", "n_patent_grant", "n_patent_invention",
    "qual_rank", "qual_rank_delta", "qual_upgrade_cum", "tech_breadth_entropy",
    "tech_breadth_ipc_class", "tech_conversion",
]
# QCC 外部 18 维（与 baseline_qcc.QCC_EXTERNAL 完全一致）
QCC_EXTERNAL = [
    "is_little_giant", "is_tech_sme", "is_renamed", "qcc_is_micro",
    "qcc_tech_level_ord", "qcc_scale_ord", "qcc_credit_level_ord",
    "zjtx_honor_cnt", "honor_cnt", "cert_cnt", "cert_valid_cnt",
    "shareholder_cnt", "patent_union_cnt", "invention_union_cnt",
    "annual_social_sec", "qcc_reg_capital_wan", "qcc_score", "qcc_tech_score",
]

N_SPLITS, N_REPEATS, SEED = 5, 20, 42


def load() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    cols = ",".join(["p.enterprise_id", "p.first_year", "p.period_t", "p.anchor_year",
                     "p.event", "p.prospective", "p.split"]
                    + [f"p.{c}" for c in ASOF14])
    pp = pd.read_sql_query(
        f"SELECT {cols} FROM ads_survival_period p", con)
    qcc = pd.read_sql_query(
        "SELECT enterprise_id, %s FROM dim_enterprise" % ",".join(QCC_EXTERNAL), con)
    name = pd.read_sql_query(
        "SELECT enterprise_id, name_display FROM dim_enterprise", con)
    con.close()
    df = pp.merge(qcc, on="enterprise_id", how="left").merge(name, on="enterprise_id", how="left")
    return df


def _standardize(X_tr, X_va, X_te):
    med = np.nanmedian(X_tr, axis=0)
    mu = np.nanmean(X_tr, axis=0)
    sd = np.nanstd(X_tr, axis=0)
    sd[sd == 0] = 1.0
    keep = np.isfinite(med) & np.isfinite(mu) & np.isfinite(sd)
    X_tr, X_va, X_te = X_tr[:, keep], X_va[:, keep], X_te[:, keep]
    med, mu, sd = med[keep], mu[keep], sd[keep]

    def fix(a):
        a = np.where(np.isnan(a), med, a)
        return (a - mu) / sd
    return fix(X_tr), fix(X_va), fix(X_te)


def make_design(df: pd.DataFrame, feats: list):
    """person-period 设计矩阵：特征 + 期次哑变量(t=1 基准)。"""
    X = df[feats].values.astype(float)
    T = df["period_t"].values
    dummies = np.column_stack([(T == t).astype(float) for t in (2, 3)])
    return np.hstack([X, dummies])


def run_cv(df_model: pd.DataFrame, feats: list, shuffle=False):
    """企业级分组 CV：以企业 t=3 结局分层，所有期同折。
    shuffle=True 时把企业结局真随机重排并映射回各行（真置换）。"""
    ents = df_model.groupby("enterprise_id").agg(
        y=("event", lambda s: int(s.max()))).reset_index()
    groups = ents["enterprise_id"].values
    y_ent = ents["y"].values.astype(int)
    if shuffle:
        rng = np.random.default_rng(SEED)
        y_perm = rng.permutation(y_ent)
        # 打乱后的企业结局映射回 df：t=3 行 event=打乱值，t=1/2 行恒 0
        ent2y = dict(zip(groups, y_perm))
        df_model = df_model.copy()
        df_model["event"] = [
            ent2y.get(e, 0) if t == 3 else 0
            for e, t in zip(df_model["enterprise_id"], df_model["period_t"])
        ]
        y_ent = y_perm

    # 企业→折 映射，然后取该折企业全部期
    rskf = RepeatedStratifiedKFold(n_splits=N_SPLITS, n_repeats=N_REPEATS, random_state=SEED)
    aucs = []
    groups_idx = np.arange(len(groups))          # 企业索引数组（可 reshape）
    for tr_ents, te_ents in rskf.split(groups_idx.reshape(-1, 1), y_ent):
        tr_set, te_set = set(groups[tr_ents]), set(groups[te_ents])
        tr = df_model[df_model.enterprise_id.isin(tr_set) & df_model.event.notna()]
        te_t3 = df_model[(df_model.enterprise_id.isin(te_set)) & (df_model.period_t == 3)
                         & df_model.event.notna()]
        if te_t3.empty or tr.empty:
            continue
        Xtr = make_design(tr, feats)
        ytr = tr["event"].values.astype(int)
        # 折内再切 val 用于标准化（简单起见用训练折自身统计量）
        Ztr, _, Zte = _standardize(Xtr, Xtr, make_design(te_t3, feats))
        lr = LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000)
        lr.fit(Ztr, ytr)
        score = lr.predict_proba(Zte)[:, 1]
        yte = te_t3["event"].values.astype(int)
        if 0 < yte.sum() < len(yte):
            aucs.append(roc_auc_score(yte, score))
    a = np.array(aucs)
    return {"auc_mean": round(float(a.mean()), 4) if len(a) else None,
            "auc_std": round(float(a.std()), 4) if len(a) else None,
            "n_folds": len(a)}


def prospective_scores(df: pd.DataFrame, feats: list):
    """对 171 家 2023 队列 t=3 前瞻行打分（训练用全部已观测数据）。"""
    known = df[df.event.notna()]
    prosp = df[(df.prospective == 1) & (df.period_t == 3)]
    Xtr = make_design(known, feats)
    ytr = known["event"].values.astype(int)
    lr = LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000)
    Ztr, _, Zp = _standardize(Xtr, Xtr, make_design(prosp, feats))
    lr.fit(Ztr, ytr)
    prosp = prosp.copy()
    prosp["risk"] = lr.predict_proba(Zp)[:, 1]
    prosp["risk_pct"] = (prosp["risk"].rank(pct=True) * 100).round(1)
    return prosp.sort_values("risk", ascending=False)


def main(argv=None) -> int:
    df = load()
    # 建模样本：event 已知的企业（307 家）的全部期
    known_ents = df.groupby("enterprise_id")["event"].apply(lambda s: s.notna().all())
    df_model = df[df.enterprise_id.isin(known_ents[known_ents].index)].copy()
    print(f"[load] 全部 {df.enterprise_id.nunique()} 家 | 建模(结局已知) {df_model.enterprise_id.nunique()} 家"
          f" | 前瞻 {int((df.prospective==1).sum())} 行")

    results = {"asof14": run_cv(df_model, ASOF14),
               "asof14+qcc18": run_cv(df_model, ASOF14 + QCC_EXTERNAL),
               "perm_qcc": run_cv(df_model, ASOF14 + QCC_EXTERNAL, shuffle=True)}
    print("\n=== 企业级分组 RepeatedStratifiedKFold(5×20)，t=3 屏障 AUC ===")
    for k, v in results.items():
        print(f"  {k:14s} AUC={v['auc_mean']}±{v['auc_std']}  (folds={v['n_folds']})")

    # 前瞻 Top 风险名单
    prosp = prospective_scores(df, ASOF14 + QCC_EXTERNAL)
    top = prosp[["enterprise_id", "name_display", "first_year", "risk", "risk_pct",
                 "n_patent_app", "cert_valid_cnt", "is_renamed"]].head(30)
    OUT_DIR.mkdir(exist_ok=True)
    top.to_csv(OUT_DIR / "survival_prospective_top.csv", index=False, encoding="utf-8-sig")
    (OUT_DIR / "survival_model_auc.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] 前瞻 Top30 风险名单: {OUT_DIR/'survival_prospective_top.csv'}")
    print(top.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
