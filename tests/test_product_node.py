# -*- coding: utf-8 -*-
"""v2.1 商标明细 + 产品节点并列层接入守卫（14 项，只读）。

守的是四件事：
  1. 结构：40 表 / 37 视图 / 1068 列、v2.1 版本登记、integrity / 外键；
  2. 清洗与守恒：8004 条明细、'-' 日期归 NULL、is_registered 5257 与主表守恒、
     35 产品类 / 10 服务类二分、桥接 1418 行 / 433 户；
  3. 口径隔离：产品节点**未并入 ads_graph_node 主图**、29 帧时序切面未动、
     ads_label / 五档 / 训练入口逐项未动，三处用户口径已纠正；
  4. 同源：接入报告、数据字典、时序契约与交付散文件字节同源。

运行：python tests/test_product_node.py
"""
from __future__ import annotations

import csv
import hashlib
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
REG = ROOT / "configs" / "field_gap_registry.json"

REPORT_MD = OUT / "product_node_report.md"
REPORT_JSON = OUT / "product_node_report.json"
DELIVERED = [
    (D_ALG / "product_node_report.md", REPORT_MD),
    (D_ALG / "product_node_report.json", REPORT_JSON),
    (D_ALG / "graph_temporal_contract.md", OUT / "graph_temporal_contract.md"),
    (D_ALG / "graph_temporal_contract.json", OUT / "graph_temporal_contract.json"),
    (D_DATA / "数据字典.md", OUT / "data_dictionary.md"),
    (D_DATA / "数据字典.csv", OUT / "data_dictionary.csv"),
]

TABLES = ("ods_qcc_trademark", "dim_product_nice_class", "bridge_enterprise_product")
VIEWS = ("v_product_node", "v_enterprise_trademark_summary",
         "v_six_entity_coverage", "v_product_grade_cross")
FAIL: list[tuple[str, str]] = []


def check(name, fn):
    try:
        fn()
        print("  ✓ " + name)
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, str(e)))
        print("  ✗ %s -> %s" % (name, e))


def con_ro() -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True)


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def a_structure():
    c = con_ro()
    try:
        t = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                      "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
        v = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
        objs = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%'").fetchall()]
        cols = sum(c.execute("SELECT COUNT(*) FROM pragma_table_info('%s')" % n)
                   .fetchone()[0] for n in objs)
        assert (t, v, cols) == (40, 37, 1068), (
            "结构 %r，期望 (40, 37, 1068)" % ((t, v, cols),))
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall(), "外键冲突"
        for n in TABLES:
            assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (n,)).fetchone(), "缺表 %s" % n
        for n in VIEWS:
            assert c.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
                             (n,)).fetchone(), "缺视图 %s" % n
        assert c.execute("SELECT version FROM meta_schema_version "
                         "ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "v2.1"
        assert c.execute("SELECT COUNT(*) FROM meta_data_source WHERE source_id="
                         "'qcc_trademark'").fetchone()[0] == 1
    finally:
        c.close()


def b_trademark_cleaning():
    c = con_ro()
    try:
        n = c.execute("SELECT COUNT(*) FROM ods_qcc_trademark").fetchone()[0]
        firms = c.execute("SELECT COUNT(DISTINCT enterprise_id) "
                          "FROM ods_qcc_trademark").fetchone()[0]
        assert (n, firms) == (8004, 444), "商标 %d 条 / %d 户" % (n, firms)
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                         "WHERE enterprise_id IS NULL OR reg_no IS NULL OR reg_no=''").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark WHERE apply_date='-' "
                         "OR reg_announce_date='-'").fetchone()[0] == 0, "'-' 日期未清洗"
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                         "WHERE apply_date IS NULL").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                         "WHERE reg_announce_date IS NULL").fetchone()[0] == 2245
        assert c.execute("SELECT COUNT(DISTINCT trademark_status) "
                         "FROM ods_qcc_trademark").fetchone()[0] == 39
        assert c.execute("SELECT MIN(nice_class), MAX(nice_class) "
                         "FROM ods_qcc_trademark").fetchone() == (1, 45)
    finally:
        c.close()


def c_registered_conservation():
    c = con_ro()
    try:
        n = c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                      "WHERE is_registered=1").fetchone()[0]
        assert n == 5257, "已注册 %d 条，期望 5257" % n
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark WHERE is_registered=1 "
                         "AND trademark_status NOT LIKE '已注册%'").fetchone()[0] == 0
        assert c.execute("SELECT SUM(n_registered) FROM v_enterprise_trademark_summary"
                         ).fetchone()[0] == 5257
    finally:
        c.close()
    R = json.loads(read(REPORT_JSON))
    assert R["conservation"]["registered_matches_main"] is True


def d_class_split():
    c = con_ro()
    try:
        tot = c.execute("SELECT COUNT(*) FROM dim_product_nice_class").fetchone()[0]
        prod = c.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                         "WHERE is_product_node=1").fetchone()[0]
        svc = c.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                        "WHERE is_service=1").fetchone()[0]
        assert (tot, prod, svc) == (45, 35, 10), (
            "尼斯类 %r，期望 (45,35,10)" % ((tot, prod, svc),))
        assert c.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                         "WHERE is_service=1 AND is_product_node=1").fetchone()[0] == 0
        kinds = dict(c.execute("SELECT class_kind, COUNT(*) "
                               "FROM ods_qcc_trademark GROUP BY 1"))
        assert kinds == {"product": 6102, "service": 1902}, (
            "产品/服务类商标条数漂移 %r" % kinds)
        # 服务类有商标但不作产品节点，是有意口径，不是数据缺失
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark WHERE "
                         "class_kind='service'").fetchone()[0] > 0
    finally:
        c.close()


def e_bridge():
    c = con_ro()
    try:
        n = c.execute("SELECT COUNT(*) FROM bridge_enterprise_product").fetchone()[0]
        firms = c.execute("SELECT COUNT(DISTINCT enterprise_id) "
                          "FROM bridge_enterprise_product").fetchone()[0]
        tm = c.execute("SELECT SUM(n_trademark) FROM bridge_enterprise_product").fetchone()[0]
        assert (n, firms, tm) == (1418, 433, 6102), (
            "桥接 %r，期望 (1418,433,6102)" % ((n, firms, tm),))
        assert c.execute("SELECT COUNT(*) FROM bridge_enterprise_product b "
                         "JOIN dim_product_nice_class p USING(product_id) "
                         "WHERE p.is_product_node=0").fetchone()[0] == 0, "服务类泄漏进桥接"
        assert c.execute("SELECT COUNT(*) FROM (SELECT enterprise_id, product_id "
                         "FROM bridge_enterprise_product GROUP BY 1,2 HAVING COUNT(*)>1)"
                         ).fetchone()[0] == 0, "桥接键重复"
    finally:
        c.close()


def f_product_node_view():
    c = con_ro()
    try:
        n = c.execute("SELECT COUNT(*) FROM v_product_node").fetchone()[0]
        assert n == 35, "v_product_node %d 行（应 35）" % n
        assert c.execute("SELECT COUNT(*) FROM v_product_node WHERE stated_match=0"
                         ).fetchone()[0] == 0, "覆盖企业数与用户 CSV 不一致"
        assert c.execute("SELECT SUM(covered_firms_actual) FROM v_product_node"
                         ).fetchone()[0] == 1418
        assert c.execute("SELECT COUNT(*) FROM v_product_node WHERE node_note "
                         "LIKE '%未并入%'").fetchone()[0] == 35
    finally:
        c.close()


def g_enterprise_summary():
    c = con_ro()
    try:
        assert c.execute("SELECT COUNT(*) FROM v_enterprise_trademark_summary").fetchone()[0] == 500
        assert c.execute("SELECT SUM(n_trademark) FROM v_enterprise_trademark_summary"
                         ).fetchone()[0] == 8004
        assert c.execute("SELECT COUNT(*) FROM v_enterprise_trademark_summary "
                         "WHERE has_trademark=1").fetchone()[0] == 444
        assert c.execute("SELECT SUM(n_product_link) FROM v_enterprise_trademark_summary"
                         ).fetchone()[0] == 1418
        assert all("68 家" in (r[0] or "") for r in c.execute(
            "SELECT DISTINCT brand_note FROM v_enterprise_trademark_summary"))
    finally:
        c.close()


def h_six_entity_coverage():
    c = con_ro()
    try:
        rows = {r[0]: r for r in c.execute("SELECT * FROM v_six_entity_coverage")}
        assert len(rows) == 6
        p = rows["产品"]
        assert (p[2], p[3], p[4]) == (0, 35, 433), "产品行口径 %r" % (p[2:5],)
        assert abs(p[5] - 0.866) < 1e-9 and "部分闭合" in p[6]
        assert "SKU" in p[7] and "未插入" in p[7]
        assert rows["人员"][3] == 2079, "人员来源行数未纠正"
        assert rows["人员"][2] == 15955, "图内人员节点数漂移"
    finally:
        c.close()


def i_main_graph_untouched():
    c = con_ro()
    try:
        g = dict(c.execute("SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY 1"))
        assert g == {"enterprise": 500, "patent": 32969, "inventor": 15955,
                     "ipc": 11588, "investor": 572}, "主图节点类型被改动 %r" % g
        assert c.execute("SELECT COUNT(*) FROM ads_graph_node "
                         "WHERE node_type LIKE '%product%'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(DISTINCT time_slice) "
                         "FROM v_graph_slice_annual").fetchone()[0] == 29
        assert c.execute("SELECT COUNT(*) FROM v_graph_slice_annual_edge "
                         "WHERE src_id IS NULL").fetchone()[0] == 0
    finally:
        c.close()


def j_published_untouched():
    c = con_ro()
    try:
        lab = tuple(c.execute("SELECT COUNT(*), SUM(label_value=1), SUM(label_value=0) "
                              "FROM ads_label").fetchone())
        assert lab == (307, 21, 286), "ads_label 被改动 %r" % (lab,)
        grades = dict(c.execute("SELECT grade_multilabel, COUNT(*) "
                                "FROM v_enterprise_multilabel_grade GROUP BY 1"))
        assert grades == {"A 引领": 81, "B 优质": 176, "C 稳健": 157,
                          "D 观察": 83, "E 预警": 3}, "五档被改动 %r" % grades
        for v in ("v_enterprise_label_matrix", "v_model_train_ready",
                  "v_model_train_ready_credit"):
            assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view' "
                             "AND name=?", (v,)).fetchone()[0] == 1, "缺视图 %s" % v
    finally:
        c.close()


def k_registry_policy():
    r = json.loads(read(REG))
    assert r["meta"]["version"] == "v5"
    assert "partial_closed" in r["meta"]["status_enum"]
    g1 = next(g for g in r["graph_gaps"] if g["id"] == "G-GRAPH-01")
    assert g1["status"] == "partial_closed", "产品节点缺口状态未改判"
    assert "SKU" in g1["reason"], "reason 未写明 SKU 级仍缺"
    assert any(k in g1["decision"] for k in ("不并入主图", "未并入主图")), (
        "decision 未写明产品节点不并入主图：%r" % g1["decision"])
    assert "source_missing" in g1["history"], "原定稿未留档"
    p = r["policies"]["v2_1_product_node"]
    for k in ("forbid", "untouched", "candidate_only", "check"):
        assert k in p, "policy 缺 %s" % k
    assert "test_product_node.py" in p["check"]


def l_graph_contract():
    R = json.loads(read(OUT / "graph_temporal_contract.json"))
    assert R["current_db_version"] == "v2.1"
    P = R["six_entity_types"]["parallel_layer_v2_1"]
    assert P["available"] and P["in_main_graph"] is False
    assert (P["n_product_node"], P["n_service_class"], P["n_covered_firms"]) == (35, 10, 433)
    md = read(OUT / "graph_temporal_contract.md")
    for k in ("产品节点并列层", "类别粒度部分闭合", "未入主图", "SKU 级", "29 帧"):
        assert k in md, "时序契约 §6 缺 %r" % k
    assert "已完整覆盖" in md and "禁止" in md


def m_report_matches_db():
    R = json.loads(read(REPORT_JSON))
    assert R["schema_version"] == "v2.1"
    assert R["structure"] == {"tables": 40, "views": 37, "columns": 1068}
    assert (R["source"]["trademark_rows"], R["source"]["trademark_firms"],
            R["source"]["product_nodes"], R["source"]["service_classes"]) == (8004, 444, 35, 10)
    assert (R["bridge"]["rows"], R["bridge"]["firms"], R["bridge"]["trademarks"]) == \
           (1418, 433, 6102)
    assert R["conservation"]["stated_match_all"] is True
    assert R["conservation"]["service_leak"] == 0
    assert R["conservation"]["brand_mismatch_firms"] == 68
    assert len(R["product_nodes"]) == 35 and len(R["six_entity_coverage"]) == 6
    assert R["untouched"]["product_node_in_main_graph"] == 0
    assert R["untouched"]["annual_slice_frames"] == 29
    c = con_ro()
    try:
        assert c.execute("SELECT COUNT(*) FROM ods_qcc_trademark").fetchone()[0] == \
               R["source"]["trademark_rows"]
    finally:
        c.close()
    md = read(REPORT_MD)
    for k in ("三处口径纠正", "类别粒度部分闭合", "未并入", "禁止清单", "68 家"):
        assert k in md, "报告缺 %r" % k


def n_delivery_same_source():
    for dst, src in DELIVERED:
        assert dst.exists(), "交付目录缺 %s" % dst.name
        assert md5(dst) == md5(src), "交付份与源份不同源：%s" % dst.name
    rows = list(csv.DictReader((OUT / "data_dictionary.csv").open(
        encoding="utf-8-sig", newline="")))
    assert len(rows) == 1068, "数据字典字段数 %d（应 1068）" % len(rows)
    for name in TABLES + VIEWS:
        g = [r for r in rows if r["对象名"] == name]
        assert g, "字典缺对象 %s" % name
        assert all(r["字段说明"].strip() for r in g), "%s 有字段说明为空" % name
        assert all(r["对象说明"].strip() for r in g), "%s 对象说明为空" % name
    for path, kws in (
        (ROOT / "src" / "data" / "export_delivery.py",
         ("product_node_report.md", "322 项", "28 个测试文件")),
        (ROOT / "src" / "data" / "apply_v2_1_product_node.py",
         ("ods_qcc_trademark", "--check", "V21PRODUCT")),
        (ROOT / "src" / "data" / "export_product_node_report.py",
         ("def gather", "def build_md")),
        (ROOT / "src" / "data" / "export_db_bundle.py",
         ("v2.1 新增", "六条使用红线", "--no-zip")),
    ):
        t = read(path)
        for k in kws:
            assert k in t, "%s 缺 %r" % (path.name, k)
    n = len(list((ROOT / "tests").glob("test_*.py")))
    assert n == 28, "tests/ 下应为 28 个守卫文件，实得 %d" % n
    for p in (ROOT / "README.md", D_ALG.parent / "00_先看这个.md",
              ROOT / "deliverables" / "03_交付说明" / "delivery_spec.md",
              ROOT / "deliverables" / "RELEASE_v1.5.0.md"):
        assert "322 项" in read(p), "%s 守卫项数未同步" % p.name


def main():
    print("=" * 68)
    print("  v2.1 商标明细 + 产品节点并列层接入守卫")
    print("=" * 68)
    for name, fn in [
        ("v2.1 结构 40 表 / 37 视图 / 1068 列、完整性与 meta 登记", a_structure),
        ("商标明细 8004 条 / 444 家、日期与状态清洗", b_trademark_cleaning),
        ("is_registered 5257 与主表「商标_有效件数」守恒", c_registered_conservation),
        ("尼斯 45 类＝35 产品 + 10 服务、服务类 1902 条不作节点", d_class_split),
        ("企业—产品桥接 1418 行 / 433 户 / 6102 条、无服务类泄漏", e_bridge),
        ("v_product_node 35 行、覆盖企业数逐类守恒", f_product_node_view),
        ("企业层汇总 500 行、8004 条守恒、品牌数口径警示", g_enterprise_summary),
        ("六类实体覆盖：产品部分闭合 / 人员 2079 纠正", h_six_entity_coverage),
        ("主图仍 5 类、无 product 节点、29 帧切面未动", i_main_graph_untouched),
        ("已发布标签 / 五档 / 训练入口逐项未动", j_published_untouched),
        ("登记册 G-GRAPH-01 改判 partial_closed、v2.1 policy 齐备", k_registry_policy),
        ("时序契约 §6 与 JSON 并列层口径同步", l_graph_contract),
        ("接入报告 md/json 与库现场回算一致", m_report_matches_db),
        ("交付散文件同源、字典 1068 列、生成源可复现、基线 28 文件 322 项", n_delivery_same_source),
    ]:
        check(name, fn)
    print("=" * 68)
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        return 1
    print("通过 14 项，失败 0 项")
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
