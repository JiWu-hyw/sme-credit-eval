"""
高企名单 → 资质事件 → 弱标签 → 建模宽表（幂等、可重跑）。

数据链路
--------
1. _data_collect/out/datasets/高企名单_浙江_2021-2025/*.csv
   → ods_qual_notice（公告级）+ ods_qual_company_raw（名单原文行）。
2. fact_qualification 的 high_tech 逐年记录做差分
   → fact_qualification_event（first_grant / renew），并回填 is_renewal。
3. 首次认定年 + 3 年为资格到期年；仅对「到期年 <= OBS_END」的完整观察窗队列
   生成 ads_label（弱标签：到期年未观测到重新认定），再拼 DWS 特征灌
   ads_model_dataset，供 v_model_dataset_flat 消费。

口径边界（重要，勿擅自放宽）
---------------------------
* 这些 CSV 是「当年新认定/备案公示名单」（增量），不是存量有效名单。
  实测跨年留存仅 0.1%~0.3%，高企资格有效期 3 年，因此「某年未出现」
  不能推断为资格失效。
* 本地没有「复核未通过 / 撤销 / 取消资格」公告，故 label_value=1 只表示
  「到期年及之后未观测到重新认定」，是**弱标签 / 代理**，不等同于
  官方复核未通过。可能混淆：名单采集遗漏、企业更名、注销迁出、主动放弃。
* 观察窗未结束（首次认定 2023+）或无 high_tech 记录的企业不生成标签，
  在宽表中 split='excluded'，label=0 仅为满足 NOT NULL 的结构占位。
* 升版规则：导师确认正式判定口径、或补齐撤销公告后，必须升
  DEFINITION_VER / DATASET_VER 并重跑，不得原地改写旧口径。

用法
----
    python -m src.data.build_qual_dataset              # 完整执行（Step 4 补算 + Step 5 as-of 切面）
    python -m src.data.build_qual_dataset --dry-run    # 只预览，不写库
    python -m src.data.build_qual_dataset --skip-ods   # 调试：跳过 10 万行 ODS 灌入
    python -m src.data.build_qual_dataset --no-derived # 跳过 Step 4 特征补算（不推荐）
    python -m src.data.build_qual_dataset --no-asof    # 跳过 Step 5 as-of 切面（不推荐）

Step 4 由 build_derived_features.run() 承担：宽表重建后自动补算
tech_breadth_entropy / core_staff_stability / qual_rank_delta /
qual_upgrade_cum / m_cert / tech_conversion 并回写 ads_model_dataset，
保证一条命令产出完整宽表。单独重跑特征补算：
python -m src.data.build_derived_features

Step 5 由 build_asof_features.run() 承担：按字典「cohort_year = 特征窗口终点」
截断事实，生成**无泄漏**的 ads_feature_asof 与 v_model_dataset_asof_flat。
⚠️ ads_model_dataset 是 2020–2025 全量快照，含标签窗口内信息，只能作为
泄漏基线；训练必须改用 v_model_dataset_asof_flat 且限定
as_of_policy='cohort'。单独重跑：python -m src.data.build_asof_features

注意：本脚本只清理由自己产出的 5 张表，不动 dim_enterprise / fact_patent /
ads_graph_* / fact_qualification 的原有行（仅 UPDATE is_renewal / valid_until）。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[2]          # _backend/
_PROJECT = _BACKEND.parent           # 工作区根
DB_PATH = _BACKEND / "data" / "db" / "zjtech_credit.db"
LIST_DIR = _PROJECT / "_data_collect" / "out" / "datasets" / "高企名单_浙江_2021-2025"

SOURCE_ID = "zj_high_tech"
QUAL_TYPE = "high_tech"
VALID_YEARS = 3
OBS_END = 2025                       # 名单覆盖的最后一个认定年度

DEFINITION_VER = "qual_survival_proxy_v0.1"
DATASET_VER = "ds_v0.1_provisional"
LABEL_TASK = "qual_survival"
LABEL_NAME = "高企资格到期未见重新认定（弱标签）"

LABEL_NOTE = (
    "弱标签/代理，不等同官方复核未通过。定义：首次 high_tech 认定年+3 为资格到期年，"
    "若 fact_qualification 在到期年及之后无 high_tech 记录则 label_value=1。"
    "混淆来源：名单采集遗漏、企业更名导致名称匹配失败、注销/迁出浙江、主动放弃复核。"
    "观察窗终点 2025。本地无撤销/复核未通过公告，正式口径待导师确认后升版重跑。"
)
SPLIT_PROTOCOL = (
    "按 md5(enterprise_id) 确定性 70/15/15 分组切分（防跨集泄漏）；"
    "split='excluded' 的 193 行观察窗未结束或无 high_tech 记录，"
    "其 label=0 是满足 NOT NULL 的结构占位，不代表续存样本，禁止入模。"
)

# 企业名后缀：用于剔除 PDF/网页抽取时粘进来的标题行
_SUFFIX = re.compile(
    r"(有限公司|有限责任公司|股份有限公司|集团有限公司|研究院|设计院|研究所|"
    r"集团公司|合作社|事务所|总厂|工厂|分公司|中心|厂)$"
)
_BAD_TITLE = re.compile(r"(序号|企业名称|证书编号|公示名单|备案|附件|认定机构|高新技术企业)")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _upsert_ingest_log(cur: sqlite3.Cursor, stage: str, target: str, payload: tuple) -> None:
    """幂等写 ingest 日志：复用已有 log_id，避免 DELETE+INSERT 导致自增 id 漂移。

    payload = (file_path, file_sha256, rows_in, rows_ok, rows_bad,
               started_at, finished_at, status, message)
    """
    key = (SOURCE_ID, stage, target)
    row = cur.execute(
        "SELECT log_id FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?", key
    ).fetchone()
    cur.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?", key
    )
    cols = ["source_id", "stage", "target_table", "file_path", "file_sha256",
            "rows_in", "rows_ok", "rows_bad", "started_at", "finished_at",
            "status", "message"]
    vals = [SOURCE_ID, stage, target, *payload]
    if row:
        cols = ["log_id", *cols]
        vals = [row[0], *vals]
    cur.execute(
        f"INSERT INTO meta_ingest_log ({','.join(cols)}) "
        f"VALUES ({','.join(['?'] * len(cols))})",
        vals,
    )


def _norm(s: str) -> str:
    """与 dim_enterprise.name_norm 对齐的轻量规范化（去空白/括号/标点）。"""
    return re.sub(r"[\s（）()·\-—_、,，.。]", "", s or "")


def clean_notice_file(path: Path):
    """解析单份名单 CSV，返回 [(seq_no, company_name, raw_payload), ...] 与统计。

    首行常被 PDF 抽取污染成「附件…序号企业名称1XX有限公司」或纯标题，
    统一按规则剥离；无法可靠还原的整行丢弃并计数，绝不把标题当企业名灌库。
    """
    raw = path.read_bytes()
    enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    lines = [l for l in raw.decode(enc, errors="replace").splitlines() if l.strip()]
    rows = list(csv.reader(lines))
    body = rows[1:]
    out, n_first_cleaned, n_first_dropped, n_bad = [], 0, 0, 0

    for idx, r in enumerate(body):
        name = (r[1] if len(r) > 1 else "").strip()
        if not name:
            continue
        if idx == 0 and (len(name) > 30 or not _SUFFIX.search(name) or _BAD_TITLE.search(name)):
            # 标题 + 表头 + 序号 + 企业名粘连：取最后一个阿拉伯数字之后的尾部
            digits = list(re.finditer(r"\d", name))
            tail = name[digits[-1].start() + 1:] if digits else ""
            if _SUFFIX.search(tail) and 4 <= len(tail) <= 60 and not _BAD_TITLE.search(tail):
                out.append((1, tail, json.dumps({"first_row_cleaned": True}, ensure_ascii=False)))
                n_first_cleaned += 1
            else:
                n_first_dropped += 1
            continue
        if _SUFFIX.search(name) and len(name) <= 60 and not _BAD_TITLE.search(name):
            out.append((idx + 1, name, None))
        else:
            n_bad += 1
    return out, {
        "rows_in": len(body),
        "rows_ok": len(out),
        "rows_bad": n_bad + n_first_dropped,
        "first_row_cleaned": n_first_cleaned,
        "first_row_dropped": n_first_dropped,
    }


def load_ods(conn: sqlite3.Connection, list_dir: Path, verbose: bool = True) -> dict:
    """灌入 ods_qual_notice + ods_qual_company_raw（先清理由本脚本产出的旧数据）。"""
    cur = conn.cursor()
    cur.execute("DELETE FROM ods_qual_company_raw")
    cur.execute("DELETE FROM ods_qual_notice")
    conn.commit()

    files = sorted(list_dir.glob("hightech_*.csv"))
    if not files:
        raise FileNotFoundError(f"未找到名单 CSV：{list_dir}")

    file_hash = hashlib.sha256()
    total_in = total_ok = total_bad = 0
    notices = []

    for notice_id, p in enumerate(files, 1):
        m = re.match(r"hightech_(\d{4})_(.+)\.csv$", p.name)
        year = int(m.group(1)) if m else None
        tag = m.group(2) if m else p.stem
        file_hash.update(p.read_bytes())
        rows, st = clean_notice_file(p)
        total_in += st["rows_in"]
        total_ok += st["rows_ok"]
        total_bad += st["rows_bad"]

        cur.execute(
            """INSERT INTO ods_qual_notice
               (notice_id, qual_type, batch_no, publish_date, year, region_code,
                region_name, issuer, source_url, source_id, file_path, raw_payload, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                notice_id, QUAL_TYPE, f"high_tech-{year}-{tag[:8]}", f"{year}-12-31" if year else None,
                year, "330000", "浙江省",
                "全国高新技术企业认定管理工作领导小组办公室",
                "http://www.innocom.gov.cn/", SOURCE_ID, str(p),
                json.dumps({
                    "rows_in": st["rows_in"], "rows_ok": st["rows_ok"],
                    "rows_bad": st["rows_bad"],
                    "first_row_cleaned": st["first_row_cleaned"],
                    "notice_title_unavailable": True,
                    "note": "公告 URL/标题元数据未随采集落盘，batch_no 用年度+文件哈希标识",
                }, ensure_ascii=False),
                _now(),
            ),
        )
        payload = [
            (notice_id, seq, name, None, raw_payload, _now())
            for seq, name, raw_payload in rows
        ]
        cur.executemany(
            """INSERT INTO ods_qual_company_raw
               (notice_id, seq_no, company_name, region_raw, raw_payload, ingested_at)
               VALUES (?,?,?,?,?,?)""",
            payload,
        )
        notices.append((p.name, st["rows_ok"]))
        if verbose:
            print(f"  [ods] {p.name:<32} ok={st['rows_ok']:>6} bad={st['rows_bad']:>3} "
                  f"first_cleaned={st['first_row_cleaned']}")
        conn.commit()

    _upsert_ingest_log(
        cur, "parse", "ods_qual_notice+ods_qual_company_raw",
        (str(list_dir), file_hash.hexdigest(), total_in, total_ok, total_bad,
         _now(), _now(), "warn" if total_bad else "ok",
         f"{len(files)} 份年度备案公示名单；首行 PDF 标题污染已清洗；公告标题/URL 元数据缺失"),
    )
    conn.commit()
    return {"n_notices": len(files), "rows_in": total_in, "rows_ok": total_ok,
            "rows_bad": total_bad, "files": notices}


def build_events(conn: sqlite3.Connection, verbose: bool = True) -> dict:
    """基于 fact_qualification 的 high_tech 逐年记录做差分，生成事件表。"""
    cur = conn.cursor()
    cur.execute("DELETE FROM fact_qualification_event")

    rows = cur.execute(
        "SELECT qual_id, enterprise_id, year FROM fact_qualification "
        "WHERE qual_type=? ORDER BY enterprise_id, year, qual_id", (QUAL_TYPE,)
    ).fetchall()
    by_ent = defaultdict(list)
    for qid, eid, year in rows:
        by_ent[eid].append((int(year), qid))

    events = []
    renew_qids = []
    ambiguous = []
    for eid, seq in by_ent.items():
        seq.sort()
        first_year, first_qid = seq[0]
        events.append((eid, f"{first_year}-12-31", first_year, "first_grant",
                       QUAL_TYPE, QUAL_TYPE, 0, first_qid,
                       "首次 high_tech 备案公示；event_date 用年末代理（公告原文日期未落盘）"))
        prev = first_year
        for year, qid in seq[1:]:
            gap = year - prev
            if gap >= VALID_YEARS:
                events.append((eid, f"{year}-12-31", year, "renew",
                               QUAL_TYPE, QUAL_TYPE, 0, qid,
                               f"距上次备案 {gap} 年（>= 有效期 {VALID_YEARS} 年），判定为到期重新认定"))
                renew_qids.append(qid)
                prev = year
            else:
                # 间隔不足一个有效期：可能是同批补录 / 更名 / 名单重复。
                # 不生成 renew 事件，也不推进 prev，避免伪造重新认定事实。
                ambiguous.append((eid, prev, year, gap, qid))

    cur.executemany(
        """INSERT INTO fact_qualification_event
           (enterprise_id, event_date, event_year, event_type, from_qual, to_qual,
            rank_delta, evidence_qual_id, note)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        events,
    )

    # 回填：仅改 is_renewal / 补 valid_until 的空值，不动 status / year / batch_no
    cur.execute(
        f"UPDATE fact_qualification SET is_renewal=0 WHERE qual_type='{QUAL_TYPE}'"
    )
    if renew_qids:
        cur.executemany(
            "UPDATE fact_qualification SET is_renewal=1 WHERE qual_id=?",
            [(q,) for q in renew_qids],
        )
    cur.execute(
        f"UPDATE fact_qualification SET valid_until=CAST(year + {VALID_YEARS} AS TEXT) "
        f"WHERE qual_type='{QUAL_TYPE}' AND (valid_until IS NULL OR valid_until='')"
    )
    conn.commit()

    types = dict(cur.execute(
        "SELECT event_type,count(*) FROM fact_qualification_event GROUP BY event_type"
    ).fetchall())
    summary = {"n_events": len(events), "by_type": types,
               "n_enterprise": len(by_ent), "n_renewal_flagged": len(renew_qids),
               "n_ambiguous": len(ambiguous)}
    if verbose:
        print(f"  [event] {summary['n_events']} 条 / {len(by_ent)} 家  {types}  "
              f"is_renewal 回填 {len(renew_qids)} 行；"
              f"间隔<{VALID_YEARS}年未生成事件 {len(ambiguous)} 条")
    return summary


def _split_of(eid: str) -> str:
    h = int(hashlib.md5(eid.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if h < 70 else ("val" if h < 85 else "test")


def build_labels_and_dataset(conn: sqlite3.Connection, verbose: bool = True) -> dict:
    """生成 ads_label（完整观察窗队列）+ ads_model_dataset（500 行，其余 excluded）。"""
    cur = conn.cursor()
    cur.execute("DELETE FROM ads_model_dataset")
    cur.execute("DELETE FROM ads_label")

    rows = cur.execute(
        "SELECT qual_id, enterprise_id, year FROM fact_qualification "
        "WHERE qual_type=? ORDER BY enterprise_id, year, qual_id", (QUAL_TYPE,)
    ).fetchall()
    by_ent = defaultdict(list)
    for qid, eid, year in rows:
        by_ent[eid].append((int(year), qid))

    # 1) 只对观察窗完整的队列打标
    labels = []
    label_of = {}
    for eid, seq in by_ent.items():
        years = sorted(y for y, _ in seq)
        first_year = years[0]
        due = first_year + VALID_YEARS
        if due > OBS_END:
            continue
        renewed = any(y >= due for y in years)
        value = 0 if renewed else 1
        label_of[eid] = (first_year, value)
        labels.append((
            eid, LABEL_TASK, first_year, value, LABEL_NAME,
            f"{first_year}-01-01", f"{OBS_END}-12-31", DEFINITION_VER,
            len(years), LABEL_NOTE,
        ))
    cur.executemany(
        """INSERT INTO ads_label
           (enterprise_id, label_task, cohort_year, label_value, label_name,
            observation_start, observation_end, definition_ver, n_events, note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        labels,
    )

    # 2) 宽表：500 行全量，未打标的行 split='excluded'
    feat = {}
    for table, cols in [
        ("dws_tech_feature", ["inv_patent_ratio", "grant_rate", "patent_citation",
                              "tech_breadth_ipc_class", "tech_breadth_entropy",
                              "patent_triple_score", "core_staff_stability", "rd_intensity",
                              "n_patent_app", "n_patent_grant", "n_patent_invention",
                              "n_inventor", "n_ipc_subclass", "n_patent_collab"]),
        ("dws_growth_feature", ["rev_cagr", "emp_cagr", "patent_cagr", "funding_to_last_val",
                                "m_funding", "m_team", "m_cert", "m_cite", "investor_prestige",
                                "qual_rank", "qual_rank_delta", "qual_upgrade_cum"]),
        ("dws_fusion_feature", ["tech_conversion", "cap_tech_synergy", "tech_growth_inter"]),
    ]:
        sel = ",".join(cols)
        for r in cur.execute(f"SELECT enterprise_id,{sel} FROM {table}").fetchall():
            feat.setdefault(r[0], {}).update(dict(zip(cols, r[1:])))

    WIDE_COLS = [
        "inv_patent_ratio", "grant_rate", "patent_citation",
        "tech_breadth_ipc_class", "tech_breadth_entropy",
        "patent_triple_score", "core_staff_stability", "rd_intensity",
        "n_patent_app", "n_patent_grant", "n_patent_invention",
        "n_inventor", "n_ipc_subclass", "n_patent_collab",
        "rev_cagr", "emp_cagr", "patent_cagr", "funding_to_last_val",
        "m_funding", "m_team", "m_cert", "m_cite", "investor_prestige",
        "qual_rank", "qual_rank_delta", "qual_upgrade_cum",
        "tech_conversion", "cap_tech_synergy", "tech_growth_inter",
    ]

    ents = cur.execute(
        "SELECT enterprise_id, name_display, region_code FROM dim_enterprise"
    ).fetchall()
    dataset_rows, split_cnt = [], defaultdict(int)
    now = _now()
    for eid, name, region in ents:
        info = label_of.get(eid)
        if info:
            cohort, label = info
            split = _split_of(eid)
        else:
            # 无完整观察窗：结构占位，禁止入模
            cohort, label, split = OBS_END, 0, "excluded"
        f = feat.get(eid, {})
        dataset_rows.append(
            (eid, DATASET_VER, LABEL_TASK, cohort, name, region, label,
             *[f.get(c) for c in WIDE_COLS], split, SPLIT_PROTOCOL, now)
        )
        split_cnt[split] += 1

    col_sql = ",".join(WIDE_COLS)
    placeholders = ",".join(["?"] * (7 + len(WIDE_COLS) + 3))
    cur.executemany(
        f"""INSERT INTO ads_model_dataset
            (enterprise_id, dataset_ver, label_task, cohort_year, name_display,
             region_code, label, {col_sql}, split, split_protocol, built_at)
            VALUES ({placeholders})""",
        dataset_rows,
    )

    _upsert_ingest_log(
        cur, "build", "fact_qualification_event+ads_label+ads_model_dataset",
        ("sqlite:fact_qualification", None,
         len(by_ent), len(labels), len(ents) - len(labels), _now(), _now(), "warn",
         f"{DEFINITION_VER} / {DATASET_VER}；弱标签，正式判定口径待确认后升版重跑"),
    )
    conn.commit()

    pos = sum(1 for x in labels if x[3] == 1)
    cohorts = defaultdict(lambda: [0, 0])
    for x in labels:
        cohorts[x[2]][0] += 1
        if x[3] == 1:
            cohorts[x[2]][1] += 1
    summary = {
        "n_labels": len(labels), "n_positive": pos,
        "n_negative": len(labels) - pos,
        "cohorts": {k: {"n": v[0], "positive": v[1]} for k, v in sorted(cohorts.items())},
        "n_dataset": len(dataset_rows), "split": dict(split_cnt),
        "definition_ver": DEFINITION_VER, "dataset_ver": DATASET_VER,
    }
    if verbose:
        print(f"  [label] {len(labels)} 条（断档样本 {pos} / 续存样本 {len(labels)-pos}）"
              f" cohorts={summary['cohorts']}")
        print(f"  [wide ] {len(dataset_rows)} 行  split={dict(split_cnt)}")
    return summary


def run(conn: sqlite3.Connection, list_dir: Path, skip_ods: bool = False,
        verbose: bool = True) -> dict:
    result = {}
    if not skip_ods:
        result["ods"] = load_ods(conn, list_dir, verbose)
    result["events"] = build_events(conn, verbose)
    result["labels"] = build_labels_and_dataset(conn, verbose)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="高企名单差分 → 资质事件 → 弱标签 → 建模宽表")
    ap.add_argument("--db", default=str(DB_PATH), help="目标数据库路径")
    ap.add_argument("--list-dir", default=str(LIST_DIR), help="年度名单 CSV 目录")
    ap.add_argument("--dry-run", action="store_true", help="只预览清洗统计与队列规模，不写库")
    ap.add_argument("--skip-ods", action="store_true", help="调试：跳过 ODS 灌入")
    ap.add_argument("--no-derived", action="store_true",
                    help="跳过 Step 4 派生特征补算（默认在宽表重建后自动补算）")
    ap.add_argument("--no-asof", action="store_true",
                    help="跳过 Step 5 as-of 切面特征（默认生成，训练必需）")
    args = ap.parse_args(argv)

    list_dir = Path(args.list_dir)
    files = sorted(list_dir.glob("hightech_*.csv"))
    print(f"[cfg] db={args.db}")
    print(f"[cfg] list_dir={list_dir}  文件数={len(files)}")
    if not files:
        print("[fatal] 未找到名单 CSV", file=sys.stderr)
        return 2

    if args.dry_run:
        tin = tok = tbad = 0
        for p in files:
            _, st = clean_notice_file(p)
            tin += st["rows_in"]; tok += st["rows_ok"]; tbad += st["rows_bad"]
            print(f"  [dry] {p.name:<32} in={st['rows_in']:>6} ok={st['rows_ok']:>6} bad={st['rows_bad']:>3}")
        print(f"[dry] 合计 in={tin} ok={tok} bad={tbad}")
        conn = sqlite3.connect(args.db)
        rows = conn.execute(
            "SELECT enterprise_id,year FROM fact_qualification WHERE qual_type=?", (QUAL_TYPE,)
        ).fetchall()
        by = defaultdict(list)
        for eid, y in rows:
            by[eid].append(int(y))
        cohorts = defaultdict(lambda: [0, 0])
        for eid, ys in by.items():
            ys.sort(); due = ys[0] + VALID_YEARS
            if due <= OBS_END:
                cohorts[ys[0]][0] += 1
                if not any(y >= due for y in ys):
                    cohorts[ys[0]][1] += 1
        print(f"[dry] 可打标队列：{dict(cohorts)}  合计 {sum(v[0] for v in cohorts.values())} 家")
        conn.close()
        print("[dry] 未写库")
        return 0

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = run(conn, list_dir, skip_ods=args.skip_ods, verbose=True)
        if not args.no_derived:
            # Step 4：宽表重建后补算 6 个派生特征，避免交付出「宽表有行但特征全空」
            from . import build_derived_features as derived
            print("\n[step4] 补算派生特征并刷新宽表")
            d = derived.run(conn, refresh_wide=True, verbose=True)
            result["derived_features"] = {"definition_ver": d["definition_ver"],
                                          "stats": d["stats"]}
        if not args.no_asof:
            # Step 5：生成 as-of 切面特征（无泄漏训练入口），原宽表保持不动
            from . import build_asof_features as asof
            print("\n[step5] 生成 as-of 切面特征（消除后视泄漏）")
            a = asof.run(conn, policies=asof.POLICIES, do_audit=True, verbose=True)
            result["asof_features"] = {
                "definition_ver": a["definition_ver"],
                "applied": a["applied"],
                "leaked_features": a["audit"].get("leaked_features", []),
            }
        print("\n[done]", json.dumps(result, ensure_ascii=False, indent=2,
                                    default=lambda o: dict(o) if hasattr(o, "keys") else str(o)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
