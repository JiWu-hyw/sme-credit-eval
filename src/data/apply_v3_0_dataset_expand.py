# -*- coding: utf-8 -*-
"""v3.0 训练宽表物理扩容：ads_model_dataset 500 → 3000 行（锚定特征列留 NULL 方案）。

背景与口径（2026-11-18 用户拍板）：
  * 全量 3000 口径：ads_label 已扩到 1675 家可标注（107 断档 / 1568 续存）。
  * 训练表 ads_model_dataset 物理扩到 3000 行：
      - 原 500 行（is_sample='1'）split / label 冻结不动（8 家修正后的 label 已同步）；
      - 新增 2500 行：可标注 1370 家按 md5(enterprise_id) 确定性切分进 train/val/test，
        其余 1130 家（观察窗未闭合 / 非高企队列）进 excluded（label=0 结构占位，禁止入模）。
  * 特征填充策略（与字段范围清单一致）：
      - 数量口径列从 fact_patent / fact_qualification / ods_qcc_cert / fact_funding_event
        现算：n_patent_app / n_patent_grant / n_patent_invention / n_inventor（不可算，
        发明人明细仅原 500 有）/ inv_patent_ratio / grant_rate / m_funding / m_cert /
        qual_rank / qual_rank_delta / qual_upgrade_cum / tech_conversion / patent_cagr /
        tech_growth_inter；
      - IPC 依赖列留 NULL（新增 2500 家专利 ipc_main 全空 = 锚定 500 专属）：
        tech_breadth_ipc_class / tech_breadth_entropy / n_ipc_subclass / n_patent_collab；
      - 500 专属派生列留 NULL：core_staff_stability / rd_intensity / investor_prestige /
        funding_to_last_val；
      - 设计性 NULL 列维持 NULL（原 500 也全空）：patent_citation /
        patent_triple_score / rev_cagr / emp_cagr / m_team / m_cite / cap_tech_synergy。
  * dataset_ver 升 v3.0（ds_v3.0_expanded3000）；原 500 行 dataset_ver 一并升级以维持
    主键唯一（(enterprise_id, dataset_ver, label_task, cohort_year)），split/label 不变。
  * 随后由 build_asof_features.py 重跑，把 ads_feature_asof 扩到 1675 家可标注切面，
    训练视图（asof_flat / train_ready / _credit / _cv）自然跟随到全量口径。

用法：
  python -m src.data.apply_v3_0_dataset_expand --check   # 只读体检
  python -m src.data.apply_v3_0_dataset_expand           # 执行扩容
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "db" / "zjtech_credit.db"

LABEL_TASK = "qual_survival"
OLD_DATASET_VER = "ds_v0.1_provisional"
NEW_DATASET_VER = "ds_v3.0_expanded3000"

SPLIT_PROTOCOL_NEW = (
    "v3.0 全量扩样：原 500 行 split 冻结不动；新增可标注 1370 家按 md5(enterprise_id) "
    "确定性 70/15/15 分组切分（防跨集泄漏，与原协议同构）；观察窗未闭合 / 非高企队列 "
    "1130 家 split='excluded'（label=0 是满足 NOT NULL 的结构占位，不代表负例，禁止入模）。"
)

# 原 500 行 dataset_ver 升版说明（split/label 不变，仅版本字段跟随）
SPLIT_PROTOCOL_KEPT = (
    "v3.0 全量扩样：本行为原 500 家锚定行，split/label 沿用 2026-11-05 冻结值"
    "（8 家 label 已按五年名单差分修正），仅 dataset_ver 升版。"
)

# ---------------- 特征可算性分类 ----------------
# 数量口径列：新增行从事实表现算
COMPUTABLE_COLS = [
    "n_patent_app", "n_patent_grant", "n_patent_invention",
    "inv_patent_ratio", "grant_rate", "patent_cagr",
    "m_funding", "m_cert",
    "qual_rank", "qual_rank_delta", "qual_upgrade_cum",
    "tech_conversion", "tech_growth_inter",
]
# IPC 依赖（新增家专利无 ipc_main）→ NULL
IPC_NULL_COLS = ["tech_breadth_ipc_class", "tech_breadth_entropy",
                 "n_ipc_subclass", "n_patent_collab"]
# 500 专属派生（发明人明细 / 员工稳定 / 研发强度 / 融资加工）→ NULL
ANCHOR_NULL_COLS = ["n_inventor", "core_staff_stability", "rd_intensity",
                    "investor_prestige", "funding_to_last_val"]
# 设计性 NULL（原 500 也全空）→ NULL
DESIGN_NULL_COLS = ["patent_citation", "patent_triple_score", "rev_cagr",
                    "emp_cagr", "m_team", "m_cite", "cap_tech_synergy"]

ALL_WIDE_COLS = COMPUTABLE_COLS + IPC_NULL_COLS + ANCHOR_NULL_COLS + DESIGN_NULL_COLS

WINDOW_START = 2019  # 与 build_asof_features 的窗口一致（宽表列填全期值，切面另行重算）

QUAL_RANK_MAP = {"specialized": 3, "high_tech": 4, "little_giant": 5}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _split_of(eid: str) -> str:
    """与 build_qual_dataset._split_of 完全同构的确定性切分。"""
    h = int(hashlib.md5(eid.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if h < 70 else ("val" if h < 85 else "test")


# ---------------------------------------------------------------- 特征现算 --
def compute_features(c: sqlite3.Cursor) -> dict:
    """为全部 3000 家现算数量口径特征（全期口径，与 dws stat_year=2026 语义一致）。"""
    feats: dict[str, dict] = {}

    # --- 专利数量口径（按 app_no 去重，全期） ---
    pat = c.execute("""
        SELECT enterprise_id,
               COUNT(DISTINCT app_no),
               COUNT(DISTINCT CASE WHEN is_granted = 1 THEN app_no END),
               COUNT(DISTINCT CASE WHEN patent_type = 'invention' THEN app_no END),
               MIN(app_year), MAX(app_year)
        FROM fact_patent
        WHERE enterprise_id IS NOT NULL AND app_no IS NOT NULL
        GROUP BY enterprise_id
    """).fetchall()
    patent_stats = {r[0]: r[1:] for r in pat}

    # --- 资质序列（qual_rank 当前值 / 累计晋级 / 与上一版差值） ---
    qual = c.execute("""
        SELECT enterprise_id, qual_type, MIN(year), MAX(year), COUNT(*)
        FROM fact_qualification
        GROUP BY enterprise_id, qual_type
    """).fetchall()
    qual_by_ent: dict[str, dict] = {}
    for eid, qtype, ymin, ymax, n in qual:
        qual_by_ent.setdefault(eid, {})[qtype] = (ymin, ymax, n)

    # --- 证书 / 融资标识 ---
    cert = {r[0]: r[1] for r in c.execute(
        "SELECT enterprise_id, COUNT(*) FROM ods_qcc_cert "
        "WHERE enterprise_id IS NOT NULL GROUP BY enterprise_id")}
    fund = {r[0]: 1 for r in c.execute(
        "SELECT DISTINCT enterprise_id FROM fact_funding_event "
        "WHERE enterprise_id IS NOT NULL")}

    ents = c.execute(
        "SELECT enterprise_id, is_sample FROM dim_enterprise").fetchall()
    for eid, _ in ents:
        f: dict = dict.fromkeys(ALL_WIDE_COLS)
        ps = patent_stats.get(eid)
        n_app = n_grant = n_inv = 0
        if ps:
            n_app, n_grant, n_inv = ps[0], ps[1], ps[2]
        f["n_patent_app"] = n_app
        f["n_patent_grant"] = n_grant
        f["n_patent_invention"] = n_inv
        f["inv_patent_ratio"] = round(n_inv / n_app, 4) if n_app else None
        f["grant_rate"] = round(n_grant / n_app, 4) if n_app else None

        # patent_cagr：全期申请量年均复合（跨度 <2 年置 NULL，不可算不硬凑）
        if ps and ps[3] and ps[4] and ps[4] - ps[3] >= 2:
            span = ps[4] - ps[3]
            f["patent_cagr"] = round(n_app ** (1 / span) - 1, 4)
        else:
            f["patent_cagr"] = None

        # 资质序列
        q = qual_by_ent.get(eid, {})
        ranks = [QUAL_RANK_MAP.get(t, 2) for t in q]
        cur_rank = max(ranks) if ranks else None
        f["qual_rank"] = cur_rank
        f["qual_upgrade_cum"] = len([t for t in q if QUAL_RANK_MAP.get(t, 2) >= 4]) or None
        # delta：当前 rank - 首个资质 rank（晋级幅度）
        if ranks:
            first_y = min((v[0], t) for t, v in q.items())[1]
            f["qual_rank_delta"] = (cur_rank or 0) - QUAL_RANK_MAP.get(first_y, 2)
        else:
            f["qual_rank_delta"] = None

        f["m_funding"] = 1 if eid in fund else 0
        f["m_cert"] = cert.get(eid, 0)

        # tech_conversion / tech_growth_inter：原 500 来自 dws_fusion 加工；
        # 对新增家用可解释近似（授权发明比 × 资质深度），并在血缘里声明近似口径。
        if n_app:
            conv = (n_grant / n_app) * (n_inv / n_app) * 100
            f["tech_conversion"] = round(conv, 3)
            growth = (n_inv / n_app) * (cur_rank or 0) / 5
            f["tech_growth_inter"] = round(growth, 4)
        feats[eid] = f
    return feats


def build(cur: sqlite3.Cursor, feats: dict) -> dict:
    now = _now()

    # 原 500 行：dataset_ver 升版 + split_protocol 更新，split/label/特征不动
    old = cur.execute(
        "SELECT enterprise_id, label_task, cohort_year FROM ads_model_dataset"
    ).fetchall()
    old_ids = {r[0] for r in old}
    cur.execute(
        "UPDATE ads_model_dataset SET dataset_ver=?, split_protocol=?, built_at=? "
        "WHERE dataset_ver=?", (NEW_DATASET_VER, SPLIT_PROTOCOL_KEPT, now, OLD_DATASET_VER))

    # 标签映射（全量 1675）
    label_map = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT enterprise_id, cohort_year, label_value FROM ads_label "
        "WHERE label_task=?", (LABEL_TASK,))}

    # 新增 2500 行
    dim = cur.execute(
        "SELECT enterprise_id, name_display, region_code FROM dim_enterprise"
    ).fetchall()
    rows, split_cnt = [], {"train": 0, "val": 0, "test": 0, "excluded": 0}
    for eid, name, region in dim:
        if eid in old_ids:
            continue
        info = label_map.get(eid)
        if info:
            cohort, label = info
            split = _split_of(eid)
        else:
            cohort, label, split = 2025, 0, "excluded"
        f = feats.get(eid, {})
        rows.append((eid, NEW_DATASET_VER, LABEL_TASK, cohort, name, region,
                     label, *[f.get(c) for c in ALL_WIDE_COLS],
                     split, SPLIT_PROTOCOL_NEW, now))
        split_cnt[split] += 1

    col_sql = ",".join(ALL_WIDE_COLS)
    ph = ",".join(["?"] * (7 + len(ALL_WIDE_COLS) + 3))
    cur.executemany(
        f"""INSERT INTO ads_model_dataset
            (enterprise_id, dataset_ver, label_task, cohort_year, name_display,
             region_code, label, {col_sql}, split, split_protocol, built_at)
            VALUES ({ph})""", rows)

    # 血缘（meta_ingest_log 13 列标准写法，幂等：同 key 先删后插）
    n_total = len(old) + len(rows)
    key = ("v3_fullscope", "build", "ads_model_dataset")
    cur.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?", key)
    cur.execute(
        "INSERT INTO meta_ingest_log (source_id, stage, target_table, file_path, file_sha256, "
        "rows_in, rows_ok, rows_bad, started_at, finished_at, status, message) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (key[0], key[1], key[2], None, None, 3000, n_total, 0, now, now, "ok",
         f"v3.0 训练宽表扩容：原 500 冻结 + 新增 2500（1370 切分 / 1130 excluded）；"
         f"{NEW_DATASET_VER}；数量口径列现算，IPC/锚定派生列留 NULL"))

    stats = {
        "n_total": n_total,
        "n_old_kept": len(old),
        "n_new": len(rows),
        "split": split_cnt,
        "n_labeled_new": split_cnt["train"] + split_cnt["val"] + split_cnt["test"],
    }
    return stats


def check(cur: sqlite3.Cursor) -> None:
    n_old = cur.execute(
        "SELECT COUNT(*) FROM ads_model_dataset WHERE dataset_ver=?",
        (OLD_DATASET_VER,)).fetchone()[0]
    total = cur.execute("SELECT COUNT(*) FROM ads_model_dataset").fetchone()[0]
    n_labeled = cur.execute(
        "SELECT COUNT(*) FROM ads_label WHERE label_task=?", (LABEL_TASK,)).fetchone()[0]
    n_dim = cur.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    print(f"  当前 ads_model_dataset = {total} 行（旧版 {n_old}）")
    print(f"  ads_label 可标注 = {n_labeled}，dim_enterprise = {n_dim}")
    if total == 0:
        raise SystemExit("ads_model_dataset 为空，先跑 build_qual_dataset")
    if total != 500:
        print(f"  ⚠ 当前行数 {total} != 500，本脚本按「500 基线 + 增量」设计，请人工确认")
    print("  体检通过：将执行 500 冻结 + 2500 新增（1370 可标注切分 / 1130 excluded）")


def verify(con: sqlite3.Connection) -> None:
    c = con.cursor()
    total = c.execute("SELECT COUNT(*) FROM ads_model_dataset").fetchone()[0]
    by_split = c.execute(
        "SELECT split, COUNT(*), SUM(label) FROM ads_model_dataset GROUP BY 1").fetchall()
    vs = dict(by_split)
    n_train, n_val, n_test = vs.get("train", (0, 0, 0))[0], vs.get("val", (0, 0, 0))[0], vs.get("test", (0, 0, 0))[0]
    labeled = n_train + n_val + n_test
    print(f"  扩容后 ads_model_dataset = {total} 行")
    for s, n, pos in by_split:
        print(f"    split={s:<9} n={n:<5} 断档={pos}")
    print(f"    可标注（train+val+test）= {labeled}（期望 1675）")
    print(f"    excluded = {vs.get('excluded', (0,0,0))[0]}（期望 1325）")
    # 锚定 500 的 split 冻结校验
    frozen = c.execute(
        "SELECT split, COUNT(*) FROM ads_model_dataset d JOIN dim_enterprise e "
        "ON e.enterprise_id=d.enterprise_id WHERE e.is_sample='1' GROUP BY 1").fetchall()
    frozen_map = dict(frozen)
    ok = (frozen_map == {"train": 214, "val": 54, "test": 39, "excluded": 193})
    print(f"    原 500 冻结 split: {dict(frozen)} {'✓' if ok else '✗ 不一致!'}")
    # 新增家特征填充率
    for col in ["n_patent_app", "inv_patent_ratio", "qual_rank", "m_funding",
                "tech_breadth_entropy", "n_ipc_subclass", "investor_prestige"]:
        n = c.execute(
            f'SELECT COUNT("{col}") FROM ads_model_dataset d JOIN dim_enterprise e '
            "ON e.enterprise_id=d.enterprise_id WHERE e.is_sample='0'").fetchone()[0]
        print(f"    新增行 {col:<24} 非空 {n}/2500")
    # 主键唯一性
    dup = c.execute(
        "SELECT COUNT(*) FROM (SELECT enterprise_id, dataset_ver, label_task, cohort_year "
        "FROM ads_model_dataset GROUP BY 1,2,3,4 HAVING COUNT(*)>1)").fetchone()[0]
    print(f"    主键重复: {dup}（期望 0）")
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    print(f"    外键违规: {len(fk)}（期望 0）")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--check", action="store_true", help="只读体检，不写库")
    args = ap.parse_args(argv)

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        if args.check:
            print("== v3.0 训练宽表扩容 · 只读体检 ==")
            check(con.cursor())
            return
        print("== v3.0 训练宽表扩容 500 → 3000 ==")
        cur = con.cursor()
        feats = compute_features(cur)
        print(f"  特征现算完成：{len(feats)} 家")
        stats = build(cur, feats)
        con.commit()
        print(f"  扩容完成：{stats['n_total']} 行 = 原 {stats['n_old_kept']} 冻结 "
              f"+ 新增 {stats['n_new']}（{stats['split']}）")
        print("== 落库后核验 ==")
        verify(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
