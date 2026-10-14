"""可解释 STGNN 信用评估模型（demo 版，对应 T30/T31/T34）。

结构：
    空间：每个时间切片用异构 GAT（to_hetero 自动为每类节点/边生成独立权重）聚合邻居。
    时间：把 company 节点在 T 个切片的嵌入拼成序列，GRU 捕捉成长轨迹。
    融合：时间维度注意力池化，得到企业上下文向量。
    输出：MLP 输出 0~1 违约概率(PD) / 信用分反相。

这是"能跑通、能收敛"的最小实现，后续 T31 正式版再补早停/正则/更复杂的时间卷积。
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GATConv, to_hetero


class _BaseGNN(nn.Module):
    """单类型 GNN 骨架，to_hetero 会为每种节点/边复制出专属权重。"""

    def __init__(self, hidden, heads=2):
        super().__init__()
        # to_hetero 要求关闭 add_self_loops，由异构转换器统一补自环
        self.conv1 = GATConv(hidden, hidden // heads, heads=heads, add_self_loops=False)
        self.conv2 = GATConv(hidden, hidden // heads, heads=heads, add_self_loops=False)

    def forward(self, x, edge_index):
        x = self.conv1(x, edge_index)
        x = F.elu(x)
        x = self.conv2(x, edge_index)
        x = F.elu(x)
        return x


class STGNN(nn.Module):
    def __init__(self, metadata, hidden=16, heads=2, temp_hidden=24):
        super().__init__()
        self.hidden = hidden
        self.encoder = to_hetero(_BaseGNN(hidden, heads), metadata, aggr="sum")
        self.gru = nn.GRU(hidden, temp_hidden, batch_first=True)
        self.temp_attn = nn.Linear(temp_hidden, 1)
        self.head = nn.Sequential(
            nn.Linear(temp_hidden, 16), nn.ReLU(), nn.Linear(16, 1)
        )

    def forward(self, snapshots):
        """
        snapshots: 同一批企业的 T 张异质图(HeteroData)，已搬到目标 device。
        返回: [N] 的 logit（经 sigmoid 即 PD）。
        """
        comp_emb = []
        for data in snapshots:
            out_dict = self.encoder(data.x_dict, data.edge_index_dict)
            comp_emb.append(out_dict["company"])  # [N, hidden]
        seq = torch.stack(comp_emb, dim=1)  # [N, T, hidden]
        out, _ = self.gru(seq)  # [N, T, temp_hidden]
        attn = torch.softmax(self.temp_attn(out), dim=1)  # [N, T, 1]
        ctx = (attn * out).sum(dim=1)  # [N, temp_hidden]
        return self.head(ctx).squeeze(-1)  # [N]


if __name__ == "__main__":
    from src.graph.synthetic import generate_dataset

    ds, _, nt, _ = generate_dataset(num_companies=40, num_snapshots=3)
    model = STGNN(ds[0].metadata(), hidden=16)
    logit = model([d.to("cpu") for d in ds])
    print("output shape:", tuple(logit.shape))
