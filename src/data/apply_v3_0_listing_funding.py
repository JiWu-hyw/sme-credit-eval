# -*- coding: utf-8 -*-
"""v3.0 整合新 16 列（上市关联 + 融资机构分级）进 dim_enterprise。

数据源：_data_collect/out/3000家_主表.csv（3000 行 × 107 列，本次新增 16 列）：
  - 上市关联 10 列：上市状态 / 股票代码 / 股票简称 / 上市主体全称 / 交易市场 /
    上市关联类型 / 上市板块 / 是否A股上市 / 是否新三板 / 上市状态_原
  - 融资机构分级 6 列：融资_最高机构级别 / 融资_级别分值 / 融资_顶级机构数 /
    融资_机构匹配数 / 融资_机构活跃度 / 融资_国资基金数

口径要点
--------
* 上市关联：上一轮「86 户上市」口径错误（F10 名录含新三板，8 开头误算上市），
  本轮按代码前缀分板块：A 股 5 户 / 新三板 68 户 / 未挂牌 2912 户。
* CSV 中 NAN 为 pandas 导出的字符串占位，入库统一转 NULL。
* 按企业名称归一（去空白 + 全角括号转半角）匹配 enterprise_id，3000/3000 全命中。
* 幂等：列已存在则跳过加列；血缘用 DELETE+INSERT 覆盖，可重复执行。

用法
----
  python -m src.data.apply_v3_0_listing_funding --check
  python -m src.data.apply_v3_0_listing_funding
"""
import argparse
import csv
import io
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(BACKEND)
DB_PATH = os.path.join(BACKEND, "data", "db", "zjtech_credit.db")
CSV_PATH = os.path.join(ROOT, "_data_collect", "out", "3000家_主表.csv")

# 列 -> SQLite 类型
NEW_COLS = {
    # 上市关联（文本）
    "上市状态": "TEXT", "股票代码": "TEXT", "股票简称": "TEXT",
    "上市主体全称": "TEXT", "交易市场": "TEXT", "上市关联类型": "TEXT",
    "上市板块": "TEXT", "是否A股上市": "TEXT", "是否新三板": "TEXT",
    "上市状态_原": "TEXT",
    # 融资机构分级
    "融资_最高机构级别": "TEXT", "融资_级别分值": "REAL",
    "融资_顶级机构数": "INTEGER", "融资_机构匹配数": "INTEGER",
    "融资_机构活跃度": "INTEGER", "融资_国资基金数": "INTEGER",
}

NULL_TOKENS = {"", "nan", "nan%", "none", "null", "-", "n/a", "na"}


def norm(s):
    return "".join(str(s or "").split()).replace("（", "(").replace("）", ")")


def _open(check):
    if check:
        return sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    return sqlite3.connect(DB_PATH)


def clean(v, typ):
    """NAN 等占位 -> None；数值列做类型转换。"""
    if v is None:
        return None
    s = str(v).strip()
    if s.lower() in NULL_TOKENS:
        return None
    if typ == "INTEGER":
        try:
            return int(float(s))
        except ValueError:
            return None
    if typ == "REAL":
        try:
            return float(s)
        except ValueError:
            return None
    return s


def load_rows():
    raw = open(CSV_PATH, "rb").read()
    txt = raw.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(txt)))


def check(con):
    c = con.cursor()
    cols = {r[1] for r in c.execute("PRAGMA table_info('dim_enterprise')")}
    missing = [k for k in NEW_COLS if k not in cols]
    rows = load_rows()
    print("=== v3.0 上市关联+融资分级整合体检（只读） ===")
    print("  CSV: %d 行 × %d 列" % (len(rows), len(rows[0])))
    print("  待加列 %d 个: %s" % (len(missing), missing))
    n2id = {norm(n): e for e, n in c.execute(
        "SELECT enterprise_id,name_display FROM dim_enterprise") if n}
    matched = sum(1 for r in rows if norm(r.get("企业名称")) in n2id)
    print("  名称匹配: %d/%d" % (matched, len(rows)))
    # 关键口径抽样
    a = sum(1 for r in rows if (r.get("是否A股上市") or "").strip() == "是")
    n3 = sum(1 for r in rows if (r.get("是否新三板") or "").strip() == "是")
    lvl = sum(1 for r in rows if (r.get("融资_最高机构级别") or "").strip().lower() not in NULL_TOKENS)
    print("  是否A股上市=是: %d / 是否新三板=是: %d / 融资_最高机构级别非空: %d" % (a, n3, lvl))
    return {"missing": missing, "matched": matched, "total": len(rows)}


def build(con):
    c = con.cursor()
    # 确保血缘 source 已登记（外键约束），可重复执行
    c.execute("""INSERT OR IGNORE INTO meta_data_source
        (source_id,source_name,source_type,url,license,fetch_mode,cost_cny,
         quota_note,owner,coverage,first_fetch,last_fetch,is_active)
        VALUES ('v3_listing_funding','v3.0上市关联+融资分级整合','internal','','','manual',0,
                'F10上市识别+投资机构分级16列','项目组','3000家',
                datetime('now'),datetime('now'),1)""")
    cols = {r[1]: r[2] for r in c.execute("PRAGMA table_info('dim_enterprise')")}
    added = []
    for name, typ in NEW_COLS.items():
        if name not in cols:
            c.execute("ALTER TABLE dim_enterprise ADD COLUMN \"%s\" %s" % (name, typ))
            added.append(name)

    n2id = {norm(n): e for e, n in c.execute(
        "SELECT enterprise_id,name_display FROM dim_enterprise") if n}
    rows = load_rows()
    set_clause = ",".join('"%s"=?' % k for k in NEW_COLS)
    recs = []
    for r in rows:
        eid = n2id.get(norm(r.get("企业名称")))
        if not eid:
            continue
        recs.append(tuple(clean(r.get(k), t) for k, t in NEW_COLS.items()) + (eid,))
    c.executemany(
        'UPDATE dim_enterprise SET %s WHERE enterprise_id=?' % set_clause, recs)

    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?",
        ("v3_listing_funding", "build", "dim_enterprise"))
    c.execute(
        """INSERT INTO meta_ingest_log
           (source_id,stage,target_table,file_path,file_sha256,rows_in,rows_ok,
            rows_bad,started_at,finished_at,status,message)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("v3_listing_funding", "build", "dim_enterprise", CSV_PATH, "",
         len(rows), len(recs), len(rows) - len(recs), now, now, "ok",
         "加列 %d；回填上市关联+融资分级 16 列；A股5/新三板68/融资分级446" % len(added)))
    con.commit()
    print("加列: %s" % added)
    print("回填 %d 行" % len(recs))
    return {"added": added, "n": len(recs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    con = _open(args.check)
    try:
        check(con)
        if not args.check:
            print()
            build(con)
            print("\n=== 落库后复核 ===")
            check(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
