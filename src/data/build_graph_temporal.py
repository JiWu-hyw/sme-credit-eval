# -*- coding: utf-8 -*-
"""v3.0 时序图物化（可复现）：从物理图 ads_graph_node/edge 重建两张时序表。

背景：v2.2 的 ads_graph_node_ts / ads_graph_edge_ts 由外部 ETL 手工物化，
没有可复现脚本；v3.0 把图谱重建到 3000 家（17.8万节点/28.5万边）后，
旧时序表（6.1万节点/17.1万边）陈旧，必须重建。

口径（严格沿用 v2.2 定稿，保证可比）：
  · 边 files_patent / belongs_ipc / invented_by：按关联专利的 app_year
    （dst 或 src 中的 patent 节点 → fact_patent.patent_id 的 app_year）
  · 边 funded_by：按「企业+投资机构」对应的融资事件年（fact_funding_event.investor_names JSON）
  · 边 tech_similar：global_snapshot，time_slice=-1（全期快照，不进年度累计）
  · 节点 patent：app_year；节点 enterprise/inventor/ipc/investor：
    关联边的 min(时点年)（edge_min_year）；无边则取该类型全局最小年
  · 时序化率：tech_similar 全期快照，其余四类按时点年；time_slice=-1 仅 tech_similar

纪律：只重建两张 _ts 表（TRUNCATE + INSERT），不动主图/标签/已发布视图。
幂等：结果只依赖物理图与事实表，可重复执行。

用法：
    python -m src.data.build_graph_temporal            # 重建
    python -m src.data.build_graph_temporal --check    # 只读体检
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DB = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")


def build(con):
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys=OFF")

    # 1) 专利节点年份映射
    pyear = {}
    for pid, yr in con.execute(
            "SELECT patent_id,app_year FROM fact_patent WHERE app_year IS NOT NULL"):
        pyear[pid] = yr

    # 2) 融资事件「企业+机构组合串」→ 年份（取最早一次）
    # build_graph_v2 把一次融资事件的全部机构用 '|' 连成一个 investor 节点，
    # 故 display_name 可能是一个机构名，也可能是「A|B|C」组合串；两种原文都要登记。
    fund_year = {}
    for eid, names, yr in con.execute(
            "SELECT enterprise_id,investor_names,event_year FROM fact_funding_event "
            "WHERE investor_names IS NOT NULL AND event_year IS NOT NULL"):
        if not yr:
            continue
        raw = str(names).strip()
        variants = {raw}
        try:
            invs = json.loads(raw)
            if isinstance(invs, list):
                variants.update(str(x).strip() for x in invs)
                variants.add("|".join(str(x).strip() for x in invs))
        except (ValueError, TypeError):
            pass
        if "|" in raw:
            variants.update(x.strip() for x in raw.split("|"))
        for v in variants:
            k = (eid, v)
            if k not in fund_year or yr < fund_year[k]:
                fund_year[k] = yr

    # 3) 投资机构 node_id → display_name（组合串原文）
    inv_name_by_id = {}
    for nid, name in con.execute(
            "SELECT node_id,display_name FROM ads_graph_node WHERE node_type='investor'"):
        inv_name_by_id[nid] = name

    # 4) 复用旧 ts 表 funded_by 的年份（原 500 家 583 条，保持历史口径不变）
    legacy_fund = {}
    try:
        for eid, iid, yr in con.execute(
                "SELECT src_id,dst_id,time_slice FROM ads_graph_edge_ts "
                "WHERE edge_type='funded_by' AND time_slice>0"):
            legacy_fund[(eid, iid)] = yr
    except sqlite3.OperationalError:
        pass

    # 5) 逐边计算时点年
    edge_rows = []
    edge_min_year = {}  # (node_type,node_id) -> min 年
    for s_type, s_id, d_type, d_id, etype, weight in con.execute(
            "SELECT src_type,src_id,dst_type,dst_id,edge_type,weight FROM ads_graph_edge"):
        if etype == "tech_similar":
            yr, src = -1, "global_snapshot"
        elif etype == "funded_by":
            yr = legacy_fund.get((s_id, d_id))
            if yr is None:
                # 从新融资事件反查：企业 + 机构节点 display_name（组合串原文）→ 年份
                inv_name = inv_name_by_id.get(d_id)
                yr = fund_year.get((s_id, inv_name)) if inv_name else None
            yr = yr if yr else None
            src = "funding_event_year" if yr else "global_snapshot"
        else:
            pid = d_id if d_type == "patent" else (s_id if s_type == "patent" else None)
            yr = pyear.get(pid) if pid else None
            src = "patent_app_year" if yr else "global_snapshot"
        # 无年份的边：tech_similar 保持 -1，其余按全期快照 -1（避免污染年度累计）
        if yr is None:
            yr = -1
        edge_rows.append((s_type, s_id, d_type, d_id, etype, yr, weight, src))
        if yr > 0:
            for nt, nid in ((s_type, s_id), (d_type, d_id)):
                k = (nt, nid)
                if k not in edge_min_year or yr < edge_min_year[k]:
                    edge_min_year[k] = yr

    # 6) 节点时点年
    node_rows = []
    for ntype, nid, name in con.execute(
            "SELECT node_type,node_id,display_name FROM ads_graph_node"):
        if ntype == "patent":
            yr, src = pyear.get(nid), "patent_app_year"
        else:
            yr, src = edge_min_year.get((ntype, nid)), "edge_min_year"
        if not yr:
            yr = -1
            src = "global_snapshot" if ntype != "patent" else "patent_app_year"
        node_rows.append((ntype, nid, yr, name, src))

    # 7) 重建两张表
    cur.execute("DELETE FROM ads_graph_node_ts")
    cur.execute("DELETE FROM ads_graph_edge_ts")
    con.executemany(
        "INSERT INTO ads_graph_node_ts(node_type,node_id,time_slice,display_name,year_source)"
        " VALUES(?,?,?,?,?)", node_rows)
    con.executemany(
        "INSERT INTO ads_graph_edge_ts(src_type,src_id,dst_type,dst_id,edge_type,"
        "time_slice,weight,year_source) VALUES(?,?,?,?,?,?,?,?)", edge_rows)
    con.commit()
    return len(node_rows), len(edge_rows)


def check(con):
    cur = con.cursor()
    n_node = cur.execute("SELECT COUNT(*) FROM ads_graph_node_ts").fetchone()[0]
    n_edge = cur.execute("SELECT COUNT(*) FROM ads_graph_edge_ts").fetchone()[0]
    n_phy_node = cur.execute("SELECT COUNT(*) FROM ads_graph_node").fetchone()[0]
    n_phy_edge = cur.execute("SELECT COUNT(*) FROM ads_graph_edge").fetchone()[0]
    tm = cur.execute(
        "SELECT COUNT(*) FROM ads_graph_edge_ts WHERE edge_type='tech_similar'"
    ).fetchone()[0]
    print("  节点 ts=%d / 物理=%d %s" % (n_node, n_phy_node, "OK" if n_node == n_phy_node else "MISMATCH"))
    print("  边   ts=%d / 物理=%d %s" % (n_edge, n_phy_edge, "OK" if n_edge == n_phy_edge else "MISMATCH"))
    print("  tech_similar 全期快照 %d 条" % tm)
    cur.execute("SELECT edge_type,year_source,COUNT(*),MIN(time_slice),MAX(time_slice) "
                "FROM ads_graph_edge_ts GROUP BY 1,2 ORDER BY 1,2")
    for r in cur.fetchall():
        print("   ", r)
    years = [r[0] for r in cur.execute(
        "SELECT DISTINCT time_slice FROM ads_graph_edge_ts WHERE time_slice>0 ORDER BY 1")]
    print("  年度帧数 %d（%s–%s）" % (len(years), years[0] if years else "-", years[-1] if years else "-"))
    return n_node == n_phy_node and n_edge == n_phy_edge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        ok = check(con)
        con.close()
    else:
        con = sqlite3.connect(DB)
        nn, ne = build(con)
        print("时序表重建：节点 %d / 边 %d" % (nn, ne))
        ok = check(con)
        con.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
