"""导出 Web 界面所需的 dashboard_data.json（demo 版，无需真实数据）。

读取已训练的 stgnn_demo.pt，在合成数据上复算 80 家企业的全量 PD / 信用分 / 真实标签，
并附带：
  - 全局特征重要性（映射到《特征工程字典》友好指标名）
  - 12 家企业的 1 跳子图（用于前端力导向关系图）
写入 web/dashboard_data.json，供 web/index.html 直接 fetch。

用法：
    .venv\\Scripts\\python.exe src/app/export_dashboard.py
"""

import json
import os
from datetime import datetime

import numpy as np
import torch

from src.graph.synthetic import generate_dataset
from src.model.stgnn import STGNN
from src.model.train_demo import evaluate, roc_auc, ks_stat
from src.app.local_explain import compute_local_explanations, REV_EDGE_TYPES

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(ROOT, "web")
CKPT = os.path.join(ROOT, "outputs", "demo", "stgnn_demo.pt")
IMP_JSON = os.path.join(ROOT, "outputs", "demo", "feature_importance.json")

# 16 维合成特征 -> 《特征工程字典》指标名映射（demo 友好展示用）
FEATURE_NAMES = [
    "发明专利占比", "专利授权率", "专利被引次数", "技术广度",
    "专利三值评分", "核心人员稳定性", "研发投入强度", "营收复合增长率",
    "员工复合增长率", "专利复合增长率", "新获融资额/上轮估值比", "里程碑虚拟变量",
    "投资机构知名度", "技术转化效率", "资本技术协同度", "综合嵌入(fused)",
]

REL_CN = {
    ("company", "applies", "patent"): "申请专利",
    ("investor", "invests", "company"): "投资",
    ("company", "rev_invests", "investor"): "被投资",
    ("company", "employs", "person"): "雇佣",
    ("company", "in_field", "field"): "所属领域",
    ("company", "makes", "product"): "生产产品",
}

NODE_CN = {
    "company": "企业", "patent": "专利", "investor": "投资机构",
    "person": "核心人员", "field": "技术领域", "product": "产品",
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ckpt = torch.load(CKPT, map_location=DEVICE, weights_only=False)
    metadata = ckpt["metadata"]
    model = STGNN(metadata, hidden=ckpt["hidden"]).to(DEVICE)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    ds, labels, _, _ = generate_dataset(
        num_companies=ckpt["num_companies"],
        num_snapshots=ckpt["num_snapshots"],
        seed=42,
    )
    snaps = [d.to(DEVICE) for d in ds]
    y = torch.tensor(labels, dtype=torch.float32).to(DEVICE)
    prob, metrics = evaluate(model, snaps, y)
    prob = prob  # np array
    y_np = labels

    companies = []
    for i in range(len(prob)):
        pd = float(prob[i])
        pred = int(pd >= 0.5)
        companies.append({
            "id": i,
            "pd": round(pd, 4),
            "score": round(float(100 - pd * 100), 2),
            "label": int(y_np[i]),
            "correct": int(pred == y_np[i]),
        })

    # 特征重要性（已算好的 JSON）
    with open(IMP_JSON, encoding="utf-8") as f:
        imp_raw = json.load(f)
    importance = [
        {"f": f"f{i}", "name": FEATURE_NAMES[i], "value": float(imp_raw[f"f{i}"])}
        for i in range(len(FEATURE_NAMES))
    ]
    importance.sort(key=lambda d: d["value"], reverse=True)

    # 12 家企业 1 跳子图
    snap = ds[0]
    sample_companies = list(range(12))
    keep_company = set(sample_companies)
    keep_other = set()
    links = []
    cat_set = set()
    for etype in snap.edge_types:
        if etype in REV_EDGE_TYPES:
            continue  # 只画正向边，避免 company↔邻居 双向冗余
        src_t, rel, dst_t = etype
        cat_set.add(src_t)
        cat_set.add(dst_t)
        ei = snap[etype].edge_index.cpu().numpy()
        rel_cn = REL_CN.get(etype, rel)
        for k in range(ei.shape[1]):
            s, d = int(ei[0, k]), int(ei[1, k])
            if src_t == "company" and s in keep_company:
                keep_other.add((dst_t, d))
                links.append({"source": f"{src_t}_{s}", "target": f"{dst_t}_{d}", "rel": rel_cn})
            elif dst_t == "company" and d in keep_company:
                keep_other.add((src_t, s))
                links.append({"source": f"{src_t}_{s}", "target": f"{dst_t}_{d}", "rel": rel_cn})

    nodes = []
    for c in sample_companies:
        risk = float(prob[c])
        nodes.append({
            "id": f"company_{c}", "name": f"企业{c}", "category": "company",
            "risk": round(risk, 3), "score": round(float(100 - risk * 100), 1),
        })
    for (t, idx) in keep_other:
        nodes.append({"id": f"{t}_{idx}", "name": f"{NODE_CN.get(t, t)}{idx}", "category": t})

    categories = [{"name": NODE_CN.get(t, t)} for t in sorted(cat_set)]

    data = {
        "meta": {
            "auc": round(float(metrics["AUC"]), 4),
            "ks": round(float(metrics["KS"]), 4),
            "accuracy": round(float(metrics["Accuracy"]), 4),
            "n_companies": len(companies),
            "default_ratio": round(float(y_np.mean()), 3),
            "device": DEVICE,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "note": "demo 合成数据，非真实企业；指标名为映射展示。",
        },
        "companies": companies,
        "importance": importance,
        "graph": {"nodes": nodes, "links": links, "categories": categories},
    }

    # 局部图解释（T41）：对每家企业计算关键关联邻居
    print("[export] 计算局部图解释（边扰动归因）...")
    data["explanations"] = compute_local_explanations(model, snaps, top_k=8)

    out_path = os.path.join(OUT_DIR, "dashboard_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[export] 已写入 {out_path}")
    print(f"[export] AUC={data['meta']['auc']} KS={data['meta']['ks']} "
          f"Acc={data['meta']['accuracy']} 企业数={data['meta']['n_companies']}")
    print(f"[export] 图谱节点={len(nodes)} 关系={len(links)}")


if __name__ == "__main__":
    main()
