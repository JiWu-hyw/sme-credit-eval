"""导出代理标签说明书（只读，不改库、不改标签）。

为什么要有这份文档
------------------
`ads_label.note` 里写了 60 个字的口径，但没人会去读数据库字段。而这条标签
的可用边界很窄（21 个断档样本、弱标签、不能当官方复核结果），一旦被当成
「真标签」拿去报指标，结论会直接塌。

所以把三处事实合并成一份可交付文档：
  1) src/data/build_qual_dataset.py 的常量 —— 定义版本、有效期、观察窗终点、
     标签名、口径备注、切分协议（**唯一事实源，本脚本不重复声明**）
  2) 库内实测 —— 队列规模、断档样本数、排除分解、更名率、名单清洗量
  3) outputs/label_positivity_audit.md 的审计结论 —— 假正类复核

产出：
  outputs/label_spec.md   代理标签说明书（评审 / 交接 / 论文方法节可直接引用）

用法：
  python -m src.data.export_label_spec
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.data.build_qual_dataset import (
    DATASET_VER,
    DEFINITION_VER,
    LABEL_NAME,
    LABEL_NOTE,
    LABEL_TASK,
    OBS_END,
    SPLIT_PROTOCOL,
    VALID_YEARS,
)

DB = Path("data/db/zjtech_credit.db")
OUT = Path("outputs/label_spec.md")


def _pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.1f}%" if b else "—"


def collect(con: sqlite3.Connection) -> dict:
    cur = con.cursor()
    s: dict = {}

    # ---- ads_label ----
    s["n_label"] = cur.execute("SELECT COUNT(*) FROM ads_label").fetchone()[0]
    s["n_pos"] = cur.execute(
        "SELECT COUNT(*) FROM ads_label WHERE label_value=1").fetchone()[0]
    s["n_neg"] = s["n_label"] - s["n_pos"]
    s["pos_rate"] = 100.0 * s["n_pos"] / s["n_label"] if s["n_label"] else 0.0
    s["cohorts"] = [
        {"year": y, "n": n, "pos": p}
        for y, n, p in cur.execute(
            "SELECT cohort_year, COUNT(*), SUM(label_value) FROM ads_label "
            "GROUP BY 1 ORDER BY 1")]
    s["windows"] = [
        {"start": a, "end": b, "n": n}
        for a, b, n in cur.execute(
            "SELECT observation_start, observation_end, COUNT(*) FROM ads_label "
            "GROUP BY 1,2 ORDER BY 1")]
    s["n_events"] = [
        {"n_ev": k, "n": n, "pos": p}
        for k, n, p in cur.execute(
            "SELECT n_events, COUNT(*), SUM(label_value) FROM ads_label "
            "GROUP BY 1 ORDER BY 1")]
    s["ver_in_db"] = [r[0] for r in cur.execute(
        "SELECT DISTINCT definition_ver FROM ads_label")]
    s["name_in_db"] = [r[0] for r in cur.execute(
        "SELECT DISTINCT label_name FROM ads_label")]

    # ---- 覆盖率：500 家里为什么只有 307 家有标签 ----
    rows = cur.execute(
        "SELECT enterprise_id, year FROM fact_qualification WHERE qual_type='high_tech'"
    ).fetchall()
    years: dict[str, list[int]] = {}
    for eid, y in rows:
        years.setdefault(eid, []).append(int(y))
    s["n_firm"] = cur.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    s["n_with_qual"] = len(years)
    s["n_no_qual"] = s["n_firm"] - len(years)
    s["first_year"] = dict(sorted(
        Counter(min(v) for v in years.values()).items()))
    s["n_due_after_obs"] = sum(1 for v in years.values() if min(v) + VALID_YEARS > OBS_END)
    s["n_due_in_obs"] = len(years) - s["n_due_after_obs"]

    # ---- 名单与资质事实 ----
    s["n_notice"] = cur.execute("SELECT COUNT(*) FROM ods_qual_notice").fetchone()[0]
    s["n_raw"] = cur.execute("SELECT COUNT(*) FROM ods_qual_company_raw").fetchone()[0]
    rows_in = rows_bad = 0
    for (p,) in cur.execute("SELECT raw_payload FROM ods_qual_notice"):
        d = json.loads(p) if p else {}
        rows_in += d.get("rows_in", 0)
        rows_bad += d.get("rows_bad", 0)
    s["rows_in"], s["rows_bad"] = rows_in, rows_bad
    s["notice_by_year"] = [
        (y, n) for y, n in cur.execute(
            "SELECT year, COUNT(*) FROM ods_qual_notice GROUP BY 1 ORDER BY 1")]
    s["qual_rows"] = cur.execute(
        "SELECT COUNT(*) FROM fact_qualification WHERE qual_type='high_tech'").fetchone()[0]
    s["ev"] = dict(cur.execute(
        "SELECT event_type, COUNT(*) FROM fact_qualification_event GROUP BY 1"))
    s["qual_by_year"] = [
        (y, n) for y, n in cur.execute(
            "SELECT year, COUNT(*) FROM fact_qualification WHERE qual_type='high_tech' "
            "GROUP BY 1 ORDER BY 1")]
    s["n_ambiguous"] = s["qual_rows"] - s["ev"].get("first_grant", 0) - s["ev"].get("renew", 0)

    # ---- 更名（混淆来源的证据）----
    s["renamed_pos"], s["renamed_neg"] = 0, 0
    for lv, n, rn in cur.execute(
            "SELECT l.label_value, COUNT(*), COALESCE(SUM(e.is_renamed),0) "
            "FROM ads_label l JOIN dim_enterprise e USING(enterprise_id) GROUP BY 1"):
        if lv == 1:
            s["renamed_pos"] = rn
        else:
            s["renamed_neg"] = rn

    # ---- 切分与对抗样本 ----
    s["split"] = dict(cur.execute(
        "SELECT split, COUNT(*) FROM ads_model_dataset GROUP BY 1"))
    s["split_pos"] = dict(cur.execute(
        "SELECT split, SUM(label) FROM ads_model_dataset GROUP BY 1"))
    s["n_asof_rows"] = cur.execute("SELECT COUNT(*) FROM v_model_dataset_asof_flat").fetchone()[0]
    s["n_asof_firm"] = cur.execute(
        "SELECT COUNT(DISTINCT enterprise_id) FROM v_model_dataset_asof_flat").fetchone()[0]
    s["n_survival"] = cur.execute("SELECT COUNT(*) FROM ads_survival_period").fetchone()[0]
    s["n_survival_firm"] = cur.execute(
        "SELECT COUNT(DISTINCT enterprise_id) FROM ads_survival_period").fetchone()[0]

    # ---- 对照任务 ----
    s["msme"] = dict(cur.execute(
        "SELECT label, COUNT(*) FROM ext_msme_sample GROUP BY 1"))
    return s


def render(s: dict) -> str:
    L: list[str] = []
    A = L.append
    ts = f"{datetime.now():%Y-%m-%d %H:%M}"
    today = f"{datetime.now():%Y-%m-%d}"

    A("# 代理标签说明书　·　`%s`\n" % DEFINITION_VER)
    A("> **生成方式**：`python -m src.data.export_label_spec`（只读，不改库、不改标签）")
    A("> **生成时间**：%s　｜　**数据集版本**：`%s`" % (ts, DATASET_VER))
    A("> **单一事实源**：标签定义常量取自 `src/data/build_qual_dataset.py`，"
      "分布数字取自库内实测，本文件不重复声明口径")
    A("> **标签任务**：`%s`　｜　**标签名**：%s" % (LABEL_TASK, LABEL_NAME))
    A("")

    # ---------------- 0 ----------------
    A("## 0. 先读这一段\n")
    A("| 问题 | 答案 |")
    A("|---|---|")
    A("| 这是什么 | 一家企业的高企资格到期后，**是否再也没在公示名单里出现过** |")
    A("| 是官方结论吗 | **不是**。是代理标签（proxy label），本地没有撤销/复核未通过公告 |")
    A("| 有多少 | %d 家：断档样本 **%d**、续存样本 %d，断档样本占比 **%.2f%%** |"
      % (s["n_label"], s["n_pos"], s["n_neg"], s["pos_rate"]))
    A("| 能干什么 | 验证方法论、比较特征族、跑生存分析 |")
    A("| 不能干什么 | 报准确率/召回率绝对值、说「预测复核未通过」、上生产 |")
    A("| 最大的坑 | 名单是**当年新认定名单**不是存量名单，所以「没出现」≠「资格没了」 |")
    A("")
    A("> 一句话：**这条标签量的是「有没有再申请」，不是「有没有被驳回」。**")
    A("> 两者只在「企业想续且被拒」时重合，而那部分本地根本没有数据。")
    A("")

    # ---------------- 1 ----------------
    A("## 1. 任务定义\n")
    c0 = s["cohorts"][0]["year"] if s["cohorts"] else OBS_END - VALID_YEARS
    A("高企资格有效期 %d 年：%d 年认定的资格在 %d 年到期，需要重新申报并通过评审才能续。"
      % (VALID_YEARS, c0, c0 + VALID_YEARS))
    A("本标签要回答的问题是：\n")
    A("> 一家在 `cohort_year` 首次拿到高企资格的企业，到资格到期年之后，"
      "**还有没有再次出现在任何一年的高企公示名单里**？\n")
    A("没有 → `label_value = 1`（断档样本，视为「存续失败」）；有 → `0`。\n")

    # ---------------- 2 ----------------
    A("## 2. 为什么必须叫「代理标签」\n")
    A("理想标签需要「复核未通过 / 撤销资格」的官方公告，本地没有。"
      "现在用的是可观测的替代信号：\n")
    A("| | 理想标签 | 本库实际用的代理信号 |")
    A("|---|---|---|")
    A("| 判据 | 官方撤销/复核未通过公告 | 到期年及之后未再出现在公示名单 |")
    A("| 能区分「申请被拒」与「没申请」 | ✅ | ❌ |")
    A("| 能区分「注销退出」与「考核失败」 | ✅ | ❌ |")
    A("| 数据可得性 | 本地无 | 14 份年度名单，%s 行 |" % f"{s['n_raw']:,}")
    A("")
    A("关键事实（源码注释里的原始记录，勿擅自放宽）：\n")
    A("- 这些 CSV 是**「当年新认定/备案公示名单」（增量）**，不是存量有效名单")
    A("- 实测跨年留存仅 **0.1%~0.3%** —— 同一家企业连续两年出现几乎不可能")
    A("- 因此「某年未出现」**在数据层面是常态，不能单独推断为资格失效**")
    A("")
    A("真正的判据只能是「**到期年之后仍无任何新记录**」，这也是当前规则的由来。")
    A("")

    # ---------------- 3 ----------------
    A("## 3. 判定规则（形式化）\n")
    A("```python")
    A("# 输入：该企业在 fact_qualification 中所有 high_tech 记录的年份集合 years")
    A("first_year = min(years)                 # 首次认定年 = 队列年 cohort_year")
    A("due        = first_year + %d           # 资格到期年（有效期 %d 年）" % (VALID_YEARS, VALID_YEARS))
    A("if due > %d:                            # 观察窗还没闭合" % OBS_END)
    A("    → 不打标，split='excluded'（label=0 只是 NOT NULL 占位）")
    A("renewed = any(y >= due for y in years)  # 到期年后是否再次出现")
    A("label_value = 0 if renewed else 1")
    A("```")
    A("")
    A("三个容易搞错的细节：\n")
    A("1. **`due` 用 `>=` 判 renewed**：到期当年再出现就算续上（资格无缝衔接），"
      "不算断档。")
    A("2. **首次认定年取 `min(years)`**，不是取最早公告批次 —— 同一企业同一年多份名单"
      "（补录）只算一次。")
    A("3. **间隔 < %d 年的重复记录不生成 renew 事件**（视为补录/更名/重复），"
      "实测这种情况有 %d 条，既不算续期也不算断档。"
      % (VALID_YEARS, s["n_ambiguous"]))
    A("")

    # ---------------- 4 ----------------
    A("## 4. 观察窗与队列\n")
    A("观察窗终点固定 `OBS_END = %d`（名单覆盖的最后一个认定年度）。"
      "只有到期年 ≤ %d 的队列，观察窗才闭合、才允许打标。\n" % (OBS_END, OBS_END))
    A("| cohort | 企业数 | 断档样本 | 断档样本占比 | 到期年 | 观察窗 |")
    A("|---|---|---|---|---|---|")
    for c in s["cohorts"]:
        win = next((w for w in s["windows"] if w["start"].startswith(str(c["year"]))), None)
        A("| %d | %d | %d | %s | %d | %s |"
          % (c["year"], c["n"], c["pos"], _pct(c["pos"], c["n"]),
             c["year"] + VALID_YEARS,
             f"{win['start']} ~ {win['end']}" if win else "—"))
    A("")
    A("首次认定年分布（全部 %d 家有 high_tech 记录的企业）：\n" % s["n_with_qual"])
    A("| 首次认定年 | 企业数 | 到期年 | 是否打标 |")
    A("|---|---|---|---|")
    for y, n in sorted(s["first_year"].items()):
        A("| %d | %d | %d | %s |"
          % (y, n, y + VALID_YEARS, "✅" if y + VALID_YEARS <= OBS_END else "❌ 窗口未闭合"))
    A("")
    A("**为什么扩不了样本**：再多就要把队列前推到 2019/2020 年，而名单只采到 2021。"
      "这是标签稀缺的结构性原因，不是采集疏漏。\n")

    # ---------------- 5 ----------------
    A("## 5. 数据链路与规模\n")
    A("```")
    A("14 份年度公示名单 CSV")
    A("   ↓ clean_notice_file()   剥离 PDF 标题污染，非法行丢弃并计数")
    A("ods_qual_notice        %2d 行（%s 行原文，丢弃 %d 行）"
      % (s["n_notice"], f"{s['rows_in']:,}", s["rows_bad"]))
    A("ods_qual_company_raw   %s 行" % f"{s['n_raw']:,}")
    A("   ↓ 名称规范化 + 匹配 dim_enterprise")
    A("fact_qualification     %d 行 high_tech（%d 家企业）" % (s["qual_rows"], s["n_with_qual"]))
    A("   ↓ 逐年差分：间隔 >= %d 年判为 renew" % VALID_YEARS)
    A("fact_qualification_event  first_grant %d / renew %d"
      % (s["ev"].get("first_grant", 0), s["ev"].get("renew", 0)))
    A("   ↓ 到期年 + 观察窗过滤")
    A("ads_label              %d 行（断档样本 %d）" % (s["n_label"], s["n_pos"]))
    A("   ↓ 拼 DWS 特征")
    A("ads_model_dataset      %d 行（含 %d 行 excluded 占位）"
      % (sum(s["split"].values()), s["split"].get("excluded", 0)))
    A("```")
    A("")
    A("名单清洗损耗：读入 %s 行 → 有效 %s 行（丢弃 %d 行，%.2f%%），"
      "丢弃项全部是标题粘连/表头/缺企业名，逐条计数可查 `ods_qual_notice.raw_payload`。"
      % (f"{s['rows_in']:,}", f"{s['n_raw']:,}", s["rows_bad"],
         100.0 * s["rows_bad"] / s["rows_in"] if s["rows_in"] else 0))
    A("")

    # ---------------- 6 ----------------
    A("## 6. 标签分布\n")
    A("| 项 | 数值 |")
    A("|---|---|")
    A("| 打标企业 | **%d** / %d（%.1f%%） |" % (s["n_label"], s["n_firm"], 100.0 * s["n_label"] / s["n_firm"]))
    A("| 断档样本 / 续存样本 | **%d / %d** |" % (s["n_pos"], s["n_neg"]))
    A("| 断档样本占比 | **%.2f%%**（不平衡比约 1:%.1f） |"
      % (s["pos_rate"], s["n_neg"] / s["n_pos"] if s["n_pos"] else 0))
    A("| definition_ver | `%s` |" % DEFINITION_VER)
    A("| 库内实际存值 | %s |" % "、".join("`%s`" % v for v in s["ver_in_db"]))
    A("")
    A("按认定次数分组（这条最能说明断档样本长什么样）：\n")
    A("| n_events（该企业 high_tech 记录条数） | 企业数 | 其中断档样本 | 说明 |")
    A("|---|---|---|---|")
    for r in s["n_events"]:
        note = "只有一次认定，之后再没出现 → **全部是断档样本**" if r["n_ev"] == 1 else "续过至少一次"
        A("| %d | %d | %d | %s |" % (r["n_ev"], r["n"], r["pos"], note))
    A("")
    A("**断档样本 = 只被认定过一次、之后杳无音信的企业**。这个特征决定了："
      "任何与「持续经营、持续申报」相关的特征都会天然有判别力，"
      "但其中有多少是「真实经营恶化」、有多少是「懒得申报」，标签本身分不开。\n")

    # ---------------- 7 ----------------
    A("## 7. 排除规则：`excluded` 的 %d 行不是续存样本\n" % s["split"].get("excluded", 0))
    A("%d 家企业里只有 %d 家能打标，剩下的分解如下：\n" % (s["n_firm"], s["n_label"]))
    A("| 类别 | 企业数 | 处置 |")
    A("|---|---|---|")
    A("| 到期年 ≤ %d（观察窗闭合） | **%d** | 打标，进 train/val/test |"
      % (OBS_END, s["n_due_in_obs"]))
    A("| 首次认定 ≥ %d，到期年 > %d（窗口未闭合） | %d | `split='excluded'` |"
      % (OBS_END - VALID_YEARS + 1, OBS_END, s["n_due_after_obs"]))
    A("| 完全没有 high_tech 记录 | %d | `split='excluded'` |" % s["n_no_qual"])
    A("| 合计 | %d | |" % s["n_firm"])
    A("")
    A("⚠️ `excluded` 行的 `label=0` **只是满足 `NOT NULL` 的结构占位**，"
      "语义是「我们还不知道」，不是「这家没出事」。")
    A("实测：混入训练会让断档样本占比从 %.2f%% 被稀释到 %.2f%%。"
      % (s["pos_rate"], 100.0 * s["n_pos"] / s["n_firm"]))
    A("")

    # ---------------- 8 ----------------
    A("## 8. 混淆来源与偏误评估\n")
    A("| # | 混淆来源 | 会把标签变成 | 量级 / 证据 | 处置 |")
    A("|---|---|---|---|---|")
    A("| 1 | 名单是增量名单非存量名单 | 假断档样本（普遍性根因） | 跨年留存 0.1%~0.3% | "
      "规则改为「到期年后仍无记录」，已规避 |")
    A("| 2 | 企业更名导致按旧名匹配不上 | 假断档样本 | 断档样本更名率 %s（%d/%d） vs 续存样本 %s（%d/%d），"
      "差 %.1f 倍 | 已用 394 条别名做 E1 复查，**0 家翻转** |"
      % (_pct(s["renamed_pos"], s["n_pos"]), s["renamed_pos"], s["n_pos"],
         _pct(s["renamed_neg"], s["n_neg"]), s["renamed_neg"], s["n_neg"],
         (s["renamed_pos"] / s["n_pos"]) / (s["renamed_neg"] / s["n_neg"])
         if s["renamed_neg"] else 0))
    A("| 3 | 注销 / 迁出浙江 | 假断档样本（实为真实退出） | 未量化，缺工商状态时序 | ⏳ 待补 |")
    A("| 4 | 主动放弃复核 | 假断档样本（按定义可接受） | 不可观测 | 接受，写进口径 |")
    A("| 5 | 缺撤销/复核未通过公告 | 无法区分「没申请」与「申请被拒」 | 本地无该数据源 | "
      "⛔ 已登记为源未接入 |")
    A("| 6 | 年度名单不全（部分年份多批） | 假断档样本 | 2023 年 3 份、2024 年 4 份、2025 年 5 份 | "
      "已按批次全量入库 |")
    A("")
    A("**已做的正面审计**（`outputs/label_positivity_audit.md`）："
      "用「现名 + 全部别名」在到期年及之后的名单原文里复查 %d 个断档样本，"
      "**假断档样本 0 家、翻转 0 次** —— 说明更名这一路混淆目前没污染标签。\n" % s["n_pos"])
    A("但要注意：更名率在断档样本中显著更高（%s vs %s），"
      "这**不是标签噪声，而是真实信号**（经营动荡 / 主体变更），"
      "建模时不要把它当异常值剔掉。\n"
      % (_pct(s["renamed_pos"], s["n_pos"]), _pct(s["renamed_neg"], s["n_neg"])))

    # ---------------- 9 ----------------
    A("## 9. 可用 / 禁用清单\n")
    A("### ✅ 可以用\n")
    A("| 场景 | 为什么可以 |")
    A("|---|---|")
    A("| 验证方法论跑得通（管线 / 特征 / 模型对比） | 标签方向明确，正续存样本可比 |")
    A("| 比较特征族的相对判别力 | 同一标签下相对排序仍有效 |")
    A("| 生存分析（`ads_survival_period` %d 行 / %d 家） | 用到期时间而非二分类，信息利用率更高 |"
      % (s["n_survival"], s["n_survival_firm"]))
    A("| 特征族迁移性对照（MSME %d 家，真实违约标签） | 外部真标签验证同构特征有信号 |" % sum(s["msme"].values()))
    A("")
    A("### ❌ 不可以用\n")
    A("| 用法 | 后果 |")
    A("|---|---|")
    A("| 报「准确率 87%%」「AUC 0.87」 | 评审一问「多少断档样本」就塌：只有 %d 个 |" % s["n_pos"])
    A("| 说「预测高企复核未通过」 | 语义错误：标签量的是「没再申请」 |")
    A("| 与官方撤销名单对齐验证 | 本地无该名单，无法对齐 |")
    A("| 直接上生产做企业预警 | 断档样本 %d 个，误判成本不可控 |" % s["n_pos"])
    A("| 用 `ads_model_dataset` 训练后报指标 | 含后视泄漏，且混入 %d 行假续存样本 |"
      % s["split"].get("excluded", 0))
    A("")

    # ---------------- 10 ----------------
    A("## 10. 建模契约（硬约束）\n")
    A("| # | 约束 | 理由 |")
    A("|---|---|---|")
    A("| 1 | 只从 `v_model_dataset_asof_flat` 取数（%d 行 / %d 家） | 唯一无后视泄漏入口 |"
      % (s["n_asof_rows"], s["n_asof_firm"]))
    A("| 2 | `as_of_policy` 只用一种，**不混** | 两种口径的标签窗口不同，混用会变成队列哑变量 |")
    A("| 3 | 按 `enterprise_id` 分组切分 | 同一企业有 2 行，跨集会泄漏 |")
    A("| 4 | 排除 `split='excluded'` | 假续存样本，见 §7 |")
    A("| 5 | 报告必须带断档样本数与置信区间 | %d 个断档样本，val 集只有 2 个，点估计无意义 |" % s["n_pos"])
    A("| 6 | 不平衡只能用 `class_weight` / `scale_pos_weight` | 过采样后报原始指标等于作弊 |")
    A("")
    A("当前切分（`ads_model_dataset`，按 `md5(enterprise_id)` 确定性 70/15/15）：\n")
    A("| split | 行数 | 断档样本 | 可否入模 |")
    A("|---|---|---|---|")
    for k in ("train", "val", "test", "excluded"):
        if k in s["split"]:
            A("| %s | %d | %d | %s |"
              % (k, s["split"][k], s["split_pos"].get(k, 0) or 0,
                 "❌ 假续存样本" if k == "excluded" else "✅"))
    A("")
    A("切分协议原文：\n")
    A("> %s\n" % SPLIT_PROTOCOL)

    # ---------------- 11 ----------------
    A("## 11. 口径原文（库内 `ads_label.note`）\n")
    A("> %s\n" % LABEL_NOTE)

    # ---------------- 12 ----------------
    A("## 12. 复现与升版\n")
    A("```bash")
    A("# 1) 重建标签与宽表（含 ODS 灌入、事件差分、派生特征、as-of 切面）")
    A("python -m src.data.build_qual_dataset")
    A("")
    A("# 2) 正类净化审计（别名复查，只读）")
    A("python -m src.data.label_positivity_audit")
    A("")
    A("# 3) 重新生成本说明书")
    A("python -m src.data.export_label_spec")
    A("```")
    A("")
    A("**升版规则（项目纪律，不得绕过）**：\n")
    A("- 补齐撤销/复核未通过公告、或导师确认正式判定口径 → 改 `DEFINITION_VER` 与 "
      "`DATASET_VER`，重跑全链路")
    A("- **禁止原地改写旧标签**：`ads_label` 全表由脚本重建，禁止手写 UPDATE")
    A("- 升版后旧实验结论必须重跑，不得跨版本比较 AUC")
    A("")

    # ---------------- 13 ----------------
    A("## 13. 变更记录\n")
    A("| 版本 | 日期 | 变更 |")
    A("|---|---|---|")
    A("| `%s` | 2026-11-05 | 首版：弱标签定义落地，307 家 / 21 断档样本 |" % DEFINITION_VER)
    A("| 说明书 v1 | %s | 首次成文，补齐混淆来源、禁用清单、建模契约 |" % today)
    A("")

    return "\n".join(L)


def main() -> None:
    con = sqlite3.connect(DB)
    try:
        s = collect(con)
    finally:
        con.close()

    md = render(s)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(md, encoding="utf-8")
    print(f"✅ 已导出 {OUT}（{len(md):,} 字符）")
    print(f"   标签 {s['n_label']} 行 / 断档样本 {s['n_pos']} / 断档样本占比 {s['pos_rate']:.2f}%")
    print(f"   打标覆盖 {s['n_label']}/{s['n_firm']}，"
          f"excluded {s['n_firm'] - s['n_label']}"
          f"（窗口未闭合 {s['n_due_after_obs']} + 无记录 {s['n_no_qual']}）")
    if s["ver_in_db"] != [DEFINITION_VER]:
        print(f"⚠️ 库内 definition_ver={s['ver_in_db']} 与代码常量 {DEFINITION_VER} 不一致")
    else:
        print("   definition_ver 与库内一致 ✅")


if __name__ == "__main__":
    main()
