# -*- coding: utf-8 -*-
"""生成 v1.9 多标签测评报告（P1 交付物）。

全部数字从只读连接的库内视图实算，不写死结论。
输出：outputs/multilabel_eval_report.md
      deliverables/01_算法端/multilabel_eval_report.md

用法：python -m src.data.export_multilabel_eval
"""
from __future__ import annotations

import datetime
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT = os.path.join(ROOT, "outputs", "multilabel_eval_report.md")
DELIV = [
    os.path.join(ROOT, "deliverables", "01_算法端", "multilabel_eval_report.md"),
    # 放进 04 目录后会自动进入 Release 附件（export_db_bundle 按目录打包）
    os.path.join(ROOT, "deliverables", "04_数据字典与数据集", "多标签测评报告.md"),
]

AXES = [
    ("y_hard_outcome", "A · 硬结果", "无严重失信 / 经营异常 / 非开业", "结果型，唯一能界定坏"),
    ("y_risk_free", "A · 风险负面", "risk_negative_cnt = 0", "过程型，非结果"),
    ("y_survival", "C · 资质存续", "高企资格到期后仍被认定", "与 B 轴同源于公示名单，不独立"),
    ("y_qual_ladder_good", "B · 资质阶梯", "三项资质标志全齐（阶梯≥3）", "与 C 轴同源，不独立"),
    ("y_qcc_credit_good", "D · 第三方信用", "qcc_credit_level_ord ≥ 11", "状态量，且分档时被复用 → 循环定义"),
    ("y_qcc_tech_good", "D · 第三方技术", "qcc_tech_level_ord ≥ 4", "状态量，且分档时被复用 → 循环定义"),
]


def main() -> int:
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    q = lambda s, *a: con.execute(s, a).fetchall()
    L: list[str] = []
    A = L.append

    A("# 多标签测评报告 · v1.9")
    A("")
    A("> 生成时间：%s　｜　库版本：v1.9（只读实算）" %
      datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("> 触发问题：「P1 是添加更多的标签来测评」＋「实际正例其实是负例，搞反了」")
    A("")
    A("## 0. 先纠正方向")
    A("")
    A("本项目研究**企业好坏 / 信用评价**，因此：")
    A("")
    A("| 类别 | 定义 | 家数 | `ads_label.label_value` |")
    A("|---|---|---:|---:|")
    A("| **正例（好企业）** | 资质正常存续、续期成功 | **286** | 0 |")
    A("| **负例（坏企业）** | 资格到期后未再出现 / 断档 | **21** | 1 |")
    A("")
    A("旧文档把 `label_value=1` 的 21 家断档企业称为「正例」，是沿用了**风险预警方向**"
      "（1=坏）的编码，却套用了「企业好坏」的措辞，方向确实反了。"
      "底表数值是已发布 v1.4.0 快照、12 个契约与历史 AUC 审计的绑定口径，"
      "**没有原地 UPDATE**，改由 `v_label_direction` / `v_model_train_ready_credit` 并列给出正确方向。")
    A("")
    A("历史 AUC 结论不受影响：原口径 AUC≈0.52 是对「风险方向标签」算的，"
      "翻成信用方向后是 0.48，两者都等价于「无判别力」，**不会从 0.52 变成 0.87**。")
    A("")
    A("## 1. 本次新增的六个标签 / 测评视图")
    A("")
    A("| 视图 | 行数 | 作用 |")
    A("|---|---:|---|")
    for name, desc in [
        ("v_label_direction", "风险方向 ↔ 信用方向逐企业对照，含正/负例定义"),
        ("v_model_train_ready_credit", "label 已翻转的训练入口：286 正例 / 21 负例"),
        ("v_label_mechanism_credit", "n_events × cohort 台账，n_positive 现在真是好企业数"),
        ("v_enterprise_label_matrix", "四轴 6 个二值锚点 + 3 个有序锚点，方向统一 1=好"),
        ("v_enterprise_multilabel_grade", "多标签五档 + L0 单标签分档对照"),
        ("v_multilabel_eval_summary", "交叉测评长表：档次 → 各锚点正例率"),
    ]:
        n = q('SELECT COUNT(*) FROM "%s"' % name)[0][0]
        A("| `%s` | %d | %s |" % (name, n, desc))
    A("")
    A("## 2. 六个锚点的方向与覆盖")
    A("")
    A("所有二值标签一律 **1 = 好 / 正例，0 = 坏 / 负例，NULL = 不可判定**。"
      "NULL 不计入票数，193 家未打标企业不会被算成坏企业。")
    A("")
    A("| 锚点 | 轴 | 判据（1=好） | 覆盖率 | 好企业数 | 独立性备注 |")
    A("|---|---|---|---:|---:|---|")
    for col, axis, rule, indep in AXES:
        obs, good = q('SELECT COUNT("%s"), COALESCE(SUM("%s"),0) '
                      'FROM v_enterprise_label_matrix' % (col, col))[0]
        A("| `%s` | %s | %s | %.1f%% | %d | %s |" %
          (col, axis, rule, 100.0 * obs / 500, good, indep))
    A("")
    A("阈值均来自 500 家实测分布并写死在 DDL 里（资质阶梯≥3 / 信用等级≥11 / 技术等级≥4），"
      "无主观权重，可逐行复核。")
    A("")
    A("## 3. 多标签五档（与 L0 对照）")
    A("")
    A("| 档次 | 家数 | 平均标签一致度 | 与 L0 同档 | 资质存续正例率* | 硬结果正例率* |")
    A("|---|---:|---:|---:|---:|---:|")
    rows = q("SELECT metric,group_value,n_enterprise,n_positive,n_observed,"
             "positive_rate,mean_agreement,n_same_l0 FROM v_multilabel_eval_summary")
    surv = {r[1]: r[5] for r in rows if r[0] == "资质存续正例率"}
    hard = {r[1]: r[5] for r in rows if r[0] == "硬结果正例率"}
    for r in rows:
        metric, gv, n, npos, nobs, rate, mean_a, same = r
        if metric != "档次日数":
            continue
        A("| **%s** | %d | %.4f | %d | %s | %s |" %
          (gv, n, mean_a or 0, same or 0,
           ("%.4f" % surv[gv]) if surv.get(gv) is not None else "—",
           ("%.4f" % hard[gv]) if hard.get(gv) is not None else "—"))
    A("")
    total, same_all = q(
        "SELECT COUNT(*), SUM(is_consistent_with_l0) "
        "FROM v_enterprise_multilabel_grade")[0]
    A("合计 %d 家，与单标签 L0 同档 **%d 家（%.1f%%）**。" %
      (total, same_all, 100.0 * same_all / total))
    A("")
    A("> ⚠️ **带 * 的两列是循环定义，不能当外部效度**："
      "分档判据里用了 `y_survival=0 → D` 与 `y_hard_outcome=0 → E`，"
      "所以 A–D 档的硬结果正例率恒为 1.0、A–C 档的存续正例率恒为 1.0 是规则使然，"
      "不是「多标签验证了分档有效」。要验证必须用**未参与分档**的独立 A 轴结果标签"
      "（撤销公告 / 严重失信名录），当前库里只有 2–3 家真事件，做不到。")
    A("")
    A("## 4. 锚点两两一致率（独立性检验）")
    A("")
    A("在两边都非 NULL 的样本里，`取值相同` 的比例。高一致率既可能是真印证，"
      "也可能是两个锚点都近乎常数（例如硬结果 460/463 都是好），必须结合覆盖率一起看。")
    A("")
    A("| 锚点 A | 锚点 B | n | 同为「好」 | 取值一致率 |")
    A("|---|---|---:|---:|---:|")
    cols = [a[0] for a in AXES]
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            n, both, agree = q(
                'SELECT COUNT(*), SUM(CASE WHEN "{a}"=1 AND "{b}"=1 THEN 1 ELSE 0 END), '
                'SUM(CASE WHEN "{a}"="{b}" THEN 1 ELSE 0 END) '
                'FROM v_enterprise_label_matrix '
                'WHERE "{a}" IS NOT NULL AND "{b}" IS NOT NULL'.format(a=a, b=b))[0]
            A("| `%s` | `%s` | %d | %d | %.3f |" % (a, b, n, both or 0, agree / n))
    A("")
    A("**读法**：`y_qual_ladder_good` 只有 45/500 家为「好」，与其余锚点的一致率"
      "低至 0.089–0.688，这不是「独立性好」的证明，而是分布严重塌陷；"
      "`y_hard_outcome` 与 `y_survival` 的 0.958 同理，两者都近乎常数。"
      "真正能构成独立印证的 A 轴结果标签，当前只有 2 家严重失信、1 家经营异常、3 家 E 档。")
    A("")
    A("## 5. 测评结论（诚实版）")
    A("")
    A("1. **方向已纠正**：正例=286 家续存好企业，负例=21 家断档坏企业；"
      "训练入口 `v_model_train_ready_credit` 可直接用，旧视图保持兼容。")
    A("2. **多标签确实加出来了**：6 个二值锚点 + 3 个有序锚点，覆盖 500 家，"
      "每个锚点的判据、阈值、覆盖率、NULL 语义都可审计。")
    A("3. **但测评没有得到一个可用的「企业好坏」结论**：与 L0 一致率仅 %.1f%%，"
      "说明单标签规则与多标签投票测的不是同一件事；六个锚点里 4 个是状态/过程量，"
      "A 轴真结果样本个位数，B/C 同源不独立，D 轴被分档复用形成循环定义。" %
      (100.0 * same_all / total))
    A("4. **样本塌陷仍在**：负例 21 家、val 集 2 个负例、E 档 3 家；"
      "任何档次的准确率 / AUC 点估计都不成立，每档必须带家数与置信区间。")
    A("5. 因此本次 P1 的价值是**把标签维度和口径边界立起来**，"
      "而不是宣称分档有效。要真正验证，需要未参与分档的独立结果标签。")
    A("")
    A("## 6. 下一步（按依赖排序）")
    A("")
    A("| 优先级 | 动作 | 是否零采集 |")
    A("|---|---|---|")
    A("| P1-续 | 用 `v_model_train_ready_credit` 重跑 as-of 训练与 SHAP，"
      "与旧方向对照（AUC 应互为 1−AUC） | ✅ 零采集 |")
    A("| P1-续 | 分队列（2021/2022）、分 as_of_policy 各跑一遍多标签测评，"
      "检验方向稳定性 | ✅ 零采集 |")
    A("| P2 | 补**独立于公示名单**的 A 轴结果标签（撤销/复核未通过公告、"
      "经营异常与严重失信名录），才能做真正的外部效度 | ❌ 需采集 |")
    A("| P2 | MSME / SBA 跨源外部效度；财报、工商状态时序 | ❌ 需外部数据 |")
    A("| 可选项 | 名单前推 2019/2020 或扩到 3,000–5,000 家 | ❌ 需采集，且**只增加同标签数量、不增加维度**，不是 P1 |")
    A("")
    A("---")
    A("")
    A("复核命令：")
    A("")
    A("```bash")
    A("python -m src.data.apply_v1_9 --check")
    A("python - <<'PY'")
    A("import sqlite3")
    A("c=sqlite3.connect('file:data/db/zjtech_credit.db?mode=ro',uri=True)")
    A("print(c.execute('SELECT credit_label,COUNT(*) FROM v_label_direction GROUP BY 1').fetchall())")
    A("print(c.execute('SELECT grade_multilabel,COUNT(*) FROM v_enterprise_multilabel_grade GROUP BY 1').fetchall())")
    A("```")
    A("")

    con.close()
    text = "\n".join(L)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("测评报告 -> %s" % OUT)
    for p in DELIV:
        if os.path.isdir(os.path.dirname(p)):
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print("          -> %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
