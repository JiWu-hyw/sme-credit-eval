#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/build_tech_features.py
=========================================================================
由已匹配的 fact_patent 聚合并派生后端数据管道下游的
  - dws_tech_feature  技术要素特征（特征工程字典 3.1–3.7）

这是《后端工作总览》§7「数据管道架构」目标中「特征工程层」的第二步落地，
紧接 cnipa.py（写入 ods_patent_raw）→ match_patents.py（回填 fact_patent）。
本脚本消费 fact_patent 中 enterprise_id 非空的已匹配专利。

---------------------------------------------------------------------
⚠️ 口径说明（务必先读）
---------------------------------------------------------------------
1. 仅聚合 **已匹配** 专利（enterprise_id IS NOT NULL）。未匹配专利不计入，
   避免把"未匹配"误判为"无专利"。
2. 聚合粒度：(enterprise_id, stat_year)，stat_year = 专利申请年(app_year)，
   与《特征工程字典》"按申请年对齐算授权率"一致。
3. 可计算指标（数据到位即落地）：
   · 3.1 inv_patent_ratio  = 发明专利数 / 申请总数
   · 3.2 grant_rate       = 授权数 / (授权+失效+撤回)，剔除未决(pending)
   · 3.3 patent_citation  = 前向被引(n_cite_fwd)均值（全 NULL 时为 NULL）
   · 3.4 tech_breadth     = 不同 IPC 小类覆盖数 + Shannon 熵
   · 原始计数 n_patent_* / n_inventor / n_ipc_subclass / n_patent_collab /
     first_app_year
4. 暂不可计算（依赖外部模型/源），显式留 NULL 并标注：
   · 3.5 patent_triple_score（SciBERT 打分）→ NULL
   · 3.6 core_staff_stability（核心人员稳定性）→ NULL
   · 3.7 rd_intensity（研发投入）→ NULL，rd_intensity_flag='missing'
5. §7.5 零泄漏：技术特征为企业级聚合，与任何标签(label)无关，天然
    leakage-safe；不在企业内随机切分，仅做整企业汇总。

---------------------------------------------------------------------
幂等：重跑安全。先按 computed_at=RUN_TAG 清理本脚本上次写入的
     dws_tech_feature 行，再插入；并写入 meta_ingest_log(stage='build')。

运行：cd sme-credit-eval && python scripts/build_tech_features.py
=========================================================================
"""
from __future__ import annotations

import datetime
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db" / "zjtech_credit.db"
RUN_TAG = "baseline_tech_v1"          # 幂等标记：识别/清理本脚本写入的行


def ipc_subclass(ipc: str | None) -> str | None:
    """IPC 小类 = 符号前 4 位（如 'G06F'）；不足 4 位返回 None。"""
    if not ipc:
        return None
    ipc = ipc.strip().replace(" ", "")
    return ipc[:4] if len(ipc) >= 4 else None


def shannon_entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log(p, 2)
    return round(h, 4)


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] 数据库不存在: {DB_PATH}", file=sys.stderr)
        return 2

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()
    started = datetime.datetime.now().isoformat(timespec="seconds")

    # 0. 幂等清理本脚本上次写入
    cur.execute("DELETE FROM dws_tech_feature WHERE computed_at=?", (RUN_TAG,))
    n_del = cur.rowcount

    # 1. 拉取已匹配专利
    rows = cur.execute(
        """
        SELECT enterprise_id, app_year, app_date, patent_type, is_granted,
               legal_status, n_cite_fwd, ipc_main
        FROM fact_patent
        WHERE enterprise_id IS NOT NULL
        """
    ).fetchall()
    print(f"[INFO] 已匹配专利参与聚合：{len(rows)} 条")

    def resolve_stat_year(app_year, app_date):
        """stat_year 优先用 app_year；缺失时从 app_date(YYYY-MM-DD) 前 4 位推导。"""
        if app_year:
            return int(app_year)
        if app_date and len(app_date) >= 4 and app_date[:4].isdigit():
            return int(app_date[:4])
        return None

    # 2. 按 (enterprise_id, stat_year=app_year) 聚合
    agg: dict[tuple[str, int], dict] = defaultdict(lambda: {
        "n_app": 0, "n_resolved": 0, "n_grant": 0, "n_inv": 0, "n_util": 0,
        "n_design": 0, "n_cited": 0, "cite_sum": 0, "cite_n": 0,
        "subclass": defaultdict(int), "first_app_year": None,
    })

    for ent, app_year, app_date, ptype, is_granted, legal, n_cite_fwd, ipc_main in rows:
        stat_year = resolve_stat_year(app_year, app_date)
        if stat_year is None:
            continue
        a = agg[(ent, stat_year)]
        a["n_app"] += 1
        # 授权率分母：已决专利（剔除未决 pending），见 DDL 3.2
        if legal != "pending":
            a["n_resolved"] += 1
        # 授权 = is_granted=1 或 legal_status='granted'（采集期 is_granted 可能为空）
        granted = (is_granted == 1) or (legal == "granted")
        if granted:
            a["n_grant"] += 1
        if ptype == "invention":
            a["n_inv"] += 1
        elif ptype == "utility":
            a["n_util"] += 1
        elif ptype == "design":
            a["n_design"] += 1
        # 被引
        if n_cite_fwd is not None:
            a["cite_sum"] += n_cite_fwd
            a["cite_n"] += 1
            if n_cite_fwd > 0:
                a["n_cited"] += 1
        # IPC 小类
        sub = ipc_subclass(ipc_main)
        if sub:
            a["subclass"][sub] += 1
        # 首申请年
        if a["first_app_year"] is None or app_year < a["first_app_year"]:
            a["first_app_year"] = app_year

    # 3. 组装 dws_tech_feature 行
    out_rows = []
    for (ent, stat_year), a in agg.items():
        n_app = a["n_app"]
        n_grant = a["n_grant"]
        # 3.2 授权率：分母 = 已决专利（剔除 pending），分子 = 授权数
        grant_rate = round(n_grant / a["n_resolved"], 4) if a["n_resolved"] else None
        inv_ratio = round(a["n_inv"] / n_app, 4) if n_app else None
        patent_citation = round(a["cite_sum"] / a["cite_n"], 4) if a["cite_n"] else None
        subclasses = list(a["subclass"].keys())
        n_ipc_subclass = len(subclasses)
        tech_breadth_entropy = shannon_entropy(list(a["subclass"].values())) \
            if n_ipc_subclass else 0.0
        out_rows.append((
            ent, stat_year,
            inv_ratio,                       # 3.1
            grant_rate,                      # 3.2
            patent_citation,                 # 3.3
            n_ipc_subclass,                  # 3.4 技术广度·IPC 小类覆盖数
            tech_breadth_entropy,            # 3.4 技术广度·Shannon 熵
            None,                            # 3.5 patent_triple_score (SciBERT) → NULL
            None,                            # 3.6 core_staff_stability → NULL
            None,                            # 3.7 rd_intensity → NULL
            "missing",                       # rd_intensity_flag
            n_app,                           # n_patent_app
            n_grant,                         # n_patent_grant
            a["n_inv"],                      # n_patent_invention
            a["n_util"],                     # n_patent_utility
            a["n_design"],                   # n_patent_design
            a["n_cited"],                    # n_patent_cited
            None,                            # n_inventor（inventor_raw 未结构化）
            n_ipc_subclass,                  # n_ipc_subclass
            None,                            # n_patent_collab（单申请人匹配，无法判定合作）
            a["first_app_year"],             # first_app_year
            RUN_TAG,
        ))

    cur.executemany(
        """
        INSERT INTO dws_tech_feature
          (enterprise_id, stat_year, inv_patent_ratio, grant_rate, patent_citation,
           tech_breadth_ipc_class, tech_breadth_entropy, patent_triple_score,
           core_staff_stability, rd_intensity, rd_intensity_flag,
           n_patent_app, n_patent_grant, n_patent_invention, n_patent_utility,
           n_patent_design, n_patent_cited, n_inventor, n_ipc_subclass,
           n_patent_collab, first_app_year, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        out_rows,
    )
    n_out = len(out_rows)
    con.commit()

    # 4. meta_ingest_log 留痕
    finished = datetime.datetime.now().isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO meta_ingest_log
          (source_id, stage, target_table, file_path, file_sha256,
           rows_in, rows_ok, rows_bad, started_at, finished_at, status, message)
        VALUES ('cnipa_ipdps', 'build', 'dws_tech_feature',
                ?, NULL, ?, ?, 0, ?, ?, 'ok', ?)
        """,
        (
            str(DB_PATH), len(rows), n_out, started, finished,
            f"baseline_tech_v1: 聚合 {len(rows)} 条已匹配专利 → "
            f"dws_tech_feature {n_out} 行(清旧 {n_del})；"
            f"3.5/3.6/3.7 留NULL标 missing；企业级聚合，§7.5 零泄漏",
        ),
    )
    con.commit()

    print(f"[OK] dws_tech_feature +{n_out} 行（清旧 {n_del}）")
    print(f"[INFO] 参与聚合已匹配专利 = {len(rows)}")
    print(f"[INFO] 3.5 SciBERT / 3.6 人员稳定性 / 3.7 研发强度 留 NULL（rd_intensity_flag=missing）")
    print(f"[INFO] 未匹配专利、专利/融资依赖列按 schema 留 NULL，待源接入补全")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
