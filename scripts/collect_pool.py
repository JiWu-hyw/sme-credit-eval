#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect_pool.py — 高新技术企业认定 / 复核名单 入库流水线
=====================================================================
模块一（多源异构数据采集，吕键 主责）
对齐：docs/数据管道架构设计.md 与 zjtech_credit.db schema v1.0（六层 28 表 + 5 视图）

【输入】data/processed/ 下三份 CSV（由 scripts/pdf_to_csv.py 从官方 PDF 解析产出）
    - 2021_first_batch.csv    : 2021 第一批认定
    - 2021_supplementary.csv  : 2021 第一批补充备案
    - 2024_recheck.csv        : 2024 认定报备 / 复核

【输出】data/db/zjtech_credit.db（既有 warehouse schema v1.0，本脚本只灌数据不建表）
    入库链路：ods_qual_notice → ods_qual_company_raw → dim_enterprise → fact_qualification
    派生报告：2021 认定 ∩ 2024 复核通过（企业级子集，控制台 + meta_ingest_log）

【核心约定】（与 schema 注释一致，严禁私自更改口径）
    - source_id   = 'zj_high_tech'    （meta_data_source 已登记，owner=吕键）
    - qual_type   = 'high_tech'       （dim_qual_type 已登记，rank=2，valid_years=3）
    - region_code = '330000'          （浙江省），region_name='浙江省'
    - enterprise_id = 'ENT' + md5(name_norm)[:12]（大写，确定性生成）
    - name_norm   : NFKC 全半角统一 + 去除括号备注（全/半角）+ 折叠空白
    - batch_no    : 2021 第一批='2021-1' / 2021 补充='2021-supp' / 2024='2024'
    - valid_until = year + valid_years(3)   （高新资质有效期 3 年）
    - 2021 状态='approved'(is_renewal=0) ；2024 状态='approved'(is_renewal=1, 复核/重新认定)

【幂等】重跑安全：每个 notice 先按 (qual_type,batch_no,year) 清理其 fact_qualification 与
    ods_qual_company_raw，再重插；dim_enterprise 用 upsert（保留最早 name_display，
    刷新 first/last_qual_year、max_qual_rank）。

【前置】仓库 schema 须已存在（建库步骤 v1.0）。若缺表则报错退出，不擅自建表。

用法：
    python scripts/collect_pool.py                 # 默认灌库
    python scripts/collect_pool.py --dry-run       # 只校验 CSV，不写库
    python scripts/collect_pool.py --db PATH --csv-dir DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "db" / "zjtech_credit.db"
DEFAULT_CSV_DIR = REPO_ROOT / "data" / "processed"

SOURCE_ID = "zj_high_tech"
QUAL_TYPE = "high_tech"
REGION_CODE = "330000"
REGION_NAME = "浙江省"

# 三份公告定义（batch_no / year / status / is_renewal 决定口径）
NOTICES = [
    {
        "file": "2021_first_batch.csv",
        "batch_no": "2021-1",
        "year": 2021,
        "status": "approved",
        "is_renewal": 0,
        "batch_label": "第一批认定",
    },
    {
        "file": "2021_supplementary.csv",
        "batch_no": "2021-supp",
        "year": 2021,
        "status": "approved",
        "is_renewal": 0,
        "batch_label": "第一批补充备案",
    },
    {
        "file": "2024_recheck.csv",
        "batch_no": "2024",
        "year": 2024,
        "status": "approved",
        "is_renewal": 1,
        "batch_label": "认定报备备案(复核)",
    },
]

REQUIRED_TABLES = [
    "ods_qual_notice",
    "ods_qual_company_raw",
    "dim_enterprise",
    "fact_qualification",
    "dim_qual_type",
    "meta_data_source",
    "meta_ingest_log",
]


# --------------------------------------------------------------------------- #
# 规范化与 ID 生成（与 schema 注释严格一致）
# --------------------------------------------------------------------------- #
_PAREN_RE = re.compile(r"[（(].*?[）)]")      # 全角/半角括号备注
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """name_norm：NFKC 全半角统一 → 去括号备注 → 折叠空白。"""
    if not name:
        return ""
    s = unicodedata_normalize(name)
    s = _PAREN_RE.sub("", s)                   # 去掉 "（有限合伙）" 等括号备注
    s = _WS_RE.sub("", s)                      # 折叠所有空白（含全角空格/不间断空格）
    return s.strip()


def unicodedata_normalize(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", s)


def make_enterprise_id(name_norm: str) -> str:
    """enterprise_id = 'ENT' + md5(name_norm)[:12]（大写）。"""
    digest = hashlib.md5(name_norm.encode("utf-8")).hexdigest().upper()
    return "ENT" + digest[:12]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def require_schema(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (%s)"
        % ",".join("?" * len(REQUIRED_TABLES)),
        REQUIRED_TABLES,
    )
    missing = set(REQUIRED_TABLES) - {r[0] for r in cur.fetchall()}
    if missing:
        raise SystemExit(
            "✗ 仓库 schema 缺失表: %s\n"
            "  本脚本只灌数据、不建表。请先用 schema v1.0 初始化 zjtech_credit.db\n"
            "  （从 tdrive 数据/db/ 拉取已建好的骨架，或运行建库脚本）。"
            % ", ".join(sorted(missing))
        )


# --------------------------------------------------------------------------- #
# 入库
# --------------------------------------------------------------------------- #
def upsert_enterprise(cur: sqlite3.Cursor, ent_id: str, name_display: str,
                      name_norm: str, qual_year: int) -> None:
    cur.execute(
        """
        INSERT INTO dim_enterprise
            (enterprise_id, name_display, name_norm, credit_code, region_code,
             region_name, enterprise_type, first_qual_year, last_qual_year,
             max_qual_rank, created_at, updated_at)
        VALUES (?,?,?,NULL,?,?, 'high_tech', ?,?, 2, ?,?)
        ON CONFLICT(enterprise_id) DO UPDATE SET
            name_display  = COALESCE(excluded.name_display, dim_enterprise.name_display),
            name_norm     = excluded.name_norm,
            region_code   = COALESCE(dim_enterprise.region_code, excluded.region_code),
            region_name   = COALESCE(dim_enterprise.region_name, excluded.region_name),
            enterprise_type = CASE
                WHEN dim_enterprise.enterprise_type IN ('unknown', 'tech_sme')
                     THEN 'high_tech'
                WHEN dim_enterprise.enterprise_type = 'high_tech'
                     THEN 'high_tech'
                ELSE dim_enterprise.enterprise_type END,
            first_qual_year = MIN(excluded.first_qual_year, dim_enterprise.first_qual_year),
            last_qual_year  = MAX(excluded.last_qual_year,  dim_enterprise.last_qual_year),
            max_qual_rank   = MAX(2, dim_enterprise.max_qual_rank),
            updated_at     = excluded.updated_at
        """,
        (ent_id, name_display, name_norm, REGION_CODE, REGION_NAME,
         qual_year, qual_year, now_iso(), now_iso()),
    )


def ingest_notice(con: sqlite3.Connection, csv_dir: Path, notice: dict,
                  valid_years: int, issuer: str) -> dict:
    """灌入单个公告：ods_qual_notice → ods_qual_company_raw + dim_enterprise + fact_qualification。
    返回该公告的明细计数，用于 meta_ingest_log。"""
    csv_path = csv_dir / notice["file"]
    if not csv_path.exists():
        raise SystemExit(f"✗ 找不到 CSV：{csv_path}")

    cur = con.cursor()
    started = now_iso()
    rows_in = rows_ok = rows_bad = 0
    seen_ent = set()  # 同公告内按 enterprise_id 去重

    # 1) 公告主记录（幂等：按业务键定位 notice_id）
    cur.execute(
        """
        INSERT INTO ods_qual_notice
            (qual_type, batch_no, publish_date, year, region_code, region_name,
             issuer, source_url, source_id, file_path, raw_payload, fetched_at)
        VALUES (?,?,NULL,?,?,?, ?,NULL,?,?,NULL,?)
        ON CONFLICT DO NOTHING
        """,
        (QUAL_TYPE, notice["batch_no"], notice["year"], REGION_CODE, REGION_NAME,
         issuer, SOURCE_ID, str(csv_path), now_iso()),
    )
    cur.execute(
        """SELECT notice_id FROM ods_qual_notice
           WHERE qual_type=? AND batch_no=? AND year=? AND region_code=? AND source_id=?""",
        (QUAL_TYPE, notice["batch_no"], notice["year"], REGION_CODE, SOURCE_ID),
    )
    notice_id = cur.fetchone()[0]

    # 幂等清理：删除该公告下的旧明细，重插
    cur.execute("DELETE FROM fact_qualification WHERE notice_id=?", (notice_id,))
    cur.execute("DELETE FROM ods_qual_company_raw WHERE notice_id=?", (notice_id,))

    # 2) 逐行解析
    import csv
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_in += 1
            name = (row.get("企业名称") or "").strip()
            cert = (row.get("证书编号") or "").strip()
            seq = (row.get("序号") or "").strip()
            issue_date = (row.get("发证日期") or "").strip()
            batch_label = (row.get("批次") or notice["batch_label"]).strip()
            if not name:
                rows_bad += 1
                continue
            name_norm = normalize_name(name)
            if not name_norm:
                rows_bad += 1
                continue
            ent_id = make_enterprise_id(name_norm)
            if ent_id in seen_ent:        # 同公告内重复企业名，跳过并记坏行
                rows_bad += 1
                continue
            seen_ent.add(ent_id)

            raw_payload = json.dumps(
                {"证书编号": cert, "来源年份": notice["year"],
                 "批次": batch_label, "发证日期": issue_date},
                ensure_ascii=False,
            )
            cur.execute(
                """INSERT INTO ods_qual_company_raw
                       (notice_id, seq_no, company_name, region_raw, raw_payload, ingested_at)
                   VALUES (?,?,?,?,?,?)""",
                (notice_id, int(seq) if seq.isdigit() else None, name,
                 REGION_NAME, raw_payload, now_iso()),
            )
            raw_id = cur.lastrowid

            upsert_enterprise(cur, ent_id, name, name_norm, notice["year"])

            valid_until = f"{notice['year'] + valid_years}-12-31"
            cur.execute(
                """INSERT INTO fact_qualification
                       (enterprise_id, qual_type, batch_no, year, publish_date,
                        region_code, status, is_renewal, valid_until, notice_id,
                        raw_id, match_score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,1.0)""",
                (ent_id, QUAL_TYPE, notice["batch_no"], notice["year"], issue_date,
                 REGION_CODE, notice["status"], notice["is_renewal"],
                 valid_until, notice_id, raw_id),
            )
            rows_ok += 1

    finished = now_iso()
    # meta_ingest_log：本公告 parse 阶段
    cur.execute(
        """INSERT INTO meta_ingest_log
               (source_id, stage, target_table, file_path, file_sha256,
                rows_in, rows_ok, rows_bad, started_at, finished_at, status, message)
           VALUES (?, 'parse', 'ods_qual_company_raw+fact_qualification', ?, ?, ?,?,?,?,?, 'ok', ?)""",
        (SOURCE_ID, str(csv_path), sha256_of(csv_path), rows_in, rows_ok, rows_bad,
         started, finished,
         f"batch_no={notice['batch_no']} year={notice['year']} "
         f"status={notice['status']} is_renewal={notice['is_renewal']}"),
    )
    con.commit()
    return {"notice_id": notice_id, "rows_in": rows_in, "rows_ok": rows_ok,
            "rows_bad": rows_bad}


def report_intersection(con: sqlite3.Connection) -> dict:
    """派生：2021 认定 ∩ 2024 复核通过（按 enterprise_id）。"""
    cur = con.cursor()
    cur.execute(
        """SELECT COUNT(DISTINCT enterprise_id) FROM fact_qualification
           WHERE qual_type=? AND year=2021""", (QUAL_TYPE,))
    n2021 = cur.fetchone()[0]
    cur.execute(
        """SELECT COUNT(DISTINCT enterprise_id) FROM fact_qualification
           WHERE qual_type=? AND year=2024""", (QUAL_TYPE,))
    n2024 = cur.fetchone()[0]
    cur.execute(
        """SELECT COUNT(DISTINCT enterprise_id) FROM fact_qualification
           WHERE qual_type=? AND year=2021
             AND enterprise_id IN (
                 SELECT enterprise_id FROM fact_qualification
                 WHERE qual_type=? AND year=2024)""", (QUAL_TYPE, QUAL_TYPE))
    n_both = cur.fetchone()[0]
    return {
        "recognized_2021": n2021,
        "rechecked_2024": n2024,
        "intersection_2021_and_2024": n_both,
        "only_2021": n2021 - n_both,
        "only_2024": n2024 - n_both,
        "union": n2021 + n2024 - n_both,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="高新技术企业名单入库流水线")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="zjtech_credit.db 路径")
    ap.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR, help="CSV 目录")
    ap.add_argument("--dry-run", action="store_true", help="只校验 CSV，不写库")
    args = ap.parse_args()

    db_path: Path = args.db
    csv_dir: Path = args.csv_dir

    print(f"► 数据库 : {db_path}")
    print(f"► CSV 目录: {csv_dir}")
    if args.dry_run:
        print("► 模式   : --dry-run（仅校验，不写库）\n")
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        print("")

    # 读取引用维表（valid_years / issuer）
    if not args.dry_run:
        ref_con = sqlite3.connect(db_path)
        require_schema(ref_con)
        cur = ref_con.cursor()
        cur.execute("SELECT valid_years, issuer FROM dim_qual_type WHERE qual_type=?",
                    (QUAL_TYPE,))
        row = cur.fetchone()
        if not row:
            ref_con.close()
            raise SystemExit(f"✗ dim_qual_type 缺少 '{QUAL_TYPE}'")
        valid_years, issuer = row[0], row[1]
        ref_con.close()
    else:
        valid_years, issuer = 3, "全国高新技术企业认定管理工作领导小组办公室"

    # 预校验 CSV 存在 + 表头
    import csv as _csv
    for notice in NOTICES:
        p = csv_dir / notice["file"]
        if not p.exists():
            print(f"  ✗ 缺失 CSV: {p}")
            return 2
        with p.open(encoding="utf-8-sig", newline="") as f:
            hdr = next(_csv.reader(f))
        need = {"序号", "企业名称", "证书编号", "来源年份", "批次", "发证日期"}
        miss = need - set(hdr)
        if miss:
            print(f"  ✗ {notice['file']} 表头缺列: {miss}")
            return 2
        print(f"  ✓ {notice['file']} 表头 OK")
    print("")

    if args.dry_run:
        print("dry-run 完成：CSV 校验通过，未写入数据库。")
        return 0

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    require_schema(con)

    summary = {"notices": []}
    for notice in NOTICES:
        print(f"► 入库公告 {notice['batch_no']} ({notice['year']}) "
              f"[{notice['batch_label']}] ...")
        r = ingest_notice(con, csv_dir, notice, valid_years, issuer)
        summary["notices"].append({"batch_no": notice["batch_no"], **r})
        print(f"     rows_in={r['rows_in']} rows_ok={r['rows_ok']} "
              f"rows_bad={r['rows_bad']} notice_id={r['notice_id']}")

    print("\n► 派生：2021 认定 ∩ 2024 复核通过")
    inter = report_intersection(con)
    for k, v in inter.items():
        print(f"     {k:32s}: {v}")

    # meta_ingest_log：build 阶段汇总
    cur = con.cursor()
    cur.execute(
        """INSERT INTO meta_ingest_log
               (source_id, stage, target_table, file_path, file_sha256,
                rows_in, rows_ok, rows_bad, started_at, finished_at, status, message)
           VALUES (?, 'build', 'dim_enterprise+fact_qualification', ?, NULL, ?,?,?,?,?, 'ok', ?)""",
        (SOURCE_ID, str(db_path),
         sum(n["rows_in"] for n in summary["notices"]),
         sum(n["rows_ok"] for n in summary["notices"]),
         sum(n["rows_bad"] for n in summary["notices"]),
         now_iso(), now_iso(),
         json.dumps(inter, ensure_ascii=False)),
    )
    con.commit()

    # 完整性抽查
    cur.execute("SELECT COUNT(*) FROM dim_enterprise")
    n_ent = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM fact_qualification")
    n_fact = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM ods_qual_company_raw")
    n_raw = cur.fetchone()[0]
    con.close()

    print("\n==== 落库统计 ====")
    print(f"  dim_enterprise        : {n_ent}")
    print(f"  fact_qualification    : {n_fact}")
    print(f"  ods_qual_company_raw  : {n_raw}")
    print("  入库完成 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
