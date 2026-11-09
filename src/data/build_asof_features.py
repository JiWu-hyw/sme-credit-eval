"""
as-of 切面特征：按「特征窗口终点」截断事实，消除后视泄漏（幂等、可重跑）。

为什么要做
----------
既有 dws_* / ads_model_dataset 的 29 列是**企业级全量聚合快照**，统计了
2020–2025 年的全部事实；而 ads_label 是 2021/2022 首次认定队列、到期年
2024/2025 的弱标签。特征里混进了标签窗口内才发生的事，属于后视泄漏。

实测最严重的是 m_cert：它取 fact_qualification 的最大年度（2025）判定，
而 2022 队列的到期年正是 2025 —— 「2025 年有重新认定记录」几乎就是
「label=0」的定义本身。审计显示 m_cert=1 的 135 家全部为续存样本（0 断档样本），
点二列相关 r=-0.2401，是全表最强特征。这不是预测信号，是标签复制品。

字典口径（唯一依据）
--------------------
《数据字典-标准数据库》§4.6：cohort_year = 「观察队列起始年（= 特征窗口终点）」，
并明确「主标签口径：2021 年高新认定队列 → 2024 年复核名单；未出现 = 1。
特征窗口限定 2019–2021，无泄漏」。《数据替代方案-开源数据路线》§3.3 同。
故：

* 'cohort'（**主口径 / 训练入口**）：as_of_year = cohort_year
* 'due_minus_1'（稳健性对照）：as_of_year = cohort_year + 3 - 1（到期前一年）
  两个政策都无泄漏；后者信息更足但窗口更贴近标签，且含认定后的行为，
  与字典「认定之前」的表述不符，仅用于敏感性对照，不作主实验。

截断规则：只用 app_year ∈ [2019, as_of_year] 的专利、year <= as_of_year 的
资质记录，以及由这些专利派生的 IPC / 发明人桥表行。窗口起点按字典取 2019，
但本地 fact_patent 最早只有 2020，实际窗口为 2020–as_of_year（已在报告标注）。

可切分性（29 列）
-----------------
可重算 15 列：inv_patent_ratio, grant_rate, tech_breadth_ipc_class,
tech_breadth_entropy, core_staff_stability, n_patent_app, n_patent_grant,
n_patent_invention, n_inventor, n_ipc_subclass, qual_rank, qual_rank_delta,
qual_upgrade_cum, m_cert, tech_conversion。

grant_rate 的回收方式（v0.2，2026-11-05）
-----------------------------------------
原先的判断是「grant_date 全空，无法按时点判定已决/未决」，这个判断只对了一半：
授权日确实没有，但「是否已决」可以用**审结期**近似 —— 申请年 + 审结期 ≤ as_of_year
的专利，结果基本已成定局。于是 grant_rate = 曾授权数 / 已审结数，两者都限窗口内。

两个前置修正缺一不可：

1. **授权口径**。is_granted 表示「此刻仍在权」，断缴年费或期限届满会翻回 0，
   所以它把「授权后失效」和「从未授权」混成一类。改用授权公告 kind code
   （B/U/S/C 只出现在授权文本上，存在即授权）判「曾授权」。实测全部已归属专利
   29,582 件中，is_granted=1 仅 15,753 件，曾授权 23,693 件 —— 少算 7,940 件（33.5%）。
   两个字段已固化在 v_patent_canonical：is_granted（在权）/ is_granted_ever（曾授权）。

2. **审结期**。发明专利 3 年（需实质审查），实用新型/外观 1 年（只做初审）。
   未过审结期的专利不进分母，否则「还在审」被当成「没授权」。

⚠️ 结构性退化（必须知道，别当成"补上了一个特征"）
主口径 as_of_year = cohort_year 下，grant_rate 恒等于 1.0：
驳回/撤回只发生在发明专利（2019–2022 年发明驳回率 33.9%，实用新型与外观为 0），
而窗口起点 2019 + 发明审结期 3 年，使 T=2021 时没有任何成熟的发明专利，
分母里剩下的全是「申请即授权」的实用新型/外观。这不是数据缺口，是结构决定。
因此该列**有值但不入模**（零方差），仅在 due_minus_1 对照口径下有少量区分度。

不可切分 14 列一律置 NULL、is_available=0，不拿全量值冒充：
patent_citation / patent_triple_score / rd_intensity / n_patent_collab /
rev_cagr / emp_cagr / patent_cagr / funding_to_last_val / m_funding /
m_team / m_cite / investor_prestige / cap_tech_synergy / tech_growth_inter
（静态快照或外部数据源，无时间维）。patent_cagr 虽有逐年专利可算，但与
原列的静态口径不可比，宁可留 NULL 也不制造同名异义列。

口径边界（勿擅自放宽）
----------------------
1. 主发明人「核心集合」也必须在窗口内界定。若沿用全量桥表的 is_first 集合，
   等于把未来才成为第一发明人的人提前放进历史分母，是隐性泄漏。
2. n_patent_grant 用 is_granted=1 且 app_year<=T 作代理；真实授权日全空，
   申请日≠授权日，早年申请的专利在 T 时可能尚未授权，会高估早期授权数。
3. 专利申请日到公开日有约 18 个月滞后：按 app_year 切面虽排除了「申请日
   在未来」，但不保证「T 时点已公开可见」。本地无公开日字段，属残余边界。
   n_patent_grant 与 tech_conversion 仍用 is_granted（在权口径）且未加审结期
   过滤，同时含「未来才授权」和「目前已失效」两种偏差，方向不固定；
   grant_rate 是加了审结期控制的严格版本，两者不可混用。
4. m_cert 在 cohort 口径下退化为常量 1（队列本就由首次认定年定义），
   已如实保留而非强制造区分度；入模应剔除。
5. 原 ads_model_dataset / v_model_dataset_flat / dws_* **完全不动**，作为
   泄漏基线留存。新入口 v_model_dataset_asof_flat 必须带
   WHERE as_of_policy='cohort'，否则同企业会出现两行。
6. 无原料一律 NULL，不用 0 填充；0 是真实观测值（如单小类熵=0、m_cert=0）。

用法
----
    python -m src.data.build_asof_features                    # 生成两个政策 + 审计
    python -m src.data.build_asof_features --policy cohort   # 只生成主口径
    python -m src.data.build_asof_features --dry-run         # 只计算与审计，不写库
    python -m src.data.build_asof_features --db <path>       # 指定库

升版规则：字典窗口口径变更、补齐授权日/公开日、或标签定义升级后，
必须升 ASOF_VER 并重跑，不得原地改写旧口径。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[2]
DB_PATH = _BACKEND / "data" / "db" / "zjtech_credit.db"
SCHEMA_SQL = _HERE.parent / "schema.sql"

ASOF_VER = "asof_features_v0.2"
# meta_ingest_log.source_id 外键指向 meta_data_source；as-of 是本地派生，
# 沿用已登记的 zj_500family（500 家主表数据源），以 target_table 区分日志。
SOURCE_ID = "zj_500family"
LOG_STAGE = "build"
LOG_TARGET = "ads_feature_asof"
VALID_YEARS = 3
WINDOW_START = 2019                     # 字典 §4.6「2019–2021」
POLICIES = ("cohort", "due_minus_1")

# 审结期：申请年到「授权与否已成定局」大致需要多久（年）。
# 依据：发明专利需实质审查，国内近年平均授权周期约 20–30 个月，取 3 年偏保守；
# 实用新型与外观只做初步审查，一般 6–12 个月，取 1 年。
# 用途：as-of 计算授权率时，只纳入 as_of_year 时点「已经能判定结果」的专利，
# 否则会把「还在审」当成「没授权」，得到一条随专利年龄下降的伪曲线。
MATURITY_YEARS = {"invention": 3, "utility": 1, "design": 1}
MATURITY_DEFAULT = 3

# 现时仍处于审查流程中的状态。这些状态具有单调性 —— 现在的「审中」在更早的
# 时点必然也是「审中」，所以用它做排除不会引入未来信息。
PENDING_STATUSES = {"实质审查", "公布", "审中"}

# 与 ads_model_dataset / v_model_dataset_flat 特征列严格同名同序
ALL_FEATURES = (
    "inv_patent_ratio", "grant_rate", "patent_citation",
    "tech_breadth_ipc_class", "tech_breadth_entropy",
    "patent_triple_score", "core_staff_stability", "rd_intensity",
    "n_patent_app", "n_patent_grant", "n_patent_invention",
    "n_inventor", "n_ipc_subclass", "n_patent_collab",
    "rev_cagr", "emp_cagr", "patent_cagr", "funding_to_last_val",
    "m_funding", "m_team", "m_cert", "m_cite", "investor_prestige",
    "qual_rank", "qual_rank_delta", "qual_upgrade_cum",
    "tech_conversion", "cap_tech_synergy", "tech_growth_inter",
)

NOT_ASOFABLE = {
    "patent_citation": "静态快照，无逐次引用时间",
    "patent_triple_score": "需 SciBERT 与静态打分，无时间维",
    "rd_intensity": "财报口径，本地无数据",
    "n_patent_collab": "单申请人匹配，原列即为 NULL，无法判定合作",
    "rev_cagr": "财报口径，本地无数据",
    "emp_cagr": "财报口径，本地无数据",
    "patent_cagr": "原列为静态快照 CAGR，与逐年口径不可比",
    "funding_to_last_val": "融资事件外部数据源，本地无时间维",
    "m_funding": "全窗融资标志，静态且含未来事件",
    "m_team": "团队里程碑，本地无数据",
    "m_cite": "引用里程碑，本地无数据",
    "investor_prestige": "投资机构数据，本地无数据",
    "cap_tech_synergy": "需季度对齐的融资×专利时序，本地无数据",
    "tech_growth_inter": "由静态 CAGR 派生，随 patent_cagr 一并置 NULL",
}
ASOFABLE = tuple(f for f in ALL_FEATURES if f not in NOT_ASOFABLE)

ASOF_NOTES = {
    "policy": {
        "cohort": "主口径：as_of_year = cohort_year（字典 §4.6 认定之前）",
        "due_minus_1": "稳健性对照：as_of_year = cohort_year + 3 - 1（到期前一年）",
    },
    "window_start": WINDOW_START,
    "n_asofable": len(ASOFABLE),
    "n_not_asofable": len(NOT_ASOFABLE),
    "not_asofable_reason": NOT_ASOFABLE,
    "grant_rate_rule": {
        "definition": "曾授权专利数 / 已审结专利数，均在窗口内且已过审结期",
        "numerator": "is_granted_ever=1（授权公告 kind code B/U/S/C 或 is_granted=1）",
        "denominator": "app_year + MATURITY_YEARS[patent_type] <= as_of_year，且现时不在 PENDING_STATUSES",
        "maturity_years": MATURITY_YEARS,
        "known_degeneracy": (
            "主口径（as_of_year = cohort_year）下本特征必然退化为常量 1.0："
            "驳回/撤回只发生在发明专利（2019–2022 年发明驳回率 33.9%，实用新型与外观为 0），"
            "而发明专利需 3 年审结期，窗口起点 2019 使 T=2021 时没有任何已成熟的发明专利，"
            "剩下的全是「申请即授权」的实用新型/外观。这是结构性结果，不是数据缺口。"
        ),
    },
    "known_limits": [
        "本地 fact_patent 最早 2020，字典 2019 起点实际无法覆盖",
        "grant_date / 公开日缺失，按 app_year 归属授权与可见性",
        "core 集合在窗口内界定，避免未来第一发明人渗入历史分母",
        "cohort 口径下 m_cert 退化为常量 1（队列由首次认定年定义）",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _shannon(counts) -> float:
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
# DDL：只执行 schema.sql 中 [ASOF:BEGIN] ~ [ASOF:END] 区块（单一事实源）
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection, verbose: bool = True) -> int:
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    # 跳过 BEGIN 标记所在行的剩余说明文字，只取下一行起的纯 SQL 区块
    m = re.search(r"-- \[ASOF:BEGIN\][^\n]*\n(.*?)-- \[ASOF:END\]", ddl, re.S)
    if not m:
        raise RuntimeError(f"schema.sql 缺少 [ASOF:BEGIN]/[ASOF:END] 区块：{SCHEMA_SQL}")
    block = m.group(1).strip()
    conn.executescript(block)          # executescript 会隐式提交，故须先于数据事务
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name LIKE 'v_%asof%'"
    ).fetchone()[0]
    if verbose:
        print(f"  [ddl  ] 已从 schema.sql 应用 as-of 区块（相关视图 {n} 个）")
    return n


# ---------------------------------------------------------------------------
# 原料装载（一次读入内存，避免 307 家 × 2 政策反复查库）
# ---------------------------------------------------------------------------
def load_sources(conn: sqlite3.Connection) -> dict:
    """装载原料。

    ⚠️ 专利一律按 **app_no 粒度** 归并（一件专利一行），不能按 patent_id 计数：
       源数据里同一件专利会落两条 —— 申请公布文本（…A，有 IPC、无法律状态）和
       授权公告文本（…B，有授权状态、无 IPC）。按行计数会让专利数虚增约 8%，
       实测 2,866 组 / 5,732 行（见 v_patent_canonical 视图注释）。
       桥表（IPC / 发明人）仍挂在原始 patent_id 上，所以先用 pid2app 映射到 app_no。
    """
    # eid -> {app_no: [year, ptype, granted_now, granted_ever, legal_status]}
    pat_no = defaultdict(dict)
    pid2app = {}                         # 原始 patent_id -> app_no（全表，含未归属）
    for eid, pid, app_no, year, ptype, granted, status in conn.execute(
        """SELECT enterprise_id, patent_id, app_no, app_year, patent_type,
                  is_granted, legal_status
           FROM fact_patent
           WHERE app_no IS NOT NULL"""
    ):
        pid2app[pid] = app_no
        if eid is None or year is None:
            continue
        y, g = int(year), int(granted or 0)
        # 「曾授权」判据：授权公告的 kind code 只出现在授权文本上，存在即授权；
        # 不能只看 is_granted —— 它表示「此刻仍在权」，断缴年费后会翻回 0。
        ever = 1 if (g == 1 or pid[-1:] in ("B", "U", "S", "C")) else 0
        rec = pat_no[eid].get(app_no)
        if rec is None:
            pat_no[eid][app_no] = [y, ptype, g, ever, status]
        else:
            # 同一件专利的两条记录：授权取并集，年份/类型取一致值，状态取首个非空
            rec[2] = max(rec[2], g)
            rec[3] = max(rec[3], ever)
            if rec[4] is None:
                rec[4] = status

    # 桥表按 app_no 归并（同一件专利的 A/B 两版各自的 IPC 与发明人取并集）
    ipc_by_app = defaultdict(set)        # app_no -> {ipc_subclass}
    for pid, sub in conn.execute(
        """SELECT patent_id, ipc_subclass FROM bridge_patent_ipc
           WHERE ipc_subclass IS NOT NULL AND TRIM(ipc_subclass) <> ''"""
    ):
        app = pid2app.get(pid)
        if app:
            ipc_by_app[app].add(sub)

    inv_by_app = defaultdict(dict)       # app_no -> {inventor_id: is_first}
    for pid, inv, is_first in conn.execute(
        "SELECT patent_id, inventor_id, is_first FROM bridge_patent_inventor"
    ):
        app = pid2app.get(pid)
        if app:
            d = inv_by_app[app]
            d[inv] = max(d.get(inv, 0), int(is_first or 0))

    ranks = dict(conn.execute("SELECT qual_type, level_rank FROM dim_qual_type"))
    quals = defaultdict(list)            # eid -> [(year, rank)]
    for eid, qtype, year in conn.execute(
        """SELECT enterprise_id, qual_type, year FROM fact_qualification
           WHERE year IS NOT NULL AND TRIM(CAST(year AS TEXT)) <> ''"""
    ):
        r = ranks.get(qtype)
        if r is not None:
            quals[eid].append((int(year), int(r)))

    # 只处理有标签的完整观察窗队列；excluded 行无有效 cohort，不生成切面
    samples = conn.execute(
        """SELECT l.enterprise_id, l.cohort_year, l.label_value, d.split
           FROM ads_label l
           JOIN ads_model_dataset d
             ON d.enterprise_id = l.enterprise_id
            AND d.label_task    = l.label_task
            AND d.cohort_year   = l.cohort_year
           WHERE l.label_task = 'qual_survival' AND d.split <> 'excluded'
           ORDER BY l.enterprise_id"""
    ).fetchall()
    return {"patents": pat_no, "ipc_by_app": ipc_by_app,
            "inv_by_app": inv_by_app, "quals": quals, "samples": samples}


# ---------------------------------------------------------------------------
# 单企业 × 单锚点 的 15 个可切分特征
# ---------------------------------------------------------------------------
def features_at(src: dict, eid: str, as_of_year: int) -> dict:
    out = dict.fromkeys(ALL_FEATURES)     # 29 列全部初始化为 None

    # 按 app_no 归并后的窗口内专利：一件专利只算一次（A/B 两版已合并）
    P = {app: rec for app, rec in src["patents"].get(eid, {}).items()
         if WINDOW_START <= rec[0] <= as_of_year}
    n_app = len(P)
    n_invention = sum(1 for rec in P.values() if rec[1] == "invention")
    n_grant = sum(1 for rec in P.values() if rec[2] == 1)

    if n_app:
        out["n_patent_app"] = n_app
        out["n_patent_invention"] = n_invention
        out["n_patent_grant"] = n_grant
        out["inv_patent_ratio"] = round(n_invention / n_app, 4)

    # 授权率：只统计 as_of_year 时点「已能判定授权与否」的专利。
    # 未过审结期的专利一律排除 —— 否则「还在审」会被当成「没授权」，
    # 得到一条随专利年龄单调下降的伪曲线（就是伪造时间趋势）。
    mature = [rec for rec in P.values()
              if rec[0] + MATURITY_YEARS.get(rec[1], MATURITY_DEFAULT) <= as_of_year
              and rec[4] not in PENDING_STATUSES]
    if mature:
        n_ever = sum(rec[3] for rec in mature)
        out["grant_rate"] = round(n_ever / len(mature), 4)

    ipc_counts = Counter()
    for app in P:
        for sub in src["ipc_by_app"].get(app, ()):
            ipc_counts[sub] += 1         # 同小类同专利只计一次
    if ipc_counts:
        n_ipc = len(ipc_counts)
        out["n_ipc_subclass"] = n_ipc
        out["tech_breadth_ipc_class"] = n_ipc
        out["tech_breadth_entropy"] = _shannon(ipc_counts.values())

    inventors = set()
    core = set()
    active = defaultdict(set)
    for app, rec in P.items():
        y = rec[0]
        for inv, is_first in src["inv_by_app"].get(app, {}).items():
            inventors.add(inv)
            active[y].add(inv)
            if is_first:
                core.add(inv)            # 核心集合仅由窗口内专利界定
    if inventors:
        out["n_inventor"] = len(inventors)
        if n_grant is not None:
            out["tech_conversion"] = round(n_grant / len(inventors), 4)

    churns = []
    for y in (as_of_year - 2, as_of_year - 1, as_of_year):
        if (y - 1) not in active or y not in active:
            continue
        prev = active[y - 1] & core
        if not prev:
            continue
        churns.append(len(prev - (active[y] & core)) / len(prev))
    if churns:
        out["core_staff_stability"] = round(1.0 - statistics.fmean(churns), 4)

    Q = sorted((y, r) for y, r in src["quals"].get(eid, ())
               if WINDOW_START <= y <= as_of_year)
    if Q:
        first_rank = Q[0][1]
        peak = first_rank
        upgrades = 0
        for _, r in Q[1:]:
            if r > peak:
                peak = r
                upgrades += 1
        out["qual_rank"] = peak
        out["qual_rank_delta"] = peak - first_rank
        out["qual_upgrade_cum"] = upgrades
    # 年度里程碑：只认 as_of_year 当年，不回溯、不外推
    out["m_cert"] = 1 if any(y == as_of_year for y, _ in Q) else 0
    return out


def as_of_year_of(cohort_year: int, policy: str) -> int:
    if policy == "cohort":
        return int(cohort_year)
    if policy == "due_minus_1":
        return int(cohort_year) + VALID_YEARS - 1
    raise ValueError(f"未知 as_of_policy: {policy}")


def compute(conn: sqlite3.Connection, policies=POLICIES, verbose: bool = True) -> tuple[dict, dict]:
    """纯计算，不写库。返回 (policy → 长表行, 诊断)。"""
    src = load_sources(conn)
    rows_by_policy, diag = {}, {}
    for policy in policies:
        rows, coverage, per_year = [], Counter(), Counter()
        for eid, cohort, label, split in src["samples"]:
            T = as_of_year_of(cohort, policy)
            vals = features_at(src, eid, T)
            per_year[T] += 1
            for name in ALL_FEATURES:
                v = vals[name]
                if v is not None:
                    coverage[name] += 1
                rows.append((eid, policy, int(cohort), T, name, v,
                             1 if v is not None else 0, _now()))
        rows_by_policy[policy] = rows
        diag[policy] = {
            "n_sample": len(src["samples"]),
            "as_of_year_dist": dict(sorted(per_year.items())),
            "n_rows": len(rows),
            "coverage": {k: coverage.get(k, 0) for k in ASOFABLE},
        }
        if verbose:
            print(f"  [calc ] {policy:<12} 样本={len(src['samples'])} "
                  f"锚点年={dict(sorted(per_year.items()))} 长表行={len(rows)}")
            print(f"          {len(ASOFABLE)} 列覆盖：" + " ".join(
                f"{k.replace('tech_breadth_', 'tb_')}={coverage.get(k, 0)}" for k in ASOFABLE))
    return rows_by_policy, diag


# ---------------------------------------------------------------------------
# 泄漏审计：点二列相关 + rank-based 单变量 AUC（纯 Python，无 numpy/sklearn）
# ---------------------------------------------------------------------------
def _corr(pairs) -> float | None:
    pts = [(v, y) for v, y in pairs if v is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = statistics.fmean(v for v, _ in pts)
    my = statistics.fmean(y for _, y in pts)
    num = sum((v - mx) * (y - my) for v, y in pts)
    dx = math.sqrt(sum((v - mx) ** 2 for v, _ in pts))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in pts))
    return round(num / (dx * dy), 4) if dx * dy > 0 else None


def _auc(pairs) -> float | None:
    """Mann-Whitney U 形式，含 ties 用平均秩；AUC<0.5 表示负向关联。"""
    pts = sorted((v, y) for v, y in pairs if v is not None)
    n_pos = sum(y for _, y in pts)
    n_neg = len(pts) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    rank_sum, i = 0.0, 0
    while i < len(pts):
        j = i
        while j < len(pts) and pts[j][0] == pts[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        rank_sum += avg_rank * sum(pts[k][1] for k in range(i, j))
        i = j
    return round((rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg), 4)


def audit(conn: sqlite3.Connection, rows_by_policy: dict, verbose: bool = True) -> dict:
    """对比 full（泄漏基线）与各 as-of 政策对标签的判别力。"""
    lab_rows = conn.execute(
        """SELECT l.enterprise_id, l.label_value, l.cohort_year FROM ads_label l
           JOIN ads_model_dataset d ON d.enterprise_id=l.enterprise_id
             AND d.label_task=l.label_task AND d.cohort_year=l.cohort_year
           WHERE l.label_task='qual_survival' AND d.split<>'excluded'"""
    ).fetchall()
    labels = {e: y for e, y, _ in lab_rows}
    cohorts = {e: cy for e, _, cy in lab_rows}
    full = {}
    row = conn.execute(
        "SELECT enterprise_id, %s FROM ads_model_dataset WHERE split<>'excluded'"
        % ",".join(ALL_FEATURES)
    )
    for r in row:
        full[r[0]] = dict(zip(ALL_FEATURES, r[1:]))

    asof = defaultdict(lambda: defaultdict(dict))
    for policy, rows in rows_by_policy.items():
        for eid, _, _, _, name, value, _, _ in rows:
            asof[policy][name][eid] = value

    report = {}
    for name in ALL_FEATURES:
        f_pairs = [(full.get(e, {}).get(name), y) for e, y in labels.items()]
        rec = {"full_r": _corr(f_pairs), "full_auc": _auc(f_pairs)}
        for policy in rows_by_policy:
            a_pairs = [(asof[policy][name].get(e), y) for e, y in labels.items()]
            rec[f"{policy}_r"] = _corr(a_pairs)
            rec[f"{policy}_auc"] = _auc(a_pairs)
            vals = [v for v in asof[policy][name].values() if v is not None]
            rec[f"{policy}_n_unique"] = len(set(vals))
            # 「有值但只有一种取值」= 零方差，入模无意义，必须与「没值」区分开
            rec[f"{policy}_is_constant"] = bool(vals) and len(set(vals)) == 1
            # 分队列检查：合并后不恒定不代表每层都有区分度。若某队列内恒定，
            # 该列就退化成「队列哑变量」，会把队列差异伪装成特征信号。
            by_cohort = defaultdict(list)
            for e, y in labels.items():
                v = asof[policy][name].get(e)
                if v is not None:
                    by_cohort[cohorts[e]].append((v, y))
            rec[f"{policy}_n_unique_by_cohort"] = {
                int(cy): len({v for v, _ in pts}) for cy, pts in sorted(by_cohort.items())}
            rec[f"{policy}_auc_by_cohort"] = {
                int(cy): _auc(pts) for cy, pts in sorted(by_cohort.items())}
        fa, ca = rec["full_auc"], rec.get("cohort_auc")
        rec["auc_drop"] = (round(abs(fa - 0.5) - abs(ca - 0.5), 4)
                           if fa is not None and ca is not None else None)
        report[name] = rec

    leaked = sorted(
        ((k, v) for k, v in report.items() if v["auc_drop"] is not None and v["auc_drop"] > 0.05),
        key=lambda kv: -kv[1]["auc_drop"],
    )
    summary = {
        "n_labeled": len(labels),
        "leaked_features": [
            {"feature": k, "full_auc": v["full_auc"],
             "cohort_auc": v["cohort_auc"], "auc_drop": v["auc_drop"],
             "full_r": v["full_r"], "cohort_r": v["cohort_r"]}
            for k, v in leaked
        ],
        "m_cert": report["m_cert"],
        "constant_features": sorted(
            k for k, v in report.items() if v.get("cohort_is_constant")),
        "constant_within_cohort": sorted(
            k for k, v in report.items()
            if any(n <= 1 for n in (v.get("cohort_n_unique_by_cohort") or {"x": 2}).values())),
        "no_signal_features": sorted(
            k for k, v in report.items()
            if v.get("cohort_auc") is not None and abs(v["cohort_auc"] - 0.5) < 0.05),
        "n_asofable": len(ASOFABLE),
        "n_not_asofable": len(NOT_ASOFABLE),
    }
    if verbose:
        print(f"  [audit] 标签样本 {len(labels)} 家；|AUC-0.5| 下降 >0.05 的特征：")
        for k, v in leaked:
            print(f"          {k:<24} full AUC={v['full_auc']} r={v['full_r']} "
                  f"→ cohort AUC={v['cohort_auc']} r={v['cohort_r']} "
                  f"判别力下降={v['auc_drop']}")
        if not leaked:
            print("          （无）")
        print(f"  [audit] m_cert 交叉：full r={report['m_cert']['full_r']} / "
              f"cohort r={report['m_cert']['cohort_r']}")
        if summary["constant_features"]:
            print(f"  [audit] ⚠️ 主口径下零方差（有值但恒定，入模无效）："
                  f"{summary['constant_features']}")
        if summary["constant_within_cohort"]:
            print(f"  [audit] ⚠️ 某个队列内零方差（会退化成队列哑变量）："
                  f"{summary['constant_within_cohort']}")
        if summary["no_signal_features"]:
            print(f"  [audit] ⚠️ 主口径 |AUC-0.5|<0.05（有值但无判别力）："
                  f"{summary['no_signal_features']}")
    return {"summary": summary, "by_feature": report}


# ---------------------------------------------------------------------------
# 落库 / 日志
# ---------------------------------------------------------------------------
def apply(conn: sqlite3.Connection, rows_by_policy: dict, verbose: bool = True) -> dict:
    cur = conn.cursor()
    applied = {}
    for policy, rows in rows_by_policy.items():
        cur.execute("DELETE FROM ads_feature_asof WHERE as_of_policy=?", (policy,))
        cur.executemany(
            """INSERT INTO ads_feature_asof
               (enterprise_id, as_of_policy, cohort_year, as_of_year,
                feature_name, feature_value, is_available, computed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        applied[policy] = len(rows)
    conn.commit()
    if verbose:
        print(f"  [apply] 写入 {applied}（按政策先删后插，幂等）")
    return applied


def _upsert_log(cur: sqlite3.Cursor, payload: tuple) -> None:
    key = (SOURCE_ID, LOG_STAGE, LOG_TARGET)
    row = cur.execute(
        "SELECT log_id FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?", key
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
        f"VALUES ({','.join(['?'] * len(cols))})", vals,
    )


def write_log(conn: sqlite3.Connection, rows_by_policy: dict, audit_result: dict) -> None:
    cur = conn.cursor()
    n_rows = sum(len(r) for r in rows_by_policy.values())
    n_avail = sum(1 for rows in rows_by_policy.values() for r in rows if r[6] == 1)
    s = audit_result["summary"]
    message = json.dumps({
        "definition_ver": ASOF_VER,
        "policies": sorted(rows_by_policy),
        "main_policy": "cohort",
        "window_start": WINDOW_START,
        "n_asofable": len(ASOFABLE),
        "n_not_asofable": len(NOT_ASOFABLE),
        "leaked_features": s["leaked_features"],
        "m_cert": s["m_cert"],
        "notes": ASOF_NOTES,
    }, ensure_ascii=False)
    _upsert_log(cur, (
        "sqlite:fact_patent+bridge_patent_ipc+bridge_patent_inventor+fact_qualification",
        None, n_rows, n_avail, 0, _now(), _now(), "ok",
        f"{ASOF_VER}: as-of 切面特征；主口径 cohort（字典 §4.6 窗口终点）；"
        f"{len(ASOFABLE)} 可切分 / {len(NOT_ASOFABLE)} 不可切分；原宽表不动。审计详情：{message}",
    ))
    conn.commit()


def run(conn: sqlite3.Connection, policies=POLICIES, do_audit: bool = True,
        verbose: bool = True) -> dict:
    rows_by_policy, diag = compute(conn, policies, verbose)
    audit_result = audit(conn, rows_by_policy, verbose) if do_audit else {}
    applied = apply(conn, rows_by_policy, verbose)
    write_log(conn, rows_by_policy, audit_result)
    return {"definition_ver": ASOF_VER, "diag": diag,
            "audit": audit_result.get("summary", {}), "applied": applied}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="as-of 切面特征：按特征窗口终点截断事实，消除后视泄漏")
    ap.add_argument("--db", default=str(DB_PATH), help="目标数据库路径")
    ap.add_argument("--policy", choices=[*POLICIES, "both"], default="both",
                    help="锚点政策，默认 both（主口径 + 稳健性对照）")
    ap.add_argument("--dry-run", action="store_true", help="只计算与审计，不写库（连 DDL 也不执行）")
    ap.add_argument("--no-audit", action="store_true", help="跳过泄漏审计")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"[fatal] 数据库不存在：{db}", file=sys.stderr)
        return 2
    policies = POLICIES if args.policy == "both" else (args.policy,)
    print(f"[cfg] db={db}  ver={ASOF_VER}  policy={'+'.join(policies)}  dry_run={args.dry_run}")

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        if not args.dry_run:
            ensure_schema(conn, verbose=True)     # 须先于任何数据事务
        rows_by_policy, diag = compute(conn, policies, verbose=True)
        if not args.no_audit:
            audit_result = audit(conn, rows_by_policy, verbose=True)
            print("\n[audit summary]", json.dumps(audit_result["summary"],
                                                 ensure_ascii=False, indent=2))
        else:
            audit_result = {}
        if args.dry_run:
            print("[dry] 未写库（DDL / 数据 / 日志均未改动）")
            return 0
        applied = apply(conn, rows_by_policy, verbose=True)
        write_log(conn, rows_by_policy, audit_result)
        print("\n[done]", json.dumps({"applied": applied, "definition_ver": ASOF_VER},
                                    ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
