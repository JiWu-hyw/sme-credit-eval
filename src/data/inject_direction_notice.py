# -*- coding: utf-8 -*-
"""在交付文档头部幂等注入「标签方向声明」（v1.9 纠偏）。

背景：旧文档把 ads_label.label_value=1（21 家资质断档）称为「正例」，
与企业好坏 / 信用评价的研究目标相反。2026-11-09 起统一口径：
    正例 = 好企业 = 资质存续（286 家, label_value=0）
    负例 = 坏企业 = 资质断档（21 家, label_value=1）
底表数值为兼容已发布契约保留，正确方向由 v_label_direction /
v_model_train_ready_credit 给出。

本脚本只改交付文档文本，不改库、不改标签。用 HTML 注释标记包裹，
重复执行不会堆叠声明；生成脚本重跑后再执行一次即可恢复。

用法：python -m src.data.inject_direction_notice
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BEGIN = "<!-- DIRECTION_NOTICE:v1 -->"
END = "<!-- /DIRECTION_NOTICE:v1 -->"

NOTICE = """**方向声明（2026-11-09 纠正，v1.9）**：本项目研究企业好坏 / 信用评价，
**正例 = 好企业 = 资质存续（286 家，`label_value=0`）**；
**负例 = 坏企业 = 资质断档（21 家，`label_value=1`）**。
`ads_label.label_value` 与 `v_model_train_ready.label` 是风险预警方向（1=断档），
为兼容已发布契约与历史 AUC 保留原数值，未原地 UPDATE；
正确方向的训练入口是 `v_model_train_ready_credit`，逐企业对照见 `v_label_direction`。
下文「断档样本」= 负例、「续存样本」= 正例；`n_positive` / `positive_rate`
等历史字段名仍指风险方向，不代表企业好坏口径的正例。
多标签测评见 `deliverables/01_算法端/multilabel_eval_report.md`。"""

# 需要注入声明的交付 / 项目文档（相对仓库根）
TARGETS = [
    # outputs 与交付目录的两份数据字典必须字节同源（test_funding_join_contract 会比对）
    "outputs/data_dictionary.md",
    "README.md",
    "data/docs/数据需求清单_扫描报告.md",
    "docs/论文/企业分档多标签体系方案-v1.0.md",
    "deliverables/00_先看这个.md",
    "deliverables/01_算法端/README.md",
    "deliverables/01_算法端/dataset_card.md",
    "deliverables/01_算法端/label_spec.md",
    "deliverables/02_前端/api_checklist.md",
    "deliverables/03_交付说明/delivery_spec.md",
    "deliverables/04_数据字典与数据集/代理标签说明书.md",
    "deliverables/04_数据字典与数据集/数据字典.md",
    "deliverables/04_数据字典与数据集/数据集/README.md",
    "deliverables/05_数据库/README.md",
]


def block() -> str:
    lines = [BEGIN]
    lines += ["> " + ln if ln else ">" for ln in NOTICE.splitlines()]
    lines.append(END)
    return "\n".join(lines) + "\n"


def inject(path: str) -> bool:
    """插入或替换声明块，返回是否改动。"""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i >= 0 and j >= 0:
        if text[i:j + len(END)].strip() == block().strip():
            return False  # 声明已是最新版，幂等早退
        new = text[:i] + block() + text[j + len(END):]
    else:
        lines = text.splitlines(keepends=True)
        at = 0
        for k, ln in enumerate(lines):
            if ln.startswith("# "):
                at = k + 1
                break
        # 跳过标题后已有的空白 / 引用行，让声明紧跟标题
        new = "".join(lines[:at]) + "\n" + block() + "\n" + "".join(lines[at:])
    if new == text:
        return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True


def main() -> int:
    changed = []
    for rel in TARGETS:
        p = os.path.join(ROOT, rel)
        if inject(p):
            changed.append(rel)
    print("方向声明已注入/更新 %d 份文档：" % len(changed))
    for rel in changed:
        print("  -", rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
