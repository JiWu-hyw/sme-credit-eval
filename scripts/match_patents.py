#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
match_patents.py — 专利申请人 → 企业 匹配流水线
=====================================================================
模块一（多源异构数据采集，吕键 主责）
对齐：docs/数据管道架构设计.md 与 zjtech_credit.db schema v1.0

【输入】ods_patent_raw（原始专利落地表，由 CNIPA / Google Patents 采集器写入；当前 0 行）
【输出】fact_patent.enterprise_id / match_method / match_confidence + log_match

【匹配三档】
    1. exact ：assignee_raw 规范化后 == dim_enterprise.name_norm        → 置信 1.0
    2. alias ：命中 dim_enterprise_alias.alias_norm                    → 置信 = alias.confidence
    3. fuzzy ：与 dim_enterprise.name_norm 模糊比 ≥ FUZZY_THRESHOLD    → 置信 = ratio

【幂等】只处理 fact_patent.enterprise_id IS NULL（未匹配）的行；
    --force 可重匹配全部。人工已确认(match_method='manual')的不动。

【前置】仓库 schema 须已存在；且 dim_enterprise 已先由 collect_pool.py 建好
    （否则无候选企业可匹配）。

用法：
    python scripts/match_patents.py                # 匹配未匹配专利
    python scripts/match_patents.py --force        # 重匹配全部
    python scripts/match_patents.py --db PATH
"""

from __future__ import annotations

import argparse
import difflib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "db" / "zjtech_credit.db"

FUZZY_THRESHOLD = 0.90          # 模糊匹配接受阈值
ALIAS_THRESHOLD = 0.85          # 别名模糊兜底阈值（若 alias 名称本身需再容错）
ACCEPT_DECISION = 0.90          # ≥ 此置信 → log_match 直接 accepted，否则 pending

# 复用以保证与企业主表同一套规范化口径
try:
    from collect_pool import normalize_name  # type: ignore
except Exception:  # 独立运行兜底
    import re as _re, unicodedata as _ud
    _PAREN = _re.compile(r"[（(].*?[）)]")
    _WS = _re.compile(r"\s+")
    def normalize_name(name: str) -> str:
        if not name:
            return ""
        s = _ud.normalize("NFKC", name)
        s = _PAREN.sub("", s)
        s = _WS.sub("", s)
        return s.strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# 候选企业索引（加速 fuzzy）
# --------------------------------------------------------------------------- #
def build_enterprise_index(con: sqlite3.Connection):
    cur = con.cursor()
    exact: dict[str, str] = {}                 # name_norm -> enterprise_id
    by_first: dict[str, list[tuple[str, str]]] = {}  # 首字 -> [(ent_id, name_norm)]
    for ent_id, name_norm in cur.execute(
        "SELECT enterprise_id, name_norm FROM dim_enterprise"
    ):
        if not name_norm:
            continue
        exact[name_norm] = ent_id
        key = name_norm[0]
        by_first.setdefault(key, []).append((ent_id, name_norm))
    # 别名表
    aliases: dict[str, tuple[str, float]] = {}
    try:
        for ent_id, alias_norm, conf in cur.execute(
            "SELECT enterprise_id, alias_norm, confidence FROM dim_enterprise_alias"
        ):
            aliases[alias_norm] = (ent_id, float(conf))
    except sqlite3.Error:
        pass
    return exact, by_first, aliases


def match_one(assignee_raw: str, exact, by_first, aliases):
    """返回 (enterprise_id, method, confidence) 或 (None, None, 0.0)。"""
    if not assignee_raw:
        return (None, None, 0.0)

    # 多申请人：依次尝试各片段
    parts = [p for p in normalize_name(assignee_raw)
             .replace("；", ";").replace("、", ";").replace(",", ";").split(";") if p]
    if not parts:
        return (None, None, 0.0)

    best = (None, None, 0.0)
    for norm in parts:
        # 1) exact
        if norm in exact:
            return (exact[norm], "exact", 1.0)
        # 2) alias
        if norm in aliases:
            ent_id, conf = aliases[norm]
            if conf > best[2]:
                best = (ent_id, "alias", conf)
            continue
        # 3) fuzzy（同首字候选）
        cand = by_first.get(norm[0], [])
        for ent_id, name_norm in cand:
            ratio = difflib.SequenceMatcher(None, norm, name_norm).ratio()
            if ratio >= FUZZY_THRESHOLD and ratio > best[2]:
                best = (ent_id, "fuzzy", round(ratio, 4))
    return best


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="专利申请人→企业匹配流水线")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="zjtech_credit.db 路径")
    ap.add_argument("--force", action="store_true",
                    help="重匹配全部 fact_patent（默认只匹配 enterprise_id 为空的）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = ap.parse_args()

    db_path: Path = args.db
    if not db_path.exists():
        print(f"✗ 数据库不存在：{db_path}（请先建库并跑 collect_pool.py）")
        return 2

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")

    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM ods_patent_raw")
    n_raw = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dim_enterprise")
    n_ent = cur.fetchone()[0]

    print(f"► 数据库 : {db_path}")
    print(f"  ods_patent_raw 行数 : {n_raw}")
    print(f"  dim_enterprise 行数: {n_ent}")

    if n_raw == 0:
        print("\n○ 暂无专利原始数据（ods_patent_raw = 0 行）。")
        print("  管线已就绪：待 CNIPA / Google Patents 采集器写入 ods_patent_raw 后，")
        print("  重跑本脚本即可自动匹配企业并回填 fact_patent。")
        con.close()
        return 0

    if n_ent == 0:
        print("\n✗ dim_enterprise 为空：请先运行 collect_pool.py 建立企业主表，"
              "否则无候选可匹配。")
        con.close()
        return 2

    exact, by_first, aliases = build_enterprise_index(con)

    where = "" if args.force else " WHERE enterprise_id IS NULL"
    cur.execute(f"SELECT patent_id, assignee_raw, raw_id FROM ods_patent_raw{where}")
    rows = cur.fetchall()

    matched = pending = unmatched = 0
    method_counts: dict[str, int] = {}

    if not args.dry_run:
        log_cur = con.cursor()

    for patent_id, assignee_raw, raw_id in rows:
        ent_id, method, conf = match_one(assignee_raw, exact, by_first, aliases)
        if ent_id is None:
            unmatched += 1
            if not args.dry_run:
                log_cur.execute(
                    """INSERT INTO log_match
                           (stage, patent_id, raw_id, raw_name, candidate_ent,
                            method, score, decision, created_at)
                       VALUES ('patent_assignee',?,?,?,NULL,'none',0.0,'pending',?)""",
                    (patent_id, raw_id, assignee_raw, now_iso()),
                )
            continue

        method_counts[method] = method_counts.get(method, 0) + 1
        decision = "accepted" if conf >= ACCEPT_DECISION else "pending"
        if decision == "accepted":
            matched += 1
        else:
            pending += 1

        if not args.dry_run:
            log_cur.execute(
                """UPDATE fact_patent
                      SET enterprise_id=?, match_method=?, match_confidence=?
                    WHERE patent_id=?""",
                (ent_id, method, conf, patent_id),
            )
            # 确保 fact_patent 行存在（从 raw 提升）
            log_cur.execute(
                """INSERT OR IGNORE INTO fact_patent
                       (patent_id, assignee_raw, match_method, match_confidence,
                        country_code, source_id, raw_id)
                   VALUES (?,?,?,?,'CN',
                       (SELECT source_id FROM ods_patent_raw WHERE raw_id=?),?)""",
                (patent_id, assignee_raw, method, conf, raw_id, raw_id),
            )
            log_cur.execute(
                """INSERT INTO log_match
                       (stage, patent_id, raw_id, raw_name, candidate_ent,
                        method, score, decision, created_at)
                   VALUES ('patent_assignee',?,?,?,?,?,?,?,?)""",
                (patent_id, raw_id, assignee_raw, ent_id, method, conf,
                 decision, now_iso()),
            )

    if not args.dry_run:
        con.commit()

    print("\n==== 匹配结果 ====")
    print(f"  待匹配专利数 : {len(rows)}")
    for m, c in sorted(method_counts.items()):
        print(f"    · {m:8s}: {c}")
    print(f"  直接接受(≥{ACCEPT_DECISION}): {matched}")
    print(f"  待人工复核(<{ACCEPT_DECISION}): {pending}")
    print(f"  未匹配          : {unmatched}")
    if args.dry_run:
        print("\n  [dry-run] 未写入数据库。")
    else:
        print("\n  匹配完成 ✓（明细见 log_match 表）")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
