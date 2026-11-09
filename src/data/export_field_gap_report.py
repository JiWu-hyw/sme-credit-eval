"""把 configs/field_gap_registry.json 的定稿结论导出成算法端可读的说明。

问题：17 条「设计性 NULL / 源未接入」逐字段定稿（reason / evidence / remedy /
impact / fallback）早已写在 JSON 里，但算法端交付目录里看不到；
评审报告 §6 的 T22「成长轨迹」因此一直被判「8 列全空、未定稿」。
另外 graph_gaps 的图侧定稿也在同一个 JSON 里，一并导出。

所有「实测覆盖率」只读现算自库，不复用登记册里的历史数字。
产物：
    outputs/field_gap_decisions.md
    outputs/field_gap_decisions.json
交付时由 src/data/export_delivery.py 复制进 01_算法端/。
"""
from __future__ import annotations

import collections
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
REG = os.path.join(ROOT, "configs", "field_gap_registry.json")
OUT_MD = os.path.join(ROOT, "outputs", "field_gap_decisions.md")
OUT_JSON = os.path.join(ROOT, "outputs", "field_gap_decisions.json")

WIDE_EMPTY = (
    "patent_citation", "patent_triple_score", "rev_cagr", "emp_cagr",
    "funding_to_last_val", "m_team", "m_cite", "cap_tech_synergy",
)


def con_ro():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def build() -> dict:
    reg = json.load(open(REG, encoding="utf-8"))
    c = con_ro()
    try:
        items = []
        for it in reg["items"]:
            t, col = it["table"], it["field"]
            cols = {r[1] for r in c.execute('PRAGMA table_info("%s")' % t)}
            if col not in cols:
                cov = None
                note = "列不存在（登记册需修正）"
            else:
                n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
                nn = c.execute('SELECT COUNT("%s") FROM "%s"' % (col, t)).fetchone()[0]
                cov = {"non_null": nn, "total": n,
                       "pct": round(100.0 * nn / n, 2) if n else None}
                note = ""
            items.append(dict(it, measured_coverage=cov, measurement_note=note))

        wide = {}
        t = "ads_model_dataset"
        cols = {r[1] for r in c.execute('PRAGMA table_info("%s")' % t)}
        n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        for col in WIDE_EMPTY:
            if col in cols:
                nn = c.execute('SELECT COUNT("%s") FROM "%s"' % (col, t)).fetchone()[0]
                wide[col] = {"non_null": nn, "total": n}
            else:
                wide[col] = {"non_null": None, "total": n}
    finally:
        c.close()

    return {
        "generated_by": "src/data/export_field_gap_report.py",
        "registry_version": reg["meta"]["version"],
        "registry_updated_at": reg["meta"]["updated_at"],
        "policy": reg["meta"]["policy"],
        "status_enum": reg["meta"]["status_enum"],
        "n_items": len(items),
        "by_status": dict(collections.Counter(i["status"] for i in items)),
        "by_priority": dict(collections.Counter(i["priority"] for i in items)),
        "items": items,
        "substitutes": reg["substitutes"],
        "graph_gaps": reg.get("graph_gaps", []),
        "wide_table_empty_columns": wide,
    }


def write_md(R: dict) -> str:
    L = []
    L.append("# 永久缺口与替代特征清单（字段级定稿）\n")
    L.append("> 生成：`src/data/export_field_gap_report.py`，实测覆盖率只读现算自 "
             "`data/db/zjtech_credit.db`｜登记册 `%s`（%s）\n" % (
                 R["registry_version"], R["registry_updated_at"]))
    L.append("> 权威源：`configs/field_gap_registry.json`。本文件是它的**只读视图**，"
             "要改口径必须「先改登记册、再改库」，守卫 `tests/test_field_gap_registry.py` 会拦截。\n")

    L.append("\n<!-- DIRECTION_NOTICE:v1 -->\n"
             "> **方向声明（v1.9）**：正例 = 好企业 = 资质存续（286 家）；"
             "负例 = 坏企业 = 资质断档（21 家）。本文只谈特征缺口，不涉及标签方向；"
             "历史字段名 `n_positive` / `positive_rate` 仍指风险方向。\n"
             "<!-- /DIRECTION_NOTICE:v1 -->\n")

    L.append("\n## 0. 一句话结论\n")
    L.append("**「空着」是决策，不是遗漏**：%d 个字段逐条定稿，%s。"
             "按评审报告 §6 的 T22，主宽表 8 个全空列**已在登记册定稿**，"
             "本文件把定稿依据、替代特征与实测覆盖率交给算法端，不需要再等采集才能开工；"
             "真正要采集的是独立 A 轴结果标签（外部效度），不是这 8 列。\n" % (
                 R["n_items"], "、".join("%s %d" % (k, v) for k, v in
                                      sorted(R["by_status"].items()))))

    L.append("\n## 1. 定稿原则\n")
    L.append("> %s\n" % R["policy"])
    L.append("| 状态 | 含义 |\n|---|---|")
    for k, v in R["status_enum"].items():
        L.append("| `%s` | %s |" % (k, v))
    L.append("\n三条硬规则：**不估算回填**、**不冒用指标名**、**不用均值/中位数填充全空列**"
             "（填充后是常量列，会在特征重要性里留下幽灵特征）。\n")

    L.append("\n## 2. 主宽表 8 个全空列（现场核对）\n")
    L.append("| 列名 | 非空 / 总行 | 结论 |\n|---|---|---|")
    for col, v in R["wide_table_empty_columns"].items():
        if v["non_null"] is None:
            L.append("| `%s` | 列不存在 | 需修登记册 |" % col)
        else:
            L.append("| `%s` | %d / %d | %s |" % (
                col, v["non_null"], v["total"],
                "全空，按定稿剔除" if v["non_null"] == 0 else "已回填，需复核登记册"))
    L.append("\n这 8 列对应评审 §6 的 T22「成长轨迹」，但**不是同一批 8 个字段**："
             "登记册里还有 `n_cite_fwd` / `n_claims` / `family_size` / `abstract` 等"
             "专利质量字段也处于设计性 NULL。逐条依据见 §3。\n")

    L.append("\n## 3. 逐字段定稿（按指标分组）\n")
    groups: dict[str, list] = collections.defaultdict(list)
    for it in R["items"]:
        groups[it["indicator"]].append(it)
    for ind in sorted(groups):
        L.append("\n### %s\n" % ind)
        for it in groups[ind]:
            cov = it["measured_coverage"]
            cov_txt = ("%d / %d（%.2f%%）" % (
                cov["non_null"], cov["total"], cov["pct"])) if cov else "列缺失"
            L.append("#### `%s.%s` — %s\n" % (it["table"], it["field"], it["cn"]))
            L.append("| 项 | 内容 |\n|---|---|")
            L.append("| 状态 / 优先级 | `%s` / `%s` |" % (it["status"], it["priority"]))
            L.append("| 决策 | %s（%s，%s） |" % (
                it["decision"], it["decided_at"], it["decided_by"]))
            L.append("| 实测覆盖 | %s |" % cov_txt)
            L.append("| 为什么留空 | %s |" % it["reason"].replace("\n", " "))
            L.append("| 影响 | %s |" % it["impact"].replace("\n", " "))
            L.append("| 补救 | %s（%s） |" % (
                it["remedy"].replace("\n", " "), it.get("remedy_cost", "—")))
            L.append("| 替代特征 | %s |" % it.get("fallback", "—").replace("\n", " "))
            ev = it.get("evidence", [])
            if ev:
                L.append("\n依据：\n")
                for e in ev:
                    L.append("- %s" % e)
            L.append("")

    L.append("\n## 4. 替代特征清单（已在库里、有值）\n")
    L.append("替身必须真的落库且有值，否则只是 PPT 上的一句话。"
             "守卫 `tests/test_field_gap_registry.py` 会按覆盖率下限现场抽查。\n")
    L.append("| 缺什么 | 用什么替 | 落库位置 | 替换后覆盖 |\n|---|---|---|---|")
    for s in R["substitutes"]:
        L.append("| %s | %s | `%s` | %s |" % (
            s["missing"], s["substitute"], s["field"], s["after_cov"]))
        L.append("")
        L.append("> 为什么成立：%s\n" % s["why"].replace("\n", " "))

    if R["graph_gaps"]:
        L.append("\n## 5. 图侧缺口定稿（评审 §5.2）\n")
        L.append("| 编号 | 缺口 | 状态 | 决策 |\n|---|---|---|---|")
        for g in R["graph_gaps"]:
            L.append("| %s | %s | `%s` | %s |" % (
                g["id"], g["gap"], g["status"], g["decision"]))
        L.append("\n完整契约见 `graph_temporal_contract.md`（年度切片 / STGNN 输入 / 守恒校验）。\n")

    L.append("\n## 6. 使用规则\n")
    L.append("1. 建模前按本清单**整列剔除**设计性 NULL 字段；不要用 `SimpleImputer`。\n")
    L.append("2. 引用数值时注明「v1.9 / 登记册 %s」；登记册与本文件不一致时以登记册为准，"
             "并回报维护人。\n" % R["registry_version"])
    L.append("3. 按评审 §7.1(3)，负例 21 家、置换噪声地板约 0.5，"
             "**AUC / 准确率绝对值不得写成性能结论**；补特征不等于补标签。\n")
    L.append("4. 本文件是只读导出物，由 `export_field_gap_report.py` 重生成；"
             "最终打包时由 `export_delivery.py` 复制进 `01_算法端/`。\n")
    return "\n".join(L) + "\n"


def main() -> int:
    R = build()
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)
    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write(write_md(R))
    bad = [k for k, v in R["wide_table_empty_columns"].items()
           if v["non_null"] not in (0, None)]
    print("[done] %s" % os.path.relpath(OUT_MD, ROOT))
    print("[done] %s" % os.path.relpath(OUT_JSON, ROOT))
    print("  %d 条定稿 / %d 条替代 / %d 条图缺口 / 主宽表 8 列全空=%s" % (
        R["n_items"], len(R["substitutes"]), len(R["graph_gaps"]), not bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
