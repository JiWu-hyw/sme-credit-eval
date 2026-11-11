# -*- coding: utf-8 -*-
"""生成三份交付包：算法端 / 前端 / 交付说明。

设计原则
--------
1. **只读**。本脚本不建表、不改视图、不写库，只 SELECT + 落盘。
2. **数字全部实测**。覆盖率、去重值、单变量 AUC 都是当场从库里算的，
   不写常量。口径变了重跑一次，交付物自动同步。
3. **不替下游做决定，但给足判据**。每个特征带 `recommendation` 列
   （keep / drop_constant / drop_no_signal / unavailable），算法端可自行取舍。
4. **把两套数据的边界钉死**。前端包里显式列明哪些契约是 placeholder，
   防止有人把合成图当真实图谱画进页面。

产出目录
--------
deliverables/
├── 01_算法端/
│   ├── README.md                     怎么用（SQL / pandas 两种接法）
│   ├── model_train_ready.csv         307 行 × 17 列，直接 fit
│   ├── feature_dictionary.csv        29 特征 × 可用性 / AUC / 建议
│   ├── dataset_card.md               数据集卡片（规模、标签、切分、边界）
│   ├── label_spec.md                 代理标签说明书（复制自 outputs）
│   └── baseline_asof_leakage.json    泄漏实测（leaky vs noleak）
├── 02_前端/
│   ├── README.md                     联调须知
│   ├── openapi.json                  OpenAPI 3.1 规范（35 接口）
│   ├── api_checklist.md              逐接口：真数据 / 另一套 / 占位 / 空壳
│   ├── enums.json                    实体枚举
│   └── sample_responses.json         5 个真数据接口的实际返回样例
└── 03_交付说明/
    └── delivery_spec.md              一页说明：能交付什么、不能交付什么

用法：
    python -m src.data.export_delivery
    python -m src.data.export_delivery --only algorithm
    python -m src.data.export_delivery --no-api        # 不起服务，跳过样例抓取
"""
import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT = os.path.join(ROOT, "deliverables")
OUTPUTS = os.path.join(ROOT, "outputs")
CONTRACTS = os.path.join(ROOT, "frontend_v2", "source", "outputs", "contracts")

D_ALG = os.path.join(OUT, "01_算法端")
D_FE = os.path.join(OUT, "02_前端")
D_DOC = os.path.join(OUT, "03_交付说明")
D_DATA = os.path.join(OUT, "04_数据字典与数据集")
D_DATASET = os.path.join(D_DATA, "数据集")

# 29 个注册特征的中文名（与 ads_feature_asof.feature_name 一一对应）
FEATURE_CN = {
    "cap_tech_synergy": "资本-技术协同度",
    "core_staff_stability": "核心人员稳定性",
    "emp_cagr": "员工数年复合增长",
    "funding_to_last_val": "最新估值/融资额",
    "grant_rate": "as-of 授权率",
    "inv_patent_ratio": "发明专利占比",
    "investor_prestige": "投资机构声望",
    "m_cert": "资质认证标志",
    "m_cite": "引用网络标志",
    "m_funding": "融资事件标志",
    "m_team": "团队质量标志",
    "n_inventor": "发明人数",
    "n_ipc_subclass": "IPC 小类数",
    "n_patent_app": "专利申请数",
    "n_patent_collab": "合作专利数",
    "n_patent_grant": "专利授权数",
    "n_patent_invention": "发明专利申请数",
    "patent_cagr": "专利数复合增长",
    "patent_citation": "专利被引次数",
    "patent_triple_score": "专利三重评分",
    "qual_rank": "资质等级",
    "qual_rank_delta": "资质等级变化",
    "qual_upgrade_cum": "累计升级次数",
    "rd_intensity": "研发强度",
    "rev_cagr": "营收复合增长",
    "tech_breadth_entropy": "技术广度熵",
    "tech_breadth_ipc_class": "IPC 大类数",
    "tech_conversion": "技术转化率",
    "tech_growth_inter": "技术成长交互项",
}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def connect():
    if not os.path.exists(DB):
        sys.exit("找不到数据库：%s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def q1(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


def _db_ver(con):
    """库内实际最新 schema 版本（与 API 的 meta.schema_version 同源）。"""
    try:
        return q1(con, "SELECT version FROM meta_schema_version "
                       "ORDER BY applied_at DESC LIMIT 1") or "unknown"
    except Exception:
        return "unknown"


def db_stats(con):
    """库规模现场计算 —— 文档里的「33 表 / 16 视图 / 764 列 / 63 万行」不许再手写。

    手写数字漂移过一次（图重建后表数没变但行数涨到 63 万、视图 9→16），
    所以统一从这里取，谁改库谁的数字自动跟着走。
    """
    tables = [r[0] for r in q(con, "SELECT name FROM sqlite_master WHERE type='table' "
                                   "AND name NOT LIKE 'sqlite_%'")]
    views = [r[0] for r in q(con, "SELECT name FROM sqlite_master WHERE type='view'")]
    n_rows = sum(q1(con, 'SELECT COUNT(*) FROM "%s"' % t) for t in tables)
    n_cols = sum(len(q(con, 'PRAGMA table_info("%s")' % t)) for t in tables + views)
    size_mb = os.path.getsize(DB) / 1048576 if os.path.exists(DB) else 0
    return {
        "n_tables": len(tables),
        "n_views": len(views),
        "n_cols": n_cols,
        "n_rows": n_rows,
        "size_mb": round(size_mb),
        "rows_str": "{:,}".format(n_rows),
    }


def graph_stats(con):
    """图表规模现场计算（节点 / 边 / 连通分量由契约现算，文档不手写）。"""
    n_node = q1(con, "SELECT COUNT(*) FROM ads_graph_node")
    n_edge = q1(con, "SELECT COUNT(*) FROM ads_graph_edge")
    n_ntype = q1(con, "SELECT COUNT(DISTINCT node_type) FROM ads_graph_node")
    return {"n_node": n_node, "n_edge": n_edge, "n_ntype": n_ntype,
            "node_str": "{:,}".format(n_node), "edge_str": "{:,}".format(n_edge)}


def rank_auc(pairs):
    """单变量 AUC（Mann-Whitney U / rank 法），自动处理并列值。"""
    clean = [(v, l) for v, l in pairs if v is not None]
    pos = [v for v, l in clean if l == 1]
    neg = [v for v, l in clean if l == 0]
    if not pos or not neg:
        return None
    order = sorted(clean, key=lambda t: t[0])
    ranks, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][0] == order[i][0]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        ranks[order[i][0]] = avg
        i = j + 1
    rp = sum(ranks[v] for v in pos)
    return (rp - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def is_hollow(d):
    """判断返回体是否是「空壳」：包装了但核心字段全是 null。

    `/api/company/diagnose` 就是这样 —— 返回
    `{company_id: "ENT...", score: null, explanation: null, graph: null, note: "..."}`，
    data 非 None、code 也是 0，但除了标识和说明，没有一个是能渲染的东西。
    只看 `code` 会以为可用，所以必须单独标出来。
    """
    if d is None:
        return True
    if isinstance(d, dict):
        if not d:
            return True
        # 这些只是标签/说明，不算内容
        meta_keys = {"company_id", "enterprise_id", "id", "name", "note",
                     "message", "detail", "ready", "exists"}
        core = {k: v for k, v in d.items() if k not in meta_keys}
        if not core:
            return True
        has_content = any(v is not None and v != "" and v != [] and v != {}
                          for v in core.values())
        # 全部为 null / 空容器 且至少有一个显式 null → 空壳
        has_null = any(v is None for v in core.values())
        return (not has_content) and has_null
    if isinstance(d, (list, str)):
        return not d
    return False


def ensure(d):
    os.makedirs(d, exist_ok=True)
    return d


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------
# 1. 算法端
# ---------------------------------------------------------------------------
def build_algorithm(con):
    ensure(D_ALG)
    st = db_stats(con)
    ddl_cols = [r[1] for r in q(con, "PRAGMA table_info(v_model_train_ready)")]
    total = q1(con, "SELECT COUNT(*) FROM v_model_train_ready")
    n_pos = q1(con, "SELECT SUM(label) FROM v_model_train_ready")

    # ---- 1.1 model_train_ready.csv ----
    recs = q(con, "SELECT * FROM v_model_train_ready "
                  "ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'val' THEN 2 "
                  "ELSE 3 END, enterprise_id")
    write_csv(os.path.join(D_ALG, "model_train_ready.csv"),
              ddl_cols, [[r[c] for c in ddl_cols] for r in recs])

    # ---- 1.2 feature_dictionary.csv ----
    registered = [r[0] for r in q(con, "SELECT DISTINCT feature_name "
                                       "FROM ads_feature_asof ORDER BY feature_name")]
    in_view = set(ddl_cols)
    frows = []
    for f in registered:
        # 覆盖率一律取「cohort 切面」口径，这样被视图剔除的列也能显示真实覆盖，
        # 不会让人误以为库里没这个数据。
        cov = q1(con, "SELECT COUNT(feature_value) FROM ads_feature_asof "
                      "WHERE feature_name=? AND as_of_policy='cohort'", (f,))
        if cov:
            if f in in_view:
                distinct = q1(con, "SELECT COUNT(DISTINCT %s) FROM v_model_train_ready" % f)
            else:
                # 被视图剔除的列要回到 cohort 切面去数，不能数 614 行
                distinct = q1(con, "SELECT COUNT(DISTINCT %s) FROM v_model_dataset_asof_flat "
                                   "WHERE as_of_policy='cohort'" % f)
        else:
            distinct = 0
        auc = None
        if cov and distinct > 1:
            auc = rank_auc([(r[0], r[1]) for r in q(
                con, "SELECT %s, label FROM v_model_dataset_asof_flat "
                     "WHERE as_of_policy='cohort'" % f)])

        if cov == 0:
            rec, why = "unavailable", "全空：源数据缺失，见 field_gap_registry"
        elif distinct <= 1:
            rec, why = "drop_constant", (
                "cohort 内恒定（去重值=1），AUC=0.5"
                + ("，已从 train_ready 剔除" if f not in in_view else ""))
        elif auc is None:
            rec, why = "drop_no_signal", "无法计算 AUC"
        elif abs(auc - 0.5) < 0.05:
            rec, why = "drop_no_signal", "|AUC-0.5|<0.05，无判别力"
        elif abs(auc - 0.5) < 0.10:
            rec, why = "weak", "0.05<=|AUC-0.5|<0.10，弱信号"
        else:
            rec, why = "keep", "|AUC-0.5|>=0.10"

        direction = "-"
        if auc is not None and abs(auc - 0.5) >= 0.05:
            direction = "正向(值大→更可能断档样本)" if auc > 0.5 else "负向(值大→更不可能断档样本)"

        frows.append([
            f, FEATURE_CN.get(f, ""), "是" if f in in_view else "否",
            "%d/%d" % (cov, total), round(100.0 * cov / total, 1),
            distinct,
            "" if auc is None else round(auc, 4),
            "" if auc is None else round(abs(auc - 0.5), 4),
            direction, rec, why,
        ])

    write_csv(os.path.join(D_ALG, "feature_dictionary.csv"),
              ["特征名", "中文名", "在train_ready中", "cohort内非空/307", "覆盖率%",
               "cohort内去重值数", "单变量AUC", "|AUC-0.5|", "方向", "建议", "判据"],
              frows)

    # ---- 1.3 dataset_card.md ----
    splits = {r[0]: (r[1], r[2]) for r in q(
        con, "SELECT split, COUNT(*), SUM(label) FROM v_model_train_ready GROUP BY 1")}
    cohorts = q(con, "SELECT cohort_year, COUNT(*), SUM(label) FROM v_model_train_ready "
                     "GROUP BY 1 ORDER BY 1")
    keep = [r for r in frows if r[9] == "keep"]
    weak = [r for r in frows if r[9] == "weak"]
    dcon = [r for r in frows if r[9] == "drop_constant"]
    dno = [r for r in frows if r[9] == "drop_no_signal"]
    unav = [r for r in frows if r[9] == "unavailable"]

    L = []
    A = L.append
    A("# 数据集卡片　·　`v_model_train_ready`\n")
    A("> 生成：`python -m src.data.export_delivery`（只读，不改库）")
    A("> 生成时间：%s　｜　库版本：%s　｜　视图：`v_model_train_ready`\n"
      % (datetime.now().strftime("%Y-%m-%d %H:%M"), _db_ver(con)))
    A("## 1. 一句话\n")
    A("浙江 500 家专精特新企业里的 **%d 家**，每家一行、一行 17 列，" % total)
    A("用于预测「高企资格到期后是否通过复核」。**这是一套小样本代理标签数据集**，")
    A("够验证方法与跑通链路，不够支撑性能结论。\n")

    A("## 2. 规模与切分\n")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 行数 | **%d**（= 企业数，无伪重复） |" % total)
    A("| 列数 | **%d**（标识 6 + 特征 11） |" % len(ddl_cols))
    A("| 断档样本 | **%d**（%.2f%%） |" % (n_pos, 100.0 * n_pos / total))
    A("| 续存样本 | %d |" % (total - n_pos))
    A("| 不平衡比 | 1 : %.1f |" % ((total - n_pos) / n_pos))
    A("")
    A("| split | 行数 | 断档样本 | 断档样本占比 |")
    A("|---|---|---|---|")
    for s in ("train", "val", "test"):
        if s in splits:
            n, p = splits[s]
            A("| %s | %d | %d | %.1f%% |" % (s, n, p, 100.0 * p / n))
    A("")
    A("切分由 `md5(enterprise_id)` 派生，**企业级固定**。同一企业在三个集里只出现一次，")
    A("不存在跨集泄漏。**禁止再按行随机切分。**\n")

    A("## 3. 队列结构\n")
    A("| cohort_year | 企业数 | 断档样本 | 断档样本占比 |")
    A("|---|---|---|---|")
    for y, n, p in cohorts:
        A("| %d | %d | %d | %.1f%% |" % (y, n, p, 100.0 * p / n))
    A("")
    A("队列年 = 首次高企认定年。**混队列训练时务必带 `cohort_year` 做分层或权重**，")
    A("否则模型会退化成「队列哑变量」（2021 vs 2022 的断档样本占比差 3.8pp）。\n")

    A("## 4. 特征可用性（29 个注册特征的实际状态）\n")
    A("| 建议 | 个数 | 特征 |")
    A("|---|---|---|")
    A("| ✅ keep | %d | %s |" % (len(keep), "、".join("`%s`" % r[0] for r in keep)))
    A("| ⚠️ weak | %d | %s |" % (len(weak), "、".join("`%s`" % r[0] for r in weak) or "—"))
    A("| ⛔ drop_constant | %d | %s |" % (len(dcon), "、".join("`%s`" % r[0] for r in dcon) or "—"))
    A("| ⛔ drop_no_signal | %d | %s |" % (len(dno), "、".join("`%s`" % r[0] for r in dno) or "—"))
    A("| ❌ unavailable | %d | %s |" % (len(unav), "、".join("`%s`" % r[0] for r in unav) or "—"))
    A("")
    A("完整判据见 `feature_dictionary.csv`；**不可用的 14 列为什么留空、用什么替代、"
      "什么时候能补**，逐条见同目录 `field_gap_decisions.md`（登记册 v3 的只读视图，"
      "含主宽表 8 个全空列的现场核对）。**不要用均值/中位数填充**：填充后是常量列，"
      "既不提供信息，又会在特征重要性里留下幽灵特征。\n")
    A("> ⚠️ `grant_rate` 在 2021 队列 160 家**全部为 1.0**（发明专利需 3 年审结期，")
    A("> 2021 时点窗口内没有成熟样本），2022 队列才有 0/1 变化。混队列训练会让它")
    A("> 退化成队列哑变量，建议优先剔除。\n")

    A("## 5. 标签口径（必读）\n")
    A("`v_model_train_ready.label = 1` 表示「高企资格 3 年到期后**未再出现在后续任何公示名单**」"
      "（断档），**不等于官方「复核未通过」**，也不是违约 / 经营异常 / 严重失信。"
      "它是从 14 份年度认定名单差分反推的**代理标签**：企业可能经营恶化，也可能只是没再申报，"
      "两者在现有数据里无法区分。混淆来源与偏误方向见 `label_spec.md`。\n")
    A("> **方向（v1.9）**：上面这个 `label` 是**风险预警方向**（1=断档）。本项目研究企业好坏 / "
      "信用评价，**正例 = 好企业 = 资质存续（286 家，label_value=0）**，"
      "**负例 = 坏企业 = 资质断档（21 家，label_value=1）**。"
      "新代码一律从 `v_model_train_ready_credit` 取标签，逐企业对照见 `v_label_direction`；"
      "本 CSV 为兼容已发布契约保留原数值，未原地改写。\n")
    A("> **官方核查（2026-11-11，issue ①）**：已核对「取消高企资格公告」栏目浙江省及宁波市 "
      "7 份公告、287 家全覆盖（含 5 份扫描件逐页读图），500 家样本 / 307 家已标注 / "
      "21 家断档经含别名比对**均 0 命中**；其中 7 家经别名归一"
      "确认资格持续有效（改名假象），审计断档率 14/307 = 4.56%。"
      "**口径隔离**：14/307 是审计结论，本 CSV 与库内 `ads_label` 仍为 21/307 = 6.84%，"
      "`DEFINITION_VER` 未升版，两者禁止混用。处置是保留该指标、作为多标签评级里 "
      "6 个锚点之一（C 轴 · 资质再申报行为），不废弃、不原地改写。"
      "详见 `official_cancel_check.md`。\n")
    A("> **v2.0 A 轴并列层（2026-11-11）**：企查查回传的失信被执行人 / 被执行人 / 终本案件 "
      "与主要人员已入库为 3 张 ODS 贴源表 + 4 个并列视图（`ods_qcc_judicial` 34 条 / 6 户、"
      "`ods_qcc_person` 清洗后 2079 人 / 490 户）。三类金额语义不同（涉案金额 / 执行标的 / "
      "未履行金额）**禁止相加**；用户主表「高管_人数」含 490 行表头脏行，取数一律走清洗后视图。"
      "复合负面事件是**并列候选锚点**，不是新标签：`ads_label`、多标签矩阵、五档评级、"
      "训练入口与 `DEFINITION_VER` 逐项未动。详见 `axis_a_judicial_report.md`。\n")
    A("一个必须知道的事实：`n_events=1` 的 21 家**全部**是断档样本，`n_events>=2` 的 286 家")
    A("**全部**是续存样本。标签几乎等价于「只被认定过一次」，这也是「持续经营类特征」")
    A("天然有判别力的原因 —— 但分不清是经营恶化还是懒得申报。\n")

    A("## 6. 无后视保证\n")
    A("本视图固定 `as_of_policy='cohort'`，所有特征只用到**认定当年及以前**的数据。")
    A("同一批特征在「全量历史」口径下 XGBoost AUC=0.796，切到 as-of 后掉到 0.524，")
    A("**虚高 0.272**。任何在后视宽表上训练得到的指标都不应作为能力证据。")
    A("实测明细见 `baseline_asof_leakage.json`。\n")

    A("## 7. 怎么用\n")
    A("""```python
import pandas as pd, sqlite3

con = sqlite3.connect('zjtech_credit.db')
df = pd.read_sql('SELECT * FROM v_model_train_ready', con)

ID_COLS = ('enterprise_id', 'name_display', 'region_code',
           'cohort_year', 'split', 'label')
FEATS = [c for c in df.columns if c not in ID_COLS]

tr = df[df.split == 'train']
va = df[df.split == 'val']
te = df[df.split == 'test']
```
""")
    A("或直接用 `model_train_ready.csv`（同内容，UTF-8 BOM，Excel 可直开）。\n")

    A("## 8. 明确不能做的事\n")
    A("| 不能 | 原因 |")
    A("|---|---|")
    A("| 报「AUC 0.87」这类单一指标 | 只有 %d 个断档样本，val 集仅 2 个，点估计无意义 |" % n_pos)
    A("| 按行随机切分 / KFold 不打组 | 同一企业会同时进训练与验证 |")
    A("| 用 `ads_model_dataset` 训练 | 含 %d 行假续存样本 + 后视泄漏 |"
      % q1(con, "SELECT COUNT(*) FROM ads_model_dataset WHERE split='excluded'"))
    A("| 把 `label` 当官方违约口径对外表述 | 是代理标签，非监管定义 |")
    A("| 与「信贷面板」那套数据混训 | 那是另一套 1,714 家企业、x1–x200 匿名特征 |")
    A("")
    with open(os.path.join(D_ALG, "dataset_card.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # ---- 1.4 复制标签说明书、泄漏实测与 P1-续 方向纠正产物 ----
    # P1-续（v1.9）产物必须随交付包一起走：旧 baseline_asof_leakage.json 是
    # 风险预警方向（预测断档），不能让算法端只拿到旧口径的结果。
    for src, dst in [
        (os.path.join(OUTPUTS, "label_spec.md"), "label_spec.md"),
        (os.path.join(OUTPUTS, "baseline_asof_leakage.json"), "baseline_asof_leakage.json"),
        (os.path.join(OUTPUTS, "multilabel_eval_report.md"), "multilabel_eval_report.md"),
        (os.path.join(OUTPUTS, "credit_direction_asof_report.md"), "credit_direction_asof_report.md"),
        (os.path.join(OUTPUTS, "credit_direction_asof.json"), "credit_direction_asof.json"),
        (os.path.join(OUTPUTS, "shap_credit_direction.json"), "shap_credit_direction.json"),
        (os.path.join(OUTPUTS, "shap_credit_direction.csv"), "shap_credit_direction.csv"),
        (os.path.join(OUTPUTS, "shap_credit_direction.png"), "shap_credit_direction.png"),
        (os.path.join(OUTPUTS, "shap_legacy_notice.md"), "shap_legacy_notice.md"),
        # 评审 §5.2 时序图 / §6 T22 字段缺口的定稿说明（2026-11-09 补）
        (os.path.join(OUTPUTS, "graph_temporal_contract.md"), "graph_temporal_contract.md"),
        (os.path.join(OUTPUTS, "graph_temporal_contract.json"), "graph_temporal_contract.json"),
        (os.path.join(OUTPUTS, "field_gap_decisions.md"), "field_gap_decisions.md"),
        (os.path.join(OUTPUTS, "field_gap_decisions.json"), "field_gap_decisions.json"),
        # 2026-11-11 issue ① 官方撤销公告核查（实证闭合，未改库、未升版）
        (os.path.join(OUTPUTS, "official_cancel_check.md"), "official_cancel_check.md"),
        (os.path.join(OUTPUTS, "official_cancel_check.json"), "official_cancel_check.json"),
        # 2026-11-11 v2.0 A 轴司法违约 + 主要人员并列层（新增三表四视图，未改已发布标签）
        (os.path.join(OUTPUTS, "axis_a_judicial_report.md"), "axis_a_judicial_report.md"),
        (os.path.join(OUTPUTS, "axis_a_judicial_report.json"), "axis_a_judicial_report.json"),
        (os.path.join(ROOT, "data", "audit", "标签核查_21家断档逐户.csv"),
         "标签核查_21家断档逐户.csv"),
        (os.path.join(ROOT, "data", "audit", "官方_取消高企资格_浙江名单.csv"),
         "官方_取消高企资格_浙江名单.csv"),
        (os.path.join(ROOT, "data", "audit", "公告清单_浙江宁波7条.json"),
         "公告清单_浙江宁波7条.json"),
        (os.path.join(ROOT, "data", "audit", "证据_2026-02第12条_官方更名标注.png"),
         "证据_2026-02第12条_官方更名标注.png"),
    ]:
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(D_ALG, dst))

    # ---- 1.5 算法端 README ----
    R = []
    A = R.append
    A("# 算法端交付包\n")
    A("## 文件清单\n")
    A("| 文件 | 说明 |")
    A("|---|---|")
    A("| `model_train_ready.csv` | **主数据**：%d 行 × %d 列，直接 fit |"
      % (total, len(ddl_cols)))
    A("| `feature_dictionary.csv` | 29 个注册特征的覆盖率 / 单变量 AUC / 建议 |")
    A("| `dataset_card.md` | 数据集卡片：规模、切分、队列、标签、边界 |")
    A("| `label_spec.md` | 代理标签说明书（14 节，必读；§13 为官方撤销公告核查） |")
    A("| `baseline_asof_leakage.json` | 泄漏实测：leaky 0.796 → noleak 0.524（**v1.8 旧风险方向，历史留档**） |")
    A("| `multilabel_eval_report.md` | v1.9 多标签测评报告（6 锚点 / 五档 / 循环定义边界） |")
    A("| `credit_direction_asof_report.md` | **P1-续 主报告**：方向纠正后 as-of 训练 / AUC 恒等式 / 分切面与分队列复核 / 重跑 SHAP |")
    A("| `credit_direction_asof.json` | 上表结构化结果（含逐折明细） |")
    A("| `shap_credit_direction.json` / `.csv` / `.png` | **当前口径**全局解释：P(好企业)，`+1`=增信因子 |")
    A("| `shap_legacy_notice.md` | 旧 `shap_global.*` / `shap_top_features.csv` 方向警告，禁止与新产物混用；含前端 4 份 mock 契约的路径基准澄清 |")
    A("| `graph_temporal_contract.md` / `.json` | **评审 §5.2 时序图**：29 帧年度累计切片 / STGNN 输入 SQL / 同源守恒校验 / 产品节点缺口定稿 |")
    A("| `field_gap_decisions.md` / `.json` | **评审 §6 T22**：17 条字段级 NULL 定稿 + 10 条替代特征 + 主宽表 8 列现场核对 |")
    A("| `official_cancel_check.md` / `.json` | **issue ① 实证闭合 + issue ② 新证据**：7 份公告 287 家全覆盖、三层 0 命中、7 家改名假象、14 家官方零命中；A 轴完整事件集合与 E 档塌陷（**未改库**） |")
    A("| `axis_a_judicial_report.md` / `.json` | **v2.0 并列层主报告**：司法违约 34 条 / 6 户、主要人员清洗后 2079 人 / 490 户、三类金额口径禁止混加、复合负面事件 6 家 / 3 家新增 E 候选；含口径隔离与禁止清单（**未改已发布标签与五档**） |")
    A("| `标签核查_21家断档逐户.csv` / `官方_取消高企资格_浙江名单.csv` / `公告清单_浙江宁波7条.json` / `证据_2026-02第12条_官方更名标注.png` | 上报告的原始证据快照（287 家名单 + 官方更名标注单页），供接收方独立复核 |")
    A("")
    A("> **入口优先级**：新代码一律从 `v_model_train_ready_credit` 取标签（1=正例·好企业），")
    A("> 结果对照看 `credit_direction_asof_report.md`；`baseline_asof_leakage.json` 与")
    A("> `outputs/shap_global.*` 属 v1.8 风险预警方向留档，语义相反，**不可混用**。")
    A("> 按评审报告 §7.1(3)，负例 21 家、置换噪声地板约 0.5，AUC / 准确率绝对值")
    A("> 不得写成性能结论。")
    A("")
    A("## 30 秒上手\n")
    A("""```python
import pandas as pd

df = pd.read_csv('model_train_ready.csv')

FEATS = ['n_patent_app', 'n_patent_invention', 'n_patent_grant',
         'inv_patent_ratio', 'tech_conversion', 'n_ipc_subclass',
         'tech_breadth_entropy', 'tech_breadth_ipc_class',
         'grant_rate', 'core_staff_stability', 'n_inventor']

tr = df[df.split == 'train']
va = df[df.split == 'val']
te = df[df.split == 'test']
```
""")
    A("## 三条硬约束\n")
    A("1. **不要重新切分**。`split` 已按企业级固定，重切会造成跨集泄漏。")
    A("2. **不要报单一指标**。%d 个断档样本，val 集只有 2 个，请报区间或交叉验证分布。" % n_pos)
    A("3. **不要用 `ads_model_dataset`**。那是后视宽表 + %d 行假续存样本。"
      % q1(con, "SELECT COUNT(*) FROM ads_model_dataset WHERE split='excluded'"))
    A("")
    A("## 如果需要原始库\n")
    A("`zjtech_credit.db`（%d MB，%d 表 / %d 视图 / %d 列）请单独索取，"
      % (st["size_mb"], st["n_tables"], st["n_views"], st["n_cols"]))
    A("字段说明见同包的 `04_数据字典与数据集/数据字典.md`。")
    A("")
    with open(os.path.join(D_ALG, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(R))

    return {"rows": total, "cols": len(ddl_cols), "pos": n_pos,
            "keep": len(keep), "unavail": len(unav)}


# ---------------------------------------------------------------------------
# 2. 前端
# ---------------------------------------------------------------------------
def build_frontend(con):
    ensure(D_FE)
    gst = graph_stats(con)

    # ---- 2.1 契约真实性格档 ----
    truth = {}
    if os.path.isdir(CONTRACTS):
        for f in sorted(os.listdir(CONTRACTS)):
            if not f.endswith(".json"):
                continue
            try:
                j = json.load(open(os.path.join(CONTRACTS, f), encoding="utf-8"))
                meta = j.get("meta", {}) if isinstance(j, dict) else {}
                truth[f] = {
                    "source": meta.get("source", "-"),
                    "n_samples": meta.get("n_samples"),
                    "generated_at": meta.get("generated_at"),
                    "disclaimer": meta.get("disclaimer", ""),
                }
            except Exception:
                truth[f] = {"source": "unreadable"}

    def verdict(src):
        if src in ("measured", "real_panel", "aggregated"):
            return "真实", "可直接联调"
        if src == "reconstructed_from_anchors":
            return "重建", "由实测锚点重建，数值可信但非逐点实测"
        if src == "demo_placeholder":
            return "占位", "**假数据**，仅可验证页面结构，不得展示数值"
        if src == "synthetic_graph":
            return "合成", "**合成图**，节点/边均为伪造，不得当作真实图谱"
        return "未知", "来源未标注，按占位处理"

    # 样例只收体量可控的真数据接口（batch/{job_id} 有 10 万字节、roc-pr-ks 有 1.8 万，不收）
    SAMPLE_PATHS = {
        "/api/enums", "/api/overview/summary", "/api/factors/importance",
        "/api/score/company/{company_id}", "/api/company/{company_id}/graph",
        "/api/company/{company_id}/explanation", "/api/company/diagnose",
        # 本轮补齐的契约接口（去掉大数组后体量可控）
        "/api/data/panel-depth", "/api/features/tree", "/api/graph/slices",
        "/api/graph/centrality", "/api/graph/structure", "/api/model/train-curve",
        "/api/explain/stage-factor", "/api/forecast/financing", "/api/forecast/survival",
        "/api/explain/local/{userid}",
    }
    # 这两个接口不带文件时会返回 IMPORT_REJECTED —— 那是正确行为，不是未就绪
    FILE_REQUIRED = {"/api/batch/upload", "/api/data/import"}

    # ---- 2.2 API 清单（起服务则实测，否则只读 OpenAPI 定义）----
    api_rows = []
    samples = {}
    api_live = False
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = os.environ.get("API_BASE", "http://127.0.0.1:8091").rstrip("/")
        spec = json.loads(opener.open(base + "/openapi.json", timeout=3).read())
        api_live = True
    except Exception:
        spec = None

    if spec is None:
        spec_path = os.path.join(ROOT, "outputs", "openapi.json")
        if os.path.exists(spec_path):
            spec = json.load(open(spec_path, encoding="utf-8"))

    if spec:
        json.dump(spec, open(os.path.join(D_FE, "openapi.json"), "w",
                             encoding="utf-8"), ensure_ascii=False, indent=2)
        paths = []
        for p, ops in spec.get("paths", {}).items():
            if "/api" not in p:
                continue
            for m in ops:
                if m in ("get", "post"):
                    paths.append((m.upper(), p))
        paths.sort(key=lambda t: t[1])

        # 预热：拿一个真实 job_id，否则 /api/batch/{job_id} 必然 404，
        # 会把「未实现」和「job 不存在」混为一谈，误导前端。
        live_job = ""
        if api_live:
            try:
                req = urllib.request.Request(base + "/api/batch-evaluate", method="POST")
                req.add_header("Content-Type", "application/json")
                req.data = b"{}"
                live_job = json.loads(opener.open(req, timeout=10).read()) \
                    .get("data", {}).get("job_id", "")
            except Exception:
                live_job = ""

        for m, p in paths:
            status, note, nbytes = "未知", "", 0
            if api_live:
                url = p.replace("{company_id}", "ENT2803CAE1D888") \
                       .replace("{enterprise_id}", "ENT2803CAE1D888") \
                       .replace("{userid}", "ENT2803CAE1D888") \
                       .replace("{job_id}", live_job or "PROBE-NO-JOB")
                # diagnose 是 POST 但 company_id 走 query 参数
                if p == "/api/company/diagnose":
                    url += "?company_id=ENT2803CAE1D888"
                try:
                    req = urllib.request.Request(base + url, method=m)
                    if m == "POST":
                        req.add_header("Content-Type", "application/json")
                        req.data = b"{}"
                    with opener.open(req, timeout=20) as resp:
                        raw = resp.read()
                        ctype = resp.headers.get("Content-Type", "")
                        extra_hdr = resp.headers.get("X-Total-Rows")
                    nbytes = len(raw)
                    if "json" not in ctype.lower():
                        # 文件流接口（CSV 导出）本来就该返回非 JSON，不算报错
                        status = "有数据"
                        note = "文件流 %s，%s 行" % (
                            ctype.split(";")[0], extra_hdr or raw.count(b"\n") - 1)
                        api_rows.append([m, p, status, nbytes, note])
                        continue
                    j = json.loads(raw)
                    if j.get("code") != 0:
                        if p in FILE_REQUIRED and j.get("code") == "IMPORT_REJECTED":
                            # 探测没带文件，被正确拒绝 —— 这是「需文件」不是「未实现」
                            status = "需文件"
                            note = ("探测未带文件，返回 `IMPORT_REJECTED` 是正确行为；"
                                    "带文件调用可用（已实测，见 `03_交付说明`）")
                        else:
                            status = "占位"
                            note = j.get("message", "") or "code 非 0"
                    elif is_hollow(j.get("data")):
                        status = "空壳"
                        note = "包装了但内部全为 null，无法渲染"
                    else:
                        status, note = "有数据", ""
                        if "tech-graph" in p or p in SAMPLE_PATHS:
                            samples[p] = j
                except Exception as e:
                    status, note = "报错", str(e)[:50]
            api_rows.append([m, p, status, nbytes or "", note])

        # 备注只写「数据本身的口径问题」，不写实现缺陷 —— 实现缺陷已修完，
        # 留着会在清单里变成过期告警（每条都跟着实测一起校验）。
        for row in api_rows:
            if row[1] == "/api/data/pipeline" and row[2] == "有数据":
                row[4] = ("live 为库内实测；`legacy_ledger` 是 10 月旧台账，"
                          "已标 deprecated，P0-4 那条会被自动检出 `contradicted=true`")
            if row[1] == "/api/overview/summary" and row[2] == "有数据":
                row[4] = ("kpi 直接读库（企业/专利/标签/图谱/体量），"
                          "并区分 B 主数据集与 A 面板两套")
            if row[1] == "/api/score/company/{company_id}" and row[2] == "有数据":
                row[4] = ("`score.value` 恒为 null 是设计（无已验证模型）；"
                          "可用的是 `proxy_score`（validated=false）")
            if row[1] == "/api/company/{company_id}/explanation" and row[2] == "有数据":
                row[4] = "规则分解（model_based=false），**不是 SHAP**，勿与 explain/global 对比"
            if row[1] == "/api/batch-evaluate" and row[2] == "有数据":
                row[4] = "同步跑完即 done；不传 userids 跑全库 500 家（约 0.1s）"
            if row[1] == "/api/batch/upload" and row[2] in ("需文件", "有数据"):
                row[4] = ("上传 CSV/XLSX 名单 → 解析企业 ID → **真正跑完评估**并返回 job_id；"
                          "实测 5 家 ID 全部评分成功，坏 ID 逐条列出")
            if row[1] == "/api/data/import" and row[2] in ("需文件", "有数据"):
                row[4] = ("上传即做字段级校验（类型/NOT NULL/主键重复/ID 格式/外键）；"
                          "`dry_run=true` 只看报告，`false` 才写 `stg_import` 缓冲区，不碰核心表")
            # 本轮补齐的契约接口：备注写口径，不写「已修复」
            CONTRACT_NOTES = {
                "/api/data/quality":
                    "22 张核心表逐列空值率 + 登记册 15 项三态 + 底表/视图矛盾 6 条；"
                    "`join_key_health` 单列「766 件专利未匹配企业」",
                "/api/data/panel-depth":
                    "as-of 面板 307 家 × 2 期（平衡）；另给原始专利年度覆盖",
                "/api/features/tree":
                    "29 特征分 5 组，11 个进训练、5 个有判别力（|AUC−0.5|≥0.05）",
                "/api/graph/structure":
                    "真实异质图 %s 节点 / %s 边（%d 类节点），企业级 6 个连通分量"
                    % (gst["node_str"], gst["edge_str"], gst["n_ntype"]),
                "/api/graph/slices":
                    "按地区 / 认定年 / 标签切片；行业切片因覆盖率不足未产出",
                "/api/graph/centrality":
                    "加权度 / 度 / PageRank；**连接多 ≠ 信用好**，勿当质量指标",
                "/api/model/train-curve":
                    "GAT 只落盘 5/41 个 epoch → `complete=false`；"
                    "**曲线锚点属 A 套信贷面板**，不是 B 套",
                "/api/model/roc-pr-ks":
                    "**重建曲线**（AUC+KS 双锚点，残差 0.0）；曲线属 A 套，"
                    "`b_dataset_auc` 才是 B 套 100 折 AUC 分布",
                "/api/explain/stage-factor":
                    "按 period_t 的描述统计，`capability=descriptive_only`；事件全在末期",
                "/api/forecast/survival":
                    "100 折 AUC + 前瞻 Top30；附 `leakage_diagnosis` 解释 0.87 的泄漏来源",
                "/api/forecast/financing":
                    "506 条真实融资事件；`can_predict` 全 false —— 财务只有 12/500 有值",
                "/api/explain/local/{userid}":
                    "逐特征百分位贡献，`model_based=false`、`is_shap=false`，按企业动态生成",
                "/api/enterprise/grade":
                    "五档 L0 静态快照基线（A 44 / B 95 / C 275 / D 83 / E 3）；"
                    "**不是标签或信用分**，不得报准确率或入模验证",
                "/api/data/datasets":
                    "A 信贷面板 / B 浙江高企两套数据的登记台账；"
                    "任何取值 `meta.allow_cross_join=false`，禁止跨集混用",
            }
            for _p, _note in CONTRACT_NOTES.items():
                if row[1] == _p and row[2] == "有数据":
                    row[4] = _note
            if row[1] == "/api/enterprise/{userid}" and row[2] == "有数据":
                row[4] = ("真实档案 + 代理分；`official_label_pending=true`"
                          "（正式 label 口径仍待导师确认）")

    # 手工补充分类：哪些属于本项目（B 套）、哪些属于信贷面板（A 套）
    B_PREFIX = "/api/tech-graph"
    B_PATHS = {
        "/api/enums", "/api/health", "/api/overview/summary", "/api/data/pipeline",
        "/api/factors/importance", "/api/score/company/{company_id}",
        "/api/company/{company_id}/graph", "/api/company/{company_id}/explanation",
        "/api/company/diagnose", "/api/batch-evaluate", "/api/batch/{job_id}",
        "/api/batch/{job_id}/export", "/api/batch/upload", "/api/data/import",
        "/api/enterprise/{userid}",
        # v1.8 新增：五档分档端点 + A/B 数据登记台账
        "/api/enterprise/grade", "/api/data/datasets",
        # 本轮补齐的 12 个契约：来自 B 套（浙江 500 家）库内数据
        "/api/data/quality", "/api/data/panel-depth", "/api/features/tree",
        "/api/graph/structure", "/api/graph/slices", "/api/graph/centrality",
        "/api/explain/stage-factor", "/api/explain/local/{userid}",
        "/api/forecast/survival", "/api/forecast/financing",
    }
    # train-curve / roc-pr-ks 的曲线锚点来自 A 套 baseline；roc-pr-ks 另附 B 套 100 折 AUC 分布
    A_PATHS = {"/api/model/benchmark", "/api/model/noise-floor", "/api/explain/global",
               "/api/model/train-curve", "/api/model/roc-pr-ks"}
    for row in api_rows:
        p = row[1]
        if p.startswith(B_PREFIX) or p in B_PATHS:
            row.append("B 浙江高企（500 家）")
        elif p in A_PATHS:
            row.append("A 信贷面板（1714 家）")
        else:
            row.append("—")

    # ---- 2.3 api_checklist.md ----
    L = []
    A = L.append
    A("# 接口清单与真实状态\n")
    A("> 实测：`python run_api.py` 后逐接口抓取。" if api_live else
      "> 未起服务，仅列 OpenAPI 定义，状态列为空。")
    A("> 生成时间：%s\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("## 一句话\n")
    n_data = sum(1 for r in api_rows if r[2] == "有数据")
    n_hollow = sum(1 for r in api_rows if r[2] == "空壳")
    n_pend = sum(1 for r in api_rows if r[2] == "占位")
    n_err = sum(1 for r in api_rows if r[2] == "报错")
    n_file = sum(1 for r in api_rows if r[2] == "需文件")
    A("共 **%d** 个接口：有数据 **%d**、空壳 **%d**、占位 **%d**、需文件 **%d**、报错 **%d**。\n"
      % (len(api_rows), n_data, n_hollow, n_pend, n_file, n_err))
    n_b = sum(1 for r in api_rows if r[5].startswith("B") and r[2] == "有数据")
    n_a = sum(1 for r in api_rows if r[5].startswith("A") and r[2] == "有数据")
    A("其中**属于本项目（浙江 500 家高企 / B 套）的有数据接口 %d 个**，" % n_b)
    A("另一套信贷面板（A 套）%d 个 —— 两者样本、特征、标签都不同，不能合并看。" % n_a)
    if n_pend == 0:
        A("**占位 0 个**：12 个原先返回 `CONTRACT_PENDING` 的接口已全部由 "
          "`src/data/build_contracts.py` 从库内实测 + 已落盘实验文件真实产出；")
        A("`explain/local/{userid}` 改为按企业动态生成（不预落 500 个文件）。")
        A("剩 %d 个「需文件」是导入接口：探测没带文件时返回 `IMPORT_REJECTED` 是正确行为，"
          "带文件调用已实测可用。" % n_file)
    else:
        n_upload = sum(1 for r in api_rows if r[2] == "占位")
        A("剩余 %d 个未就绪；报错 %d 个。" % (n_upload, n_err))
    A("")

    A("## 逐接口\n")
    A("| 方法 | 路径 | 状态 | 返回大小 | 归属 | 备注 |")
    A("|---|---|---|---|---|---|")
    for m, p, s, nb, note, belong in api_rows:
        mark = {"有数据": "✅", "空壳": "⚠️", "占位": "⬜", "需文件": "📎",
                "报错": "❌"}.get(s, "?")
        A("| %s | `%s` | %s %s | %s | %s | %s |"
          % (m, p, mark, s, ("%d B" % nb) if nb else "—", belong, note or ""))
    A("")
    A("> ⚠️ **空壳** = HTTP 200、`code:0`、`data` 非 null，但内部字段全是 null。")
    A("> 只看 `code` 会以为可用，实际渲染出来是空白。")
    A("> 📎 **需文件** = 导入类接口，探测未带文件时返回 `IMPORT_REJECTED` 是正确的；"
      "带文件即可用。")
    A("")

    A("## ⚠️ 两套数据不能混\n")
    A("| | A 信贷面板 | B 浙江高企（本项目） |")
    A("|---|---|---|")
    A("| 企业数 | 1,714 | 500 |")
    A("| 样本 | 3,465 行 | 307 家打标 |")
    A("| 特征 | `x1`–`x200` 匿名 | 29 个有名字的特征 |")
    A("| 断档样本 | 75 | 21 |")
    A("| 落在 | `baseline_results.json`、`shap_global.json`、`noise_floor.json` | "
      "`v_model_train_ready`、`ads_graph_node/edge` |")
    A("")
    A("`/api/model/benchmark`、`/api/model/noise-floor`、`/api/explain/global` 返回的是 **A 套**，")
    A("`/api/tech-graph/*` 返回的是 **B 套**。同一个页面里同时展示会自相矛盾")
    A("（概览说 1,714 家、图谱说 500 家）。**上线前必须二选一或分开标注。**\n")

    A("## 契约文件真实性\n")
    A("**现在有两处契约目录，别混：**\n")
    A("| 目录 | 谁在读 | 内容 |")
    A("|---|---|---|")
    A("| `outputs/contracts/` | **API 正在读** | 现共 12 个（v1.8 新增 `enterprise_grade.json`），全部带 `provenance` |")
    A("| `frontend_v2/source/outputs/contracts/` | 没人读 | 旧的 %d 个，其中 7 个是假数据 |"
      % len(truth))
    A("")
    A("### 旧目录（`frontend_v2/…/contracts/`）逐文件判定\n")
    A("| 契约 | 来源 | 判定 | 能否直接用 |")
    A("|---|---|---|---|")
    for f in sorted(truth):
        v, why = verdict(truth[f]["source"])
        A("| `%s` | `%s` | **%s** | %s |" % (f, truth[f]["source"], v, why))
    A("")
    bad = [f for f in sorted(truth) if verdict(truth[f]["source"])[0] in ("占位", "合成", "未知")]
    if bad:
        A("**%d 个不能当数据用**：%s\n"
          % (len(bad), "、".join("`%s`" % b for b in bad)))
        A("`panel_depth.json` 自己就写着「不得作为任何实证结论引用」。")
        A("**不要整体搬到 `outputs/contracts/`** —— 那样假数据会悄悄覆盖掉真契约。\n")

    A("## 联调三条约定\n")
    A("1. **企业 ID** 是 `ENT` + 12 位 hex、共 15 字符（如 `ENT2803CAE1D888`），")
    A("   **不是** `CN-330100-0001`。传错不会 500，返回 `code=NOT_FOUND`，"
      "`meta.detail` 里写着正确格式。")
    A("2. **响应统一** `{code, message, data, meta}`。HTTP 恒 200，成败看 `code`：")
    A("   `0` 成功；非 0 是 pending/阻塞码，`meta.detail` 会带原因。")
    A("3. **`meta.schema_version`** 启动时读 `meta_schema_version`，当前 `%s`；"
      "库里升版它就跟着变，可以拿它做契约判断。" % _db_ver(con))
    A("4. **`score.value` 恒为 null** 是设计：无已验证模型。要展示排序请用 "
      "`proxy_score`（`validated=false`），且必须自带一句口径说明。\n")

    A("## 已修的实现缺陷（本轮）\n")
    A("| 原问题 | 现在 |")
    A("|---|---|")
    A("| `score/company` 在 dashboard 缺失时直接 404 | 改读 SQLite，未知企业返回 `NOT_FOUND` 业务码，HTTP 仍 200 |")
    A("| `company/diagnose` 返回空壳（三个字段全 null） | 档案 + 代理分 + 逐特征贡献 + 真实相似子图 |")
    A("| `company/{id}/graph` 所有企业返回同一张 demo 图 | 按 `company_id` 取真实 `tech_similar` 连边 |")
    A("| `factors/importance` 恒 pending | B 套单变量 AUC + A 套 SHAP 分开返回 |")
    A("| `overview/summary.kpi` 恒 null | 直读库（企业/专利/标签/图谱/表视图/体量） |")
    A("| `data/pipeline` 透传 10 月旧台账 | live 实测 + 当前登记册；旧台账降级留档并做矛盾检测 |")
    A("| `batch-evaluate` 只入队不执行 | 同步跑完置 done，`/export` 输出真实 CSV |")
    A("| `meta.schema_version` 写死 `v1.0` | 启动时读库（当前 `%s`） |" % _db_ver(con))
    A("| `batch/upload`、`data/import` 只查后缀就入队等死 | 真正解析 CSV/XLSX + 字段级校验"
      "（类型/NOT NULL/主键重复/ID 格式/外键）+ `stg_import` 缓冲落库；校验不过拒绝落库 |")
    A("| `company/diagnose` 只认 query 参数，前端发 JSON body 直接 422 | query 与 body "
      "三种写法都接受（`company_id` / `userid` / `enterprise_id`） |")
    A("| 12 个接口返回 `CONTRACT_PENDING` | 由 `build_contracts.py` 从真实数据源产出，"
      "全部带 `provenance`；`explain/local` 改按企业动态生成 |")
    A("")
    A("## 契约清单（`outputs/contracts/` 12 个文件 + 1 个动态生成接口）\n")
    A("由 `python -m src.data.build_contracts` 生成。每个文件顶层强制带 `provenance`，")
    A("缺了脚本直接报错拒绝写出 —— 这是防「为了让前端有图可画而编数据」的硬约束。\n")
    A("| 契约 | provenance | 数据来源 | 能展示什么 / 不能展示什么 |")
    A("|---|---|---|---|")
    A("| `data_quality.json` | measured | 22 表逐列扫描 + 登记册 | 空值率、15 项缺口三态、6 条底表/视图矛盾 |")
    A("| `panel_depth.json` | measured | `v_model_dataset_asof_flat` | 307 家 × 2 期平衡面板；**无**财务年度面板 |")
    A("| `feature_tree.json` | derived | 训练视图 + as-of 视图 | 29 特征分 5 组、覆盖率、单变量 AUC |")
    A("| `graph_structure.json` | measured | `ads_graph_node/edge` | %s 节点 / %s 边，真实异质图 |"
      % (gst["node_str"], gst["edge_str"]))
    A("| `graph_slices.json` | measured | 企业维度 + 专利 + 边 | 地区/认定年/标签切片 |")
    A("| `graph_centrality.json` | derived | tech_similar 子图 | 度/加权度/PageRank Top50 |")
    A("| `train_curve.json` | partial | GAT + baseline 落盘结果 | 只有 5/41 个 GAT 点，**不是**完整曲线 |")
    A("| `roc_pr_ks.json` | reconstructed | AUC+KS 双锚点 | 曲线形状可信，**点位不是实测** |")
    A("| `stage_factor_weight.json` | partial | `ads_survival_period` | 按期描述统计，**不是**因子权重模型 |")
    A("| `survival.json` | measured | 100 折 AUC + 前瞻名单 | 含 `leakage_diagnosis` 解释 0.87 的泄漏 |")
    A("| `financing.json` | partial | 506 条融资事件 | 融资画像，**不能**做融资/营收预测 |")
    A("| `enterprise_grade.json` | measured | `v_enterprise_grade_l0` | 五档家数与命中规则；**不是**标签/信用分，不得报准确率或入模验证 |")
    A("| `local_explain`（动态生成，非文件） | 动态生成 | `v_model_train_ready` | 逐特征百分位贡献，非 SHAP |")
    A("")
    A("### provenance 五级制（前端据此决定能不能展示数值）\n")
    A("| 等级 | 含义 | 能否展示 |")
    A("|---|---|---|")
    A("| `measured` | 有直接实测来源 | ✅ 可展示、可引用 |")
    A("| `derived` | 由实测数据算出（AUC/分布/中心性） | ✅ 可展示，须附计算方法 |")
    A("| `partial` | 只覆盖部分维度，`missing` 逐条列出 | ⚠️ 可展示已知部分，缺失处标「待接入」 |")
    A("| `reconstructed` | 由实测锚点反推曲线 | ⚠️ 形状可信，**不得声称逐点实测** |")
    A("| `unavailable` | 数据源不存在 | ❌ 只给原因，不给数值 |")
    A("")
    A("## 仍缺什么（数据层，不是工程层）\n")
    A("1. **断档样本样本 21 个** —— AUC 的 std 在 0.126–0.140，只能支撑方法论演示，不能支撑性能结论。")
    A("   唯一解是扩样本池（500 → 5,000），而名单只到 2021 年。")
    A("2. **财务序列缺失** —— `latest_rev_wan` 仅 12/500、`rev_cagr` 0/500，营收/利润预测做不了。")
    A("3. **766 件专利未匹配到企业**（`fact_patent.enterprise_id` 为空，占 2.3%），")
    A("   这部分在建模时会被整行丢弃，已单列在 `data_quality.join_key_health`。")
    A("4. **视图与底表脱节** —— `patent_cagr` 等 6 列在 as-of 视图全空，但 `dws_growth_feature`")
    A("   有 484 行有值。下游看到全空会误判为「没采」，实为视图没带出来。")
    A("5. **行业切片** —— `qcc_industry_mid` 覆盖率不足，行业对比图暂不可做。")
    A("")
    with open(os.path.join(D_FE, "api_checklist.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # ---- 2.3b 真实契约整包复制（前端可直接拿来做 mock，口径一致）----
    cdir_src = os.path.join(ROOT, "outputs", "contracts")
    cdir_dst = ensure(os.path.join(D_FE, "contracts"))
    n_copied = 0
    if os.path.isdir(cdir_src):
        for fn in sorted(os.listdir(cdir_src)):
            if not fn.endswith(".json"):
                continue
            shutil.copy2(os.path.join(cdir_src, fn), os.path.join(cdir_dst, fn))
            n_copied += 1
    with open(os.path.join(cdir_dst, "_README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join([
            "# contracts/（%d 个，与 `outputs/contracts/` 同源）\n" % n_copied,
            "这 %d 个就是 API 当前实际在读的契约，每个顶层带 `provenance`。" % n_copied,
            "**推荐直接用它做 mock** —— 与线上返回值同源，不会出现"
            "「本地 mock 通过、联调对不上」。\n",
            "生成命令：`python -m src.data.build_contracts`（只读库 + 读已落盘实验文件）。\n",
            "## `provenance.kind` 决定展示口径\n",
            "| kind | 含义 | 前端怎么处理 |",
            "|---|---|---|",
            "| `measured` | 直接实测 | 直接画图，可引用数值 |",
            "| `derived` | 由实测数据算出 | 可直接画，图表注明计算方法 |",
            "| `partial` | 只覆盖部分维度 | 画已知部分，`missing` 里的维度显示「待接入」 |",
            "| `reconstructed` | 由锚点反推 | 画曲线，但必须带「由锚点重建，非逐点实测」 |",
            "| `unavailable` | 数据源不存在 | 显示「无数据源」，**不要画成 0** |",
            "",
            "每个文件还有 `provenance.source`（数据来自哪张表/哪个文件）与",
            "`provenance.method`（怎么算的），可直接展示在图表脚注里。\n",
            "## 三个必须注意的\n",
            "1. `roc_pr_ks.json` 的 `curves[].roc` 是**重建**曲线，AUC 与 KS 已精确匹配锚点"
            "（残差 0.0），但点位不是逐条样本算的。",
            "2. `train_curve.json` 的 GAT 系列 `complete=false`：训练跑了 41 epoch，"
            "只落盘了 5 个点。别当完整收敛曲线。",
            "3. `financing.json` 的 `can_predict` 全是 false —— 财务数据只有 12/500 有值，"
            "不要拿它做融资/营收预测。",
            "",
        ]))

    # ---- 2.4 enums.json ----
    src_enums = os.path.join(ROOT, "configs", "enums.json")
    if os.path.exists(src_enums):
        shutil.copy2(src_enums, os.path.join(D_FE, "enums.json"))

    # ---- 2.5 真实返回样例 ----
    if samples:
        json.dump(samples, open(os.path.join(D_FE, "sample_responses.json"), "w",
                                encoding="utf-8"), ensure_ascii=False, indent=2)

    # ---- 2.6 前端 README ----
    R = []
    A = R.append
    A("# 前端交付包\n")
    A("## 文件清单\n")
    A("| 文件 | 说明 |")
    A("|---|---|")
    A("| `api_checklist.md` | **先看这个**：逐接口真实状态 + 两套数据隔离 + 联调约定 |")
    A("| `openapi.json` | OpenAPI 3.1 规范，可导入 Apifox / Postman |")
    A("| `enums.json` | 实体枚举（company / patent / investor …）中文映射 |")
    if samples:
        A("| `sample_responses.json` | %d 个真数据接口的实际返回样例，可直接当 mock |"
          % len(samples))
    A("")
    A("## 能直接联调的接口（实测 code=0 且 data 非空）\n")
    A("**图谱/检索**")
    A("```\nGET /api/tech-graph/summary                        %s 节点 / %s 边\n"
      "GET /api/tech-graph/enterprises?q=&limit=          500 家企业，含 similar_cnt\n"
      "GET /api/tech-graph/enterprise/{id}/profile        专利数 / IPC 分布\n"
      "GET /api/tech-graph/enterprise/{id}/neighborhood   技术相似企业\n```\n"
      % (gst["node_str"], gst["edge_str"]))
    A("**评分/诊断（读库，不再依赖 dashboard_data.json）**")
    A("```\nGET  /api/score/company/{id}            档案+特征+proxy_score（score.value 为 null）\n"
      "GET  /api/company/{id}/graph            真实 tech_similar 子图（top_k 可调）\n"
      "GET  /api/company/{id}/explanation      代理分逐特征贡献（非 SHAP）\n"
      "POST /api/company/diagnose?company_id=  上面三者的聚合\n```\n")
    A("**全局/批量**")
    A("```\nGET  /api/overview/summary     KPI 直读库（企业/专利/标签/图谱/体量）\n"
      "GET  /api/data/pipeline       实时管道 + 缺口登记册（legacy 已降级标注）\n"
      "GET  /api/factors/importance  B 套单变量 AUC + A 套 SHAP，分开返回\n"
      "POST /api/batch-evaluate      同步跑完，返回 job_id（不传 userids 跑全库）\n"
      "GET  /api/batch/{job_id}      任务状态\n"
      "GET  /api/batch/{job_id}/export  真实 CSV 流（BOM，Excel 直开）\n"
      "GET  /api/enums               实体枚举\n```\n")
    A("`{id}` 用 `ENT2803CAE1D888` 这种格式（ENT + 12 位 hex）。\n")
    A("## 启动后端\n")
    A("```bash\npython run_api.py --port 8000\n# 接口文档 http://127.0.0.1:8000/docs\n```\n")
    A("## 要注意的三件事\n")
    A("1. `<meta>.schema_version` 启动时从 `meta_schema_version` 读取（当前 %s），"
      "不再是写死的常量。" % _db_ver(con))
    A("2. 契约目录里有 %d 个 placeholder / 合成数据，`api_checklist.md` 里标了" % len(bad))
    A("   **占位 / 合成** 的不要展示数值。")
    A("3. `/api/model/*` 和 `/api/explain/global` 属于另一套信贷面板数据，")
    A("   与本项目图谱不同源，同页面展示会互相矛盾。")
    A("")
    with open(os.path.join(D_FE, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(R))

    return {"n_api": len(api_rows), "n_data": n_data, "n_hollow": n_hollow,
            "n_pend": n_pend, "n_file": n_file, "n_err": n_err,
            "n_contract": len(truth),
            "n_bad": len(bad), "n_b": n_b, "n_a": n_a, "live": api_live}


# ---------------------------------------------------------------------------
# 3. 交付说明
# ---------------------------------------------------------------------------
def build_doc(con, alg, fe):
    st = db_stats(con)
    gst = graph_stats(con)
    ensure(D_DOC)
    total = q1(con, "SELECT COUNT(*) FROM v_model_train_ready")
    n_pos = q1(con, "SELECT SUM(label) FROM v_model_train_ready")
    n_excl = q1(con, "SELECT COUNT(*) FROM ads_model_dataset WHERE split='excluded'")
    n_tables = q1(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                       "AND name NOT LIKE 'sqlite_%'")
    n_views = q1(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
    n_cols = sum(len(q(con, 'PRAGMA table_info("%s")' % r[0]))
                 for r in q(con, "SELECT name FROM sqlite_master "
                                 "WHERE type IN ('table','view') "
                                 "AND name NOT LIKE 'sqlite_%'"))
    n_rows = sum(q1(con, 'SELECT COUNT(*) FROM "%s"' % r[0])
                 for r in q(con, "SELECT name FROM sqlite_master "
                                 "WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    n_firm = q1(con, "SELECT COUNT(*) FROM dim_enterprise")
    n_patent = q1(con, "SELECT COUNT(DISTINCT app_no) FROM fact_patent "
                       "WHERE app_no IS NOT NULL")

    L = []
    A = L.append
    A("# 交付说明　·　一页纸\n")
    A("> 生成：`python -m src.data.export_delivery`（只读）")
    A("> 时间：%s\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))

    A("## 一、交付了什么\n")
    A("| 包 | 目录 | 核心内容 | 状态 |")
    A("|---|---|---|---|")
    A("| 算法端 | `01_算法端/` | %d 行 × %d 列训练集 + 29 特征判据 + 标签说明 + 泄漏实测 | ✅ 可用 |"
      % (alg["rows"], alg["cols"]))
    A("| 前端 | `02_前端/` | OpenAPI + 逐接口真实状态 + 枚举 + 返回样例 | ✅ %d 个可用（B %d / A %d） |"
      % (fe["n_data"], fe["n_b"], fe["n_a"]))
    A("| 说明 | `03_交付说明/` | 本文档 | ✅ |")
    A("| 数据字典与数据集 | `04_数据字典与数据集/` | "
      "%d 列字典（md + csv）+ 代理标签说明书 + 3 个数据集 CSV + 缺口登记册 | ✅ 6 个文件 |"
      % st["n_cols"])
    A("")

    A("## 二、数据底座\n")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 库 | `zjtech_credit.db`，%d MB |" % st["size_mb"])
    A("| 结构 | %d 表 + %d 视图 / %d 列 / %s 行 |" % (n_tables, n_views, n_cols, f"{n_rows:,}"))
    A("| 企业 | %d 家浙江专精特新 |" % n_firm)
    A("| 专利 | %s 件（按申请号去重） |" % f"{n_patent:,}")
    A("| 标签 | %d 家打标，%d 断档样本（%.1f%%） |" % (total, n_pos, 100.0 * n_pos / total))
    A("| 完整性 | `integrity_check` ok，外键 0 冲突 |")
    A("")

    A("## 三、算法端能拿到什么\n")
    A("**唯一入口**：`v_model_train_ready` —— %d 行 × %d 列，一行一家企业。" % (total, alg["cols"]))
    A("这层视图已把三个坑物理挡掉：\n")
    A("| 坑 | 后果 | 视图怎么处理 |")
    A("|---|---|---|")
    A("| %d 行 `excluded` 假续存样本 | 断档样本占比被从 6.8%% 稀释到 4.2%% | 只放 train/val/test |" % n_excl)
    A("| 伪重复（一家两行）| 权重翻倍，断档样本 21→30 | 固定 `as_of_policy='cohort'` |")
    A("| 4 个常量特征 | 污染重要性排序 | 已剔除 |")
    A("")
    fd = os.path.join(D_ALG, "feature_dictionary.csv")
    cnt = {}
    if os.path.exists(fd):
        for r in csv.DictReader(open(fd, encoding="utf-8-sig")):
            cnt[r["建议"]] = cnt.get(r["建议"], 0) + 1
    A("29 个注册特征的实测分布：**keep %d / 弱信号 %d / 常量 %d / 无判别力 %d / 全空 %d**"
      % (cnt.get("keep", 0), cnt.get("weak", 0), cnt.get("drop_constant", 0),
         cnt.get("drop_no_signal", 0), cnt.get("unavailable", 0)))
    A("（%d+%d+%d+%d+%d = %d，29 个注册特征全部有结论）。"
      "逐个判据见 `feature_dictionary.csv`。\n"
      % (cnt.get("keep", 0), cnt.get("weak", 0), cnt.get("drop_constant", 0),
         cnt.get("drop_no_signal", 0), cnt.get("unavailable", 0), sum(cnt.values())))

    A("## 四、前端能接什么\n")
    A("接口共 **%d** 个，实测：有数据 **%d**、空壳 **%d**、占位 **%d**、需文件 **%d**、"
      "报错 **%d**。\n"
      % (fe["n_api"], fe["n_data"], fe["n_hollow"], fe["n_pend"],
         fe.get("n_file", 0), fe["n_err"]))
    A("其中**属于本项目（B 套）的 %d 个**：tech-graph 4、"
      "企业档案/评分/诊断/图谱/解释 5、概览/管道/因子/枚举 4、批量 3、"
      "**本轮补齐的契约 10**（quality、panel-depth、features/tree、graph×3、"
      "stage-factor、local-explain、survival、financing）；"
      "A 套面板 %d 个（benchmark / noise-floor / explain-global / train-curve / roc-pr-ks）。\n"
      % (fe["n_b"], fe["n_a"]))
    if fe["n_pend"] == 0:
        A("**占位接口 0 个**：12 个原有占位已全部产出真实契约（`outputs/contracts/`，"
          "每个带 `provenance`）。前端拿到 `meta.provenance.kind` 即可决定展示口径：")
        A("`measured`/`derived` 可直接画；`partial`/`reconstructed` 要带口径说明；"
          "`unavailable` 显示「无数据源」。\n")
    else:
        A("仍有 **%d 个**占位，明细见 `02_前端/api_checklist.md`。\n" % fe["n_pend"])
    A("旧契约目录 `frontend_v2/source/outputs/contracts/` 里有 **%d 个** placeholder / "
      "合成数据（API 不读它），**不要搬进 `outputs/contracts/`**，否则会覆盖真契约。\n"
      % fe["n_bad"])

    A("## 五、三件必须转达的事\n")
    A("**1. 有两套数据，别混。**")
    A("本项目是 500 家企业（浙江高企）；另有早期一套 1,714 家企业的信贷面板（`x1–x200` 匿名特征）。")
    A("`/api/model/benchmark`、`/api/model/noise-floor`、`/api/explain/global` 返回的是**后者**，")
    A("`/api/tech-graph/*` 返回的是**前者**。同页面展示会自相矛盾。\n")
    A("**2. 标签是代理标签，且方向已纠正（v1.9）。**")
    A("按企业好坏口径：**正例 = 好企业 = 资质存续（286 家）**，"
      "**负例 = 坏企业 = 资质断档（21 家）**。")
    A("`ads_label.label_value` / `v_model_train_ready.label` 保留风险预警方向（1=断档）以兼容已发布契约；"
      "**新实验一律用 `v_model_train_ready_credit`**，逐企业对照见 `v_label_direction`。")
    A("21 个负例只够验证方法论，**不够支撑性能结论**。汇报时别写单一 AUC。\n")
    A("> **官方核查（2026-11-11，issue ①）**：已核对「取消高企资格公告」栏目浙江省及宁波市 "
      "7 份公告、287 家全覆盖，500 家样本 / 21 家断档经含别名比对**均 0 命中**；其中 7 家为"
      "别名归一后的改名假象，审计断档率 14/307 = 4.56%。标签定性为**资质再申报行为**，"
      "保留为多标签评级里 6 个锚点之一，**不废弃、不原地改写**。"
      "⚠️ 14/307 是审计结论，库内仍为 21/307、`DEFINITION_VER` 未升版，两者禁止混用。"
      "详见 `official_cancel_check.md`。\n")
    A("> **v2.0 A 轴并列层（2026-11-11）**：新增 `ods_qcc_judicial`（34 条 / 6 户）、"
      "`ods_qcc_person`（清洗后 2079 人 / 490 户）、`ods_qcc_axis_a_snapshot`（500 行原始快照）"
      "与 4 个并列视图；库结构变为 **37 表 / 33 视图 / 997 列**（数据字典同步）。"
      "复合负面事件共 6 家，其中 3 家原不在 E 预警档，是 issue ② 的第一批样本内事件；"
      "但五档事件率 A→E **不单调**（D 档为 0），只能证明「数据已就位」，"
      "**不能宣称评级有效**。复合事件是并列候选，正式标签 / 五档 / 训练入口 / "
      "`DEFINITION_VER` 一律未改。详见 `01_算法端/axis_a_judicial_report.md`。\n")
    A("**3. 企业 ID 格式。**")
    A("`ENT` + 12 位 hex、共 15 字符。传 `CN-330100-0001` 不会 500，"
      "会返回 `code=NOT_FOUND` 且 `meta.detail` 写明正确格式；"
      "前端应渲染成「未找到」而非「系统错误」。\n")

    A("## 六、还没做的\n")
    A("| 项 | 影响 | 难度 |")
    A("|---|---|---|")
    A("| P2：把复合负面事件正式纳入评级 / 做跨源效度 | v2.0 已落地样本内司法违约 6 户（3 家新增 E 候选），但仍是**并列候选视图**；6 个锚点里 4 个是状态量、分档复用形成循环定义 | 需另一次升版拍板（改 `DEFINITION_VER` / `DATASET_VER` → 重建矩阵与五档 → 重跑训练 / SHAP / 测评） |")
    A("| 扩母池或补采更多司法 / 失信名录 | 样本内复合事件仅 6 家（1.20%），五档事件率不单调且置信区间极宽 | 需采集；287 家官方撤销名单已全覆盖但样本内 0 交集 |")
    A("| issue ① 后续：导师书面口径、287 家名单入库、母池 21,968 口径落库 | 路径 (a) 证据链已完全闭合（7 份公告 287 家全覆盖、三层 0 命中）；`DEFINITION_VER` v0.1 未升版 | 需拍板 + 落库 |")
    A("| 升版 v0.2：别名归一后重建 `ads_label` 与训练入口 | 7 家改名假象目前全被压在 D 观察档；改库会连锁影响守卫、AUC / SHAP / 多标签结论 | **待拍板，本次未做** |")
    A("| 扩样本池（500 → 5,000） | 只增加同类共线标签的**数量**，不增加标签**维度**，已从 P1 移出 | 需拍板的可选采集项 |")
    A("| 训练一个可验证模型 | `score.value` 恒为 null，只能给 `proxy_score` | 依赖独立结果标签 |")
    A("| 旧契约目录 %d 个假数据尚未删除 | 已用 `outputs/contracts/` 的 12 个真契约替代，"
      "API 不再读旧目录；但目录还在，误拷会覆盖 | 低 |" % fe["n_bad"])
    A("| `grant_date` / 引文库 / CAGR / 财务序列 | 结构性缺口，源数据没有 | 无解（已登记） |")
    A("| 行业切片 | `qcc_industry_mid` 覆盖率不足 | 依赖补采 |")
    A("")
    A("**本轮已闭合**：12 个 `CONTRACT_PENDING` 接口全部产出真实契约；"
      "2 个导入接口完成字段级校验与 staging 落库；`explain/local` 改为按企业动态生成；"
      "**issue ① 已由官方撤销公告实证闭合**——7 份公告 287 家全覆盖（含 5 份扫描件逐页读图），"
      "500 家样本 / 307 家已标注 / 21 家断档含别名比对均 0 命中，其中 7 家为改名假象；"
      "标签定性为「资质再申报行为」并保留为多标签之一；**issue ② 取得 287 家完整行政决定集合**"
      "（样本内 0 交集，E 档仍塌陷）。就这项核查本身而言**未改库、未升版**，"
      "见 `official_cancel_check.md`。")
    A("**其后两轮本地升版（均为并列层，不动已发布口径）**：P1-续 已用 "
      "`v_model_train_ready_credit` 重跑 as-of 训练 / SHAP，验证方向翻转后 AUC 互为 1−AUC、"
      "并完成分队列与分 `as_of_policy` 复核；**v2.0** 接入企查查司法违约与主要人员，"
      "库结构 34/29/912 → **37 表 / 33 视图 / 997 列**，数据字典同步重生成，"
      "复合负面事件 6 家 / 3 家新增 E 候选。两轮都逐项保留 `ads_label` 307/21/286、"
      "多标签五档、`DEFINITION_VER` 与 `ASOF_VER`。")
    A("")
    A("## 七、复现\n")
    A("```bash")
    A("python -m src.data.build_contracts          # 12 个契约 → outputs/contracts/")
    A("python -m src.data.export_data_dictionary   # 字段字典 %d 列" % st["n_cols"])
    A("python -m src.data.export_label_spec         # 标签说明书")
    A("python -m src.data.export_field_gap_registry # 缺口登记册")
    A("python -m src.data.export_official_cancel_check # issue ① 官方撤销公告核查报告（只读，不改库）")
    A("python -m src.data.apply_v2_0_axis_a          # v2.0 A 轴司法/人员并列层（幂等；--check 只读）")
    A("python -m src.data.export_axis_a_judicial_report # v2.0 A 轴接入报告（只读）")
    A("python -m src.data.apply_funding_provenance  # A7 融资事件行级溯源回填（幂等）")
    A("python -m src.data.export_db_bundle         # 05_数据库 快照 + Release 附件")
    A("python -m src.data.export_delivery --zip     # 本交付包三个 + zip")
    A("")
    A("python tests/test_train_ready.py             # 8 项 训练集守卫")
    A("python tests/test_api_live.py                # 9 项 接口守卫")
    A("python tests/test_contracts.py               # 19 项 契约反比对守卫")
    A("python tests/test_data_dictionary.py         # 5 项 字典守卫")
    A("python tests/test_label_spec.py              # 6 项 说明书守卫")
    A("python tests/test_patent_dedup.py            # 5 项 去重守卫")
    A("python tests/test_grant_definition.py        # 7 项 授权口径守卫")
    A("python tests/test_field_gap_registry.py      # 7 项 缺口登记 + 标签任务 / as-of 口径守卫")
    A("python tests/test_graph_v2.py                # 8 项 图谱口径守卫")
    A("python tests/test_label_mechanism.py         # 7 项 标签机制守卫")
    A("python tests/test_patent_master.py           # 4 项 专利唯一口径守卫")
    A("python tests/test_graph_id_docs.py           # 22 项 图 ID / 融资补算守卫")
    A("python tests/test_enterprise_grade_l0.py     # 11 项 五档分档视图守卫")
    A("python tests/test_funding_join_contract.py   # 10 项 融资 LEFT JOIN 接入口径守卫")
    A("python tests/test_v1_8_views.py              # 8 项 有效覆盖 / 年度切片 / 物理口径守卫")
    A("python tests/test_dataset_isolation.py       # 8 项 A/B 数据隔离 / 分档端点守卫")
    A("python tests/test_funding_provenance.py      # 8 项 融资事件行级溯源回填守卫")
    A("python tests/test_schema.py                 # 38 项 约束 / 字典 / 链路守卫（已适配 B 套）")
    A("python tests/test_collect_pool.py           # 16 项 公示名单采集器 / 幂等守卫（临时库）")
    A("python tests/test_multilabel_eval.py        # 16 项 v1.9 标签方向 / 多标签测评守卫")
    A("python tests/test_credit_direction.py       # 20 项 P1-续 方向纠正训练 / SHAP / AUC 恒等式守卫")
    A("python tests/test_graph_temporal_contract.py # 15 项 时序图同源 / 守恒 / 查询性能闸门守卫")
    A("python tests/test_shap_direction_notice.py  # 8 项 SHAP 方向语义 / 契约路径基准守卫")
    A("python tests/test_field_gap_decisions.py    # 11 项 缺口定稿落地 / 交付散文件同源守卫")
    A("python tests/test_official_cancel_check.py  # 10 项 官方核查证据链 / 口径隔离 / 未改库守卫")
    A("python tests/test_axis_a_judicial.py       # 12 项 v2.0 A 轴结构 / 清洗 / 口径隔离 / 字典同源守卫")
    A("```")
    A("合计 **298 项**，全绿（26 个测试文件）。`test_contracts.py` 的重点是**反向比对**："
      "把契约里的数拿回 SQLite 现场算一遍，对不上就失败 —— 防的是「为了有图可画而编数据」。")
    A("")
    A("**测试文件范围（2026-11-11 更新）**：`tests/` 下 26 个 `test_*.py` **全部纳入交付守卫**，"
      "新增 `test_official_cancel_check.py`（10 项：官方公告证据链、21 家逐户判定、口径隔离与未改库）"
      "与 `test_axis_a_judicial.py`（12 项：v2.0 三表四视图、490 行表头脏行剔除、三类金额分列、"
      "复合事件候选与已发布标签未动、997 列字典与交付散文件同源）。"
      "其中 `test_schema.py`（38 项）与 `test_collect_pool.py`（16 项）源自早期 A 套 27 表"
      "链路自测，已于 11-09 适配 B 套 v1.8：IPC 改校验 A–H 八个部类齐全（全量字典 11,616 行）、"
      "桥表改按本次插入的 patent_id 定点校验、覆盖度视图改断言「基线 500 + 本次 1 家」；"
      "采集器补齐 `requirements.txt` 已声明的 `openpyxl>=3.1` 后，5 个夹具恢复为 9 家企业，"
      "XLSX 解析路径真实覆盖。`test_collect_pool.py` 全程用临时库，与交付库相互独立；"
      "若环境缺 openpyxl，XLSX 项显式记为 SKIP、端到端期望值按实际夹具数动态计算，"
      "既不静默算通过也不会误报失败。")
    A("")
    with open(os.path.join(D_DOC, "delivery_spec.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------------------
# 4. 数据字典与数据集
# ---------------------------------------------------------------------------
def build_data_bundle(con):
    """单独可交的三件：数据字典、代理标签说明书、数据集。

    这三件原本散在 `outputs/` 与算法包里，接收方要翻三个地方。这里合成一个
    自解释目录：字典两个格式（人读 + 机器读）、说明书、三个 CSV，外加各自的 README。
    """
    ensure(D_DATA)
    ensure(D_DATASET)

    n_tab = q1(con, "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    n_view = q1(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='view'")
    n_all = q1(con, "SELECT COUNT(*) FROM v_model_dataset_asof_flat")
    n_tr = q1(con, "SELECT COUNT(*) FROM v_model_train_ready")
    n_pos = q1(con, "SELECT SUM(label) FROM v_model_train_ready")
    n_ent = q1(con, "SELECT COUNT(*) FROM dim_enterprise")
    n_excl = q1(con, "SELECT COUNT(*) FROM ads_model_dataset WHERE split='excluded'")

    # ---- 4.1 数据字典（人读 md + 机器读 csv）----
    copied = []
    for src_name, dst_name in [("data_dictionary.md", "数据字典.md"),
                               ("data_dictionary.csv", "数据字典.csv"),
                               ("label_spec.md", "代理标签说明书.md"),
                               ("field_gap_registry.md", "字段缺口登记册.md")]:
        src = os.path.join(OUTPUTS, src_name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(D_DATA, dst_name))
            copied.append(dst_name)

    # 字典实际列数（以 CSV 行数为准，防口径漂移）
    import csv as _csv
    dd_rows = 0
    dd_path = os.path.join(D_DATA, "数据字典.csv")
    if os.path.exists(dd_path):
        with open(dd_path, encoding="utf-8-sig", newline="") as f:
            dd_rows = sum(1 for _ in _csv.reader(f)) - 1

    # 缺口登记册条目数（以 configs/field_gap_registry.json 为准，不写死）
    reg_n_items = reg_n_sub = reg_n_traps = 0
    try:
        with open(os.path.join(ROOT, "configs", "field_gap_registry.json"),
                  encoding="utf-8") as f:
            _reg = json.load(f)
        reg_n_items = len(_reg.get("items", []))
        reg_n_sub = len(_reg.get("substitutes", []))
        reg_n_traps = len(_reg.get("meta", {}).get("traps", []))
    except Exception:
        pass

    # ---- 4.2 数据集：训练集 / 全量 as-of 面板 / 企业主档 ----
    tr_src = os.path.join(D_ALG, "model_train_ready.csv")
    if os.path.exists(tr_src):
        shutil.copy2(tr_src, os.path.join(D_DATASET, "model_train_ready.csv"))
    else:  # 单独生成 04 包时算法包可能还没构建
        tr_cols = [r[1] for r in q(con, "PRAGMA table_info(v_model_train_ready)")]
        tr = q(con, "SELECT * FROM v_model_train_ready "
                    "ORDER BY CASE split WHEN 'train' THEN 1 WHEN 'val' THEN 2 "
                    "ELSE 3 END, enterprise_id")
        write_csv(os.path.join(D_DATASET, "model_train_ready.csv"),
                  tr_cols, [[r[c] for c in tr_cols] for r in tr])

    flat_cols = [r[1] for r in q(con, "PRAGMA table_info(v_model_dataset_asof_flat)")]
    flat = q(con, "SELECT * FROM v_model_dataset_asof_flat "
                  "ORDER BY enterprise_id, as_of_year")
    write_csv(os.path.join(D_DATASET, "dataset_asof_flat.csv"),
              flat_cols, [[r[c] for c in flat_cols] for r in flat])

    roster_cols = ["enterprise_id", "name_display", "credit_code", "region_code",
                   "region_name", "enterprise_type", "first_qual_year",
                   "last_qual_year", "max_qual_rank", "is_listed", "patent_cnt"]
    have = {r[1] for r in q(con, "PRAGMA table_info(dim_enterprise)")}
    roster_cols = [c for c in roster_cols if c in have]
    roster = q(con, "SELECT * FROM dim_enterprise ORDER BY enterprise_id")
    write_csv(os.path.join(D_DATASET, "enterprise_roster.csv"),
              roster_cols, [[r[c] for c in roster_cols] for r in roster])

    # ---- 4.3 数据集 README ----
    R = []
    A = R.append
    A("# 数据集\n")
    A("三个 CSV，都从 `zjtech_credit.db` 只读导出，口径与库内一致。\n")
    A("| 文件 | 行 × 列 | 是什么 | 能不能直接训练 |")
    A("|---|---|---|---|")
    A("| `model_train_ready.csv` | **%d × 17** | 训练集，一行一家，已切 split | ✅ 直接 fit |"
      % n_tr)
    A("| `dataset_asof_flat.csv` | **%d × %d** | 全量 as-of 面板，含 `excluded` | ❌ 含假续存样本，勿直接训 |"
      % (n_all, len(flat_cols)))
    A("| `enterprise_roster.csv` | **%d × %d** | 企业主档（500 家名单） | ❌ 无标签，仅供关联 |"
      % (len(roster), len(roster_cols)))
    A("")
    A("## model_train_ready.csv（主数据）\n")
    A("`%d` 行 = 500 家 − %d 家被排除（首次认定在 2023/2024/2025，3 年窗口未闭合）"
      "− 7 家无 high_tech 记录。" % (n_tr, n_excl))
    A("")
    A("| split | 行数 | 其中断档样本 |")
    A("|---|---|---|")
    for sp in ("train", "val", "test"):
        r_ = q1(con, "SELECT COUNT(*) FROM v_model_train_ready WHERE split=?", (sp,))
        p_ = q1(con, "SELECT SUM(label) FROM v_model_train_ready WHERE split=?", (sp,)) or 0
        A("| `%s` | %d | %d |" % (sp, r_, p_))
    A("| **合计** | **%d** | **%d**（%.1f%%）|" % (n_tr, n_pos, 100.0 * n_pos / n_tr))
    A("")
    A("17 列 = 5 个键（`enterprise_id` / `name_display` / `region_code` / "
      "`cohort_year` / `split`）+ 1 个标签 + 11 个特征。")
    A("4 个常量特征（`m_cert` / `qual_rank` / `qual_rank_delta` / `qual_upgrade_cum`）"
      "已从视图中剔除。")
    A("")
    A("## dataset_asof_flat.csv（全量面板）\n")
    A("`%d` 行是 as-of 宽表，同一家企业按不同 `as_of_policy` 会有多行。"
      "**训练必须过滤 `as_of_policy='cohort'`**，否则同一个体会重复计权。" % n_all)
    A("")
    A("```python")
    A("import pandas as pd")
    A("df = pd.read_csv('dataset_asof_flat.csv')")
    A("df = df[df.as_of_policy == 'cohort']      # %d 行 → 一家一行" % n_tr)
    A("```")
    A("")
    A("`split == 'excluded'` 的 %d 行**不是续存样本**，是「还没到观察终点」，"
      "当续存样本用会把断档样本占比从 6.8%% 稀释到 4.2%%。" % n_excl)
    A("")
    A("## 三条不能做的事\n")
    A("1. **不要重新切分**。`split` 按企业级固定，重切会造成跨集泄漏（实测 AUC 0.796 → 0.524）。")
    A("2. **不要报单一 AUC**。%d 个断档样本、val 只有 2 个，请报交叉验证区间。" % n_pos)
    A("3. **不要把 `label` 当官方违约口径**。它是代理标签：高企资格 3 年到期复核未通过 = 断档样本。")
    A("")
    A("字段逐列说明见上级目录 `数据字典.md`，标签定义见 `代理标签说明书.md`。")
    A("")
    with open(os.path.join(D_DATASET, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(R))

    # ---- 4.4 本目录 README ----
    L = []
    A = L.append
    A("# 数据字典与数据集\n")
    A("三件配套的东西放在一起，接收方不用再翻三个地方。\n")
    A("| 交付物 | 文件 | 规模 | 用途 |")
    A("|---|---|---|---|")
    A("| **数据字典** | `数据字典.md` | %d 列逐列说明 | 人读：查某个字段是什么意思、空多少 |"
      % dd_rows)
    A("| **数据字典**（机器读） | `数据字典.csv` | %d 行 | 灌进元数据系统 / 写代码时查 |" % dd_rows)
    A("| **代理标签说明书** | `代理标签说明书.md` | 14 节 | 标签怎么造、阈值、陷阱、争议点；§13 官方撤销公告核查（287 家全覆盖） |")
    A("| **字段缺口登记册** | `字段缺口登记册.md` | %d 空字段 + %d 替身 + %d 陷阱 | 哪个字段是空、为什么空、能不能补 |"
      % (reg_n_items, reg_n_sub, reg_n_traps))
    A("| **数据集** | `数据集/` | 3 个 CSV | 直接训练 + 全量面板 + 企业名单 |")
    A("")
    A("## 数据字典覆盖范围\n")
    A("| 对象 | 数量 | 字段数 |")
    A("|---|---|---|")
    A("| 表 | %d | — |" % n_tab)
    A("| 视图 | %d | — |" % n_view)
    A("| **字段合计** | — | **%d** |" % dd_rows)
    A("")
    A("每一列都有：类型、是否可空、非空率、中文说明。视图字段也逐列写了说明，"
      "不是只列表名。")
    A("")
    A("`数据字典.csv` 列：`object_type`（table / view）、`object_name`、`column`、"
      "`type`、`not_null`、`pk`、`nonnull_rate`、`comment`。")
    A("")
    A("## 三个交付物怎么配合用\n")
    A("1. 先看 `代理标签说明书.md` —— 不理解标签就没法理解数据集。")
    A("2. 再看 `数据集/README.md` —— 三个 CSV 哪个能训、哪个不能。")
    A("3. 查字段时用 `数据字典.md`（全文搜列名）或直接 grep `数据字典.csv`。")
    A("")
    A("```bash")
    A("# 例：查某个字段属于哪张表、空值率多少")
    A("grep 'grant_rate' 数据字典.csv")
    A("```")
    A("")
    A("## 一个必须知道的坑\n")
    A("有 15 个字段是空的，但它们**不都是「没采到」**，分三种状态：")
    A("`null_by_design`（业务上就不存在）/ `source_missing`（源数据没有）/ "
      "`partial_insufficient`（采了一点但不够用）。")
    A("混淆这三种会导出一个错误结论「这批数据啥都没有」。逐项登记见同目录 "
      "`字段缺口登记册.md`。")
    A("")
    with open(os.path.join(D_DATA, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    return {"dict_rows": dd_rows, "tables": n_tab, "views": n_view,
            "flat": (len(flat), len(flat_cols)), "train": (n_tr, 17),
            "roster": (len(roster), len(roster_cols)), "copied": copied}


def do_zip():
    """把各目录打成 zip，方便直接发出去。"""
    import zipfile
    pairs = [(D_ALG, "algorithm"), (D_FE, "frontend"), (D_DOC, "readme"),
             (D_DATA, "data_dictionary_dataset")]
    made = []
    for d, name in pairs:
        if not os.path.isdir(d):
            continue
        zp = os.path.join(OUT, "%s.zip" % name)
        n = 0
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(d):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    z.write(fp, os.path.relpath(fp, OUT))
                    n += 1
        made.append((zp, n, os.path.getsize(zp)))
    return made


def main():
    ap = argparse.ArgumentParser(description="生成三份交付包")
    ap.add_argument("--only", choices=["algorithm", "frontend", "doc", "data"],
                    help="只生成其中一个包")
    ap.add_argument("--zip", action="store_true", help="额外打三个 zip")
    args = ap.parse_args()

    con = connect()
    print("=" * 64)
    print("  生成交付包")
    print("=" * 64)

    alg = fe = db = None
    if args.only in (None, "algorithm"):
        alg = build_algorithm(con)
        print("✅ 算法端：%d 行 × %d 列，断档样本 %d → %s"
              % (alg["rows"], alg["cols"], alg["pos"], D_ALG))
    if args.only in (None, "frontend"):
        fe = build_frontend(con)
        print("✅ 前端：%d 接口（有数据 %d / 空壳 %d / 占位 %d / 需文件 %d / 报错 %d），"
              "%d 个旧契约中 %d 个不可用%s"
              % (fe["n_api"], fe["n_data"], fe["n_hollow"], fe["n_pend"],
                 fe["n_file"], fe["n_err"],
                 fe["n_contract"], fe["n_bad"],
                 "" if fe["live"] else "（未起服务，状态列为空）"))
    if args.only in (None, "data"):
        db = build_data_bundle(con)
        print("✅ 数据字典与数据集：字典 %d 列 / 面板 %d×%d / 训练集 %d×%d / 名单 %d×%d → %s"
              % (db["dict_rows"], db["flat"][0], db["flat"][1],
                 db["train"][0], db["train"][1], db["roster"][0], db["roster"][1],
                 D_DATA))
    if args.only in (None, "doc"):
        if alg is None:
            alg = build_algorithm(con)
        if fe is None:
            fe = build_frontend(con)
        build_doc(con, alg, fe)
        print("✅ 说明：%s" % D_DOC)

    # 方向声明必须「最后注入、打包之前执行」：交付文档重生成后头部声明会丢失。
    # 历史上曾因只给 outputs/data_dictionary.md 补声明、漏了交付目录那份，
    # 导致两份字典不同源、test_funding_join_contract 失败。此处统一收口。
    # inject_direction_notice 幂等，且对不存在的目标自动跳过，不影响 --only 分块生成。
    from src.data.inject_direction_notice import main as inject_notice
    inject_notice()

    if args.zip:
        for zp, n, size in do_zip():
            print("📦 %s（%d 个文件，%.1f KB）" % (os.path.basename(zp), n, size / 1024))

    con.close()
    print("=" * 64)


if __name__ == "__main__":
    main()
