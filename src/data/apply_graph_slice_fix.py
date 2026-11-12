"""幂等修订两个年度切片视图，使其与物理图 ads_graph_edge 严格同源。

背景：算法端评审报告 §5.2(1) 指出「预置图单切片、STGNN 无时序输入」。
库内其实已有 v_graph_slice_annual / _edge 两个年度累计切片视图，但旧定义
从 fact_patent JOIN bridge_* 现算边，与物理图不同源：belongs_ipc 末年
累计 51,795 条，物理图只有 51,776 条，差 19 条。这会让按年度切面建出来的
图与静态图对不上。

本脚本只 DROP/CREATE 两个**已存在**的视图：
  * 不新增表或视图（v1.9 基线 34 表 / 29 视图；v2.0 A 轴并列层后 37 / 33；
    v2.1 产品节点并列层后 40 / 37）
  * 不 UPDATE 任何底表行（time_slice 全 0 保持不变）
  * 不升 schema 版本号（属视图逻辑修订）
  * 同步 src/data/schema.sql 的 DDL 单一事实源

用法：
    python -m src.data.apply_graph_slice_fix            # 应用 + 校验
    python -m src.data.apply_graph_slice_fix --check    # 只读校验
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
MIG = os.path.join(ROOT, "src", "data", "migrations", "v1_9_1_graph_slice_fix.sql")
SCHEMA = os.path.join(ROOT, "src", "data", "schema.sql")
BACKUP = os.path.join(ROOT, "outputs", "graph_slice_pre_fix_ddl.json")

BEGIN = "-- [V191GRAPHFIX:BEGIN]"
END = "-- [V191GRAPHFIX:END]"
ANCHOR = "\n-- [V18CONSISTENCY:END]"
OLD_HEAD = "DROP VIEW IF EXISTS v_graph_slice_annual;"

TIMED_EDGES = ("files_patent", "belongs_ipc", "invented_by")


def sql_block() -> str:
    with open(MIG, encoding="utf-8") as f:
        text = f.read()
    i, j = text.index(BEGIN), text.index(END) + len(END)
    return text[i:j].rstrip() + "\n"


def con_ro() -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def check(con: sqlite3.Connection) -> list[str]:
    """返回问题列表；空列表 = 全部通过。"""
    bad: list[str] = []

    ddl = dict(
        con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='view' "
            "AND name IN ('v_graph_slice_annual','v_graph_slice_annual_edge')"
        ).fetchall()
    )
    for v in ("v_graph_slice_annual", "v_graph_slice_annual_edge"):
        if v not in ddl:
            bad.append("缺视图 %s" % v)
            continue
        sql = ddl[v] or ""
        if "ads_graph_edge" not in sql:
            bad.append("%s 仍是旧定义（未以物理图为边源）" % v)
        if "CROSS JOIN" not in sql or "INDEXED BY" not in sql:
            bad.append("%s 丢了 CROSS JOIN / INDEXED BY 性能约束（会退化到分钟级）" % v)

    idx = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_gedge_%'"
    )}
    for name in ("idx_gedge_dst_typ", "idx_gedge_src_typ"):
        if name not in idx:
            bad.append("缺查询索引 %s" % name)

    n_tab = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    n_view = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
    # 本修订只 DROP/CREATE 两个已存在的视图，不得增删表或视图。
    # 允许四种基线：v1.9（34/29）、v2.0 A 轴并列层之后（37/33）、
    # v2.1 产品节点并列层之后（40/37 = 37/33 再 +3 表 +4 视图）、
    # v2.2 时序图并列层之后（42/37 = 40/37 再 +2 表 0 视图：ads_graph_node_ts / ads_graph_edge_ts）。
    # 差值必须严格等于各次升版新增的并列层对象，不接受任意漂移。
    if (n_tab, n_view) not in ((34, 29), (37, 33), (40, 37), (42, 37)):
        bad.append("结构变为 %d 表 / %d 视图（允许 34/29、37/33、40/37 或 42/37；"
                   "本修订不得增删表或视图）" % (n_tab, n_view))
    if (n_tab, n_view) in ((37, 33), (40, 37), (42, 37)):
        v20 = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('ods_qcc_judicial','ods_qcc_person','ods_qcc_axis_a_snapshot')")}
        if len(v20) != 3:
            bad.append("v2.0 A 轴并列层的 3 张 ODS 表不全")
    if (n_tab, n_view) == (40, 37):
        v21t = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('ods_qcc_trademark','dim_product_nice_class','bridge_enterprise_product')")}
        if len(v21t) != 3:
            bad.append("40 表基线不是 v2.1 产品节点并列层（3 张新表不全）")
        v21v = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name IN "
            "('v_product_node','v_enterprise_trademark_summary',"
            "'v_six_entity_coverage','v_product_grade_cross')")}
        if len(v21v) != 4:
            bad.append("37 视图基线不是 v2.1 产品节点并列层（4 个新视图不全）")
    if (n_tab, n_view) == (42, 37):
        v22t = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('ads_graph_node_ts','ads_graph_edge_ts')")}
        if len(v22t) != 2:
            bad.append("42 表基线不是 v2.2 时序图并列层（2 张新表不全）")

    # 物理底表未被改写：time_slice 仍全为 0
    for t in ("ads_graph_node", "ads_graph_edge"):
        nz = con.execute("SELECT COUNT(*) FROM %s WHERE time_slice <> 0" % t).fetchone()[0]
        if nz:
            bad.append("%s.time_slice 被改写（%d 行非 0），违反不 UPDATE 底表约定" % (t, nz))

    ymax = con.execute("SELECT MAX(time_slice) FROM v_graph_slice_annual").fetchone()[0]
    if ymax is None:
        bad.append("年度指标视图无年份")
        return bad

    # 末年守恒：年度边视图 == 物理图
    phy = dict(
        con.execute(
            "SELECT edge_type, COUNT(*) FROM ads_graph_edge WHERE edge_type IN (?,?,?) GROUP BY 1",
            TIMED_EDGES,
        ).fetchall()
    )
    ann = dict(
        con.execute(
            "SELECT edge_type, COUNT(*) FROM v_graph_slice_annual_edge "
            "WHERE time_slice=? GROUP BY 1", (ymax,),
        ).fetchall()
    )
    for e in TIMED_EDGES:
        if phy.get(e, 0) != ann.get(e, 0):
            bad.append("末年 %s 不同源：物理 %d / 年度 %d" % (e, phy.get(e, 0), ann.get(e, 0)))
    if set(ann) - set(TIMED_EDGES):
        bad.append("年度切面出现非时序边类型：%s" % (set(ann) - set(TIMED_EDGES)))

    # 末年指标累计 == 物理图（另加专利节点）
    cum = dict(
        con.execute(
            "SELECT metric, n_cumulative FROM v_graph_slice_annual WHERE time_slice=?", (ymax,)
        ).fetchall()
    )
    expect = {
        "n_node_patent": con.execute(
            "SELECT COUNT(*) FROM ads_graph_node WHERE node_type='patent'"
        ).fetchone()[0],
        "n_edge_files_patent": phy.get("files_patent", 0),
        "n_edge_belongs_ipc": phy.get("belongs_ipc", 0),
        "n_edge_invented_by": phy.get("invented_by", 0),
    }
    for k, v in expect.items():
        if cum.get(k) != v:
            bad.append("末年指标 %s = %s，期望 %d（物理图）" % (k, cum.get(k), v))

    # 端点 ID 全部能连回节点表（防建出孤立节点）
    orphan = con.execute(
        """SELECT COUNT(*) FROM (
             SELECT src_id AS nid, src_type AS nt FROM v_graph_slice_annual_edge
              WHERE time_slice=?
             UNION
             SELECT dst_id, dst_type FROM v_graph_slice_annual_edge
              WHERE time_slice=?
           ) x
           LEFT JOIN ads_graph_node n ON n.node_type=x.nt AND n.node_id=x.nid
           WHERE n.node_id IS NULL""", (ymax, ymax)
    ).fetchone()[0]
    if orphan:
        bad.append("末年有 %d 个端点连不回 ads_graph_node" % orphan)

    # 逐年累计单调不减
    dec = con.execute(
        """SELECT COUNT(*) FROM (
             SELECT metric, time_slice, n_cumulative,
                    LAG(n_cumulative) OVER (PARTITION BY metric ORDER BY time_slice) AS prev
               FROM v_graph_slice_annual
           ) WHERE prev IS NOT NULL AND n_cumulative < prev"""
    ).fetchone()[0]
    if dec:
        bad.append("累计指标出现 %d 处逐年下降" % dec)

    # DDL 单一事实源同步
    if os.path.exists(SCHEMA):
        with open(SCHEMA, encoding="utf-8") as f:
            s = f.read()
        if BEGIN not in s or END not in s:
            bad.append("schema.sql 未同步 V191GRAPHFIX 块")
        else:
            if sql_block().strip() not in s:
                bad.append("schema.sql 的块内容与迁移文件不一致")
            if OLD_HEAD in s[:s.index(BEGIN)]:
                # 旧定义残留在块之前 → 后执行的旧 CREATE 会覆盖新定义
                bad.append("schema.sql 仍残留旧年度切片定义（会覆盖新定义）")
    else:
        bad.append("缺 schema.sql")

    return bad


def sync_schema_sql() -> None:
    with open(SCHEMA, encoding="utf-8") as f:
        s = f.read()
    if BEGIN in s:
        i = s.index(BEGIN)
        # 块起点前可能有一行空行，保留文件原有前缀
        prefix = s[:i].rstrip() + "\n\n"
    else:
        i = s.index(OLD_HEAD)
        prefix = s[:i]
    j = s.index(ANCHOR, i) if ANCHOR in s[i:] else s.index("-- [V19MULTILABEL:BEGIN]", i)
    new_text = prefix + sql_block() + "\n" + s[j:]
    if new_text == s:
        return  # 内容未变则不写，避免无谓地改动 DDL 文件 mtime
    with open(SCHEMA, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读校验，不写库")
    args = ap.parse_args()

    if args.check:
        c = con_ro()
        bad = check(c)
        c.close()
        if bad:
            for b in bad:
                print("  FAIL  " + b)
            print("[check] %d 项未通过" % len(bad))
            return 1
        print("[check] 年度切片同源修订全部通过")
        return 0

    if not os.path.exists(DB):
        print("缺数据库：%s" % DB)
        return 1

    c = sqlite3.connect(DB)
    try:
        # 幂等短路：已应用且校验全通过时**不写库**。
        # 否则每次跑守卫都会刷新 DB mtime，导致 label_spec / data_dictionary
        # 的「文档不早于库」守卫连锁失败。
        already = not check(c)
        if already:
            print("[skip ] 修订已应用且校验通过，不重复写库（保持 DB mtime 稳定）")
        else:
            # 回滚点：只改视图，保存旧 DDL 即可完整回退
            os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
            old = c.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='view' "
                "AND name IN ('v_graph_slice_annual','v_graph_slice_annual_edge') ORDER BY name"
            ).fetchall()
            if not any("ads_graph_edge" in (sql or "") for _, sql in old):
                with open(BACKUP, "w", encoding="utf-8") as f:
                    json.dump({"views": {n: s for n, s in old}}, f,
                              ensure_ascii=False, indent=2)
                print("[backup] 旧视图 DDL → %s" % os.path.relpath(BACKUP, ROOT))

            c.executescript(sql_block())
            c.commit()
            print("[apply] 两个年度切片视图已重建（以 ads_graph_edge 为唯一边源）")

        sync_schema_sql()
        print("[sync ] schema.sql 已同步 V191GRAPHFIX 块")
        bad = check(c)
    finally:
        c.close()

    if bad:
        for b in bad:
            print("  FAIL  " + b)
        return 1
    print("[check] 末年守恒 / 端点连通 / 累计单调 / 底表未改写 / DDL 同步 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
