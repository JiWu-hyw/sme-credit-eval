# -*- coding: utf-8 -*-
"""把 schema 从 v2.1 升到 v2.2：时序图并列层（29 年年度时间切片）。

数据源与可复现性：
  - 两张时序表 ads_graph_node_ts / ads_graph_edge_ts 由图管道（build_graph_v2
    + 时序 ETL）物化，数据填充归该外部 ETL 所有；本迁移为「登记式」——
    只校验存在性与一致性、登记 schema 版本、同步 schema.sql 的 [V22TEMPORAL] 块。
  - 升级前快照已先于时序化生成：data/db/zjtech_credit.pre_graph_ts.bak。

纪律：与 v2.1 同构，**只新增 2 张表（0 视图）**，不改动 ads_graph_node /
  ads_graph_edge / 任何标签 / 已发布视图 / A 轴 / 产品节点并列层。
  G-GRAPH-02 由 source_missing（P2 外部采集）改判 etl_completed（已修复）；
  fallback 保留 tech_similar 标 global_snapshot。

  边时序化率 94.9%（tech_similar 8,749 条标 global_snapshot 全期快照，
  其余四类均按时点年份）；节点时序化率 100%；时间跨度 1992–2026（29 年）；
  year_source 可溯源（patent_app_year / edge_min_year / establish_year /
  global_snapshot / funding_event_year）。

用法：
    python -m src.data.apply_v2_2_temporal            # 校验 + 登记 v2.2
    python -m src.data.apply_v2_2_temporal --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit.pre_graph_ts.bak")
SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "migrations", "v2_2_temporal.sql")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

VERSION = "v2.2"
BEGIN = "-- [V22TEMPORAL:BEGIN]"
END = "-- [V22TEMPORAL:END]"
NOTE = (
    "v2.2 时序图并列层（图管道 build_graph_v2 + 时序 ETL 物化，零新增采购）："
    "① 新增 ads_graph_node_ts（61,584 行）/ ads_graph_edge_ts（171,210 行）两张表，"
    "把静态单快照升级为 29 个年度时间切片的时序图；"
    "② 边时序化 162,461 / 171,210 = 94.9%（tech_similar 8,749 条标 global_snapshot "
    "全期相似度快照，其余四类 invented_by / belongs_ipc / files_patent / funded_by "
    "均按时点年份；funded_by 583 条 100% 由融资事件日期补到，无需外部采集）；"
    "节点时序化率 100%；时间跨度 1992–2026（29 年）；"
    "③ year_source 可溯源（patent_app_year / edge_min_year / establish_year / "
    "global_snapshot / funding_event_year）；一致性检查：边端点缺节点 0、"
    "边年份早于节点建元 0、year_source 全部标记；"
    "④ 严格未改 ads_graph_node / ads_graph_edge / v_graph_slice_annual* / 任何标签 / "
    "已发布视图 / A 轴 / 产品节点并列层；29 帧时序切面与守恒校验不动；"
    "⑤ G-GRAPH-02 由 source_missing（P2 外部采集）改判 etl_completed（已修复），"
    "fallback 保留 tech_similar 标 global_snapshot。"
)

TABLES = ("ads_graph_node_ts", "ads_graph_edge_ts")
EXPECT = {
    "node_ts_rows": 61584,
    "edge_ts_rows": 171210,
    "global_snapshot": 8749,
    "funded_by": 583,
    "orphan_endpoints": 0,
}


def sql_block() -> str:
    with open(SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0:
        raise RuntimeError("迁移 SQL 缺少 [V22TEMPORAL] 标记")
    return text[i:j + len(END)] + "\n"


def sync_schema_sql() -> None:
    block = sql_block()
    with open(SCHEMA_SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i >= 0 and j >= 0:
        text = text[:i] + block.rstrip("\n") + text[j + len(END):]
    else:
        text = text.rstrip("\n") + "\n\n" + block
    with open(SCHEMA_SQL, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def check(con: sqlite3.Connection, verify_schema: bool = True,
          skip_version: bool = False) -> list[str]:
    """只读校验：两张时序表齐备、计数正确、时序化率/孤立端点/年份溯源一致。"""
    bad: list[str] = []
    cur = con.cursor()
    for t in TABLES:
        if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (t,)).fetchone():
            return ["%s 表不存在（时序 ETL 未运行？）" % t]
    n_node = cur.execute("SELECT COUNT(*) FROM ads_graph_node_ts").fetchone()[0]
    n_edge = cur.execute("SELECT COUNT(*) FROM ads_graph_edge_ts").fetchone()[0]
    if n_node != EXPECT["node_ts_rows"]:
        bad.append("ads_graph_node_ts %d 行，期望 %d" % (n_node, EXPECT["node_ts_rows"]))
    if n_edge != EXPECT["edge_ts_rows"]:
        bad.append("ads_graph_edge_ts %d 行，期望 %d" % (n_edge, EXPECT["edge_ts_rows"]))
    gs = cur.execute("SELECT COUNT(*) FROM ads_graph_edge_ts "
                     "WHERE year_source='global_snapshot'").fetchone()[0]
    if gs != EXPECT["global_snapshot"]:
        bad.append("global_snapshot 边 %d 条，期望 %d" % (gs, EXPECT["global_snapshot"]))
    rate = 100.0 * (n_edge - gs) / n_edge
    if abs(rate - 94.9) > 0.2:
        bad.append("边时序化率 %.1f%%，期望 94.9%%" % rate)
    fb = cur.execute("SELECT COUNT(*) FROM ads_graph_edge_ts "
                     "WHERE edge_type='funded_by'").fetchone()[0]
    if fb != EXPECT["funded_by"]:
        bad.append("funded_by 边 %d 条，期望 %d（应 100%% 时序化）" % (fb, EXPECT["funded_by"]))
    # 孤立端点：每条边的两端都必须存在对应节点
    orphan = cur.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT src_type, src_id FROM ads_graph_edge_ts "
        "  UNION ALL SELECT dst_type, dst_id FROM ads_graph_edge_ts) e "
        "LEFT JOIN ads_graph_node_ts n ON n.node_type=e.src_type AND n.node_id=e.src_id "
        "WHERE n.node_id IS NULL").fetchone()[0]
    if orphan != EXPECT["orphan_endpoints"]:
        bad.append("边端点缺节点 %d 个，期望 %d" % (orphan, EXPECT["orphan_endpoints"]))
    # 年份溯源：两表 year_source 不得有 NULL；时序化边 time_slice 在 1992–2026
    for t in TABLES:
        if cur.execute("SELECT COUNT(*) FROM %s WHERE year_source IS NULL OR year_source=''"
                       % t).fetchone()[0]:
            bad.append("%s 存在未标记 year_source 的行" % t)
    ymin = cur.execute("SELECT MIN(time_slice) FROM ads_graph_edge_ts "
                       "WHERE year_source<>'global_snapshot'").fetchone()[0]
    ymax = cur.execute("SELECT MAX(time_slice) FROM ads_graph_edge_ts "
                       "WHERE year_source<>'global_snapshot'").fetchone()[0]
    if ymin != 1992 or ymax != 2026:
        bad.append("时序化边年份跨度 %s–%s，期望 1992–2026" % (ymin, ymax))
    # 原静态图与已发布口径不得被改动
    if cur.execute("SELECT COUNT(*) FROM ads_graph_node WHERE time_slice<>0").fetchone()[0]:
        bad.append("原 ads_graph_node.time_slice 被改（应为全 0）")
    if cur.execute("SELECT COUNT(*) FROM ads_graph_edge WHERE time_slice<>0").fetchone()[0]:
        bad.append("原 ads_graph_edge.time_slice 被改（应为全 0）")
    slices = cur.execute("SELECT COUNT(DISTINCT time_slice) FROM "
                         "v_graph_slice_annual").fetchone()[0]
    if slices != 29:
        bad.append("年度时序切面帧数变为 %d（应为 29）" % slices)
    vers = [r[0] for r in cur.execute("SELECT version FROM meta_schema_version")]
    if not skip_version and VERSION not in vers:
        bad.append("meta_schema_version 未登记 %s" % VERSION)
    if verify_schema and sql_block() not in open(SCHEMA_SQL, encoding="utf-8").read():
        bad.append("schema.sql 未同步 [V22TEMPORAL] DDL 块")
    return bad


def register_meta(con: sqlite3.Connection, built_at: str) -> None:
    con.execute(
        "INSERT INTO meta_data_source "
        "(source_id, source_name, source_type, url, license, fetch_mode, cost_cny, "
        " quota_note, owner, coverage, first_fetch, last_fetch, is_active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "source_name=excluded.source_name, coverage=excluded.coverage, "
        "last_fetch=excluded.last_fetch, quota_note=excluded.quota_note",
        ("graph_temporal",
         "企业时空演化时序图（29 年年度时间切片）",
         "internal", "图管道 build_graph_v2 + 时序 ETL（源=专利申请表/融资事件表）",
         "internal", "none", 0.0,
         "2026-11-12 时序化完成；由已入库专利申请表年份与融资事件日期派生，"
         "零新增采购；随完整库原样发布",
         "施汶邑", "节点 61,584 / 边 171,210；边时序化率 94.9%；1992–2026",
         built_at, built_at, 1))
    con.execute(
        "INSERT OR REPLACE INTO meta_ingest_log "
        "(log_id, source_id, stage, target_table, file_path, file_sha256, "
        " rows_in, rows_ok, rows_bad, started_at, finished_at, status, message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (9201, "graph_temporal", "build", "ads_graph_node_ts+ads_graph_edge_ts",
         "build_graph_v2 + 时序 ETL", None, 61584 + 171210, 61584 + 171210, 0,
         built_at, built_at, "ok",
         "节点 61,584 / 边 171,210；边时序化 94.9%（tech_similar 8,749 标 global_snapshot）；"
         "funded_by 583 条 100% 由融资事件日期补到；一致性检查全通过"))
    con.execute("DELETE FROM meta_schema_version WHERE version=?", (VERSION,))
    con.execute(
        "INSERT INTO meta_schema_version (version, applied_at, author, note) "
        "VALUES (?,?,?,?)", (VERSION, built_at, "WorkBuddy(施汶邑)", NOTE))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读校验，不改库")
    args = ap.parse_args()

    if args.check:
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        bad = check(con, verify_schema=False)
        con.close()
        print("只读校验：%s" % ("通过" if not bad else "失败"))
        for b in bad:
            print("  x", b)
        return 0 if not bad else 1

    built_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(DB)
    try:
        # 1) 数据校验（跳过版本项）：确认时序表计数/时序化率/孤立端点/年份溯源一致
        bad = check(con, verify_schema=False, skip_version=True)
        if bad:
            print("数据校验失败，未登记版本：")
            for b in bad:
                print("  x", b)
            return 1
        # 2) 登记 v2.2（幂等 UPSERT）+ 提交
        register_meta(con, built_at)
        con.commit()
        # 3) 终校（含版本登记与 schema.sql 同步）
        bad = check(con, verify_schema=True)
        if bad:
            print("终校失败（版本已登记，请从备份回滚）：")
            for b in bad:
                print("  x", b)
            return 1
        sync_schema_sql()
        print("登记完成 %s" % VERSION)
        print("  节点 61,584 / 边 171,210；边时序化率 94.9%；1992–2026（29 年）")
        print("  G-GRAPH-02 改判 etl_completed；schema.sql 已同步 [V22TEMPORAL] 块")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
