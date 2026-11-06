# -*- coding: utf-8 -*-
"""加载企查查 4 张维度表（荣誉 / 资质证书 / 股东 / 年报）到 ods_qcc_*，
并把聚合特征回填进 dim_enterprise 的 QCC 扩展列。

背景
----
_data_collect/out 第二批整合版新增了 4 张企查查维度 CSV（以及专利并集）。
其中专利并集已由 load_500family 并入 fact_patent（source 字段可追溯），本脚本
只处理**尚未入库**的 4 张维度表，并派生可直接进模型的聚合列。

设计要点
--------
1. 单一事实源：ods 表 DDL 见 schema.sql 的 [QCC:BEGIN]/[QCC:END] 区块，本脚本
   通过正则截取该区块执行（与 as-of 区块同机制）；dim_enterprise 的新列则在
   存量库上幂等 ALTER（schema.sql 的 CREATE TABLE 已含，新建库不重复加）。
2. 企业主键：用 db.norm_company_name + make_enterprise_id 把 企业名称 解析为
   enterprise_id；不在 500 户清单内的（理论为 0）保留 company_name、enterprise_id=NULL。
3. 聚合列全部诚实置 NULL（NAN/空/未公示 → None），不拿 0 冒充缺失。
4. 年份类字段：年报社保人数「46人」抽数字；营收「3717.81」已是万元；
   科创等级（入门/普通/良好/优秀/卓越 → 1..5）、企业规模（XS/S/M/L → 1..4）、
   信用等级（L-N → N）做有序编码，保留原始文本列备查。
5. 幂等：每次先 DELETE FROM ods_qcc_* 再 INSERT；dim 列 UPDATE 覆盖写。
6. 结束后重导 manifest，反映新增表/列。

用法
----
    python -m src.data.load_qcc_dimensions
    python -m src.data.load_qcc_dimensions --src-dir ../../_data_collect/out
    python -m src.data.load_qcc_dimensions --db <path> --dry-run
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parents[2]
DB_PATH = _BACKEND / "data" / "db" / "zjtech_credit.db"
SCHEMA_SQL = _HERE.parent / "schema.sql"
DEFN_VER = "qcc_dimensions_v0.1"

MASTER_CSV = "合并_500家_企业主表.csv"
HONOR_CSV = "企查查_荣誉.csv"
CERT_CSV = "企查查_资质证书.csv"
SHARE_CSV = "企查查_股东信息.csv"
ANNUAL_CSV = "企查查_年报解析.csv"

# 科创等级有序编码（质量由低到高）
TECH_LEVEL_ORD = {"入门": 1, "普通": 2, "良好": 3, "优秀": 4, "卓越": 5}
# 企业规模有序编码（微型→大型）
SCALE_ORD = {"XS": 1, "S": 2, "M": 3, "L": 4}


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "NAN", "nan", "None", "null", "-") else s


def _to_int(v):
    s = _clean(v)
    if not s:
        return None
    # 抽数字：社保人数「46人」、持股数等
    m = re.search(r"-?\d+", s.replace(",", ""))
    return int(m.group()) if m else None


def _to_float(v):
    s = _clean(v)
    if not s:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    x = float(m.group())
    # 营收「亿」→ 万元
    if "亿" in s:
        x *= 10000
    return x


def _yesno(v):
    s = _clean(v)
    if s == "是":
        return 1
    if s == "否":
        return 0
    return None


def _tech_level_ord(v):
    s = _clean(v)
    return TECH_LEVEL_ORD.get(s) if s else None


def _scale_ord(v):
    s = _clean(v)
    if not s:
        return None
    # 「S(小型)」「XS(微型)」「M(中型)」「L(大型)」
    m = re.match(r"([A-Za-z]+)", s)
    key = m.group(1).upper() if m else None
    return SCALE_ORD.get(key)


def _credit_ord(v):
    s = _clean(v)
    if not s:
        return None
    m = re.search(r"\d+", s)          # 「L-12」→ 12
    return int(m.group()) if m else None


def _parse_wan(v):
    """年报财务字段：『企业选择不公示』→ None；否则解析为万元。"""
    s = _clean(v)
    if not s or "不公示" in s:
        return None
    return _to_float(s)


def _ensure_schema(conn, verbose=True):
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    m = re.search(r"-- \[QCC:BEGIN\][^\n]*\n(.*?)-- \[QCC:END\]", ddl, re.S)
    if not m:
        raise RuntimeError("schema.sql 缺少 [QCC:BEGIN]/[QCC:END] 区块")
    block = m.group(1).strip()
    conn.executescript(block)
    conn.commit()
    if verbose:
        print("  [ddl  ] 已应用 QCC 区块（ods_qcc_* 表）")


def _ensure_dim_columns(conn, verbose=True):
    """幂等补齐 dim_enterprise 的 QCC 扩展列（存量库 ALTER；新建库由 schema 提供）。"""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(dim_enterprise)").fetchall()}
    cols = [
        ("is_little_giant", "INTEGER"), ("is_tech_sme", "INTEGER"),
        ("is_high_tech_union", "INTEGER"), ("is_renamed", "INTEGER"),
        ("qcc_is_micro", "INTEGER"), ("qcc_tech_level_ord", "INTEGER"),
        ("qcc_scale_ord", "INTEGER"), ("qcc_credit_level_ord", "INTEGER"),
        ("zjtx_honor_cnt", "INTEGER"), ("honor_cnt", "INTEGER"),
        ("cert_cnt", "INTEGER"), ("cert_valid_cnt", "INTEGER"),
        ("shareholder_cnt", "INTEGER"), ("patent_union_cnt", "INTEGER"),
        ("invention_union_cnt", "INTEGER"), ("annual_social_sec", "INTEGER"),
        ("annual_revenue_wan", "REAL"), ("annual_net_profit_wan", "REAL"),
        ("qcc_reg_capital_wan", "REAL"), ("qcc_score", "REAL"),
        ("qcc_tech_score", "REAL"), ("qcc_tech_level", "TEXT"),
        ("qcc_enterprise_scale", "TEXT"), ("qcc_credit_level", "TEXT"),
        ("qcc_industry_section", "TEXT"), ("qcc_industry_class", "TEXT"),
        ("qcc_industry_mid", "TEXT"), ("qcc_industry_sub", "TEXT"),
        ("qcc_biz_scope", "TEXT"), ("qcc_intro", "TEXT"),
        ("qcc_match_method", "TEXT"),
    ]
    added = 0
    for c, t in cols:
        if c not in existing:
            conn.execute("ALTER TABLE dim_enterprise ADD COLUMN %s %s" % (c, t))
            added += 1
    conn.commit()
    if verbose:
        print("  [ddl  ] dim_enterprise 新增列 %d（已存在跳过 %d）" % (added, len(cols) - added))


def _load_honor(conn, path, ent_by_name, verbose=True):
    rows = []
    n_match = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = _clean(r.get("企业名称"))
            eid = ent_by_name.get(name) if name else None
            if eid:
                n_match += 1
            rows.append((
                eid, name, _clean(r.get("名称")), _clean(r.get("荣誉类型")),
                _clean(r.get("级别")), _clean(r.get("发布单位")),
                _to_int(r.get("认证年份")), _clean(r.get("发布日期")),
                _clean(r.get("有效期自")), _clean(r.get("有效期至")),
                _clean(r.get("来源")),
            ))
    conn.execute("DELETE FROM ods_qcc_honor")
    conn.executemany(
        """INSERT INTO ods_qcc_honor
           (enterprise_id, company_name, honor_name, honor_type, level, publisher,
            cert_year, publish_date, valid_from, valid_to, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    if verbose:
        print("  [honor] ods_qcc_honor = %d，匹配企业 %d" % (len(rows), n_match))


def _load_cert(conn, path, ent_by_name, verbose=True):
    rows = []
    n_match = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = _clean(r.get("企业名称"))
            eid = ent_by_name.get(name) if name else None
            if eid:
                n_match += 1
            rows.append((
                eid, name, _clean(r.get("资质名称")),
                _clean(r.get("资质类别及等级/产品名称/认证项目")),
                _clean(r.get("证书编号")), _clean(r.get("发证机构")),
                _clean(r.get("发证日期")), _clean(r.get("有效期至")),
                _clean(r.get("证书状态")),
            ))
    conn.execute("DELETE FROM ods_qcc_cert")
    conn.executemany(
        """INSERT INTO ods_qcc_cert
           (enterprise_id, company_name, cert_name, cert_category, cert_no,
            issuer, issue_date, valid_to, cert_status)
           VALUES (?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    if verbose:
        print("  [cert ] ods_qcc_cert = %d，匹配企业 %d" % (len(rows), n_match))


def _load_shareholder(conn, path, ent_by_name, verbose=True):
    """股东信息 CSV 含重复列名（股东名称/股东类型 各出现两次），按位置读取。

    pos: 0 企业名称 | 1 股东名称 | 2 股东类型 | 3,4 重复(忽略) | 5 股份类型 |
         6 持股数（股） | 7 认缴出资额（万元） | 8 认缴出资日期 | 9 间接持股比例 |
         10 首次持股日期 | 11 关联产品/机构 | 12 col12(忽略)
    """
    rows = []
    n_match = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for cols in reader:
            if len(cols) < 12:
                continue
            name = _clean(cols[0])
            eid = ent_by_name.get(name) if name else None
            if eid:
                n_match += 1
            rows.append((
                eid, name, _clean(cols[1]), _clean(cols[2]),
                _clean(cols[5]), _to_int(cols[6]), _to_float(cols[7]),
                _clean(cols[8]), _to_float(cols[9]), _clean(cols[10]),
                _clean(cols[11]),
            ))
    conn.execute("DELETE FROM ods_qcc_shareholder")
    conn.executemany(
        """INSERT INTO ods_qcc_shareholder
           (enterprise_id, company_name, shareholder_name, shareholder_type, share_kind,
            shares, subscription_wan, subscription_date, indirect_ratio,
            first_hold_date, related_product)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    if verbose:
        print("  [share] ods_qcc_shareholder = %d，匹配企业 %d" % (len(rows), n_match))


def _load_annual(conn, path, ent_by_name, verbose=True):
    rows = []
    n_match = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = _clean(r.get("企业名称"))
            eid = ent_by_name.get(name) if name else None
            if eid:
                n_match += 1
            rows.append((
                eid, name, _to_int(r.get("年份")), _clean(r.get("公布日期")),
                _parse_wan(r.get("营业总收入")), _to_int(r.get("从业人数")),
                _to_int(r.get("社保人数")), _parse_wan(r.get("资产总额")),
                _parse_wan(r.get("纳税总额")), _parse_wan(r.get("负债总额")),
                _parse_wan(r.get("净利润")), _parse_wan(r.get("利润总额")),
                _parse_wan(r.get("所有者权益合计")), _parse_wan(r.get("营业总收入中主营业务收入")),
                _clean(r.get("企业经营状态")),
            ))
    conn.execute("DELETE FROM ods_qcc_annual")
    conn.executemany(
        """INSERT INTO ods_qcc_annual
           (enterprise_id, company_name, report_year, publish_date,
            total_revenue_wan, employee_num, social_sec_num, total_assets_wan,
            tax_total_wan, liability_wan, net_profit_wan, profit_total_wan,
            owner_equity_wan, main_biz_rev_wan, biz_status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    if verbose:
        print("  [annual] ods_qcc_annual = %d，匹配企业 %d" % (len(rows), n_match))


def _populate_dim(conn, master_path, ent_by_name, verbose=True):
    """从主表 CSV 派生聚合列，回填 dim_enterprise（覆盖写）。"""
    updates = []
    n = 0
    with open(master_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            name = _clean(r.get("企业名称"))
            eid = ent_by_name.get(name) if name else None
            if not eid:
                continue
            updates.append((
                _yesno(r.get("D5_国家级小巨人")),
                _yesno(r.get("D6_科技型中小企业")),
                _yesno(r.get("D2_高企认定(三源合并)")),
                _yesno(r.get("是否更名")),
                _yesno(r.get("QCC_是否小微")),
                _tech_level_ord(r.get("QCC_科创等级")),
                _scale_ord(r.get("QCC_企业规模")),
                _credit_ord(r.get("QCC_信用等级")),
                _to_int(r.get("D7_专精特新类荣誉数")),
                _to_int(r.get("荣誉_条数")),
                _to_int(r.get("资质证书_条数")),
                _to_int(r.get("股东_人数")),
                _to_int(r.get("专利数_并集")),
                _to_int(r.get("发明专利数_并集")),
                _to_int(r.get("QCC_年报社保人数")),     # 「46人」→ 46
                _to_float(r.get("QCC_年报营收_万元")),   # 已是万元
                _to_float(r.get("QCC_注册资本_万元")),
                _to_float(r.get("QCC_企查分")),
                _to_float(r.get("QCC_科创分")),
                _clean(r.get("QCC_科创等级")),
                _clean(r.get("QCC_企业规模")),
                _clean(r.get("QCC_信用等级")),
                _clean(r.get("QCC_国标行业门类")),
                _clean(r.get("QCC_国标行业大类")),
                _clean(r.get("QCC_国标行业中类")),
                _clean(r.get("QCC_国标行业小类")),
                _clean(r.get("QCC_经营范围")),
                _clean(r.get("QCC_企业简介")),
                _clean(r.get("QCC_匹配方式")),
                eid,
            ))
            n += 1
    conn.executemany(
        """UPDATE dim_enterprise SET
              is_little_giant=?, is_tech_sme=?, is_high_tech_union=?, is_renamed=?,
              qcc_is_micro=?, qcc_tech_level_ord=?, qcc_scale_ord=?, qcc_credit_level_ord=?,
              zjtx_honor_cnt=?, honor_cnt=?, cert_cnt=?, shareholder_cnt=?,
              patent_union_cnt=?, invention_union_cnt=?, annual_social_sec=?,
              annual_revenue_wan=?, qcc_reg_capital_wan=?, qcc_score=?, qcc_tech_score=?,
              qcc_tech_level=?, qcc_enterprise_scale=?, qcc_credit_level=?,
              qcc_industry_section=?, qcc_industry_class=?, qcc_industry_mid=?,
              qcc_industry_sub=?, qcc_biz_scope=?, qcc_intro=?, qcc_match_method=?
           WHERE enterprise_id=?""", updates)
    # cert_valid_cnt 来自 ods_qcc_cert 聚合（有效证书数）
    conn.execute(
        """UPDATE dim_enterprise SET cert_valid_cnt = COALESCE(
              (SELECT COUNT(*) FROM ods_qcc_cert c
                WHERE c.enterprise_id = dim_enterprise.enterprise_id
                  AND c.cert_status = '有效'), 0)""")
    # annual_net_profit_wan 来自 ods_qcc_annual（净利润，万元；多为 NULL）
    conn.execute(
        """UPDATE dim_enterprise SET annual_net_profit_wan = (
              SELECT a.net_profit_wan FROM ods_qcc_annual a
              WHERE a.enterprise_id = dim_enterprise.enterprise_id
                AND a.net_profit_wan IS NOT NULL
              LIMIT 1)""")
    conn.commit()
    if verbose:
        print("  [dim  ] dim_enterprise QCC 聚合列回填 %d 户" % n)


def main(argv=None):
    ap = argparse.ArgumentParser(description="加载企查查维度表 + 回填 dim_enterprise 聚合列")
    ap.add_argument("--db", default=str(DB_PATH), help="目标数据库路径")
    ap.add_argument("--src-dir", default=None,
                    help="合并 CSV 目录（默认 _data_collect/out）")
    ap.add_argument("--dry-run", action="store_true", help="只校验数据源，不写库")
    args = ap.parse_args(argv)

    src = Path(args.src_dir) if args.src_dir else (
        _BACKEND.parent / "_data_collect" / "out")
    master = src / MASTER_CSV
    honor = src / HONOR_CSV
    cert = src / CERT_CSV
    share = src / SHARE_CSV
    annual = src / ANNUAL_CSV
    for p in (master, honor, cert, share, annual):
        if not p.exists():
            print("缺少数据源：%s" % p, file=sys.stderr)
            return 2

    print("[cfg] db=%s  src=%s  dry_run=%s" % (args.db, src, args.dry_run))
    if args.dry_run:
        print("[dry] 数据源就绪：\n  %s\n  %s\n  %s\n  %s\n  %s"
              % (master, honor, cert, share, annual))
        return 0

    from src.data import db as dbmod
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys=OFF")   # ods 表外键引用 dim，先建表/清表阶段关闭
    try:
        _ensure_schema(conn, verbose=True)
        _ensure_dim_columns(conn, verbose=True)
        # 企业名 → enterprise_id 映射
        ent_by_name = {n: e for e, n in conn.execute(
            "SELECT enterprise_id, name_display FROM dim_enterprise")}
        _load_honor(conn, honor, ent_by_name)
        _load_cert(conn, cert, ent_by_name)
        _load_shareholder(conn, share, ent_by_name)
        _load_annual(conn, annual, ent_by_name)
        _populate_dim(conn, master, ent_by_name)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        conn.close()

    # 复体检 + 重导 manifest（反映新增表/列）
    dbmod.init_db(args.db, force=False, verbose=False)
    try:
        dbmod.verify_schema(args.db, verbose=True)
        dbmod.export_manifest(args.db, verbose=True)
    except Exception as e:  # manifest 非阻塞
        print("[warn] manifest 导出失败：%s" % e)
    print("\n✅ 企查查维度表载入完成 → %s（defn_ver=%s）" % (args.db, DEFN_VER))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
