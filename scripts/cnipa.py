#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/cnipa.py — CNIPA 专利采集适配器（免费源先行 · 骨架）
=========================================================================
模块一（多源异构数据采集，吕键 主责）
对齐：docs/数据管道架构设计.md 与 zjtech_credit.db schema v1.0
下游：match_patents.py（ods_patent_raw → fact_patent）
再下游：build_tech_features.py（fact_patent → dws_tech_feature）

【数据链路】
    CNIPA 免费源 ──fetch──▶ 原始专利记录
                ──parse──▶ ods_patent_raw（落地表，本脚本写入）
                ──match──▶ fact_patent（match_patents.py 负责）
                ──build──▶ dws_tech_feature（build_tech_features.py 负责）

【免费源选择】（均已在 meta_data_source 注册，cost_cny=0）
    ★ cnipa_ipdps ：国家知识产权局·知识产权数据资源公共服务系统
      https://ipdps.cnipa.gov.cn/  —— 官方、合规免费，按年度/地区批量下载
      （bulk_download）。本脚本默认 source_id。
    备选 google_patents ：Google Patents Public Datasets (BigQuery 公开数据集，
      需 GCP 账号 + 免费额度)；uspto_odp ：USPTO 开放数据门户（免费）。

【本脚本能力边界（骨架）】
    1. ✅ parse ：把 CNIPA 标准字段（中文列名亦可）规范化为 ods_patent_raw，
       已可用；
    2. ✅ --csv PATH ：本地 CSV 导入模式，无需任何外部凭证，今天就能跑通
        pipeline（免费源的"离线替代路径"）；
    3. ✅ --demo ：自测模式，写入若干示例专利（assignee 用真实企业名，使
        match_patents.py 能命中），验证 ods→fact→dws 整链不被卡死；
    4. ⏳ --online ：真实 CNIPA 在线采集骨架，fetch_online() 给出登录态 /
       反爬 / 批量下载接入点，待网络与凭证就绪后补全（见函数内 TODO）。

【幂等】ods_patent_raw 无 RUN_TAG 列，故以公开号(pub_no)去重：仅插入库内
    尚不存在的 pub_no（app_no 作兜底键）。重跑不会重复灌入已有记录，也
    不删除历史 —— 与项目"加新不覆盖"哲学一致。

【留痕】按 meta_ingest_log.stage 合法枚举('fetch'/'parse')分别留痕。

运行：
    python scripts/cnipa.py --demo                 # 自测，写示例专利
    python scripts/cnipa.py --csv path/to/data.csv # 本地 CSV 灌库
    python scripts/cnipa.py --online               # 真实在线采集（待补全）
    python scripts/cnipa.py --demo --dry-run        # 只统计，不写库
=========================================================================
"""
from __future__ import annotations

import argparse
import csv
import datetime
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "db" / "zjtech_credit.db"
SOURCE_ID = "cnipa_ipdps"            # 对应 meta_data_source 中已注册的免费源

# CNIPA 标准导出字段 / 常见别名 → ods_patent_raw 列名 的别名表
COLUMN_ALIASES = {
    "pub_no":           ["pub_no", "公开号", "公开（公告）号", "公开公告号"],
    "app_no":           ["app_no", "申请号"],
    "title":            ["title", "名称", "发明名称", "专利名称"],
    "abstract":         ["abstract", "摘要"],
    "app_date":         ["app_date", "申请日"],
    "pub_date":         ["pub_date", "公开日", "公开（公告）日"],
    "grant_date":       ["grant_date", "授权公告日", "授权日"],
    "assignee_raw":     ["assignee_raw", "申请人", "专利权人"],
    "assignee_addr_raw":["assignee_addr_raw", "地址", "申请人地址"],
    "inventor_raw":     ["inventor_raw", "发明人"],
    "ipc_raw":          ["ipc_raw", "ipc", "ipc分类", "主分类号", "ipc主分类"],
    "legal_status_raw": ["legal_status_raw", "法律状态"],
    "country_code":     ["country_code", "国别", "国家"],
}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def norm_date(v: str | None) -> str | None:
    """CNIPA 日期常为 '2021.03.15' 或 '2021-03-15'，统一为 YYYY-MM-DD。"""
    if not v:
        return None
    v = v.strip().replace("/", "-")
    if "." in v and len(v) == 10:
        v = v.replace(".", "-")
    return v if len(v) >= 7 else None


def norm_text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def build_header_map(headers: list[str]) -> dict[str, str]:
    """把 CSV 表头映射到 ods 列名；找不到的列留空（由默认值补）。"""
    hmap: dict[str, str] = {}
    norm_headers = {h.strip(): h for h in headers}
    for col, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            if a in norm_headers:
                hmap[col] = norm_headers[a]
                break
    return hmap


def normalize_record(row: dict, hmap: dict[str, str]) -> dict:
    """把一行原始记录（按 hmap 取值）规范化为 ods_patent_raw 字段。"""
    def get(col: str) -> str | None:
        src = hmap.get(col)
        return norm_text(row.get(src)) if src else None

    return {
        "pub_no":           get("pub_no"),
        "app_no":           get("app_no"),
        "title":            get("title"),
        "abstract":         get("abstract"),
        "app_date":         norm_date(get("app_date")),
        "pub_date":         norm_date(get("pub_date")),
        "grant_date":       norm_date(get("grant_date")),
        "assignee_raw":     get("assignee_raw"),
        "assignee_addr_raw":get("assignee_addr_raw"),
        "inventor_raw":     get("inventor_raw"),
        "ipc_raw":          get("ipc_raw"),
        "legal_status_raw": get("legal_status_raw"),
        "country_code":     get("country_code") or "CN",
    }


# --------------------------------------------------------------------------- #
# 写库：ods_patent_raw 去重插入
# --------------------------------------------------------------------------- #
def load_existing_keys(con: sqlite3.Connection) -> set[str]:
    """已存在的 (pub_no 或 APP:<app_no>) 去重键集合。"""
    cur = con.cursor()
    keys: set[str] = set()
    for pub, app in cur.execute("SELECT pub_no, app_no FROM ods_patent_raw"):
        if pub:
            keys.add(pub)
        elif app:
            keys.add("APP:" + app)
    return keys


def upsert_records(con: sqlite3.Connection, records: list[dict],
                   dry_run: bool = False) -> tuple[int, int]:
    """插入 ods_patent_raw，按 pub_no/app_no 去重。返回 (插入数, 跳过数)。"""
    existing = load_existing_keys(con)
    cur = con.cursor()
    inserted = skipped = 0
    for rec in records:
        key = rec["pub_no"] or ("APP:" + rec["app_no"] if rec["app_no"] else None)
        if key and key in existing:
            skipped += 1
            continue
        if dry_run:
            inserted += 1
            continue
        cur.execute(
            """INSERT INTO ods_patent_raw
                   (pub_no, app_no, title, abstract, app_date, pub_date,
                    grant_date, assignee_raw, assignee_addr_raw, inventor_raw,
                    ipc_raw, legal_status_raw, country_code, source_id,
                    file_path, row_no, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["pub_no"], rec["app_no"], rec["title"], rec["abstract"],
                rec["app_date"], rec["pub_date"], rec["grant_date"],
                rec["assignee_raw"], rec["assignee_addr_raw"], rec["inventor_raw"],
                rec["ipc_raw"], rec["legal_status_raw"], rec["country_code"],
                SOURCE_ID, "cnipa_adapter", 0, now_iso(),
            ),
        )
        if key:
            existing.add(key)
        inserted += 1
    if not dry_run:
        con.commit()
    return inserted, skipped


def log_stage(con: sqlite3.Connection, stage: str, target: str,
              rows_in: int, rows_ok: int, rows_bad: int, message: str) -> None:
    cur = con.cursor()
    started = finished = now_iso()
    cur.execute(
        """INSERT INTO meta_ingest_log
               (source_id, stage, target_table, file_path, file_sha256,
                rows_in, rows_ok, rows_bad, started_at, finished_at,
                status, message)
           VALUES (?,?,?,?,?,?,?,?,?,?,'ok',?)""",
        (SOURCE_ID, stage, target, "cnipa_adapter", None,
         rows_in, rows_ok, rows_bad, started, finished, message),
    )
    con.commit()


# --------------------------------------------------------------------------- #
# 模式 1：本地 CSV 导入
# --------------------------------------------------------------------------- #
def run_csv(con: sqlite3.Connection, csv_path: Path,
            dry_run: bool = False) -> None:
    if not csv_path.exists():
        print(f"✗ CSV 不存在：{csv_path}")
        return
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        hmap = build_header_map(reader.fieldnames or [])
        records = [normalize_record(r, hmap) for r in reader]
    print(f"► 解析 CSV：{csv_path}（{len(records)} 行）")
    print(f"  列映射：{hmap if hmap else '（无匹配列，将全用默认空值）'}")
    inserted, skipped = upsert_records(con, records, dry_run)
    if dry_run:
        print(f"  [dry-run] 拟插入 {inserted}，跳过 {skipped}")
    else:
        log_stage(con, "parse", "ods_patent_raw", len(records), inserted,
                  skipped, f"CSV 导入 {csv_path.name}：插入{inserted}/跳过{skipped}")
        print(f"  ✓ 插入 {inserted} 行，跳过（已存在）{skipped} 行")


# --------------------------------------------------------------------------- #
# 模式 2：--demo 自测（用真实企业名，验证整链）
# --------------------------------------------------------------------------- #
def run_demo(con: sqlite3.Connection, dry_run: bool = False) -> None:
    # 取前若干真实企业名作为 assignee，使 match_patents.py 能精确命中
    names = [r[0] for r in con.execute(
        "SELECT name_display FROM dim_enterprise "
        "WHERE name_display IS NOT NULL LIMIT 3")]
    demo = [
        # (pub_no, assignee, patent_type, legal_status, app_date)
        ("DEMO_CN_000001", names[0] if names else "浙江王牌门业有限公司",
         "invention", "granted", "2021-03-15"),
        ("DEMO_CN_000002", names[1] if len(names) > 1 else "温州昶宏电子科技有限公司",
         "utility", "granted", "2021-07-22"),
        ("DEMO_CN_000003", "某某虚构科技有限公司（demo 未匹配）",
         "design", "pending", "2022-01-09"),
    ]
    records = []
    for pub, assignee, ptype, status, adate in demo:
        records.append({
            "pub_no": pub, "app_no": "CN" + pub[-6:], "title": f"示例专利-{pub}",
            "abstract": "DEMO 自测数据，请勿作为真实专利使用。",
            "app_date": adate, "pub_date": adate, "grant_date": adate,
            "assignee_raw": assignee, "assignee_addr_raw": "浙江省",
            "inventor_raw": None, "ipc_raw": "G06F", "legal_status_raw": status,
            "country_code": "CN",
        })
    print(f"► --demo 自测：准备 {len(records)} 条示例专利"
          f"（含 {min(len(names),2)} 个真实企业名以验证匹配）")
    inserted, skipped = upsert_records(con, records, dry_run)
    if dry_run:
        print(f"  [dry-run] 拟插入 {inserted}，跳过 {skipped}")
    else:
        log_stage(con, "parse", "ods_patent_raw", len(records), inserted,
                  skipped,
                  f"DEMO 自测：插入{inserted}/跳过{skipped}；"
                  f"含真实企业名以验证 match_patents 链；pub_no 前缀 DEMO_ 可清理")
        print(f"  ✓ 插入 {inserted} 行，跳过 {skipped} 行")
        print("  下一步：python scripts/match_patents.py  →  回填 fact_patent")
        print("          python scripts/build_tech_features.py  →  解锁 dws_tech_feature")


# --------------------------------------------------------------------------- #
# 模式 3：真实 CNIPA 在线采集（骨架，待补全）
# --------------------------------------------------------------------------- #
def fetch_online(con: sqlite3.Connection, limit: int | None = None,
                 dry_run: bool = False) -> None:
    """
    ⏳ 真实在线采集骨架 —— 当前未实现，列出接入 TODO。

    CNIPA「知识产权数据资源公共服务系统」(ipdps.cnipa.gov.cn) 是官方合规免费源，
    但需登录态 + 反爬签名，批量下载流程如下，待网络/凭证就绪后补全：

    TODO 清单：
      1. 注册并登录账号，获取会话 cookie + CSRF token（requests.Session 维持）；
      2. 进入"数据下载"栏目，按 统计范围(地区/年度) 构造查询请求；
      3. 解析返回的批量文件（常为 zip 内含 CSV/XML），逐条 normalize_record；
      4. 调用 upsert_records() 落 ods_patent_raw，并 log_stage('fetch'/'parse')。

    备选免费源（同样 cost=0）：
      - google_patents (BigQuery)：用 bigquery 客户端拉 patent 公开数据集，
        SQL 按 assignee 过滤；需 GCP 项目 + 免费额度；
      - uspto_odp (PATFT/AppFT API)：REST 按申请人检索，免费但限频。

    实现前请先与袁雪茹/指导教师确认：是否启用在线自动采集（合规与配额）。
    """
    print("⏳ fetch_online() 为骨架，尚未实现真实采集。")
    print("   接入 TODO 见函数文档字符串（登录态 → 查询 → 批量下载 → 解析落库）。")
    print("   当前可走的免费路径：--csv（离线导入）或 --demo（自测）。")
    log_stage(con, "fetch", "ods_patent_raw(online-skeleton)", 0, 0, 0,
              "online 骨架：未实现真实采集，待登录态/反爬/批量下载接入")
    return


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="CNIPA 专利采集适配器（免费源先行）")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="zjtech_credit.db 路径")
    ap.add_argument("--csv", type=Path, help="本地专利 CSV 导入路径")
    ap.add_argument("--demo", action="store_true", help="写入示例专利自测")
    ap.add_argument("--online", action="store_true", help="真实在线采集（骨架）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = ap.parse_args()

    if not (args.csv or args.demo or args.online):
        ap.error("请指定一种模式：--csv PATH / --demo / --online")

    db_path: Path = args.db
    if not db_path.exists():
        print(f"✗ 数据库不存在：{db_path}（请先建库并跑 collect_pool.py）")
        return 2

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")

    if args.csv:
        run_csv(con, args.csv, args.dry_run)
    elif args.demo:
        run_demo(con, args.dry_run)
    elif args.online:
        fetch_online(con, None, args.dry_run)

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
