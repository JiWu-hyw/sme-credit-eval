"""
build_survival_dataset.py — 方案C：高企资格存续生存分析数据集（幂等）。

数据结构
--------
person-period 展开：每企业 t=1..3 期（高企资格 3 年有效，t=3 为续期屏障）。
特征锚点 = fy + t - 1（每期期初），复用 build_asof_features.features_at 的
14 个无泄漏特征计算（同一实现，不重复造轮子）。

事件/删失口径
------------
* event=1：仅 t=3 且完整观察窗（fy+3 <= OBS_END=2025）且到期年及之后
  无重新认定 high_tech 记录（与 ads_label 正类同口径，已通过方案A审计稳健）。
* event=0：该期期末仍存活（前瞻期除外）。
  - t=1,2：恒为 0（事件只发生在 t=3 屏障）。
  - t=3 完整窗：到期年及之后有重新认定 → 0。
* event=NULL + prospective=1：所有「期末未观察完」的行，包括
  - 2023+ 队列 t=3 行（观察窗未满），特征可用、结局未知，供前瞻风险评分
    （171 家 2023 队列 2026 年到期）；
  - 过程期 t=1/2 中期末（fy+t）已超出 OBS_END 的行，如 fy=2025 的 t=1
    （期末 2026）、fy=2024 的 t=2（期末 2026）、fy=2025 的 t=2（期末 2027）。
    这些期同样没有观察完，结局未知，必须与 t=3 前瞻行同标 prospective=1。
    （早期版本只对 t=3 置标志，导致 20 行 event=NULL 却 prospective=0，
      已修正为 prosp = 1 if event is None else 0。）
  其 t=1/2 行 event=0（过程期存活是事实）。
* 2023+ 队列若 fy+t-1 > OBS_END-? —— t 期期末 = fy+t，要求期末 <= OBS_END 才算
  已观察存活；否则该行 event 亦置 NULL（前瞻）。如 fy=2024：t=2 期末 2026>2025，
  t=2 行 event=NULL。fy=2025 同理。

split 沿用 ads_model_dataset 的企业级 md5 70/15/15 切分（train/val/test 同企业
所有期同侧，防跨期泄漏）。

QCC 外部维度（dim_enterprise 静态列）不入本表——静态快照与期初锚点无时间对齐
关系，模型侧在载入时按 enterprise_id LEFT JOIN（与 baseline_qcc 同法）。

用法
----
    python -m src.data.build_survival_dataset             # 完整构建
    python -m src.data.build_survival_dataset --dry-run   # 预览不写库
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent))

from src.data import db as dbmod                       # noqa: E402
from src.data.build_asof_features import features_at   # noqa: E402

DB = HERE.parent.parent / "data/db/zjtech_credit.db"
OBS_END = 2025          # 观察窗终点（名单覆盖 2021-2025）
VALID_YEARS = 3         # 高企资格有效期

ASOF14 = [
    "core_staff_stability", "inv_patent_ratio", "m_cert", "n_inventor",
    "n_ipc_subclass", "n_patent_app", "n_patent_grant", "n_patent_invention",
    "qual_rank", "qual_rank_delta", "qual_upgrade_cum", "tech_breadth_entropy",
    "tech_breadth_ipc_class", "tech_conversion",
]
DEFN_VER = "survival_pp_v0.1"


def ensure_schema(con: sqlite3.Connection) -> None:
    sql = (HERE / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"-- \[SURV:BEGIN\]\n(.*?)-- \[SURV:END\]", sql, re.S)
    if not m:
        raise RuntimeError("schema.sql 缺少 [SURV:BEGIN]/[SURV:END] 区块")
    con.executescript(m.group(1))


def main(dry: bool = False) -> int:
    if not DB.exists():
        raise SystemExit(f"未找到主库: {DB}")
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")
    ensure_schema(con)

    # ---- 原料（复用 as-of 计算）----
    src = load_sources_shim(con)
    # ---- 队列：有 high_tech 记录的 493 家 ----
    rows = con.execute(
        """SELECT enterprise_id, MIN(year) AS fy FROM fact_qualification
           WHERE qual_type='high_tech' GROUP BY enterprise_id ORDER BY enterprise_id"""
    ).fetchall()

    # ---- 企业级 split（沿用宽表切分）----
    split_map = dict(con.execute(
        "SELECT enterprise_id, split FROM ads_model_dataset").fetchall())

    # ---- 事件标记：到期年及之后是否有重新认定 ----
    renew_map = {}
    for eid, fy in rows:
        yrs = [y for (y,) in con.execute(
            "SELECT year FROM fact_qualification WHERE enterprise_id=? "
            "AND qual_type='high_tech' AND year >= ?", (eid, fy + VALID_YEARS))]
        renew_map[eid] = len(yrs) > 0

    pp_rows, n_event, n_cens, n_prosp = [], 0, 0, 0
    for eid, fy in rows:
        expiry = fy + VALID_YEARS
        full_window = expiry <= OBS_END
        renewed = renew_map[eid]
        split = split_map.get(eid)
        for t in (1, 2, 3):
            anchor = fy + t - 1
            end_of_period = fy + t
            if t < 3:
                # 过程期：期末 <= OBS_END 则存活事实成立；否则该期同样未观察完
                event = 0 if end_of_period <= OBS_END else None
            else:
                if full_window:
                    event = 0 if renewed else 1
                else:
                    event = None          # 前瞻行
            # 前瞻标记必须与「结局未知」严格一致，不能只对 t=3 生效：
            # fy=2025 的 t=1（期末 2026）、fy=2024/2025 的 t=2（期末 2026/2027）
            # 同样超出 OBS_END，属于未观察完的过程期，prospective 应为 1。
            prosp = 1 if event is None else 0
            if event == 1:
                n_event += 1
            elif event is None:
                n_prosp += 1
            else:
                n_cens += 1
            vals = features_at(src, eid, anchor)
            pp_rows.append(
                (eid, int(fy), t, anchor, event, prosp, split,
                 *[vals.get(c) for c in ASOF14], datetime.now().isoformat(timespec="seconds")))

    n_ent = len(rows)
    n_rows = len(pp_rows)
    print(f"[构建] 企业 {n_ent} 家 → person-period {n_rows} 行")
    print(f"       event=1: {n_event} | event=0: {n_cens} | 前瞻(NULL): {n_prosp}")
    print(f"       前瞻队列构成: 2023 队列 t=3 前瞻 {sum(1 for r in pp_rows if r[5]==1 and r[1]==2023)} 家")

    if dry:
        print("[dry-run] 未写库")
        con.close()
        return 0

    con.execute("DELETE FROM ads_survival_period")
    con.executemany(
        "INSERT INTO ads_survival_period (enterprise_id, first_year, period_t, anchor_year,"
        " event, prospective, split, %s, built_at) VALUES (?,?,?,?,?,?,?,%s,?)"
        % (",".join(ASOF14), ",".join("?" * len(ASOF14))),
        pp_rows,
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM ads_survival_period").fetchone()[0]
    print(f"[写库] ads_survival_period = {n} 行  DEFN_VER={DEFN_VER}")
    con.close()
    return 0


def load_sources_shim(con):
    """轻量适配：直接复用 build_asof_features.load_sources 的原料装载逻辑，
    但 samples 不需要（本脚本自定队列）。"""
    from src.data.build_asof_features import load_sources as _ls
    src = _ls(con)
    return src


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(main(dry=args.dry_run))
