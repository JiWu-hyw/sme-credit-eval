# -*- coding: utf-8 -*-
"""500 家专精特新交付数据 → 标准数据库（schema v1.0）加载入口。

本脚本是「浙江中小科技企业信用智能评估」项目当前唯一的权威数据入口。
它把 _data_collect/out 合并产出的三份交付 CSV 灌入 data/db/zjtech_credit.db：

  - 合并_500家_企业主表.csv   → dim_enterprise(含企业画像补充列) + fact_qualification
                              + dws_tech_feature / dws_growth_feature / dws_fusion_feature
  - 合并_专利明细_并集.csv     → fact_patent + dim_inventor + bridge_patent_inventor
                              + bridge_patent_ipc（+ dim_ipc 字典同步填充）
  - 合并_融资事件流水.csv     → fact_funding_event

设计要点
--------
1. 复用 db.py 的规范化口径（norm_company_name / make_enterprise_id /
   make_inventor_id），保证企业主数据与专利匹配、资质判定同源。
2. 企业代理键 enterprise_id = 'ENT' + md5(name_norm)[:12]，确定性生成，
   与 schema 契约完全一致；专利按公开公告号去重（INSERT OR IGNORE）。
3. 缺失统一记作 'NAN' / 空串 → None（不写脏值）；注册资本/参保人数/专利被引/
   研发投入等真缺口诚实置 NULL（见合并说明_数据清单与缺口.md）。
4. 只建表一次：先 db.init_db()（幂等），再灌数据；重跑安全。
   存量库若缺特征层所需的列，由 _ensure_feature_columns 幂等 ALTER 补齐
   （schema.sql 用 IF NOT EXISTS 不会改已有表）。
5. 主表为 2026 年横截面快照，dws 特征表按 (enterprise_id, stat_year) 粒度，
   统一以 SNAPSHOT_YEAR=2026 作为快照年写入，与按年管道产出的同表特征可并存查询。
6. 默认数据源目录 = 仓库 _backend/data/raw/（已镜像合并 CSV）；
   可用 --src-dir 指向 _data_collect/out 或其它位置。
7. 数据灌装完成后，在同一事务链内构建「企业技术相似图谱」
   （ads_graph_node / ads_graph_edge，见 build_tech_graph），
   使图谱纳入统一 ETL —— 重跑本脚本即自动重建图谱，无需再跑独立脚本；
   --skip-graph 可临时跳过（仅灌数据）。

用法
----
    python -m src.data.load_500family                 # 用默认 raw 目录灌库
    python -m src.data.load_500family --src-dir ../../_data_collect/out
    python -m src.data.load_500family --db PATH --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 复用同目录 db.py 的规范化与建库能力
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import db as dbmod  # noqa: E402

# _backend 目录（api.py / db.py 实际数据根）
BACKEND_ROOT = HERE.parent.parent                      # _backend/src/data -> _backend
DB_PATH = BACKEND_ROOT / "data" / "db" / "zjtech_credit.db"
RAW_DIR = BACKEND_ROOT / "data" / "raw"               # 合并 CSV 的本地权威副本

# 主表是 2026 年采集的横截面快照，特征均为「截至采集时」的累计/当前值。
# 按 schema 约定 dws 表以 (enterprise_id, stat_year) 为粒度，这里统一以
# SNAPSHOT_YEAR 作为快照年写入，与按年管道产出的同表特征可并存查询。
SNAPSHOT_YEAR = 2026

MASTER_CSV = "合并_500家_企业主表.csv"
PATENT_CSV = "合并_专利明细_并集.csv"
FUNDING_CSV = "合并_融资事件流水.csv"

# 地市 → 行政区划代码（浙江省）
CITY_CODE = {
    "杭州市": "330100", "宁波市": "330200", "温州市": "330300", "嘉兴市": "330400",
    "湖州市": "330500", "绍兴市": "330600", "金华市": "330700", "衢州市": "330800",
    "舟山市": "330900", "台州市": "331000", "丽水市": "331100",
}
PROV_CODE = "330000"

# 文献类型 → patent_type 枚举
# 注：企查查把"发明"拆成"发明公布 / 发明授权"，与主数据"发明"口径不同，
# 但二者均属发明专利，统一映射到 invention（跨源并集统计时按申请号归并已在 union 阶段处理）。
DOC_TYPE_MAP = {"发明": "invention", "实用新型": "utility", "外观设计": "design",
                "发明公布": "invention", "发明授权": "invention"}

# 融资事件类型 → fact_funding_event.event_type 枚举
FUND_EVENT_MAP = [
    (("ipo",), ("ipo", "上市")),
    (("seasoned_equity",), ("增资",)),
    (("private_placement",), ("新增机构股东",)),
]

# —— 技术相似图谱构建参数（统一 ETL 一环；scripts/build_tech_graph.py 共用）——
GRAPH_TIME_SLICE = 0       # 0 = 全期累计切片
GRAPH_JACCARD_MIN = 0.10   # 企业间技术相似连边的最小 Jaccard 相似度
GRAPH_SHARED_MIN = 2       # 连边所需的最少共享 IPC 小类数

# 与 schema.sql 一致的图谱表定义（含 tech_similar 枚举；仅存量旧库需要重建时使用）
GRAPH_EDGE_DDL = """
CREATE TABLE IF NOT EXISTS ads_graph_edge (
    src_type   TEXT NOT NULL CHECK (src_type IN ('enterprise','patent','ipc','inventor')),
    src_id     TEXT NOT NULL,
    dst_type   TEXT NOT NULL CHECK (dst_type IN ('enterprise','patent','ipc','inventor')),
    dst_id     TEXT NOT NULL,
    edge_type  TEXT NOT NULL CHECK (edge_type IN
                 ('files_patent','belongs_ipc','co_inventor','cites','qualifies','tech_similar')),
    time_slice INTEGER NOT NULL,
    weight     REAL,
    PRIMARY KEY (src_type, src_id, dst_type, dst_id, edge_type, time_slice)
) STRICT;
"""
GRAPH_NODE_DDL = """
CREATE TABLE IF NOT EXISTS ads_graph_node (
    node_type    TEXT NOT NULL CHECK (node_type IN ('enterprise','patent','ipc','inventor')),
    node_id      TEXT NOT NULL,
    time_slice   INTEGER NOT NULL,
    display_name TEXT,
    PRIMARY KEY (node_type, node_id, time_slice)
) STRICT;
"""


def _is_nan(v) -> bool:
    return v is None or str(v).strip() in ("", "NAN", "nan", "NaN", "NULL", "null")


def _clean(v):
    return None if _is_nan(v) else str(v).strip()


def _to_int(v):
    v = _clean(v)
    if v is None:
        return None
    m = re.search(r"-?\d+", v.replace(",", ""))
    return int(m.group()) if m else None


def _to_float(v):
    v = _clean(v)
    if v is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
    return float(m.group()) if m else None


def _parse_date(v):
    """'2020.09.11' / '2020-09-11' / '2020/09/11' → '2020-09-11'。"""
    v = _clean(v)
    if v is None:
        return None
    parts = re.split(r"[.\-/]", v)
    if len(parts) == 3:
        y, m, d = parts
        try:
            return "%04d-%02d-%02d" % (int(y), int(m), int(d))
        except ValueError:
            return None
    return None


def _year_of(v):
    d = _parse_date(v)
    return int(d[:4]) if d else None


def _first_token(v, sep=";"):
    v = _clean(v)
    if v is None:
        return None
    tok = v.split(sep)[0].strip()
    return tok or None


def _split_ipc(v):
    """'H01M8/18;H01M4/96;H01M4/86' → ['H01M8/18','H01M4/96','H01M4/86']。

    仅保留形如 [A-H]+数字 开头的合法 IPC 片段；不按 '/' 切分（'/' 是小组分隔符）。
    """
    v = _clean(v)
    if not v:
        return []
    out = []
    for tok in re.split(r"[;；,， ]+", v):
        tok = tok.strip().upper()
        if tok and re.match(r"^[A-H]\d", tok):
            out.append(tok)
    return out


def _ipc_subclass(code):
    """IPC 完整码 → 小类（section+class+subclass，4 字符，如 'H01M'）。"""
    if not code or len(code) < 4:
        return None
    m = re.match(r"^([A-H]\d{2}[A-Z])", code)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# IPC 分类法名称字典（WIPO 标准 IPC 第8/9版，部/类层级稳定）
# 仅含部(1)与类(3)两级权威中文名；小组/小类行取其所属类名作为可用标题。
# ---------------------------------------------------------------------------
IPC_SECTION_TITLE = {
    "A": "人类生活必需", "B": "作业；运输", "C": "化学；冶金",
    "D": "纺织；造纸", "E": "固定建筑物", "F": "机械工程；照明；加热；武器；爆破",
    "G": "物理", "H": "电学",
}
IPC_CLASS_TITLE = {
    # A 部
    "A01": "农业；林业；畜牧业；狩猎；诱捕；捕鱼",
    "A21": "焙烤；食用面团", "A22": "屠宰；肉品处理；家禽或鱼的加工",
    "A23": "其他类不包含的食品或食料；及其处理", "A24": "烟草；雪茄；纸烟；吸烟者用品",
    "A41": "服装", "A42": "帽类制品", "A43": "鞋类", "A44": "男用服饰用品；珠宝",
    "A45": "手携物品或旅行品", "A46": "刷类制品",
    "A47": "家具；家庭用的物品或设备；咖啡磨；香料磨；一般吸尘器",
    "A61": "医学或兽医学；卫生学", "A62": "救生；消防",
    "A63": "运动；游戏；娱乐活动", "A99": "本部中其他类目不包含的技术主题",
    # B 部
    "B01": "一般的物理或化学的方法或装置", "B02": "破碎、磨粉或粉碎；谷物碾磨的预处理",
    "B03": "用液体或用风力摇床或风力跳汰机选矿；其他选矿或除砾石的方法或装置",
    "B04": "用于实现物理或化学工艺过程的离心装置或离心机",
    "B05": "一般喷射或雾化；对表面涂覆液体或其他流体的一般方法",
    "B06": "一般机械振动的发生或传递", "B07": "将固体从固体中分离；分选",
    "B08": "清洁", "B09": "固体废物、污染物或废物的处理或处置；分离的一般方法或装置",
    "B21": "基本上无切削的金属机械加工；金属冲压", "B22": "铸造；冶金",
    "B23": "机床；其他类目中不包括的金属加工", "B24": "磨削；抛光",
    "B25": "手动工具；轻便机动工具；手动器械的手柄；一般标准的连接或固定装置；车间设备；铆接",
    "B26": "手动切割工具；切口；打孔",
    "B27": "木材或类似材料的加工或保存；一般钉钉机或钉U形钉机",
    "B28": "加工水泥、黏土或石料", "B29": "塑料的加工；一般处于塑性状态物质的加工",
    "B30": "压力机", "B31": "纸品制作；纸的加工", "B32": "层状产品",
    "B41": "印刷；排版机；打字机；模印机", "B42": "装订；图册；文件夹；特种印刷品",
    "B43": "书写或绘图器具；办公用品", "B44": "装饰艺术",
    "B60": "一般车辆", "B61": "铁路", "B62": "无轨陆用车辆",
    "B63": "船舶或其他水上船只；与船有关的设备",
    "B64": "飞行器；航空；宇宙航行",
    "B65": "输送；包装；贮存；搬运薄的或细丝状材料",
    "B66": "卷扬；提升；牵引", "B67": "开启或封闭瓶子、罐或类似的容器；液体的贮运",
    "B68": "鞍具；家具罩面",     "B81": "微观结构技术", "B82": "纳米技术", "B33": "增材制造（3D打印）",
    "B99": "本部中其他类目不包含的技术主题",
    # C 部
    "C01": "无机化学", "C02": "水、废水、污水或污泥的处理",
    "C03": "玻璃；矿棉或渣棉", "C04": "水泥；混凝土；人造石；陶瓷；耐火材料",
    "C05": "肥料；肥料制造", "C06": "炸药；火柴", "C07": "有机化学",
    "C08": "有机高分子化合物；其制备或化学加工；以其为基料的组合物",
    "C09": "染料；涂料；抛光剂；天然树脂；黏合剂；其他类目不包含的组合物；其他类目不包含的材料的应用",
    "C10": "石油、煤气及炼焦工业；含一氧化碳的工业气体；燃料；润滑剂；泥煤",
    "C11": "动物或植物油、脂、脂肪物质或蜡；由此制取的脂肪酸；洗涤剂；蜡烛",
    "C12": "生物化学；啤酒；酒精；果汁酒；醋；微生物学；酶学；突变或遗传工程",
    "C13": "糖工业", "C14": "小原皮；大原皮；毛皮；皮革",
    "C21": "铁的冶金", "C22": "冶金；黑色或有色金属合金；合金或有色金属的处理",
    "C23": "对金属材料的镀覆；用金属材料对材料的镀覆；表面化学处理；金属材料的扩散处理；真空蒸发法、溅射法、离子注入法或化学气相沉积法的一般镀覆；金属材料腐蚀或积垢的一般抑制",
    "C25": "电解或电泳工艺；其所用设备", "C30": "晶体生长", "C40": "组合技术",
    "C99": "本部中其他类目不包含的技术主题",
    # D 部
    "D01": "天然或人造的线或纤维；纺纱或纺丝",
    "D02": "纱线；纱线或绳索的机械整理；整经或络经", "D03": "织造",
    "D04": "编织；花边制作；针织；饰带；无纺织物",
    "D05": "缝纫；绣花；簇绒", "D06": "织物等的处理；洗涤；其他类不包括的柔性材料",
    "D07": "绳；除电缆以外的缆索", "D21": "造纸；纤维素的生产",
    "D99": "本部中其他类目不包含的技术主题",
    # E 部
    "E01": "道路、铁路或桥梁的建筑", "E02": "水利工程；基础；疏浚",
    "E03": "给水；排水", "E04": "建筑物", "E05": "锁；钥匙；门窗零件；保险箱",
    "E06": "一般门、窗、百叶窗或卷帘；梯子", "E21": "土层或岩石的钻进；采矿",
    "E99": "本部中其他类目不包含的技术主题",
    # F 部
    "F01": "一般机器或发动机；一般的发动机装置；蒸汽机",
    "F02": "燃烧发动机；热气或燃烧生成物的发动机装置",
    "F03": "液力机械或液力发动机；风力、弹力或重力发动机；其他类目中不包括的产生反推力的发动机装置",
    "F04": "液体变容式机械；液体泵或弹性流体泵",
    "F15": "流体压力执行机构；一般液压技术和气动技术",
    "F16": "工程元件或部件；为产生和保持机器或设备的有效运行的一般措施；一般绝热",
    "F17": "气体或液体的贮存或分配", "F21": "照明",
    "F22": "蒸汽的发生", "F23": "燃烧设备；燃烧方法", "F24": "供热；炉灶；通风",
    "F25": "制冷或冷却；加热和制冷的联合系统；热泵系统；冰的制造或储存；气体的液化或固化",
    "F26": "干燥", "F27": "炉；窑；烘烤炉；蒸馏炉", "F28": "一般热交换",
    "F41": "武器", "F42": "弹药；爆破", "F99": "本部中其他类目不包含的技术主题",
    # G 部
    "G01": "测量；测试", "G02": "光学",
    "G03": "摄影术；电影术；利用了光波以外其他波的类似技术；电记录术；全息摄影术",
    "G04": "测时学", "G05": "控制器的常规控制；调节", "G06": "计算；推算；计数",
    "G07": "核算装置", "G08": "信号装置",
    "G09": "教育；密码术；显示；广告；印鉴", "G10": "乐器；声学",
    "G11": "信息存储", "G12": "仪器的零部件",     "G21": "核物理；核工程", "G16": "特别适用于特定应用领域的信息通信技术（ICT）",
    "G99": "本部中其他类目不包含的技术主题",
    # H 部
    "H01": "基本电气元件", "H02": "发电、变电或配电",
    "H03": "基本电子电路", "H04": "电通信技术",     "H05": "其他类目不包含的电技术", "H10": "半导体器件；其他类目未包括的电固体器件",
    "H99": "本部中其他类目不包含的技术主题",
}


def _ipc_parent_code(code, level):
    """按 IPC 层级推导父级码：小组(5)→小类，小类(4)→类，类(3)→部，部(1)→NULL。"""
    if level >= 5:
        return _ipc_subclass(code)        # 'H01M8/18' → 'H01M'
    if level == 4:
        return code[:3]                   # 'H01M' → 'H01'
    if level == 3:
        return code[0]                    # 'H01' → 'H'
    return None


def _ipc_title(code, level, section, klass):
    """取该 IPC 行可用的分类法名称：部行用部名，其余用所属类名（最权威可用）。"""
    if level == 1 and section in IPC_SECTION_TITLE:
        return IPC_SECTION_TITLE[section]
    return IPC_CLASS_TITLE.get(klass)


def _split_years(v):
    v = _clean(v)
    if not v:
        return []
    out = []
    for tok in re.split(r"[/、,， ]+", v):
        y = _to_int(tok)
        if y and 1990 <= y <= 2100:
            out.append(y)
    return out


def _prestige_rank(v):
    """机构级别 'A/C' → 最佳等级（A=1,B=2,C=3,D=4）。"""
    v = _clean(v)
    if not v:
        return None
    best = None
    for ch in v.upper():
        if ch == "A":
            best = 1
        elif ch == "B" and best is None:
            best = 2
        elif ch == "C" and best is None:
            best = 3
        elif ch == "D" and best is None:
            best = 4
    return best


def _fund_event_type(v):
    v = _clean(v) or ""
    low = v
    for enum, keys in FUND_EVENT_MAP:
        if any(k.lower() in low.lower() for k in keys):
            return enum[0]
    return "other"


def _investor_list(v):
    v = _clean(v)
    if not v:
        return None
    names = re.split(r"[；;]+", v)
    out = []
    for n in names:
        n = re.sub(r"\[.*?\]", "", n).strip()  # 去掉 [投资机构] 等标签
        if n:
            out.append(n)
    return json.dumps(out, ensure_ascii=False) if out else None


def _yesno(v):
    """'是'→1 / '否'→0 / 其它(含空/NAN)→None。"""
    v = _clean(v)
    if v is None:
        return None
    if v == "是":
        return 1
    if v == "否":
        return 0
    return None


def load_master(conn: sqlite3.Connection, path: Path, verbose: bool = True):
    """企业主表 → dim_enterprise + fact_qualification。"""
    ent_by_norm = {}
    ent_rank = {}          # enterprise_id → max_qual_rank（供 dws 特征层引用）
    n_ent = n_qual = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = _clean(row.get("企业名称"))
            if not name:
                continue
            name_norm = dbmod.norm_company_name(name)
            eid = dbmod.make_enterprise_id(name_norm)

            city = _clean(row.get("地市")) or ""
            region_code = CITY_CODE.get(city, PROV_CODE)
            credit = _clean(row.get("统一社会信用代码"))
            is_listed = 1 if (str(row.get("上市关联") or "").startswith("上市")
                              or (row.get("融资_是否上市") or "") == "是") else 0
            stock = None
            if is_listed:
                m = re.search(r"\d{6}", str(row.get("上市关联") or ""))
                if m:
                    stock = m.group()

            # 资质层级
            spec_year = _to_int(row.get("专精特新认定年度"))
            ht_years = _split_years(row.get("高企认定年度(合并)"))
            if (row.get("是否高企") or "").strip() == "是" and not ht_years:
                # 仅有“是否高企=是”但无合并年度，给一个占位（避免漏标 high_tech）
                pass
            is_giant = (row.get("国家级小巨人") or "").strip() == "是"

            ranks = set()
            if is_giant:
                ranks.add(4)
            if spec_year:
                ranks.add(3)
            if ht_years:
                ranks.add(2)
            max_rank = max(ranks) if ranks else 0
            if len(ranks) > 1:
                etype = "multi"
            elif max_rank == 4:
                etype = "little_giant"
            elif max_rank == 3:
                etype = "specialized"
            elif max_rank == 2:
                etype = "high_tech"
            else:
                etype = "unknown"

            qual_years = [y for y in ([spec_year] if spec_year else []) + ht_years if y]
            first_year = min(qual_years) if qual_years else None
            last_year = max(qual_years) if qual_years else None

            # 企业画像补充列（工商/信用/在线存在/资讯；注册资本·参保人数多为 NAN，诚实置 NULL）
            found_date = _parse_date(row.get("成立日期"))
            found_years = _to_float(row.get("成立年限(年)"))
            legal_rep = _clean(row.get("法定代表人"))
            biz_status = _clean(row.get("经营状态"))
            reg_capital = _to_float(row.get("注册资本"))
            insured_num = _to_int(row.get("参保人数"))
            admin_penalty_cnt = _to_int(row.get("行政管理条数"))
            serious_dishonesty_cnt = _to_int(row.get("严重失信条数"))
            biz_anomaly_cnt = _to_int(row.get("经营异常条数"))
            credit_promise_cnt = _to_int(row.get("信用承诺条数"))
            has_website = _yesno(row.get("有企业官网"))
            has_wiki = _yesno(row.get("有百科词条"))
            has_thirdparty_page = _yesno(row.get("有第三方企业信息页"))
            news_cnt = _to_int(row.get("资讯条数"))
            order_award_cnt = _to_int(row.get("订单中标"))
            honor_cnt = _to_int(row.get("荣誉资质"))
            risk_negative_cnt = _to_int(row.get("风险负面"))

            conn.execute(
                """INSERT OR REPLACE INTO dim_enterprise
                   (enterprise_id, name_display, name_norm, credit_code, region_code,
                    region_name, enterprise_type, first_qual_year, last_qual_year,
                    max_qual_rank, is_listed, stock_code, patent_cnt, created_at, updated_at,
                    found_date, found_years, legal_rep, biz_status, reg_capital, insured_num,
                    admin_penalty_cnt, serious_dishonesty_cnt, biz_anomaly_cnt, credit_promise_cnt,
                    has_website, has_wiki, has_thirdparty_page, news_cnt, order_award_cnt,
                    honor_cnt, risk_negative_cnt)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, name, name_norm, credit, region_code, city or None, etype,
                 first_year, last_year, max_rank, is_listed, stock,
                 _to_int(row.get("专利总数(我方_全时段)")),
                 dbmod.now_iso(), dbmod.now_iso(),
                 found_date, found_years, legal_rep, biz_status, reg_capital, insured_num,
                 admin_penalty_cnt, serious_dishonesty_cnt, biz_anomaly_cnt, credit_promise_cnt,
                 has_website, has_wiki, has_thirdparty_page, news_cnt, order_award_cnt,
                 honor_cnt, risk_negative_cnt),
            )
            ent_by_norm[name_norm] = eid
            ent_rank[eid] = max_rank
            n_ent += 1

            # 专精特新资质事实
            if spec_year:
                batch = _clean(row.get("专精特新认定批次")) or ("specialized-%d" % spec_year)
                conn.execute(
                    """INSERT OR IGNORE INTO fact_qualification
                       (qual_id, enterprise_id, qual_type, batch_no, year, publish_date,
                        region_code, status, is_renewal, valid_until, match_score)
                       VALUES (NULL,?,?,?,?,?,?,?,0,?,1.0)""",
                    (eid, "specialized", batch, spec_year, None, region_code,
                     "announced", str(spec_year + 3)))
                n_qual += 1
            # 高企资质事实（合并年度可能多值）
            for y in sorted(set(ht_years)):
                conn.execute(
                    """INSERT OR IGNORE INTO fact_qualification
                       (qual_id, enterprise_id, qual_type, batch_no, year, publish_date,
                        region_code, status, is_renewal, valid_until, match_score)
                       VALUES (NULL,?,?,?,?,?,?,?,0,?,1.0)""",
                    (eid, "high_tech", "high_tech-%d" % y, y, None, region_code,
                     "announced", str(y + 3)))
                n_qual += 1

    conn.commit()
    if verbose:
        print("[master] dim_enterprise = %d, fact_qualification += %d" % (n_ent, n_qual))
    return ent_by_norm, ent_rank


def load_patents(conn: sqlite3.Connection, path: Path, ent_by_norm: dict, verbose: bool = True):
    """专利明细 → fact_patent + dim_inventor + bridge_patent_inventor + bridge_patent_ipc（+ dim_ipc）。"""
    n_pat = n_inv = n_match = n_ipc = 0
    seen_pat = set()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 兼容两套表头：新并集 CSV 用「公开号」，旧主数据用「公开公告号」
            pub = _first_token(row.get("公开公告号")) or _first_token(row.get("公开号"))
            if not pub:
                pub = _first_token(row.get("申请号"))
            if not pub or pub in seen_pat:
                continue
            seen_pat.add(pub)

            app_no = _first_token(row.get("申请号"))
            # 兼容「发明名称」（旧）/「专利名称」（新并集）
            title = _clean(row.get("发明名称")) or _clean(row.get("专利名称"))
            abstract = _clean(row.get("摘要"))
            app_date = _parse_date(row.get("申请日"))
            app_year = _year_of(row.get("申请日"))
            pub_date = _parse_date(_first_token(row.get("公开公告日")))
            ptype = DOC_TYPE_MAP.get(_clean(row.get("文献类型")) or "", None)
            legal = _clean(row.get("法律状态"))

            # ⚠️ 本列语义 = 「此刻仍在权」，不是「是否曾获批授权」。
            #   「未缴年费专利权终止 / 期限届满专利权终止 / 失效 / 放弃」这些状态
            #   描述的是**授权之后**的失效，落在这里会被判成 0，于是
            #   「授权后失效」与「从未授权」被混成一类（全库 7,940 件，占已授权 33.5%）。
            #   需要「曾授权」请用 v_patent_canonical.is_granted_ever
            #   （依据公告号 kind code：B/U/S/C 均为授权公告，存在即授权）。
            #   本次不改这里：源列语义一旦静默变更，历史报表与已交付结论都会失稳；
            #   正确做法是在视图层派生，留待下次全量重灌 ETL 时一并订正。
            if legal and any(k in legal for k in ("授权", "有效")):
                is_granted = 1
            elif legal and any(k in legal for k in ("驳回", "失效", "撤回", "放弃", "终止")):
                is_granted = 0
            elif pub and "B" in pub:
                is_granted = 1
            else:
                # schema 规定 is_granted NOT NULL：待决/未明一律记为 0，
                # 原始法律状态仍保留在 legal_status 文本列，可回溯区分
                is_granted = 0

            ipc_codes = _split_ipc(row.get("IPC分类号"))
            ipc = ipc_codes[0] if ipc_codes else None   # 主 IPC 仍进 fact_patent.ipc_main
            assignee = _clean(row.get("申请人"))

            # 企业匹配：规范化精确匹配
            enterprise_id = None
            method = None
            conf = None
            if assignee:
                a_norm = dbmod.norm_company_name(assignee)
                if a_norm in ent_by_norm:
                    enterprise_id = ent_by_norm[a_norm]
                    method = "exact"
                    conf = 1.0
                else:
                    # 兜底：申请人为“X公司…”，企业名是其子串
                    for en_norm, eid in ent_by_norm.items():
                        if en_norm and en_norm in a_norm and len(a_norm) - len(en_norm) <= 6:
                            enterprise_id = eid
                            method = "fuzzy"
                            conf = 0.9
                            break

            conn.execute(
                """INSERT OR IGNORE INTO fact_patent
                   (patent_id, app_no, title, abstract, app_date, app_year, pub_date,
                    grant_date, grant_year, patent_type, legal_status, is_granted,
                    country_code, enterprise_id, assignee_raw, ipc_main,
                    match_confidence, match_method, source_id, raw_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)""",
                (pub, app_no, title, abstract, app_date, app_year, pub_date,
                 None, None, ptype, legal, is_granted, "CN", enterprise_id,
                 assignee, ipc, conf, method),
            )
            if enterprise_id:
                n_match += 1
            n_pat += 1

            # 专利-IPC 多值桥表：主 IPC 进 fact_patent.ipc_main，全部 IPC 进 bridge_patent_ipc
            for seq, code in enumerate(ipc_codes, 1):
                subclass = _ipc_subclass(code)
                conn.execute(
                    "INSERT OR IGNORE INTO bridge_patent_ipc"
                    "(patent_id, ipc_code, ipc_subclass, is_main, seq)"
                    " VALUES (?,?,?,?,?)",
                    (pub, code, subclass, 1 if seq == 1 else 0, seq))
                n_ipc += 1
                # 同步填充 IPC 分类字典（含层级父码与分类法名称）
                _lvl = 5 if "/" in code else 4
                conn.execute(
                    "INSERT OR IGNORE INTO dim_ipc"
                    "(ipc_code, ipc_level, ipc_section, ipc_class, ipc_subclass, parent_code, title)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (code, _lvl, code[0], code[:3], subclass,
                     _ipc_parent_code(code, _lvl),
                     _ipc_title(code, _lvl, code[0], code[:3])))

            # 发明人 → dim_inventor + bridge
            inv_raw = _clean(row.get("发明人"))
            if inv_raw:
                inventors = [x.strip() for x in re.split(r"[;；]+", inv_raw) if x.strip()]
                for seq, inv in enumerate(inventors, 1):
                    iid = dbmod.make_inventor_id(dbmod.norm_company_name(inv))
                    conn.execute(
                        "INSERT OR IGNORE INTO dim_inventor"
                        "(inventor_id, name_display, name_norm, addr_hash, first_year, last_year, patent_cnt)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (iid, inv, dbmod.norm_company_name(inv), "", app_year, app_year, 1))
                    conn.execute(
                        "INSERT OR IGNORE INTO bridge_patent_inventor"
                        "(patent_id, inventor_id, seq, is_first) VALUES (?,?,?,?)",
                        (pub, iid, seq, 1 if seq == 1 else 0))
                    n_inv += 1
    conn.commit()
    if verbose:
        print("[patent] fact_patent = %d, matched enterprise = %d, inventor links = %d, ipc links = %d"
              % (n_pat, n_match, n_inv, n_ipc))
    return n_pat, n_match


def load_funding(conn: sqlite3.Connection, path: Path, ent_by_norm: dict, verbose: bool = True):
    """融资事件流水 → fact_funding_event。"""
    n_fund = n_match = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = _clean(row.get("企业名称"))
            if not name:
                continue
            eid = ent_by_norm.get(dbmod.norm_company_name(name))
            if eid:
                n_match += 1
            edate = _parse_date(row.get("事件日期"))
            etype = _fund_event_type(row.get("事件类型"))
            amount = _to_float(row.get("增资额(万元)"))
            amount_cny = amount * 10000 if amount is not None else None
            inv_json = _investor_list(row.get("新增股东"))
            prestige = _prestige_rank(row.get("机构级别"))
            is_pub = 1 if "上市" in (row.get("事件类型") or "") else 0
            conn.execute(
                """INSERT OR IGNORE INTO fact_funding_event
                   (event_id, enterprise_id, event_date, event_year, event_type,
                    amount_cny, round_label, post_valuation, investor_names,
                    prestige_rank, source_id, is_public_co)
                   VALUES (NULL,?,?,?,?,?,?,NULL,?,?,NULL,?)""",
                (eid, edate, _year_of(row.get("事件日期")), etype, amount_cny,
                 _clean(row.get("事件性质")), inv_json, prestige, is_pub))
            n_fund += 1
    conn.commit()
    if verbose:
        print("[funding] fact_funding_event = %d, matched enterprise = %d" % (n_fund, n_match))
    return n_fund, n_match


def log_ingest(conn: sqlite3.Connection, source_id, stage, target, path, rows_in, rows_ok):
    conn.execute(
        """INSERT INTO meta_ingest_log
           (source_id, stage, target_table, file_path, rows_in, rows_ok, rows_bad, status, message)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (source_id, stage, target, str(path), rows_in, rows_ok, rows_in - rows_ok,
         "ok", "500-family delivery load"))


def _ensure_feature_columns(conn: sqlite3.Connection):
    """幂等补齐特征层所需列。

    schema.sql 用 ``CREATE TABLE IF NOT EXISTS``，对**已存在**的库不会自动追加新列；
    因此对存量库（如 data/db/zjtech_credit.db）需要显式 ALTER。新建库走 schema.sql 已含新列。
    重复执行安全：先查 PRAGMA table_info，缺才加。
    """
    # dim_enterprise 企业画像补充列
    dim_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(dim_enterprise)").fetchall()}
    dim_add = [
        ("found_date", "TEXT"), ("found_years", "REAL"), ("legal_rep", "TEXT"),
        ("biz_status", "TEXT"), ("reg_capital", "REAL"), ("insured_num", "INTEGER"),
        ("admin_penalty_cnt", "INTEGER"), ("serious_dishonesty_cnt", "INTEGER"),
        ("biz_anomaly_cnt", "INTEGER"), ("credit_promise_cnt", "INTEGER"),
        ("has_website", "INTEGER"), ("has_wiki", "INTEGER"),
        ("has_thirdparty_page", "INTEGER"), ("news_cnt", "INTEGER"),
        ("order_award_cnt", "INTEGER"), ("honor_cnt", "INTEGER"),
        ("risk_negative_cnt", "INTEGER"),
    ]
    for c, t in dim_add:
        if c not in dim_cols:
            conn.execute("ALTER TABLE dim_enterprise ADD COLUMN %s %s" % (c, t))

    # dws_growth_feature 融资汇总补充列
    g_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(dws_growth_feature)").fetchall()}
    g_add = [
        ("funding_occurred", "INTEGER"), ("funding_rounds", "INTEGER"),
        ("funding_first_date", "TEXT"), ("funding_last_date", "TEXT"),
        ("funding_span_months", "INTEGER"), ("funding_net_increase_wan", "REAL"),
        ("funding_max_round_wan", "REAL"), ("funding_orgs_cnt", "INTEGER"),
        ("funding_prestige_score", "REAL"), ("funding_top_org_level", "INTEGER"),
        ("funding_top_org_name", "TEXT"), ("funding_orgs_list", "TEXT"),
    ]
    for c, t in g_add:
        if c not in g_cols:
            conn.execute("ALTER TABLE dws_growth_feature ADD COLUMN %s %s" % (c, t))
    conn.commit()


def load_features(conn: sqlite3.Connection, path: Path, ent_by_norm: dict,
                  ent_rank: dict, verbose: bool = True):
    """主表特征列 → dws_tech_feature / dws_growth_feature / dws_fusion_feature。

    主表为 2026 横截面快照，统一以 SNAPSHOT_YEAR 作为 stat_year。
    专利/授权率/IPC 广度/发明人/CAGR/融资汇总等可直接映射；被引、研发投入、
    营收 CAGR 等属硬缺口，诚实置 NULL（见合并说明_数据清单与缺口.md）。
    """
    n_tech = n_growth = n_fusion = 0
    sy = SNAPSHOT_YEAR
    now = dbmod.now_iso()
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = _clean(row.get("企业名称"))
            if not name:
                continue
            eid = ent_by_norm.get(dbmod.norm_company_name(name))
            if not eid:
                continue
            rank = ent_rank.get(eid)

            # ---- dws_tech_feature：技术要素特征 ----
            inv_ratio = _to_float(row.get("发明专利占比(我方)"))
            grant_rate = _to_float(row.get("授权率_有效除全部(我方)"))
            ipc_n = _to_int(row.get("技术广度_IPC小类数(我方)"))
            conn.execute(
                """INSERT OR REPLACE INTO dws_tech_feature
                   (enterprise_id, stat_year, inv_patent_ratio, grant_rate,
                    tech_breadth_ipc_class, n_patent_app, n_patent_grant,
                    n_patent_invention, n_patent_utility, n_patent_design,
                    n_inventor, n_ipc_subclass, n_patent_collab, first_app_year,
                    rd_intensity_flag, computed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, sy, inv_ratio, grant_rate, ipc_n,
                 _to_int(row.get("专利总数(我方_全时段)")),
                 _to_int(row.get("有效专利数(我方)")),
                 _to_int(row.get("发明专利数(我方)")),
                 _to_int(row.get("实用新型数(我方)")),
                 _to_int(row.get("外观设计数(我方)")),
                 _to_int(row.get("发明人数(我方)")), ipc_n,
                 _to_int(row.get("其中共同申请(张丽晶)")),
                 _to_int(row.get("首件申请年(我方)")),
                 "missing", now))
            n_tech += 1

            # ---- dws_growth_feature：成长轨迹 + 融资汇总 ----
            top_level = _prestige_rank(row.get("融资_最高机构级别"))
            occurred = _yesno(row.get("融资_是否发生"))
            conn.execute(
                """INSERT OR REPLACE INTO dws_growth_feature
                   (enterprise_id, stat_year, patent_cagr, investor_prestige,
                    funding_occurred, funding_rounds, funding_first_date,
                    funding_last_date, funding_span_months, funding_net_increase_wan,
                    funding_max_round_wan, funding_orgs_cnt, funding_prestige_score,
                    funding_top_org_level, funding_top_org_name, funding_orgs_list,
                    qual_rank, m_funding, computed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, sy, _to_float(row.get("专利申请CAGR(我方)")), top_level,
                 occurred, _to_int(row.get("融资_轮次数")),
                 _parse_date(row.get("融资_首次事件日")),
                 _parse_date(row.get("融资_末次事件日")),
                 _to_int(row.get("融资_事件跨度月")),
                 _to_float(row.get("融资_累计净增万元")),
                 _to_float(row.get("融资_单轮最大万元")),
                 _to_int(row.get("融资_参与机构数")),
                 _to_float(row.get("融资_级别分值")), top_level,
                 _clean(row.get("融资_最高级别机构")),
                 _investor_list(row.get("融资_参与机构名单")),
                 rank, (1 if occurred == 1 else 0), now))
            n_growth += 1

            # ---- dws_fusion_feature：融合特征 ----
            # tech_growth_inter 按 schema 注释用「资质层级 × 专利增长」作代理（需两者皆非 NULL）
            cagr = _to_float(row.get("专利申请CAGR(我方)"))
            inter = (rank * cagr) if (rank is not None and cagr is not None) else None
            conn.execute(
                """INSERT OR REPLACE INTO dws_fusion_feature
                   (enterprise_id, stat_year, tech_growth_inter, computed_at)
                   VALUES (?,?,?,?)""",
                (eid, sy, inter, now))
            n_fusion += 1
    conn.commit()
    if verbose:
        print("[features] dws_tech_feature = %d, dws_growth_feature = %d, "
              "dws_fusion_feature = %d (stat_year=%d)" % (n_tech, n_growth, n_fusion, sy))
    return n_tech, n_growth, n_fusion


# ============================================================================
# 企业技术相似图谱（统一 ETL 的最后一环；scripts/build_tech_graph.py 复用本函数）
# ============================================================================
def _ensure_graph_edge_enum(conn: sqlite3.Connection) -> bool:
    """存量库若 ads_graph_edge 的 CHECK 枚举缺 tech_similar，则重建边表。

    正常路径下 schema.sql 已含该枚举，不做任何 DDL（避免无谓 DROP）。
    返回是否发生了重建。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ads_graph_edge'"
    ).fetchone()
    if row and "tech_similar" in row[0]:
        return False
    conn.execute("DROP TABLE IF EXISTS ads_graph_edge")
    conn.execute(GRAPH_EDGE_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gedge_src "
                 "ON ads_graph_edge(src_type, src_id, time_slice)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gedge_dst "
                 "ON ads_graph_edge(dst_type, dst_id, time_slice)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gedge_typ "
                 "ON ads_graph_edge(edge_type, time_slice)")
    return True


def build_tech_graph(conn: sqlite3.Connection, verbose: bool = True) -> dict:
    """构建企业级「技术相似图谱」，写入 ads_graph_node / ads_graph_edge。

    数据来源（均为本次 ETL 已灌入的表）：
      dim_enterprise / fact_patent / bridge_patent_ipc / dim_ipc
    边语义：
      files_patent  企业→专利   weight = 匹配置信度
      belongs_ipc   专利→IPC    weight = 主分类 1.0 / 副分类 0.5
      tech_similar  企业↔企业   weight = IPC 小类集合的 Jaccard 相似度
        （保留条件：共享小类数 >= GRAPH_SHARED_MIN 且 Jaccard >= GRAPH_JACCARD_MIN）
    time_slice 统一取 GRAPH_TIME_SLICE(0) 表示「全期累计」。

    幂等：运行前清空两张图表。统一 ETL（main）与独立脚本共用本函数，
    保证「重跑加载 = 图谱自动重建」，不再存在游离的手工步骤。
    """
    cur = conn.cursor()
    _ensure_graph_edge_enum(conn)
    conn.execute(GRAPH_NODE_DDL)
    conn.execute("DELETE FROM ads_graph_node")
    conn.execute("DELETE FROM ads_graph_edge")
    conn.commit()

    # ---- 读基础数据（用索引访问，兼容 sqlite3.Row 行工厂）----
    enterprises = cur.execute(
        "SELECT enterprise_id, name_display FROM dim_enterprise").fetchall()
    patents = cur.execute(
        "SELECT patent_id, title, enterprise_id, COALESCE(match_confidence,1.0) "
        "FROM fact_patent").fetchall()
    bridges = cur.execute(
        "SELECT patent_id, ipc_code, ipc_subclass, is_main FROM bridge_patent_ipc").fetchall()
    ipc_title = {r[0]: (r[1] or "") for r in cur.execute(
        "SELECT ipc_code, title FROM dim_ipc").fetchall()}
    if verbose:
        print("[graph][read] enterprise=%d patent=%d bridge=%d ipc_dict=%d"
              % (len(enterprises), len(patents), len(bridges), len(ipc_title)))

    # ---- 节点：enterprise / patent / ipc ----
    node_rows = []
    for e in enterprises:
        node_rows.append(("enterprise", e[0], GRAPH_TIME_SLICE, (e[1] or "")[:60]))
    for p in patents:
        node_rows.append(("patent", p[0], GRAPH_TIME_SLICE, (p[1] or "")[:80]))
    used_ipc = sorted({b[1] for b in bridges})
    for code in used_ipc:
        node_rows.append(("ipc", code, GRAPH_TIME_SLICE, ipc_title.get(code, "")[:60]))
    cur.executemany(
        "INSERT OR IGNORE INTO ads_graph_node "
        "(node_type, node_id, time_slice, display_name) VALUES (?,?,?,?)", node_rows)
    conn.commit()

    # ---- 边：files_patent / belongs_ipc ----
    edge_rows = []
    pat2ent = {}
    for p in patents:
        if p[2]:
            pat2ent[p[0]] = p[2]
            edge_rows.append(("enterprise", p[2], "patent", p[0],
                              "files_patent", GRAPH_TIME_SLICE, float(p[3] or 1.0)))
    for b in bridges:
        edge_rows.append(("patent", b[0], "ipc", b[1], "belongs_ipc",
                          GRAPH_TIME_SLICE, 1.0 if b[3] else 0.5))
    cur.executemany(
        "INSERT OR IGNORE INTO ads_graph_edge "
        "(src_type, src_id, dst_type, dst_id, edge_type, time_slice, weight) "
        "VALUES (?,?,?,?,?,?,?)", edge_rows)
    conn.commit()

    # ---- 企业 → IPC 小类集合 → 两两 Jaccard ----
    ent_subclass = {}
    for b in bridges:
        eid = pat2ent.get(b[0])
        if eid and b[2]:
            ent_subclass.setdefault(eid, set()).add(b[2])
    ent_ids = sorted(ent_subclass)
    sim_rows = []
    for i, a in enumerate(ent_ids):
        sa = ent_subclass[a]
        for b_id in ent_ids[i + 1:]:
            sb = ent_subclass[b_id]
            inter = sa & sb
            if len(inter) < GRAPH_SHARED_MIN:
                continue
            jac = len(inter) / len(sa | sb)
            if jac < GRAPH_JACCARD_MIN:
                continue
            sim_rows.append(("enterprise", a, "enterprise", b_id,
                             "tech_similar", GRAPH_TIME_SLICE, round(jac, 4)))
    cur.executemany(
        "INSERT OR IGNORE INTO ads_graph_edge "
        "(src_type, src_id, dst_type, dst_id, edge_type, time_slice, weight) "
        "VALUES (?,?,?,?,?,?,?)", sim_rows)
    conn.commit()

    nodes_by_type = dict(cur.execute(
        "SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY node_type").fetchall())
    edges_by_type = dict(cur.execute(
        "SELECT edge_type, COUNT(*) FROM ads_graph_edge GROUP BY edge_type").fetchall())
    summary = {
        "time_slice": GRAPH_TIME_SLICE,
        "n_nodes": sum(nodes_by_type.values()),
        "n_edges": sum(edges_by_type.values()),
        "nodes_by_type": nodes_by_type,
        "edges_by_type": edges_by_type,
        "n_enterprises_with_patent": len(ent_ids),
        "jaccard_min": GRAPH_JACCARD_MIN,
        "shared_min": GRAPH_SHARED_MIN,
    }
    if verbose:
        print("[graph] nodes=%d %s / edges=%d %s (参与企业=%d, Jaccard>=%.2f, 共享小类>=%d)"
              % (summary["n_nodes"], nodes_by_type, summary["n_edges"], edges_by_type,
                 len(ent_ids), GRAPH_JACCARD_MIN, GRAPH_SHARED_MIN))
    return summary


def _clear_targets(conn: sqlite3.Connection):
    """重跑前清空本次加载涉及的所有目标表，保证幂等。

    按依赖序（子表先于父表）删除，即便开启外键约束也安全。
    同时清掉本数据源的入库日志，避免 meta_ingest_log 重复累积。
    """
    # ads_graph_* 无外键依赖，先清；它们由本 ETL 末尾的 build_tech_graph 重建，
    # 纳入统一清表范围可保证「重跑 = 全量重建」，不残留上一版图谱。
    # dws 特征表引用 dim_enterprise，须先于父表清空；其余按依赖序
    # bridge_patent_ipc 引用 fact_patent，须先于 fact_patent 清空。
    # ads_feature_asof / ads_model_dataset / ads_label 为后续 as-of/qual 流水线
    # 产出的派生表，均带 enterprise_id → dim_enterprise 外键，须在父表之前清空；
    # 它们会由阶段 B/C 重建，清空不影响基础数据。
    order = ["ads_feature_asof", "ads_model_dataset", "ads_label",
             "ads_graph_edge", "ads_graph_node",
             "dws_fusion_feature", "dws_growth_feature", "dws_tech_feature",
             "bridge_patent_inventor", "bridge_patent_ipc", "fact_patent",
             "fact_qualification", "fact_funding_event", "dim_inventor",
             "dim_enterprise", "dim_ipc"]
    # 派生表随项目演进而新增，可能携带对基础表的外键；清空前临时关闭外键约束，
    # 保证「重跑 = 全量重建」幂等，避免逐一维护清空顺序导致 FK 冲突。
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for t in order:
            conn.execute("DELETE FROM %s" % t)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("DELETE FROM meta_ingest_log WHERE source_id='zj_500family'")
    conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser(description="500 家交付数据 → 标准数据库")
    ap.add_argument("--db", default=str(DB_PATH), help="目标数据库路径")
    ap.add_argument("--src-dir", default=str(RAW_DIR), help="合并 CSV 所在目录")
    ap.add_argument("--dry-run", action="store_true", help="只校验文件存在，不写库")
    ap.add_argument("--skip-graph", action="store_true",
                    help="跳过技术相似图谱构建（仅灌数据，调试用）")
    args = ap.parse_args(argv)

    src = Path(args.src_dir)
    master = src / MASTER_CSV
    patent = src / PATENT_CSV
    funding = src / FUNDING_CSV
    for p in (master, patent, funding):
        if not p.exists():
            print("缺少数据源：%s" % p, file=sys.stderr)
            return 2
    if args.dry_run:
        print("[dry-run] 数据源就绪：\n  %s\n  %s\n  %s" % (master, patent, funding))
        return 0

    db_path = args.db
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    # 1) 建库（幂等）
    dbmod.init_db(db_path, force=False, verbose=True)
    # 登记新数据源（meta_ingest_log 的外键约束要求 source_id 已存在）
    seed = dbmod.connect(db_path)
    seed.execute(
        "INSERT OR IGNORE INTO meta_data_source"
        "(source_id, source_name, source_type, url, license, fetch_mode, cost_cny,"
        " quota_note, owner, coverage, first_fetch, last_fetch, is_active)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,1)",
        ("zj_500family", "500 家专精特新交付数据（合并主表 + 专利并集 + 融资流水）",
         "manual", "(本地 data/raw/ 合并 CSV，源自 _data_collect/out)", "internal",
         "manual", 0.0, "张丽晶专利明细 + 本人七类采集，2026-10-28 合并；2026-11-03 落地",
         "施汶邑", "企业主数据 / 资质 / 专利 / 融资"))
    seed.commit()
    seed.close()
    conn = dbmod.connect(db_path)
    try:
        # 存量库补齐特征层所需列（新建库由 schema.sql 提供，此步幂等无副作用）
        _ensure_feature_columns(conn)
        # 重跑前清空目标表，保证幂等（避免 qual/funding 等自增主键表重复累积）
        _clear_targets(conn)
        ent_by_norm, ent_rank = load_master(conn, master)
        log_ingest(conn, "zj_500family", "parse", "dim_enterprise+fact_qualification",
                   master, 500, len(ent_by_norm))
        npat, nmat = load_patents(conn, patent, ent_by_norm)
        log_ingest(conn, "zj_500family", "parse", "fact_patent+bridge",
                   patent, npat, npat)
        nfund, nfmt = load_funding(conn, funding, ent_by_norm)
        log_ingest(conn, "zj_500family", "parse", "fact_funding_event",
                   funding, nfund, nfmt)
        ntech, ngrowth, nfusion = load_features(conn, master, ent_by_norm, ent_rank)
        log_ingest(conn, "zj_500family", "build", "dws_tech/growth/fusion_feature",
                   master, 500, ntech)
        # 统一 ETL 最后一环：用刚灌入的专利/IPC 数据重建技术相似图谱
        if not args.skip_graph:
            gsum = build_tech_graph(conn)
            log_ingest(conn, "zj_500family", "build", "ads_graph_node+ads_graph_edge",
                       patent, gsum["n_enterprises_with_patent"], gsum["n_edges"])
        else:
            print("[graph] --skip-graph 已跳过图谱构建（ads_graph_* 保持清空状态）")
        conn.commit()   # 落定 meta_ingest_log（否则随连接关闭回滚）
    finally:
        conn.close()

    # 2) 复体检（连带刷新 manifest）
    rep = dbmod.verify_schema(db_path, verbose=True)
    try:
        dbmod.export_manifest(db_path, verbose=True)
    except Exception as e:  # manifest 非阻塞
        print("[warn] manifest 导出失败：%s" % e)
    # 回填 dim_enterprise.patent_cnt（已匹配专利数）
    conn = dbmod.connect(db_path)
    try:
        conn.execute(
            "UPDATE dim_enterprise SET patent_cnt = "
            "(SELECT COUNT(*) FROM fact_patent p WHERE p.enterprise_id = dim_enterprise.enterprise_id) "
            "WHERE enterprise_id IN (SELECT DISTINCT enterprise_id FROM fact_patent WHERE enterprise_id IS NOT NULL)")
        conn.commit()
    finally:
        conn.close()
    print("\n✅ 500 家交付数据载入完成 → %s" % db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
