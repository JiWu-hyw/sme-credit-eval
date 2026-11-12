#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_outputs_manifest.py — 由 configs/paths.yaml 生成 outputs/manifest.json（产物索引）

归属：后端 / 数据管道。对应《后端完整答复》v2.0 第二章第 2 条「outputs/ 统一管理」的**阶段 1**：
      落唯一路径字典 + 产物索引，不迁移任何现有产物、不接管任何脚本写入点。

为什么零依赖：本机 python 未安装 PyYAML（既有的 scripts/build_enums.py --check 也因缺
      pyyaml 无法运行），故本脚本用标准库解析 configs/paths.yaml 的固定三段结构，
      不引入新依赖、不改动既有脚本。

用法（在仓库根目录执行）：
    python scripts/build_outputs_manifest.py            # 生成 outputs/manifest.json
    python scripts/build_outputs_manifest.py --check    # 只读比对：文件集合 + sha256 + status
                                                       # 非 0 退出 = 产物已变，需重新生成索引

索引每条记录：name / path / size_bytes / sha256 / mtime / status / producer /
              contract_ver / provenance_kind（契约类才有，取自 JSON 的 provenance.kind）。
--check 忽略 generated_at，避免时间戳造成假性漂移；它只比对内容指纹。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATHS = os.path.join(ROOT, "configs", "paths.yaml")
OUT = os.path.join(ROOT, "outputs", "manifest.json")


def parse_paths(path: str) -> dict:
    """极简解析：顶层段 / 2 空格键值 / 4 空格 scan 子项键值。格式由 paths.yaml 约定。"""
    data: dict = {"meta": {}, "roots": {}, "scan": {}}
    section = subsection = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            s = line.strip()
            if ":" not in s:
                continue
            key, _, val = s.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if indent == 0:
                section, subsection = key, None
                data.setdefault(section, {})
            elif section in ("meta", "roots") and indent == 2:
                data[section][key] = val
            elif section == "scan" and indent == 2 and not val:
                subsection = key
                data["scan"][subsection] = {}
            elif section == "scan" and indent >= 4 and subsection:
                data["scan"][subsection][key] = val
    if not data["roots"].get("manifest") or not data["scan"]:
        raise SystemExit("[FATAL] paths.yaml 结构不完整：缺 roots.manifest 或 scan 段")
    return data


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(cfg: dict) -> list:
    entries = []
    for _, rule in sorted(cfg["scan"].items()):
        d = os.path.join(ROOT, rule["dir"])
        if not os.path.isdir(d):
            print("[WARN] 目录不存在，跳过：%s" % rule["dir"], file=sys.stderr)
            continue
        if "pattern" in rule:
            files = sorted(glob.glob(os.path.join(d, rule["pattern"])))
        else:
            files = [os.path.join(d, n.strip()) for n in rule["names"].split(",") if n.strip()]
        for p in files:
            if not os.path.isfile(p):
                print("[WARN] 产物缺失，跳过：%s" % os.path.relpath(p, ROOT), file=sys.stderr)
                continue
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            e = {
                "name": os.path.basename(p),
                "path": rel,
                "size_bytes": os.path.getsize(p),
                "sha256": sha256(p),
                "mtime": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S"),
                "status": rule["status"],
                "producer": rule.get("producer", "-"),
                "contract_ver": rule.get("contract_ver", "-"),
            }
            if rule["status"] == "contract" and p.endswith(".json"):
                try:
                    with open(p, encoding="utf-8") as f:
                        e["provenance_kind"] = (json.load(f).get("provenance") or {}).get("kind", "-")
                except Exception:
                    e["provenance_kind"] = "unreadable"
            entries.append(e)
    return entries


def build(cfg: dict) -> dict:
    entries = collect(cfg)
    return {
        "meta": {
            "version": cfg["meta"].get("version", "v1.0"),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "configs/paths.yaml",
            "generator": "scripts/build_outputs_manifest.py",
            "stage": cfg["meta"].get("status", "stage1_index_only"),
            "caveat": cfg["meta"].get("caveat", ""),
            "note": "阶段 1：只索引，不迁移产物、不接管写入点；--check 忽略 generated_at。",
        },
        "roots": cfg["roots"],
        "summary": {
            "n_entries": len(entries),
            "n_contract": sum(1 for e in entries if e["status"] == "contract"),
            "n_report": sum(1 for e in entries if e["status"] == "report"),
        },
        "entries": entries,
    }


def check(cfg: dict) -> int:
    if not os.path.isfile(OUT):
        print("FAIL  outputs/manifest.json 不存在")
        return 1
    with open(OUT, encoding="utf-8") as f:
        old = json.load(f)
    sig = lambda es: {(e["path"], e["sha256"], e["status"]) for e in es}
    want, have = sig(collect(cfg)), sig(old.get("entries", []))
    if want == have:
        print("OK    outputs/manifest.json 与产物一致（%d 条）" % len(want))
        return 0
    for p in sorted(want ^ have):
        print("DRIFT %s" % (p[0],))
    print("FAIL  产物已变更，请重跑 python scripts/build_outputs_manifest.py")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读比对，不写文件")
    args = ap.parse_args()
    cfg = parse_paths(PATHS)
    if args.check:
        return check(cfg)
    doc = build(cfg)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("OK  outputs/manifest.json：%d 条（契约 %d / 报告 %d）"
          % (doc["summary"]["n_entries"], doc["summary"]["n_contract"],
             doc["summary"]["n_report"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
