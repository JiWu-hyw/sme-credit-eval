"""给前端原型 SHAP 契约幂等注入「方向语义」声明。

问题（算法端评审 §6 T40–T44 + v1.9 方向纠正后暴露）：
前端 P5 用的 shap_global.json / shap_local*.json 明确写着
「+1=风险因子（值越大越推高违约概率）」，是 A 套信贷面板 PD 风险方向的
**原型 mock**（contract_status=mock / label_pending，label_ref=null）。
v1.9 把企业好坏口径定成「正例=好企业」，当前实测 SHAP 是
outputs/shap_credit_direction.json（P(好企业)，+1=增信因子），两者语义相反。
不标注的话，算法端很容易把 mock 里的因子方向当成信用方向结论引用。

本脚本只往每个 JSON 的 meta 里插入 3 个字符串字段，
不改任何数值 / 排序 / 结论，不重排文件格式，可重复执行。
用法：python -m src.data.inject_shap_direction_notice
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTRACT_DIR = os.path.join(
    ROOT, "src", "app", "frontend", "extracted", "outputs", "contracts")

TARGETS = (
    "shap_global.json",
    "shap_local.json",
    "shap_local_label_pending.json",
    "shap_local_sparse.json",
)

KEY = "direction_notice"
VERSION = "v1"

BLOCK = (
    '    "label_direction": "A 套信贷面板 PD 风险方向（+1=推高违约概率）；'
    '与 v1.9 企业好坏口径 P(好企业) 语义相反",\n'
    '    "current_shap_entry": "outputs/shap_credit_direction.json'
    '（P(好企业)，+1=增信因子，-1=风险因子）",\n'
    '    "direction_notice": "v1：本契约是前端原型 mock（contract_status=mock / '
    'label_pending，label_ref=null），禁止与当前口径 SHAP 混用或互引数值；'
    '详见 outputs/shap_legacy_notice.md",\n'
)


def inject(path: str) -> bool:
    """返回是否真的改写了文件。幂等。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    # 先确认注入后仍是合法 JSON，避免把契约写坏
    marker = re.compile(r'\n    "label_direction":.*?\n'
                        r'    "current_shap_entry":.*?\n'
                        r'    "direction_notice":.*?\n', re.S)
    head = '"meta": {\n'
    if head not in text:
        raise ValueError("%s 缺标准 meta 头，拒绝注入" % os.path.basename(path))

    if marker.search(text):
        new = marker.sub("\n" + BLOCK, text, count=1)
        if new == text:
            return False
    else:
        new = text.replace(head, head + BLOCK, 1)

    # 幂等短路：内容已一致
    if new == text:
        return False
    json.loads(new)  # 语法自检，失败则抛异常、不落盘
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    return True


def verify() -> list[str]:
    bad = []
    for fn in TARGETS:
        p = os.path.join(CONTRACT_DIR, fn)
        if not os.path.exists(p):
            bad.append("缺契约 %s" % fn)
            continue
        d = json.load(open(p, encoding="utf-8"))
        m = d.get("meta", {})
        if not m.get(KEY, "").startswith(VERSION):
            bad.append("%s 缺 %s=%s 声明" % (fn, KEY, VERSION))
        if "PD 风险方向" not in m.get("label_direction", ""):
            bad.append("%s 的 label_direction 语义不对" % fn)
        if "shap_credit_direction.json" not in m.get("current_shap_entry", ""):
            bad.append("%s 未指向当前口径 SHAP 产物" % fn)
    return bad


def main() -> int:
    changed = []
    for fn in TARGETS:
        p = os.path.join(CONTRACT_DIR, fn)
        if not os.path.exists(p):
            print("  MISS  %s（跳过）" % fn)
            continue
        if inject(p):
            changed.append(fn)
            print("  +     %s" % fn)
        else:
            print("  =     %s（已是最新）" % fn)
    bad = verify()
    if bad:
        for b in bad:
            print("  FAIL  " + b)
        return 1
    print("[done] %d/%d 份契约改写，%d 份验证通过；未改动任何数值" % (
        len(changed), len(TARGETS), len(TARGETS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
