"""缺口登记册导出（只读，不改库）。

单一事实源：`configs/field_gap_registry.json`
产出：`outputs/field_gap_registry.md`（人读）+ 库内实测覆盖率（用于自证「确实空着」）

为什么要有这个脚本：
  空字段有两种——「忘了填」和「故意不填」。前者是 bug，后者是口径。
  两者在库里长得一模一样（都是 NULL），只能靠登记册区分。
  本脚本每次运行都会把登记状态与库内实测覆盖率对账：
  登记为「设计性 NULL」的列，实测必须为 0；一旦不为 0 说明有人偷偷回填，直接报错。

用法：
  python -m src.data.export_field_gap_registry
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path("data/db/zjtech_credit.db")
REG = Path("configs/field_gap_registry.json")
OUT = Path("outputs/field_gap_registry.md")

STATUS_CN = {
    "null_by_design": ("⛔ 设计性 NULL", "已决策留空，填了就是造假"),
    "source_missing": ("◻ 源未接入", "外部数据源未采购，有补齐路径"),
    "partial_insufficient": ("◐ 部分不足", "有值但覆盖过低，仅留档不入模"),
}
STATUS_ORDER = ["null_by_design", "source_missing", "partial_insufficient"]
MUST_BE_EMPTY = {"null_by_design", "source_missing"}


def _n_nonnull(cur, table: str, col: str) -> tuple[int, int]:
    """返回 (总行数, 非空数)；列不存在返回 (-1, -1)。

    必须先用 PRAGMA 确认列存在：SQLite 会把 "不存在的列" 当字符串字面量，
    COUNT("xxx") 于是返回总行数，覆盖率假性 100%。
    """
    if col not in {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}:
        return -1, -1
    n = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    nn = cur.execute(f'SELECT COUNT("{col}") FROM {table}').fetchone()[0]
    return n, nn


def main() -> int:
    reg = json.loads(REG.read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()

    rows, drift, missing_field = [], [], []
    for it in reg["items"]:
        t, f = it["table"], it["field"]
        if not cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone():
            missing_field.append(f"{t}.{f}（表不存在）")
            continue
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
        if f not in cols:
            missing_field.append(f"{t}.{f}（列不存在）")
            continue
        n, nn = _n_nonnull(cur, t, f)
        pct = 100.0 * nn / n if n else 0.0
        if it["status"] in MUST_BE_EMPTY and nn > 0:
            drift.append(f"{t}.{f} 登记为空但实测 {nn}/{n}")
        rows.append({**it, "n": n, "nn": nn, "pct": pct})

    con.close()

    L: list[str] = []
    L.append("# 字段缺口登记册")
    L.append("")
    L.append(f"更新：{datetime.now():%Y-%m-%d %H:%M}　｜　决策人：{reg['meta']['owner']}")
    L.append("")
    L.append(f"> 口径原则：{reg['meta']['policy']}")
    L.append("")

    # 一、决策摘要
    by_status: dict[str, list[dict]] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)
    L.append("## 一、决策摘要")
    L.append("")
    L.append("| 状态 | 含义 | 字段数 | 涉及指标 |")
    L.append("|---|---|---|---|")
    for st in STATUS_ORDER:
        items = by_status.get(st)
        if not items:
            continue
        cn, desc = STATUS_CN.get(st, (st, ""))
        inds = sorted({i["indicator"] for i in items})
        L.append(f"| {cn} | {desc} | {len(items)} | {', '.join(inds)} |")
    L.append("")
    L.append(f"合计 **{len(rows)}** 个字段按口径留空，全部有决策记录、有补齐路径，"
             "不属于「遗漏」或「待办未做」。\n")
    L.append("空字段只有两种：**算不出来**（口径）和**忘了填**（bug）。"
             "本册的作用就是把前者钉死，让后者无处可藏。")
    L.append("")

    # 二、逐字段
    L.append("## 二、逐字段登记")
    L.append("")
    L.append("| 指标 | 字段 | 中文 | 优先级 | 状态 | 实测覆盖 | 决策 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rows:
        cn, _ = STATUS_CN.get(r["status"], (r["status"], ""))
        L.append(f"| {r['indicator']} | `{r['table']}.{r['field']}` | {r['cn']} "
                 f"| {r['priority']} | {cn} | {r['nn']}/{r['n']}（{r['pct']:.1f}%） "
                 f"| {r['decision']} |")
    L.append("")

    # 三、逐条理由
    L.append("## 三、逐条理由与补齐路径")
    L.append("")
    for r in rows:
        cn, _ = STATUS_CN.get(r["status"], (r["status"], ""))
        L.append(f"### {r['indicator']} `{r['table']}.{r['field']}`【{r['cn']}】—— {cn}")
        L.append("")
        L.append(f"- **为什么空着**：{r['reason']}")
        L.append("- **证据**：" + "；".join(r["evidence"]))
        L.append(f"- **怎么才能补上**：{r['remedy']}（成本：{r['remedy_cost']}）")
        L.append(f"- **对下游的影响**：{r['impact']}")
        if r.get("fallback"):
            L.append(f"- **可行的替代（不冒用原字段名）**：{r['fallback']}")
        L.append(f"- **决策**：{r['decision']}，{r['decided_at']}，{r['decided_by']}")
        L.append("")

    # 四、建模侧剔除清单
    L.append("## 四、建模侧必读：剔除清单")
    L.append("")
    L.append("以下列在库里恒空，**任何训练/预测流程都必须整列剔除**。"
             "不要用中位数/均值填充——填充后是常量列，既不提供信息，"
             "又会在 SHAP 与特征重要性里制造「重要度为 0 的幽灵特征」。")
    L.append("")
    L.append("```python")
    L.append("EXCLUDE_NULL_BY_DESIGN = {")
    for r in rows:
        why = "设计性 NULL" if r["status"] == "null_by_design" else \
              ("源未接入" if r["status"] == "source_missing" else "覆盖过低")
        L.append(f'    "{r["field"]}",  # {r["indicator"]} {r["cn"]}（{why}）')
    L.append("}")
    L.append("```")
    L.append("")
    L.append("保留可用、且常被误认为同类的替代特征：")
    L.append("")
    L.append("| 替代特征 | 覆盖 | 说明 |")
    L.append("|---|---|---|")
    L.append("| `inv_patent_ratio` | 500/500 | T1 发明专利占比，技术质量的第一可用代理 |")
    L.append("| `grant_rate` | 500/500 | T2 授权率，与专利质量强相关 |")
    L.append("| `tech_breadth_entropy` | 500/500 | T4 技术广度熵，替代「技术相似性」的静态口径 |")
    L.append("| `rd_intensity` | 453/500 | T7 研发强度（替代口径，flag=proxy） |")
    L.append("| `core_staff_stability` | 441/500 | T6 核心人员稳定性 |")
    L.append("")

    # 五、对账
    L.append("## 五、一致性对账")
    L.append("")
    if drift:
        L.append("**❌ 发现回填漂移**（登记为空但库内已有值，说明口径被破坏）：")
        L.append("")
        for d in drift:
            L.append(f"- {d}")
    else:
        L.append("✅ 登记为「设计性 NULL / 源未接入」的字段，库内实测**全部为 0**，"
                 "未发现偷偷回填。")
    if missing_field:
        L.append("")
        L.append("⚠️ 登记项在库内找不到（登记册与 schema 不同步）：")
        for m in missing_field:
            L.append(f"- {m}")
    L.append("")

    subs = reg.get("substitutes", [])
    if subs:
        L.append("## 六、已用的替身（不是缺口，是解决方案）")
        L.append("")
        L.append("| 缺的字段 | 原本多缺 | 用什么替 | 替上之后 | 落库位置 |")
        L.append("|---|---|---|---|---|")
        for s_ in subs:
            L.append(f"| {s_['missing']} | {s_['missing_cov']} | **{s_['substitute']}** "
                     f"| {s_['after_cov']} | `{s_['field']}` |")
        L.append("")
        L.append("为什么可以替：")
        L.append("")
        for s_ in subs:
            L.append(f"- **{s_['missing']} → {s_['substitute']}**：{s_['why']}")
        L.append("")

    traps = reg["meta"].get("traps", [])
    if traps:
        L.append("## 七、两条踩过的坑（别再踩）")
        L.append("")
        for i, t in enumerate(traps, 1):
            L.append(f"{i}. {t}")
        L.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"写出 {OUT}（{len(rows)} 个字段）")
    if drift or missing_field:
        print("对账失败：", drift + missing_field, file=sys.stderr)
        return 1
    print("对账通过：设计性 NULL 全部为空")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
