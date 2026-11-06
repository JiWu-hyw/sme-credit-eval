"""融资特征 as-of 补算（v1.6，依据评审报告 §2.3(3) 回落核查）。

为什么有这个脚本：
  build_asof_features.py 把 m_funding / investor_prestige 归入 NOT_ASOFABLE，
  理由写「本地无时间维」「本地无数据」。但 fact_funding_event.event_year
  506/506 全填、prestige_rank 352/506 有值 —— 报告 §2.3(3) 也明说
  「融资维度可做『有没有融过、融了几轮』」。这是能算而没算。

口径（与既有 as-of 层一致）：
  · as_of_year = cohort_year（cohort 主口径）或 cohort_year + 3 - 1（due_minus_1）
  · 窗口 = event_year <= as_of_year（事件年不晚于观测截止年）
  · m_funding 0/1、n_funding_events 计数（「融了几轮」）
  · investor_prestige_best / _latest：窗口内最好 / 最近一次的机构等级
    （1=头部 VC … 4=个人天使；NULL=窗口内无带等级事件，与 0 语义不同）

边界与诚实声明：
  · 覆盖有限：打标 307 家中 cohort 口径 60 家有事件（19.5%）、
    due_minus_1 口径 95 家（31%）。这不是随机子集——能融资的企业
    本身就更活跃，缺失机制与「企业质量」相关（MNAR）。
  · 本表**不进 v_model_train_ready**（那是 17 列既有口径，动列要升版），
    需要时 LEFT JOIN v_feature_asof_funding。
  · prestige_rank 来源：仅上市公司子集可得（AKShare），
    字典口径为「机构知名度等级（1 头部 VC … 4 个人天使）」。

用法：
  python -m src.data.build_asof_funding [--dry-run] [--db <path>]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRATION = os.path.join(BACKEND, "src", "data", "migrations", "v1_6_funding.sql")

VALID_YEARS = 3


def rebuild(conn: sqlite3.Connection, verbose: bool = True) -> dict:
    t0 = time.time()
    cur = conn.cursor()

    cur.executescript(open(MIGRATION, encoding="utf-8").read())

    # as-of 锚点：直接取 ads_feature_asof 里已固化的锚点，
    # 保证与主 as-of 层的 cohort_year / as_of_year 一字不差。
    anchors = cur.execute(
        "SELECT DISTINCT enterprise_id, as_of_policy, cohort_year, as_of_year "
        "FROM ads_feature_asof").fetchall()

    # 融资事件按企业读入
    events = {}
    for eid, yr, rank in cur.execute(
        "SELECT enterprise_id, event_year, prestige_rank FROM fact_funding_event "
        "WHERE event_year IS NOT NULL"):
        events.setdefault(eid, []).append((int(yr), rank))

    rows = []
    for eid, policy, cohort_year, as_of_year in anchors:
        # prestige_rank 可能是 None；排序键用 (year, rank is None, rank) 保证可比
        evs = sorted(events.get(eid, []),
                     key=lambda e: (e[0], e[1] is None, e[1] or 0))
        in_win = [e for e in evs if e[0] <= as_of_year]
        ranked = [e for e in in_win if e[1] is not None]
        m_funding = 1 if in_win else 0
        n_ev = len(in_win)
        best = min((int(e[1]) for e in ranked), default=None)
        latest = ranked[-1][1] if ranked else None
        rows.append((eid, policy, cohort_year, as_of_year,
                     m_funding, n_ev,
                     best, int(latest) if latest is not None else None))

    cur.executemany(
        "INSERT OR REPLACE INTO ads_feature_asof_funding "
        "(enterprise_id, as_of_policy, cohort_year, as_of_year, "
        " m_funding, n_funding_events, investor_prestige_best, investor_prestige_latest) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()

    # 汇总
    stats = {}
    for policy in ("cohort", "due_minus_1"):
        r = cur.execute(
            "SELECT COUNT(*), SUM(m_funding), SUM(n_funding_events), "
            "SUM(CASE WHEN investor_prestige_best IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM ads_feature_asof_funding WHERE as_of_policy=?", (policy,)).fetchone()
        n_lab = cur.execute(
            "SELECT COUNT(*) FROM ads_feature_asof_funding WHERE as_of_policy=?",
            (policy,)).fetchone()[0]
        stats[policy] = {"n": n_lab, "n_with_funding": r[1] or 0,
                         "n_events": r[2] or 0, "n_with_prestige": r[3] or 0}
    summary = {"rows": len(rows), "by_policy": stats,
               "elapsed_sec": round(time.time() - t0, 1)}
    if verbose:
        for p, s in stats.items():
            print("[asof-funding] %-12s %d 家 / 有融资 %d / 事件 %d 次 / 带机构等级 %d 家"
                  % (p, s["n"], s["n_with_funding"], s["n_events"], s["n_with_prestige"]))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="融资特征 as-of 补算（v1.6）")
    ap.add_argument("--db", default=os.path.join(
        BACKEND, "data", "db", "zjtech_credit.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    try:
        if args.dry_run:
            cur = conn.cursor()
            anchors = cur.execute(
                "SELECT DISTINCT enterprise_id, as_of_policy, as_of_year "
                "FROM ads_feature_asof").fetchall()
            by = {}
            for eid, policy, asy in anchors:
                n = cur.execute(
                    "SELECT COUNT(*) FROM fact_funding_event "
                    "WHERE enterprise_id=? AND event_year<=?", (eid, asy)).fetchone()[0]
                by.setdefault(policy, [0, 0])
                by[policy][0] += 1
                if n:
                    by[policy][1] += 1
            for p, (tot, hv) in by.items():
                print("[dry-run] %-12s %d 家锚点，其中窗口内有融资事件 %d 家" % (p, tot, hv))
            return 0
        s = rebuild(conn)
        print("[asof-funding] 完成：%d 行" % s["rows"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
