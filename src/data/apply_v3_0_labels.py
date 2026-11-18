# -*- coding: utf-8 -*-
"""v3.0 标签并表：用五年高企名单差分结果全量重建 ads_label（可复现）。

背景：
  v1.9 的 ads_label 只有 307 行（原 500 家里 high_tech 观察窗闭合者），
  且库内 fact_qualification 漏采了部分 2024/2025 复核记录，导致 8 家误判。
  本轮用本地五年高企名单 + 企查查荣誉表交叉验证，重算 3000 家全量标签：
    · 3000 家命中高企名单 2906 家（96.9%）
    · 可标注（观察窗闭合）1675 家：断档 107 / 续存 1568，断档率 6.4%
    · 原 500 家 307 个可标注口径与库一致，其中 8 家 label_value 因名单年份补全被修正

数据源：_data_collect/out/3000家_标签_名单差分.csv（逐户 名单命中年度/荣誉年份/cohort/label）
匹配：企业名称归一（去空格/全半角括号）→ dim_enterprise.name_display，3000/3000 命中。

纪律：
  1. DELETE 旧 ads_label 全量重建（原 500 的 8 家用新标签覆盖，用户 2026-11-18 拍板）。
  2. definition_ver 升 'qual_survival_proxy_v3.0'，DATASET 口径同步升版。
  3. 只改 ads_label（及 ads_model_dataset 的 label 列由后续 build_qual_dataset 重跑），
     不动 fact_* / dim_* / 图谱 / 已发布非标签视图。
  4. 幂等：可重复执行，结果只依赖 CSV + dim_enterprise。

用法：
    python -m src.data.apply_v3_0_labels            # 执行
    python -m src.data.apply_v3_0_labels --check    # 只读校验（不写库）
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sqlite3
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(BACKEND)
DB_PATH = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")
LABEL_CSV = os.path.join(ROOT, "_data_collect", "out", "3000家_标签_名单差分.csv")

LABEL_TASK = "qual_survival"
LABEL_NAME = "高企资格到期未见重新认定（弱标签·五年名单差分）"
DEFINITION_VER = "qual_survival_proxy_v3.0"
LABEL_NOTE = (
    "弱标签/代理，不等同官方复核未通过。定义：首次 high_tech 认定年+3 为资格到期年，"
    "若到期年及之后无 high_tech 记录则 label_value=1。数据源=本地五年高企名单+企查查荣誉表交叉验证。"
    "观察窗终点 2025；2023 认定到期 2026 名单未发布故 NA。本地无撤销公告，正式口径待导师确认后升版。"
)
OBS_END = 2025


def norm(s):
    import re
    return re.sub(r"[（）()　\s]", "", str(s or ""))


def load_csv_rows():
    raw = open(LABEL_CSV, "rb").read()
    txt = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if txt is None:
        raise RuntimeError("无法解码标签 CSV: %s" % LABEL_CSV)
    return list(csv.DictReader(io.StringIO(txt)))


def build_label_records(con):
    """返回 (records, stats)。records = (eid,cohort_year,label_value,...)。"""
    rows = load_csv_rows()
    # 名称 → enterprise_id（主名 + 别名）
    name2id = {}
    for eid, name in con.execute(
            "SELECT enterprise_id,name_display FROM dim_enterprise"):
        if name:
            name2id[norm(name)] = eid
    for eid, name in con.execute(
            "SELECT enterprise_id,alias_norm FROM dim_enterprise_alias "
            "WHERE alias_norm IS NOT NULL"):
        if name:
            name2id.setdefault(norm(name), eid)

    records = []
    miss = []
    dist = Counter()
    cohort_dist = Counter()
    for r in rows:
        lab = (r.get("label") or "").strip()
        dist[lab] += 1
        if lab not in ("0", "1"):
            continue
        nm = norm(r.get("企业名称"))
        eid = name2id.get(nm)
        if not eid:
            miss.append(r.get("企业名称"))
            continue
        try:
            cohort = int(float(r.get("cohort")))
        except (ValueError, TypeError):
            continue
        value = int(lab)
        cohort_dist[cohort] += 1
        # n_events = 名单命中年度数（分号/逗号分隔的年份个数）；供 v_label_mechanism 复核
        hit = (r.get("名单命中年度") or "")
        n_events = len([y for y in hit.replace(";", ",").split(",")
                        if y.strip().isdigit()]) or None
        records.append((
            eid, LABEL_TASK, cohort, value, LABEL_NAME,
            "%d-01-01" % cohort, "%d-12-31" % OBS_END,
            DEFINITION_VER, n_events, LABEL_NOTE,
        ))
    stats = {
        "csv_total": len(rows),
        "label_dist": dict(dist),
        "cohort_dist": dict(cohort_dist),
        "n_records": len(records),
        "n_positive": sum(1 for x in records if x[3] == 1),
        "n_negative": sum(1 for x in records if x[3] == 0),
        "miss_name": miss,
    }
    return records, stats


def check(con):
    """只读校验：新标签能重建且关键数字对得上。"""
    records, stats = build_label_records(con)
    ok = True
    print("标签并表只读校验：")
    print("  CSV 总行数:", stats["csv_total"])
    print("  label 分布:", stats["label_dist"])
    print("  可标注记录:", stats["n_records"],
          "断档(1)=%d 续存(0)=%d" % (stats["n_positive"], stats["n_negative"]))
    print("  cohort 分布:", stats["cohort_dist"])
    print("  名称未匹配:", len(stats["miss_name"]))
    if stats["n_records"] != 1675:
        print("  ✗ 可标注应为 1675，实得 %d" % stats["n_records"])
        ok = False
    if stats["n_positive"] != 107:
        print("  ✗ 断档应为 107，实得 %d" % stats["n_positive"])
        ok = False
    if stats["miss_name"]:
        print("  ✗ 存在名称未匹配:", stats["miss_name"][:5])
        ok = False
    if ok:
        print("  ✅ 校验通过")
    return ok


def build(con):
    records, stats = build_label_records(con)
    if stats["miss_name"]:
        raise RuntimeError("存在名称未匹配，拒绝并表: %s" % stats["miss_name"][:5])
    cur = con.cursor()
    # 确保血缘 source 已登记（外键约束），可重复执行
    cur.execute("""INSERT OR IGNORE INTO meta_data_source
        (source_id,source_name,source_type,url,license,fetch_mode,cost_cny,
         quota_note,owner,coverage,first_fetch,last_fetch,is_active)
        VALUES ('zj_high_tech_5y','浙江五年高企备案名单差分','official_list',
                'innocom.gov.cn','public_notice','manual',0,
                '本地五年名单+企查查荣誉交叉，v3.0标签源','项目组','3000家/1675可标注',
                datetime('now'),datetime('now'),1)""")
    cur.execute("DELETE FROM ads_label WHERE label_task=?", (LABEL_TASK,))
    cur.executemany(
        """INSERT INTO ads_label
           (enterprise_id,label_task,cohort_year,label_value,label_name,
            observation_start,observation_end,definition_ver,n_events,note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""", records)
    # 登记血缘（13 列，与 meta_ingest_log DDL 一致）
    now = datetime.now().isoformat(timespec="seconds")
    cur.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?",
        ("zj_high_tech_5y", "build", "ads_label"))
    cur.execute(
        """INSERT INTO meta_ingest_log
           (source_id,stage,target_table,file_path,file_sha256,rows_in,rows_ok,
            rows_bad,started_at,finished_at,status,message)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("zj_high_tech_5y", "build", "ads_label", LABEL_CSV, "",
         stats["csv_total"], stats["n_records"],
         stats["csv_total"] - stats["n_records"], now, now, "ok",
        "%s / 断档%d 续存%d / 原500的8家按五年名单修正" %
        (DEFINITION_VER, stats["n_positive"], stats["n_negative"])))
    # 同步训练宽表 label：ads_model_dataset.label 旧值来自 500 家 CSV，
    # 8 家修正后必须与 ads_label.label_value 一致，否则 v_model_train_ready
    # 与 ads_label 逐行对账失败（训练口径与标签口径分裂）。只更新可标注企业。
    cur.execute(
        """UPDATE ads_model_dataset SET label = (
               SELECT label_value FROM ads_label l
               WHERE l.enterprise_id = ads_model_dataset.enterprise_id
                 AND l.label_task = ?)
           WHERE label_task = ?
             AND EXISTS (SELECT 1 FROM ads_label l
                         WHERE l.enterprise_id = ads_model_dataset.enterprise_id
                           AND l.label_task = ?)""",
        (LABEL_TASK, LABEL_TASK, LABEL_TASK))
    synced = cur.rowcount
    con.commit()
    print("同步 ads_model_dataset.label: %d 行" % synced)
    print("标签并表完成：ads_label 写入 %d 行（断档 %d / 续存 %d）" %
          (stats["n_records"], stats["n_positive"], stats["n_negative"]))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    con = sqlite3.connect(DB_PATH if not args.check
                          else "file:%s?mode=ro" % DB_PATH.replace("\\", "/"),
                          uri=args.check)
    try:
        if args.check:
            ok = check(con)
        else:
            build(con)
            ok = check(con)
    finally:
        con.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
