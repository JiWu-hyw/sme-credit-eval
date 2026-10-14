"""面板自建异质图上的 GAT 模型（对应 T30–T34 的真实数据版本）。

- 输入：`src/graph/panel_graph.py` 构建的 3 类节点 / 6 种边异质图
- 模型：GATConv ×2 + 线性头，经 `to_hetero` 转为异质 GNN
- 任务：record 节点的违约二分类（BCEWithLogits，pos_weight 处理不均衡）
- 口径：与 `src/model/baseline.py` 的**同一 80/20 分组留出集、同一 top-k 特征**，
  指标由 `src/model/metrics.py` 统一计算，可直接与 LR/XGBoost 横向对比。

用法
----
    .venv\\Scripts\\python.exe -m src.model.gat_panel
"""

import json
import os
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.model_selection import GroupShuffleSplit
from torch_geometric.nn import GATConv, to_hetero

from src.graph.panel_graph import build_panel_hetero
from src.model.metrics import compute_metrics

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "outputs")
RESULTS_OUT = os.path.join(OUT_DIR, "gat_panel_results.json")
CKPT_OUT = os.path.join(OUT_DIR, "gat_panel.pt")
BASELINE_JSON = os.path.join(OUT_DIR, "baseline_results.json")

HIDDEN = 64
HEADS = 4
DROPOUT = 0.5
EPOCHS = 300
LR = 0.01
WEIGHT_DECAY = 5e-4
PATIENCE = 40
SEED = 42


class GATBackbone(torch.nn.Module):
    """同质 GAT 骨干，随后由 to_hetero 展开为异质模型。"""

    def __init__(self, hidden: int, heads: int, dropout: float):
        super().__init__()
        self.conv1 = GATConv((-1, -1), hidden, heads=heads, add_self_loops=False, dropout=dropout)
        self.conv2 = GATConv((-1, -1), hidden, heads=1, add_self_loops=False, dropout=dropout)
        self.dropout = dropout

    def forward(self, x, edge_index):
        x = self.conv1(x, edge_index)
        x = F.elu(x)
        x = F.dropout(x, p=self.dropout, training=self.training)
        x = self.conv2(x, edge_index)
        return x


class HeteroGAT(torch.nn.Module):
    def __init__(self, metadata, hidden: int = HIDDEN, heads: int = HEADS, dropout: float = DROPOUT):
        super().__init__()
        self.gnn = to_hetero(GATBackbone(hidden, heads, dropout), metadata, aggr="sum")
        self.head = torch.nn.Linear(hidden, 1)

    def forward(self, data):
        out = self.gnn(data.x_dict, data.edge_index_dict)
        return self.head(out["record"]).squeeze(-1)


def _select_features_from_baseline(n_features: int = 40) -> Optional[List[str]]:
    """复用基线留出集筛出的 top-k 特征名，保证与基线同口径。"""
    if not os.path.exists(BASELINE_JSON):
        return None
    with open(BASELINE_JSON, encoding="utf-8") as f:
        res = json.load(f)
    sel = res.get("holdout_row", {}).get("selected_features")
    return sel[:n_features] if sel else None


class MLPControl(torch.nn.Module):
    """无图对照：只用 record 自身特征（不使用任何边），用来判断"图结构是否真有增益"。"""

    def __init__(self, in_dim: int, hidden: int = HIDDEN, dropout: float = DROPOUT):
        super().__init__()
        self.net = torch.nn.Sequential(
            torch.nn.Linear(in_dim, hidden),
            torch.nn.ReLU(),
            torch.nn.Dropout(dropout),
            torch.nn.Linear(hidden, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


def train_no_graph_control(data, meta, epochs: int = EPOCHS, verbose: bool = True) -> Dict[str, object]:
    """在与 GAT 完全相同的特征 / 划分 / 早停协议下训练一个无图 MLP。"""
    torch.manual_seed(SEED)
    X = data["record"].x
    y = data["record"].y
    train_mask, test_mask = meta["train_mask"], meta["test_mask"]
    user_idx = meta["user_idx"]

    tr_pos = np.where(train_mask.numpy())[0]
    gss = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=SEED)
    sub_tr, sub_va = next(gss.split(tr_pos, groups=user_idx[tr_pos]))
    fit_mask = torch.zeros_like(train_mask); val_mask = torch.zeros_like(train_mask)
    fit_mask[tr_pos[sub_tr]] = True; val_mask[tr_pos[sub_va]] = True

    model = MLPControl(X.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor([meta["pos_weight"]]))

    best = {"val_auc": -1.0, "epoch": -1, "state": None}
    for ep in range(1, epochs + 1):
        model.train(); opt.zero_grad()
        loss = loss_fn(model(X)[fit_mask], y[fit_mask])
        loss.backward(); opt.step()
        model.eval()
        with torch.no_grad():
            va = compute_metrics(y[val_mask].numpy().astype(int),
                                 torch.sigmoid(model(X)[val_mask]).numpy())
        if va["auc"] > best["val_auc"]:
            best = {"val_auc": va["auc"], "epoch": ep,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
        if ep - best["epoch"] >= PATIENCE:
            break

    model.load_state_dict(best["state"]); model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model(X)).numpy()
    m = compute_metrics(y[test_mask].numpy().astype(int), prob[test_mask.numpy()])
    if verbose:
        print("  MLP对照 AUC=%.4f  KS=%.4f  Acc=%.4f  Recall=%.4f  F1=%.4f（best epoch=%d）"
              % (m["auc"], m["ks"], m["accuracy"], m["recall"], m["f1"], best["epoch"]))
    return {"model": "无图 MLP 对照（同特征/同划分/同早停）", "test_metrics": m,
            "best_epoch": best["epoch"], "best_val_auc": best["val_auc"]}


def train(epochs: int = EPOCHS, verbose: bool = True) -> Dict[str, object]:
    torch.manual_seed(SEED)
    np.random.seed(SEED)

    # ---- 1) 与基线同口径的特征子集 ----
    fn = _select_features_from_baseline(40)
    if fn is None:
        idx = None
        if verbose:
            print("[gat] 未找到基线特征表，使用全部特征")
    else:
        import pandas as pd
        from src.data.preprocess import PANEL_OUT as _P
        from src.graph.panel_graph import NON_FEATURE
        df_cols = [c for c in pd.read_parquet(_P).columns if c not in NON_FEATURE]
        idx = np.array([df_cols.index(c) for c in fn if c in df_cols], dtype=np.int64)
        if verbose:
            print("[gat] 复用基线 top-%d 特征（与基线同口径）" % len(idx))

    data, meta = build_panel_hetero(feature_idx=idx)
    if verbose:
        print("[gat] 图: record=%d company=%d cluster=%d | 边类型 %d"
              % (meta["n_record"], meta["n_company"], meta["n_cluster"],
                 len(data.edge_types)))

    train_mask = meta["train_mask"]
    test_mask = meta["test_mask"]
    user_idx = meta["user_idx"]
    y = data["record"].y

    # ---- 2) 从训练企业里再切 15% 作验证集（按 userid 分组，防泄漏）----
    tr_np = train_mask.numpy()
    tr_pos = np.where(tr_np)[0]
    tr_groups = user_idx[tr_pos]
    gss = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=SEED)
    sub_tr, sub_va = next(gss.split(tr_pos, groups=tr_groups))
    fit_mask = torch.zeros_like(train_mask)
    val_mask = torch.zeros_like(train_mask)
    fit_mask[tr_pos[sub_tr]] = True
    val_mask[tr_pos[sub_va]] = True

    # ---- 3) 训练 ----
    model = HeteroGAT(data.metadata())
    opt = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    pos_weight = torch.tensor([meta["pos_weight"]], dtype=torch.float32)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    best = {"val_auc": -1.0, "epoch": -1, "state": None}
    history = []
    for ep in range(1, epochs + 1):
        model.train()
        opt.zero_grad()
        logit = model(data)
        loss = loss_fn(logit[fit_mask], y[fit_mask])
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            logit = model(data)
            va = compute_metrics(y[val_mask].numpy().astype(int), torch.sigmoid(logit[val_mask]).numpy())
        history.append({"epoch": ep, "loss": float(loss), "val_auc": va["auc"]})
        if va["auc"] > best["val_auc"]:
            best = {"val_auc": va["auc"], "epoch": ep,
                    "state": {k: v.detach().clone() for k, v in model.state_dict().items()}}
        if ep - best["epoch"] >= PATIENCE:
            break

    model.load_state_dict(best["state"])
    model.eval()
    with torch.no_grad():
        prob = torch.sigmoid(model(data)).numpy()
    m_test = compute_metrics(y[test_mask].numpy().astype(int), prob[test_mask.numpy()])

    if verbose:
        print("[gat] 训练 %d 轮，best epoch=%d（val AUC=%.4f）" % (len(history), best["epoch"], best["val_auc"]))
        print("      record 节点平均 PD=%.4f（真实正例率 %.4f）"
              % (prob.mean(), float(y.mean())))
        print("  GAT     AUC=%.4f  KS=%.4f  Acc=%.4f  Recall=%.4f  F1=%.4f"
              % (m_test["auc"], m_test["ks"], m_test["accuracy"], m_test["recall"], m_test["f1"]))

    out = {
        "model": "HeteroGAT（GATConv×2 + 线性头，to_hetero）",
        "graph": {
            "node_types": {nt: int(data[nt].num_nodes) for nt in ["record", "company", "cluster"]},
            "edge_types": ["-".join(et) for et in data.edge_types],
            "n_features": meta["n_features"],
        },
        "protocol": "与 baseline 同一 80/20 分组留出集、同一 top-k 特征；验证集从训练企业中按 userid 切 15%",
        "train": {"epochs_run": len(history), "best_epoch": best["epoch"], "best_val_auc": best["val_auc"],
                  "pos_weight": meta["pos_weight"]},
        "test_metrics": m_test,
        "no_graph_control": train_no_graph_control(data, meta, epochs=epochs, verbose=verbose),
        "history_tail": history[-5:],
    }
    torch.save({"state_dict": model.state_dict(), "meta": {k: v for k, v in meta.items() if k != "feature_names"}},
               CKPT_OUT)
    with open(RESULTS_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if verbose:
        print("[gat] 结果已写出: %s" % RESULTS_OUT)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    res = train()

    # ---- 与基线对比 ----
    if os.path.exists(BASELINE_JSON):
        with open(BASELINE_JSON, encoding="utf-8") as f:
            base = json.load(f)
        print("\n[gat] 三方对比（同一留出集）")
        print("  %-10s AUC=%.4f  KS=%.4f  Recall=%.4f" % (
            "LR", base["holdout_row"]["logistic_regression"]["auc"],
            base["holdout_row"]["logistic_regression"]["ks"],
            base["holdout_row"]["logistic_regression"]["recall"]))
        print("  %-10s AUC=%.4f  KS=%.4f  Recall=%.4f" % (
            "XGBoost", base["holdout_row"]["xgboost"]["auc"],
            base["holdout_row"]["xgboost"]["ks"], base["holdout_row"]["xgboost"]["recall"]))
        m = res["test_metrics"]
        print("  %-10s AUC=%.4f  KS=%.4f  Recall=%.4f" % ("GAT", m["auc"], m["ks"], m["recall"]))
        c = res["no_graph_control"]["test_metrics"]
        print("  %-10s AUC=%.4f  KS=%.4f  Recall=%.4f" % ("MLP对照", c["auc"], c["ks"], c["recall"]))
        print("  置换对照（打乱标签 CV）XGBoost AUC=%.4f（噪声地板）"
              % base["cv_row_shuffled_label"]["xgboost"]["auc_mean"])
        print("  → 图增益(AUC) = GAT - MLP对照 = %+.4f（是否>0 决定“图结构”在本数据上是否有效）"
              % (m["auc"] - c["auc"]))


if __name__ == "__main__":
    main()
