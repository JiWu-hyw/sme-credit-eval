# -*- coding: utf-8 -*-
"""重建《数据现状说明_有什么与缺什么.docx》—— v3.0 全量扩样版（2026-11-18）。

数据口径来源（全部取自库内实测，可逐项复核）：
- zjtech_credit.db：42 表 / 38 视图 / 1,109 列（表 659 + 视图 450），schema v3.0
- meta_schema_version 升版台账（v1.0 → v3.0 共 14 条）
- dim_enterprise 3000 / ads_label 1675 / ads_model_dataset 3000 / ads_feature_asof 97,150
- ads_graph_node 177,840 / ads_graph_edge 285,043 / 时序 32 帧
- v_enterprise_grade_l0 五档 A167/B470/C2155/D154/E54
- v_feature_cohort_gap 分队列判别力台账

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
    para(doc, "3,000 家专精特新数据现状说明", size=18, bold=True, color=BLUE,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "—— 数据层面：有什么、缺什么（v3.0 全量扩样版）", size=12, color=GRAY,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "编制日期：2026-11-18　|　口径：字段级 + 库表级 + 图谱级 + 标签级", size=10, color=GRAY,
         align=WD_ALIGN_PARAGRAPH.CENTER)

    table(doc, ["项目", "说明"], [
        ["数据主体", "浙江省专精特新中小企业 3,000 家（原 2023 年度第一批 500 家 + 名单扩样新增 2,500 家）"],
        ["数据来源", "① 本人采集（政府公开平台 / 官方接口 / 搜索引擎 RSS）；② 同学张丽晶提供的两份 Excel；③ 企查查批量回传 3,000 家全量（专利 / 商标 / 资质 / 股东 / 高管 / 荣誉 / 司法 / 融资）；④ 2021–2025 五年高新技术企业备案名单（标签差分依据）。均为零新增采购"],
        ["数据格式", "CSV（UTF-8-SIG）+ XLSX + SQLite 标准库，缺失值统一标记为 NAN"],
        ["合并方式", "以「企业名称」为主键，经现用名 / 曾用名 / 归一名称三键桥接；3,000 户信用代码 100% 齐备"],
        ["统计截止", "2026-11-18"],
        ["标准数据库", "zjtech_credit.db（42 表 + 38 视图 / 1,109 列，schema v3.0，314 MB）"],
        ["图数据规模", "五类实体 177,840 节点 + 285,043 边，时序化 32 个年度切片（1992–2026）"],
    ], widths=[Pt(90), Pt(380)])

    heading(doc, "一、文档说明")
    para(doc, "本文只做数据盘点和缺口说明，不做分析结论、不涉及用途评价。所有数字均取自实际数据文件与标准数据库，可逐项复核。")
    para(doc, "说明：文中「覆盖」指该字段有非空值的户数（总体 3,000 户）；「NAN」为约定的缺失标记，可直接被 pandas 识别（na_values=[\"NAN\"]）。")
    para(doc, "本版相对 2026-11-12（v2.2，500 家）版的主要变化：① 样本由 500 家扩至 3,000 家，dim_enterprise 由 63 列扩至 79 列"
              "（新增上市关联 10 列 + 融资机构分级 6 列）；② 资质存续标签由 307 家扩至 1,675 家（口径 v3.0 五年名单差分）；"
              "③ 训练表物理扩至 3,000 行、可入模 1,675 家（原 500 家时代的 305 家锚定视图保留作对照）；"
              "④ 异质图由 61,584 节点 / 171,210 边扩至 177,840 / 285,043；⑤ 时序切面由 29 帧扩至 32 帧；⑥ 全库 1,109 列。", color=None)
    note(doc, "重要口径：扩样后「全量」与「锚定」并存——司法 A 轴、主要人员、高企资质等原受限项已可全量；"
              "上市关联（10 列）、融资机构分级（6 列）、高企证书编号（4 列）三项锚定字段已于 v3.0 落库扩至全量；"
              "仅剩 3 类字段（IPC 技术广度、网络足迹 8 列、信用浙江风险合规 4 列）仍只对原 500 家可得，"
              "在扩样表中一律留 NULL，不作估算回填（见第四节）。")

    heading(doc, "二、数据总览")
    table(doc, ["模块", "字段数", "覆盖情况（3,000 户口径）", "数据来源"], [
        ["① 主体与主键", "3", "3,000 户全部；信用代码 3,000", "名单 + 企查查画像"],
        ["② 资质与政策", "13", "专精特新 3,000；高企记录 493 户 / 781 条", "省经信厅公示 + 张丽晶认定档案 + 五年备案名单"],
        ["③ 专利·统计", "14", "2,972 户有专利申请", "官方专利统计接口 + 企查查"],
        ["④ 专利·明细", "17", "145,413 行 / 142,547 件（申请号去重）", "三源并集（贴源 ods_patent_raw 62,217 行 + 张丽晶批次 + 企查查）"],
        ["⑤ 工商信息", "8", "注册资本 2,999；参保人数 2,998；成立日期 2,963", "省政府信用平台 + 企查查"],
        ["⑥ 风险合规", "8", "463 户（仅原 500 家可得，扩样新家为空）", "省政府信用平台"],
        ["⑦ 网络足迹与舆情", "10", "500 户（仅原 500 家可得；媒体 36）", "搜索引擎 RSS"],
        ["⑧ 上市关联", "10", "有上市关联 88 户（A 股 5 / 新三板 68）；3,000 户分类标记齐备", "财新 / 交易所公开信息"],
        ["⑨ 企查查回传", "17+", "商标 2,618 户 / 人员 2,953 户 / 股东 2,999 户 / 司法 72 户 / 年报·认证·荣誉 500 户", "企查查批量查询"],
        ["⑩ 融资事件", "6", "1,982 笔 / 574 户", "开源融资数据 + 企查查整合"],
        ["⑪ 对照数据集", "—", "MSME 1,707 家；高企备案名单 106,446 行 / 14 期公告", "Kaggle / 高企认定工作网"],
    ], widths=[Pt(105), Pt(40), Pt(180), Pt(145)])

    heading(doc, "三、已有数据（字段级）")
    para(doc, "3.1–3.8（主体主键 / 资质政策 / 专利统计与明细 / 工商 / 风险合规 / 网络舆情 / 上市关联）结构未变、覆盖已按 3,000 户更新（见第二章），"
              "此处只列扩样后口径变化最大的三组：企查查回传、融资与对照集、名单扩样新增字段。")

    heading(doc, "3.9 企查查批量回传（3,000 家全量口径）", size=12)
    table(doc, ["数据集", "规模", "口径说明"], [
        ["ods_qcc_judicial（司法违约）", "289 条 / 72 户", "失信 / 被执行 / 终本三类合并；三类金额（涉案 / 执行标的 / 未履行）分列带 amount_kind，禁止相加"],
        ["ods_qcc_person（主要人员）", "13,935 行 / 2,953 户", "清洗后口径（原 500 家部分剔除 490 行表头脏行）；为并列层来源明细，非图节点（图内人员＝发明人）"],
        ["ods_qcc_shareholder（出资人股东）", "15,297 行 / 2,999 户", "出资人 / 股东信息"],
        ["ods_qcc_trademark（商标明细）", "57,224 条 / 2,618 户", "尼斯 1–45 类；is_registered 口径与主表「商标_有效件数」守恒；状态原文保留"],
        ["ods_qcc_cert（认证）/ ods_qcc_honor（荣誉）", "63,637 / 21,000 条", "认证与荣誉贴源明细，3,000 家口径"],
        ["ods_qcc_annual（年报）", "500 行", "年报核心字段快照（仅原 500 家有该来源）"],
        ["ods_qcc_axis_a_snapshot", "500 行 × 17 列", "A 轴 17 列原始快照对照"],
    ], widths=[Pt(150), Pt(90), Pt(230)])

    heading(doc, "3.10 融资事件与对照集", size=12)
    table(doc, ["数据集", "规模", "口径说明"], [
        ["fact_funding_event（融资事件）", "1,982 笔 / 574 户", "双来源血缘：zj_500family 506 笔 + qcc_funding_3000 1,476 笔；轮次与事件年全填，金额 352 笔有值，估值 0 笔（登记为缺口）"],
        ["ads_feature_asof_funding（融资旁路特征）", "3,350 行 / 1,675 家", "cohort 与 due_minus_1 双时点口径，m_funding / n_funding_events / investor_prestige_best/_latest"],
        ["ext_msme_sample（MSME 对照集）", "1,707 家", "Kaggle US MSBE 数据集，仅作外部对照，不入模"],
        ["投资机构（图节点）", "1,884 个", "由融资事件机构名归一，funded_by 边 1,972 条"],
    ], widths=[Pt(150), Pt(90), Pt(230)])

    heading(doc, "3.11 名单扩样新增字段（dim_enterprise +16 列）", size=12)
    table(doc, ["字段组", "列", "覆盖（新增 2,500 家 / 原 500 家）", "说明"], [
        ["上市关联（10 列）", "上市状态 / 股票代码 / 股票简称 / 上市主体全称 / 交易市场 / 上市关联类型 / 上市板块 / 是否A股上市 / 是否新三板 / 上市状态_原",
         "分类列 2,500 / 500；股票代码 66 / 22", "A 股 5 户（含宇树科技 688836）、新三板 68 户；「是否上市」为分类列，全家有值"],
        ["融资机构分级（6 列）", "融资_最高机构级别 / 融资_级别分值 / 融资_顶级机构数 / 融资_机构匹配数 / 融资_机构活跃度 / 融资_国资基金数",
         "有机构级别 373 / 73；其余 2,500 / 500", "机构等级 1–4 分级；446 户匹配到机构；未匹配者记 0，不记 NAN"],
    ], widths=[Pt(85), Pt(150), Pt(105), Pt(130)])

    heading(doc, "四、缺失数据（完全缺失 / 按设计留空）")
    para(doc, "当前「源头就没有」的完全缺失只剩 2 项；另有 6 类字段属「扩样新家不可得」，明确留 NULL、不作估算回填。")
    table(doc, ["缺失数据", "缺口规模", "缺失原因（数据层面）"], [
        ["研发投入", "3,000 / 3,000", "公开渠道未采集；企业非上市，无财报"],
        ["专利被引次数", "145,413 / 145,413 行", "官方渠道不公开引用数据（fact_patent_citation 恒 0 行）"],
        ["营业收入 / 净利润", "2,988 / 3,000", "仅 12 户有值（原 500 家中带年报者）；非上市无财报"],
    ], widths=[Pt(120), Pt(110), Pt(240)])
    table(doc, ["仍锚定字段（仅原 500 家）", "全量覆盖", "留空原因"], [
        ["IPC 技术广度（tech_breadth_entropy / n_ipc_subclass 等）", "500 / 3,000", "专利导出无 IPC；需 CNIPA/专利之星批查，唯一值得投入的补采项"],
        ["网络足迹 8 列（官网 / 百科 / 第三方页 / 新闻 / 订单等）", "500 / 3,000", "未对 3,000 家采集，且实测弱信号，建议放弃"],
        ["信用浙江风险合规 4 列（行政处罚 / 严重失信 / 经营异常 / 信用承诺）", "463 / 3,000", "逐户查询有验证码，3,000 家不可行；部分可由 A 轴行政处罚替代"],
    ], widths=[Pt(200), Pt(80), Pt(190)])
    note(doc, "已扩全量的原锚定项（v3.0 落库 dim_enterprise，不再留 NULL）：上市关联 10 列（88 户关联，A 股 5 / 新三板 68）、"
              "融资机构分级 6 列（446 户）、高企证书编号 4 列（编号 2,505/3,000 = 83.5%，认定次数 3,000/3,000）；"
              "融资深度金额类字段（累计净增/单轮最大/可解析占比）仍受 MNAR 限制（352/1,982 = 17.8%）。")
    note(doc, "处置原则：以上 3 类在扩样表（ads_model_dataset 新增行）中一律为 NULL，不填推测值、不用均值/中位数插补；"
              "训练时的缺失处理在算法端统一做，库内保持「有就是有、没有就是空」。")

    heading(doc, "五、缺失数据（部分缺失）")
    table(doc, ["字段", "已有", "缺失", "缺口比例"], [
        ["成立日期 / 成立年限", "2,963", "37", "1.2%"],
        ["法定代表人", "2,958", "42", "1.4%"],
        ["经营状态 biz_status", "2,963", "37", "1.2%"],
        ["注册资本", "2,999", "1", "0.03%"],
        ["参保人数", "2,998", "2", "0.07%"],
        ["国民经济行业（门类）", "2,889", "111", "3.7%"],
        ["专利权利要求", "—", "占明细 94.9%", "仅本人采集的 959 条齐全"],
        ["专利法律状态", "—", "占明细 94.7%", "张丽晶批次无此字段"],
        ["专利代理机构", "—", "占明细 95.1%", "张丽晶批次无此字段"],
        ["融资事件金额 amount_cny", "352 / 1,982", "1,630", "82.2%（MNAR）"],
        ["融资估值 post_valuation", "0 / 1,982", "1,982", "100%"],
    ], widths=[Pt(140), Pt(90), Pt(80), Pt(85)])
    para(doc, "说明：专利明细类缺口集中在张丽晶提供的批次上——那批数据含摘要但无权利要求、无法律状态、无代理机构；本人采集的部分这些字段齐全。")

    heading(doc, "六、标准数据库落地情况（42 表 + 38 视图 / 1,109 列，schema v3.0）")
    para(doc, "数据库 zjtech_credit.db 共 42 张表（659 列）+ 38 个视图（450 列），合计 1,109 列，按 META/ODS/DIM/FACT/BRIDGE/DWS/ADS/EXT/LOG 九层组织，全部有数据；"
              "版本自 v1.0（2026-11-05）起在 meta_schema_version 留有 14 条升版台账，当前 v3.0（2026-11-18）。")
    table(doc, ["层", "表数", "代表表与规模"], [
        ["META", "3", "meta_schema_version 14 条升版台账；meta_data_source / meta_ingest_log"],
        ["ODS", "11", "ods_patent_raw 62,217；ods_qcc_* 7 张（商标 57,224 / 认证 63,637 / 荣誉 21,000 / 股东 15,297 / 人员 13,935 / 司法 289 / 年报 500）；ods_qual_company_raw 106,446 / ods_qual_notice 14"],
        ["DIM", "6", "dim_enterprise 3,000 × 79 列；dim_ipc 11,616；dim_inventor 15,955；dim_product_nice_class 45；dim_qual_type；dim_enterprise_alias"],
        ["FACT", "5", "fact_patent 145,413（142,547 件）；fact_funding_event 1,982；fact_qualification 3,781；fact_qualification_event 779；fact_patent_citation 0（被引缺口，见四）"],
        ["BRIDGE", "3", "bridge_patent_ipc / bridge_patent_inventor / bridge_enterprise_product 1,418（433 户）"],
        ["DWS", "3", "dws_tech_feature / dws_growth_feature / dws_fusion_feature"],
        ["ADS", "9", "ads_label 1,675；ads_model_dataset 3,000；ads_feature_asof 97,150；ads_feature_asof_funding 3,350；ads_graph_node 177,840 / ads_graph_edge 285,043；ads_graph_node_ts / ads_graph_edge_ts；ads_survival_period"],
        ["EXT", "1", "ext_msme_sample 1,707"],
        ["LOG", "1", "log_match 名称匹配审计日志"],
    ], widths=[Pt(50), Pt(40), Pt(380)])
    para(doc, "关键演进：v1.0（27 表建库）→ v1.2 专利去重 → v1.4 异质图 5 类节点 → v1.7 分档基线 → v1.9 标签方向纠正与多标签 → "
              "v2.0 A 轴司法/主要人员并列层 → v2.1 商标 + 产品节点并列层 → v2.2 时序图 → v3.0 名单扩样 3,000 家（详见第七、八章）。")

    heading(doc, "七、图谱与时序")

    heading(doc, "7.1 五类实体覆盖", size=12)
    table(doc, ["实体", "图内节点数", "来源明细", "覆盖企业", "状态"], [
        ["企业", "3,000", "3,000", "3,000", "完整入图"],
        ["专利", "145,413", "145,413", "2,972", "完整入图（三源并集，申请号去重 142,547 件）"],
        ["投资机构", "1,884", "1,884", "574", "完整入图（funded_by 边 1,972 条）"],
        ["人员（发明人）", "15,955", "13,935*", "2,953", "完整入图（图内＝发明人；*企查查主要人员为并列层明细，非图节点）"],
        ["领域（IPC）", "11,588", "11,588", "—", "完整入图（IPC 小类承担技术域语义，ID 已剥 WIPO 版本号后缀）"],
        ["产品", "（未入主图）", "35 类", "433", "类别粒度部分闭合：尼斯 35 个产品类 / 433 户；10 个服务类不作节点；SKU 级仍缺（G-GRAPH-01=partial_closed）"],
    ], widths=[Pt(78), Pt(70), Pt(60), Pt(55), Pt(207)])
    para(doc, "主图合计 177,840 节点 / 285,043 边（5 类节点 × 5 类边：files_patent 144,647 / invented_by 77,899 / belongs_ipc 51,776 / tech_similar 8,749 / funded_by 1,972）。")

    heading(doc, "7.2 时序图（32 个年度切片）", size=12)
    table(doc, ["指标", "数值", "说明"], [
        ["ads_graph_node_ts", "177,840 行", "节点时序化率 100%，按建元年归入年度切片"],
        ["ads_graph_edge_ts", "285,043 行", "全部带 year_source 标记；其中 274,322 条（96.2%）真正按专利年份时序化"],
        ["时间跨度", "1992–2026（32 帧）", "另有 -1 未定时点桶 1,340 个节点（无年份来源）"],
        ["year_source 构成", "3 类标记", "patent_app_year 274,322（files_patent / belongs_ipc / invented_by）/ global_snapshot 10,138 / funding_event_year 583"],
        ["funded_by 时序化", "583 / 1,972 = 29.6%", "只有这部分补到融资事件年；其余 1,389 条无事件年，回退标 global_snapshot，不进年度切面视图"],
        ["tech_similar 例外", "8,749 条", "标 global_snapshot 全期相似度快照（TF-IDF 余弦 top-20，无时点语义）；global_snapshot 桶 10,138 = 8,749 + 1,389"],
        ["年度切面视图覆盖", "3 类边 / 274,322 条", "v_graph_slice_annual_edge 只覆盖专利相关三类边，不含 funded_by / tech_similar"],
        ["一致性检查", "三项全过", "边端点缺节点 0；边年份早于节点建元 0；year_source 全部标记"],
        ["G-GRAPH-02", "etl_completed", "fallback 保留 tech_similar 标 global_snapshot"],
    ], widths=[Pt(105), Pt(115), Pt(250)])
    para(doc, "诚实说明（2020 跳点）：belongs_ipc 边 2019 年仅 45 条、2020 年突增至 8,157 条——主因是张丽晶那批专利明细只带 2020 年起申请窗口的 IPC，"
              "属采集窗口造成的结构性跳点，不是真实技术布局突变；跨年比较时须注明。")
    para(doc, "口径红线：v3.0 未改 ads_graph_node / ads_graph_edge 的结构与主图语义；年度切片一律走 v_graph_slice_annual* 视图，禁止 UPDATE 物理表。")

    heading(doc, "八、标签与训练集（v3.0 全量口径）")

    heading(doc, "8.1 资质存续标签", size=12)
    table(doc, ["项目", "数值", "说明"], [
        ["标签定义", "高企资格到期未见重新认定", "五年备案名单差分（2021→2024 / 2022→2025），definition_ver=qual_survival_proxy_v3.0"],
        ["可标注样本", "1,675 家", "3,000 家中名单命中 2,906，其中 1,675 家观察期届满可判定"],
        ["正例（好企业）", "1,568 家（label_value=0）", "资质存续"],
        ["负例（断档）", "107 家（label_value=1）", "断档率 6.4%"],
        ["队列分布", "2021 队列 796 家（5.8%）/ 2022 队列 879 家（6.9%）", "两队列断档率接近，可合并训练"],
        ["五档分布", "A 167 / B 470 / C 2,155 / D 154 / E 54", "L0 规则基线，全量 3,000 家；E 档为真实注销/失信企业"],
    ], widths=[Pt(100), Pt(160), Pt(210)])
    note(doc, "样本选择效应（必须在论文中明示）：专精特新样本的断档率 6.4%，显著低于母池的 36.2%——"
              "不是这批企业更抗风险，而是专精特新认定门槛本身就筛掉了大部分会断档的企业。跨抽样比较断档率没有意义。")

    heading(doc, "8.2 训练集与切分", size=12)
    table(doc, ["项目", "数值", "说明"], [
        ["ads_model_dataset", "3,000 行 × 39 列", "物理扩到全量；excluded 1,325 家（观察期未满或无标签）"],
        ["可入模", "1,675 家", "train 1,165（正 70）/ val 251（正 13）/ test 259（正 24）"],
        ["ads_feature_asof", "97,150 行 / 1,675 家", "长表切面特征，从 fact_patent 现算，不依赖 dws_*"],
        ["as-of 宽表", "3,350 行", "cohort 与 due_minus_1 双时点各 1,675 行"],
        ["锚定对照视图", "v_model_train_ready_credit 305 家", "原 500 家时代基线，保留用于新旧口径对照，不参与主训练"],
    ], widths=[Pt(100), Pt(150), Pt(220)])

    heading(doc, "8.3 判别力现状（诚实记录）", size=12)
    table(doc, ["特征", "全量 AUC", "2021 队列", "2022 队列", "队列差", "判定"], [
        ["inv_patent_ratio", "0.4985", "0.5334", "0.4716", "0.062", "队列间基本一致（≈随机，无判别力）"],
        ["core_staff_stability", "0.6224", "0.7978", "0.4532", "0.345", "队列间方向不一致，全样本 AUC 不可单独引用"],
        ["grant_rate", "0.5035", "0.5008", "0.5170", "0.016", "队列间基本一致（无判别力）"],
        ["n_patent_app", "0.4279", "0.4648", "0.3912", "0.074", "队列间基本一致（反向弱信号）"],
    ], widths=[Pt(110), Pt(60), Pt(60), Pt(60), Pt(45), Pt(135)])
    para(doc, "必须写明的事实：扩到 1,675 家后，inv_patent_ratio 的全量 AUC 从 500 家时代的 0.576 掉到 0.4985（≈随机），"
              "两队列的差距也从 0.338 收敛到 0.062。原因不是 bug：原 500 家时代的「队列反差」是抽样噪声，"
              "扩样后消失；判别力真正消失的根源是专精特新群体在专利结构上高度同质化——同为专精特新，"
              "发明占比这个指标无法再区分谁会在三年后掉出高企名单。论文与汇报不得再引用 0.87/0.58 一类旧数字。", color=RED)

    heading(doc, "九、数据口径与约定")
    table(doc, ["事项", "约定"], [
        ["缺失值标记", "统一写为字符串 NAN，读取时 na_values=[\"NAN\"]；库内则存 NULL"],
        ["专利数双口径", "145,413 行 = 三源并集明细；142,547 件 = 申请号去重（v_patent_master 口径）；禁止用 COUNT(*) fact_patent 当企业专利数"],
        ["标签方向", "正例 = 好企业 = 资质存续 1,568 家（label_value=0）；负例 = 断档 107 家（label_value=1）；训练入口 v_model_train_ready"],
        ["样本分层", "全量 3,000（视角 A）/ 原 500 锚定（视角 B）；两套视图并列，不混算、不跨层比较"],
        ["产品节点", "尼斯 35 个产品类（非 45）；未并入主图；SKU 级仍缺"],
        ["主要人员", "清洗后 13,935 人 / 2,953 户（原 500 家部分保留未清洗口径作对照）"],
        ["商标口径", "库内以贴源明细重算 n_brand_exact；主表 9 列只作外部对照，禁止混用"],
        ["上市关联", "「是否上市」为分类列（全家有值）；股票代码等明细只对 88 户有值，不得当覆盖率指标"],
        ["时点", "成立年限按 2026-11-18 计算；时序图切片至 2026"],
    ], widths=[Pt(90), Pt(380)])

    heading(doc, "十、数据文件清单")
    table(doc, ["文件", "内容", "规模"], [
        ["zjtech_credit.db", "标准数据库（42 表 + 38 视图 / 1,109 列，v3.0）", "314 MB（VACUUM INTO 一致快照）"],
        ["数据字典.csv / .md", "全库逐列字典", "1,109 行"],
        ["3000家_建模主表_含标签.csv", "建模主表（dim + 标签 + 关键特征）", "3,000 × 113"],
        ["字段范围清单_全量3000_锚定500.xlsx", "逐字段标注全量/锚定 + 覆盖率 + 来源", "159 字段"],
        ["model_train_ready.csv", "算法端唯一入口（可入模 1,675 家）", "1,675 × 17"],
        ["3000家_标签_名单差分.csv", "五年名单差分标签底稿", "3,000 行"],
        ["标签分布与五档分档检验报告_v3.0.docx", "标签分布、卡方与趋势检验、分档一致性", "含同源性声明"],
        ["新增2500家数据采集清单.docx", "扩样批次采集口径与字段对照", "—"],
        ["datasets/高企名单_*.csv", "高企备案名单解析结果", "14 期公告 / 106,446 行"],
    ], widths=[Pt(150), Pt(160), Pt(160)])

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
