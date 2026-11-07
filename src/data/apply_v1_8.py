# -*- coding: utf-8 -*-
"""把 schema 从 v1.7 升到 v1.8：口径一致性三视图 + 登记版本。

幂等：重复执行只会 DROP/CREATE 同一组视图并 REPLACE 同一行版本记录。
执行前自动留一份升级前快照 data/db/zjtech_credit_pre_v18.db 作回滚点。

只增视图，不改任何事实表数据、不改主宽表 29 列结构、不改 ASOF_VER。

用法：
    python -m src.data.apply_v1_8            # 执行迁移
    python -m src.data.apply_v1_8 --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_v18.db")
SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "migrations", "v1_8_consistency.sql")

VERSION = "v1.8"
NOTE = (
    "v1.8 口径一致性（评审报告 §4.1 / §5.2(1) / §5.3）："
    "① 新增 v_feature_coverage_effective，把 m_funding / investor_prestige 的覆盖"
    "从旁路表 ads_feature_asof_funding 按双键并入，原 v_asof_feature_coverage 保持不动，"
    "零覆盖特征由 14 个收敛为 12 个（其余 11 个真无数据源，不造值）；"
    "② 新增 v_graph_slice_annual（29 年 × 4 指标累计台账）与 "
    "v_graph_slice_annual_edge（边级年度切片），修正 README 切片 SQL 的两处陈旧："
    "关联键改用 fact_patent.patent_id（去重主视图会漏 19% 的桥表边），"
    "IPC 节点 ID 剥 WIPO 版本号后缀与预置图对齐；"
    "③ 物理 ads_graph_node/edge.time_slice 仍全为 0（全期累计，与既有契约绑定，"
    "且 build_graph_v2 幂等重建会写回 0），年度切片一律走视图，禁止 UPDATE 物理表。"
    "④ API 新增 /api/enterprise/grade 与 /api/data/datasets，"
    "/api/data/pipeline 支持 dataset 参数做 A/B 两套数据隔离；契约新增 enterprise_grade.json。"
    "v1.3.0 Release 附件仍为 v1.6 快照。"
)

# 末年（T=2026）累计规模，独立 SQL 实跑基准
EXPECT_CUM = {
    "n_node_patent": 32969,
    "n_edge_files_patent": 32203,
    "n_edge_belongs_ipc": 51795,
    "n_edge_invented_by": 77899,
}
# 旁路表实测非空家数（cohort / due_minus_1），用于有效覆盖对账
EXPECT_SIDE = {
    ("cohort", "m_funding"): 307,
    ("due_minus_1", "m_funding"): 307,
    ("cohort", "investor_prestige"): 59,
    ("due_minus_1", "investor_prestige"): 75,
}
VIEWS = ("v_feature_coverage_effective", "v_graph_slice_annual",
         "v_graph_slice_annual_edge")


def check(con: sqlite3.Connection) -> list[str]:
    """确定性校验：视图存在、覆盖收敛、物理口径未动、切片累计对账、版本登记。"""
    bad: list[str] = []
    for v in VIEWS:
        if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
                (v,)).fetchone():
            return ["%s 视图不存在" % v]

    # ① 有效覆盖：原视图 14 个零覆盖，新视图 12 个，且只收敛两列
    n_zero_old = con.execute(
        "SELECT COUNT(*) FROM v_asof_feature_coverage "
        "WHERE as_of_policy='cohort' AND n_available=0").fetchone()[0]
    n_zero_new = con.execute(
        "SELECT COUNT(*) FROM v_feature_coverage_effective "
        "WHERE as_of_policy='cohort' AND n_available=0").fetchone()[0]
    if n_zero_old != 14 or n_zero_new != 12:
        bad.append("零覆盖特征数不符：原视图 %d / 新视图 %d，期望 14 / 12"
                   % (n_zero_old, n_zero_new))
    side = dict(con.execute(
        "SELECT as_of_policy || '|' || feature_name, n_available "
        "FROM v_feature_coverage_effective WHERE requires_left_join=1").fetchall())
    got_side = {tuple(k.split("|")): v for k, v in side.items()}
    if got_side != EXPECT_SIDE:
        bad.append("旁路两列覆盖与实测不符：实得 %r，期望 %r" % (got_side, EXPECT_SIDE))
    if con.execute(
            "SELECT COUNT(*) FROM v_feature_coverage_effective "
            "WHERE requires_left_join=1 AND feature_name NOT IN "
            "('m_funding','investor_prestige')").fetchone()[0]:
        bad.append("requires_left_join 标到了旁路两列之外的特征")
    if con.execute(
            "SELECT COUNT(*) FROM v_feature_coverage_effective "
            "WHERE requires_left_join=0 AND n_available<>0 "
            "AND feature_name IN ('m_funding','investor_prestige')").fetchone()[0]:
        bad.append("旁路两列有未标来源的覆盖值")

    # ② as-of 契约未动：29 个特征 / 17 列训练入口 / 两列恒 NULL / 视图不引用旁路表
    #    （与 tests/test_funding_join_contract.py 的 f_structure_untouched 同口径）
    nf = con.execute(
        "SELECT COUNT(DISTINCT feature_name) FROM ads_feature_asof").fetchone()[0]
    if nf != 29:
        bad.append("ads_feature_asof 特征数被改动：%d，期望 29" % nf)
    n_train = len(con.execute("PRAGMA table_info(v_model_train_ready)").fetchall())
    if n_train != 17:
        bad.append("v_model_train_ready 列数被改动：%d，期望 17" % n_train)
    for col in ("m_funding", "investor_prestige"):
        if con.execute("SELECT COUNT(\"%s\") FROM v_model_dataset_asof_flat"
                       % col).fetchone()[0]:
            bad.append("主宽表 %s 被原地回填，违反 LEFT JOIN 契约" % col)
    ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='v_model_dataset_asof_flat'"
    ).fetchone()[0]
    if "ads_feature_asof_funding" in ddl:
        bad.append("主宽表视图被改成引用旁路表，ASOF_VER 契约被破坏")

    # ③ 物理 time_slice 仍全 0（年度切片只走视图）
    for t in ("ads_graph_node", "ads_graph_edge"):
        n = con.execute("SELECT COUNT(*) FROM \"%s\" WHERE time_slice<>0" % t).fetchone()[0]
        if n:
            bad.append("%s 的物理 time_slice 被改写：%d 行" % (t, n))

    # ④ 年度切片台账：29 年 × 4 指标，累计单调，末年和独立基准一致
    n_y = con.execute("SELECT COUNT(DISTINCT time_slice) FROM v_graph_slice_annual").fetchone()[0]
    n_m = con.execute("SELECT COUNT(DISTINCT metric) FROM v_graph_slice_annual").fetchone()[0]
    if (n_y, n_m) != (29, 4):
        bad.append("台账维度不符：%d 年 / %d 指标，期望 29 / 4" % (n_y, n_m))
    tmax = con.execute("SELECT MAX(time_slice) FROM v_graph_slice_annual").fetchone()[0]
    cum = dict(con.execute(
        "SELECT metric, n_cumulative FROM v_graph_slice_annual WHERE time_slice=?",
        (tmax,)).fetchall())
    if cum != EXPECT_CUM:
        bad.append("末年累计与独立基准不符：实得 %r，期望 %r" % (cum, EXPECT_CUM))
    dec = con.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT metric, time_slice, n_cumulative,"
        "        LAG(n_cumulative) OVER (PARTITION BY metric ORDER BY time_slice) AS prev"
        " FROM v_graph_slice_annual) WHERE prev IS NOT NULL AND n_cumulative < prev"
    ).fetchone()[0]
    if dec:
        bad.append("累计台账出现非单调年份：%d 处" % dec)

    # ⑤ 边级切片：列齐全、能按 T 取数、IPC 节点 ID 不带版本号后缀
    cols = [r[1] for r in con.execute("PRAGMA table_info(v_graph_slice_annual_edge)")]
    for c in ("time_slice", "src_type", "src_id", "dst_type", "dst_id",
              "edge_type", "weight"):
        if c not in cols:
            bad.append("边级切片视图缺列 %s" % c)
    dirty = con.execute(
        "SELECT COUNT(*) FROM (SELECT dst_id FROM v_graph_slice_annual_edge "
        "WHERE edge_type='belongs_ipc' AND time_slice=1992) "
        "WHERE instr(dst_id,'(')>0").fetchone()[0]
    if dirty:
        bad.append("IPC 节点 ID 未剥版本号后缀：%d 条" % dirty)

    # ⑥ 版本登记
    vers = [r[0] for r in con.execute("SELECT version FROM meta_schema_version")]
    if VERSION not in vers:
        bad.append("meta_schema_version 未登记 %s" % VERSION)
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
            print("  ✗", b)
        return 0 if not bad else 1

    if not os.path.exists(DB):
        print("数据库不存在：%s" % DB)
        return 1

    if not os.path.exists(BACKUP):
        shutil.copy2(DB, BACKUP)
        print("[backup] 升级前快照 → %s" % BACKUP)

    with open(SQL, encoding="utf-8") as f:
        ddl = f.read()

    con = sqlite3.connect(DB)
    try:
        con.executescript(ddl)
        con.execute(
            "INSERT OR REPLACE INTO meta_schema_version(version, applied_at, author, note) "
            "VALUES (?, datetime('now','localtime'), ?, ?)",
            (VERSION, "WorkBuddy(施汶邑)", NOTE))
        con.commit()
        bad = check(con)
        print("[v1.8] 表 %d / 视图 %d / 版本 %s" % (
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0],
            con.execute("SELECT version FROM meta_schema_version ORDER BY rowid DESC LIMIT 1")
            .fetchone()[0]))
        for row in con.execute(
                "SELECT coverage_source, COUNT(*), SUM(n_available) "
                "FROM v_feature_coverage_effective GROUP BY 1"):
            print("  覆盖来源 %s : %d 行 / 可用 %d" % row)
        print("  年度切片 %d 年 / %d 指标" % (
            con.execute("SELECT COUNT(DISTINCT time_slice) FROM v_graph_slice_annual")
            .fetchone()[0],
            con.execute("SELECT COUNT(DISTINCT metric) FROM v_graph_slice_annual")
            .fetchone()[0]))
        if bad:
            print("校验失败：")
            for b in bad:
                print("  ✗", b)
            return 1
        print("✅ V1.8 MIGRATION PASSED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
