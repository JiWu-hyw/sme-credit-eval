# -*- coding: utf-8 -*-
"""标准数据库：建库、字典灌装、结构校验、指标映射、导出。

引擎：SQLite 3.37+（STRICT tables）
路径：data/db/zjtech_credit.db
用法：
    python -m src.data.db --init          # 建库（幂等；已存在则只补缺表）
    python -m src.data.db --init --force  # 重建（会删除原库文件！仅空库阶段使用）
    python -m src.data.db --verify        # 结构体检 + 覆盖率 + 指标映射自检
    python -m src.data.db --manifest      # 导出结构清单 configs/db_schema_manifest.json
    python -m src.data.db --export        # 各表导出 parquet 到 data/processed/db_export/
"""
from __future__ import annotations  # 兼容 3.9，同时支持 dict[str, ...] 写法（本文件不用）

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_DIR = os.path.join(ROOT, "data", "db")
DB_PATH = os.path.join(DB_DIR, "zjtech_credit.db")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
MANIFEST_PATH = os.path.join(ROOT, "configs", "db_schema_manifest.json")
EXPORT_DIR = os.path.join(ROOT, "data", "processed", "db_export")

SCHEMA_VERSION = "v1.0"

# ============================================================================
# 一、指标 → 字段 映射契约（与 docs/特征工程字典.md 一一对应）
#     key = 字典编号, value = (表名, 字段名, 口径说明)
# ============================================================================
INDICATOR_MAPPING = {
    # —— 技术要素（7 项）——
    "3.1 inv_patent_ratio": ("dws_tech_feature", "inv_patent_ratio",
                             "发明专利申请量 / 专利申请总量；数据来源 CNIPA/Google Patents"),
    "3.2 grant_rate": ("dws_tech_feature", "grant_rate",
                       "按申请年对齐的授权率，剔除未决项（legal_status='pending'）"),
    "3.3 patent_citation": ("dws_tech_feature", "patent_citation",
                            "近 5 年被引总数；稀疏需零值/对数处理，原始计数见 n_patent_cited"),
    "3.4 tech_breadth": ("dws_tech_feature", "tech_breadth_ipc_class + tech_breadth_entropy",
                         "IPC 小类覆盖数 + Shannon 熵（双口径，字典允许 int/float）"),
    "3.5 patent_triple_score": ("dws_tech_feature", "patent_triple_score",
                                "SciBERT 向量 + 被引/同族/权利要求数 的轻量打分均值"),
    "3.6 core_staff_stability": ("dws_tech_feature", "core_staff_stability",
                                 "以发明人为核心人员界定：1 - 年流失率"),
    "3.7 rd_intensity": ("dws_tech_feature", "rd_intensity",
                         "替代口径=研发人员占比；口径标注见 rd_intensity_flag，校验组做敏感性"),
    # —— 成长轨迹（6 项）——
    "4.1 rev_cagr": ("dws_growth_feature", "rev_cagr",
                     "3 年营收 CAGR；中小企业披露不全，口径标注 rev_cagr_flag"),
    "4.2 emp_cagr": ("dws_growth_feature", "emp_cagr", "3 年员工数 CAGR"),
    "4.3 patent_cagr": ("dws_growth_feature", "patent_cagr",
                        "3 年专利申请 CAGR；数据来源专利表逐年计数"),
    "4.4 funding_to_last_val": ("dws_growth_feature", "funding_to_last_val",
                                "本轮融资额/上轮投后估值；非上市缺失 → 用 qual_rank_delta 代理"),
    "4.5 milestone_flags": ("dws_growth_feature", "m_funding / m_team / m_cert / m_cite",
                            "四类里程碑虚拟变量；m_cert 由资质名单差分直接产出（免费可得）"),
    "4.6 investor_prestige": ("dws_growth_feature", "investor_prestige",
                              "投资机构知名度等级；来源 fact_funding_event.prestige_rank"),
    # —— 融合特征（2 项）——
    "5.1 tech_conversion": ("dws_fusion_feature", "tech_conversion",
                            "营收 CAGR / 专利 CAGR，或 授权专利数/研发人员数"),
    "5.2 cap_tech_synergy": ("dws_fusion_feature", "cap_tech_synergy",
                             "融资后 N 季专利产出增速提升值；P2，先不做"),
    # —— 本方案新增（原 funding 的公开数据替代）——
    "A1 资质晋级序列": ("dws_growth_feature", "qual_rank / qual_rank_delta / qual_upgrade_cum",
                        "公开数据替代融资信号：科技型→高新→专精特新→小巨人 的层级与时点"),
    "A2 资质存续标签": ("ads_label", "label_value",
                        "主任务标签：cohort_year 认定→观察期复核，未通过=1"),
}

# 资质层级字典（梯度晋级序列依据）
QUAL_TYPES = [
    ("tech_sme", "科技型中小企业", 1, 1, "浙江省经济和信息化厅",
     "每年入库，有效期 1 年（需每年重新评价）"),
    ("high_tech", "高新技术企业", 2, 3, "全国高新技术企业认定管理工作领导小组办公室",
     "有效期 3 年，期满需重新认定（本项目主标签即基于此复核）"),
    ("specialized", "专精特新中小企业", 3, 3, "省/市经济和信息化厅（局）",
     "有效期 3 年，梯度培育体系省级层"),
    ("little_giant", "专精特新「小巨人」企业", 4, 3, "工业和信息化部",
     "国家级，有效期 3 年；梯度培育顶层"),
]

# 数据源登记（全部免费源，cost_cny=0；预算红线核查依据）
DATA_SOURCES = [
    ("zj_tech_sme", "浙江省科技型中小企业入库公示名单", "official_list",
     "https://zj87.jxt.zj.gov.cn/（名单公示栏目）", "public", "web_parse", 0.0,
     "每批公示 10 个工作日；无接口，需解析网页/附件。地市科技局同步公示"
     "（温州 wzkj.wenzhou.gov.cn / 舟山 zskjj.zhoushan.gov.cn / 丽水 kjj.lishui.gov.cn），"
     "附件多为 .xls，可补省网缺口", "吕键",
     "企业样本池 L1 + 资质存续弱标签"),
    ("zj_high_tech", "全国高新技术企业认定管理工作网·浙江备案名单", "official_list",
     "http://www.innocom.gov.cn/", "public", "web_parse", 0.0,
     "年度备案公告，附件为 PDF", "吕键",
     "企业样本池 L2 + 主标签（复核通过/未通过）"),
    ("zj_specialized", "浙江省/地市专精特新中小企业公示名单", "official_list",
     "各市经信局官网", "public", "web_parse", 0.0,
     "附件 docx/pdf 混杂", "吕键", "企业样本池 L3 + 晋级序列锚点"),
    ("google_patents", "Google Patents Public Datasets (BigQuery)", "patent_db",
     "https://console.cloud.google.com/marketplace/details/google_patents_public_datasets/google-patents-research",
     "public", "api", 0.0,
     "每月有免费查询额度；大查询按扫描量计费，需控次数", "吕键",
     "技术要素全指标（申请/授权/IPC/被引/发明人）"),
    ("cnipa_ipdps", "国家知识产权局·知识产权数据资源公共服务系统", "patent_db",
     "https://ipdps.cnipa.gov.cn/", "public", "bulk_download", 0.0,
     "注册后按数据种类批量下载，62 种基础数据", "吕键",
     "专利增量补齐 + 论文声明官方数据源"),
    ("uspto_odp", "USPTO Open Data Portal（原 PatentsView）", "patent_db",
     "https://data.uspto.gov/", "public", "api", 0.0,
     "需注册免费 API key；2026-03 由 PatentsView 迁移", "施汶邑",
     "国际专利对照（可选）"),
    ("kaggle_msme", "Kaggle: MSME Credit Data by 30S-CR", "open_dataset",
     "https://www.kaggle.com/datasets/cr30srtfcn/msme-credit-data-by-30scr",
     "CC BY-NC 4.0", "api", 0.0,
     "1707 家中国中小微企业 / 87 特征 / 0-1 违约标签", "施汶邑",
     "对照标签（信贷违约口径）"),
    ("kaggle_sba", "Kaggle: SBA Loans Case Data Set", "open_dataset",
     "https://www.kaggle.com/datasets/larsen0966/sba-loans-case-data-set",
     "public", "api", 0.0,
     "约 90 万条美国小企业贷款记录", "施汶邑", "大规模稳健性对照"),
    ("akshare", "AKShare 开源财经数据接口库", "market_api",
     "https://github.com/akfamily/akshare", "MIT", "api", 0.0,
     "pip install akshare；上市公司定增/可转债/IPO 公告", "吕键",
     "融资事件（仅上市公司子集）"),
    ("zj_credit_panel", "浙江中小科技企业信贷面板（原数据，跨源对照）", "internal",
     "（本地 data/raw/zhejiang_real/）", "internal", "manual", 0.0,
     "3465 行 × 204 列；提供方 P0 缺口不可得，降级为跨源对照", "袁雪茹",
     "跨源对照实验（0.59 天花板作为动机实证）"),
]


# ============================================================================
# 二、工具函数
# ============================================================================
def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """建立连接：开启外键、返回按列名取值的行工厂。

    注：使用 SQLite 默认的 DELETE 日志模式（不开 WAL），
    保证 .db 单文件自包含 —— 直接拷贝/同步到网盘不会丢数据。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def norm_company_name(name: str) -> str:
    """企业名规范化：去空白、全角转半角、统一括号、去省级前缀。"""
    if name is None:
        return ""
    s = str(name).strip()
    # 全角转半角
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out)
    # 统一括号
    s = s.replace("（", "(").replace("）", ")").replace("〔", "(").replace("〕", ")")
    # 去括号备注（如 "(原杭州XX有限公司)"）
    s = re.sub(r"\([^)]*\)", "", s)
    # 去空白与常见前缀
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^(浙江省|浙江|杭州市|杭州|宁波市|宁波|温州市|温州|嘉兴市|嘉兴|湖州市|湖州|"
               r"绍兴市|绍兴|金华市|金华|衢州市|衢州|舟山市|舟山|台州市|台州|丽水市|丽水)", "", s)
    return s


def make_enterprise_id(name_norm: str, prefix: str = "ENT") -> str:
    """确定性代理键：ENT + md5(规范化名)[:12] 大写。不依赖统一社会信用代码。"""
    h = hashlib.md5(name_norm.encode("utf-8")).hexdigest()[:12].upper()
    return prefix + h


def make_inventor_id(name_norm: str, addr_hash: str = "", prefix: str = "INV") -> str:
    h = hashlib.md5((name_norm + "|" + (addr_hash or "")).encode("utf-8")).hexdigest()[:10].upper()
    return prefix + h


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================================
# 三、建库
# ============================================================================
def init_db(db_path: str = DB_PATH, force: bool = False, verbose: bool = True) -> str:
    """建库：执行 schema.sql（幂等）+ 灌装字典表 + 写版本记录。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if force and os.path.exists(db_path):
        # 同时清理 WAL 旁挂文件，避免残留脏页
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            if os.path.exists(p):
                os.remove(p)
        if verbose:
            print("[init] 已删除旧库文件（--force）")

    with open(SCHEMA_SQL, "r", encoding="utf-8") as f:
        ddl = f.read()

    conn = connect(db_path)
    try:
        conn.executescript(ddl)
        conn.commit()
        if verbose:
            print("[init] DDL 执行完成")
        seed_dictionaries(conn, verbose=verbose)
        # 版本记录（幂等）
        conn.execute(
            "INSERT OR REPLACE INTO meta_schema_version(version, applied_at, author, note) "
            "VALUES (?,?,?,?)",
            (SCHEMA_VERSION, now_iso(), "WorkBuddy(施汶邑)",
             "建库：meta/ods/dim/fact/dws/ads 六层 28 表 + 5 视图；指标口径对齐 特征工程字典 v0.1"),
        )
        conn.commit()
        if verbose:
            n_tab = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            n_view = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='view'"
            ).fetchone()[0]
            print("[init] 完成：%d 张表 / %d 个视图 / %s" % (n_tab, n_view, db_path))
    finally:
        conn.close()
    return db_path


def seed_dictionaries(conn: sqlite3.Connection, verbose: bool = True) -> None:
    """灌装字典表：资质类型、数据源、IPC 常用部类。"""
    ts = now_iso()
    for qt, name, rank, valid, issuer, note in QUAL_TYPES:
        conn.execute(
            "INSERT OR REPLACE INTO dim_qual_type"
            "(qual_type, qual_name, level_rank, valid_years, issuer, is_gradable, note) "
            "VALUES (?,?,?,?,?,1,?)",
            (qt, name, rank, valid, issuer, note),
        )
    for sid, sname, stype, url, lic, mode, cost, quota, owner, cov in DATA_SOURCES:
        conn.execute(
            "INSERT OR REPLACE INTO meta_data_source"
            "(source_id, source_name, source_type, url, license, fetch_mode, cost_cny,"
            " quota_note, owner, coverage, first_fetch, last_fetch, is_active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,1)",
            (sid, sname, stype, url, lic, mode, cost, quota, owner, cov),
        )
    # IPC 八部（技术广度的上位分类）
    sections = [
        ("A", "人类生活必需"), ("B", "作业；运输"), ("C", "化学；冶金"),
        ("D", "纺织；造纸"), ("E", "固定建筑物"), ("F", "机械工程；照明；加热；武器；爆破"),
        ("G", "物理"), ("H", "电学"),
    ]
    for sec, title in sections:
        conn.execute(
            "INSERT OR REPLACE INTO dim_ipc"
            "(ipc_code, ipc_level, ipc_section, ipc_class, ipc_subclass, parent_code, title) "
            "VALUES (?,?,?,?,?,NULL,?)",
            (sec, 1, sec, None, None, title),
        )
    conn.commit()
    if verbose:
        print("[seed] 资质类型 %d 条 / 数据源 %d 条 / IPC 部类 %d 条"
              % (len(QUAL_TYPES), len(DATA_SOURCES), len(sections)))


# ============================================================================
# 四、结构校验
# ============================================================================
def verify_schema(db_path: str = DB_PATH, verbose: bool = True) -> dict:
    """体检：表/视图是否齐全、字段是否落位、指标映射是否可解析、数据覆盖度。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError("数据库不存在，请先运行 --init：%s" % db_path)

    required_tables = [
        # meta
        "meta_schema_version", "meta_data_source", "meta_ingest_log",
        # ods
        "ods_qual_notice", "ods_qual_company_raw", "ods_patent_raw",
        # dim
        "dim_qual_type", "dim_enterprise", "dim_enterprise_alias", "dim_ipc", "dim_inventor",
        # fact
        "fact_qualification", "fact_qualification_event", "fact_patent",
        "bridge_patent_ipc", "bridge_patent_inventor", "fact_patent_citation",
        "fact_funding_event", "log_match",
        # dws
        "dws_tech_feature", "dws_growth_feature", "dws_fusion_feature",
        # ads / ext
        "ads_label", "ads_model_dataset", "ads_graph_node", "ads_graph_edge", "ext_msme_sample",
    ]
    required_views = [
        "v_enterprise_profile", "v_patent_yearly", "v_qual_history",
        "v_model_dataset_flat", "v_data_coverage",
    ]

    conn = connect(db_path)
    report = {"tables": {}, "views": {}, "indicators": {}, "coverage": {}, "errors": []}
    try:
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        existing_views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'")}

        for t in required_tables:
            if t not in existing_tables:
                report["errors"].append("缺表：" + t)
                continue
            cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
            report["tables"][t] = cols

        for v in required_views:
            if v not in existing_views:
                report["errors"].append("缺视图：" + v)
                continue
            report["views"][v] = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % v)]

        # 指标映射自检：字段是否真在对应表里
        for key, (table, field, _note) in INDICATOR_MAPPING.items():
            tables = [table] if " " not in table else table.split(" + ")
            ok = True
            missing = []
            for tb in tables:
                tb = tb.strip()
                if tb not in report["tables"]:
                    ok = False
                    missing.append(tb + "(表缺失)")
                    continue
                # 支持 'a / b / c' 多字段
                for fd in [x.strip() for x in field.split("+")]:
                    for one in [y.strip() for y in fd.split("/")]:
                        if one and one not in report["tables"][tb]:
                            ok = False
                            missing.append(tb + "." + one)
            report["indicators"][key] = {"ok": ok, "missing": missing}
            if not ok:
                report["errors"].append("指标字段缺失：%s → %s" % (key, ", ".join(missing)))

        # 覆盖度
        for row in conn.execute("SELECT obj, n FROM v_data_coverage"):
            report["coverage"][row["obj"]] = row["n"]
    finally:
        conn.close()

    if verbose:
        print("=" * 64)
        print("数据库结构体检：%s" % db_path)
        print("=" * 64)
        print("表 %d/%d    视图 %d/%d    指标映射 %d 项"
              % (len(report["tables"]), len(required_tables),
                 len(report["views"]), len(required_views),
                 len(INDICATOR_MAPPING)))
        bad = [k for k, v in report["indicators"].items() if not v["ok"]]
        if bad:
            print("指标映射异常：")
            for k in bad:
                print("  ✗ %s" % k)
        else:
            print("指标映射：全部字段已落位 ✓")
        print("\n数据覆盖度：")
        for k, v in report["coverage"].items():
            print("  %-16s %d" % (k, v))
        if report["errors"]:
            print("\n错误 %d 项：" % len(report["errors"]))
            for e in report["errors"]:
                print("  ✗ " + e)
        else:
            print("\n✅ SCHEMA HEALTHY")
    return report


# ============================================================================
# 五、导出结构清单（供评审与版本比对）
# ============================================================================
def export_manifest(db_path: str = DB_PATH, out_path: str = MANIFEST_PATH,
                    verbose: bool = True) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError("数据库不存在，请先运行 --init")
    conn = connect(db_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "db_path": os.path.relpath(db_path, ROOT).replace("\\", "/"),
        "generated_at": now_iso(),
        "indicators": {k: {"table": v[0], "field": v[1], "note": v[2]}
                       for k, v in INDICATOR_MAPPING.items()},
        "tables": {},
        "views": {},
    }
    try:
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"):
            cols = []
            for r in conn.execute("PRAGMA table_info(%s)" % name):
                cols.append({
                    "name": r[1], "type": r[2], "notnull": bool(r[3]),
                    "default": r[4], "pk": bool(r[5]),
                })
            n = conn.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
            manifest["tables"][name] = {"columns": cols, "n_cols": len(cols), "n_rows": n}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"):
            manifest["views"][name] = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % name)]
        manifest["total_tables"] = len(manifest["tables"])
        manifest["total_views"] = len(manifest["views"])
        manifest["total_columns"] = sum(v["n_cols"] for v in manifest["tables"].values())
    finally:
        conn.close()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    if verbose:
        print("[manifest] %d 表 / %d 视图 / %d 字段 → %s"
              % (manifest["total_tables"], manifest["total_views"],
                 manifest["total_columns"], out_path))
    return out_path


# ============================================================================
# 六、导出 parquet（供建模脚本直接读取，避免每次都走 SQL）
# ============================================================================
def export_parquet(db_path: str = DB_PATH, out_dir: str = EXPORT_DIR,
                   tables=None, verbose: bool = True) -> list:
    import pandas as pd

    if not os.path.exists(db_path):
        raise FileNotFoundError("数据库不存在，请先运行 --init")
    os.makedirs(out_dir, exist_ok=True)
    conn = connect(db_path)
    written = []
    try:
        if tables is None:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name")]
        for t in tables:
            df = pd.read_sql_query("SELECT * FROM %s" % t, conn)
            path = os.path.join(out_dir, t + ".parquet")
            df.to_parquet(path, index=False)
            written.append(path)
            if verbose:
                print("  %-28s %5d 行 × %3d 列" % (t, len(df), len(df.columns)))
    finally:
        conn.close()
    if verbose:
        print("[export] %d 个 parquet → %s" % (len(written), out_dir))
    return written


# ============================================================================
# CLI
# ============================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description="标准数据库管理（SQLite）")
    ap.add_argument("--init", action="store_true", help="建库（幂等）")
    ap.add_argument("--force", action="store_true", help="配合 --init 重建（删旧库）")
    ap.add_argument("--verify", action="store_true", help="结构体检")
    ap.add_argument("--manifest", action="store_true", help="导出结构清单 JSON")
    ap.add_argument("--export", action="store_true", help="导出各表 parquet")
    ap.add_argument("--db", default=DB_PATH, help="数据库路径")
    args = ap.parse_args(argv)

    if not any([args.init, args.verify, args.manifest, args.export]):
        ap.print_help()
        return 0

    if args.init:
        init_db(args.db, force=args.force)
    if args.verify:
        rep = verify_schema(args.db)
        return 0 if not rep["errors"] else 1
    if args.manifest:
        export_manifest(args.db)
    if args.export:
        export_parquet(args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
