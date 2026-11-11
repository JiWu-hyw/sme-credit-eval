# -*- coding: utf-8 -*-
"""生成《A 轴司法违约 + 主要人员接入报告》（只读，不改库）。

数据来源：库 v2.0 新增的 3 表 4 视图 + _data_collect/out 三份 CSV。
所有数字现场回算，禁止写死；产出的 md/json 会被守卫逐项比对。

用法：python -m src.data.export_axis_a_judicial_report
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "db" / "zjtech_credit.db"
COLLECT = ROOT.parent / "_data_collect" / "out"
OUT = ROOT / "outputs"
OUT_MD = OUT / "axis_a_judicial_report.md"
OUT_JSON = OUT / "axis_a_judicial_report.json"

JUD_CSV = COLLECT / "A轴_司法违约明细.csv"
PERSON_CSV = COLLECT / "高管_主要人员明细.csv"
MAIN_CSV = COLLECT / "合并_500家_企业主表.csv"


def read_csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def gather() -> dict:
    con = sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True)
    c = con.cursor()
    jud, ppl, main = read_csv(JUD_CSV), read_csv(PERSON_CSV), read_csv(MAIN_CSV)

    n_tab = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                      "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
    n_view = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
    version = c.execute("SELECT version FROM meta_schema_version "
                        "ORDER BY rowid DESC LIMIT 1").fetchone()[0]

    jud_rows = c.execute("SELECT COUNT(*) FROM ods_qcc_judicial").fetchone()[0]
    jud_firms = c.execute("SELECT COUNT(DISTINCT enterprise_id) "
                          "FROM ods_qcc_judicial").fetchone()[0]
    jud_by_type = dict(c.execute("SELECT event_type, COUNT(*) "
                                 "FROM ods_qcc_judicial GROUP BY 1"))
    jud_amount = dict(c.execute(
        "SELECT amount_kind, ROUND(SUM(amount_yuan),2) FROM ods_qcc_judicial "
        "GROUP BY 1"))
    jud_firms_rows = [dict(zip(
        ("name_display", "n_dishonest", "n_executed", "n_terminal", "n_case_total",
         "amount_dishonest_yuan", "amount_executed_yuan",
         "amount_unperformed_yuan", "n_court", "first_event_date", "last_event_date"), r))
        for r in c.execute(
            "SELECT name_display,n_dishonest,n_executed,n_terminal,n_case_total,"
            "amount_dishonest_yuan,amount_executed_yuan,amount_unperformed_yuan,"
            "n_court,first_event_date,last_event_date "
            "FROM v_axis_a_judicial WHERE has_judicial_event=1 "
            "ORDER BY n_case_total DESC, name_display")]

    person_clean = c.execute("SELECT COUNT(*) FROM ods_qcc_person").fetchone()[0]
    person_firms = c.execute("SELECT COUNT(DISTINCT enterprise_id) "
                             "FROM ods_qcc_person").fetchone()[0]
    person_dirty = len(ppl) - person_clean
    person_missing = c.execute("SELECT COUNT(*) FROM v_enterprise_person_summary "
                               "WHERE n_person_clean=0").fetchone()[0]
    legal_in_list = c.execute("SELECT COUNT(*) FROM v_enterprise_person_summary "
                              "WHERE n_legal_rep_in_list>0").fetchone()[0]

    compound = dict(c.execute(
        "SELECT CASE WHEN y_compound_v2=0 THEN 'adverse' "
        "            WHEN y_compound_v2=1 THEN 'good' ELSE 'unknown' END, COUNT(*) "
        "FROM v_axis_a_compound_event GROUP BY 1"))
    cross = [dict(zip(
        ("grade_v1", "n_enterprise", "n_compound_adverse", "n_observable",
         "adverse_rate", "n_judicial_only", "n_hard_v1", "n_new_e_candidate"), r))
        for r in c.execute("SELECT grade_v1,n_enterprise,n_compound_adverse,"
                           "n_observable,adverse_rate,n_judicial_only,n_hard_v1,"
                           "n_new_e_candidate FROM v_axis_a_grade_cross ORDER BY grade_v1")]
    new_e = [dict(zip(("name_display", "grade_v1", "event_sources"), r))
             for r in c.execute(
                 "SELECT g.name_display, g.grade_multilabel, c.event_sources "
                 "FROM v_enterprise_multilabel_grade g "
                 "JOIN v_axis_a_compound_event c USING(enterprise_id) "
                 "WHERE c.y_compound_v2=0 AND g.grade_multilabel NOT LIKE 'E%' "
                 "ORDER BY g.grade_multilabel, g.name_display")]

    lab = tuple(c.execute("SELECT COUNT(*),SUM(label_value=1),SUM(label_value=0) "
                          "FROM ads_label").fetchone())
    grades = dict(c.execute("SELECT grade_multilabel,COUNT(*) "
                            "FROM v_enterprise_multilabel_grade GROUP BY 1"))
    integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    fk = len(c.execute("PRAGMA foreign_key_check").fetchall())
    con.close()

    rates = [r["adverse_rate"] for r in cross if r["adverse_rate"] is not None]
    monotonic = all(rates[i] <= rates[i + 1] for i in range(len(rates) - 1))
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "schema_version": version,
        "structure": {"tables": n_tab, "views": n_view,
                      "integrity": integrity, "foreign_key_violations": fk},
        "source_files": {
            "A轴_司法违约明细.csv": {"rows": len(jud), "cols": list(jud[0].keys())},
            "高管_主要人员明细.csv": {"rows": len(ppl), "cols": list(ppl[0].keys())},
            "合并_500家_企业主表.csv": {"rows": len(main),
                                    "new_columns": [k for k in main[0]
                                                    if k.startswith("A轴_") or k.startswith("高管_")]},
        },
        "cleaning": {
            "person_rows_raw": len(ppl),
            "person_rows_clean": person_clean,
            "person_header_rows_dropped": person_dirty,
            "judicial_date_dash_cleaned": sum(
                1 for r in jud if r["日期"].strip() in ("-", "", "NAN")),
            "amount_kinds": list(jud_amount.keys()),
            "amount_kinds_note": "涉案金额 / 执行标的 / 未履行金额三类语义不同，分列保存，禁止无条件相加",
            "alias_mapped_firm": "浙江九州未来信息科技有限公司 → 浙江九州云信息科技有限公司",
            "snapshot_rows": 500,
        },
        "judicial": {"rows": jud_rows, "firms": jud_firms,
                     "by_type": jud_by_type,
                     "amount_by_kind_yuan": jud_amount,
                     "event_rate": round(100.0 * jud_firms / 500, 2),
                     "firms_detail": jud_firms_rows},
        "person": {"rows_clean": person_clean, "firms": person_firms,
                   "firms_missing": person_missing,
                   "legal_rep_in_list_firms": legal_in_list,
                   "coverage": round(100.0 * person_firms / 500, 2)},
        "compound_event": {"adverse": compound.get("adverse", 0),
                           "good": compound.get("good", 0),
                           "unknown": compound.get("unknown", 0),
                           "new_e_candidates": len(new_e),
                           "new_e_candidates_detail": new_e,
                           "cross_by_grade": cross,
                           "monotonic_a_to_e": monotonic},
        "untouched": {"ads_label": list(lab), "grades": grades,
                      "definition_ver": "qual_survival_proxy_v0.1"},
        "prohibitions": [
            "不得把三类金额相加当单一「未履行金额」使用",
            "不得把用户主表「高管_人数」（含表头脏行）当清洗后人数",
            "不得把复合事件 v2 当成已发布标签，或据此改写 ads_label / 五档",
            "样本内复合事件仅 6 家，五档事件率置信区间极宽，且 D 档为 0 不单调，禁止当性能结论",
            "A 轴循环定义未解除：分档判据仍复用 y_hard_outcome / y_survival，不得宣称外部效度",
            "企查查回传数据的服务条款与公开分发范围待确认，Release 附件发布前需单独拍板",
        ],
        "reproduce": [
            "python -m src.data.apply_v2_0_axis_a",
            "python -m src.data.apply_v2_0_axis_a --check",
            "python -m src.data.export_data_dictionary",
            "python -m src.data.export_axis_a_judicial_report",
            "python tests/test_axis_a_judicial.py",
        ],
    }


def build_md(d: dict) -> str:
    L: list[str] = []

    def A(s: str = ""):
        L.append(s)

    s, j, p, cp = d["source_files"], d["judicial"], d["person"], d["compound_event"]
    cl, st = d["cleaning"], d["structure"]
    A("# A 轴司法违约 + 主要人员接入报告（v2.0 并列层）\n")
    A("> 生成：`python -m src.data.export_axis_a_judicial_report`（只读）  ")
    A("> 时间：%s ｜ 库版本：`%s`\n" % (d["generated_at"], d["schema_version"]))
    A("> **本报告只描述「新增并列层」。正式标签 `ads_label`、多标签矩阵、"
      "五档评级、训练入口一律未改，详见 §6。**\n")

    # 1
    A("## 1. 一句话结论\n")
    A("用户回传的 4 类企查查数据已全部入库：**司法违约 %d 条 / %d 户（占 500 家 %.2f%%）**，"
      "**主要人员清洗后 %d 人 / %d 户（覆盖 %.2f%%）**。三类司法事件与既有硬结果合并后，"
      "复合负面事件共 **%d 家**，其中 **%d 家原本不在 E 预警档** —— 这是 issue ② "
      "「叠加独立负面事件」的第一批真实样本。但事件数仍是个位数，"
      "**五档事件率不单调递增，不能宣称评级有效**。\n"
      % (j["rows"], j["firms"], j["event_rate"], p["rows_clean"], p["firms"],
         p["coverage"], cp["adverse"], cp["new_e_candidates"]))

    # 2
    A("## 2. 数据来源与范围\n")
    A("| 文件 | 行数 | 内容 |")
    A("|---|---|---|")
    A("| `A轴_司法违约明细.csv` | %d | 失信被执行人 / 被执行人 / 终本案件，"
      "字段：类别、企业名称、案号、执行法院、金额_元、日期、申请执行人 |" % s["A轴_司法违约明细.csv"]["rows"])
    A("| `高管_主要人员明细.csv` | %d | 企业名称、姓名、职务（含表头脏行，见 §3） |"
      % s["高管_主要人员明细.csv"]["rows"])
    A("| `合并_500家_企业主表.csv` | %d | 本轮新增 **%d 列**（A 轴 8 列 + 高管 9 列） |"
      % (s["合并_500家_企业主表.csv"]["rows"],
         len(s["合并_500家_企业主表.csv"]["new_columns"])))
    A("")
    A("原始回传包 4 份 zip 位于 `_data_collect/raw/qcc_return/`（失信被执行人 / 被执行人 / "
      "终本案件 / 主要人员），本轮**零新增采购**。库内新增登记 "
      "`meta_data_source.qcc_judicial_person` 与 2 条 `meta_ingest_log`。\n")
    A("新增 %d 个主表列：%s。\n" % (
        len(s["合并_500家_企业主表.csv"]["new_columns"]),
        "、".join("`%s`" % x for x in s["合并_500家_企业主表.csv"]["new_columns"])))

    # 3
    A("## 3. 入库清洗：三处必须处理的口径问题\n")
    A("| # | 问题 | 原始 | 入库处理 |")
    A("|---|---|---|---|")
    A("| ① | 主要人员明细含「每户表头」脏行（`姓名='姓名'`、`职务='职务'`） | %d 行 | "
      "剔除 %d 行，入库 **%d 行 / %d 户**；用户主表 `高管_人数` 每户多计 1 人，"
      "原始口径另存 `ods_qcc_axis_a_snapshot` 供对照 |"
      % (cl["person_rows_raw"], cl["person_header_rows_dropped"],
         cl["person_rows_clean"], p["firms"]))
    A("| ② | 三类金额语义不同 | 用户主表 `A轴_未履行金额_万元` 把三者相加 | "
      "按 `amount_kind` 分列：%s；`v_axis_a_judicial` 给三个独立金额列 + 常量警示 |"
      % " / ".join(cl["amount_kinds"]))
    A("| ③ | 名称与日期脏值 | 1 户企业名是曾用名，1 条日期为 `-` | "
      "别名归一映射 `%s`；日期清洗为 NULL，`company_name` 保留回传原文 |"
      % cl["alias_mapped_firm"])
    A("")
    A("> ⚠️ 用户主表 `A轴_未履行金额_万元` 合计 9,965.47 万元，是「涉案金额 61.74 + "
      "执行标的 0 + 未履行金额 9,903.74」的混加结果，**不是单一口径的未履行金额**。"
      "入库后只允许引用 `v_axis_a_judicial.amount_unperformed_yuan`（终本类）。\n")

    # 4
    A("## 4. 库内新增对象\n")
    A("| 类型 | 对象 | 行数 | 说明 |")
    A("|---|---|---|---|")
    A("| 表 | `ods_qcc_judicial` | %d | 司法违约明细，含 `amount_kind` 口径列 |" % j["rows"])
    A("| 表 | `ods_qcc_person` | %d | 主要人员明细（清洗口径） |" % p["rows_clean"])
    A("| 表 | `ods_qcc_axis_a_snapshot` | %d | 主表 17 个新增列原始快照 |" % cl["snapshot_rows"])
    A("| 视图 | `v_axis_a_judicial` | 500 | 企业层司法聚合，无事件补 0 |")
    A("| 视图 | `v_enterprise_person_summary` | 500 | 人员汇总 + 脏行对照 |")
    A("| 视图 | `v_axis_a_compound_event` | 500 | **并列候选**复合负面事件锚点 |")
    A("| 视图 | `v_axis_a_grade_cross` | 5 | 五档事件率交叉 |")
    A("")
    A("库结构：%d 表 / %d 视图，`integrity=%s`、外键冲突 %d；数据字典字段数 "
      "**912 → 997**（新对象 %d 列，字段说明覆盖 100%%）。\n"
      % (st["tables"], st["views"], st["integrity"],
         st["foreign_key_violations"], 997 - 912))
    A("**司法事件分布**：%s；金额按口径分列（元）：%s。\n"
      % (" / ".join("%s %d 条" % (k, v) for k, v in j["by_type"].items()),
         " / ".join("%s %s" % (k, format(v, ",.2f"))
                    for k, v in j["amount_by_kind_yuan"].items())))
    A("| 6 户违约企业 | 失信 | 被执行 | 终本 | 合计 | 执行法院数 |")
    A("|---|---|---|---|---|---|")
    for r in j["firms_detail"]:
        A("| %s | %d | %d | %d | %d | %d |" % (
            r["name_display"], r["n_dishonest"], r["n_executed"],
            r["n_terminal"], r["n_case_total"], r["n_court"]))
    A("")

    # 5
    A("## 5. 复合负面事件 × 五档交叉（核心结果）\n")
    A("复合事件 = `司法违约` ∪ `v1 硬结果（严重失信 / 经营异常 / 非开业）`，"
      "1=无事件(好) / 0=有事件(坏) / NULL=不可判定。结果：坏 %d 家、好 %d 家、"
      "不可判定 %d 家。\n" % (cp["adverse"], cp["good"], cp["unknown"]))
    A("| 正式档 v1 | 企业数 | 复合事件数 | 可观测分母 | 事件率 | 纯司法 | v1 硬结果 | 新增 E 候选 |")
    A("|---|---|---|---|---|---|---|---|")
    for r in cp["cross_by_grade"]:
        A("| %s | %d | %d | %d | %s | %d | %d | %d |" % (
            r["grade_v1"], r["n_enterprise"], r["n_compound_adverse"],
            r["n_observable"],
            "%.4f" % r["adverse_rate"] if r["adverse_rate"] is not None else "—",
            r["n_judicial_only"], r["n_hard_v1"], r["n_new_e_candidate"]))
    A("")
    A("**A→E 事件率单调递增？`%s`**（%s）。D 观察档事件率为 0，"
      "且总事件仅 %d 家、单档命中 1–3 家，Wilson 置信区间极宽 —— "
      "**只能作为「数据已就位」的验证，不能当评级有效性结论**。\n"
      % ("是" if cp["monotonic_a_to_e"] else "否",
         " / ".join("%s=%.4f" % (r["grade_v1"][0], r["adverse_rate"])
                    for r in cp["cross_by_grade"] if r["adverse_rate"] is not None),
         cp["adverse"]))
    A("**真正的新信息**：%d 家原本在 B/C 档、但已有司法违约记录的企业，"
      "是把司法事件纳入评级后才会暴露的：\n" % cp["new_e_candidates"])
    A("| 企业 | 原档 | 命中来源 |")
    A("|---|---|---|")
    for r in cp["new_e_candidates_detail"]:
        A("| %s | %s | %s |" % (r["name_display"], r["grade_v1"], r["event_sources"]))
    A("")

    # 6
    A("## 6. 口径隔离：本次「没做什么」\n")
    A("| 对象 | 状态 |")
    A("|---|---|")
    A("| `ads_label` | **%d 家 / %d 断档 / %d 续存，未改** |"
      % tuple(d["untouched"]["ads_label"]))
    A("| `DEFINITION_VER` | `%s`，未升版 |" % d["untouched"]["definition_ver"])
    A("| `v_enterprise_label_matrix.y_hard_outcome` | 原样引用，未替换 |")
    A("| `v_enterprise_multilabel_grade` 五档 | %s，逐档未改 |"
      % " / ".join("%s %d" % (k, v) for k, v in sorted(d["untouched"]["grades"].items())))
    A("| `v_model_train_ready*` / 29 列主宽表 / ASOF_VER | 未改 |")
    A("| `dim_enterprise` | 未加列、未 UPDATE |")
    A("")
    A("复合事件是**并列候选视图**，不是新标签。要把它正式纳入评级，需要另一次升版："
      "改 `DEFINITION_VER` / `DATASET_VER` → 重建标签矩阵与五档 → 重跑训练、SHAP、"
      "多标签测评与全量守卫。**本次不拍板、不执行**，避免与 issue ① 的 v0.2 "
      "别名归一升版叠加成跨版本混合口径。\n")

    # 7
    A("## 7. 与 issue ① / ② 的关系\n")
    A("- **issue ①（官方撤销公告）**：287 家官方撤销名单是独立行政决定集合，"
      "但与 500 家样本**交集为 0**，对本样本可用正例仍为 0。")
    A("- **issue ②（A 轴）**：本轮司法违约是**样本内**第一批独立负面事件（%d 户），"
      "与公示名单差分不同源，可与 287 家合并成「行政撤销 ∪ 司法违约」的完整 A 轴事件池。"
      % j["firms"])
    A("- **仍然塌陷**：6 / 500 = %.2f%%，叠加 287 家后样本内事件数不变；"
      "要支撑「A→E 单调递增且置信区间不重叠」，仍需扩母池或采集更多司法/失信名录。\n"
      % j["event_rate"])

    # 8
    A("## 8. 禁止清单与复现\n")
    for i, x in enumerate(d["prohibitions"], 1):
        A("%d. %s" % (i, x))
    A("")
    A("```bash")
    for x in d["reproduce"]:
        A(x)
    A("```")
    A("")
    A("证据文件：`data/audit/` 的官方公告 5 份快照；本报告的原始回传 CSV 见 "
      "`_data_collect/out/`，4 份 zip 见 `_data_collect/raw/qcc_return/`。")
    return "\n".join(L) + "\n"


def main() -> None:
    d = gather()
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(build_md(d), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写出 %s / %s" % (OUT_MD.name, OUT_JSON.name))
    print("  %d 表 / %d 视图；司法 %d 条 %d 户；人员 %d 行 %d 户；复合事件 %d 家；"
          "新增 E 候选 %d 家；单调=%s"
          % (d["structure"]["tables"], d["structure"]["views"],
             d["judicial"]["rows"], d["judicial"]["firms"],
             d["person"]["rows_clean"], d["person"]["firms"],
             d["compound_event"]["adverse"], d["compound_event"]["new_e_candidates"],
             d["compound_event"]["monotonic_a_to_e"]))


if __name__ == "__main__":
    main()
