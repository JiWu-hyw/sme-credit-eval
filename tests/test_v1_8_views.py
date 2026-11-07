# -*- coding: utf-8 -*-
"""v1.8 口径一致性守卫：有效特征覆盖 + 图谱年度切片 + 物理口径未动。

守的是三件容易「看起来做对了、其实把口径弄坏」的事：
  1. 有人直接回写主宽表 m_funding / investor_prestige（破坏 ASOF_VER 契约）
  2. 有人 UPDATE ads_graph_node.time_slice 做年度切片（图重建脚本会写回 0，
     且既有契约绑定全期累计单切片）
  3. 有人把 12 个真无数据源的特征也「补」出覆盖率，制造虚假可用特征

八项断言，全部是库内确定性 SQL；三处 DDL 逐字符比对防止迁移与 schema.sql 漂移。

用法：python tests/test_v1_8_views.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
MIG = os.path.join(ROOT, "src", "data", "migrations", "v1_8_consistency.sql")
SCHEMA = os.path.join(ROOT, "src", "data", "schema.sql")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_v18.db")
sys.path.insert(0, ROOT)

from src.data.apply_v1_8 import check as v18_check, EXPECT_CUM, EXPECT_SIDE  # noqa: E402

PASS, FAIL = [], []
VIEWS = ("v_feature_coverage_effective", "v_graph_slice_annual",
         "v_graph_slice_annual_edge")


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))


def con():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def one(sql, params=()):
    c = con()
    try:
        return c.execute(sql, params).fetchone()[0]
    finally:
        c.close()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _norm(s):
    """去掉行注释与空白差异，只比 SQL 本体（迁移文件里视图之间有分隔注释）。"""
    s = re.sub(r"--[^\n]*", "", s)
    return re.sub(r"\s+", " ", s).strip().rstrip(";").strip()


# ---------------------------------------------------------------------------
def a_migration_check():
    c = con()
    try:
        bad = v18_check(c)
    finally:
        c.close()
    assert not bad, "apply_v1_8.check() 未通过：%s" % "; ".join(bad)


def b_views_and_version():
    c = con()
    try:
        got = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}
    finally:
        c.close()
    for v in VIEWS:
        assert v in got, "缺视图 %s" % v
    ver = one("SELECT version FROM meta_schema_version "
              "ORDER BY applied_at DESC, rowid DESC LIMIT 1")
    assert ver == "v1.8", "库版本 %s，期望 v1.8" % ver
    assert os.path.exists(BACKUP), "升级前回滚点缺失：%s" % BACKUP


def c_original_coverage_untouched():
    n = one("SELECT COUNT(*) FROM v_asof_feature_coverage "
            "WHERE as_of_policy='cohort' AND n_available=0")
    assert n == 14, "原覆盖视图被改动：cohort 零覆盖 %d（应仍为 14）" % n
    ddl = one("SELECT sql FROM sqlite_master WHERE name='v_asof_feature_coverage'")
    assert "ads_feature_asof_funding" not in ddl, "原覆盖视图被改成引用旁路表"


def d_effective_side_recompute():
    c = con()
    try:
        side = dict(c.execute(
            "SELECT as_of_policy || '|' || feature_name, n_available "
            "FROM v_feature_coverage_effective WHERE requires_left_join=1").fetchall())
        # 独立 SQL 从底表重算，不依赖视图
        ind = dict(c.execute(
            "SELECT as_of_policy || '|m_funding', COUNT(m_funding) "
            "FROM ads_feature_asof_funding GROUP BY 1").fetchall())
        ind.update(dict(c.execute(
            "SELECT as_of_policy || '|investor_prestige', COUNT(investor_prestige_best) "
            "FROM ads_feature_asof_funding GROUP BY 1").fetchall()))
    finally:
        c.close()
    assert side == ind, "旁路覆盖与底表独立重算不符：%r vs %r" % (side, ind)
    assert {tuple(k.split("|")): v for k, v in side.items()} == EXPECT_SIDE, (
        "旁路覆盖与迁移基准不符：%r" % side)


def e_no_fake_features():
    zero = one("SELECT COUNT(*) FROM v_feature_coverage_effective "
               "WHERE as_of_policy='cohort' AND n_available=0")
    assert zero == 12, "零覆盖特征数 %d，期望 12（只收敛旁路两列）" % zero
    bad = one("SELECT COUNT(*) FROM v_feature_coverage_effective "
              "WHERE requires_left_join=0 AND n_available>0 "
              "AND feature_name IN ('m_funding','investor_prestige')")
    assert bad == 0, "旁路两列存在未标来源的覆盖值"
    tagged = one("SELECT COUNT(*) FROM v_feature_coverage_effective "
                 "WHERE requires_left_join=1 AND feature_name NOT IN "
                 "('m_funding','investor_prestige')")
    assert tagged == 0, "requires_left_join 被标到其他特征上"


def f_asof_contract_untouched():
    assert one("SELECT COUNT(DISTINCT feature_name) FROM ads_feature_asof") == 29
    c = con()
    try:
        n_train = len(c.execute("PRAGMA table_info(v_model_train_ready)").fetchall())
        ddl = c.execute("SELECT sql FROM sqlite_master "
                        "WHERE name='v_model_dataset_asof_flat'").fetchone()[0]
        for col in ("m_funding", "investor_prestige"):
            assert c.execute('SELECT COUNT("%s") FROM v_model_dataset_asof_flat'
                             % col).fetchone()[0] == 0, "%s 被原地回填" % col
    finally:
        c.close()
    assert n_train == 17, "训练入口列数 %d（应为 17）" % n_train
    assert "ads_feature_asof_funding" not in ddl, "主宽表视图引用了旁路表"


def g_physical_timeslice_still_zero():
    for t in ("ads_graph_node", "ads_graph_edge"):
        n = one('SELECT COUNT(*) FROM "%s" WHERE time_slice<>0' % t)
        assert n == 0, "%s 物理 time_slice 被改写：%d 行" % (t, n)
    # 年度切片只在视图里有多年份
    n_y = one("SELECT COUNT(DISTINCT time_slice) FROM v_graph_slice_annual")
    assert n_y == 29, "视图年度切片年份数 %d（应为 29）" % n_y


def h_ddl_consistency_and_edge_view():
    c = con()
    try:
        lib = {v: c.execute("SELECT sql FROM sqlite_master WHERE name=?",
                            (v,)).fetchone()[0] for v in VIEWS}
        cols = [r[1] for r in c.execute(
            "PRAGMA table_info(v_graph_slice_annual_edge)")]
        dirty = c.execute(
            "SELECT COUNT(*) FROM (SELECT dst_id FROM v_graph_slice_annual_edge "
            "WHERE edge_type='belongs_ipc' AND time_slice=1992) "
            "WHERE instr(dst_id,'(')>0").fetchone()[0]
    finally:
        c.close()
    for v in VIEWS:
        for path in (MIG, SCHEMA):
            t = read(path)
            m = re.search(r"(CREATE VIEW %s AS.*?)(?=DROP VIEW|-- \[V18CONSISTENCY:END\]|\Z)"
                          % re.escape(v), t, re.S | re.I)
            assert m, "%s 缺 %s 的 CREATE VIEW 定义" % (os.path.basename(path), v)
            assert _norm(m.group(1)) == _norm(lib[v]), (
                "%s 与库内 %s 定义不一致" % (os.path.basename(path), v))
    for col in ("time_slice", "src_type", "src_id", "dst_type", "dst_id",
                "edge_type", "weight"):
        assert col in cols, "边级切片视图缺列 %s" % col
    assert dirty == 0, "IPC 节点 ID 未剥 WIPO 版本号后缀：%d 条" % dirty
    # 末年累计与预置图逐类型对账（belongs_ipc 差 19 条来自 IPC 归一并边，单独说明）
    cum = one("SELECT n_cumulative FROM v_graph_slice_annual "
              "WHERE metric='n_edge_invented_by' "
              "AND time_slice=(SELECT MAX(time_slice) FROM v_graph_slice_annual)")
    assert cum == one("SELECT COUNT(*) FROM ads_graph_edge WHERE edge_type='invented_by'"), (
        "末年 invented_by 累计与预置图不符")
    assert EXPECT_CUM["n_edge_invented_by"] == 77899


def main():
    print("=" * 68)
    print("  v1.8 口径一致性守卫（有效覆盖 / 年度切片 / 物理口径未动）")
    print("=" * 68)
    check("迁移脚本 check() 全通过", a_migration_check)
    check("三视图存在 / v1.8 版本 / 回滚点", b_views_and_version)
    check("原覆盖视图保持不动", c_original_coverage_untouched)
    check("旁路覆盖与底表独立重算对账", d_effective_side_recompute)
    check("12 个真空字段未被补出覆盖率", e_no_fake_features)
    check("as-of 契约（29 特征 / 17 列 / 恒 NULL）未动", f_asof_contract_untouched)
    check("物理 time_slice 仍全 0，年度切片只在视图", g_physical_timeslice_still_zero)
    check("三处 DDL 一致 / 边级切片列与 IPC 归一", h_ddl_consistency_and_edge_view)
    print("-" * 68)
    if FAIL:
        print("❌ V1.8 VIEW TESTS FAILED（%d 过 / %d 败）" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  · %s: %s" % (n, e))
        sys.exit(1)
    print("✅ V1.8 VIEW TESTS PASSED（%d 项）" % len(PASS))


if __name__ == "__main__":
    main()
