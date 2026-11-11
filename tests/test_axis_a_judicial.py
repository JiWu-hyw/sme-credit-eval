"""v2.0 A 轴司法违约 + 主要人员接入守卫（12 项，只读）。

守的是四件事：
  1. 结构：37 表 / 33 视图、v2.0 版本登记、integrity / 外键；
  2. 清洗：490 行表头脏行剔除、三类金额分列、'-' 日期清洗、别名归一；
  3. 口径隔离：ads_label / 五档 / DEFINITION_VER / dim_enterprise 逐项未动，
     复合事件只是并列候选；
  4. 同源：报告、数据字典、交付散文件与库现场回算一致。

运行：python tests/test_axis_a_judicial.py
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs"
D_ALG = ROOT / "deliverables" / "01_算法端"
D_DATA = ROOT / "deliverables" / "04_数据字典与数据集"
DB = ROOT / "data" / "db" / "zjtech_credit.db"
# 企查查原始回传明细不在仓库内（含企业/人员明细，公开分发范围待拍板）。
# 干净检出时这两项「原始文件对照」显式 SKIP 并打印，库内断言照常全跑，
# 既不误报失败、也不静默算通过；本地有回传包时仍是完整的现场复核。
COLLECT = ROOT.parent / "_data_collect" / "out"
RAW_JUDICIAL = COLLECT / "A轴_司法违约明细.csv"
RAW_PERSON = COLLECT / "高管_主要人员明细.csv"

REPORT_MD = OUT / "axis_a_judicial_report.md"
REPORT_JSON = OUT / "axis_a_judicial_report.json"
DELIVERED = [
    (D_ALG / "axis_a_judicial_report.md", REPORT_MD),
    (D_ALG / "axis_a_judicial_report.json", REPORT_JSON),
    (D_DATA / "数据字典.md", OUT / "data_dictionary.md"),
    (D_DATA / "数据字典.csv", OUT / "data_dictionary.csv"),
]

FAIL: list[tuple[str, str]] = []


def check(name, fn):
    try:
        fn()
        print("  ✓ " + name)
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, str(e)))
        print("  ✗ %s -> %s" % (name, e))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def con() -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True)


def a_structure():
    c = con()
    n_tab = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                      "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
    n_view = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
    assert (n_tab, n_view) == (37, 33), "结构漂移：%d 表 / %d 视图" % (n_tab, n_view)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not c.execute("PRAGMA foreign_key_check").fetchall(), "存在外键冲突"
    assert c.execute("SELECT version FROM meta_schema_version "
                     "ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "v2.0"
    assert c.execute("SELECT COUNT(*) FROM meta_data_source WHERE "
                     "source_id='qcc_judicial_person'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM meta_ingest_log WHERE "
                     "log_id IN (9001,9002)").fetchone()[0] == 2
    c.close()


def b_new_tables():
    c = con()
    for t, n in (("ods_qcc_judicial", 34), ("ods_qcc_person", 2079),
                 ("ods_qcc_axis_a_snapshot", 500)):
        got = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        assert got == n, "%s 应为 %d 行，实得 %d" % (t, n, got)
    c.close()


def c_judicial_cleaning():
    c = con()
    by = dict(c.execute("SELECT event_type, COUNT(*) FROM ods_qcc_judicial GROUP BY 1"))
    assert by == {"失信被执行人": 1, "被执行人": 1, "终本案件": 32}, "类别分布漂移 %r" % by
    kinds = dict(c.execute("SELECT amount_kind, COUNT(*) FROM ods_qcc_judicial GROUP BY 1"))
    assert set(kinds) == {"涉案金额", "执行标的", "未履行金额"}, "金额口径列缺失 %r" % kinds
    assert c.execute("SELECT COUNT(*) FROM ods_qcc_judicial "
                     "WHERE amount_kind IS NULL OR enterprise_id IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM ods_qcc_judicial "
                     "WHERE event_date='-' OR event_date='NAN'").fetchone()[0] == 0
    if RAW_JUDICIAL.exists():
        n_dash = sum(1 for r in csv.DictReader(
            RAW_JUDICIAL.open(encoding="utf-8-sig", newline=""))
            if r["日期"].strip() in ("-", "", "NAN"))
        assert n_dash == 1, "原始 '-' 日期应为 1 条"
    else:
        print("      SKIP 原始回传明细不在仓库内（日期脏值对照略过，库内断言照常）")
    assert c.execute("SELECT COUNT(*) FROM ods_qcc_judicial WHERE "
                     "company_name LIKE '%九州未来%'").fetchone()[0] > 0, (
        "别名归一映射的原文企业名未保留")
    c.close()


def d_person_cleaning():
    c = con()
    assert c.execute("SELECT COUNT(*) FROM ods_qcc_person WHERE "
                     "person_name IN ('姓名','序号') OR position='职务'").fetchone()[0] == 0
    clean = c.execute("SELECT COUNT(*) FROM ods_qcc_person").fetchone()[0]
    if RAW_PERSON.exists():
        raw = list(csv.DictReader(RAW_PERSON.open(
            encoding="utf-8-sig", newline="")))
        assert len(raw) - clean == 490, "表头脏行数不是 490"
    else:
        print("      SKIP 原始回传明细不在仓库内（表头脏行对照略过，库内断言照常）")
    assert c.execute("SELECT COUNT(DISTINCT enterprise_id) FROM ods_qcc_person").fetchone()[0] == 490
    assert c.execute("SELECT COUNT(*) FROM v_enterprise_person_summary "
                     "WHERE n_person_clean=0").fetchone()[0] == 10
    assert c.execute("SELECT COUNT(*) FROM v_enterprise_person_summary "
                     "WHERE n_person_clean>0 AND n_header_rows_dropped<>1").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM ods_qcc_person p "
                     "LEFT JOIN dim_enterprise d USING(enterprise_id) "
                     "WHERE d.enterprise_id IS NULL").fetchone()[0] == 0
    c.close()


def e_snapshot_raw():
    c = con()
    snap = c.execute("SELECT SUM(n_person_raw), SUM(n_dishonest_raw), "
                     "SUM(n_executed_raw), SUM(n_terminal_raw) "
                     "FROM ods_qcc_axis_a_snapshot").fetchone()
    clean = c.execute("SELECT COUNT(*) FROM ods_qcc_person").fetchone()[0]
    assert snap[0] - clean == 490, "快照原始人数与清洗人数之差应为 490"
    assert (snap[1], snap[2], snap[3]) == (1, 1, 32), "快照事件条数与明细不符"
    assert c.execute("SELECT COUNT(*) FROM ods_qcc_axis_a_snapshot "
                     "WHERE has_event_raw='是'").fetchone()[0] == 6
    c.close()


def f_views():
    c = con()
    for v, n in (("v_axis_a_judicial", 500), ("v_enterprise_person_summary", 500),
                 ("v_axis_a_compound_event", 500), ("v_axis_a_grade_cross", 5)):
        got = c.execute('SELECT COUNT(*) FROM "%s"' % v).fetchone()[0]
        assert got == n, "%s 应为 %d 行，实得 %d" % (v, n, got)
    # 三类金额分列，终本类是唯一「未履行金额」口径
    row = c.execute("SELECT SUM(amount_dishonest_yuan), SUM(amount_executed_yuan), "
                    "SUM(amount_unperformed_yuan) FROM v_axis_a_judicial").fetchone()
    assert abs(row[0] - 617364.0) < 1 and row[1] == 0, "失信/被执行金额漂移"
    assert row[2] > 99000000, "终本未履行金额量级异常"
    assert "禁止" in c.execute(
        "SELECT amount_note FROM v_axis_a_judicial LIMIT 1").fetchone()[0]
    c.close()


def g_compound_event():
    c = con()
    d = dict(c.execute("SELECT CASE WHEN y_compound_v2=0 THEN 'adverse' "
                       "WHEN y_compound_v2=1 THEN 'good' ELSE 'unknown' END, COUNT(*) "
                       "FROM v_axis_a_compound_event GROUP BY 1"))
    assert d == {"adverse": 6, "good": 458, "unknown": 36}, "复合事件分布漂移 %r" % d
    assert c.execute("SELECT SUM(n_new_e_candidate) FROM v_axis_a_grade_cross").fetchone()[0] == 3
    rows = c.execute("SELECT g.grade_multilabel FROM v_enterprise_multilabel_grade g "
                     "JOIN v_axis_a_compound_event c USING(enterprise_id) "
                     "WHERE c.y_compound_v2=0 AND g.grade_multilabel NOT LIKE 'E%'").fetchall()
    assert sorted(r[0][0] for r in rows) == ["B", "C", "C"], "新增 E 候选档位漂移"
    c.close()


def h_cross_not_monotonic():
    c = con()
    rows = c.execute("SELECT grade_v1, n_compound_adverse, adverse_rate "
                     "FROM v_axis_a_grade_cross ORDER BY grade_v1").fetchall()
    assert len(rows) == 5
    rates = [r[2] for r in rows]
    assert not all(rates[i] <= rates[i + 1] for i in range(4)), (
        "事件率意外单调，报告里的「不单调」结论需同步")
    d = dict((r[0], r[1]) for r in rows)
    assert d["A 引领"] == 0 and d["D 观察"] == 0 and d["E 预警"] == 3, (
        "五档事件分布漂移 %r" % d)
    assert sum(d.values()) == 6, "总复合事件数应为 6"
    c.close()


def i_published_untouched():
    c = con()
    assert tuple(c.execute("SELECT COUNT(*),SUM(label_value=1),SUM(label_value=0) "
                           "FROM ads_label").fetchone()) == (307, 21, 286)
    grades = dict(c.execute("SELECT grade_multilabel,COUNT(*) "
                            "FROM v_enterprise_multilabel_grade GROUP BY 1"))
    assert grades == {"A 引领": 81, "B 优质": 176, "C 稳健": 157,
                      "D 观察": 83, "E 预警": 3}, "已发布五档被改 %r" % grades
    assert c.execute("SELECT COUNT(*) FROM v_enterprise_label_matrix "
                     "WHERE y_hard_outcome=0").fetchone()[0] == 3
    cols = [r[1] for r in c.execute("PRAGMA table_info(dim_enterprise)")]
    assert "n_dishonest" not in cols and "n_person_clean" not in cols, (
        "dim_enterprise 被加列，违反「不改主表」纪律")
    from src.data.build_qual_dataset import DEFINITION_VER
    assert DEFINITION_VER == "qual_survival_proxy_v0.1"
    c.close()


def j_report_matches_db():
    p = json.loads(read(REPORT_JSON))
    c = con()
    assert p["schema_version"] == "v2.0"
    assert p["structure"]["tables"] == 37 and p["structure"]["views"] == 33
    assert p["judicial"]["rows"] == c.execute(
        "SELECT COUNT(*) FROM ods_qcc_judicial").fetchone()[0]
    assert p["judicial"]["firms"] == c.execute(
        "SELECT COUNT(DISTINCT enterprise_id) FROM ods_qcc_judicial").fetchone()[0]
    assert p["person"]["rows_clean"] == c.execute(
        "SELECT COUNT(*) FROM ods_qcc_person").fetchone()[0]
    assert p["compound_event"]["adverse"] == c.execute(
        "SELECT COUNT(*) FROM v_axis_a_compound_event WHERE y_compound_v2=0").fetchone()[0]
    assert p["compound_event"]["monotonic_a_to_e"] is False
    assert len(p["prohibitions"]) >= 6
    c.close()
    t = read(REPORT_MD)
    for kw in ("34 条 / 6 户", "2079", "490 行", "9,965.47", "复合负面事件",
               "A→E 事件率单调递增", "口径隔离", "未改", "禁止清单"):
        assert kw in t, "报告缺关键表述 %r" % kw


def k_data_dictionary():
    rows = list(csv.DictReader((OUT / "data_dictionary.csv").open(
        encoding="utf-8-sig", newline="")))
    assert len(rows) == 997, "数据字典字段数应为 997，实得 %d" % len(rows)
    for name in ("ods_qcc_judicial", "ods_qcc_person", "ods_qcc_axis_a_snapshot",
                 "v_axis_a_judicial", "v_enterprise_person_summary",
                 "v_axis_a_compound_event", "v_axis_a_grade_cross"):
        g = [r for r in rows if r["对象名"] == name]
        assert g, "字典缺对象 %s" % name
        assert all(r["字段说明"].strip() for r in g), "%s 有字段说明为空" % name
        assert all(r["对象说明"].strip() for r in g), "%s 对象说明为空" % name
    assert not [r for r in rows if not r["字段说明"].strip()], "字典存在无说明字段"


def l_delivery_same_source_and_repro():
    for dst, src in DELIVERED:
        assert dst.exists(), "交付目录缺 %s" % dst.name
        assert md5(dst) == md5(src), "交付份与源份不同源：%s" % dst.name
    for path, kws in (
        (ROOT / "src" / "data" / "export_delivery.py",
         ("axis_a_judicial_report.md", "298 项", "26 个测试文件")),
        (ROOT / "src" / "data" / "apply_v2_0_axis_a.py",
         ("ods_qcc_judicial", "ods_qcc_person", "--check")),
        (ROOT / "src" / "data" / "export_axis_a_judicial_report.py",
         ("def gather", "def build_md")),
    ):
        t = read(path)
        for k in kws:
            assert k in t, "%s 缺 %r" % (path.name, k)
    n = len(list((ROOT / "tests").glob("test_*.py")))
    assert n == 26, "tests/ 下应为 26 个守卫文件，实得 %d" % n
    for p in (ROOT / "README.md", D_ALG.parent / "00_先看这个.md",
              ROOT / "deliverables" / "03_交付说明" / "delivery_spec.md",
              ROOT / "deliverables" / "RELEASE_v1.5.0.md"):
        assert "298 项" in read(p), "%s 守卫项数未同步" % p.name


def main():
    print("=" * 68)
    print("  v2.0 A 轴司法违约 + 主要人员接入守卫")
    print("=" * 68)
    for name, fn in [
        ("v2.0 结构 37 表 / 33 视图、完整性与 meta 登记", a_structure),
        ("3 张新表行数 34 / 2079 / 500", b_new_tables),
        ("司法明细：类别 / 金额口径 / 日期 / 别名清洗", c_judicial_cleaning),
        ("人员明细：490 脏行剔除 / 490 户 / 10 户缺失", d_person_cleaning),
        ("主表快照保留未清洗口径并可对照", e_snapshot_raw),
        ("4 个并列视图行数与金额分列", f_views),
        ("复合事件 6 家 / 3 家新增 E 候选", g_compound_event),
        ("五档交叉：事件率不单调、禁止当性能结论", h_cross_not_monotonic),
        ("已发布标签 / 五档 / 主表 / DEFINITION_VER 未动", i_published_untouched),
        ("报告 md/json 与库现场回算一致", j_report_matches_db),
        ("数据字典 997 列、新对象说明 100% 覆盖", k_data_dictionary),
        ("交付散文件同源、生成源可复现、基线 26 文件 298 项", l_delivery_same_source_and_repro),
    ]:
        check(name, fn)
    print("=" * 68)
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        return 1
    print("通过 12 项，失败 0 项")
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
