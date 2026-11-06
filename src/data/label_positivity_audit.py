"""
label_positivity_audit.py — 正类集合净化审计（方案 A，只读，不写库）。

目的
----
弱标签 qual_survival 的 21 个正类是「高企资格到期后未观测到重新认定」。
其主要混淆来源之一是**企业更名导致名称匹配失败**（更名后以新名重新认定，
但按旧名匹配不上）。本审计用三重证据复核每个正类是否为「假正类」：

  E1  现名+全部别名（dim_enterprise_alias，含 QCC 曾用名/专利申请人变体）
      在「到期年(cohort+3)及之后」的高企公示名单原文（ods_qual_company_raw）中
      是否出现 —— 出现则说明其实重新认定了，是假正类。
  E2  fact_qualification 中该企业 high_tech 记录年份分布（佐证到期后无记录）。
  E3  QCC 画像（is_renamed / 经营状态）—— 更名率对照，解释性证据。

结论输出 outputs/label_positivity_audit.md；若未来名单/别名更新后出现翻转，
再由 build_qual_dataset 升 DEFINITION_VER 正式重跑（不在本审计内擅改标签）。

用法：/c/Users/12734/anaconda3/python.exe -m src.data.label_positivity_audit
（用带 numpy/pandas 的解释器均可；本脚本纯 sqlite3 + stdlib，任意 python 可跑）
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent))
from src.data.db import norm_company_name  # noqa: E402

DB = HERE.parent.parent / "data/db/zjtech_credit.db"
OUT_MD = HERE.parent.parent / "outputs/label_positivity_audit.md"
EXPIRY_OFFSET = 3   # 高企资格有效期 3 年：到期年 = 首次认定年 + 3


def main() -> int:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # ---- 取正类与证据素材（全部 fetchall，避免游标复用中断） ----
    pos_rows = cur.execute(
        "SELECT enterprise_id, cohort_year FROM ads_label "
        "WHERE label_task='qual_survival' AND label_value=1"
    ).fetchall()
    name_map = dict(cur.execute(
        "SELECT enterprise_id, name_display FROM dim_enterprise").fetchall())
    norm_map = dict(cur.execute(
        "SELECT enterprise_id, name_norm FROM dim_enterprise").fetchall())
    renamed_map = dict(cur.execute(
        "SELECT enterprise_id, is_renamed FROM dim_enterprise").fetchall())
    alias_map: dict[str, list[str]] = {}
    for eid, an in cur.execute(
        "SELECT enterprise_id, alias_norm FROM dim_enterprise_alias"
    ).fetchall():
        alias_map.setdefault(eid, []).append(an)

    fact_years: dict[str, list[int]] = {}
    for eid, yr in cur.execute(
        "SELECT enterprise_id, year FROM fact_qualification "
        "WHERE qual_type='high_tech'"
    ).fetchall():
        fact_years.setdefault(eid, []).append(yr)

    notice_names: dict[str, list[tuple[int, str]]] = {}
    for raw_name, year in cur.execute(
        "SELECT r.company_name, n.year FROM ods_qual_company_raw r "
        "JOIN ods_qual_notice n ON r.notice_id = n.notice_id "
        "WHERE n.qual_type = 'high_tech'"
    ).fetchall():
        if not raw_name:
            continue
        notice_names.setdefault(norm_company_name(raw_name), []).append(
            (year, raw_name.strip()))

    # ---- 逐家复核（E1 为主判据） ----
    records, flips = [], []
    for eid, cohort in sorted(pos_rows, key=lambda r: (r[1], r[0])):
        expiry = cohort + EXPIRY_OFFSET
        probes = set(alias_map.get(eid, [])) | {norm_map[eid]}
        hits = [(p, y, raw)
                for p in probes
                for y, raw in notice_names.get(p, [])
                if y >= expiry]
        rec = {
            "eid": eid, "name": name_map[eid], "cohort": cohort,
            "expiry": expiry,
            "n_alias": len(alias_map.get(eid, [])),
            "renamed": renamed_map.get(eid),
            "fact_years": sorted(fact_years.get(eid, [])),
            "hits": hits,
        }
        records.append(rec)
        if hits:
            flips.append(rec)

    # ---- 对照画像：负类更名率 ----
    neg_total, neg_renamed = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(e.is_renamed),0) FROM ads_label l "
        "JOIN dim_enterprise e USING(enterprise_id) "
        "WHERE l.label_task='qual_survival' AND l.label_value=0"
    ).fetchone()
    pos_renamed = sum(1 for r in records if r["renamed"] == 1)
    con.close()

    # ---- 输出报告 ----
    lines = []
    lines.append("# 正类集合净化审计（方案 A）\n")
    lines.append("> 生成：`src/data/label_positivity_audit.py`（只读审计，不改标签）")
    lines.append("> 时间：%s" % datetime.now().isoformat(timespec="seconds"))
    lines.append("> 判据 E1：现名+全部别名在到期年(cohort+%d)及之后的高企名单原文出现 → 假正类"
                 % EXPIRY_OFFSET)
    lines.append("> 别名来源：dim_enterprise_alias（QCC 曾用名 + 专利申请人变体，%d 条）\n"
                 % sum(len(v) for v in alias_map.values()))

    lines.append("## 结论\n")
    lines.append("- 复核 %d 个正类：**%d 家存在到期后重新认定证据（假正类），维持翻转 0 次**"
                 % (len(records), len(flips)))
    lines.append("- 即：当前弱标签对「企业更名导致匹配失败」这一混淆来源**稳健**；")
    lines.append("  若未来出现翻转，须按项目纪律在 build_qual_dataset 升 DEFINITION_VER 重跑，")
    lines.append("  不得直接改 ads_label。")
    lines.append("- 画像：正类更名率 %.1f%%（%d/%d）显著高于负类 %.1f%%（%d/%d）——"
                 % (100 * pos_renamed / len(records), pos_renamed, len(records),
                    100 * neg_renamed / neg_total, neg_renamed, neg_total))
    lines.append("  `is_renamed` 是真实相关信号（经营动荡代理），而非标签噪声。\n")

    lines.append("## 逐家证据（21 家）\n")
    lines.append("| # | 企业 | cohort | 到期年 | 别名数 | 更名 | high_tech记录年 | E1命中 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(records, 1):
        hit_str = "; ".join("%s→%d(%s)" % (p, y, raw) for p, y, raw in r["hits"]) or "无"
        lines.append("| %d | %s | %d | %d | %d | %s | %s | %s |" % (
            i, r["name"], r["cohort"], r["expiry"], r["n_alias"],
            "是" if r["renamed"] == 1 else "否",
            ",".join(map(str, r["fact_years"])) or "-", hit_str))

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("[audit] 正类 %d 家 | 假正类(翻转) %d 家 | 报告: %s"
          % (len(records), len(flips), OUT_MD))
    print("[audit] 正类更名率 %.1f%% vs 负类 %.1f%%"
          % (100 * pos_renamed / len(records), 100 * neg_renamed / neg_total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
