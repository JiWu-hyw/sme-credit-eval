# -*- coding: utf-8 -*-
"""重建《数据现状说明_有什么与缺什么.docx》—— v2.2 全面刷新版（2026-11-12）。

数据口径来源（全部可逐项复核）：
- deliverables/05_数据库/zjtech_credit.db：42 表 / 37 视图 / 1081 列，schema v2.2
- meta_schema_version 升版台账（v1.0 → v2.2 共 13 条）
- v_six_entity_coverage / v_graph_slice_annual / ads_graph_node_ts / ads_graph_edge_ts
- _data_collect/out/合并_500家企业数据总表.xlsx（501 行 × 153 列 + 2 张新 sheet）

用法：python scripts/build_data_status_doc.py [--out 路径]
"""
import argparse
import os

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(BACKEND, "data", "docs", "数据现状说明_有什么与缺什么.docx")

BLUE = RGBColor(0x1F, 0x4E, 0x79)
GRAY = RGBColor(0x59, 0x59, 0x59)
RED = RGBColor(0xC0, 0x00, 0x00)
GREEN = RGBColor(0x2E, 0x75, 0x36)


def set_font(run, size=10.5, bold=False, color=None, name="微软雅黑"):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color is not None:
        run.font.color.rgb = color


def para(doc, text, size=10.5, bold=False, color=None, style=None, align=None):
    p = doc.add_paragraph(style=style)
    if align is not None:
        p.alignment = align
    r = p.add_run(text)
    set_font(r, size=size, bold=bold, color=color)
    return p


def heading(doc, text, size=14, color=BLUE, space_before=10):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space_before)
    r = p.add_run(text)
    set_font(r, size=size, bold=True, color=color)
    return p


def note(doc, text):
    return para(doc, text, size=9, color=GRAY)


def table(doc, header, rows, widths=None):
    tb = doc.add_table(rows=1 + len(rows), cols=len(header))
    tb.style = "Table Grid"
    for j, h in enumerate(header):
        cell = tb.rows[0].cells[j]
        cell.text = ""
        r = cell.paragraphs[0].add_run(h)
        set_font(r, size=9.5, bold=True)
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            cell = tb.rows[i + 1].cells[j]
            cell.text = ""
            r = cell.paragraphs[0].add_run(str(v))
            set_font(r, size=9.5)
    if widths:
        for j, w in enumerate(widths):
            for row in tb.rows:
                row.cells[j].width = w
    return tb


def build(out_path):
    doc = docx.Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    style.font.size = Pt(10.5)

    # ===== 封面元信息 =====
    para(doc, "500 家专精特新样本数据现状说明", size=18, bold=True, color=BLUE,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "—— 数据层面：有什么、缺什么（v2.2 全面刷新版）", size=12, color=GRAY,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "编制日期：2026-11-12　|　口径：字段级 + 库表级 + 图谱级", size=10, color=GRAY,
         align=WD_ALIGN_PARAGRAPH.CENTER)

    table(doc, ["项目", "说明"], [
        ["数据主体", "浙江省 2023 年度第一批专精特新中小企业 500 家样本"],
        ["数据来源", "① 本人采集（政府公开平台 / 官方接口 / 搜索引擎 RSS）；② 同学张丽晶提供的两份 Excel；③ 企查查批量查询回传（司法 / 主要人员 / 股东 / 商标 / 年报 / 认证 / 荣誉，零新增采购）"],
        ["数据格式", "CSV（UTF-8-SIG）+ XLSX + SQLite 标准库，缺失值统一标记为 NAN"],
        ["合并方式", "以「企业名称」为主键，500 户全部精确匹配，无错位"],
        ["统计截止", "2026-11-12"],
        ["标准数据库", "zjtech_credit.db（42 张表 + 37 个视图 / 1081 列，schema v2.2）"],
        ["图数据规模", "六类实体 61,584 节点 + 171,210 边，时序化 29 个年度切片（1992–2026）"],
    ], widths=[Pt(90), Pt(380)])

    heading(doc, "一、文档说明")
    para(doc, "本文只做数据盘点和缺口说明，不做分析结论、不涉及用途评价。所有数字均取自实际数据文件与标准数据库，可逐项复核。")
    para(doc, "说明：文中「覆盖」指该字段有非空值的户数（共 500 户）；「NAN」为约定的缺失标记，可直接被 pandas 识别（na_values=[\"NAN\"]）。")
    para(doc, "本版相对 2026-10-28 版的主要变化：标准库由 27 张表（v1.0）扩至 42 张表（v2.2）；融资事件、MSME 对照集、出资人股东、企查查 A 轴（司法/主要人员）、商标与产品节点、时序图均已补齐入库；完全缺失字段收敛至「研发投入 + 专利被引」两项。")

    heading(doc, "二、数据总览")
    table(doc, ["模块", "字段数", "覆盖情况", "数据来源"], [
        ["① 主体与主键", "3", "500 户全部（信用代码 474）", "样本名单 + 信用平台"],
        ["② 资质与政策", "13", "专精特新 500；高企 493", "省经信厅公示 + 张丽晶认定档案"],
        ["③ 专利·统计", "14", "500 户全部", "官方专利统计接口"],
        ["④ 专利·明细", "17", "18,109 条", "张丽晶 17,663 条 + 本人 959 条"],
        ["⑤ 工商信息", "8", "458–474 户（注册资本 0）", "省政府信用平台"],
        ["⑥ 风险合规", "8", "463 户", "省政府信用平台"],
        ["⑦ 网络足迹与舆情", "10", "500 户（媒体 36）", "搜索引擎 RSS"],
        ["⑧ 上市关联", "2", "9 家", "财经数据接口"],
        ["⑨ 企查查回传（新增）", "17+", "司法 6 户 / 主要人员 490 户 / 股东 500 户 / 商标 444 户 / 年报·认证·荣誉 500 户", "企查查批量查询"],
        ["⑩ 融资事件（新增）", "6", "506 条 / 182 户", "开源融资数据整合"],
        ["⑪ 对照数据集", "—", "MSME 1,707 家已入库；高企名单 13 份 PDF / 约 10 万条", "Kaggle / 高企认定工作网"],
    ], widths=[Pt(110), Pt(45), Pt(170), Pt(145)])

    heading(doc, "三、已有数据（字段级）")
    para(doc, "3.1–3.8 节与 2026-10-28 版一致（主体主键 / 资质政策 / 专利统计与明细 / 工商 / 风险合规 / 网络舆情 / 上市关联），此处不再重复；明细见旧版存档。以下仅列 2026-11-12 新增部分。")

    heading(doc, "3.9 企查查批量回传（2026-11-11/12，零新增采购）", size=12)
    table(doc, ["数据集", "规模", "口径说明"], [
        ["ods_qcc_judicial（司法违约）", "34 条 / 6 户", "失信 1、被执行 1、终本 32；三类金额（涉案/执行标的/未履行）分列带 amount_kind，禁止相加"],
        ["ods_qcc_person（主要人员）", "2,079 行 / 490 户", "清洗后口径；原始 2,569 行含 490 行表头脏行已剔除；为并列层来源明细，非图节点（图内人员＝发明人）"],
        ["ods_qcc_shareholder（出资人股东）", "2,247 行", "出资人 / 股东信息（旧版列为完全缺失，已补齐）"],
        ["ods_qcc_trademark（商标明细）", "8,004 条 / 444 家", "尼斯 1–45 类；is_registered 5,257 条与主表「商标_有效件数」守恒；状态 39 种保留原文"],
        ["ods_qcc_annual（年报）", "500 行", "年报核心字段快照"],
        ["ods_qcc_cert（认证）/ ods_qcc_honor（荣誉）", "10,242 / 3,216 条", "认证与荣誉贴源明细"],
        ["ods_qcc_axis_a_snapshot", "500 行 × 17 列", "用户主表本轮 A 轴 17 列原始快照对照"],
    ], widths=[Pt(150), Pt(90), Pt(230)])

    heading(doc, "3.10 融资事件与对照集（旧版列为完全缺失，已补齐）", size=12)
    table(doc, ["数据集", "规模", "口径说明"], [
        ["fact_funding_event（融资事件）", "506 条 / 182 户", "轮次 / 金额（145 条）/ 机构 / 事件年 506/506 全填；估值 post_valuation 0/506 登记为缺口"],
        ["ext_msme_sample（MSME 对照集）", "1,707 家", "Kaggle US MSBE 数据集已入库（旧版写 0）"],
        ["投资机构", "572 家", "完整入图（182 户有融资事件）"],
    ], widths=[Pt(150), Pt(90), Pt(230)])

    heading(doc, "四、缺失数据（完全缺失）")
    para(doc, "与 2026-10-28 版相比，原 17 项完全缺失中已补齐：融资事件、出资人/股东、MSME 对照集、国家级小巨人与 L1/L3 名单（对照定位用途已降级）。当前仅余 2 项硬缺口：")
    table(doc, ["缺失数据", "缺口规模", "缺失原因（数据层面）"], [
        ["研发投入", "500 / 500", "公开渠道未采集；企业非上市，无财报"],
        ["专利被引次数", "18,109 / 18,109 条", "官方渠道不公开引用数据（G-GRAPH 相邻缺口同源）"],
    ], widths=[Pt(120), Pt(100), Pt(250)])
    para(doc, "注册资本 / 参保人数 / 营业收入 / 员工人数仍为 500/500 缺失（政府信用平台不公示、非上市无财报），视为结构性不可得，不计入硬缺口台账。")

    heading(doc, "五、缺失数据（部分缺失）")
    table(doc, ["字段", "已有", "缺失", "缺口比例"], [
        ["统一社会信用代码", "474", "26", "5.2%"],
        ["成立日期 / 成立年限", "463", "37", "7.4%"],
        ["法定代表人", "458", "42", "8.4%"],
        ["风险合规 8 类", "463", "37", "7.4%"],
        ["专利权利要求", "934 条", "17,175 条", "94.9%"],
        ["专利法律状态", "959 条", "17,150 条", "94.7%"],
        ["专利代理机构", "883 条", "17,226 条", "95.1%"],
        ["融资事件金额 amount_cny", "145 / 506", "361", "71.3%（MNAR）"],
        ["融资估值 post_valuation", "0 / 506", "506", "100%"],
    ], widths=[Pt(140), Pt(80), Pt(80), Pt(80)])
    para(doc, "说明：专利明细类缺口集中在张丽晶提供的 17,150 条记录上——那批数据含摘要但无权利要求、无法律状态、无代理机构；本人采集的 959 条这些字段齐全。")

    heading(doc, "六、标准数据库落地情况（42 张表，schema v2.2）")
    para(doc, "数据库 zjtech_credit.db 共 42 张表 + 37 个视图（合计 1,081 列），按 META/ODS/DIM/FACT/BRIDGE/DWS/ADS/EXT 八层组织，全部有数据；版本自 v1.0（2026-11-05）起在 meta_schema_version 留有 13 条升版台账，当前 v2.2（2026-11-12）。")
    table(doc, ["层", "表数", "代表表与规模"], [
        ["META", "3", "meta_schema_version 13 条升版台账；meta_data_source / meta_ingest_log"],
        ["ODS", "11", "ods_patent_raw 18,109；ods_qcc_* 7 张（商标 8,004 / 认证 10,242 / 荣誉 3,216 / 股东 2,247 / 人员 2,079 / 司法 34 / 年报 500）"],
        ["DIM", "6", "dim_enterprise 500；dim_ipc 11,616；dim_inventor 15,955；dim_product_nice_class 45"],
        ["FACT", "5", "fact_patent 32,969；fact_funding_event 506；fact_qualification 1,281；fact_patent_citation 0（被引缺口，见四）"],
        ["BRIDGE", "3", "bridge_patent_ipc / bridge_patent_inventor / bridge_enterprise_product 1,418"],
        ["DWS", "3", "dws_tech_feature / dws_growth_feature / dws_fusion_feature"],
        ["ADS", "9", "ads_label 307；ads_graph_node 61,584；ads_graph_edge 171,210；ads_graph_node_ts / ads_graph_edge_ts（v2.2 时序图）；ads_feature_asof_funding 614"],
        ["EXT", "1", "ext_msme_sample 1,707"],
    ], widths=[Pt(55), Pt(45), Pt(370)])
    para(doc, "关键演进：v1.0（27 表建库）→ v1.2 专利去重 → v1.4 异质图 5 类节点 → v1.7 分档基线 → v1.9 标签方向纠正与多标签 → v2.0 A 轴司法/主要人员并列层 → v2.1 商标 + 产品节点并列层 → v2.2 时序图（详见第七章）。")

    heading(doc, "七、图谱与时序（新增章节）")

    heading(doc, "7.1 六类实体覆盖", size=12)
    table(doc, ["实体", "图内节点数", "来源明细", "覆盖企业", "状态"], [
        ["企业", "500", "500", "500", "完整入图"],
        ["专利", "32,969", "32,969", "500", "完整入图（三源并集，公开号去重）"],
        ["投资机构", "572", "572", "182", "完整入图（182 户有融资事件）"],
        ["人员（发明人）", "15,955", "2,079*", "490", "完整入图（图内=发明人；*企查查主要人员 2,079 人为并列层明细，非图节点）"],
        ["领域（IPC）", "11,588", "11,588", "500", "完整入图（IPC 小类承担技术域语义）"],
        ["产品", "（未入主图）", "35", "433", "类别粒度部分闭合：尼斯 35 个产品类 / 433 户；10 个服务类不作节点；SKU 级产品仍缺（G-GRAPH-01=partial_closed）"],
    ], widths=[Pt(80), Pt(70), Pt(60), Pt(55), Pt(205)])
    para(doc, "主图合计 61,584 节点 / 171,210 边（5 类节点 × 5 类边：invented_by / belongs_ipc / files_patent / funded_by / tech_similar）。A 轴主要人员 2,079 人为并列层（v_six_entity_coverage 口径）。")

    heading(doc, "7.2 时序图（v2.2：静态单快照 → 29 年年度切片）", size=12)
    table(doc, ["指标", "数值", "说明"], [
        ["ads_graph_node_ts", "61,584 行", "节点时序化率 100%，按建元年归入年度切片"],
        ["ads_graph_edge_ts", "171,210 行", "边时序化 162,461 / 171,210 = 94.9%"],
        ["时间跨度", "1992–2026（29 帧）", "29 个年度时间切片"],
        ["funded_by 时序化", "583 / 583 = 100%", "由融资事件日期补到（year_source=funding_event_year），零外部采集"],
        ["tech_similar 例外", "8,749 条", "标 global_snapshot 全期相似度快照（TF-IDF 余弦 top-20，无时点语义）"],
        ["year_source 溯源", "5 类标记", "patent_app_year / edge_min_year / establish_year / global_snapshot / funding_event_year"],
        ["一致性检查", "三项全过", "边端点缺节点 0；边年份早于节点建元 0；year_source 全部标记"],
        ["G-GRAPH-02", "etl_completed", "由 source_missing 改判已修复；fallback 保留 tech_similar 标 global_snapshot"],
    ], widths=[Pt(110), Pt(110), Pt(250)])
    para(doc, "诚实说明（2020 跳点）：belongs_ipc 边 2019 年仅 45 条、2020 年突增至 8,157 条——主因是张丽晶那批专利明细只带 2020 年起申请窗口的 IPC，属采集窗口造成的结构性跳点，不是真实技术布局突变；跨年比较时须注明。")
    para(doc, "口径红线：v2.2 严格未改 ads_graph_node / ads_graph_edge / v_graph_slice_annual* / 任何标签与已发布视图；29 帧时序切面与守恒校验不动；年度切片一律走视图，禁止 UPDATE 物理表。")

    heading(doc, "八、数据口径与约定")
    table(doc, ["事项", "约定"], [
        ["缺失值标记", "统一写为字符串 NAN，读取时 na_values=[\"NAN\"]"],
        ["专利数双口径", "「29,392 件」= 全时段含共同申请；「17,422 条」= 2020–2025 窗口且仅归属本企业；两者不可相加"],
        ["专利明细去重", "按申请号去重，交集 513 条；并集 18,109 条；库内唯一口径走 v_patent_master（30,103 件）"],
        ["高企双口径", "本人 491 户（2021–2025）/ 张丽晶 469 户（2023–2025）；合并值 493 户"],
        ["标签方向", "正例 = 好企业 = 资质存续 286 家（label_value=0）；负例 = 断档 21 家；训练入口 v_model_train_ready_credit"],
        ["产品节点", "尼斯 35 个产品类（非 45）；未并入主图；SKU 级仍缺"],
        ["主要人员", "清洗后 2,079 人 / 490 户（主表「高管_人数」为未清洗口径，保留对照）"],
        ["商标口径", "库内以贴源明细重算 n_brand_exact；主表 9 列只作外部对照，禁止混用"],
        ["代号校验", "信用代码全部通过 GB 32100-2015 校验位算法"],
        ["时点", "成立年限按 2026-10-28 计算；时序图切片至 2026"],
    ], widths=[Pt(90), Pt(380)])

    heading(doc, "九、数据文件清单")
    table(doc, ["文件", "内容", "规模"], [
        ["合并_500家企业数据总表.xlsx", "合并主表 + 产品节点_尼斯35类 + 六类实体覆盖", "501 × 153（3 sheet：主表 153 列、产品 46×7、覆盖 7×6）"],
        ["合并_500家_企业主表.csv", "同主表 CSV 版", "500 行"],
        ["合并_专利明细_并集.csv", "专利明细（带来源标记）", "18,109 条 × 17 列"],
        ["500家_数据覆盖矩阵.xlsx", "本人采集矩阵 + 汇总页", "500 × 47"],
        ["500家_工商信息 / 信用档案 / 舆情汇总.csv", "工商 / 8 类合规 / 网络足迹", "各 500 行"],
        ["500家_BingRSS条目.csv", "提及明细", "1,746 条"],
        ["zjtech_credit.db", "标准数据库（42 表 + 37 视图 / 1081 列，v2.2）", "VACUUM INTO 一致快照"],
        ["数据字典.csv / .md", "全库逐列字典", "1,081 行"],
        ["datasets/高企名单_*.csv", "高企备案名单解析结果", "13 份 / 约 10 万条"],
    ], widths=[Pt(150), Pt(150), Pt(170)])

    para(doc, "（全文完）", color=GRAY, align=WD_ALIGN_PARAGRAPH.CENTER)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    doc.save(out_path)
    print("saved:", out_path)
    print("paragraphs:", len(doc.paragraphs), "tables:", len(doc.tables))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    build(args.out)
