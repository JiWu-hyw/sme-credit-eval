#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将三份「高新技术企业认定/备案名单」PDF 解析为 CSV 中间文件。

输入（data/raw/，不进 Git）：
  - 浙江2021高新认定第一批_公示名单.pdf
  - 浙江2021高新认定_第一批补充备案名单.pdf
  - 浙江2024高新备案_公告名单.pdf

输出（data/processed/，本地生成、可脚本复现）：
  - 2021_first_batch.csv         2021 第一批认定（9760 家）
  - 2021_supplementary.csv       2021 第一批补充备案（296 家）
  - 2021_recognized_pool.csv     2021 认定合并池（第一批+补充 = 10056 家）
  - 2024_recheck.csv             2024 认定报备备案（复核名单，12762 家）

PDF 为文本型，结构统一：表头(序号/企业名称/证书编号) + 数据行
  <序号>\n<企业名称>\n<证书编号 GR13位>

解析策略：以「证书编号 GR\\d{13}」为锚点，其紧邻上一行为企业名称，
向上回溯最近的纯整数行作为序号；序号与证书编号之间的所有行合并为企业名称
（兼容企业名称换行折行的情况）。
"""
from __future__ import annotations

import csv
import os
import re
import sys

import pymupdf  # fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")
OUT_DIR = os.path.join(ROOT, "data", "processed")

CERT_RE = re.compile(r"^GR\d{12}$")
INT_RE = re.compile(r"^\d+$")
ISSUE_DATE_RE = re.compile(r"发证日期[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

# 文件 -> 元信息
FILES = [
    {
        "file": "浙江2021高新认定第一批_公示名单.pdf",
        "year": 2021,
        "batch": "第一批认定",
        "expected": 9760,
        "out": "2021_first_batch.csv",
    },
    {
        "file": "浙江2021高新认定_第一批补充备案名单.pdf",
        "year": 2021,
        "batch": "第一批补充备案",
        "expected": 296,
        "out": "2021_supplementary.csv",
    },
    {
        "file": "浙江2024高新备案_公告名单.pdf",
        "year": 2024,
        "batch": "认定报备备案(复核)",
        "expected": 12762,
        "out": "2024_recheck.csv",
    },
]


def extract_lines(path: str):
    """返回 (lines, issue_date_str)。lines 为去空白后的非空行列表。"""
    doc = pymupdf.open(path)
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    full = "\n".join(text_parts)
    lines = [ln.strip() for ln in full.splitlines() if ln.strip()]
    m = ISSUE_DATE_RE.search(full)
    issue_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
    return lines, issue_date


def parse_records(lines: list[str]) -> list[tuple[int, str, str]]:
    """以证书编号为锚点解析记录。"""
    cert_idx = [i for i, ln in enumerate(lines) if CERT_RE.match(ln)]
    records = []
    for j in cert_idx:
        cert = lines[j]
        # 向上找最近的纯整数行作为序号
        seq = None
        for k in range(j - 1, -1, -1):
            if INT_RE.match(lines[k]):
                seq = int(lines[k])
                # 序号与证书编号之间的所有行合并为企业名称
                name = "".join(lines[k + 1 : j]).strip()
                break
        if seq is None:
            continue
        records.append((seq, name, cert))
    return records


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    pools = []  # 2021 合并池
    all_ok = True

    for meta in FILES:
        path = os.path.join(RAW_DIR, meta["file"])
        if not os.path.exists(path):
            print(f"[跳过] 文件不存在: {path}", file=sys.stderr)
            all_ok = False
            continue

        lines, issue_date = extract_lines(path)
        recs = parse_records(lines)

        # 按序号排序，保证稳定输出
        recs.sort(key=lambda r: r[0])

        out_path = os.path.join(OUT_DIR, meta["out"])
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["序号", "企业名称", "证书编号", "来源年份", "批次", "发证日期"])
            for seq, name, cert in recs:
                w.writerow([seq, name, cert, meta["year"], meta["batch"], issue_date])

        ok = len(recs) == meta["expected"]
        all_ok = all_ok and ok
        print(f"[OK? {ok}] {meta['out']}: 解析 {len(recs)} 条，期望 {meta['expected']} 条"
              f"（年份 {meta['year']} {meta['batch']}，发证日期 {issue_date}）")

        if meta["year"] == 2021:
            pools.extend((seq, name, cert, meta["batch"], issue_date) for seq, name, cert in recs)

    # 2021 认定合并池
    # 两个 2021 文件各自序号都从 1 起，合并后序号会冲突；
    # 改用全局唯一的「证书编号」排序，并重新顺排 1..N 作为合并池序号。
    pool_path = os.path.join(OUT_DIR, "2021_recognized_pool.csv")
    pools.sort(key=lambda r: r[2])  # 按证书编号（GR+12位，字典序==数值序）
    with open(pool_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "企业名称", "证书编号", "批次", "发证日期"])
        for new_seq, (seq, name, cert, batch, issue_date) in enumerate(pools, start=1):
            w.writerow([new_seq, name, cert, batch, issue_date])
    print(f"[合并] 2021_recognized_pool.csv: {len(pools)} 条（第一批+补充，按证书编号重排）")

    print("\n==== 校验 ====")
    # 证书编号唯一性 + 空企业名称检查
    empty_names = 0
    for meta in FILES:
        p = os.path.join(OUT_DIR, meta["out"])
        certs = []
        with open(p, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                certs.append(row["证书编号"])
                if not row["企业名称"].strip():
                    empty_names += 1
        dup = len(certs) - len(set(certs))
        print(f"  {meta['out']}: 唯一证书编号 {len(set(certs))}，重复 {dup}")
    # 合并池唯一性
    pool_certs = []
    with open(pool_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pool_certs.append(row["证书编号"])
    print(f"  2021_recognized_pool.csv: 唯一证书编号 {len(set(pool_certs))}，重复 {len(pool_certs)-len(set(pool_certs))}，空企业名称 {empty_names}")

    if empty_names:
        all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
