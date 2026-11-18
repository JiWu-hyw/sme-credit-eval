# -*- coding: utf-8 -*-
"""v3.0 全量口径收口：biz_status 归一 + 放开已发布视图到 3000 家。

背景
----
apply_v3_0_labels 已把 ads_label 扩到 1675 家可标注企业。用户核对确认：
司法 A 轴 / 主要人员 / high_tech 标签均已全量覆盖，原先为「防旧数字漂移」
而给 9 个视图加的 is_sample='1' 限定应放开到 3000 家全量。真正仍 pin 500
的只有 6 项建模字段（IPC 技术广度 / 网络足迹 8 列 / 信用浙江 4 列 / 上市关联
/ 融资深度字段 / 高企证书编号），它们是字段级缺口，不是视图级缺口。

两个动作
--------
1. biz_status 归一：新增 2500 家的企查查「存续」与工商「开业」语义等价，
   五档规则判 biz_status<>'开业' 为 E 预警，若不移规则会把 2497 家全打成 E。
   仅归一 is_sample='0' 且 biz_status IN (存续/在营/正常经营) → '开业'；
   注销/吊销等真实异常保留（应判 E）。原 500 家（开业/NULL）一行不动。
2. 放开 8 个视图：把 FROM (SELECT * FROM dim_enterprise WHERE is_sample='1')
   替换成 FROM dim_enterprise，恢复对 3000 家全量可见。幂等。

用法
----
  python -m src.data.apply_v3_0_fullscope --check   # 只读体检
  python -m src.data.apply_v3_0_fullscope           # 落库
"""
import argparse
import sqlite3
from datetime import datetime

DB_PATH = "data/db/zjtech_credit.db"

# 归一为「开业」的正常经营状态词（企查查口径 -> 工商口径）
NORMAL_STATUS = ("存续", "在营", "正常经营", "在业")

# 需要放开 is_sample 限定的视图白名单（与 apply_v3_0_fill_dim.VIEWS_TO_PIN 对齐，
# v_qual_history 本来就没 pin，不在其中）
VIEWS_TO_UNPIN = [
    "v_enterprise_profile",
    "v_graph_coverage",
    "v_data_coverage",
    "v_enterprise_grade_l0",
    "v_enterprise_label_matrix",
    "v_axis_a_judicial",
    "v_enterprise_person_summary",
    "v_enterprise_trademark_summary",
]

# fill_dim 用来 pin 的两种 SQL 片段（逆操作）
PIN_PATTERNS = [
    # 别名形式：FROM (SELECT * FROM dim_enterprise WHERE is_sample='1') e/d
    ("FROM (SELECT * FROM dim_enterprise WHERE is_sample='1')", "FROM dim_enterprise"),
    # 无别名/子查询形式
    ("FROM dim_enterprise WHERE is_sample='1'", "FROM dim_enterprise"),
    # CROSS JOIN 形式（fill_dim 的别名插入）
    ("FROM dim_enterprise d /* v3.0: 主样本层限定 */ CROSS JOIN (SELECT 1 WHERE d.is_sample='1')",
     "FROM dim_enterprise d"),
]


def _open(check):
    if check:
        return sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
    return sqlite3.connect(DB_PATH)


def norm_status_scan(con):
    """只读扫描需要归一的行。"""
    ph = ",".join("?" * len(NORMAL_STATUS))
    rows = con.execute(
        "SELECT biz_status, COUNT(*) FROM dim_enterprise "
        "WHERE is_sample='0' AND biz_status IN (%s) GROUP BY 1" % ph,
        NORMAL_STATUS).fetchall()
    return rows


def check(con):
    c = con.cursor()
    print("=== v3.0 全量口径体检（只读） ===")
    total = c.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    s1 = c.execute("SELECT COUNT(*) FROM dim_enterprise WHERE is_sample='1'").fetchone()[0]
    s0 = c.execute("SELECT COUNT(*) FROM dim_enterprise WHERE is_sample='0'").fetchone()[0]
    print("  dim_enterprise 总=%d (is_sample=1:%d / =0:%d)" % (total, s1, s0))
    ns = norm_status_scan(con)
    n_norm = sum(n for _, n in ns)
    print("  待归一 biz_status(新增行): %s => 合计 %d" % (ns, n_norm))
    real_bad = c.execute(
        "SELECT biz_status, COUNT(*) FROM dim_enterprise WHERE is_sample='0' "
        "AND biz_status IS NOT NULL AND biz_status NOT IN ('开业','存续','在营','正常经营','在业') "
        "GROUP BY 1").fetchall()
    print("  真实异常状态(保留判E): %s" % (real_bad,))
    still = []
    for v in VIEWS_TO_UNPIN:
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (v,)).fetchone()
        if row and row[0] and "is_sample" in row[0]:
            still.append(v)
    print("  仍含 is_sample 限定的视图 %d 个: %s" % (len(still), still))
    print("  ads_label 可标注: %d" % c.execute(
        "SELECT COUNT(*) FROM ads_label WHERE label_task='qual_survival'").fetchone()[0])
    return {"n_norm": n_norm, "still_pinned": still}


def build(con):
    c = con.cursor()
    # 确保血缘 source 已登记（外键约束），可重复执行
    c.execute("""INSERT OR IGNORE INTO meta_data_source
        (source_id,source_name,source_type,url,license,fetch_mode,cost_cny,
         quota_note,owner,coverage,first_fetch,last_fetch,is_active)
        VALUES ('v3_fullscope','v3.0全量口径整合','internal','','','manual',0,
                'biz_status归一+放开8视图到3000','项目组','3000家',
                datetime('now'),datetime('now'),1)""")
    # 动作1：biz_status 归一（仅新增行）
    ph = ",".join("?" * len(NORMAL_STATUS))
    cur = c.execute(
        "UPDATE dim_enterprise SET biz_status='开业' "
        "WHERE is_sample='0' AND biz_status IN (%s)" % ph, NORMAL_STATUS)
    n_norm = cur.rowcount

    # 动作2：放开视图
    unpinned, no_match = [], []
    for v in VIEWS_TO_UNPIN:
        row = c.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (v,)).fetchone()
        if not row or not row[0]:
            no_match.append((v, "视图不存在"))
            continue
        sql = row[0]
        if "is_sample" not in sql:
            unpinned.append((v, "already"))
            continue
        new_sql = sql
        for pat, rep in PIN_PATTERNS:
            new_sql = new_sql.replace(pat, rep)
        if "is_sample" in new_sql:
            no_match.append((v, "仍有残留 is_sample"))
            continue
        c.execute("DROP VIEW IF EXISTS %s" % v)
        c.execute(new_sql)
        unpinned.append((v, "unpinned"))

    # 血缘登记
    now = datetime.now().isoformat(timespec="seconds")
    c.execute(
        "DELETE FROM meta_ingest_log WHERE source_id=? AND stage=? AND target_table=?",
        ("v3_fullscope", "build", "dim_enterprise+views"))
    c.execute(
        """INSERT INTO meta_ingest_log
           (source_id,stage,target_table,file_path,file_sha256,rows_in,rows_ok,
            rows_bad,started_at,finished_at,status,message)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("v3_fullscope", "build", "dim_enterprise+views", "src/data/apply_v3_0_fullscope.py",
         "", n_norm, n_norm, 0, now, now, "ok",
         "biz_status 归一新增行 %d；放开视图 %s" %
         (n_norm, ",".join(v for v, _ in unpinned))))
    con.commit()
    print("归一新增行 biz_status: %d" % n_norm)
    print("放开视图: %s" % unpinned)
    if no_match:
        print("⚠ 未匹配: %s" % no_match)
    return {"n_norm": n_norm, "unpinned": unpinned, "no_match": no_match}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    con = _open(args.check)
    try:
        if args.check:
            check(con)
        else:
            build(con)
            print("\n=== 落库后复核 ===")
            check(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
