"""
补算 6 个「原料已在、只差计算」的建模特征（幂等、可重跑、只 UPDATE 不重建）。

覆盖字段
--------
| 字段 | 落库表 | 事实源 | 口径 |
|---|---|---|---|
| tech_breadth_entropy | dws_tech_feature | bridge_patent_ipc | 企业 IPC 小类分布的 Shannon 熵(log2) |
| core_staff_stability | dws_tech_feature | bridge_patent_inventor | 1 − 近 3 年核心发明人平均年流失率 |
| qual_rank_delta      | dws_growth_feature | fact_qualification | 观测窗内已观测到的层级净跃升 |
| qual_upgrade_cum     | dws_growth_feature | fact_qualification | 观测窗内已观测到的晋级次数 |
| m_cert               | dws_growth_feature | fact_qualification | 最近观测年度是否发生资质获取/重新认定(0/1) |
| tech_conversion      | dws_fusion_feature | dws + bridge_patent_inventor | 有效专利数 / 研发人员数下界（代理） |

执行完会同步刷新 ads_model_dataset 的这 6 列，否则宽表仍是 NULL。

口径边界（重要，勿擅自放宽）
---------------------------
1. **无失效降级**：fact_qualification 的 status 全为 announced，本地没有撤销 /
   复核未通过 / 注销公告，且年度名单是「新认定增量」而非存量有效名单。
   因此 qual_* 只统计「有名单证据的正向晋级」，**不构造 −n 的失效降级**。
   也因为只有 high_tech(2) / specialized(3) 两类资质，这两个字段区分度天然有限。
2. **stat_year 快照**：当前 dws 三表均为企业 × 单年(2026)全量聚合快照，
   不是逐年面板。qual_rank_delta 因此填「全观测窗净跃升」而非「某一当年 Δ」，
   qual_upgrade_cum 填全窗累计次数，与快照粒度保持一致。
3. **发明人 ≠ 人事名册**：核心人员以「曾为第一发明人(is_first=1)」界定；
   某年未出现在专利申请中不等于离职，churn 会被系统性高估。
   因此 core_staff_stability 是**代理指标**，不等同真实任职稳定度。
   只在企业相邻两年均有专利申请观测时才计算该年 churn，避免把
   「当年没申请专利」误判为「全员离职」；无可算年份则留 NULL，不填 0/1。
4. **tech_conversion 代理**：rev_cagr / emp_cagr / funding_to_last_val 全空
   （无公开财报），字典 5.1 首选口径「营收 CAGR / 专利 CAGR」不可算，
   故采用字典允许的替代口径「授权专利数 / 研发人员数」：
   分子 = dws_tech_feature.n_patent_grant（500 家主表「有效专利数(我方)」）；
   分母 = max(桥表按企业去重发明人数, dws_tech_feature.n_inventor)。
   两个分母来源都低估研发人员数：主表 n_inventor 有 233 家被截断在 20，
   桥表仅含已匹配专利且 41 家 ≤2 人；取较大值作为下界可避免 /1 型伪极端值。
   即便如此仍是粗代理，入模前建议 winsorize 或 log1p，不宜单独做业务解释。
5. **潜在后视泄漏（已另行解决）**：以上均为企业级全量聚合，与既有 dws
   特征口径一致，但 ads_label 是 2021/2022 队列、到期年 2024/2025。
   本脚本只补齐字段，**不更改既有宽表时间口径**；无泄漏入口由
   `build_asof_features.py` 提供（`ads_feature_asof` +
   `v_model_dataset_asof_flat`，主口径 `as_of_policy='cohort'`）。
   特别是本脚本产出的 m_cert 取 2025 年度，实测与标签直接重合，
   **禁止用于训练**，只作为泄漏基线留存。
6. 无数据一律留 NULL，不用 0 填充；0 是真实观测值（如单小类熵 = 0）。
7. **m_cert 用最近观测年度、不用全窗**：字典 4.5 里程碑标志本意是喂给
   时空图谱的年度时间切片。若按「全观测窗是否拿过资质」，因 500 家在
   2023 年都有 specialized 记录，结果会 500/500 全为 1 —— 常量列、零信息量；
   按近 3 年同样全为 1。故取 fact_qualification 的最大年度（当前 2025），
   以「该年度是否有资质记录」判定，得 140 家 =1 / 360 家 =0。
   ⚠️ 年度名单是「新认定/备案增量」，m_cert=0 只表示**当年未观测到资质公告**，
   绝不表示「企业无资质」或「资格失效」。窗口与现有 m_funding（全窗口径）
   不一致，已在 NOTES / ingest log 标注；全窗晋级信息由 qual_upgrade_cum 承载。

用法
----
    python -m src.data.build_derived_features                    # 补算 6 列 + 刷新宽表
    python -m src.data.build_derived_features --dry-run          # 只预览，不写库
    python -m src.data.build_derived_features --no-refresh-wide  # 不刷新宽表
    python -m src.data.build_derived_features --db <path>        # 指定库

升版规则：字典口径变更、补齐撤销公告、或接入财报后，必须升 DERIVED_VER 并重跑，
不得原地改写旧口径。
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[2]
DB_PATH = _BACKEND / "data" / "db" / "zjtech_credit.db"

DERIVED_VER = "derived_features_v0.2"       # v0.2 新增 m_cert（年度里程碑口径）
SOURCE_ID = "zj_500family"
LOG_STAGE = "build"
LOG_TARGET = "dws_tech/growth/fusion_feature+ads_model_dataset"
STABILITY_WINDOW = (2023, 2024, 2025)        # 字典 3.6「近 3 年」
FEATURES = (
    "tech_breadth_entropy",
    "core_staff_stability",
    "qual_rank_delta",
    "qual_upgrade_cum",
    "m_cert",
    "tech_conversion",
)

NOTES = {
    "tech_breadth_entropy":
        "Shannon 熵(log2)，权重=企业在该 IPC 小类下的去重专利数；仅统计已匹配专利。"
        "单小类=0，无桥表记录=NULL。与 tech_breadth_ipc_class（主 IPC 小类数）口径不同，"
        "全量桥表的小类覆盖数会更宽，故不回写该列。",
    "core_staff_stability":
        "1 − mean(年流失率)；核心人员=企业内曾为第一发明人(is_first=1)的人；"
        "年流失率=|前一年活跃核心 − 当年活跃核心| / |前一年活跃核心|，"
        "窗口 2023/2024/2025，且要求相邻两年企业均有专利申请观测。"
        "代理指标：未出现在专利申请中≠离职，会高估流失。无可算年份=NULL。",
    "qual_rank_delta":
        "观测窗内层级净跃升 = 最高层级 − 首次层级；层级取 dim_qual_type.level_rank。"
        "仅统计有名单证据的正向晋级，因无撤销/复核未通过公告，不构造失效降级(−n)。"
        "dws 为 2026 全量快照，故取全窗净变化而非某一当年 Δ。",
    "qual_upgrade_cum":
        "观测窗内层级创新高的次数（首次记录不计），同一年多条记录按层级升序处理。"
        "本地仅 high_tech(2)/specialized(3) 两类资质，值域以 0/1 为主。",
    "m_cert":
        "字典 4.5「期间获取资质」里程碑标志，按最近观测年度（fact_qualification 最大 year，"
        "当前 2025）判定：当年有资质记录=1，否则=0。不用全窗口径，因为 2023 年 "
        "specialized 覆盖 500/500 家，全窗会退化成常量列。"
        "⚠️ 名单是新认定增量，0 只表示当年未观测到公告，不代表无资质或资格失效；"
        "与 m_funding（全窗口径）窗口不一致；全窗晋级信息见 qual_upgrade_cum。",
    "tech_conversion":
        "字典 5.1 替代口径「授权专利数/研发人员数」代理：分子=n_patent_grant"
        "（500 家主表「有效专利数(我方)」）；分母=max(bridge_patent_inventor 按企业去重发明人数, "
        "dws_tech_feature.n_inventor)，取二者较大值作为研发人员数下界——两个来源都低估："
        "主表 n_inventor 有 233 家截断在 20，桥表仅含已匹配专利且 41 家≤2 人。"
        "首选口径 rev_cagr/patent_cagr 因无公开财报不可算。粗代理，入模前建议 winsorize/log1p。",
}

LOG_MESSAGE = (
    f"{DERIVED_VER}: 补算 6 特征（entropy/stability/qual_delta/qual_upgrade/m_cert/"
    "tech_conversion）并同步 ads_model_dataset；"
    "边界：m_cert 为最近观测年度里程碑，0≠无资质；不构造资质失效降级；"
    "发明人稳定性/技术转化效率为代理口径；"
    "全量企业级聚合存在潜在后视泄漏，训练需 as-of 切面重算。"
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _shannon(counts) -> float:
    """Shannon 熵(log2)，counts 为各类别的权重；空/全零返回 0.0。"""
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            h -= p * math.log(p, 2)
    return round(h, 4)


# ---------------------------------------------------------------------------
# 1. 技术广度熵
# ---------------------------------------------------------------------------
def compute_ipc_entropy(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """返回 (eid→entropy, eid→小类覆盖数)。

    权重用「该小类下的去重专利数」，而非桥表行数：一件专利可挂多个 IPC 号，
    同小类重复出现只计一次，避免把多标引误当成技术布局更宽。
    """
    rows = conn.execute(
        """
        SELECT p.enterprise_id, b.ipc_subclass, COUNT(DISTINCT p.app_no)
        FROM bridge_patent_ipc b
        JOIN fact_patent p ON p.patent_id = b.patent_id
        WHERE p.enterprise_id IS NOT NULL
          AND b.ipc_subclass IS NOT NULL AND TRIM(b.ipc_subclass) <> ''
        GROUP BY p.enterprise_id, b.ipc_subclass
        """
    ).fetchall()
    by_ent = defaultdict(list)
    for eid, sub, n in rows:
        by_ent[eid].append(int(n))
    entropy = {eid: _shannon(v) for eid, v in by_ent.items()}
    coverage = {eid: len(v) for eid, v in by_ent.items()}
    return entropy, coverage


# ---------------------------------------------------------------------------
# 2. 核心人员稳定性
# ---------------------------------------------------------------------------
def compute_core_staff_stability(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT p.enterprise_id, CAST(p.app_year AS INTEGER), b.inventor_id, b.is_first
        FROM bridge_patent_inventor b
        JOIN fact_patent p ON p.patent_id = b.patent_id
        WHERE p.enterprise_id IS NOT NULL
          AND p.app_year IS NOT NULL AND TRIM(CAST(p.app_year AS TEXT)) <> ''
        """
    ).fetchall()
    active = defaultdict(lambda: defaultdict(set))   # eid → year → {inventor_id}
    core = defaultdict(set)                          # eid → {第一发明人}
    for eid, year, inv, is_first in rows:
        if not (2000 <= int(year) <= 2100):
            continue
        y = int(year)
        active[eid][y].add(inv)
        if is_first:
            core[eid].add(inv)

    out = {}
    for eid, by_year in active.items():
        churns = []
        for y in STABILITY_WINDOW:
            # 相邻两年都必须有专利申请观测，否则该年 churn 不可判定
            if (y - 1) not in by_year or y not in by_year:
                continue
            prev = by_year[y - 1] & core[eid]
            if not prev:
                continue
            cur = by_year[y] & core[eid]
            churns.append(len(prev - cur) / len(prev))
        if churns:
            out[eid] = round(1.0 - statistics.fmean(churns), 4)
    return out


# ---------------------------------------------------------------------------
# 3. 资质晋级序列
# ---------------------------------------------------------------------------
def compute_qual_sequence(conn: sqlite3.Connection) -> dict:
    """返回 eid → (qual_rank_delta, qual_upgrade_cum)。

    只处理有名单证据的正向晋级；不因「某年未出现在增量名单」推断资格失效。
    """
    ranks = dict(conn.execute("SELECT qual_type, level_rank FROM dim_qual_type").fetchall())
    rows = conn.execute(
        """
        SELECT enterprise_id, qual_type, CAST(year AS INTEGER)
        FROM fact_qualification
        WHERE year IS NOT NULL AND TRIM(CAST(year AS TEXT)) <> ''
        """
    ).fetchall()
    by_ent = defaultdict(list)
    for eid, qtype, year in rows:
        r = ranks.get(qtype)
        if r is None or not (2000 <= int(year) <= 2100):
            continue
        by_ent[eid].append((int(year), int(r)))

    out = {}
    for eid, seq in by_ent.items():
        seq.sort()                       # 同年按层级升序，避免 specialized 抢在 high_tech 前
        first_rank = seq[0][1]
        peak = first_rank
        upgrades = 0
        for _, r in seq[1:]:
            if r > peak:
                peak = r
                upgrades += 1
        out[eid] = (peak - first_rank, upgrades)
    return out


# ---------------------------------------------------------------------------
# 4. 技术转化效率（代理）
# ---------------------------------------------------------------------------
def compute_tech_conversion(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """返回 (eid→tech_conversion, eid→研发人员数下界分母)。"""
    inv_cnt = dict(conn.execute(
        """
        SELECT p.enterprise_id, COUNT(DISTINCT b.inventor_id)
        FROM bridge_patent_inventor b
        JOIN fact_patent p ON p.patent_id = b.patent_id
        WHERE p.enterprise_id IS NOT NULL
        GROUP BY p.enterprise_id
        """
    ).fetchall())
    main_rows = dict(conn.execute(
        "SELECT enterprise_id, n_patent_grant FROM dws_tech_feature"
    ).fetchall())
    csv_inv = dict(conn.execute(
        "SELECT enterprise_id, n_inventor FROM dws_tech_feature"
    ).fetchall())
    out, denom = {}, {}
    for eid, n_grant in main_rows.items():
        if n_grant is None:
            continue
        # 两个来源都低估研发人员数，取较大值作为下界，避免 /1 造成的伪极端值
        d = max(int(inv_cnt.get(eid) or 0), int(csv_inv.get(eid) or 0))
        if d <= 0:
            continue
        denom[eid] = d
        out[eid] = round(float(n_grant) / float(d), 4)
    return out, denom


# ---------------------------------------------------------------------------
# 5. m_cert：最近观测年度资质里程碑（字典 4.5）
# ---------------------------------------------------------------------------
def compute_m_cert(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """返回 (eid→0/1, 诊断)。

    年度取 fact_qualification 的最大 year（当前 2025），不使用全窗：
    2023 年 specialized 覆盖 500/500 家，全窗口径会退化为常量 1。
    """
    row = conn.execute(
        """SELECT MAX(CAST(year AS INTEGER)) FROM fact_qualification
           WHERE year IS NOT NULL AND TRIM(CAST(year AS TEXT)) <> ''"""
    ).fetchone()
    if not row or row[0] is None:
        raise RuntimeError("fact_qualification 无有效年度，无法计算 m_cert")
    latest = int(row[0])
    has = {r[0] for r in conn.execute(
        """SELECT DISTINCT enterprise_id FROM fact_qualification
           WHERE CAST(year AS INTEGER)=?""", (latest,)
    )}
    eids = {r[0] for r in conn.execute("SELECT enterprise_id FROM dim_enterprise")}
    out = {eid: (1 if eid in has else 0) for eid in eids}

    # 与独立产出的事件表交叉校验（不参与取值，只作一致性诊断）
    events = {r[0] for r in conn.execute(
        "SELECT DISTINCT enterprise_id FROM fact_qualification_event WHERE event_year=?",
        (latest,)
    )}
    diag = {
        "m_cert_year": latest,
        "n_positive": len(has & eids),
        "n_negative": len(eids - has),
        "event_mismatch": {
            "qual_only": sorted(has - events)[:10],
            "event_only": sorted(events - has)[:10],
            "n_qual_only": len(has - events),
            "n_event_only": len(events - has),
        },
    }
    return out, diag


# ---------------------------------------------------------------------------
# 汇总 / 落库
# ---------------------------------------------------------------------------
def _stats(values: dict, universe: set[str]) -> dict:
    vs = [v for v in values.values() if v is not None]
    s = {
        "non_null": len(vs),
        "null": len(universe) - len(vs),
        "coverage": round(len(vs) / len(universe), 4) if universe else 0.0,
    }
    if vs:
        sv = sorted(vs)
        s.update(min=sv[0], max=sv[-1], mean=round(statistics.fmean(vs), 4),
                 p50=sv[len(sv) // 2], p95=sv[min(len(sv) - 1, int(len(sv) * 0.95))],
                 n_gt20=sum(1 for v in vs if v > 20))
        if isinstance(vs[0], int):
            s["dist"] = {str(k): vs.count(k) for k in sorted(set(vs))}
    return s


def _upsert_log(cur: sqlite3.Cursor, payload: tuple) -> None:
    """幂等写日志：复用已有 log_id，避免 DELETE+INSERT 让自增 id 漂移。"""
    key = (SOURCE_ID, LOG_STAGE, LOG_TARGET)
    row = cur.execute(
        "SELECT log_id FROM meta_ingest_log "
        "WHERE source_id=? AND stage=? AND target_table=?", key
    ).fetchone()
    cur.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?", key
    )
    cols = ["source_id", "stage", "target_table", "file_path", "file_sha256",
            "rows_in", "rows_ok", "rows_bad", "started_at", "finished_at",
            "status", "message"]
    vals = [SOURCE_ID, LOG_STAGE, LOG_TARGET, *payload]
    if row:
        cols = ["log_id", *cols]
        vals = [row[0], *vals]
    cur.execute(
        f"INSERT INTO meta_ingest_log ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        vals,
    )


def _snapshot_keys(conn: sqlite3.Connection, table: str) -> list[tuple[str, int]]:
    return conn.execute(
        f"SELECT enterprise_id, stat_year FROM {table} ORDER BY enterprise_id, stat_year"
    ).fetchall()


def compute_all(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """纯计算，不写库。返回 (eid→{feature:value}, 附加诊断)。"""
    entropy, ipc_cov = compute_ipc_entropy(conn)
    stability = compute_core_staff_stability(conn)
    qual = compute_qual_sequence(conn)
    conversion, inv_denom = compute_tech_conversion(conn)
    mcert, mcert_diag = compute_m_cert(conn)

    eids = {r[0] for r in conn.execute("SELECT enterprise_id FROM dim_enterprise")}
    result = {eid: {k: None for k in FEATURES} for eid in eids}
    for eid, v in entropy.items():
        if eid in result:
            result[eid]["tech_breadth_entropy"] = v
    for eid, v in stability.items():
        if eid in result:
            result[eid]["core_staff_stability"] = v
    for eid, (delta, cum) in qual.items():
        if eid in result:
            result[eid]["qual_rank_delta"] = delta
            result[eid]["qual_upgrade_cum"] = cum
    for eid, v in mcert.items():
        if eid in result:
            result[eid]["m_cert"] = v
    for eid, v in conversion.items():
        if eid in result:
            result[eid]["tech_conversion"] = v

    diag = {
        "n_enterprise": len(eids),
        "ipc_subclass_coverage": ipc_cov,
        "inventor_denominator": inv_denom,
        "m_cert": mcert_diag,
        "stats": {
            k: _stats({e: v[k] for e, v in result.items()}, eids) for k in FEATURES
        },
        "notes": NOTES,
        "definition_ver": DERIVED_VER,
        "stability_window": list(STABILITY_WINDOW),
    }
    return result, diag


def apply(conn: sqlite3.Connection, result: dict, refresh_wide: bool = True,
          verbose: bool = True) -> dict:
    cur = conn.cursor()
    tech_keys = _snapshot_keys(conn, "dws_tech_feature")
    growth_keys = _snapshot_keys(conn, "dws_growth_feature")
    fusion_keys = _snapshot_keys(conn, "dws_fusion_feature")

    cur.executemany(
        """UPDATE dws_tech_feature
           SET tech_breadth_entropy=?, core_staff_stability=?
           WHERE enterprise_id=? AND stat_year=?""",
        [(result.get(eid, {}).get("tech_breadth_entropy"),
          result.get(eid, {}).get("core_staff_stability"), eid, sy)
         for eid, sy in tech_keys],
    )
    cur.executemany(
        """UPDATE dws_growth_feature
           SET qual_rank_delta=?, qual_upgrade_cum=?, m_cert=?
           WHERE enterprise_id=? AND stat_year=?""",
        [(result.get(eid, {}).get("qual_rank_delta"),
          result.get(eid, {}).get("qual_upgrade_cum"),
          result.get(eid, {}).get("m_cert"), eid, sy)
         for eid, sy in growth_keys],
    )
    cur.executemany(
        """UPDATE dws_fusion_feature SET tech_conversion=?
           WHERE enterprise_id=? AND stat_year=?""",
        [(result.get(eid, {}).get("tech_conversion"), eid, sy)
         for eid, sy in fusion_keys],
    )
    n_wide = 0
    if refresh_wide:
        wide = conn.execute("SELECT enterprise_id FROM ads_model_dataset").fetchall()
        cur.executemany(
            """UPDATE ads_model_dataset
               SET tech_breadth_entropy=?, core_staff_stability=?,
                   qual_rank_delta=?, qual_upgrade_cum=?, m_cert=?, tech_conversion=?
               WHERE enterprise_id=?""",
            [(result.get(eid, {}).get("tech_breadth_entropy"),
              result.get(eid, {}).get("core_staff_stability"),
              result.get(eid, {}).get("qual_rank_delta"),
              result.get(eid, {}).get("qual_upgrade_cum"),
              result.get(eid, {}).get("m_cert"),
              result.get(eid, {}).get("tech_conversion"), eid)
             for (eid,) in wide],
        )
        n_wide = len(wide)
    conn.commit()

    _upsert_log(cur, (
        "sqlite:bridge_patent_ipc+bridge_patent_inventor+fact_qualification",
        None, len(tech_keys) + len(growth_keys) + len(fusion_keys),
        sum(1 for v in result.values() for k in FEATURES if v[k] is not None),
        0, _now(), _now(), "warn", LOG_MESSAGE,
    ))
    conn.commit()
    applied = {
        "dws_tech_feature_rows": len(tech_keys),
        "dws_growth_feature_rows": len(growth_keys),
        "dws_fusion_feature_rows": len(fusion_keys),
        "ads_model_dataset_rows": n_wide,
    }
    if verbose:
        print(f"  [apply] {applied}")
    return applied


def run(conn: sqlite3.Connection, refresh_wide: bool = True,
        verbose: bool = True) -> dict:
    result, diag = compute_all(conn)
    if verbose:
        for k in FEATURES:
            print(f"  [calc ] {k:<24} {diag['stats'][k]}")
        print(f"  [calc ] m_cert 年度={diag['m_cert']['m_cert_year']} "
              f"断档样本={diag['m_cert']['n_positive']} 续存样本={diag['m_cert']['n_negative']} "
              f"事件表一致性={diag['m_cert']['event_mismatch']['n_qual_only']}/"
              f"{diag['m_cert']['event_mismatch']['n_event_only']}")
    applied = apply(conn, result, refresh_wide=refresh_wide, verbose=verbose)
    return {"stats": diag["stats"], "applied": applied,
            "definition_ver": DERIVED_VER, "notes": NOTES}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补算 6 个派生特征并刷新建模宽表")
    ap.add_argument("--db", default=str(DB_PATH), help="目标数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只计算并打印统计/样例，不写库")
    ap.add_argument("--no-refresh-wide", action="store_true", help="不刷新 ads_model_dataset")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"[fatal] 数据库不存在：{db}", file=sys.stderr)
        return 2
    print(f"[cfg] db={db}  ver={DERIVED_VER}  dry_run={args.dry_run}")
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result, diag = compute_all(conn)
        for k in FEATURES:
            print(f"  [calc ] {k:<24} {diag['stats'][k]}")
        print(f"  [calc ] m_cert 年度={diag['m_cert']['m_cert_year']} "
              f"断档样本={diag['m_cert']['n_positive']} 续存样本={diag['m_cert']['n_negative']} "
              f"事件表一致性={diag['m_cert']['event_mismatch']['n_qual_only']}/"
              f"{diag['m_cert']['event_mismatch']['n_event_only']}")
        sample = sorted(result)[:5]
        for eid in sample:
            print(f"  [eg   ] {eid} {json.dumps(result[eid], ensure_ascii=False)}")
        if args.dry_run:
            print("[dry] 未写库")
            return 0
        applied = apply(conn, result, refresh_wide=not args.no_refresh_wide)
        print("\n[done]", json.dumps(applied, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
