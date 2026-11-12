# -*- coding: utf-8 -*-
"""v1.9 标签方向纠正 + 多标签测评（P1）守卫。

验证重点：
  ① 方向：正例=好企业=资质存续（286），负例=坏企业=资质断档（21）；
     ads_label 底表数值未被原地 UPDATE，正确方向由并列视图给出；
  ② 多标签：6 个二值锚点方向统一 1=好、NULL 不计入票数、阈值可审计；
  ③ 测评：五档闭合、E 档硬约束、与 L0 对照、循环定义警示；
  ④ 证据链：迁移脚本 --check、schema.sql DDL 同步、登记册 v3、测评报告。

用法：python tests/test_multilabel_eval.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
REG = os.path.join(ROOT, "configs", "field_gap_registry.json")
MIG = os.path.join(ROOT, "src", "data", "migrations", "v1_9_multilabel.sql")
SCHEMA = os.path.join(ROOT, "src", "data", "schema.sql")
REPORT = os.path.join(ROOT, "deliverables", "01_算法端", "multilabel_eval_report.md")

VIEWS = ("v_label_direction", "v_model_train_ready_credit",
         "v_label_mechanism_credit", "v_enterprise_label_matrix",
         "v_enterprise_multilabel_grade", "v_multilabel_eval_summary")
BINARY = ("y_hard_outcome", "y_risk_free", "y_survival",
          "y_qual_ladder_good", "y_qcc_credit_good", "y_qcc_tech_good")

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, repr(e)))
        print("  ✗ %s —— %r" % (name, e))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def con_ro():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def main():
    print("=" * 68)
    print("  v1.9 标签方向纠正 + 多标签测评守卫（P1）")
    print("=" * 68)
    c = con_ro()
    one = lambda s: c.execute(s).fetchone()[0]

    def a_views():
        got = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        miss = [v for v in VIEWS if v not in got]
        assert not miss, "缺视图 %r" % miss
        ver = one("SELECT version FROM meta_schema_version "
                  "ORDER BY applied_at DESC, rowid DESC LIMIT 1")
        # v2.0 A 轴 / v2.1 产品节点并列层均不改 6 个锚点 / 五档 / 标签矩阵。
        assert ver in ("v1.9", "v2.0", "v2.1"), (
            "库版本 %s，期望 v1.9 或更高" % ver)

    def b_base_untouched():
        d = dict(c.execute("SELECT label_value, COUNT(*) FROM ads_label GROUP BY 1"))
        assert d == {0: 286, 1: 21}, "ads_label 被原地 UPDATE：%r" % d
        d2 = dict(c.execute("SELECT label, COUNT(*) FROM v_model_train_ready GROUP BY 1"))
        assert d2 == {0: 286, 1: 21}, "旧训练入口被改动：%r" % d2
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()

    def c_direction_mapping():
        n = one("SELECT COUNT(*) FROM v_label_direction")
        assert n == 307, "方向对照行数 %d，期望 307" % n
        bad = one("SELECT COUNT(*) FROM v_label_direction "
                  "WHERE credit_label <> 1 - risk_label")
        assert bad == 0, "方向映射不自洽：%d 行" % bad
        d = dict(c.execute("SELECT credit_label, COUNT(*) "
                           "FROM v_label_direction GROUP BY 1"))
        assert d == {1: 286, 0: 21}, "信用方向分布 %r，期望 286 正例 / 21 负例" % d
        cls = dict(c.execute("SELECT credit_class, COUNT(*) "
                             "FROM v_label_direction GROUP BY 1"))
        assert cls == {"positive": 286, "negative": 21}, "类名标注错：%r" % cls
        row = c.execute("SELECT positive_definition, negative_definition "
                        "FROM v_label_direction LIMIT 1").fetchone()
        assert "好企业" in row[0] and "坏企业" in row[1], "正负例定义文案缺失"

    def d_train_ready_credit():
        cols = [r[1] for r in c.execute("PRAGMA table_info(v_model_train_ready_credit)")]
        assert len(cols) == 19 and "label_direction" in cols and "positive_class" in cols, \
            "训练入口列结构不符：%r" % cols
        d = dict(c.execute("SELECT label, COUNT(*) "
                           "FROM v_model_train_ready_credit GROUP BY 1"))
        assert d == {1: 286, 0: 21}, "训练入口方向未翻转：%r" % d
        bad = one("SELECT COUNT(*) FROM v_model_train_ready a "
                  "JOIN v_model_train_ready_credit b USING (enterprise_id) "
                  "WHERE a.label + b.label <> 1")
        assert bad == 0, "新旧入口 label 不互补：%d 行" % bad
        # 特征列必须逐列相同，翻转不能顺带改特征
        pairs = c.execute(
            "SELECT COUNT(*) FROM v_model_train_ready a "
            "JOIN v_model_train_ready_credit b USING (enterprise_id) "
            "WHERE a.n_patent_app IS NOT b.n_patent_app "
            "   OR a.grant_rate IS NOT b.grant_rate").fetchone()[0]
        assert pairs == 0, "翻转时特征列被改动：%d 行" % pairs

    def e_mechanism_credit():
        p, n = c.execute("SELECT SUM(n_positive), SUM(n_negative) "
                         "FROM v_label_mechanism_credit").fetchone()
        assert (p, n) == (286, 21), "机制台账合计 %r" % ((p, n),)
        # 与旧台账逐组互补
        bad = one(
            "SELECT COUNT(*) FROM v_label_mechanism o "
            "JOIN v_label_mechanism_credit b "
            "  ON b.n_events=o.n_events AND b.cohort_year=o.cohort_year "
            "WHERE o.n_positive <> b.n_negative OR o.n_negative <> b.n_positive")
        assert bad == 0, "新旧台账不互补：%d 组" % bad

    def f_matrix_shape():
        assert one("SELECT COUNT(*) FROM v_enterprise_label_matrix") == 500
        for col in BINARY:
            bad = one('SELECT COUNT(*) FROM v_enterprise_label_matrix '
                      'WHERE "%s" IS NOT NULL AND "%s" NOT IN (0,1)' % (col, col))
            assert bad == 0, "%s 出现非二值取值：%d 行" % (col, bad)

    def g_null_semantics():
        n_surv = one("SELECT COUNT(*) FROM v_enterprise_label_matrix "
                     "WHERE y_survival IS NULL")
        n_hard = one("SELECT COUNT(*) FROM v_enterprise_label_matrix "
                     "WHERE y_hard_outcome IS NULL")
        assert (n_surv, n_hard) == (193, 37), \
            "NULL 语义被破坏：未打标 %d / 硬结果缺失 %d" % (n_surv, n_hard)
        bad = one("SELECT COUNT(*) FROM v_enterprise_label_matrix "
                  "WHERE n_good + n_bad <> n_labels_observed "
                  "   OR n_labels_observed NOT BETWEEN 4 AND 6 "
                  "   OR label_agreement NOT BETWEEN 0 AND 1")
        assert bad == 0, "票数 / 一致度不自洽：%d 行" % bad
        # 未打标企业不得被当成坏企业计票
        null_all_good = one("SELECT COUNT(*) FROM v_enterprise_label_matrix "
                            "WHERE y_survival IS NULL AND n_labels_observed = 5")
        assert null_all_good > 0, "未打标企业未被正确扣减有效锚点数"

    def h_thresholds():
        d = dict(c.execute(
            "SELECT 'ladder', COUNT(*) FROM v_enterprise_label_matrix WHERE y_qual_ladder_good=1 "
            "UNION ALL SELECT 'credit', COUNT(*) FROM v_enterprise_label_matrix WHERE y_qcc_credit_good=1 "
            "UNION ALL SELECT 'tech', COUNT(*) FROM v_enterprise_label_matrix WHERE y_qcc_tech_good=1"))
        assert d == {"ladder": 45, "credit": 382, "tech": 157}, \
            "阈值口径与实测分布不符：%r" % d
        # 阈值写死在 DDL 里，可审计
        sql = read(MIG)
        for kw in ("y_qual_ladder >= 3", "y_qcc_credit >= 11", "y_qcc_tech >= 4"):
            assert kw in sql, "DDL 缺阈值 %s" % kw

    def i_grades():
        assert one("SELECT COUNT(*) FROM v_enterprise_multilabel_grade") == 500
        d = dict(c.execute("SELECT grade_multilabel, COUNT(*) "
                           "FROM v_enterprise_multilabel_grade GROUP BY 1"))
        assert d == {"A 引领": 81, "B 优质": 176, "C 稳健": 157,
                     "D 观察": 83, "E 预警": 3}, "五档家数不符：%r" % d
        same = one("SELECT SUM(is_consistent_with_l0) "
                   "FROM v_enterprise_multilabel_grade")
        assert same == 294, "与 L0 同档家数 %d，期望 294" % same
        assert one("SELECT COUNT(*) FROM v_enterprise_multilabel_grade "
                   "WHERE grade_l0 IS NULL") == 0, "L0 对照缺失"

    def j_hard_constraint():
        bad = one("SELECT COUNT(*) FROM v_enterprise_multilabel_grade g "
                  "JOIN v_enterprise_label_matrix m USING (enterprise_id) "
                  "WHERE m.y_hard_outcome = 0 AND g.grade_multilabel <> 'E 预警'")
        assert bad == 0, "硬结果坏企业未落 E 档：%d 家" % bad
        bad2 = one("SELECT COUNT(*) FROM v_enterprise_multilabel_grade "
                   "WHERE grade_code <> substr(grade_multilabel,1,1)")
        assert bad2 == 0, "grade_code 与档次不一致：%d 行" % bad2

    def k_eval_summary():
        assert one("SELECT COUNT(*) FROM v_multilabel_eval_summary") >= 15
        # 覆盖率行：未打标企业不得计入正例分母
        row = c.execute("SELECT n_observed, n_positive FROM v_multilabel_eval_summary "
                        "WHERE metric='标签锚点覆盖率'").fetchone()
        assert tuple(row) == (500, 307), "锚点覆盖率口径错：%r" % (tuple(row),)
        sql = read(MIG)
        assert "循环定义" in sql, "DDL 未写明循环定义边界"

    def l_migration_check():
        r = subprocess.run([sys.executable, "-m", "src.data.apply_v1_9", "--check"],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 0, "迁移 --check 失败：%s" % r.stdout[-400:]
        assert "通过" in r.stdout, "--check 未给出通过结论"

    def m_ddl_synced():
        mig = read(MIG)
        i, j = mig.find("-- [V19MULTILABEL:BEGIN]"), mig.find("-- [V19MULTILABEL:END]")
        assert i >= 0 and j >= 0, "迁移 SQL 缺标记"
        block = mig[i:j + len("-- [V19MULTILABEL:END]")]
        assert block in read(SCHEMA), "schema.sql 未同步 v1.9 DDL 块"

    def n_registry():
        r = json.loads(read(REG))
        # v3 = 方向纠正定稿；v4 = G-GRAPH-01 改判 partial_closed 并加 v2.1 policy。
        assert r["meta"]["version"] in ("v3", "v4", "v5"), "登记册未升 v3/v4/v5"
        if r["meta"]["version"] in ("v4", "v5"):
            assert "v2_1_product_node" in r["policies"], "v4 缺 v2.1 并列层策略"
        t = r["label_tasks"]["tasks"][0]
        assert (t["positive"], t["negative"]) == (286, 21), \
            "登记册方向未纠正：%r" % ((t["positive"], t["negative"]),)
        assert len(r["label_tasks"]["multilabel_axes"]["axes"]) == 6, "多标签轴未登记 6 个"
        assert "label_direction" in r["policies"], "缺方向策略"

    def o_report():
        t = read(REPORT)
        for kw in ("正例（好企业）", "负例（坏企业）", "286", "21",
                   "循环定义", "58.8%", "v_model_train_ready_credit"):
            assert kw in t, "测评报告缺关键结论：%s" % kw

    def p_notice_injected():
        t = read(os.path.join(ROOT, "deliverables", "00_先看这个.md"))
        assert "DIRECTION_NOTICE:v1" in t, "主交付文档未注入方向声明"
        assert "正例 = 好企业" in t and "负例 = 坏企业" in t, "方向声明内容不全"

    for name, fn in [
        ("六个视图存在 / 版本 v1.9", a_views),
        ("底表 ads_label 未被原地 UPDATE", b_base_untouched),
        ("方向映射 286 正例 / 21 负例", c_direction_mapping),
        ("训练入口 label 与旧视图互补", d_train_ready_credit),
        ("机制台账方向互补", e_mechanism_credit),
        ("多标签矩阵 500 行 / 二值锚点合法", f_matrix_shape),
        ("NULL 语义与票数自洽", g_null_semantics),
        ("分箱阈值与实测分布对账", h_thresholds),
        ("五档闭合 / L0 一致 294 家", i_grades),
        ("E 档硬约束优先级", j_hard_constraint),
        ("交叉测评汇总口径", k_eval_summary),
        ("迁移脚本 --check 通过", l_migration_check),
        ("schema.sql 与迁移 DDL 一致", m_ddl_synced),
        ("缺口登记册 v3/v4/v5 方向正确", n_registry),
        ("测评报告含纠偏与循环定义警示", o_report),
        ("主文档注入方向声明", p_notice_injected),
    ]:
        check(name, fn)

    c.close()
    print("=" * 68)
    if FAIL:
        print("通过 %d，失败 %d 项：" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  - %s：%s" % (n, e))
        return 1
    print("全部通过：%d 项" % len(PASS))
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
