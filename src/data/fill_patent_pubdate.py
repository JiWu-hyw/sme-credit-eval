"""从 企查查_专利.csv 回填 fact_patent.pub_date（公开日）。

背景
----
fact_patent 当前 pub_date 全为空（0%）。源 CSV `企查查_专利.csv` 含 `公开(公告)日期`
（28,289 行 100% 有值，格式 YYYY-MM-DD），经 申请号/公开(公告)号 可命中 DB 中
28,289 条专利。本脚本仅 UPDATE 这一列，纯增量、幂等、可重跑。

用法
----
    python -m src.data.fill_patent_pubdate [--dry-run] [--csv PATH] [--db PATH]

注意：仅回填 pub_date 为 NULL 的行（已填的不覆盖）；日期非 YYYY-MM-DD 会尝试
宽松解析并归一化，无法解析则跳过该行。
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "..", "..", "data", "db", "zjtech_credit.db")
DEFAULT_CSV = os.path.join(
    HERE, "..", "..", "..", "_data_collect", "out", "企查查_专利.csv"
)

_DATE_RE = re.compile(r"^\s*(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})")


def _norm_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    m = _DATE_RE.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None
    # 退路：尝试 ISO / 其它常见形态
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def build_map(csv_path: str) -> dict[str, str]:
    """key: 申请号 或 公开(公告)号 -> 归一化公开日。后者优先级低，故先填 app_no。"""
    by_appno: dict[str, str] = {}
    by_pubno: dict[str, str] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pd = _norm_date(row.get("公开(公告)日期"))
            if not pd:
                continue
            appno = (row.get("申请号") or "").strip()
            pubno = (row.get("公开(公告)号") or "").strip()
            if appno:
                by_appno[appno] = pd
            if pubno:
                by_pubno[pubno] = pd
    return by_appno, by_pubno


def run(db_path: str, csv_path: str, dry_run: bool = False) -> None:
    by_appno, by_pubno = build_map(csv_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    before = cur.execute(
        "SELECT COUNT(*) FROM fact_patent WHERE pub_date IS NOT NULL"
    ).fetchone()[0]
    rows = cur.execute("SELECT patent_id, app_no FROM fact_patent").fetchall()

    updates: list[tuple[str, str]] = []  # (pub_date, patent_id)
    seen: set[str] = set()
    skipped_nonnull = 0
    for pid, appno in rows:
        # 优先 app_no（更稳定），回退 pubno
        new_pd = by_appno.get(appno) if appno else None
        if new_pd is None and pid:
            new_pd = by_pubno.get(pid)
        if new_pd is None:
            continue
        if pid in seen:
            continue
        # 已填则不覆盖
        if cur.execute(
            "SELECT 1 FROM fact_patent WHERE patent_id=? AND pub_date IS NOT NULL",
            (pid,),
        ).fetchone():
            skipped_nonnull += 1
            continue
        updates.append((new_pd, pid))
        seen.add(pid)

    print(f"CSV 映射规模: app_no={len(by_appno)} pub_no={len(by_pubno)}")
    print(f"DB 行数: {len(rows)}; 回填前 pub_date 非空: {before}")
    print(f"本次待 UPDATE: {len(updates)} 条; 跳过(已非空): {skipped_nonnull}")

    if dry_run:
        print("[dry-run] 不写库。")
        top = updates[:5]
        for pd, pid in top:
            print(f"  {pid} -> {pd}")
        con.close()
        return

    cur.executemany(
        "UPDATE fact_patent SET pub_date=? WHERE patent_id=?", updates
    )
    con.commit()
    after = cur.execute(
        "SELECT COUNT(*) FROM fact_patent WHERE pub_date IS NOT NULL"
    ).fetchone()[0]
    con.close()
    print(f"✅ 写库完成。回填后 pub_date 非空: {after} / {len(rows)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    run(args.db, args.csv, args.dry_run)


if __name__ == "__main__":
    main()
