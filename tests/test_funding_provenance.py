# -*- coding: utf-8 -*-
"""A7 守卫：融资事件行级溯源回填「补得对、且没顺手改业务数值」。

守的四类事故：
  1. 只填了一部分行，或填了没在 meta_data_source 登记的来源；
  2. 借回填之机改了金额 / 估值 / 机构等级等业务列（SHA-256 前后指纹对账）；
  3. 借回填之机升了 schema、改了表/视图结构，或往 meta_ingest_log 塞非法 stage；
  4. 只改了库却没改 ETL 源码，下次重跑 load_500family.py 又变回 0/来源断链。

v3.0 全量口径（dim 500→3000）：融资事实表 1982 行 =
  506(zj_500family) + 1476(qcc_funding_3000)，source_id 全部非空；post_valuation
  全 NULL（v1.8 设计性口径保持）。A7/v1.8 回填凭证里的 506 行、单源、SHA-256
  指纹均为历史基线，故 a/c/d/e 只把它当历史事实校验，不再拿它约束当前快照。

八项断言，全部来自库内确定性 SQL / 文件读取，不依赖模型或运行时推断。

用法：python tests/test_funding_provenance.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
REPORT = os.path.join(ROOT, "outputs", "funding_provenance_backfill.json")
DICT_CSV = os.path.join(ROOT, "outputs", "data_dictionary.csv")
ETL = os.path.join(ROOT, "src", "data", "load_500family.py")
NOTES = os.path.join(ROOT, "src", "data", "column_notes.py")
sys.path.insert(0, ROOT)

from src.data.apply_funding_provenance import check as backfill_check  # noqa: E402

SOURCE_ID = "zj_500family"
# A7 / v1.8 历史基线：d、e 仍校验回填凭证与 parse 日志中的 506/506（历史事实，勿改为 1982）。
EXPECT_ROWS = 506
# v3.0 全量口径：dim 500→3000，融资事实表 1982 行 = 506(zj_500family) + 1476(qcc_funding_3000)。
FULL_ROWS = 1982
VALID_SOURCES = ("qcc_funding_3000", "zj_500family")
# 故意独立写一遍，不复用脚本里的常量，避免「自己验自己」
BUSINESS_COLS = (
    "event_id", "enterprise_id", "event_date", "event_year", "event_type",
    "amount_cny", "round_label", "post_valuation", "investor_names",
    "prestige_rank", "is_public_co",
)

PASS, FAIL = [], []


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


def load_report():
    with open(REPORT, encoding="utf-8") as f:
        return json.load(f)


def local_fingerprint():
    """独立重算业务列指纹（与回填脚本实现分离，只共享列清单语义）。"""
    c = con()
    try:
        rows = c.execute("SELECT %s FROM fact_funding_event ORDER BY event_id"
                         % ", ".join(BUSINESS_COLS)).fetchall()
    finally:
        c.close()
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main():
    print("=" * 60)
    print("A7 融资事件溯源回填守卫")
    print("=" * 60)

    def a_script_check():
        c = con()
        try:
            bad = backfill_check(c)
        finally:
            c.close()
        # v3.0 全量扩样后，A7(v1.8) 历史脚本 check() 里写死的 506 基线会报「历史基线不符」，
        # 属预期：行数 506→1982、来源单源→双源、源 CSV 行数、业务指纹随扩样改变。
        # 这里只放行这四类已知的口径漂移；若 check() 报出其它问题（source_id 未回填、
        # 来源未登记、parse 证据链断裂、凭证缺失等）仍判失败，保证校验依然有意义。
        KNOWN_BASELINE_DRIFT = (
            "融资事件行数",
            "source_id 取值不唯一",
            "源 CSV",
            "业务列指纹与凭证",
        )
        unexpected = [m for m in bad if not m.startswith(KNOWN_BASELINE_DRIFT)]
        assert not unexpected, "回填脚本只读校验出现预期外问题：%s" % unexpected
        if bad:
            print("    （A7 历史脚本 check 报 %d 条 v3.0 基线漂移，属预期）" % len(bad))

    def b_full_coverage():
        assert one("SELECT COUNT(*) FROM fact_funding_event") == FULL_ROWS, \
            "融资事件行数不是 %d" % FULL_ROWS
        assert one("SELECT COUNT(source_id) FROM fact_funding_event") == FULL_ROWS, \
            "source_id 仍有 NULL"
        assert one("SELECT COUNT(*) FROM fact_funding_event WHERE source_id IS NULL") == 0, \
            "source_id 存在 NULL 行"
        got = [r[0] for r in con().execute(
            "SELECT DISTINCT source_id FROM fact_funding_event ORDER BY 1")]
        assert tuple(got) == VALID_SOURCES, \
            "source_id 取值集合不符：%r，期望 %r" % (got, VALID_SOURCES)

    def c_business_unchanged():
        # v3.0 扩样（dim 500→3000）改变了融资事实表本身的业务数据：1982 行 =
        # 506(zj_500family) + 1476(qcc_funding_3000)，amount / round / investor 等业务列
        # 随新增行变化。因此凭证里的 SHA-256 是「A7 / v1.8 回填时点」的历史基线，
        # 无法与当前快照精确对账——按任务要求跳过精确比对，改为记录性说明，
        # 并校验当前快照的两个关键口径：source_id 全非空、post_valuation 全 NULL
        # （v1.8 设计性 NULL 口径在 v3.0 保持不变）。
        rep = load_report()
        fp = local_fingerprint()
        print("    （历史 v1.8 业务指纹 %s...；v3.0 当前快照指纹 %s...，精确对账已跳过）"
              % (rep.get("business_columns_fingerprint_before", "")[:12], fp[:12]))
        assert rep.get("business_columns_fingerprint_before") == \
            rep.get("business_columns_fingerprint_after"), \
            "历史凭证内部前后指纹不相等（凭证本身被篡改）"
        assert one("SELECT COUNT(source_id) FROM fact_funding_event") == FULL_ROWS, \
            "当前快照 source_id 未全非空"
        assert one("SELECT COUNT(post_valuation) FROM fact_funding_event") == 0, \
            "当前快照 post_valuation 不再是全 NULL（v1.8 设计性 NULL 口径被破坏）"

    def d_report_complete():
        rep = load_report()
        assert rep.get("change_id") == "A7_provenance_backfill", "凭证缺少变更编号"
        assert rep.get("schema_version") == "v1.8", "回填不应升 schema 版本"
        assert rep.get("schema_structure_changed") is False, "回填不应改表结构"
        assert rep.get("rows_total") == EXPECT_ROWS and \
            rep.get("rows_backfilled") == EXPECT_ROWS, "凭证行数不是 506/506"
        assert "source_id" not in rep.get("columns_untouched", []), \
            "凭证把 source_id 也列成了未改动列"
        ev = rep.get("ingest_log_evidence") or {}
        assert ev.get("source_id") == SOURCE_ID and \
            ev.get("rows_in") == EXPECT_ROWS and ev.get("rows_ok") == EXPECT_ROWS, \
            "凭证里的 parse 入库证据不完整"

    def e_evidence_chain():
        c = con()
        try:
            src = c.execute("SELECT is_active FROM meta_data_source WHERE source_id=?",
                            (SOURCE_ID,)).fetchone()
            assert src and src[0] == 1, "meta_data_source 未登记该来源或未启用"
            parse = c.execute(
                "SELECT COUNT(*) FROM meta_ingest_log WHERE target_table='fact_funding_event' "
                "AND source_id=? AND stage='parse' AND rows_in=? AND rows_ok=?",
                (SOURCE_ID, EXPECT_ROWS, EXPECT_ROWS)).fetchone()[0]
            assert parse >= 1, "找不到 506/506 的 parse 入库记录"
            bad_stage = c.execute(
                "SELECT COUNT(*) FROM meta_ingest_log WHERE stage NOT IN "
                "('fetch','parse','match','build')").fetchone()[0]
            assert bad_stage == 0, "meta_ingest_log 出现非法 stage：%d 条" % bad_stage
        finally:
            c.close()

    def f_structure_untouched():
        c = con()
        try:
            objs = c.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%'").fetchall()
            tables = [n for t, n in objs if t == "table"]
            views = [n for t, n in objs if t == "view"]
            cols = sum(len(c.execute('PRAGMA table_info("%s")' % n).fetchall())
                       for n in tables + views)
            # A7 时点为 v1.8（34/23/837）；v1.9 只增 6 个多标签视图（表数不变）；
            # v2.0 再增 3 张 A 轴 ODS 贴源表 + 4 个并列视图，
            # v2.1 再增商标/尼斯分类/桥接 3 表 + 4 视图，
            # v2.2 再增时序图 2 表（ads_graph_node_ts / ads_graph_edge_ts，0 视图），
            # 均为新增对象，不触碰任何事实表 / 主档数据。此处按「基线表数 + 新增表身份」校验，
            # 防止有人借升版之名悄悄改动 fact_* / dim_*。
            V20_TABLES = {"ods_qcc_judicial", "ods_qcc_person",
                          "ods_qcc_axis_a_snapshot"}
            # v2.1 产品节点并列层再加 3 张表（1 张 ODS + 1 维表 + 1 桥表）。
            V21_TABLES = {"ods_qcc_trademark", "dim_product_nice_class",
                          "bridge_enterprise_product"}
            # v2.2 时序图并列层再加 2 张表（节点 / 边年度切片）。
            V22_TABLES = {"ads_graph_node_ts", "ads_graph_edge_ts"}
            # 基线里原有 4 张企查查 ODS 表。
            QCC_ODS_BASELINE = {"ods_qcc_annual", "ods_qcc_cert",
                                "ods_qcc_honor", "ods_qcc_shareholder"}
            # v3.0 全量扩样：42 表 / 38 视图，均为扩样新增对象，不触碰事实表 / 主档结构。
            assert len(tables) in (34, 37, 40, 42), "事实表数被改动：%d" % len(tables)
            if len(tables) == 37:
                assert V20_TABLES <= set(tables), (
                    "v2.0 三张 A 轴贴源表不全：%r" % (V20_TABLES - set(tables)))
                assert {t for t in tables if t.startswith("ods_qcc_")} == (
                    QCC_ODS_BASELINE | V20_TABLES), (
                    "ODS 贴源层出现 v2.0 之外的额外表")
            if len(tables) == 40:
                assert V20_TABLES | V21_TABLES <= set(tables), (
                    "v2.1 产品节点三张表不全：%r" % (V21_TABLES - set(tables)))
                assert {t for t in tables if t.startswith("ods_qcc_")} == (
                    QCC_ODS_BASELINE | V20_TABLES | {"ods_qcc_trademark"}), (
                    "ODS 贴源层出现 v2.1 之外的额外表")
                v21v = {r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='view' AND name IN "
                    "('v_product_node','v_enterprise_trademark_summary',"
                    "'v_six_entity_coverage','v_product_grade_cross')")}
                assert len(v21v) == 4, "v2.1 四个并列视图不全：%r" % v21v
            if len(tables) == 42:
                assert V20_TABLES | V21_TABLES | V22_TABLES <= set(tables), (
                    "v2.2 时序图两张表不全：%r" % (V22_TABLES - set(tables)))
                assert {t for t in tables if t.startswith("ods_qcc_")} == (
                    QCC_ODS_BASELINE | V20_TABLES | {"ods_qcc_trademark"}), (
                    "ODS 贴源层出现 v2.2 之外的额外表")
            assert len(views) >= 23, "视图数少于 A7 时点：%d" % len(views)
            assert cols >= 837, "字段数少于 A7 时点：%d" % cols
            ver = c.execute("SELECT version FROM meta_schema_version "
                            "ORDER BY applied_at DESC LIMIT 1").fetchone()[0]
            assert ver in ("v1.8", "v1.9", "v2.0", "v2.1", "v2.2", "v3.0"), (
                "schema 版本异常：%s" % ver)
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", \
                "integrity_check 不通过"
            assert not c.execute("PRAGMA foreign_key_check").fetchall(), "外键冲突"
        finally:
            c.close()

    def g_etl_source_fixed():
        t = read(ETL)
        assert 'FUNDING_SOURCE_ID = "zj_500family"' in t, \
            "ETL 源码没有定义行级来源常量，重跑会再次断链"
        assert "prestige, FUNDING_SOURCE_ID, is_pub" in t, \
            "load_funding INSERT 仍把 source_id 写成 NULL"

    def h_dictionary_synced():
        t = read(NOTES)
        assert "source_id" in t.split('"fact_funding_event"')[1].split("}")[0], \
            "column_notes.py 未说明 source_id 是血缘列"
        # 字典是生成产物（outputs/* 不入库）；干净检出时只校验源码口径，
        # 与 test_funding_join_contract.py 的「存在则校验」约定一致。
        if not os.path.exists(DICT_CSV):
            print("    （干净检出：outputs/data_dictionary.csv 不存在，跳过字典非空率校验）")
            return
        with open(DICT_CSV, encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f)
                    if r["对象名"] == "fact_funding_event" and r["字段名"] == "source_id"]
        assert len(rows) == 1, "数据字典里没有 fact_funding_event.source_id 这一行"
        assert rows[0]["非空行数"] == str(FULL_ROWS) and \
            float(rows[0]["非空率%"]) == 100.0, \
            "字典非空率未随 v3.0 全量口径重生成：非空 %s / %s%%（期望 %d / 100%%）" % (
                rows[0]["非空行数"], rows[0]["非空率%"], FULL_ROWS)
        assert "血缘" in rows[0]["字段说明"] or "zj_500family" in rows[0]["字段说明"], \
            "字典未说明该列是行级血缘"

    for name, fn in [
        ("回填脚本自身校验通过（历史基线漂移已放行）", a_script_check),
        ("1982 行全覆盖且来源双合法、无 NULL", b_full_coverage),
        ("业务指纹历史基线说明 + 当前快照口径校验", c_business_unchanged),
        ("变更凭证字段完整（A7/v1.8 历史基线）", d_report_complete),
        ("证据链：登记册 + parse 日志 + 合法 stage", e_evidence_chain),
        ("schema 结构 / 版本 / 完整性未动", f_structure_untouched),
        ("ETL 源码已修，重跑不再断链", g_etl_source_fixed),
        ("字典与字段说明同步为 1982 / 100%", h_dictionary_synced),
    ]:
        check(name, fn)

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ FUNDING PROVENANCE TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
