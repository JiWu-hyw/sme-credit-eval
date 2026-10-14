"""局部图解释（GNNExplainer 思路：边扰动归因，demo 版对应 T41）。

为什么这么做：
    标准 GNNExplainer 在"异质图 + 时序封装模型(to_hetero)"上兼容性脆弱，
    且其核心思想就是"衡量每条边对预测的贡献"。本模块用更稳健、完全可控的
    「边删除归因」复现这一思想——对目标企业移除某条直接关联边（及其反向边，
    保持异质图对称）后，观察违约概率 PD 的变化量 Δ，Δ 越大说明该邻居/边对评分越关键。

方法：
    1. 取最新时间切片（代表企业"当前"状态）作为解释基础。
    2. 基线 PD = 模型在完整图上对该企业的输出。
    3. 对每条与目标企业相连（入射或出射）的"正向关系"，单独移除该边 + 其反向边后重算 PD。
    4. delta = 基线PD - 删除后PD：
         delta > 0  → 该边原本"推高"风险（风险来源，前端标红）
         delta < 0  → 该边原本"降低"风险（正面支撑，前端标绿）
    5. 取 |delta| 最大的 top_k 个邻居作为关键关联。

输出结构（供前端直接消费）：
    {
      "<company_id>": {
        "baseline_pd": float,
        "neighbors": [ {node_id, type, rel, delta}, ... ]  # 已按 |delta| 降序、截断 top_k
      }, ...
    }

用法：
    from src.app.local_explain import compute_local_explanations
    exp = compute_local_explanations(model, snaps, top_k=8)
"""

import numpy as np
import torch

# 与导出脚本保持一致的关系中文名（正向边）
REL_CN = {
    ("company", "applies", "patent"): "申请专利",
    ("investor", "invests", "company"): "获投资",
    ("company", "employs", "person"): "雇佣核心人员",
    ("company", "in_field", "field"): "所属技术领域",
    ("company", "makes", "product"): "生产产品",
}

# 正向边 -> 其反向边（删除时成对移除，保持异质图对称）
REV_PAIRS = {
    ("investor", "invests", "company"): ("company", "rev_invests", "investor"),
    ("company", "applies", "patent"): ("patent", "cited_by", "company"),
    ("company", "employs", "person"): ("person", "worked_at", "company"),
    ("company", "in_field", "field"): ("field", "field_of", "company"),
    ("company", "makes", "product"): ("product", "made_by", "company"),
}
# 全部反向边类型（用于跳过、不重复处理）
REV_EDGE_TYPES = set(REV_PAIRS.values())


def _remove_col(ei, k):
    """返回删去第 k 列后的 edge_index（空边时返回 [2,0]）。"""
    if ei.shape[1] > 1:
        return torch.cat([ei[:, :k], ei[:, k + 1:]], dim=1)
    return ei[:, :0]


@torch.no_grad()
def compute_local_explanations(model, snaps, top_k=8):
    """对每张快照最后一张(last)上的每个 company 计算边扰动归因。

    snaps: 已 `.to(device)` 的 snapshot 列表（来自 generate_dataset）。
    返回 company_id(str) -> {baseline_pd, neighbors[top_k]} 的字典。
    """
    model.eval()
    device = next(model.parameters()).device
    last = snaps[-1].to(device)

    base_logit = model([last])
    base_pd = torch.sigmoid(base_logit).cpu().numpy()
    n = base_pd.shape[0]

    # 预存最新快照各边类型的 CPU edge_index，避免重复拷贝大特征
    ei_store = {et: last[et].edge_index.cpu().clone() for et in last.edge_types}

    explanations = {}
    for c in range(n):
        nbrs = []
        for et in last.edge_types:
            if et in REV_EDGE_TYPES:
                continue  # 反向边由对应的正向关系成对处理，跳过避免重复/不对称
            s_t, r, d_t = et
            ei = ei_store[et]
            E = int(ei.shape[1])
            if E == 0:
                continue
            arr = ei.numpy()
            # c 仅当作为"company 类型节点"出现在该边，避免与 patent/product 等
            # 共用 0-based 索引空间导致的误判（例如 company_4 被误当作 patent）
            if s_t == "company":
                cols = np.where(arr[0] == c)[0]
            else:  # company 为 dst（invests）
                cols = np.where(arr[1] == c)[0]
            for k in cols:
                s, d = int(arr[0, k]), int(arr[1, k])
                # 用边类型判断邻居节点类型，避免 company 与 investor/patent 等共用
                # 0-based 索引空间导致的误判（如 company_67 与 investor_67 重叠）
                nb_type, nb_idx = (d_t, d) if s_t == "company" else (s_t, s)

                pert = last.clone()
                pert[et].edge_index = _remove_col(ei, k).to(device)
                # 成对移除反向边（同一对 company-邻居）
                ret = REV_PAIRS[et]
                rev_ei = ei_store[ret]
                rev_arr = rev_ei.numpy()
                rev_cols = np.where(
                    ((rev_arr[0] == c) & (rev_arr[1] == nb_idx))
                    | ((rev_arr[0] == nb_idx) & (rev_arr[1] == c))
                )[0]
                if len(rev_cols):
                    pert[ret].edge_index = _remove_col(rev_ei, rev_cols[0]).to(device)

                pd_pert = float(torch.sigmoid(model([pert]))[c].cpu())
                delta = float(base_pd[c] - pd_pert)
                nbrs.append({
                    "node_id": f"{nb_type}_{nb_idx}",
                    "type": nb_type,
                    "rel": REL_CN.get(et, r),
                    "delta": round(delta, 4),
                })

        nbrs.sort(key=lambda x: abs(x["delta"]), reverse=True)
        explanations[str(c)] = {
            "baseline_pd": round(float(base_pd[c]), 4),
            "neighbors": nbrs[:top_k],
        }

    return explanations


if __name__ == "__main__":
    import os
    import time

    from src.graph.synthetic import generate_dataset
    from src.model.stgnn import STGNN

    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    CKPT = os.path.join(ROOT, "outputs", "demo", "stgnn_demo.pt")

    ckpt = torch.load(CKPT, map_location=DEVICE, weights_only=False)
    model = STGNN(ckpt["metadata"], hidden=ckpt["hidden"]).to(DEVICE)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    ds, _, _, _ = generate_dataset(
        num_companies=ckpt["num_companies"], num_snapshots=ckpt["num_snapshots"], seed=42
    )
    snaps = [d.to(DEVICE) for d in ds]

    t0 = time.time()
    exp = compute_local_explanations(model, snaps, top_k=8)
    print(f"[local_explain] 计算 {len(exp)} 家企业局部解释 耗时 {time.time()-t0:.1f}s")
    sample = max(exp.items(), key=lambda kv: kv[1]["baseline_pd"])
    cid, info = sample
    print(f"  风险最高企业 #{cid} baseline_pd={info['baseline_pd']}")
    for nb in info["neighbors"][:5]:
        mark = "↑风险" if nb["delta"] > 0 else "↓风险"
        print(f"    {nb['node_id']} ({nb['rel']}) Δ={nb['delta']:+.4f} {mark}")
