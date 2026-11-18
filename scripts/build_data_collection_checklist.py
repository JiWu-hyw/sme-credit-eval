# -*- coding: utf-8 -*-
"""生成《新增2500家数据采集清单》Word（2026-11-16）。

数据依据：
- _inbox/浙江专精特新中小企业_3000家名单（原500家+新增2500家）.xlsx（表格引擎实测统计）
- 原500家已有数据面（_backend 交付库 v2.2 / 合并主表 153 列）
- 项目 P0/P2 待办与字段缺口登记册（研发投入、专利被引为仅余两项硬缺口）

用法：python scripts/build_data_collection_checklist.py [--out 路径]
"""
import argparse
import os

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(BACKEND, "data", "docs", "新增2500家数据采集清单.docx")

BLUE = RGBColor(0x1F, 0x4E, 0x79)
GRAY = RGBColor(0x59, 0x59, 0x59)
RED = RGBColor(0xC0, 0x00, 0x00)


def set_font(run, size=10.5, bold=False, color=None, name="微软雅黑"):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color is not None:
        run.font.color.rgb = color


def para(doc, text, size=10.5, bold=False, color=None, align=None):
    p = doc.add_paragraph()
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

    para(doc, "新增 2500 家企业数据采集清单", size=18, bold=True, color=BLUE,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "—— 名单扩样 500 → 3000：采什么、去哪采、按什么顺序", size=12, color=GRAY,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "编制日期：2026-11-16　|　名单文件：浙江专精特新中小企业_3000家名单（原500家+新增2500家）.xlsx", size=10, color=GRAY,
         align=WD_ALIGN_PARAGRAPH.CENTER)

    heading(doc, "一、名单盘点（已实测核对）")
    table(doc, ["项", "结果"], [
        ["名单结构", "「原500家」500 行 +「新增2500家」2500 行 +「来源说明」；7 列（序号/企业名称/认定批次/名单类型/地市/地市判定/数据来源）"],
        ["与原样本重合", "0 家（两 sheet 企业名零交集；新增内部零重复）"],
        ["新增批次构成", "2023 第一批 941 / 2024 第一批 938 / 2024 第二批 29 / 2025 第一批 592"],
        ["地市分布", "杭州 1291 / 嘉兴 189 / 湖州 159 / 台州 124 / 温州 108 / 绍兴 106 / 衢州 62 / 金华 11 / 舟山 6 / 丽水 6 / 待补 438"],
        ["地市判定来源", "官方公示文件 1559 / 按名称县区推断 503（回测准确率 99.89%→100%）/ 待补 438"],
        ["名单质量", "企业名均为工商全称，可直接用于专利检索（申请人）与企查查批量查询；名单本身无需再采集（除 438 家地市待补）"],
    ], widths=[Pt(90), Pt(380)])

    heading(doc, "二、采集总原则（承接原 500 家口径）")
    para(doc, "原 500 家已有数据面 = 工商 / 资质 / 专利统计 / 专利明细 / 风险合规 / 网络舆情 / 企查查七类（司法·人员·股东·商标·年报·认证·荣誉）/ 融资事件。新增 2500 家按「同一套口径补齐」，保证 3000 家同构可合并；已定稿的分发口径（全部公开、零新增采购）与预算红线（数据购置 ≤¥3,000，企查查议题已关闭）继续适用。")
    para(doc, "优先级排序逻辑：① 主键与资质是合并前提（P0）；② 专利与企查查七类是模型主粮（P1）；③ 舆情与对照集是增益项（P2）；④ 研发投入/专利被引为公认不可得（不采集，登记在案）。")

    heading(doc, "三、采集清单（按优先级）")

    heading(doc, "P0 主键与资质层（合并前提，最先做）", size=12)
    table(doc, ["数据", "来源", "规模预估", "说明"], [
        ["统一社会信用代码", "企查查批量查询（名称→代码）", "2500 家", "跨源合并主键；原 500 家实测 94.8% 可取到，缺失以企业名兜底"],
        ["专精特新认定信息（批次/文号/有效期）", "名单自带批次 + 省经信厅公示原文", "2500 家", "名单已含批次；有效期 3 年自 2023-05 / 2024 / 2025 各批起算，用于资质存续标签"],
        ["高企认定名单比对（是否高企/认定年度）", "高企认定工作网 13 份 PDF（约 10 万条，已解析）", "2500 家匹配", "零新增采集：用已入库的高企全量名单反向匹配即可"],
        ["地市补全（438 家待补）", "按企业名称县区推断规则（99.89%→100%）", "438 家", "名单内自查；影响分层抽样与地市对照"],
    ], widths=[Pt(110), Pt(120), Pt(70), Pt(170)])

    heading(doc, "P1 模型主粮层（技术要素 + A/D 轴锚点）", size=12)
    table(doc, ["数据", "来源", "规模预估", "说明"], [
        ["专利统计（总数/发明/有效/IPC 广度等 14 字段）", "官方专利统计接口（同原 500 家通道）", "2500 家 × 14 字段", "技术要素主特征；按申请人=企业全称检索"],
        ["专利明细（申请号/日期/IPC/摘要等 17 字段）", "同上，明细口径", "预估 12–16 万条（按原 500 家均值 66 件/家外推）", "时序图与 IPC 网络的原料；含申请年即可入时序切片"],
        ["企查查·司法（失信/被执行/终本）", "企查查批量查询", "预估覆盖 ~98%", "A 轴硬结果锚点（y_hard_outcome 判据）"],
        ["企查查·主要人员", "企查查批量查询", "预估 ~10,000 人", "并列层明细（非图节点）；剔除表头脏行口径沿用"],
        ["企查查·股东出资人", "企查查批量查询", "预估 ~11,000 行", "原 500 家 2247 行（~4.5 行/家）"],
        ["企查查·商标（尼斯 1–45 类）", "企查查批量查询", "预估 ~40,000 条", "产品节点并列层（35 产品类）；is_registered 口径沿用"],
        ["企查查·年报/认证/荣誉", "企查查批量查询", "年报 2500 / 认证 ~50,000 / 荣誉 ~16,000", "D 轴与成长轨迹增益字段"],
        ["融资事件（轮次/金额/机构/日期）", "开源融资数据整合（同原 500 家通道）", "预估 ~2,500 条（182/500 家外推）", "成长轨迹主特征 + funded_by 边时序化（funding_event_year）"],
    ], widths=[Pt(110), Pt(120), Pt(70), Pt(170)])

    heading(doc, "P2 增益层（按余力做）", size=12)
    table(doc, ["数据", "来源", "规模预估", "说明"], [
        ["工商信息（成立日期/法代/状态/注册地址）", "省政府信用平台", "预估覆盖 ~93%", "成立年限与建元年（时序图 establish_year）"],
        ["风险合规 8 类记录条数", "省政府信用平台", "预估覆盖 ~93%", "A 轴过程量锚点（y_risk_free 判据）"],
        ["网络足迹与舆情（BingRSS）", "搜索引擎 RSS", "2500 家", "0 篇占比预计与原 500 家相近（~63%）；采集耗时与限流主要瓶颈"],
        ["上市关联匹配", "财经数据接口", "少数几家", "同原 500 家口径（2 上市 + 7 疑似子公司）"],
    ], widths=[Pt(110), Pt(120), Pt(70), Pt(170)])

    heading(doc, "不采集（登记在案的硬缺口，与原 500 家一致）", size=12)
    table(doc, ["数据", "定性与处置"], [
        ["研发投入", "公开渠道不可得（非上市无财报）；不采集，缺口登记册在案"],
        ["专利被引次数", "官方渠道不公开；不采集，G-GRAPH 相邻缺口同源"],
        ["注册资本 / 参保人数 / 营业收入 / 员工人数", "政府信用平台不公示；结构性不可得，不计入硬缺口台账"],
    ], widths=[Pt(160), Pt(310)])

    heading(doc, "四、工作量与顺序建议")
    table(doc, ["顺序", "动作", "预估工作量", "产出"], [
        ["1", "名单入 ODS（新增 ods_roster_3000 表，含批次/地市/判定列）+ 438 家地市补全", "半天", "3000 家统一主名单"],
        ["2", "企查查批量查询 P0+P1 全套（信用代码→司法→人员→股东→商标→年报→认证→荣誉）", "3–5 天（限流为主）", "七类 ODS 表扩到 3000 家"],
        ["3", "专利统计 + 明细（按申请人批量检索）", "2–3 天", "fact_patent 扩容 ~16 万条"],
        ["4", "高企名单反向匹配（零采集）", "1 小时", "资质层标签"],
        ["5", "融资事件整合", "1 天", "fact_funding_event 扩容"],
        ["6", "入库后统一重建：特征 → 标签 → 图谱（含 29 帧时序）→ 字典 → 守卫基线", "2–3 天", "库升版（预期 v3.0），全量守卫重跑"],
    ], widths=[Pt(35), Pt(200), Pt(80), Pt(155)])
    para(doc, "关键提醒：① 扩样只增加同类标签的数量、不增加维度（v1.9 已定稿），不要为扩样重开标签设计；② 新增 2024/2025 批次的资质有效期起点不同，做资质存续标签时须按批次分别起算；③ 2500 家是「同批次同口径扩样」，入库走并列层原则——不动原 500 家已发布对象，扩容表升版处理。")

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
