"""真实面板自建异质图（对应 T20–T24 的"真实数据"落地版本）。

背景：当前真实面板**没有企业侧实体数据**（无专利/投资机构/人员/领域），
无法搭建立项书描述的六类实体异质图；六类实体图由 `src/graph/synthetic.py`
做架构验证（见 docs/实验结果报告）。本模块在真实数据上给出**可用的最小异质图**：

节点类型（3 类）
    record   3,465 个申请记录（主节点，带 label 与 x 特征）
    company  1,714 家企业（特征 = 其记录的均值 + 记录数）
    cluster  K 个画像簇（KMeans 对 record 特征聚类，特征 = 簇质心）

边类型（4 组、8 条有向边，双向成对）
    record  --belongs_company--> company      company --has_record--> record
    record  --in_cluster-->       cluster     cluster --has_record_rec--> record
    record  --sim_record-->       record      特征余弦相似 top-k
    company --sim_company-->      company     聚合特征余弦相似 top-k

时间维：`month_index` 落在 record 节点特征里，可按月切子图（面板纵深浅，
不做企业内长序列，符合《数据质量报告》§3 结论）。

用法
----
    from src.graph.panel_graph import build_panel_hetero
    data, meta = build_panel_hetero(feature_idx)
"""

import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from torch_geometric.data import HeteroData

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PANEL = os.path.join(ROOT, "data", "processed", "zhejiang_panel_clean.parquet")

NON_FEATURE = {"userid", "apply_time", "month_index", "label", "region_code", "split", "n_records"}

NODE_TYPES = ["record", "company", "cluster"]
EDGE_DEFS = [
    ("record", "belongs_company", "company"),
    ("company", "has_record", "record"),
    ("record", "in_cluster", "cluster"),
    ("cluster", "has_record_rec", "record"),
    ("record", "sim_record", "record"),
    ("company", "sim_company", "company"),
]


def _knn_edges(features: np.ndarray, k: int) -> np.ndarray:
    """余弦相似 top-k 邻居，返回 shape=(2, n*k) 的 edge_index（不含自环）。"""
    from sklearn.preprocessing import normalize
    f = normalize(features.astype(np.float64), axis=1, copy=True)
    k = min(k, len(f) - 1)
    sim = f @ f.T
    np.fill_diagonal(sim, -np.inf)
    nbr = np.argpartition(-sim, kth=k - 1, axis=1)[:, :k]
    src = np.repeat(np.arange(len(f)), k)
    dst = nbr.reshape(-1)
    return np.stack([src, dst], axis=0)


def build_panel_hetero(
    feature_idx: Optional[np.ndarray] = None,
    k_sim: int = 5,
    n_clusters: int = 10,
    seed: int = 42,
) -> Tuple[HeteroData, Dict[str, object]]:
    """构建真实面板异质图。

    feature_idx：使用哪些特征列（下标）；None 表示全部。建议传基线筛出的 top-k，
    与基线同口径。所有统计（KMeans/相似度）均只使用特征，不触碰 label，无泄漏。
    """
    df = pd.read_parquet(PANEL)
    feats = [c for c in df.columns if c not in NON_FEATURE]
    use = feats if feature_idx is None else [feats[i] for i in feature_idx]

    n_rec = len(df)
    users = df["userid"].values
    uniq_users = pd.unique(users)
    comp_id = {u: i for i, u in enumerate(uniq_users)}
    user_idx = np.array([comp_id[u] for u in users], dtype=np.int64)

    X_rec = df[use].values.astype(np.float32)
    y_rec = df["label"].values.astype(np.float32)
    month = df["month_index"].values.astype(np.float32)

    # ---- 企业节点特征：其记录的均值 + 记录数 ----
    n_comp = len(uniq_users)
    X_comp = np.zeros((n_comp, X_rec.shape[1] + 1), dtype=np.float32)
    np.add.at(X_comp[:, :-1], user_idx, X_rec)
    counts = np.bincount(user_idx, minlength=n_comp).astype(np.float32)
    X_comp[:, :-1] /= np.maximum(counts[:, None], 1.0)
    X_comp[:, -1] = np.log1p(counts)

    # ---- 画像簇节点 ----
    from sklearn.cluster import KMeans
    k = min(n_clusters, max(2, len(df) // 50))
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X_rec)
    rec_cluster = km.labels_.astype(np.int64)
    X_cluster = km.cluster_centers_.astype(np.float32)

    data = HeteroData()
    data["record"].x = torch.tensor(X_rec, dtype=torch.float32)
    data["record"].y = torch.tensor(y_rec, dtype=torch.float32)
    data["record"].month = torch.tensor(month, dtype=torch.float32)
    data["record"].num_nodes = n_rec
    data["company"].x = torch.tensor(X_comp, dtype=torch.float32)
    data["company"].num_nodes = n_comp
    data["cluster"].x = torch.tensor(X_cluster, dtype=torch.float32)
    data["cluster"].num_nodes = k

    # ---- 边 ----
    rec_idx = np.arange(n_rec, dtype=np.int64)
    # 归属边（双向）
    rec_comp = np.stack([rec_idx, user_idx], 0)
    data["record", "belongs_company", "company"].edge_index = torch.tensor(rec_comp, dtype=torch.long)
    data["company", "has_record", "record"].edge_index = torch.tensor(rec_comp[::-1].copy(), dtype=torch.long)
    # 画像边（双向）
    rec_clu = np.stack([rec_idx, rec_cluster], 0)
    data["record", "in_cluster", "cluster"].edge_index = torch.tensor(rec_clu, dtype=torch.long)
    data["cluster", "has_record_rec", "record"].edge_index = torch.tensor(rec_clu[::-1].copy(), dtype=torch.long)
    # 相似边
    data["record", "sim_record", "record"].edge_index = torch.tensor(
        _knn_edges(X_rec, k_sim), dtype=torch.long
    )
    data["company", "sim_company", "company"].edge_index = torch.tensor(
        _knn_edges(X_comp, k_sim), dtype=torch.long
    )

    meta = {
        "n_record": n_rec,
        "n_company": n_comp,
        "n_cluster": k,
        "n_features": len(use),
        "n_train_record": int((df["split"] == "train").sum()),
        "n_test_record": int((df["split"] == "test").sum()),
        "train_mask": torch.tensor((df["split"] == "train").values, dtype=torch.bool),
        "test_mask": torch.tensor((df["split"] == "test").values, dtype=torch.bool),
        "user_idx": user_idx,
        "feature_names": use,
        "pos_weight": float((y_rec == 0).sum() / max((y_rec == 1).sum(), 1)),
    }
    return data, meta


if __name__ == "__main__":
    d, m = build_panel_hetero()
    print("nodes:", {nt: int(d[nt].num_nodes) for nt in NODE_TYPES})
    for et in EDGE_DEFS:
        print("edges %-38s %s" % ("-".join(et), tuple(d[et].edge_index.shape)))
    print("pos_weight=%.2f  train=%d test=%d" % (m["pos_weight"], m["n_train_record"], m["n_test_record"]))
