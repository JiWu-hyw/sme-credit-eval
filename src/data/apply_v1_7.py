# -*- coding: utf-8 -*-
"""把 schema 从 v1.6 升到 v1.7：落 `v_enterprise_grade_l0` 视图 + 登记版本。

幂等：重复执行只会 DROP/CREATE 同一个视图并 REPLACE 同一行版本记录。
执行前自动留一份升级前快照 data/db/zjtech_credit_pre_v17.db 作回滚点。

用法：
    python -m src.data.apply_v1_7            # 执行迁移
    python -m src.data.apply_v1_7 --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_v17.db")
SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "migrations", "v1_7_grade.sql")

VERSION = "v1.7"
NOTE = (
    "v1.7 企业技术与资质梯队分档 L0 规则基线（方案 docs/论文/企业分档多标签体系方案-v1.0.md §6 P0）："
    "① 新增视图 v_enterprise_grade_l0，字典序分层 E 预警 → D 观察 → A 引领 → B 优质 → C 稳健，"
    "输出 grade / grade_code / hit_rule 与 8 个判据中间量，实测 44/95/275/83/3 家合计 500；"
    "admin_penalty_cnt 语义与字段名不符，明确排除；lab=-1 的 193 家未打标企业不参与 D 档判定。"
    "② 视图带 grade_ver=l0_v1.0 与 scope_note，声明其为静态快照对照基线、非 as-of 训练口径、"
    "禁止用 qcc_tech_level_ord / qcc_credit_level_ord 反向验证（循环定义）。"
    "③ configs/field_gap_registry.json 新增 label_tasks 段，把 msme_default / sba_default / "
    "survival_risk 三个空标签任务登记为「源未接入」，并固化 as-of 口径约定。"
    "④ 守卫测试 tests/test_enterprise_grade_l0.py + 登记册守卫新增标签任务项。"
    "本次只增视图与登记，不改任何事实表数据；v1.3.0 Release 附件仍为 v1.6 快照。"
)

# 方案 §4.3 实测家数；迁移后必须逐档对上，防止判据被无意改动
EXPECT = {"A 引领": 44, "B 优质": 95, "C 稳健": 275, "D 观察": 83, "E 预警": 3}


def check(con: sqlite3.Connection) -> list[str]:
    """确定性校验：视图存在、企业全覆盖、家数闭合、判据优先级、版本登记。"""
    bad: list[str] = []
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_enterprise_grade_l0'"
    ).fetchone()
    if not exists:
        return ["v_enterprise_grade_l0 视图不存在"]

    cols = [r[1] for r in con.execute("PRAGMA table_info(v_enterprise_grade_l0)")]
    if "admin_penalty_cnt" in cols:
        bad.append("admin_penalty_cnt 被写进视图判据（语义未核实字段禁止入档）")
    for col in ("grade", "grade_code", "hit_rule", "grade_ver", "scope_note",
                "label_value_qual_survival"):
        if col not in cols:
            bad.append("视图缺列 %s" % col)

    n_ent = con.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    n_view = con.execute("SELECT COUNT(*) FROM v_enterprise_grade_l0").fetchone()[0]
    n_distinct = con.execute(
        "SELECT COUNT(DISTINCT enterprise_id) FROM v_enterprise_grade_l0").fetchone()[0]
    if n_view != n_ent or n_distinct != n_ent:
        bad.append("视图未对 500 家企业一行一家：视图 %d / 去重 %d / 企业 %d"
                   % (n_view, n_distinct, n_ent))

    got = dict(con.execute(
        "SELECT grade, COUNT(*) FROM v_enterprise_grade_l0 GROUP BY grade").fetchall())
    if got != EXPECT:
        bad.append("五档家数与方案不符：实得 %r，期望 %r" % (got, EXPECT))
    if sum(got.values()) != n_ent:
        bad.append("五档家数未闭合：%d ≠ 企业数 %d" % (sum(got.values()), n_ent))

    # 硬约束优先：命中 E 判据的企业不得落在 A/B/C/D
    leak = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_grade_l0 WHERE grade <> 'E 预警' "
        "AND (serious_dishonesty_cnt > 0 OR biz_anomaly_cnt > 0 "
        "     OR (biz_status IS NOT NULL AND biz_status <> '开业'))").fetchone()[0]
    if leak:
        bad.append("E 档硬约束被降级：%d 家" % leak)
    # 未打标企业不得被当成续存样本推入 D 档（只能靠风险负面命中）
    mis = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_grade_l0 "
        "WHERE label_value_qual_survival = -1 AND hit_rule = 'D:资质断档信号'").fetchone()[0]
    if mis:
        bad.append("lab=-1 被当作资质断档信号：%d 家" % mis)

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
        print("[v1.7] 表 %d / 视图 %d / 版本 %s" % (
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0],
            con.execute("SELECT version FROM meta_schema_version ORDER BY rowid DESC LIMIT 1")
            .fetchone()[0]))
        for row in con.execute(
                "SELECT grade, COUNT(*) FROM v_enterprise_grade_l0 GROUP BY grade ORDER BY grade"):
            print("  %s : %d" % row)
        if bad:
            print("校验失败：")
            for b in bad:
                print("  ✗", b)
            return 1
        print("✅ V1.7 MIGRATION PASSED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
