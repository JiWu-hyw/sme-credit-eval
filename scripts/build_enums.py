#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_enums.py — 由 configs/enums.yaml 生成 configs/enums.json（实体枚举唯一权威源的下游）

归属：后端 / 数据管道（吕键）。输入是本域契约 configs/enums.yaml，故生成器由后端持有。
契约定位：enums.yaml 是实体类型「type（英文）/ type_cn（中文）/ 图例顺序 / 主题变量」的唯一权威源；
          本脚本是它唯一的下游生成器，产物 enums.json「禁止手工编辑」。
          type→颜色 与 configs/viz_theme.yaml §entity_category 同源，本脚本跨文件校验二者一致。

产物（默认路径，相对仓库根）：
    configs/enums.json   —— 机器可读，前端直接 import（entity.ts 消费）

用法：
    python scripts/build_enums.py                 # 生成
    python scripts/build_enums.py --check         # 校验产物是否与 yaml 同步（CI 用，非 0 退出＝已漂移）
    python scripts/build_enums.py --out X
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

try:
    import yaml
except ImportError:
    sys.stderr.write("[FATAL] 需要 pyyaml：pip install pyyaml\n")
    sys.exit(2)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_YAML = os.path.join(REPO_ROOT, "configs", "enums.yaml")
DEFAULT_OUT = os.path.join(REPO_ROOT, "configs", "enums.json")
VIZ_THEME = os.path.join(REPO_ROOT, "configs", "viz_theme.yaml")


def load_and_validate(path: str):
    with open(path, "rb") as f:
        raw = f.read()
    data = yaml.safe_load(raw.decode("utf-8"))

    errs = []
    if not isinstance(data, dict):
        errs.append("顶层必须是 mapping")
    for key in ("meta", "entity_types"):
        if key not in data:
            errs.append(f"缺少必需段: {key}")
    if errs:
        raise ValueError("configs/enums.yaml 结构错误: " + "; ".join(errs))

    items = data.get("entity_types")
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("entity_types 必须是非空 list")

    seen_type, seen_order = set(), set()
    for it in items:
        for f in ("type", "cn", "order", "theme_var"):
            if f not in it:
                raise ValueError(f"entity_type 缺字段 {f}: {it}")
        t = it["type"]
        if t in seen_type:
            raise ValueError(f"entity_type type 重复: {t}")
        if it["order"] in seen_order:
            raise ValueError(f"entity_type order 重复: {it['order']}")
        # theme_var 必须与 viz_theme.yaml 的命名规则严格一致（--entity-<type>）
        expect = f"--entity-{t}"
        if it["theme_var"] != expect:
            raise ValueError(f"entity_type {t} 的 theme_var 应为 {expect}，实际 {it['theme_var']!r}")
        seen_type.add(t)
        seen_order.add(it["order"])

    # order 从 1 起连续
    if seen_order != set(range(1, len(items) + 1)):
        raise ValueError(f"entity_type order 必须从 1 起连续，实际 {sorted(seen_order)}")

    # 跨文件一致性：viz_theme.yaml §entity_category 键集合 必须 == entity_types 的 type 集合
    if os.path.exists(VIZ_THEME):
        with open(VIZ_THEME, "rb") as f:
            vt = yaml.safe_load(f.read().decode("utf-8"))
        vt_keys = set((vt.get("entity_category") or {}).keys())
        enums_keys = {it["type"] for it in items}
        if vt_keys != enums_keys:
            only_vt = sorted(vt_keys - enums_keys)
            only_enums = sorted(enums_keys - vt_keys)
            raise ValueError(
                "enums.yaml.entity_types 与 viz_theme.yaml.entity_category 键集合不一致："
                f"仅 viz_theme 有 {only_vt}，仅 enums 有 {only_enums}"
            )

    return data, raw


def build_json(data: dict, source_rel: str, sha: str) -> str:
    version = data["meta"].get("version", "v1.0")
    payload = {
        "version": version,
        "meta": {
            "source": source_rel,
            "source_sha256": sha,
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
        "entity_types": [
            {
                "type": it["type"],
                "cn": it["cn"],
                "order": int(it["order"]),
                "theme_var": it["theme_var"],
            }
            for it in data["entity_types"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_or_check(path: str, content: str, check: bool) -> bool:
    if check:
        if not os.path.exists(path):
            print(f"[MISSING] {path}")
            return False
        with open(path, "r", encoding="utf-8") as f:
            cur = f.read()
        # 忽略 generated_at 漂移：check 只比对结构字段，避免时间戳导致的假阳性
        try:
            cur_obj = json.loads(cur)
            new_obj = json.loads(content)
            cur_obj.get("meta", {}).pop("generated_at", None)
            new_obj.get("meta", {}).pop("generated_at", None)
            ok = cur_obj == new_obj
        except Exception:
            ok = False
        print(("[OK]      " if ok else "[DRIFT]   ") + path)
        return ok
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"[WROTE]   {path}  ({len(content.encode('utf-8'))} bytes)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="由 enums.yaml 生成 enums.json")
    ap.add_argument("--yaml", default=DEFAULT_YAML, help="输入 yaml（默认 configs/enums.yaml）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出 enums.json")
    ap.add_argument("--check", action="store_true", help="仅校验产物是否与 yaml 同步（不写盘）")
    args = ap.parse_args()

    ypath = os.path.abspath(args.yaml)
    if not os.path.exists(ypath):
        sys.stderr.write(f"[FATAL] 找不到 {ypath}\n")
        return 2

    try:
        data, raw = load_and_validate(ypath)
    except Exception as e:
        sys.stderr.write(f"[FATAL] {e}\n")
        return 2

    sha = hashlib.sha256(raw).hexdigest()
    source_rel = os.path.relpath(ypath, REPO_ROOT).replace("\\", "/")

    content = build_json(data, source_rel, sha)
    ok = write_or_check(os.path.abspath(args.out), content, args.check)

    if args.check:
        if ok:
            print("[PASS] enums.json 与 enums.yaml 同步")
            return 0
        print("[FAIL] enums.json 已漂移，请运行：python scripts/build_enums.py")
        return 1

    print(f"[DONE] 来源 {source_rel} {data['meta'].get('version')} sha256 {sha[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
