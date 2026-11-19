# -*- coding: utf-8 -*-
"""v3.0 整合高企证书编号 4 列进 dim_enterprise。

数据源：_data_collect/out/3000家_主表.csv（3000 行 × 111 列），由
_data_collect/s69_cert_number.py 从「3000家_荣誉明细.csv」(17,784 条) 提取，
高企记录 3,233 条、编号非空 2,848 条（88.1%）。

新增 4 列
--------
* 高企证书编号（最新一次）：2,505 / 3,000（83.5%）
* 高企编号_最新年：2,715 / 3,000（90.5%）
* 高企认定次数：3,000 / 3,000（100%；1 次 2,354 / 2 次 361 / 0 次 285）
* 高企证书编号_全部：2,715 / 3,000（多年份拼接，如 2023:GR202333007771;2020:GR2020...）

编号格式（GR + 认定年份4位 + 地区码2位 + 序号6位）可解码；
编号解码年份 vs 高企编号_最新年：一致 2,505 / 不一致 0（零不一致）。

口径要点
--------
* CSV 中 NAN 为占位，入库统一转 NULL；认定次数转 INTEGER。
* 按企业名称归一（去空白 + 全角括号转半角）匹配 enterprise_id，3000/3000 全命中。
* 幂等：列已存在则跳过加列；血缘用 DELETE+INSERT 覆盖，可重复执行。

用法
----
  python -m src.data.apply_v3_0_hitech_cert --check
  python -m src.data.apply_v3_0_hitech_cert
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

SOURCE_ID = "v3_hitech_cert"

# 列 -> SQLite 类型
NEW_COLS = {
    "高企证书编号": "TEXT",
    "高企编号_最新年": "TEXT",
    "高企认定次数": "INTEGER",
    "高企证书编号_全部": "TEXT",
}

NULL_TOKENS = {"", "nan", "nan%", "none", "null", "-", "n/a", "na"}


def norm(s):
    return "".join(str(s or "").split()).replace("（", "(").replace("）", ")")


def _open(check):
    if check:
        return sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    return sqlite3.connect(DB_PATH)


def clean(v, typ):
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
    return s


def load_rows():
    raw = open(CSV_PATH, "rb").read()
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def check(con):
    c = con.cursor()
    cols = {r[1] for r in c.execute("PRAGMA table_info('dim_enterprise')")}
    missing = [k for k in NEW_COLS if k not in cols]
    rows = load_rows()
    print("=== v3.0 高企证书编号整合体检（只读） ===")
    print("  CSV: %d 行 × %d 列" % (len(rows), len(rows[0])))
    print("  待加列 %d 个: %s" % (len(missing), missing))
    n2id = {norm(n): e for e, n in c.execute(
        "SELECT enterprise_id,name_display FROM dim_enterprise") if n}
    matched = sum(1 for r in rows if norm(r.get("企业名称")) in n2id)
    print("  名称匹配: %d/%d" % (matched, len(rows)))
    cov = {k: sum(1 for r in rows if str(r.get(k, "")).strip().lower() not in NULL_TOKENS)
           for k in NEW_COLS}
    print("  CSV 覆盖: " + " / ".join("%s=%d" % (k, v) for k, v in cov.items()))
    # 库内覆盖（列已存在时）
    if not missing:
        lib = {}
        for k in NEW_COLS:
            c.execute('SELECT COUNT("%s") FROM dim_enterprise' % k)
            lib[k] = c.fetchone()[0]
        print("  库内非空: " + " / ".join("%s=%d" % (k, v) for k, v in lib.items()))
    return {"missing": missing, "matched": matched, "total": len(rows), "cov": cov}


def build(con):
    c = con.cursor()
    c.execute("""INSERT OR IGNORE INTO meta_data_source
        (source_id,source_name,source_type,url,license,fetch_mode,cost_cny,
         quota_note,owner,coverage,first_fetch,last_fetch,is_active)
        VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),1)""",
        (SOURCE_ID, "v3.0高企证书编号整合", "internal", "", "", "manual", 0,
         "荣誉明细提取高企证书编号/认定次数4列", "项目组", "3000家"))
    cols = {r[1] for r in c.execute("PRAGMA table_info('dim_enterprise')")}
    added = []
    for name, typ in NEW_COLS.items():
        if name not in cols:
            c.execute('ALTER TABLE dim_enterprise ADD COLUMN "%s" %s' % (name, typ))
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
        "UPDATE dim_enterprise SET %s WHERE enterprise_id=?" % set_clause, recs)

    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?",
        (SOURCE_ID, "build", "dim_enterprise"))
    c.execute(
        """INSERT INTO meta_ingest_log
           (source_id,stage,target_table,file_path,file_sha256,rows_in,rows_ok,
            rows_bad,started_at,finished_at,status,message)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (SOURCE_ID, "build", "dim_enterprise", CSV_PATH, "",
         len(rows), len(recs), len(rows) - len(recs), now, now, "ok",
         "加列 %d；回填高企证书编号4列；编号2505/最新年2715/次数3000/全部2715" % len(added)))
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
