"""STGNN demo 训练 / 评估 / 解释。

- 合成数据上训练，输出 AUC / KS / 准确率。
- 保存模型 checkpoint 与样例评分 JSON。
- 用 Permutation Importance 做全局可解释性（特征置乱后 AUC 下降量），并画柱状图。
- 全程不依赖 sklearn / 真实数据，torch + numpy + (可选)matplotlib 即可。
"""

import json
import os

import numpy as np
import torch
import torch.nn.functional as F

from src.graph.synthetic import generate_dataset
from src.model.stgnn import STGNN

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
OUT_DIR = os.path.join("outputs", "demo")


# ---------------- 评估指标（手写，避免额外依赖） ----------------
def roc_auc(y_true, y_score):
    y_true = np.asarray(y_true)
    y_score = np.asarray(y_score)
    order = np.argsort(y_score)
    y = y_true[order]
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    ranks = np.arange(1, len(y) + 1)
    sum_rank_pos = ranks[y == 1].sum()
    return (sum_rank_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def ks_stat(y_true, y_score):
    y_true = np.asarray(y_true)
    y_score = np.asarray(y_score)
    order = np.argsort(y_score)
    y = y_true[order]
    cum_pos = np.cumsum(y) / max(y.sum(), 1)
    cum_neg = np.cumsum(1 - y) / max(len(y) - y.sum(), 1)
    return float(np.max(np.abs(cum_pos - cum_neg)))


@torch.no_grad()
def evaluate(model, snapshots, y):
    model.eval()
    snaps = [d.to(DEVICE) for d in snapshots]
    logit = model(snaps)
    prob = torch.sigmoid(logit).cpu().numpy()
    y = y.cpu().numpy() if hasattr(y, "cpu") else np.asarray(y)
    pred = (prob >= 0.5).astype(int)
    auc = roc_auc(y, prob)
    ks = ks_stat(y, prob)
    acc = float((pred == y).mean())
    return prob, {"AUC": auc, "KS": ks, "Accuracy": acc}


def train(num_companies=80, num_snapshots=4, epochs=80, lr=1e-3, seed=42):
    os.makedirs(OUT_DIR, exist_ok=True)
    ds, labels, _, _ = generate_dataset(
        num_companies=num_companies, num_snapshots=num_snapshots, seed=seed
    )
    metadata = ds[0].metadata()
    model = STGNN(metadata, hidden=16).to(DEVICE)
    y = torch.tensor(labels, dtype=torch.float32).to(DEVICE)

    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = F.binary_cross_entropy_with_logits

    print(f"[device] {DEVICE}  companies={num_companies}  snapshots={num_snapshots}")
    for ep in range(1, epochs + 1):
        model.train()
        opt.zero_grad()
        snaps = [d.to(DEVICE) for d in ds]
        logit = model(snaps)
        loss = loss_fn(logit, y)
        loss.backward()
        opt.step()
        if ep == 1 or ep % 20 == 0:
            with torch.no_grad():
                prob = torch.sigmoid(logit).cpu().numpy()
                print(
                    f"  epoch {ep:3d}  loss={loss.item():.4f}  "
                    f"AUC={roc_auc(labels, prob):.3f}"
                )

    # 最终评估
    prob, metrics = evaluate(model, ds, y)
    print("[metrics]", {k: round(v, 4) for k, v in metrics.items()})

    # 保存 checkpoint
    ckpt = os.path.join(OUT_DIR, "stgnn_demo.pt")
    torch.save(
        {
            "model_state": model.state_dict(),
            "metadata": metadata,
            "hidden": 16,
            "num_companies": num_companies,
            "num_snapshots": num_snapshots,
        },
        ckpt,
    )

    # 样例评分
    idx = np.random.default_rng(0).choice(num_companies, 8, replace=False)
    samples = [
        {
            "company_id": int(i),
            "pd": round(float(prob[i]), 4),
            "credit_score": round(float(100 - prob[i] * 100), 2),
            "true_default": int(labels[i]),
        }
        for i in idx
    ]
    with open(os.path.join(OUT_DIR, "sample_scores.json"), "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)

    # 全局可解释性：Permutation Importance（仅 company 前 16 维特征）
    importance = permutation_importance(model, ds, y, feature_dim=16)
    with open(os.path.join(OUT_DIR, "feature_importance.json"), "w", encoding="utf-8") as f:
        json.dump(
            {f"f{i}": round(float(v), 4) for i, v in enumerate(importance)},
            f,
            ensure_ascii=False,
            indent=2,
        )
    plot_importance(importance)

    return model, metrics, samples, importance, ckpt


@torch.no_grad()
def permutation_importance(model, snapshots, y, feature_dim=16, seed=7):
    base_prob, _ = evaluate(model, snapshots, y)
    base_auc = roc_auc(y.cpu().numpy(), base_prob)
    y_np = y.cpu().numpy()
    imp = []
    rng = np.random.default_rng(seed)
    for j in range(feature_dim):
        perturbed = [d.clone() for d in snapshots]
        perm = rng.permutation(snapshots[0]["company"].x.shape[0])
        for d in perturbed:
            x = d["company"].x.clone()
            x[:, j] = x[perm, j]
            d["company"].x = x
        prob, _ = evaluate(model, perturbed, y)
        imp.append(base_auc - roc_auc(y_np, prob))
    return np.array(imp)


def plot_importance(importance):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("[plot] matplotlib 不可用，跳过柱状图（数据已写入 feature_importance.json）")
        return
    order = np.argsort(importance)
    labels = [f"f{i}" for i in order]
    vals = importance[order]
    plt.figure(figsize=(7, 4))
    colors = ["#c0392b" if v > 0 else "#7f8c8d" for v in vals]
    plt.barh(labels, vals, color=colors)
    plt.xlabel("Permutation Importance (AUC 下降量)")
    plt.title("STGNN 全局特征重要性（demo）")
    plt.tight_layout()
    path = os.path.join(OUT_DIR, "feature_importance.png")
    plt.savefig(path, dpi=120)
    print(f"[plot] 已保存 {path}")


if __name__ == "__main__":
    train()
