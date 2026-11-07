# -*- coding: utf-8 -*-
"""A7：回填 fact_funding_event.source_id（只补溯源列，不动任何业务数值）。

背景
----
`src/data/load_500family.py:load_funding()` 把 `合并_融资事件流水.csv` 的 506 条
事件全部导入 `fact_funding_event`，并在 `meta_ingest_log` 里以
`source_id='zj_500family'` 登记了 506/506 的 parse 记录；但 INSERT 语句把行内的
`source_id` 写成了 NULL，导致 0/506 有值，行级血缘断掉。

依据（三重对账，不猜来源）
--------------------------
1. `data/raw/合并_融资事件流水.csv` 实测 506 行 = 表内 506 行；
2. `meta_ingest_log` 已有的 parse 记录：zj_500family / fact_funding_event / 506 in, 506 ok；
3. `meta_data_source.source_id='zj_500family'` 登记在册且 is_active=1。

边界
----
- **只 UPDATE `source_id IS NULL` 的行**，且只写这一个常量；
- 金额 `amount_cny`、估值 `post_valuation`、机构等级、机构名单、事件日期等
  所有业务列一律不动，回填前后用 SHA-256 指纹逐列对账；
- 不改 schema（列本来就存在），因此 **schema 版本保持 v1.8**，不升 v1.9；
- 单列一次变更记录：`outputs/funding_provenance_backfill.json` 凭证 +
  `deliverables/RELEASE_v1.4.0.md` 单列一节。**不往 `meta_ingest_log` 追加新行**：
  该表 `stage` 有 CHECK 约束只允许 fetch/parse/match/build，而硬塞一个 parse 记录会
  和原有的 506/506 parse 入库记录混淆，反而破坏证据链；
- 幂等：重复执行不会重复写日志、不会重复回填，只做只读校验。

用法：
    python -m src.data.apply_funding_provenance            # 执行回填（幂等）
    python -m src.data.apply_funding_provenance --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_provenance.db")
RAW_CSV = os.path.join(ROOT, "data", "raw", "合并_融资事件流水.csv")
REPORT = os.path.join(ROOT, "outputs", "funding_provenance_backfill.json")

SOURCE_ID = "zj_500family"
EXPECT_ROWS = 506
CHANGE_ID = "A7_provenance_backfill"

# 业务列（除 source_id 之外的全部列）——回填前后逐列指纹对账
BUSINESS_COLS = (
    "event_id", "enterprise_id", "event_date", "event_year", "event_type",
    "amount_cny", "round_label", "post_valuation", "investor_names",
    "prestige_rank", "is_public_co",
)


def fingerprint(con: sqlite3.Connection) -> str:
    """按主键排序，把业务列整体哈希；任何一个数值被改动都会变。"""
    sql = "SELECT %s FROM fact_funding_event ORDER BY event_id" % ", ".join(BUSINESS_COLS)
    rows = con.execute(sql).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def raw_row_count() -> int:
    if not os.path.exists(RAW_CSV):
        return -1
    with open(RAW_CSV, encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def check(con: sqlite3.Connection) -> list[str]:
    """确定性只读校验：列全覆盖、来源单一且登记在册、业务列指纹未变。"""
    bad: list[str] = []

    n = con.execute("SELECT COUNT(*) FROM fact_funding_event").fetchone()[0]
    if n != EXPECT_ROWS:
        bad.append("融资事件行数 %d，期望 %d" % (n, EXPECT_ROWS))

    n_null = con.execute(
        "SELECT COUNT(*) FROM fact_funding_event WHERE source_id IS NULL").fetchone()[0]
    if n_null:
        bad.append("source_id 仍有 %d 行未回填" % n_null)

    got = [r[0] for r in con.execute(
        "SELECT DISTINCT source_id FROM fact_funding_event ORDER BY 1")]
    if got != [SOURCE_ID]:
        bad.append("source_id 取值不唯一：%r，期望仅 %r" % (got, SOURCE_ID))

    src = con.execute(
        "SELECT is_active FROM meta_data_source WHERE source_id=?", (SOURCE_ID,)).fetchone()
    if not src:
        bad.append("meta_data_source 未登记 %s" % SOURCE_ID)
    elif src[0] != 1:
        bad.append("meta_data_source 中 %s 的 is_active=%r" % (SOURCE_ID, src[0]))

    parse = con.execute(
        "SELECT COUNT(*) FROM meta_ingest_log WHERE target_table='fact_funding_event' "
        "AND source_id=? AND stage='parse' AND rows_in=? AND rows_ok=? AND status='ok'",
        (SOURCE_ID, EXPECT_ROWS, EXPECT_ROWS)).fetchone()[0]
    if not parse:
        bad.append("找不到 506/506 的 zj_500family parse 入库记录，来源证据链断裂")

    n_raw = raw_row_count()
    if n_raw >= 0 and n_raw != n:
        bad.append("源 CSV %d 行与事实表 %d 行不一致" % (n_raw, n))

    if not os.path.exists(REPORT):
        bad.append("变更凭证不存在：%s" % REPORT)
    else:
        with open(REPORT, encoding="utf-8") as f:
            rep = json.load(f)
        for k in ("business_columns_fingerprint_before", "business_columns_fingerprint_after"):
            if rep.get(k) != fingerprint(con):
                bad.append("业务列指纹与凭证 %s 不一致（业务数值被改动？）" % k)
        if rep.get("business_columns_fingerprint_before") != rep.get(
                "business_columns_fingerprint_after"):
            bad.append("凭证里回填前后指纹不相等")
        if rep.get("rows_backfilled") != EXPECT_ROWS or rep.get("source_id") != SOURCE_ID:
            bad.append("凭证里回填行数 / 来源标识不符")
        ev = rep.get("ingest_log_evidence") or {}
        if (ev.get("source_id") != SOURCE_ID or ev.get("rows_in") != EXPECT_ROWS
                or ev.get("rows_ok") != EXPECT_ROWS):
            bad.append("凭证里的 parse 入库证据不完整")
        live = con.execute(
            "SELECT log_id FROM meta_ingest_log WHERE log_id=? AND source_id=? "
            "AND target_table='fact_funding_event' AND stage='parse'",
            (ev.get("log_id"), SOURCE_ID)).fetchone()
        if not live:
            bad.append("凭证引用的 parse 入库记录在库中不存在")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读校验，不改库")
    args = ap.parse_args()

    if args.check:
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        bad = check(con)
        con.close()
        print("只读校验：%s" % ("通过" if not bad else "失败"))
        for b in bad:
            print("  x", b)
        return 0 if not bad else 1

    if not os.path.exists(DB):
        print("数据库不存在：%s" % DB)
        return 1

    # 幂等：已经回填过就只校验，不重复备份 / 不重复写日志
    probe = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    n_null = probe.execute(
        "SELECT COUNT(*) FROM fact_funding_event WHERE source_id IS NULL").fetchone()[0]
    probe.close()
    if n_null == 0 and os.path.exists(REPORT):
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        bad = check(con)
        con.close()
        print("[A7] 已回填，跳过写入；只读校验 %s" % ("通过" if not bad else "失败"))
        for b in bad:
            print("  x", b)
        return 0 if not bad else 1

    if not os.path.exists(BACKUP):
        shutil.copy2(DB, BACKUP)
        print("[backup] 回填前快照 -> %s" % BACKUP)

    con = sqlite3.connect(DB)
    try:
        # 证据链先验：行数 / 源 CSV / 元数据登记 / parse 日志
        n = con.execute("SELECT COUNT(*) FROM fact_funding_event").fetchone()[0]
        n_raw = raw_row_count()
        if n != EXPECT_ROWS or (n_raw >= 0 and n_raw != n):
            print("证据链不符：表 %d 行 / 源 CSV %d 行，拒绝回填" % (n, n_raw))
            return 1
        if not con.execute("SELECT 1 FROM meta_data_source WHERE source_id=?",
                           (SOURCE_ID,)).fetchone():
            print("meta_data_source 未登记 %s，拒绝回填" % SOURCE_ID)
            return 1
        if not con.execute(
                "SELECT 1 FROM meta_ingest_log WHERE target_table='fact_funding_event' "
                "AND source_id=? AND stage='parse' AND rows_in=? AND rows_ok=?",
                (SOURCE_ID, EXPECT_ROWS, EXPECT_ROWS)).fetchone():
            print("找不到 506/506 的 parse 入库记录，拒绝回填")
            return 1

        before = fingerprint(con)
        cur = con.execute(
            "UPDATE fact_funding_event SET source_id=? WHERE source_id IS NULL",
            (SOURCE_ID,))
        changed = cur.rowcount
        after = fingerprint(con)
        if before != after:
            con.rollback()
            print("业务列指纹在回填中发生变化，已回滚；拒绝提交")
            return 1

        ev_row = con.execute(
            "SELECT log_id, source_id, rows_in, rows_ok, status FROM meta_ingest_log "
            "WHERE target_table='fact_funding_event' AND source_id=? AND stage='parse' "
            "ORDER BY log_id DESC LIMIT 1", (SOURCE_ID,)).fetchone()
        con.commit()

        rep = {
            "change_id": CHANGE_ID,
            "change": "A7 fact_funding_event.source_id 行级溯源回填",
            "schema_version": "v1.8",
            "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_id": SOURCE_ID,
            "rows_total": EXPECT_ROWS,
            "rows_backfilled": changed,
            "raw_csv": "data/raw/合并_融资事件流水.csv",
            "raw_csv_rows": n_raw,
            "ingest_log_evidence": {
                "log_id": ev_row[0], "source_id": ev_row[1],
                "rows_in": ev_row[2], "rows_ok": ev_row[3], "status": ev_row[4],
            },
            "columns_untouched": list(BUSINESS_COLS),
            "business_columns_fingerprint_before": before,
            "business_columns_fingerprint_after": after,
            "schema_structure_changed": False,
            "note": (
                "load_500family.py 原 INSERT 把行级 source_id 写成 NULL，但 "
                "meta_ingest_log 已有 zj_500family 的 506/506 parse 记录、"
                "meta_data_source 已登记该源。只补这一列常量，不动金额/估值/事件等"
                "任何业务数值；schema 版本不升，仍为 v1.8。"
            ),
        }
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)

        bad = check(con)
        print("[A7] 回填 %d 行 -> %s；业务列指纹 %s（前后一致）" %
              (changed, SOURCE_ID, before[:16]))
        print("[A7] 变更凭证 -> %s" % REPORT)
        if bad:
            print("校验失败：")
            for b in bad:
                print("  x", b)
            return 1
        print("A7 PROVENANCE BACKFILL PASSED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
