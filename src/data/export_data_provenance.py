# -*- coding: utf-8 -*-
"""生成数据来源声明 / 数据使用条款（只读，不改库）。

2026-11-12 用户最终拍板「数据全部都上传公开」：完整库原样随 Release 附件
发布，不做去标识化、不撤下历史附件、不私下交付。两份文档是**随包说明**
（来源、建议用途、禁止事项与风险告知），不是分发限制。

两份文档都是交付散文件，必须写回本生成源，保证最终一次性打包时同源不丢。

用法：
    python -m src.data.export_data_provenance
"""
from __future__ import annotations

import os
import sqlite3

from src.data.export_db_bundle import DB

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "deliverables")
REPO = "https://github.com/JiWu-hyw/sme-credit-eval"
DECIDED = "2026-11-12"

QCC_TABLES = (
    "ods_qcc_annual", "ods_qcc_cert", "ods_qcc_honor", "ods_qcc_shareholder",
    "ods_qcc_judicial", "ods_qcc_person", "ods_qcc_axis_a_snapshot",
    "ods_qcc_trademark",
)
PERSON_FIELDS = (
    ("ods_qcc_person", "person_name"),
    ("ods_qcc_judicial", "applicant"),
    ("ods_qcc_axis_a_snapshot", "person_names_raw"),
    ("ods_qcc_axis_a_snapshot", "case_sample_raw"),
)


def _counts():
    """采集层表行数与自然人字段规模，现场从工作库算，不写常量。"""
    c = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    try:
        tables = []
        for t in QCC_TABLES:
            n = c.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            tables.append((t, n))
        persons = []
        for t, col in PERSON_FIELDS:
            n = c.execute(
                'SELECT COUNT(*) FROM "%s" WHERE "%s" IS NOT NULL '
                'AND TRIM("%s") <> ""' % (t, col, col)).fetchone()[0]
            persons.append((t, col, n))
        return tables, persons
    finally:
        c.close()


def provenance_md():
    tables, persons = _counts()
    qcc_total = sum(n for _, n in tables)
    L = []
    A = L.append
    A("# 数据来源与使用声明")
    A("")
    A("> **口径定稿 %s**（用户拍板「数据全部都上传公开」）：**完整库原样随 "
      "GitHub Release 附件全部公开**，不做去标识化、不撤下历史附件、不私下交付。"
      "本文件取代此前各文档里「服务条款待确认 / 需单独拍板 / 禁止公开分发 / "
      "去标识化 / 私下交付」等互不一致的旧表述。" % DECIDED)
    A("")
    A("## 1. 一句话结论")
    A("")
    A("本项目数据是**科研教学用途**的二次汇编。企业工商、专利、商标、司法案件等"
      "原始事实信息多来自政府公示渠道；其中一部分通过第三方商业平台的"
      "**登录态批量查询**取得，并非采购官方 API。用户已决定把包含这部分数据在内的"
      "完整数据库、`ods_qcc_*` 原始采集层与全部派生数据集**原样公开**。"
      "项目方**不主张**对第三方平台数据拥有排他权利，也不声明已取得书面再分发授权；"
      "本文件只说明来源、建议用途与已知风险。")
    A("")
    A("## 2. 数据来源清单")
    A("")
    A("| 来源 | 取得方式 | 落在库里 | 性质 |")
    A("|---|---|---|---|")
    A("| 浙江省专精特新 / 高企公示名单 | 公示网页、PDF 名单抓取与解析 | `dim_enterprise`、`fact_qualification`、`ads_label` | 政府公示信息 |")
    A("| CNIPA 专利公告 / 法律状态 | 公开专利数据包与公告解析 | `fact_patent`、`bridge_patent_ipc`、`ads_graph_*` | 官方公开数据 |")
    A("| 企查查公开页面 | **登录态浏览器批量查询**（`s14_qcc_login` / `s15_qcc_cookie` / `s16_qcc_cdp_crawl` 等脚本），**非官方 API 采购** | `ods_qcc_annual` / `_cert` / `_honor` / `_shareholder`（v1.x）、`ods_qcc_judicial` / `_person` / `_axis_a_snapshot`（v2.0）、`ods_qcc_trademark`（v2.1） | **第三方平台采集，受平台服务条款约束** |")
    A("| 用户补充的企查查回传 CSV | 对方批量查询结果文件，存于仓库外 `_data_collect/out/` | 同上 v2.0 / v2.1 并列层 | 同上；原始 CSV **未纳入交付包** |")
    A("| 融资事件合并流水 | 本地合并 CSV | `fact_funding_event`、`v_feature_asof_funding` | 项目内整理 |")
    A("")
    A("> ¥3,000「企查查 / 天眼查 API 采购」议题已由用户关闭（%s），"
      "本项目**不存在商业数据授权**。因此第 6 节的风险不能靠「已购买许可」消除。" % DECIDED)
    A("")
    A("## 3. 公开范围与个人信息")
    A("")
    A("按「全部公开」口径，交付快照 `05_数据库/zjtech_credit.db` 是工作库 "
      "`data/db/zjtech_credit.db` 的 `VACUUM INTO` 一致性副本，**字段值逐列相同、"
      "未做任何脱敏**。下表规模为现场回算，不是估算值：")
    A("")
    A("| 采集层表 | 行数 |")
    A("|---|---|")
    for t, n in tables:
        A("| `%s` | %s |" % (t, format(n, ",")))
    A("| **合计** | **%s** |" % format(qcc_total, ","))
    A("")
    A("其中含自然人可识别信息的字段**原样公开**（这些多为工商 / 司法 / 商标"
      "公示登记信息中已公开的姓名）：")
    A("")
    A("| 表.字段 | 非空行数 |")
    A("|---|---|")
    for t, col, n in persons:
        A("| `%s.%s` | %s |" % (t, col, format(n, ",")))
    A("")
    A("企业名称、统一社会信用代码、`case_no`（裁判文书案号）、`reg_no`（商标注册号）、"
      "`trademark_name` 亦按原样保留。接收方须自行判断使用场景是否符合"
      "《个人信息保护法》关于已公开个人信息「合理范围」的要求；**公开 ≠ 可任意处理**。")
    A("")
    A("## 4. 允许与禁止")
    A("")
    A("允许：科研、教学、方法复现、校内评审与论文写作中的引用；下载、内部复制、"
      "运行分析；在保留本声明与 `DATA_LICENSE.md` 的前提下转发与再分发。")
    A("")
    A("禁止：")
    A("")
    A("1. 商业使用、转售、转授权，或把数据并入商业产品 / 商业数据服务；")
    A("2. 用于真实个人征信、授信、投资决策、人事评价等对个人权益有实质影响的场景；")
    A("3. 把自然人字段与第三方人员库 / 征信库拼接用于重识别或画像，"
      "或超出科研教学目的处理个人信息；")
    A("4. 把本项目数据与另一套样本（A 信贷面板 / B 浙江高企）混用，"
      "或把并列观察量当作评级有效性证据；")
    A("5. 移除或改写本声明、`DATA_LICENSE.md` 与来源 / 风险说明。")
    A("")
    A("引用请注明项目名称与版本号，并链接到 " + REPO + "。")
    A("")
    A("## 5. 分发渠道")
    A("")
    A("| 产物 | 渠道 | 说明 |")
    A("|---|---|---|")
    A("| Release 附件 `zjtech_credit_data.zip` | **GitHub Release（公开）** | 含 `04_数据字典与数据集/` 与 `05_数据库/` 完整库快照，以及全部交付文档 |")
    A("| 完整库 `05_数据库/zjtech_credit.db` | **随上公开** | 与工作库一致的 VACUUM 副本，**未脱敏** |")
    A("| 历史 v1.0.0 / v1.3.0 / v1.4.0 附件 | **保持不动** | 按 add-new-no-overwrite 约定原地留档，不撤下、不覆盖 |")
    A("| 本地 9 个历史 zip | **原地留档** | 不删、不覆盖 |")
    A("| 企查查回传原始 CSV | 未纳入交付包 | 仓库外 `_data_collect/out/`，仅本地留存 |")
    A("")
    A("`export_db_bundle.RELEASE_PARTS` 保持 `['04_数据字典与数据集', '05_数据库']`；"
      "脚本不得加入去标识化步骤。")
    A("")
    A("## 6. 仍需知悉的风险（公开不可撤回）")
    A("")
    A("- **平台服务条款**：登录态批量查询可能违反第三方平台的用户协议。"
      "这是采集方与平台之间的合同问题，**本项目未取得书面授权**；"
      "用户已决定公开，本声明不使采集行为合规。若平台提出异议，应立即停止分发并删除相关数据。")
    A("- **个人信息**：完整库含自然人姓名字段且未脱敏，接收方须在合理范围内处理，"
      "不得用于真实征信 / 授信 / 人事决策。")
    A("- **公开不可撤回**：v1.0.0 起的公开附件已包含 `ods_qcc_*` 四表；"
      "本轮进一步把 v2.0 / v2.1 并列层与完整库一并公开。"
      "一旦上传，无法追溯已发生的下载，撤回只能停止后续分发。")
    A("- 本项目**不提供任何权利担保**，也不对第三方数据的准确性、完整性负责。")
    A("")
    A("## 7. 撤回与联系")
    A("")
    A("数据主体、平台方或接收方如需撤回、更正或删除，请通过 "
      + REPO + "/issues 提交；我们会在核实后从工作库、交付快照与所有"
      "交付副本中同步删除，更新 MANIFEST / SHA-256，并视情况撤下对应附件。"
      "请注意已发生的公开下载无法追溯。")
    A("")
    A("## 8. 复现")
    A("")
    A("```bash")
    A("python -m src.data.export_data_provenance   # 本声明 / 使用条款")
    A("python -m src.data.export_db_bundle --no-zip # 完整库一致性快照（不脱敏）")
    A("python -m src.data.export_delivery          # 交付散文件（不带 --zip）")
    A("python tests/test_data_distribution.py      # 全部公开口径守卫")
    A("```")
    A("")
    A("口径登记：`configs/field_gap_registry.json` → `policies.data_distribution`。")
    return "\n".join(L) + "\n"


def license_md():
    L = []
    A = L.append
    A("# 数据使用条款（DATA LICENSE）")
    A("")
    A("**生效日期**：%s　|　**适用对象**：本项目全部交付数据、数据库副本与派生数据集。" % DECIDED)
    A("")
    A("> 这不是开源软件许可证，也**不是对公开分发的限制**。用户已决定数据全部公开；"
      "本项目中相当一部分数据来自第三方平台的采集结果，项目方**不拥有、也不声称拥有**"
      "这些数据的排他权利，因此不能授予商用许可。本条款只说明「在什么条件下"
      "我们建议并允许使用」。")
    A("")
    A("## 1. 允许")
    A("")
    A("在遵守下列条件的前提下，允许你下载、内部复制、运行分析、复现方法，"
      "在论文或报告中引用结果，并**转发或再分发完整数据集**：")
    A("")
    A("1. 仅限非营利的科研、教学、校内评审与方法复现；")
    A("2. 引用时注明项目名称、版本与 " + REPO + "；")
    A("3. 保留本条款与 `DATA_PROVENANCE.md`，并在任何再传递时一并附上；")
    A("4. 不改变数据内容而不加说明，不把派生结果伪装成原始采集数据。")
    A("")
    A("## 2. 明确禁止")
    A("")
    A("- 商业使用、转售、转授权、并入商业数据库或数据服务；")
    A("- 用于真实个人征信、授信、投资决策、人事评价等对个人权益有实质影响的场景；")
    A("- 把自然人字段与第三方人员库 / 征信库 / 工商库拼接用于重识别或画像，"
      "或超出科研教学目的处理个人信息；")
    A("- 移除或改写本条款、来源声明与风险说明。")
    A("")
    A("## 3. 第三方权利")
    A("")
    A("第三方平台对其页面、数据库编排与接口可能享有权利，平台服务条款另行适用。"
      "**本条款不替代、也不豁免**你与该平台之间的任何义务。"
      "采集方式为登录态批量查询，项目方未取得书面再分发授权；"
      "如平台提出异议，本条款自动终止，接收方应停止使用并删除副本。")
    A("")
    A("## 4. 个人信息")
    A("")
    A("按「全部公开」口径，交付库**未做去标识化**，含自然人姓名字段。"
      "接收方须按《个人信息保护法》在合理范围内处理，不得尝试恶意重识别，"
      "不得超出第 1 节目的使用。详见 `DATA_PROVENANCE.md` §3。")
    A("")
    A("## 5. 撤回与删除")
    A("")
    A("项目方可随时撤回本条款。数据主体、平台方或接收方通过 "
      + REPO + "/issues 提出删除请求后，我们将在工作库与所有副本中同步删除；"
      "**公开已发生的下载无法追溯**。")
    A("")
    A("## 6. 无担保")
    A("")
    A("数据按「现状」提供，**不保证准确性、完整性、时效性或适销性**。"
      "本项目仅有 21 个负例样本，任何性能数字只够验证方法论，"
      "不得作为模型有效性或信用评价结论。")
    A("")
    A("## 7. 与代码许可分开")
    A("")
    A("本条款仅约束**数据**。仓库中的代码、SQL 与文档不在本条款授权范围内，"
      "在未附加代码许可证前，默认保留所有权利。")
    return "\n".join(L) + "\n"


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("DATA_PROVENANCE.md", provenance_md),
                     ("DATA_LICENSE.md", license_md)):
        p = os.path.join(OUT, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(fn())
        print("[done] %s" % os.path.relpath(p, ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
