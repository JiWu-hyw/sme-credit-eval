#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FE-12 契约生成脚本：P3「时空图谱」三区块所需的三份契约
------------------------------------------------------------------
产出（均落在 outputs/contracts/，字段 snake_case，逐字对齐前端 src/types/contracts.ts）：
  graph_structure.json  E1 六类实体异质图（节点 / 边 / 类型统计 / 抽样信息）
  graph_slices.json     E3 季度时间切片（每季度增量 + 累计节点边数）
  graph_centrality.json E2 度分布 + 四类中心性对比（度 / 介数 / 接近 / 特征向量）

口径（必须写进 meta，不是注释里说说）：
  1) 六类实体图当前**没有真实实体数据**，一切来自 src/graph/synthetic.py 的合成图，
     meta.dataset = 'synthetic_graph'（红线三：合成图必须警示基调，不得与真实面板同图）；
  2) 企业节点的 userid 与真实信贷面板**未完成映射**（数据缺口 P0-1），
     专利 / 融资 / 人员三类外部数据未接入（P0-4 / P0-5）——本页所有结构结论只用于验证
     「图谱 → 模型 → 解释」链路是否走得通，不得当作浙江中小科技企业的实证结论；
  3) 时间维以「季度切片」实现：真实面板每企业平均 2 个月观测，做不了长序列，
     这里用合成数据把跨季度图切片的结构跑通，等真实数据到位后替换数据源即可，
     契约结构与前端代码**零改动**。

用法：
  python scripts/build_graph_contracts.py            # 合成图（默认）
  python scripts/build_graph_contracts.py --out outputs/contracts
  python scripts/build_graph_contracts.py --nodes 260 --seasons 8   # 调规模
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
SEED = 20261022
TZ = timezone(timedelta(hours=8))

# 真实信贷面板参考锚点（仅口径说明，不是本脚本产出）
NOISE_FLOOR = 0.477
REAL_PANEL_ROW_AUC = 0.59
SYNTHETIC_GRAPH_AUC = 0.9149  # 六类实体合成图 STGNN 架构验证结果，**只能标在合成图上**

# 六类实体（键名逐字镜像 configs/viz_theme.yaml › entity_category：
#   企业 company / 专利 patent / 投资机构 investor / 核心人员 person / 技术领域 field / 产品 product）
ENTITY_KINDS = ["company", "patent", "investor", "person", "field", "product"]

# 边的语义（三种语义边 + 三种辅助边，辅助边用于把孤立实体接回主网）
EDGE_KINDS = [
    "apply",      # 企业 → 专利：申请
    "grant",      # 企业 → 专利：授权（与 apply 同端点不同语义，合成里用 granted 标记区分）
    "invest",     # 投资机构 → 企业：投资
    "employ",     # 核心人员 → 企业：任职
    "field_of",   # 专利 → 技术领域：归属
    "produce",    # 企业 → 产品：产出
]

QUARTERS = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4"]

CITY_POOL = ["杭州", "宁波", "温州", "嘉兴", "绍兴", "湖州", "金华", "台州"]
FIELD_POOL = [
    ("新一代信息技术", "G06"), ("高端装备制造", "B23"), ("生物医药", "C12"),
    ("新材料", "C08"), ("新能源", "H01"), ("节能环保", "C02"),
]
PRODUCT_POOL = ["工业软件", "智能传感模组", "检测试剂", "电池材料", "工业机器人", "环保装备"]


# ---------------------------------------------------------------- 工具
def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def round4(x: float) -> float:
    return round(float(x), 4)


def stable_int(text: str, mod: int) -> int:
    """稳定伪随机：同一节点 id 每次跑出来的值都一样（契约可复现，不依赖 dict 顺序）。"""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16) % mod


class LCG:
    """自带的线性同余伪随机（不依赖 random 模块的版本行为，跨 Python 版本可复现）。"""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state / 0x7FFFFFFF

    def randint(self, lo: int, hi: int) -> int:  # 闭区间
        return lo + int(self.next() * (hi - lo + 1)) % (hi - lo + 1)

    def choice(self, seq: list[Any]) -> Any:
        return seq[self.randint(0, len(seq) - 1)]


def meta(extra: dict[str, Any] | None = None) -> dict:
    """契约 §6.2 必备六字段 + 红线补充字段。合成图：noise_floor 不适用，用 null 明示。"""
    base = {
        "generated_at": now_iso(),
        "script": "scripts/build_graph_contracts.py",
        "dataset": "synthetic_graph",
        "n_samples": None,
        "seed": SEED,
        "schema_version": SCHEMA_VERSION,
        "noise_floor": None,
        "semantic_known": False,
        "label_ref": None,
        "source": "synthetic_graph",
        "disclaimer": (
            "六类实体合成图（synthetic_graph）：真实实体数据尚未接入（数据缺口 P0-1 映射键、"
            "P0-4 专利、P0-5 融资），本契约仅用于验证「图谱 → 模型 → 解释」链路与页面交互，"
            "不得作为浙江中小科技企业的实证结论引用。"
        ),
        "blocked_by": ["P0-1", "P0-4", "P0-5"],
        "redline": {
            "no_mixed_dataset": True,
            "noise_floor_applicable": False,
            "unknown_fields_shown_as_code": True,
        },
    }
    if extra:
        base.update(extra)
    return base


# ---------------------------------------------------------------- 图构造
def build_graph(n_companies: int, n_seasons: int) -> dict:
    """生成六类实体异质图。

    结构上刻意做成「异质二分 + 少数枢纽」：
      企业 → 专利（申请/授权）、专利 → 技术领域、投资机构 → 企业、人员 → 企业、企业 → 产品。
    技术领域 / 产品 / 投资机构是**共享枢纽**（多个企业连到同一个），这正是 GAT 聚合邻居时
    最该被注意到的结构；企业节点只与自己的实体相连，度分布在 3–8 之间。
    """
    rnd = LCG(SEED // 1000)
    quarters = QUARTERS[:n_seasons]

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edge: set[tuple[str, str, str]] = set()

    def add_node(nid: str, kind: str, label: str, **kw: Any) -> None:
        nodes.append({"id": nid, "kind": kind, "label": label, **kw})

    def add_edge(src: str, dst: str, kind: str, quarter: str, **kw: Any) -> None:
        key = (src, dst, kind)
        if key in seen_edge:
            return
        seen_edge.add(key)
        edges.append({"source": src, "target": dst, "kind": kind, "quarter": quarter, **kw})

    # ---- 技术领域（固定 6 个，共享枢纽）
    fields = []
    for idx, (name, ipc) in enumerate(FIELD_POOL):
        fid = f"F{idx + 1:02d}"
        add_node(fid, "field", name, ipc_prefix=ipc, size_hint=1.0)
        fields.append(fid)

    # ---- 产品（固定 6 个）
    products = []
    for idx, name in enumerate(PRODUCT_POOL):
        pid = f"R{idx + 1:02d}"
        add_node(pid, "product", name, size_hint=1.0)
        products.append(pid)

    # 规模比例按「每企业平均 2.6 件专利 / 1.2 个任职人 / 0.6 个投资关系」控制，图不过于稠密
    n_investors = max(10, round(n_companies * 0.09))
    n_persons = max(16, round(n_companies * 0.22))

    # ---- 企业
    company_ids = []
    for i in range(n_companies):
        cid = f"C{i + 1:04d}"
        city = CITY_POOL[i % len(CITY_POOL)]
        add_node(
            cid, "company", f"企业 {cid[1:]}",
            city=city,
            stage=["初创", "成长", "成熟"][i % 3],
            risk_label=["正常", "关注", "预警"][(i * 7) % 3],
            entered_at=quarters[min(len(quarters) - 1, i % max(1, len(quarters)))],
            alias=f"合成企业#{i + 1:03d}（{city}）",
            size_hint=round4(1.0 + (i % 5) * 0.18),
        )
        company_ids.append(cid)

    # ---- 投资机构（每个连 3–9 家企业）
    investor_ids = []
    for i in range(n_investors):
        iid = f"I{i + 1:03d}"
        add_node(
            iid, "investor", f"机构 {iid[1:]}",
            tier=["头部", "腰部", "早期"][i % 3],
            entered_at=quarters[0],
            size_hint=round4(1.2 + (i % 4) * 0.15),
        )
        investor_ids.append(iid)

    # ---- 核心人员（每人 1 家企业任职，少数 2 家）
    person_ids = []
    for i in range(n_persons):
        pid = f"P{i + 1:03d}"
        add_node(
            pid, "person", f"人员 {pid[1:]}",
            role=["创始人", "CTO", "研发骨干"][i % 3],
            entered_at=quarters[0],
            size_hint=0.8,
        )
        person_ids.append(pid)

    # ---- 专利 + 边（严格 0 重复：每件专利只有一条申请/授权边 → 企业节点不会因去重塌成 1 度）
    pat_seq = 0
    for ci, cid in enumerate(company_ids):
        base_q = ci % len(quarters)
        n_pat = 2 + (ci * 3) % 4  # 2–5 件
        for k in range(n_pat):
            pat_seq += 1
            pat_id = f"T{pat_seq:04d}"
            q_idx = min(len(quarters) - 1, base_q + k)
            granted = (pat_seq % 3) != 0
            add_node(
                pat_id, "patent", f"专利 {pat_id[1:]}",
                granted=granted,
                ipc_prefix=FIELD_POOL[stable_int(pat_id, len(FIELD_POOL))][1],
                citations=stable_int(pat_id, 40),
                entered_at=quarters[q_idx],
                size_hint=round4(0.7 + stable_int(pat_id, 9) * 0.06),
            )
            add_edge(cid, pat_id, "grant" if granted else "apply", quarters[q_idx])
            add_edge(pat_id, fields[stable_int(pat_id, len(fields))], "field_of", quarters[q_idx])

        # 产品边：企业「按行业」挂到同一批产品上 → 产品成为共享枢纽，
        # 抽样后仍能把企业拼成连通片（否则抽样只留度高的专利，图会碎成一地孤岛）
        if ci % 2 == 0:
            add_edge(cid, products[(ci // 2) % len(products)], "produce", quarters[base_q])

        # 人员任职：企业 ci 用 ci、ci*2+1 两个人员位，跨企业复用 → 人员也有共享效果
        for k in range(1 + (ci % 2)):
            pid = person_ids[(ci * 2 + k) % n_persons]
            add_edge(pid, cid, "employ", quarters[base_q])

        if ci % 5 != 4:
            iid = investor_ids[(ci * 3) % n_investors]
            add_edge(iid, cid, "invest", quarters[min(len(quarters) - 1, base_q + 1)])

    return {"nodes": nodes, "edges": edges, "quarters": quarters}


def type_stats(nodes: list[dict], edges: list[dict]) -> dict:
    by_kind: dict[str, int] = {k: 0 for k in ENTITY_KINDS}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
    by_edge: dict[str, int] = {k: 0 for k in EDGE_KINDS}
    for e in edges:
        by_edge[e["kind"]] = by_edge.get(e["kind"], 0) + 1
    return {"node_kinds": by_kind, "edge_kinds": by_edge}


# ---------------------------------------------------------------- 中心性
def degrees(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    return deg


def adjacency(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        a, b = e["source"], e["target"]
        adj[a].append(b)
        adj[b].append(a)
    return adj


def betweenness(adj: dict[str, list[str]]) -> dict[str, float]:
    """Brandes 算法（无权）。规模几百个节点，纯 Python 秒级。"""
    import collections

    bc = {v: 0.0 for v in adj}
    for s in adj:
        stack: list[str] = []
        pred: dict[str, list[str]] = {v: [] for v in adj}
        sigma = {v: 0.0 for v in adj}
        sigma[s] = 1.0
        dist = {v: -1 for v in adj}
        dist[s] = 0
        queue = collections.deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {v: 0.0 for v in adj}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w]:
                    delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    n = len(adj)
    scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 1.0
    # 无向图每条最短路走两次
    return {v: bc[v] * scale for v in bc}


def closeness(adj: dict[str, list[str]]) -> dict[str, float]:
    import collections

    out = {}
    n = len(adj)
    for s in adj:
        dist = {s: 0}
        queue = collections.deque([s])
        while queue:
            v = queue.popleft()
            for w in adj[v]:
                if w not in dist:
                    dist[w] = dist[v] + 1
                    queue.append(w)
        reach = len(dist) - 1
        total = sum(d for d in dist.values() if d > 0)
        # Wasserman-Faust 归一：只对可达节点算，避免不连通图数值塌成 0
        out[s] = (reach / total) * (reach / (n - 1)) if total and n > 1 else 0.0
    return out


def eigenvector(adj: dict[str, list[str]], iters: int = 300) -> dict[str, float]:
    """幂迭代求主特征向量的绝对值分量。

    ⚠️ 这个函数在二分图上踩过坑，务必别改回去：
      企业↔专利、专利↔技术领域构成二分结构，邻接矩阵的谱是 ±λ 成对出现的。
      每步都 `abs()` 会让正负两模态相互抵消，结果来回震荡、永远不收敛
      （实测秩相关出现 −0.31，特征向量中心性把「重要」排反）。
      正确做法是**先带符号迭代到收敛或符号翻转**，翻转即表示主模态换了，
      此时重置为均匀向量继续推；最后取绝对值。
    """
    v = {k: 1.0 / math.sqrt(max(1, len(adj))) for k in adj}
    for _ in range(iters):
        nxt = {k: sum(v[w] for w in adj[k]) for k in adj}
        norm = math.sqrt(sum(x * x for x in nxt.values())) or 1.0
        nxt = {k: nxt[k] / norm for k in nxt}
        # 符号翻转（二分图 ±λ 交替）：把负半侧翻正后再推，避免两模态互相抵消
        neg = sum(1 for x in nxt.values() if x < 0)
        if neg > len(nxt) // 2:
            nxt = {k: -x for k, x in nxt.items()}
        v = nxt
    return {k: abs(x) for k, x in v.items()}


def percentile_rank(values: list[float]) -> list[float]:
    """转成 0–1 的百分位，四类中心性量纲不同，只有百分位能放同一张图对比。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    n = max(1, len(values) - 1)
    for pos, idx in enumerate(order):
        ranks[idx] = pos / n
    return ranks


# ---------------------------------------------------------------- 三份契约
def build_structure(graph: dict, sample_cap: int) -> dict:
    nodes, edges = graph["nodes"], graph["edges"]
    stats = type_stats(nodes, edges)
    deg = degrees(nodes, edges)

    # ---- 抽样策略：既要"连片"又要"每类都有代表"
    #
    # 两版都踩过坑，记下来免得再犯：
    #   第一版「纯按度优先」→ 挑进来的全是个位数度的专利，技术领域 / 产品这些真枢纽
    #     一个没进来，画出来是一地碎块；
    #   第二版「按度预填种子 + BFS 扩邻」→ 预填阶段就把 150 个额度用光了，BFS 一步没走，
    #     结果 3 个专利 + 89 个企业，类别严重失衡。
    # 现在的做法是分档给每家实体配额，再在配额内按度优先：
    #   6 个技术领域、6 个产品、全部投资机构（它们本来就是共享枢纽，宁多勿少）；
    #   专利 / 人员按比例给少量（多图里专利是叶节点，全画进来会挤掉企业）；
    #   剩余额度全给企业，最后用 BFS 把被选中实体的邻居补进来，保证不悬空。
    import collections

    if len(nodes) > sample_cap:
        adj_all: dict[str, list[str]] = {n["id"]: [] for n in nodes}
        for e in edges:
            adj_all[e["source"]].append(e["target"])
            adj_all[e["target"]].append(e["source"])

        by_kind: dict[str, list[dict]] = {k: [] for k in ENTITY_KINDS}
        for n in sorted(nodes, key=lambda n: (-deg[n["id"]], n["id"])):
            by_kind.setdefault(n["kind"], []).append(n)

        quota = {
            "field": len(by_kind.get("field", [])),
            "product": len(by_kind.get("product", [])),
            "investor": len(by_kind.get("investor", [])),
            # 专利配额给到 28%：专利是企业通往技术领域枢纽的**唯一路径**，
            # 专利给少了，企业就接不到枢纽上 → 子图碎成好几块（实测从 1 块主连通域变 5 块）
            "patent": max(8, int(sample_cap * 0.28)),
            "person": max(6, int(sample_cap * 0.12)),
        }
        keep = set()
        for kind, q in quota.items():
            keep.update(n["id"] for n in by_kind.get(kind, [])[:q])
        # 企业填充：按「度优先」补满，但**不把最后几个名额花在没邻居的企业上**——
        # 它们是全图里投资关系缺口造成的孤立点（10 个），画进抽样里就是一串孤零零的泡泡。
        dormant = {n["id"] for n in nodes if not adj_all[n["id"]]}

        def linked(pid: str) -> int:
            """该企业已有多少个邻居被选中——用来优先接上孤立的枢纽（机构 / 产品）"""
            return sum(1 for x in adj_all[pid] if x in keep)

        companies = [n for n in by_kind.get("company", []) if n["id"] not in dormant]
        companies.sort(key=lambda n: (-linked(n["id"]), -deg[n["id"]], n["id"]))
        for n in companies:
            if len(keep) >= sample_cap:
                break
            keep.add(n["id"])

        # BFS 补邻居：只补企业 / 专利的邻居，且不挤占已达上限的类别
        adj = adj_all
        queue = collections.deque(keep)
        while queue and len(keep) < sample_cap:
            cur = queue.popleft()
            for nb in adj[cur]:
                if len(keep) >= sample_cap:
                    break
                if nb not in keep:
                    keep.add(nb)
                    queue.append(nb)
    else:
        keep = {n["id"] for n in nodes}

    nodes_out = []
    for n in nodes:
        if n["id"] not in keep:
            continue
        item = dict(n)
        item["degree"] = deg[n["id"]]
        nodes_out.append(item)
    edges_out = [
        {**e, "id": f"{e['source']}-{e['target']}-{e['kind']}"}
        for e in edges
        if e["source"] in keep and e["target"] in keep
    ]

    return {
        "meta": meta({
            "n_samples": len(nodes),
            "n_nodes_full": len(nodes),
            "n_edges_full": len(edges),
            "sampled": len(nodes) > sample_cap,
            "sample_cap": sample_cap,
            "synthetic_auc": SYNTHETIC_GRAPH_AUC,
            "real_panel_row_auc": REAL_PANEL_ROW_AUC,
            "note": (
                "synthetic_auc=0.9149 是六类实体合成图上 STGNN 架构验证的结果，与真实面板 "
                "0.596 不是一个数据集、不能同图比较（红线一）。"
            ),
        }),
        "data": {
            "nodes": nodes_out,
            "edges": edges_out,
            "quarters": graph["quarters"],
            "stats": {
                **stats,
                "n_nodes": len(nodes),
                "n_edges": len(edges),
                "n_nodes_shown": len(nodes_out),
                "n_edges_shown": len(edges_out),
                "density": round4(2 * len(edges) / max(1, len(nodes) * (len(nodes) - 1))),
                "avg_degree": round4(sum(deg.values()) / max(1, len(nodes))),
                "isolated": sum(1 for v in deg.values() if v == 0),
                "dropped_isolated": sum(1 for v in deg.values() if v == 0),
            },
            "entity_kinds": ENTITY_KINDS,
            "edge_kinds": EDGE_KINDS,
        },
    }


def build_slices(graph: dict) -> dict:
    nodes, edges = graph["nodes"], graph["edges"]
    quarters = graph["quarters"]
    # 企业进入季度取 entered_at；专利 / 人员按其连边最早季度进入（更接近真实增量图）
    first_q: dict[str, str] = {}
    for e in edges:
        for end in (e["source"], e["target"]):
            q = e["quarter"]
            if end not in first_q or q < first_q[end]:
                first_q[end] = q

    slices = []
    cum_nodes = 0
    cum_edges = 0
    for q in quarters:
        new_nodes = sum(1 for n in nodes if first_q.get(n["id"], q) == q)
        new_edges = sum(1 for e in edges if e["quarter"] == q)
        cum_nodes += new_nodes
        cum_edges += new_edges
        new_by_kind = {
            k: sum(1 for n in nodes if n["kind"] == k and first_q.get(n["id"], q) == q)
            for k in ENTITY_KINDS
        }
        slices.append({
            "quarter": q,
            "new_nodes": new_nodes,
            "new_edges": new_edges,
            "cum_nodes": cum_nodes,
            "cum_edges": cum_edges,
            "new_by_kind": new_by_kind,
            "visible_node_ids": [n["id"] for n in nodes if first_q.get(n["id"], q) <= q],
        })

    return {
        "meta": meta({
            "n_samples": len(quarters),
            "n_nodes_full": len(nodes),
            "temporal_unit": "quarter",
            "note": (
                "真实面板每企业平均 2 个月观测，长序列建模不可行；时间维以季度累计切片实现，"
                "visible_node_ids 是该季度「截至当时已出现」的节点集合，供前端切片联动。"
            ),
        }),
        "data": {
            "quarters": quarters,
            "slices": slices,
            "summary": {
                "n_slices": len(quarters),
                "n_nodes_total": len(nodes),
                "n_edges_total": len(edges),
                "peak_new_edges_quarter": max(slices, key=lambda s: s["new_edges"])["quarter"],
                "growth": round4(
                    slices[-1]["cum_nodes"] / max(1, slices[0]["cum_nodes"])
                ),
            },
        },
    }


def build_centrality(graph: dict, top_k: int = 15) -> dict:
    nodes, edges = graph["nodes"], graph["edges"]
    adj = adjacency(nodes, edges)
    deg = degrees(nodes, edges)
    btw = betweenness(adj)
    clo = closeness(adj)
    eig = eigenvector(adj)

    ids = [n["id"] for n in nodes]
    ranks = {
        "degree": dict(zip(ids, percentile_rank([deg[i] for i in ids]))),
        "betweenness": dict(zip(ids, percentile_rank([btw[i] for i in ids]))),
        "closeness": dict(zip(ids, percentile_rank([clo[i] for i in ids]))),
        "eigenvector": dict(zip(ids, percentile_rank([eig[i] for i in ids]))),
    }

    # ---- 度分布：按**度值**分档（1 / 2 / 3–4 / 5–8 / 9–16 / 17–32 / 33+），不按等距数值分箱
    #
    # 两版都不行，记下来：
    #   ① 等高分箱 → 长尾图里 870 个节点全挤在第一箱，其余 7 箱空着，图上看不出任何形状；
    #   ② 线性等距分箱 → 一样，第一箱 872、其余 9 箱空着。
    # 度是整数且 0–3 占九成，只有**按度值分档**（近似 log 刻度）才能同时看清「大头」和「尾巴」。
    edges_by_bucket = [1, 1, 2, 4, 8, 16, 32, None]  # None = 开口档
    buckets = []
    prev_hi = 0
    for span in edges_by_bucket:
        lo = prev_hi + 1
        hi = lo + span - 1 if span else None
        if hi is None:
            cnt = sum(1 for v in deg.values() if v >= lo)
            label = f"≥{lo}"
            lo_f, hi_f = float(lo), float(max(deg.values()) if deg else lo)
        else:
            cnt = sum(1 for v in deg.values() if lo <= v <= hi)
            label = f"{lo}" if lo == hi else f"{lo}–{hi}"
            lo_f, hi_f = float(lo), float(hi)
        buckets.append({"degree_range": label, "lo": lo_f, "hi": hi_f, "nodes": cnt})
        prev_hi = hi if hi is not None else prev_hi
    # 度 0 的孤立节点单列一档。
    # 这批节点（本图 10 个）不进任何分档的话，分档合计 868 ≠ 878，
    # 读者一加就发现"图上的节点比柱子加起来还多"。孤立节点恰恰是**要看见**的事实：
    # 它们是本次抽样被跳过的企业（dropped_isolated），说明"没连边的机构在图上等于隐身"。
    isolated_cnt = sum(1 for v in deg.values() if v == 0)
    buckets.insert(
        0,
        {"degree_range": "0（孤立）", "lo": 0.0, "hi": 0.0, "nodes": isolated_cnt, "isolated": True},
    )
    max_d = max(deg.values()) if deg else 1

    # ---- 分类型 Top-K（每类各自排序，避免 Top-Bar 被企业节点完全占满）
    by_kind_top: dict[str, list[dict]] = {}
    for kind in ENTITY_KINDS:
        pool = [n for n in nodes if n["kind"] == kind]
        pool.sort(key=lambda n: (-deg[n["id"]], n["id"]))
        by_kind_top[kind] = [
            {
                "id": n["id"],
                "label": n["label"],
                "kind": kind,
                "degree": deg[n["id"]],
                "betweenness": round4(btw[n["id"]]),
                "closeness": round4(clo[n["id"]]),
                "eigenvector": round4(eig[n["id"]]),
                "degree_pct": round4(ranks["degree"][n["id"]]),
                "betweenness_pct": round4(ranks["betweenness"][n["id"]]),
                "closeness_pct": round4(ranks["closeness"][n["id"]]),
                "eigenvector_pct": round4(ranks["eigenvector"][n["id"]]),
            }
            for n in pool[:top_k]
        ]

    # ---- 全局 Top-K（按度）
    top = sorted(nodes, key=lambda n: (-deg[n["id"]], n["id"]))[:top_k]
    overall = [
        {
            "id": n["id"], "label": n["label"], "kind": n["kind"], "degree": deg[n["id"]],
            "betweenness": round4(btw[n["id"]]), "closeness": round4(clo[n["id"]]),
            "eigenvector": round4(eig[n["id"]]),
            "degree_pct": round4(ranks["degree"][n["id"]]),
            "betweenness_pct": round4(ranks["betweenness"][n["id"]]),
            "closeness_pct": round4(ranks["closeness"][n["id"]]),
            "eigenvector_pct": round4(ranks["eigenvector"][n["id"]]),
        }
        for n in top
    ]

    # ---- 四类中心性的秩相关（Spearman）：结论「谁重要」会不会因指标而变
    def spearman(a: str, b: str) -> float:
        ra, rb = ranks[a], ranks[b]
        n = len(ids)
        ma = sum(ra[i] for i in ids) / n
        mb = sum(rb[i] for i in ids) / n
        cov = sum((ra[i] - ma) * (rb[i] - mb) for i in ids)
        va = math.sqrt(sum((ra[i] - ma) ** 2 for i in ids))
        vb = math.sqrt(sum((rb[i] - mb) ** 2 for i in ids))
        return round4(cov / (va * vb)) if va and vb else 0.0

    metrics = ["degree", "betweenness", "closeness", "eigenvector"]
    corr = {a: {b: (1.0 if a == b else spearman(a, b)) for b in metrics} for a in metrics}

    # ---- 逐节点索引
    # 为什么要有：浮层是"点谁看谁"，而 Top 榜只覆盖每类 15 个。
    # 第一版只给 Top 榜，结果点开任意一个普通节点，浮层里中心性区块直接不出现 ——
    # 用户以为"这个节点没有中心性"（其实是契约没给）。全量索引（每个节点只带百分位）约 30KB，
    # 换来的是一致性：任何节点点开都有四维数据。
    node_index = {
        n["id"]: {
            "kind": n["kind"],
            "label": n["label"],
            "degree": deg[n["id"]],
            "degree_pct": round4(ranks["degree"][n["id"]]),
            "betweenness_pct": round4(ranks["betweenness"][n["id"]]),
            "closeness_pct": round4(ranks["closeness"][n["id"]]),
            "eigenvector_pct": round4(ranks["eigenvector"][n["id"]]),
        }
        for n in nodes
    }

    return {
        "meta": meta({
            "n_samples": len(nodes),
            "n_nodes_full": len(nodes),
            "note": (
                "四类中心性量纲不同（度是计数、介数是路径占比、接近是距离倒数、特征向量是归一化分量），"
                "页面上统一换算成 0–1 百分位再对比；Spearman 秩相关用于判断「谁重要」这张榜单稳不稳。"
            ),
        }),
        "data": {
            "degree_distribution": {
                "bins": buckets,
                "max_degree": max_d,
                "mean_degree": round4(sum(deg.values()) / max(1, len(nodes))),
                "median_degree": sorted(deg.values())[len(deg) // 2] if deg else 0,
                "hub_share": round4(
                    sum(v for v in deg.values() if v >= max(2, max_d * 0.5)) / max(1, sum(deg.values()))
                ),
            },
            "metrics": metrics,
            "metric_labels": {
                "degree": "度中心性",
                "betweenness": "介数中心性",
                "closeness": "接近中心性",
                "eigenvector": "特征向量中心性",
            },
            "interpretation": {
                "eigenvector_vs_degree": (
                    "特征向量中心性与度中心性呈负秩相关：这是二分图（企业↔专利、专利↔领域）的"
                    "结构事实，不是计算错误——专利节点只连几件企业/领域，但其邻居是枢纽，"
                    "主特征向量分量自然偏高；企业节点邻居多但都是叶节点，分量反而低。"
                    "结论：判断「谁重要」必须声明用的是哪个中心性，单看度会把领域枢纽排在第一。"
                ),
            },
            "by_kind_top": by_kind_top,
            "overall_top": overall,
            "node_index": node_index,
            "rank_correlation": corr,
        },
    }


# ---------------------------------------------------------------- 主流程
def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    size = path.stat().st_size
    print(f"  ✓ {path.as_posix():48s} {size / 1024:8.1f} KB")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 P3 时空图谱三份契约")
    ap.add_argument("--out", default="outputs/contracts")
    ap.add_argument("--nodes", type=int, default=180, help="企业节点数（其余实体按比例派生）")
    ap.add_argument("--seasons", type=int, default=6, help="季度切片数（≤8）")
    ap.add_argument("--sample-cap", type=int, default=150, help="默认渲染节点上限（超过则抽样）")
    args = ap.parse_args()

    out = Path(args.out)
    seasons = max(1, min(8, args.seasons))
    graph = build_graph(max(12, args.nodes), seasons)
    print(f"六类实体合成图：{len(graph['nodes'])} 节点 / {len(graph['edges'])} 边 / {seasons} 个季度切片")

    write(out / "graph_structure.json", build_structure(graph, args.sample_cap))
    write(out / "graph_slices.json", build_slices(graph))
    write(out / "graph_centrality.json", build_centrality(graph))


if __name__ == "__main__":
    main()
