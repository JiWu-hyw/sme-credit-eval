# -*- coding: utf-8 -*-
"""时序图切面守卫（评审 §5.2(1) 闭合 + 同源修订防回归）。

防三类事故：
  1. 年度切片又退回「从 fact_patent JOIN bridge_* 现算边」，与物理图差 19 条；
  2. 有人为了「造时序」去 UPDATE ads_graph_edge.time_slice，破坏全量快照语义；
  3. 报告数字是手写编的，与库里现场算出来的对不上。

用法：python tests/test_graph_temporal_contract.py
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
MIG = os.path.join(ROOT, "src", "data", "migrations", "v1_9_1_graph_slice_fix.sql")
SCHEMA = os.path.join(ROOT, "src", "data", "schema.sql")
REG = os.path.join(ROOT, "configs", "field_gap_registry.json")
REPORT = os.path.join(ROOT, "outputs", "graph_temporal_contract.md")
REPORT_JSON = os.path.join(ROOT, "outputs", "graph_temporal_contract.json")
BACKUP = os.path.join(ROOT, "outputs", "graph_slice_pre_fix_ddl.json")

PASS, FAIL = [], []
TIMED = ("files_patent", "belongs_ipc", "invented_by")


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


def con_ro():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():
    c = con_ro()
    cur = c.cursor()

    def t_views_same_source():
        ddl = dict(cur.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='view' "
            "AND name IN ('v_graph_slice_annual','v_graph_slice_annual_edge')").fetchall())
        assert set(ddl) == {"v_graph_slice_annual", "v_graph_slice_annual_edge"}, (
            "年度切片视图缺失：%s" % sorted(ddl))
        for v, sql in ddl.items():
            assert "ads_graph_edge" in sql, "%s 退回旧定义（未以物理图为边源）" % v
            assert "bridge_patent_ipc" not in sql, "%s 仍直接 JOIN bridge 表" % v

    def t_structure_unchanged():
        n_tab = cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
        n_view = cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
        assert n_tab == 37, "表数 %d（v2.0 新增 3 张 A 轴表后应为 37）" % n_tab
        assert n_view == 33, "视图数 %d（v2.0 新增 4 个 A 轴视图后应为 33）" % n_view

    def t_bottom_tables_untouched():
        for t in ("ads_graph_node", "ads_graph_edge"):
            n = cur.execute("SELECT COUNT(*) FROM %s WHERE time_slice <> 0" % t).fetchone()[0]
            assert n == 0, "%s.time_slice 被 UPDATE（%d 行非 0）" % (t, n)

    def t_years():
        ys = [r[0] for r in cur.execute(
            "SELECT DISTINCT time_slice FROM v_graph_slice_annual ORDER BY 1")]
        assert len(ys) == 29, "年度切片年份数 %d（应为 29）" % len(ys)
        assert ys[0] == 1992 and ys[-1] == 2026, "年份范围异常：%s–%s" % (ys[0], ys[-1])

    def t_last_year_conservation():
        y = cur.execute("SELECT MAX(time_slice) FROM v_graph_slice_annual").fetchone()[0]
        phy = dict(cur.execute(
            "SELECT edge_type, COUNT(*) FROM ads_graph_edge WHERE edge_type IN (?,?,?) GROUP BY 1",
            TIMED).fetchall())
        ann = dict(cur.execute(
            "SELECT edge_type, COUNT(*) FROM v_graph_slice_annual_edge "
            "WHERE time_slice=? GROUP BY 1", (y,)).fetchall())
        assert set(ann) == set(TIMED), "年度切面边类型异常：%s" % sorted(ann)
        for e in TIMED:
            assert phy.get(e) == ann.get(e), (
                "%s 末年不同源：物理 %s / 年度 %s" % (e, phy.get(e), ann.get(e)))
        cum = dict(cur.execute(
            "SELECT metric, n_cumulative FROM v_graph_slice_annual WHERE time_slice=?",
            (y,)).fetchall())
        n_patent = cur.execute(
            "SELECT COUNT(*) FROM ads_graph_node WHERE node_type='patent'").fetchone()[0]
        assert cum.get("n_node_patent") == n_patent, "patent 节点累计不守恒"
        for e in TIMED:
            assert cum.get("n_edge_" + e) == phy.get(e), "%s 指标累计不守恒" % e

    def t_no_orphan_endpoints():
        y = cur.execute("SELECT MAX(time_slice) FROM v_graph_slice_annual").fetchone()[0]
        n = cur.execute(
            """SELECT COUNT(*) FROM (
                 SELECT src_id AS nid, src_type AS nt FROM v_graph_slice_annual_edge
                  WHERE time_slice=?
                 UNION
                 SELECT dst_id, dst_type FROM v_graph_slice_annual_edge
                  WHERE time_slice=?
               ) x LEFT JOIN ads_graph_node n
                 ON n.node_type=x.nt AND n.node_id=x.nid
               WHERE n.node_id IS NULL""", (y, y)).fetchone()[0]
        assert n == 0, "末年有 %d 个端点连不回节点表" % n

    def t_query_performance():
        """性能闸门：普通 JOIN / CTE 改法会让全表 COUNT 从 0.9s 劣化到 >170s。"""
        import time
        c2 = con_ro()
        t0 = time.time()
        # 5 秒硬中断，避免守卫本身被慢查询拖死
        c2.set_progress_handler(lambda: 1 if time.time() - t0 > 5 else 0, 20000)
        try:
            n = c2.execute("SELECT COUNT(*) FROM v_graph_slice_annual_edge").fetchone()[0]
        finally:
            c2.set_progress_handler(None, 0)
            c2.close()
        elapsed = time.time() - t0
        assert n > 800000, "年度切片行数异常：%d" % n
        assert elapsed < 5.0, (
            "全表 COUNT 耗时 %.1fs（>5s）：CROSS JOIN / INDEXED BY 被改掉，"
            "会卡死数据字典导出" % elapsed)
        idx = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_gedge_%'")}
        assert {"idx_gedge_dst_typ", "idx_gedge_src_typ"} <= idx, (
            "缺查询索引：%s" % sorted(idx))
        for v, s in cur.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='view' "
                "AND name IN ('v_graph_slice_annual','v_graph_slice_annual_edge')"):
            assert "CROSS JOIN" in s and "INDEXED BY" in s, (
                "%s 丢性能约束" % v)

    def t_monotonic():
        n = cur.execute(
            """SELECT COUNT(*) FROM (
                 SELECT n_cumulative,
                        LAG(n_cumulative) OVER (PARTITION BY metric ORDER BY time_slice) p
                   FROM v_graph_slice_annual
               ) WHERE p IS NOT NULL AND n_cumulative < p""").fetchone()[0]
        assert n == 0, "累计指标有 %d 处逐年下降" % n

    def t_schema_sync():
        s = read(SCHEMA)
        assert "-- [V191GRAPHFIX:BEGIN]" in s and "-- [V191GRAPHFIX:END]" in s, (
            "schema.sql 缺 V191GRAPHFIX 块")
        mig = read(MIG)
        i = mig.index("-- [V191GRAPHFIX:BEGIN]")
        j = mig.index("-- [V191GRAPHFIX:END]") + len("-- [V191GRAPHFIX:END]")
        assert mig[i:j].strip() in s, "schema.sql 块内容与迁移文件不一致"
        assert "DROP VIEW IF EXISTS v_graph_slice_annual;" not in s[:s.index(
            "-- [V191GRAPHFIX:BEGIN]")], "schema.sql 残留旧年度切片定义"

    def t_migration_check():
        p = subprocess.run(
            [sys.executable, "-m", "src.data.apply_graph_slice_fix", "--check"],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, "迁移 --check 未通过：%s %s" % (
            p.stdout.strip(), p.stderr.strip()[:200])

    def t_registry_gaps():
        r = json.load(open(REG, encoding="utf-8"))
        ids = {g["id"] for g in r.get("graph_gaps", [])}
        assert {"G-GRAPH-01", "G-GRAPH-02", "G-GRAPH-03"} <= ids, (
            "graph_gaps 缺定稿条目：%s" % ids)
        assert "graph_temporal" in r["policies"], "policies 缺 graph_temporal"
        assert r["policies"]["graph_temporal"]["check"].endswith("--check"), (
            "graph_temporal.check 未指向只读校验命令")
        # 产品节点必须被标注为禁止伪造
        g1 = next(g for g in r["graph_gaps"] if g["id"] == "G-GRAPH-01")
        assert g1["status"] == "source_missing" and "禁止" in g1["fallback"], (
            "产品节点缺口未按「保持缺失、禁止伪造」定稿")

    def t_report_exists_and_numbers():
        for p in (REPORT, REPORT_JSON, BACKUP):
            assert os.path.exists(p), "缺产物 %s" % os.path.relpath(p, ROOT)
        R = json.load(open(REPORT_JSON, encoding="utf-8"))
        md = read(REPORT)
        y = cur.execute("SELECT MAX(time_slice) FROM v_graph_slice_annual").fetchone()[0]
        n_ipc = cur.execute(
            "SELECT COUNT(*) FROM ads_graph_edge WHERE edge_type='belongs_ipc'").fetchone()[0]
        assert R["annual_slice"]["year_max"] == y, "报告年份与库不一致"
        assert R["physical_graph"]["edge_types"]["belongs_ipc"] == n_ipc, (
            "报告 IPC 边数与库不一致")
        assert format(n_ipc, ",") in md, "报告正文缺现场算出的 IPC 边数"
        assert all(v is True for k, v in R["conservation"].items()
                   if k != "orphan_endpoints"), "报告里守恒校验未全通过"
        assert R["conservation"]["orphan_endpoints"] == 0, "报告里孤立端点非 0"

    def t_report_contract_sections():
        md = read(REPORT)
        for key in ("STGNN 输入契约", "累计快照", "as-of", "产品节点", "不进时序切面",
                    "口径边界", "守恒校验"):
            assert key in md, "报告缺章节/要点：%s" % key
        assert "time_slice = :T" in md, "报告缺可执行的 as-of 查询模板"

    def t_backup_integrity():
        b = json.load(open(BACKUP, encoding="utf-8"))
        assert set(b["views"]) == {"v_graph_slice_annual", "v_graph_slice_annual_edge"}, (
            "回滚点缺旧视图 DDL")
        assert "bridge_patent_ipc" in b["views"]["v_graph_slice_annual_edge"], (
            "回滚点内容不像修订前定义")

    def t_ddl_hash_stable():
        """幂等：再执行一次迁移不应改变视图定义。"""
        before = {n: hashlib.sha256((s or "").encode()).hexdigest()
                  for n, s in cur.execute(
                      "SELECT name, sql FROM sqlite_master WHERE type='view' "
                      "AND name IN ('v_graph_slice_annual','v_graph_slice_annual_edge')")}
        p = subprocess.run(
            [sys.executable, "-m", "src.data.apply_graph_slice_fix"],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, "重复应用迁移失败：%s" % p.stdout.strip()[-300:]
        c2 = con_ro()
        after = {n: hashlib.sha256((s or "").encode()).hexdigest()
                 for n, s in c2.execute(
                     "SELECT name, sql FROM sqlite_master WHERE type='view' "
                     "AND name IN ('v_graph_slice_annual','v_graph_slice_annual_edge')")}
        c2.close()
        assert before == after, "重复应用后视图 DDL 发生变化（非幂等）"

    for name, fn in [
        ("年度切片视图以物理图为唯一边源", t_views_same_source),
        ("当前结构：37 表 / 33 视图（v2.0 A 轴并列层后）", t_structure_unchanged),
        ("物理底表 time_slice 未被 UPDATE", t_bottom_tables_untouched),
        ("年度切片 29 帧（1992–2026）", t_years),
        ("末年三类边 + patent 节点守恒", t_last_year_conservation),
        ("末年端点全部连回节点表", t_no_orphan_endpoints),
        ("累计指标逐年单调不减", t_monotonic),
        ("查询性能闸门：全表 COUNT < 5s + 索引齐备", t_query_performance),
        ("schema.sql 与迁移文件一致且无旧定义残留", t_schema_sync),
        ("迁移 --check 只读校验通过", t_migration_check),
        ("登记册图缺口三条已定稿", t_registry_gaps),
        ("报告数字现场回算一致", t_report_exists_and_numbers),
        ("报告含 STGNN 契约与口径边界", t_report_contract_sections),
        ("回滚点保存修订前 DDL", t_backup_integrity),
        ("重复应用迁移幂等", t_ddl_hash_stable),
    ]:
        check(name, fn)

    c.close()
    print("\n%d 项通过 / %d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  FAIL %s: %s" % (n, e))
        return 1
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
