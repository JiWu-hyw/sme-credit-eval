#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/build_features.py
=========================================================================
由已落地的「资质名单 / 企业维度」数据，派生后端数据管道下游的
  - ads_label          标签基线（label_task='qual_survival' 资质存续）
  - dws_growth_feature 成长轨迹基线（资质可派生列；专利/融资依赖列留 NULL）

这是《后端工作总览》§7「数据管道架构」目标中「特征工程层」的 FIRST
落地步。本脚本只消耗**内部已灌数据**（source_id = zj_high_tech），
零外部依赖、可离线运行。

---------------------------------------------------------------------
⚠️ 口径说明（务必先读，避免误用）
---------------------------------------------------------------------
1. ads_label.label_task 受 schema CHECK 约束，仅允许
   ('qual_survival','msme_default','sba_default','survival_risk')。
   - 本脚本只落地 **qual_survival（资质存续）**：以 2021 认定队列为
     cohort（cohort_year=2021），观察至 2024 复核，仍在册=1 / 退出=0。
     这是一个真实、可解释、零外部依赖的标签。
   - msme_default / sba_default / survival_risk 属 P0③（PD/违约口径待
     袁雪茹向数据提供方索要），当前数据无法落地，留待口径确认后补全。
2. dws_growth_feature 中 m_funding/m_team/m_cert/m_cite 是**布尔里程碑
   标志（NULL/0/1）**，不是计数；rev_cagr/emp_cagr/patent_cagr/
   funding_to_last_val/investor_prestige 等依赖外部源（CNIPA/企查查/
   akshare），当前 0 行，故显式留 NULL，待对应源接入后补全。
3. qual_rank 取 dim_enterprise.max_qual_rank（企业级最高资质层级，
   数值越大越高：tech_sme=1…little_giant=4；当前数据均=2 high_tech）。
   qual_rank_delta / qual_upgrade_cum 在当前单资质等级下为 0。
4. 遵守《后端工作总览》§7.5 零泄漏红线：按 enterprise_id 分组计算，
   不在企业内随机切分；本脚本为「标签/特征派生」而非「标准化」，
   不引入跨企业统计量。

---------------------------------------------------------------------
幂等：重跑安全。先按 RUN_TAG 清理本脚本上次写入的 ads_label /
     dws_growth_feature 行，再插入；并写入 meta_ingest_log 留痕。

运行：cd sme-credit-eval && python scripts/build_features.py
=========================================================================
"""
from __future__ import annotations

import datetime
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db" / "zjtech_credit.db"
RUN_TAG = "baseline_qual_v1"          # 幂等标记：识别/清理本脚本写入的行
COHORT_YEARS = (2021, 2024)          # 与 fact_qualification.year 口径一致


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] 数据库不存在: {DB_PATH}", file=sys.stderr)
        return 2

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON")
    cur = con.cursor()
    started = datetime.datetime.now().isoformat(timespec="seconds")

    # ---- 0. 幂等清理本脚本上次写入 ----
    cur.execute("DELETE FROM ads_label WHERE definition_ver=?", (RUN_TAG,))
    n_del_label = cur.rowcount
    cur.execute("DELETE FROM dws_growth_feature WHERE computed_at=?", (RUN_TAG,))
    n_del_growth = cur.rowcount

    # ---- 1. 拉取 企业 × cohort 的资质事实 ----
    qual_rows = cur.execute(
        """
        SELECT enterprise_id, year,
               COUNT(*)            AS n_events,
               MIN(publish_date)   AS obs_start,
               MAX(publish_date)   AS obs_end
        FROM fact_qualification
        WHERE year IN (?, ?)
        GROUP BY enterprise_id, year
        """,
        COHORT_YEARS,
    ).fetchall()

    # 企业级最高资质层级（来自 dim_enterprise）
    rank_map = dict(
        cur.execute("SELECT enterprise_id, max_qual_rank FROM dim_enterprise").fetchall()
    )
    # 所有候选企业（名单内）
    ent_ids = [r[0] for r in cur.execute("SELECT enterprise_id FROM dim_enterprise").fetchall()]

    # qual[(ent, year)] = (n_events, obs_start, obs_end)
    qual = {(e, y): (n, s, e2) for (e, y, n, s, e2) in qual_rows}

    # ---- 2. ads_label：qual_survival（资质存续）----
    # 仅对 2021 认定队列（cohort_year=2021）打标，因其有后续 2024 复核观察窗。
    label_rows = []
    for ent in ent_ids:
        if (ent, 2021) not in qual:
            continue
        n21, s21, e21 = qual[(ent, 2021)]
        survived = 1 if (ent, 2024) in qual else 0
        obs_end = qual[(ent, 2024)][2] if survived else None
        label_rows.append(
            (
                ent,
                "qual_survival",
                2021,
                survived,
                "资质存续" if survived else "资质退出（未通过复核）",
                s21,
                obs_end,
                RUN_TAG,
                n21,
                "baseline: 2021 认定企业至 2024 复核是否仍在册；"
                "msme_default/sba_default/survival_risk 待 P0③ 口径确认",
            )
        )
    cur.executemany(
        """
        INSERT INTO ads_label
          (enterprise_id, label_task, cohort_year, label_value, label_name,
           observation_start, observation_end, definition_ver, n_events, note)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        label_rows,
    )
    n_label = len(label_rows)

    # ---- 3. dws_growth_feature（资质派生列；其余留 NULL）----
    growth_rows = []
    for ent in ent_ids:
        rank = rank_map.get(ent)
        has21 = (ent, 2021) in qual
        has24 = (ent, 2024) in qual
        # 复核未过：2021 认定但 2024 不在名单（退出/复核失败）
        ever_fail = 1 if (has21 and not has24) else 0
        for y in COHORT_YEARS:
            if (ent, y) not in qual:
                continue
            growth_rows.append(
                (
                    ent, y,
                    None,   # rev_cagr            待营收源
                    None,   # emp_cagr            待招聘源
                    None,   # patent_cagr         待专利源
                    None,   # funding_to_last_val 待融资源
                    None,   # m_funding           待融资源（布尔）
                    None,   # m_team              待招聘源（布尔）
                    1,      # m_cert             资质里程碑（布尔，本 cohort 有认定）
                    None,   # m_cite             待专利源（布尔）
                    None,   # investor_prestige  待融资源
                    rank,   # qual_rank          企业级最高层级（当前均=2 high_tech）
                    0,      # qual_rank_delta    当前单资质等级无跨年变化
                    0,      # qual_upgrade_cum   当前无晋级
                    ever_fail,
                    RUN_TAG,
                )
            )
    cur.executemany(
        """
        INSERT INTO dws_growth_feature
          (enterprise_id, stat_year, rev_cagr, emp_cagr, patent_cagr,
           funding_to_last_val, m_funding, m_team, m_cert, m_cite,
           investor_prestige, qual_rank, qual_rank_delta, qual_upgrade_cum,
           ever_fail_review, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        growth_rows,
    )
    n_growth = len(growth_rows)

    con.commit()

    # ---- 4. meta_ingest_log 留痕 ----
    finished = datetime.datetime.now().isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO meta_ingest_log
          (source_id, stage, target_table, file_path, file_sha256,
           rows_in, rows_ok, rows_bad, started_at, finished_at, status, message)
        VALUES ('zj_high_tech', 'build', 'ads_label+dws_growth_feature',
                ?, NULL, ?, ?, 0, ?, ?, 'ok', ?)
        """,
        (
            str(DB_PATH),
            len(ent_ids),
            n_label + n_growth,
            started,
            finished,
            f"baseline_qual_v1: ads_label(qual_survival)={n_label}行(清旧 {n_del_label}), "
            f"dws_growth_feature={n_growth}行(清旧 {n_del_growth}); "
            f"专利/融资依赖列显式留NULL待源接入",
        ),
    )
    con.commit()

    print(f"[OK] ads_label(qual_survival) +{n_label} 行（清旧 {n_del_label}）")
    print(f"[OK] dws_growth_feature       +{n_growth} 行（清旧 {n_del_growth}）")
    print(f"[INFO] 企业候选数 = {len(ent_ids)}；cohort = {COHORT_YEARS}")
    print(f"[INFO] 专利/融资依赖列已显式留 NULL，待 CNIPA / 企查查源接入后补全")
    print(f"[INFO] msme_default/sba_default/survival_risk 属 P0③，待袁雪茹确认口径")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
