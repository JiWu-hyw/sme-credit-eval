"""合成异质图数据生成器（demo 用，无需真实授权数据）。

产出多张"时间切片"的异质图，节点类型沿用项目定义：
    company / patent / investor / person / field / product
语义边：
    company -applies-> patent
    investor -invests-> company   （按企业违约状态选择投资机构质量）
    company -rev_invests-> investor  （invests 的严格反向，保证配对）
    company -employs-> person
    company -in_field-> field
    company -makes-> product

为了让"局部图解释"有得看、并贴合"技术要素 + 成长轨迹"叙事，本版本让
多种关系都携带与违约标签相关的可学信号：
    - company 自身前 4 维注入标签信号（基础）；
    - 违约企业的专利/人员/产品特征偏弱（前 2 维为负），正常企业偏强；
    - 违约企业倾向连到"低质量"投资机构（inv_q 偏低），正常企业连高质量；
    - 前 2 个技术领域被设为"高风险领域"（特征偏负）。
因此移除不同类型的边会对 PD 产生不同方向/幅度的影响，局部解释可高亮
多种关键邻居。
"""

import numpy as np
import torch
from torch_geometric.data import HeteroData

NODE_TYPES = ["company", "patent", "investor", "person", "field", "product"]

# 边类型（src, rel, dst）
EDGE_DEFS = [
    ("company", "applies", "patent"),
    ("investor", "invests", "company"),
    ("company", "rev_invests", "investor"),
    ("company", "employs", "person"),
    ("company", "in_field", "field"),
    ("company", "makes", "product"),
]


def generate_dataset(num_companies=80, num_snapshots=4, seed=42, feature_dim=16):
    rng = np.random.default_rng(seed)

    # 约 35% 企业违约，作为标签
    labels = (rng.random(num_companies) < 0.35).astype(np.float32)

    # company 基础特征：仅注入很弱的信号，迫使模型主要依赖图结构聚合
    base = rng.normal(0.0, 1.0, size=(num_companies, feature_dim)).astype(np.float32)
    base[:, :4] += labels[:, None] * 0.2

    data_list = []
    for t in range(num_snapshots):
        growth = 1.0 + 0.25 * t
        n_patent = int(num_companies * 2 * growth)
        n_investor = int(num_companies * 0.5 * growth) + 3
        n_person = int(num_companies * 3 * growth)
        n_field = 12
        n_product = int(num_companies * 1.5 * growth)

        data = HeteroData()
        # company 节点（含轻微时间噪声）
        comp_x = base + rng.normal(0, 0.05, (num_companies, feature_dim))
        data["company"].x = torch.tensor(comp_x, dtype=torch.float32)
        data["company"].y = torch.tensor(labels, dtype=torch.float32)
        data.num_companies = num_companies

        # ---------- 先确定边（连接关系） ----------
        # company -applies-> patent
        c_p = np.stack(
            [rng.integers(0, num_companies, n_patent), np.arange(n_patent)], 0
        )
        data["company", "applies", "patent"].edge_index = torch.tensor(
            c_p, dtype=torch.long
        )
        # investor -invests-> company：随机连接，投资关系本身不携带强标签信号
        # （关键信号改由"需聚合多个"的技术要素类邻居承载，使局部解释能高亮多种关系）
        inv_q = rng.normal(0.0, 1.0, n_investor).astype(np.float32)  # 投资机构质量分
        inv_idx = rng.integers(0, n_investor, num_companies)
        inv_c = np.stack([inv_idx, np.arange(num_companies)], 0)
        data["investor", "invests", "company"].edge_index = torch.tensor(
            inv_c, dtype=torch.long
        )
        # 严格反向边：company -rev_invests-> investor（与 invests 配对）
        rev = np.stack([inv_c[1], inv_c[0]], 0)
        data["company", "rev_invests", "investor"].edge_index = torch.tensor(
            rev, dtype=torch.long
        )
        # company -employs-> person
        c_per = np.stack(
            [rng.integers(0, num_companies, n_person), np.arange(n_person)], 0
        )
        data["company", "employs", "person"].edge_index = torch.tensor(
            c_per, dtype=torch.long
        )
        # company -in_field-> field
        c_f = np.stack(
            [np.arange(num_companies), rng.integers(0, n_field, num_companies)], 0
        )
        data["company", "in_field", "field"].edge_index = torch.tensor(
            c_f, dtype=torch.long
        )
        # company -makes-> product
        c_pr = np.stack(
            [rng.integers(0, num_companies, n_product), np.arange(n_product)], 0
        )
        data["company", "makes", "product"].edge_index = torch.tensor(
            c_pr, dtype=torch.long
        )

        # 反向边：让 company 也能接收各邻居的消息（形成 2 跳回路，模型才能
        # 综合利用"技术要素/团队/领域/产品"等多类关系，而非只靠投资边）。
        data["patent", "cited_by", "company"].edge_index = torch.tensor(
            np.stack([c_p[1], c_p[0]], 0), dtype=torch.long
        )
        data["person", "worked_at", "company"].edge_index = torch.tensor(
            np.stack([c_per[1], c_per[0]], 0), dtype=torch.long
        )
        data["field", "field_of", "company"].edge_index = torch.tensor(
            np.stack([c_f[1], c_f[0]], 0), dtype=torch.long
        )
        data["product", "made_by", "company"].edge_index = torch.tensor(
            np.stack([c_pr[1], c_pr[0]], 0), dtype=torch.long
        )

        # ---------- 再生成邻居特征（带标签相关信号） ----------
        def _neighbor_signal(edge_src, edge_dst, dst_n):
            """对每条边，按所连 company 的标签给目标(dst)邻居特征前 2 维注入信号。"""
            x = rng.normal(0, 1, (dst_n, feature_dim)).astype(np.float32)
            for k in range(edge_src.shape[0]):
                c = int(edge_src[k])
                sig = -0.35 if labels[c] == 1 else 0.35
                x[int(edge_dst[k]), :2] += sig
            return x

        data["patent"].x = torch.tensor(
            _neighbor_signal(c_p[0], c_p[1], n_patent), dtype=torch.float32
        )
        data["person"].x = torch.tensor(
            _neighbor_signal(c_per[0], c_per[1], n_person), dtype=torch.float32
        )
        data["product"].x = torch.tensor(
            _neighbor_signal(c_pr[0], c_pr[1], n_product), dtype=torch.float32
        )
        # investor：质量分写入第 0 维（很弱，退居次要）
        investor_x = rng.normal(0, 1, (n_investor, feature_dim)).astype(np.float32)
        investor_x[:, 0] += inv_q * 0.25
        data["investor"].x = torch.tensor(investor_x, dtype=torch.float32)
        # field：前 2 个设为高风险领域（特征偏负）
        field_x = rng.normal(0, 1, (n_field, feature_dim)).astype(np.float32)
        field_x[:2, :2] -= 0.35
        data["field"].x = torch.tensor(field_x, dtype=torch.float32)

        data_list.append(data)

    return data_list, labels, NODE_TYPES, EDGE_DEFS


if __name__ == "__main__":
    ds, lbl, nt, ed = generate_dataset()
    md = ds[0].metadata()
    print("snapshots:", len(ds))
    print("node types:", md[0])
    print("edge types:", len(md[1]))
    print("company nodes:", ds[0]["company"].x.shape)
    print("default ratio:", float(lbl.mean()))
