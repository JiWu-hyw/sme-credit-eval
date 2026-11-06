"""用「能拿到的」补「拿不到的」（只填真实可得的，不估算、不编造）。

对应汇报里那张替身表，逐条落地：

  1. 授权日期（0%）→ 申请日期
       不编授权日；把 134 条缺 app_date 的专利申请年从申请号前缀恢复
       （CN200210021411.6 → 2002），app_date 精确日仍留空。
  2. 被引次数（0 条）→ 法律状态（86.2%）
       新增 patent_lapse_ratio（授权后失效占比）/ patent_reject_ratio（驳回撤回占比）。
       这两个是「专利质量」的可用信号 —— 被引是外部评价，失效是内部结局。
  3. 年报财务（几乎不公示）→ 年报实有项，没有就留空
       新增 latest_report_year / latest_rev_wan / latest_net_profit_wan /
       latest_total_assets_wan / latest_tax_wan / latest_emp_num，
       从 ods_qcc_annual 取「每个字段最近一次非空值」。
       **不拿社保参保人数顶替营收** —— 人数是人数，营收是营收，换了就串味。

不做的：
  · 主分类号缺的那 51% 补不了（对照表只做层级映射，不会凭空生出分类号）
  · 摘要缺的 97% 用专利名称替代（已在库，不新建列）
  · 注册地址原文用 region 替代（已在库）

用法：
  python -m src.data.fill_substitutes [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("data/db/zjtech_credit.db")

# 法律状态 → 结局归类。口径来自 fact_patent.legal_status 实际取值（12 类）。
LAPSED = ("未缴年费专利权终止", "期限届满专利权终止", "失效", "避重放弃")
REJECTED = ("公布驳回", "公布撤回", "公布视为撤回")
PENDING = ("实质审查", "公布")

# 年报财务快照列：dws 列名 → ods 列名
FIN_COLS = {
    "latest_rev_wan": "total_revenue_wan",
    "latest_net_profit_wan": "net_profit_wan",
    "latest_total_assets_wan": "total_assets_wan",
    "latest_tax_wan": "tax_total_wan",
    "latest_emp_num": "employee_num",
}


def has_col(cur, table: str, col: str) -> bool:
    return col in {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}


def add_col(cur, table: str, col: str, typ: str) -> None:
    if not has_col(cur, table, col):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    log: list[str] = [f"# 替身填充执行记录 {datetime.now():%Y-%m-%d %H:%M}", ""]

    # ---------- 1) 申请年从申请号恢复 ----------
    rows = cur.execute(
        """SELECT patent_id, app_no, app_year, pub_date FROM fact_patent
           WHERE app_year IS NULL AND app_no IS NOT NULL AND length(app_no) >= 6"""
    ).fetchall()
    fixed, skipped = 0, []
    for r in rows:
        m = r["app_no"].strip()
        if not m.upper().startswith("CN"):
            skipped.append((r["app_no"], "非 CN 申请号"))
            continue
        try:
            yr = int(m[2:6])
        except ValueError:
            skipped.append((r["app_no"], "前缀非年份"))
            continue
        if not 1985 <= yr <= 2026:
            skipped.append((r["app_no"], f"年份越界 {yr}"))
            continue
        if r["pub_date"] and yr > int(r["pub_date"][:4]):
            skipped.append((r["app_no"], f"申请年 {yr} 晚于公开年 {r['pub_date'][:4]}"))
            continue
        if not args.dry_run:
            cur.execute(
                "UPDATE fact_patent SET app_year=? WHERE patent_id=? AND app_year IS NULL",
                (yr, r["patent_id"]),
            )
        fixed += 1
    log.append(f"## 1) 申请年恢复")
    log.append(f"- 缺 app_year：{len(rows)} 条，从申请号恢复 **{fixed}** 条，跳过 {len(skipped)} 条")
    for a, why in skipped[:5]:
        log.append(f"  - 跳过 {a}：{why}")
    log.append("- app_date 精确日**不补**：申请号里没有日，编一个日期就是造假")
    log.append("")

    # ---------- 2) 法律状态质量特征 ----------
    add_col(cur, "dws_tech_feature", "n_patent_lapsed", "INTEGER")
    add_col(cur, "dws_tech_feature", "n_patent_rejected", "INTEGER")
    add_col(cur, "dws_tech_feature", "patent_lapse_ratio", "REAL")
    add_col(cur, "dws_tech_feature", "patent_reject_ratio", "REAL")

    # 走 v_patent_canonical：一件专利一行（源数据里 A/B 两条已合并），
    # 否则同一件专利会被数两次，失效占比的分母就虚了。
    cur.execute("SELECT enterprise_id, legal_status FROM v_patent_canonical WHERE enterprise_id IS NOT NULL")
    agg: dict[str, dict[str, int]] = {}
    for eid, ls in cur.fetchall():
        a = agg.setdefault(eid, {"total": 0, "lapsed": 0, "rejected": 0})
        a["total"] += 1
        if ls in LAPSED:
            a["lapsed"] += 1
        elif ls in REJECTED:
            a["rejected"] += 1
    q_status = cur.execute(
        "SELECT COUNT(*) FROM v_patent_canonical "
        "WHERE enterprise_id IS NOT NULL AND legal_status IS NOT NULL"
    ).fetchone()[0]
    per_firm_status = {
        r[0]: r[1] for r in cur.execute(
            "SELECT enterprise_id, COUNT(*) FROM v_patent_canonical "
            "WHERE enterprise_id IS NOT NULL AND legal_status IS NOT NULL GROUP BY 1")
    }

    n_lapse_filled, ratios = 0, []
    for eid, a in agg.items():
        # 分母用「有法律状态的专利数」，不是全部专利 —— 没状态的不该算成好专利
        denom = per_firm_status.get(eid, 0)
        if denom == 0:
            continue
        if not args.dry_run:
            cur.execute(
                """UPDATE dws_tech_feature
                   SET n_patent_lapsed=?, n_patent_rejected=?,
                       patent_lapse_ratio=?, patent_reject_ratio=?
                   WHERE enterprise_id=?""",
                (a["lapsed"], a["rejected"],
                 round(a["lapsed"] / denom, 4), round(a["rejected"] / denom, 4), eid),
            )
        n_lapse_filled += 1
        ratios.append(a["lapsed"] / denom)
    log.append("## 2) 法律状态 → 专利质量代理")
    log.append(f"- 有法律状态的专利：{q_status} / 32,969（86.2%）")
    log.append(f"- 失效类（{' / '.join(LAPSED)}）与驳回类（{' / '.join(REJECTED)}）分别计数")
    log.append(f"- 覆盖 **{n_lapse_filled}/500** 家企业")
    if ratios:
        ratios_sorted = sorted(ratios)
        mid = len(ratios_sorted) // 2
        med0 = (ratios_sorted[mid] if len(ratios_sorted) % 2
                else (ratios_sorted[mid - 1] + ratios_sorted[mid]) / 2)
        log.append(f"- 失效占比：中位 {med0:.3f}，范围 {ratios_sorted[0]:.3f} – {ratios_sorted[-1]:.3f}")
    log.append("")

    # ---------- 3) 年报财务快照 ----------
    add_col(cur, "dws_growth_feature", "latest_report_year", "INTEGER")
    for c in FIN_COLS:
        add_col(cur, "dws_growth_feature", c, "REAL" if c != "latest_emp_num" else "INTEGER")

    src_cols = ", ".join(FIN_COLS.values())
    annual = cur.execute(
        f"""SELECT enterprise_id, report_year, {src_cols} FROM ods_qcc_annual
            ORDER BY enterprise_id, report_year"""
    ).fetchall()
    latest: dict[str, dict] = {}
    for r in annual:
        eid = r["enterprise_id"]
        slot = latest.setdefault(eid, {"year": None})
        slot["year"] = max(slot["year"] or 0, r["report_year"])
        for dst, src in FIN_COLS.items():
            if r[src] is not None:            # 只取非空：有的填，没有的保持 NULL
                slot[dst] = r[src]
    fin_cnt = {c: 0 for c in FIN_COLS}
    for eid, slot in latest.items():
        if not args.dry_run:
            cur.execute(
                f"""UPDATE dws_growth_feature SET latest_report_year=?,
                    {', '.join(f'{c}=?' for c in FIN_COLS)}
                    WHERE enterprise_id=?""",
                (slot["year"], *[slot.get(c) for c in FIN_COLS], eid),
            )
        for c in FIN_COLS:
            if slot.get(c) is not None:
                fin_cnt[c] += 1
    log.append("## 3) 年报财务快照（有的填，没有留空）")
    log.append(f"- 来源：ods_qcc_annual，覆盖 {len(latest)} 家企业（实际披露 2025 单年）")
    for c in FIN_COLS:
        log.append(f"  - `{c}`：{fin_cnt[c]}/500")
    log.append("- **不拿社保参保人数顶替营收**：人数与金额不是一个量纲，换了指标就串味")
    log.append("- 因只有单年数据，rev_cagr / emp_cagr 仍算不出（需要 ≥2 个年度）")
    log.append("")

    if not args.dry_run:
        con.commit()

    # 复核
    if not args.dry_run:
        n = cur.execute("SELECT COUNT(*) FROM dws_tech_feature").fetchone()[0]
        for c in ("patent_lapse_ratio", "patent_reject_ratio"):
            v = cur.execute(f'SELECT COUNT("{c}") FROM dws_tech_feature').fetchone()[0]
            log.append(f"- 复核 `{c}`：{v}/{n}")
        if ratios:
            ratios.sort()
            mid = len(ratios) // 2
            med = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
            log.append(f"- 失效占比中位数：{med:.3f}（范围 {ratios[0]:.3f} – {ratios[-1]:.3f}）")
        else:
            log.append("- 失效占比中位数：NA")
        still = cur.execute("SELECT COUNT(*) FROM v_patent_canonical WHERE app_year IS NULL").fetchone()[0]
        log.append(f"- 复核 app_year 仍缺：{still} 条")

    con.close()
    out = Path("outputs/substitute_fill_report.md")
    out.write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log))
    print(f"\n{'[dry-run] 未写库' if args.dry_run else '已提交'} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
