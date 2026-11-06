"""
load_aux_tables.py — 补齐 3 张本地可补的规划表（幂等，先清后插）。

  1. dim_enterprise_alias  企业别名表
     - 来源A：QCC 主表 `QCC_曾用名`（清洗 NAN 假值，多曾用名按分隔符拆分）
       → source='qcc_former_name', confidence=1.0
     - 来源B：fact_patent.assignee_raw 与企业正式名不一致的变体名（多为分公司名）
       → source='patent_assignee', confidence 按 match_method 分级（exact 0.9 / fuzzy 0.75）
     - alias_norm 统一走 db.norm_company_name（与主流程同口径）
  2. ods_patent_raw  专利原始落地层
     - 3 个专利 CSV 原样落地（并集/企查查/富源），带 file_path+row_no 血缘
     - 随后回填 fact_patent.source_id / raw_id（按 pub_no+app_no 匹配，只填空）
  3. log_match  匹配审计日志
     - stage='patent_assignee'：fact_patent 按 (企业, assignee_raw, method) 聚合，decision='accepted'
     - stage='qual_company'：ods_qual_company_raw 按公司名归一后能命中 dim_enterprise 的记 accepted
     - 未匹配的 raw 公司仅统计打印，不逐行入日志（避免 10 万行噪声）

用法：python -m src.data.load_aux_tables [--dry-run]
"""
import argparse
import csv
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent.parent) not in sys.path:          # _backend
    sys.path.insert(0, str(HERE.parent.parent))
from src.data.db import norm_company_name            # noqa: E402

BACKEND = HERE.parent.parent
ROOT = BACKEND.parent
DB = BACKEND / "data/db/zjtech_credit.db"
SRC_MAIN = ROOT / "_data_collect/out/合并_500家_企业主表.csv"
SRC_UNION = ROOT / "_data_collect/out/合并_专利明细_并集.csv"
SRC_QCC = ROOT / "_data_collect/out/企查查_专利.csv"
SRC_RICH = ROOT / "_data_collect/out/500家_专利明细.csv"

FAKE = {"", "NAN", "NONE", "NULL", "-", "/"}
SPLIT_RE = re.compile(r"[;；、,，]")


def _clean(v):
    s = (v or "").strip()
    return None if s.upper() in FAKE else s


# ---------------------------------------------------------------- 1) 别名表
def load_aliases(read_cur, write_cur, dry):
    n_former = n_variant = 0
    # A) QCC 曾用名
    ent_by_display = {}
    for eid, name in read_cur.execute("SELECT enterprise_id, name_display FROM dim_enterprise"):
        ent_by_display[norm_company_name(name)] = eid
    with open(SRC_MAIN, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = _clean(row.get("企业名称"))
            former_raw = _clean(row.get("QCC_曾用名"))
            if not name or not former_raw:
                continue
            eid = ent_by_display.get(norm_company_name(name))
            if not eid:
                continue
            for alias in SPLIT_RE.split(former_raw):
                alias = alias.strip()
                if len(alias) < 4:
                    continue
                n_former += 1
                if not dry:
                    write_cur.execute(
                        "INSERT OR IGNORE INTO dim_enterprise_alias"
                        "(enterprise_id, alias_name, alias_norm, source, confidence, evidence, created_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (eid, alias, norm_company_name(alias), "qcc_former_name",
                         1.0, "企查查曾用名字段", datetime.now().isoformat(timespec="seconds")),
                    )
    # B) 专利申请人变体名
    rows = read_cur.execute(
        "SELECT enterprise_id, assignee_raw, match_method, COUNT(*), AVG(match_confidence)"
        " FROM fact_patent WHERE assignee_raw IS NOT NULL"
        " GROUP BY enterprise_id, assignee_raw, match_method"
    ).fetchall()
    for eid, raw_name, method, cnt, avg_conf in rows:
        ent_norm = None
        r = read_cur.execute(
            "SELECT name_norm FROM dim_enterprise WHERE enterprise_id=?", (eid,)
        ).fetchone()
        if r:
            ent_norm = r[0]
        alias_norm = norm_company_name(raw_name)
        if ent_norm and alias_norm == ent_norm:
            continue                                   # 归一后相同，不算别名
        conf = 0.9 if method == "exact" else (0.75 if method == "fuzzy" else 0.6)
        n_variant += 1
        if not dry:
            write_cur.execute(
                "INSERT OR IGNORE INTO dim_enterprise_alias"
                "(enterprise_id, alias_name, alias_norm, source, confidence, evidence, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (eid, raw_name.strip(), alias_norm, "patent_assignee", conf,
                 f"专利申请人名与正式名不一致({method}匹配, {cnt} 条专利)", 
                 datetime.now().isoformat(timespec="seconds")),
            )
    return n_former, n_variant


# ------------------------------------------------------- 2) ods_patent_raw
def _read_csv_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):   # 表头为第1行
            yield i, row


def load_ods_patent_raw(write_cur, dry):
    specs = [
        (SRC_UNION, {
            "pub_no": "公开号", "app_no": "申请号", "title": "专利名称",
            "app_date": "申请日", "ipc_raw": "IPC分类号", "legal_status_raw": "法律状态",
            "inventor_raw": "发明人", "assignee_raw": "申请人"}),
        (SRC_QCC, {
            "pub_no": "公开(公告)号", "app_no": "申请号", "title": "发明名称",
            "app_date": "申请日", "pub_date": "公开(公告)日期", "legal_status_raw": "法律状态",
            "inventor_raw": "发明人", "assignee_raw": "申请(专利权)人"}),
        (SRC_RICH, {
            "pub_no": "公开号", "app_no": "申请号", "title": "专利名称",
            "abstract": "摘要", "app_date": "申请日", "pub_date": "公开日",
            "ipc_raw": "IPC", "legal_status_raw": "法律状态",
            "inventor_raw": "发明人", "assignee_raw": "申请人"}),
    ]
    n = 0
    now = datetime.now().isoformat(timespec="seconds")
    for path, mapping in specs:
        for row_no, row in _read_csv_rows(path):
            vals = {k: _clean(row.get(v)) for k, v in mapping.items()}
            n += 1
            if not dry:
                write_cur.execute(
                    "INSERT INTO ods_patent_raw"
                    "(pub_no, app_no, title, abstract, app_date, pub_date,"
                    " assignee_raw, inventor_raw, ipc_raw, legal_status_raw,"
                    " country_code, source_id, file_path, row_no, ingested_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (vals.get("pub_no"), vals.get("app_no"), vals.get("title"),
                     vals.get("abstract"), vals.get("app_date"), vals.get("pub_date"),
                     vals.get("assignee_raw"), vals.get("inventor_raw"),
                     vals.get("ipc_raw"), vals.get("legal_status_raw"),
                     "CN", "zj_500family", path.name, row_no, now),
                )
    return n


def backfill_patent_lineage(read_cur, write_cur, dry):
    """回填 fact_patent.source_id / raw_id（优先并集 CSV 行，按 pub_no+app_no 匹配）。"""
    key2raw = {}
    for raw_id, pub, app in read_cur.execute(
        "SELECT raw_id, pub_no, app_no FROM ods_patent_raw"
    ).fetchall():
        for key in ((pub, app), (pub, None), (None, app)):
            if key[0] or key[1]:
                key2raw.setdefault(key, raw_id)
    n = 0
    rows = read_cur.execute(
        "SELECT patent_id, app_no FROM fact_patent WHERE source_id IS NULL OR raw_id IS NULL"
    ).fetchall()
    for pid, app in rows:
        raw_id = key2raw.get((pid, app)) or key2raw.get((pid, None)) or (key2raw.get((None, app)) if app else None)
        if raw_id is None:
            continue
        n += 1
        if not dry:
            write_cur.execute(
                "UPDATE fact_patent SET source_id='zj_500family', raw_id=? WHERE patent_id=?",
                (raw_id, pid),
            )
    return n, len(rows)


# ------------------------------------------------------------- 3) log_match
def load_log_match(read_cur, write_cur, dry):
    now = datetime.now().isoformat(timespec="seconds")
    # A) 专利申请人匹配审计（聚合级）
    n_pat = 0
    rows = read_cur.execute(
        "SELECT enterprise_id, assignee_raw, match_method, COUNT(*), AVG(match_confidence)"
        " FROM fact_patent WHERE assignee_raw IS NOT NULL"
        " GROUP BY enterprise_id, assignee_raw, match_method"
    ).fetchall()
    for eid, raw_name, method, cnt, avg_conf in rows:
        n_pat += 1
        if not dry:
            write_cur.execute(
                "INSERT INTO log_match"
                "(stage, patent_id, raw_name, candidate_ent, method, score, decision, created_at)"
                " VALUES ('patent_assignee', NULL, ?, ?, ?, ?, 'accepted', ?)",
                (raw_name.strip(), eid, method or "unknown",
                 round(avg_conf, 4) if avg_conf is not None else None, now),
            )
    # B) 资质名单公司匹配审计（按公司名归一命中即 accepted）
    n_hit = n_miss = 0
    ent_norms = set(r[0] for r in read_cur.execute("SELECT name_norm FROM dim_enterprise"))
    seen = set()
    for (raw_name,) in read_cur.execute(
        "SELECT DISTINCT company_name FROM ods_qual_company_raw"
    ).fetchall():
        if not raw_name:
            continue
        nm = norm_company_name(raw_name)
        if nm in seen:
            continue
        seen.add(nm)
        if nm in ent_norms:
            n_hit += 1
            if not dry:
                write_cur.execute(
                    "INSERT INTO log_match"
                    "(stage, raw_name, candidate_ent, method, score, decision, created_at)"
                    " VALUES ('qual_company', ?, NULL, 'name_norm_exact', 1.0, 'accepted', ?)",
                    (raw_name.strip(), now),
                )
        else:
            n_miss += 1
    return n_pat, n_hit, n_miss


# ------------------------------------------------------------------- main
def main(dry=False):
    if not DB.exists():
        raise SystemExit(f"未找到主库: {DB}")
    con = sqlite3.connect(DB)
    rcur = con.cursor()      # 只读游标
    wcur = con.cursor()      # 写游标（读写分离，避免迭代中断）
    mode = "DRY-RUN（未写库）" if dry else "写库"
    try:
        if not dry:
            wcur.execute("DELETE FROM dim_enterprise_alias")
            wcur.execute("DELETE FROM ods_patent_raw")
            wcur.execute("DELETE FROM log_match")
            wcur.execute("UPDATE fact_patent SET source_id=NULL, raw_id=NULL")
        n_f, n_v = load_aliases(rcur, wcur, dry)
        n_ods = load_ods_patent_raw(wcur, dry)
        n_bl, n_todo = backfill_patent_lineage(rcur, wcur, dry)
        n_p, n_qh, n_qm = load_log_match(rcur, wcur, dry)
        if not dry:
            con.commit()
        print(f"[{mode}]")
        print(f"  dim_enterprise_alias : 曾用名 {n_f} + 申请人变体 {n_v}")
        print(f"  ods_patent_raw       : 落地 {n_ods} 行（3 CSV 原样）")
        print(f"  fact_patent 血缘回填 : {n_bl}/{n_todo} 条 source_id/raw_id")
        print(f"  log_match            : patent_assignee {n_p} 条 / qual_company accepted {n_qh} 条"
              f"（未匹配 {n_qm} 家仅统计未入日志）")
    finally:
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(dry=args.dry_run)
