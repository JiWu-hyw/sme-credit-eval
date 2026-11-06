"""异质图重建 v2：5 类节点 / 7 类边 / 权重区分度改进。

背景（数据交付包评审报告 XM-2026-RPT-001，§5.2「三条结构性缺陷」）：
  1) 预置图只有 3 类节点（企业 / 专利 / IPC），立项书要求的六类实体中
     人员（dim_inventor 15,955）、投资机构（fact_funding_event 349 家）都没有入图；
  2) tech_similar 边权重区分度低（最小 0.10、中位 0.138、max 1.0，>=0.3 仅 663 条），
     直接当注意力权重会使消息传递近似平均池化；
  3) 另发现一个报告未提及的真实缺陷：files_patent 边只有 29,751 条，
     而 fact_patent 中有 32,203 件专利已匹配企业 —— 差 2,452 条，
     原因是 rematch_unmatched_patents.py 补匹配后没有重建图，边表陈旧。

本脚本一次性重建 ads_graph_node / ads_graph_edge：
  · 节点 5 类：enterprise / patent / ipc / inventor / investor
  · 边   6 类：files_patent / belongs_ipc / invented_by / funded_by / tech_similar
              （co_inventor / cites / qualifies 保留枚举，本脚本不产出）
  · tech_similar 由「IPC 小类集合 Jaccard 全量连边」改为
    「IPC 小类 TF-IDF 向量余弦 + 每企业 top-K 邻居」，权重分布显著拉开。

口径：
  · time_slice 统一 0 = 全期累计（与既有契约一致）。年度切片不物化，
    由 v_graph_patent_timeline 给出各 T 的规模，算法端按 app_year <= T 自建即可。
  · 幂等：可重复执行，结果只取决于底表。

用法：
  python -m src.data.build_graph_v2 [--db data/db/zjtech_credit.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from hashlib import md5

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

NODE_TYPES = ("enterprise", "patent", "ipc", "inventor", "investor")
EDGE_TYPES = ("files_patent", "belongs_ipc", "invented_by", "funded_by",
              "co_inventor", "cites", "qualifies", "tech_similar")

TIME_SLICE = 0            # 0 = 全期累计
SIM_TOPK = 20             # 每家企业保留的相似邻居数
SIM_MIN_COS = 0.05        # 余弦下限（低于此值视为无技术关联）
SIM_MIN_SHARED = 2        # 至少共享 2 个 IPC 小类，避免单点巧合

NODE_DDL = """
CREATE TABLE {name} (
    node_type    TEXT NOT NULL CHECK (node_type IN
                   ('enterprise','patent','ipc','inventor','investor')),
    node_id      TEXT NOT NULL,
    time_slice   INTEGER NOT NULL,
    display_name TEXT,
    PRIMARY KEY (node_type, node_id, time_slice)
) STRICT
"""

EDGE_DDL = """
CREATE TABLE {name} (
    src_type   TEXT NOT NULL CHECK (src_type IN
                 ('enterprise','patent','ipc','inventor','investor')),
    src_id     TEXT NOT NULL,
    dst_type   TEXT NOT NULL CHECK (dst_type IN
                 ('enterprise','patent','ipc','inventor','investor')),
    dst_id     TEXT NOT NULL,
    edge_type  TEXT NOT NULL CHECK (edge_type IN
                 ('files_patent','belongs_ipc','invented_by','funded_by',
                  'co_inventor','cites','qualifies','tech_similar')),
    time_slice INTEGER NOT NULL,
    weight     REAL,
    PRIMARY KEY (src_type, src_id, dst_type, dst_id, edge_type, time_slice)
) STRICT
"""


def investor_id(name: str) -> str:
    """投资机构 ID：与 ENT/INV 同风格的前缀 + 10 位 hex。"""
    return "IVS" + md5(name.encode("utf-8")).hexdigest()[:10].upper()


def _parse_investors(raw) -> list[str]:
    """fact_funding_event.investor_names 是 JSON 数组字符串。"""
    if not raw:
        return []
    s = str(raw).strip()
    if not s:
        return []
    try:
        arr = json.loads(s)
        if isinstance(arr, list):
            return [str(x).strip() for x in arr if str(x).strip()]
        return [str(arr).strip()]
    except Exception:
        # 兜底：按中英文逗号/分号切
        for sep in ("；", ";", "、", "，"):
            s = s.replace(sep, ",")
        return [x.strip() for x in s.split(",") if x.strip()]


def build_similarity(ent_subclass: dict[str, Counter]) -> list[tuple]:
    """IPC 小类 TF-IDF 向量余弦 + top-K 邻居。

    相比原 Jaccard 全量连边（min=0.10 硬门槛 → 权重被压在 0.10~0.25），
    TF-IDF 会压低「人人都有」的通用小类、抬高高专精度小类，
    再叠加 top-K 截断，权重分布呈明显长尾，可直接作为注意力权重。
    """
    ents = sorted(ent_subclass)
    n = len(ents)
    if n == 0:
        return []
    df = Counter()
    for e in ents:
        for sc in ent_subclass[e]:
            df[sc] += 1
    idf = {sc: math.log((1 + n) / (1 + c)) + 1.0 for sc, c in df.items()}

    vecs: dict[str, dict[str, float]] = {}
    for e in ents:
        v = {}
        for sc, c in ent_subclass[e].items():
            v[sc] = (1.0 + math.log(c)) * idf.get(sc, 1.0)
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs[e] = {k: x / norm for k, x in v.items()}

    out = []
    for i, a in enumerate(ents):
        va = vecs[a]
        sims = []
        for b in ents[i + 1:]:
            vb = vecs[b]
            shared = len(va.keys() & vb.keys())
            if shared < SIM_MIN_SHARED:
                continue
            small, big = (va, vb) if len(va) <= len(vb) else (vb, va)
            cos = sum(w * big.get(k, 0.0) for k, w in small.items())
            if cos >= SIM_MIN_COS:
                sims.append((cos, b))
        sims.sort(reverse=True)
        for cos, b in sims[:SIM_TOPK]:
            out.append(("enterprise", a, "enterprise", b, "tech_similar",
                        TIME_SLICE, round(cos, 4)))
    return out


def rebuild(conn: sqlite3.Connection, verbose: bool = True) -> dict:
    t0 = time.time()
    cur = conn.cursor()

    # 依赖图表的视图（v_data_coverage 等）必须先卸下，否则 DROP TABLE 会报
    # 「error in view ... no such table」。改名后再按原 DDL 原样建回。
    dep_views = [(r[0], r[1]) for r in cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='view' "
        "AND sql LIKE '%ads_graph_%' AND name NOT LIKE 'v_graph_%'").fetchall()]
    for name, _ in dep_views:
        cur.execute("DROP VIEW IF EXISTS %s" % name)

    cur.execute("DROP TABLE IF EXISTS ads_graph_node_v2")
    cur.execute("DROP TABLE IF EXISTS ads_graph_edge_v2")
    cur.execute(NODE_DDL.format(name="ads_graph_node_v2"))
    cur.execute(EDGE_DDL.format(name="ads_graph_edge_v2"))

    enterprises = cur.execute(
        "SELECT enterprise_id, name_display FROM dim_enterprise").fetchall()
    patents = cur.execute(
        "SELECT patent_id, title, enterprise_id, COALESCE(match_confidence,1.0) "
        "FROM fact_patent").fetchall()
    bridges = cur.execute(
        "SELECT patent_id, ipc_code, ipc_subclass, is_main FROM bridge_patent_ipc").fetchall()
    inventors = cur.execute(
        "SELECT inventor_id, name_display FROM dim_inventor").fetchall()
    inv_edges = cur.execute(
        "SELECT patent_id, inventor_id, is_first FROM bridge_patent_inventor").fetchall()
    fundings = cur.execute(
        "SELECT enterprise_id, investor_names FROM fact_funding_event "
        "WHERE investor_names IS NOT NULL AND TRIM(investor_names) <> ''").fetchall()
    ipc_title = {r[0]: (r[1] or "") for r in cur.execute(
        "SELECT ipc_code, title FROM dim_ipc").fetchall()}

    # ---------- 节点 ----------
    node_rows = []
    for e in enterprises:
        node_rows.append(("enterprise", e[0], TIME_SLICE, (e[1] or "")[:60]))
    for p in patents:
        node_rows.append(("patent", p[0], TIME_SLICE, (p[1] or "")[:80]))
    used_ipc = sorted({b[1] for b in bridges})
    for code in used_ipc:
        node_rows.append(("ipc", code, TIME_SLICE, ipc_title.get(code, "")[:60]))
    for i in inventors:
        node_rows.append(("inventor", i[0], TIME_SLICE, (i[1] or "")[:60]))

    investor_names = {}
    for f in fundings:
        for nm in _parse_investors(f[1]):
            investor_names.setdefault(investor_id(nm), nm)
    for iid, nm in investor_names.items():
        node_rows.append(("investor", iid, TIME_SLICE, nm[:80]))

    cur.executemany(
        "INSERT OR IGNORE INTO ads_graph_node_v2 "
        "(node_type, node_id, time_slice, display_name) VALUES (?,?,?,?)", node_rows)

    # ---------- 边 ----------
    edge_rows = []
    pat2ent = {}
    for p in patents:
        if p[2]:
            pat2ent[p[0]] = p[2]
            edge_rows.append(("enterprise", p[2], "patent", p[0],
                              "files_patent", TIME_SLICE, float(p[3] or 1.0)))
    for b in bridges:
        edge_rows.append(("patent", b[0], "ipc", b[1], "belongs_ipc",
                          TIME_SLICE, 1.0 if b[3] else 0.5))
    for e in inv_edges:
        edge_rows.append(("patent", e[0], "inventor", e[1], "invented_by",
                          TIME_SLICE, 1.0 if e[2] else 0.5))

    fund_pairs = set()
    for f in fundings:
        if not f[0]:
            continue
        for nm in _parse_investors(f[1]):
            fund_pairs.add((f[0], investor_id(nm)))
    for eid, iid in sorted(fund_pairs):
        edge_rows.append(("enterprise", eid, "investor", iid, "funded_by",
                          TIME_SLICE, 1.0))

    cur.executemany(
        "INSERT OR IGNORE INTO ads_graph_edge_v2 "
        "(src_type, src_id, dst_type, dst_id, edge_type, time_slice, weight) "
        "VALUES (?,?,?,?,?,?,?)", edge_rows)

    # ---------- 企业技术相似（新权重）----------
    ent_subclass = defaultdict(Counter)
    for b in bridges:
        eid = pat2ent.get(b[0])
        if eid and b[2]:
            ent_subclass[eid][b[2]] += 1
    sim_rows = build_similarity(dict(ent_subclass))
    cur.executemany(
        "INSERT OR IGNORE INTO ads_graph_edge_v2 "
        "(src_type, src_id, dst_type, dst_id, edge_type, time_slice, weight) "
        "VALUES (?,?,?,?,?,?,?)", sim_rows)

    # ---------- 替换旧表 ----------
    cur.execute("DROP TABLE IF EXISTS ads_graph_node")
    cur.execute("DROP TABLE IF EXISTS ads_graph_edge")
    cur.execute("ALTER TABLE ads_graph_node_v2 RENAME TO ads_graph_node")
    cur.execute("ALTER TABLE ads_graph_edge_v2 RENAME TO ads_graph_edge")
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_gnode_slice ON ads_graph_node(node_type, time_slice)",
        "CREATE INDEX IF NOT EXISTS idx_gedge_src ON ads_graph_edge(src_type, src_id, time_slice)",
        "CREATE INDEX IF NOT EXISTS idx_gedge_dst ON ads_graph_edge(dst_type, dst_id, time_slice)",
        "CREATE INDEX IF NOT EXISTS idx_gedge_typ ON ads_graph_edge(edge_type, time_slice)",
    ):
        cur.execute(sql)
    for _, sql in dep_views:
        cur.execute(sql)
    conn.commit()

    nodes_by_type = dict(cur.execute(
        "SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY node_type").fetchall())
    edges_by_type = dict(cur.execute(
        "SELECT edge_type, COUNT(*) FROM ads_graph_edge GROUP BY edge_type").fetchall())
    n_matched = cur.execute(
        "SELECT COUNT(*) FROM fact_patent WHERE enterprise_id IS NOT NULL "
        "AND enterprise_id <> ''").fetchone()[0]
    n_files = edges_by_type.get("files_patent", 0)

    summary = {
        "time_slice": TIME_SLICE,
        "n_nodes": sum(nodes_by_type.values()),
        "n_edges": sum(edges_by_type.values()),
        "nodes_by_type": nodes_by_type,
        "edges_by_type": edges_by_type,
        "matched_patents": n_matched,
        "files_patent_edges": n_files,
        "stale_gap": n_matched - n_files,
        "n_enterprises_with_patent": len(pat2ent.values()) and len(set(pat2ent.values())),
        "sim_topk": SIM_TOPK,
        "sim_min_cos": SIM_MIN_COS,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    if verbose:
        print("[graph-v2] nodes=%d %s" % (summary["n_nodes"], nodes_by_type))
        print("[graph-v2] edges=%d %s" % (summary["n_edges"], edges_by_type))
        print("[graph-v2] files_patent 对账：匹配专利 %d / 边 %d / 缺口 %d"
              % (n_matched, n_files, summary["stale_gap"]))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="异质图 v2 重建（5 类节点 / 7 类边）")
    ap.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "db", "zjtech_credit.db"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        if args.dry_run:
            cur = conn.cursor()
            n_matched = cur.execute(
                "SELECT COUNT(*) FROM fact_patent WHERE enterprise_id IS NOT NULL "
                "AND enterprise_id <> ''").fetchone()[0]
            n_files = cur.execute(
                "SELECT COUNT(*) FROM ads_graph_edge WHERE edge_type='files_patent'"
            ).fetchone()[0]
            print("[dry-run] 匹配专利 %d / 现有 files_patent 边 %d / 缺口 %d"
                  % (n_matched, n_files, n_matched - n_files))
            return 0
        s = rebuild(conn)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(s, fh, ensure_ascii=False, indent=2)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
