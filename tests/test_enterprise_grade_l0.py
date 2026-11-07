# -*- coding: utf-8 -*-
"""v1.7 企业「技术与资质梯队分档」L0 视图守卫。

防的是这类事故：判据被悄悄改动（比如把「风险负面」调低优先级、把
admin_penalty_cnt 塞进来凑覆盖、把未打标的 193 家当负例），视图仍然
查得通、家数也凑得齐，但分档语义已经变了。

关键一条是「独立对账」：用方案 §4.2 的原始 SQL 现场重算一遍，逐企业
比对 grade，而不是拿视图自己的输出验自己。

用法：python tests/test_enterprise_grade_l0.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
SCHEMA = os.path.join(ROOT, "src", "data", "schema.sql")
MIGRATION = os.path.join(ROOT, "src", "data", "migrations", "v1_7_grade.sql")

EXPECT = {"A 引领": 44, "B 优质": 95, "C 稳健": 275, "D 观察": 83, "E 预警": 3}

# 方案 §4.2 原文 SQL（不含 GROUP BY），独立重算用
INDEPENDENT = """
WITH b AS (
  SELECT d.enterprise_id,
         COALESCE(d.serious_dishonesty_cnt,0) sd,
         COALESCE(d.biz_anomaly_cnt,0)       ba,
         d.biz_status,
         COALESCE(d.risk_negative_cnt,0)     rn,
         COALESCE(d.is_little_giant,0)       lg,
         COALESCE(d.qcc_tech_level_ord,0)    tech,
         COALESCE(d.qcc_credit_level_ord,0)  cred,
         COALESCE(l.label_value,-1)          lab
  FROM dim_enterprise d
  LEFT JOIN ads_label l
    ON l.enterprise_id = d.enterprise_id AND l.label_task = 'qual_survival'
)
SELECT enterprise_id,
       CASE
         WHEN sd > 0 OR ba > 0
           OR (biz_status IS NOT NULL AND biz_status <> '开业') THEN 'E 预警'
         WHEN rn > 0 OR lab = 1                                 THEN 'D 观察'
         WHEN lg = 1 OR tech >= 5                               THEN 'A 引领'
         WHEN tech = 4 AND cred >= 11                           THEN 'B 优质'
         ELSE 'C 稳健'
       END AS grade
FROM b
"""

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:                       # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ✗ %s —— %s: %s" % (name, type(e).__name__, e))


def _assert(cond, detail=None):
    assert cond, "实际值：%r" % (detail,)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", "", sql).rstrip(";")


def _create_view(text: str) -> str:
    m = re.search(r"CREATE VIEW v_enterprise_grade_l0 AS.*?;", text, re.S | re.I)
    _assert(m is not None, "未找到 CREATE VIEW 定义")
    return _norm(m.group(0))


def main() -> int:
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    try:
        print("L0 分档视图守卫（v1.7）")

        def a_exists():
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' "
                "AND name='v_enterprise_grade_l0'").fetchone()
            _assert(row, "视图不存在")

        def b_coverage():
            n_ent = con.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
            n = con.execute("SELECT COUNT(*) FROM v_enterprise_grade_l0").fetchone()[0]
            nd = con.execute(
                "SELECT COUNT(DISTINCT enterprise_id) FROM v_enterprise_grade_l0").fetchone()[0]
            _assert(n == n_ent == nd == 500,
                    "视图 %d / 去重 %d / 企业 %d" % (n, nd, n_ent))

        def c_distribution():
            got = dict(con.execute(
                "SELECT grade, COUNT(*) FROM v_enterprise_grade_l0 GROUP BY grade").fetchall())
            _assert(got == EXPECT, "实得 %r ≠ 方案 %r" % (got, EXPECT))
            _assert(sum(got.values()) == 500, "家数未闭合：%d" % sum(got.values()))

        def d_hard_constraint():
            leak = con.execute(
                "SELECT COUNT(*) FROM v_enterprise_grade_l0 WHERE grade <> 'E 预警' "
                "AND (serious_dishonesty_cnt > 0 OR biz_anomaly_cnt > 0 "
                "     OR (biz_status IS NOT NULL AND biz_status <> '开业'))").fetchone()[0]
            _assert(leak == 0, "E 档硬约束被降级 %d 家" % leak)

        def e_priority():
            # 命中 D 判据且未命中 E 硬约束的，必须落在 D（D 优先于 A/B）
            wrong = con.execute(
                "SELECT COUNT(*) FROM v_enterprise_grade_l0 g "
                "WHERE (g.risk_negative_cnt > 0 OR g.label_value_qual_survival = 1) "
                "AND NOT (g.serious_dishonesty_cnt > 0 OR g.biz_anomaly_cnt > 0 "
                "         OR (g.biz_status IS NOT NULL AND g.biz_status <> '开业')) "
                "AND g.grade <> 'D 观察'").fetchone()[0]
            _assert(wrong == 0, "D 档优先于 A/B 的规则被破坏：%d 家" % wrong)

        def f_unlabeled():
            # -1 = 未打标 193 家，不得被当成「资质断档信号」
            mis = con.execute(
                "SELECT COUNT(*) FROM v_enterprise_grade_l0 "
                "WHERE label_value_qual_survival = -1 "
                "AND hit_rule = 'D:资质断档信号'").fetchone()[0]
            _assert(mis == 0, "lab=-1 被当作断档信号：%d 家" % mis)
            n = con.execute(
                "SELECT COUNT(*) FROM v_enterprise_grade_l0 "
                "WHERE label_value_qual_survival = -1").fetchone()[0]
            _assert(n == 193, "未打标企业数应为 193，实得 %d" % n)

        def g_excluded_field():
            cols = [r[1] for r in con.execute(
                "PRAGMA table_info(v_enterprise_grade_l0)")]
            _assert("admin_penalty_cnt" not in cols,
                    "语义未核实字段 admin_penalty_cnt 被写入视图")
            ddl = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='v_enterprise_grade_l0'").fetchone()[0]
            _assert("admin_penalty_cnt" not in ddl, "判据 SQL 中出现 admin_penalty_cnt")

        def h_meta_columns():
            bad = con.execute(
                "SELECT COUNT(*) FROM v_enterprise_grade_l0 "
                "WHERE grade_code <> substr(grade,1,1) "
                "   OR hit_rule IS NULL OR hit_rule = '' "
                "   OR substr(hit_rule,1,1) <> substr(grade,1,1)").fetchone()[0]
            _assert(bad == 0, "档位码 / 命中判据与档位不一致：%d 家" % bad)
            ver = con.execute(
                "SELECT DISTINCT grade_ver FROM v_enterprise_grade_l0").fetchall()
            _assert(ver == [("l0_v1.0",)], "规则版本异常：%r" % (ver,))
            notes = con.execute(
                "SELECT COUNT(*) FROM v_enterprise_grade_l0 "
                "WHERE scope_note NOT LIKE '%as-of%'").fetchone()[0]
            _assert(notes == 0, "使用边界声明缺失：%d 家" % notes)

        def i_independent_recompute():
            """用方案原文 SQL 现场重算，逐企业对账（防止自证）。"""
            want = dict(con.execute(INDEPENDENT).fetchall())
            got = dict(con.execute(
                "SELECT enterprise_id, grade FROM v_enterprise_grade_l0").fetchall())
            _assert(set(want) == set(got), "企业集合不一致")
            diff = [k for k in want if want[k] != got.get(k)]
            _assert(not diff, "档位与独立重算不一致 %d 家，例如 %r"
                    % (len(diff), [(k, want[k], got.get(k)) for k in diff[:3]]))

        def j_ddl_consistency():
            """库内视图、schema.sql、迁移 SQL 三处定义必须一致。"""
            live = _norm(con.execute(
                "SELECT sql FROM sqlite_master WHERE name='v_enterprise_grade_l0'"
            ).fetchone()[0])
            with open(SCHEMA, encoding="utf-8") as f:
                sch = _create_view(f.read())
            with open(MIGRATION, encoding="utf-8") as f:
                mig = _create_view(f.read())
            _assert(live == sch, "库内视图定义与 schema.sql 不一致")
            _assert(sch == mig, "schema.sql 与 migrations/v1_7_grade.sql 不一致")

        def k_version():
            v = con.execute(
                "SELECT version FROM meta_schema_version ORDER BY rowid DESC LIMIT 1"
            ).fetchone()[0]
            _assert(v == "v1.7", "库内最新版本为 %s" % v)
            views = con.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
            _assert(views == 20, "视图数应为 20，实得 %d" % views)

        for name, fn in [
            ("视图存在", a_exists),
            ("500 家企业一行一家", b_coverage),
            ("五档家数与方案一致且闭合", c_distribution),
            ("E 档硬约束一票定档", d_hard_constraint),
            ("D 档优先于 A/B", e_priority),
            ("lab=-1 未打标企业不被误判", f_unlabeled),
            ("admin_penalty_cnt 未入档", g_excluded_field),
            ("档位码 / 命中判据 / 版本 / 边界声明齐备", h_meta_columns),
            ("与方案原文 SQL 独立重算逐企业对账", i_independent_recompute),
            ("库内 / schema.sql / 迁移 SQL 三处 DDL 一致", j_ddl_consistency),
            ("schema 版本与视图数正确", k_version),
        ]:
            check(name, fn)
    finally:
        con.close()

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ ENTERPRISE GRADE L0 TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
