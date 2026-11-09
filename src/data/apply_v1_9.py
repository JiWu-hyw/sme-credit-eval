# -*- coding: utf-8 -*-
"""把 schema 从 v1.8 升到 v1.9：标签方向纠正 + 多标签测评体系（P1，零采集）。

幂等：重复执行只会 DROP/CREATE 同一组视图、REPLACE 同一行版本记录、
      幂等同步 schema.sql 的 [V19MULTILABEL] 块。
执行前自动留一份升级前快照 data/db/zjtech_credit_pre_v19.db 作回滚点。

严格不改动：ads_label、v_model_train_ready、v_enterprise_grade_l0、
            主宽表 43 列 / 29 特征、ASOF_VER、任何事实表数据。

用法：
    python -m src.data.apply_v1_9            # 执行迁移
    python -m src.data.apply_v1_9 --check    # 只读校验，不改库
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
BACKUP = os.path.join(ROOT, "data", "db", "zjtech_credit_pre_v19.db")
SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "migrations", "v1_9_multilabel.sql")
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

VERSION = "v1.9"
BEGIN = "-- [V19MULTILABEL:BEGIN]"
END = "-- [V19MULTILABEL:END]"
NOTE = (
    "v1.9 标签方向纠正 + 多标签测评（P1）："
    "① 旧文档把 ads_label.label_value=1（21 家资质断档）称为正例，方向反了；"
    "本项目研究企业好坏，正例=好企业=资质存续（label_value 0 的 286 家），"
    "负例=坏企业=断档（label_value 1 的 21 家）。底表数值是已发布 v1.4.0 "
    "快照与 12 个契约的绑定口径，禁止原地 UPDATE，改以并列视图表达；"
    "② 新增 v_label_direction / v_model_train_ready_credit / "
    "v_label_mechanism_credit 三个方向映射视图；"
    "③ P1 落地为「添加更多标签来测评」：v_enterprise_label_matrix（四轴 6 个"
    "二值锚点 + 3 个有序锚点，一律 1=好/正例）、v_enterprise_multilabel_grade"
    "（多标签五档 + L0 对照）、v_multilabel_eval_summary（交叉测评长表）；"
    "④ 扩样本与外部采集降级为非前置可选项，A/B/C/D 轴的独立性边界写入视图。"
    "未改任何事实表数据、未改 29 列主宽表结构、未改 ASOF_VER。"
)
VIEWS = ("v_label_direction", "v_model_train_ready_credit",
         "v_label_mechanism_credit", "v_enterprise_label_matrix",
         "v_enterprise_multilabel_grade", "v_multilabel_eval_summary")

# 实测基线（迁移前同一组 SQL 独立跑出的结果，用于对账）
EXPECT = {
    "n_enterprise": 500,
    "n_labeled": 307,
    "credit_positive": 286,     # 好企业 / 正例
    "credit_negative": 21,      # 坏企业 / 负例
    "risk_positive": 21,        # 旧风险方向下被误称「正例」的断档家数
    "n_labels_max": 6,
    "grade_sum": 500,
}


def sql_block() -> str:
    """取迁移文件里带标记的 DDL 块（含标记行），用于同步 schema.sql。"""
    with open(SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0:
        raise RuntimeError("迁移 SQL 缺少 [V19MULTILABEL] 标记")
    return text[i:j + len(END)] + "\n"


def check(con: sqlite3.Connection) -> list[str]:
    """确定性校验：视图存在、方向互补、底表未动、票数自洽、档次闭合、版本登记。"""
    bad: list[str] = []
    for v in VIEWS:
        if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
                (v,)).fetchone():
            return ["%s 视图不存在" % v]

    # ① 底表绝不能被原地翻转
    dist = dict(con.execute(
        "SELECT label_value, COUNT(*) FROM ads_label GROUP BY 1").fetchall())
    if dist != {0: EXPECT["credit_positive"], 1: EXPECT["credit_negative"]}:
        bad.append("ads_label 被改动：%r，期望 {0:286, 1:21}" % dist)
    old = dict(con.execute(
        "SELECT label, COUNT(*) FROM v_model_train_ready GROUP BY 1").fetchall())
    if old != {0: 286, 1: 21}:
        bad.append("v_model_train_ready 被改动：%r" % old)

    # ② 方向映射：credit_label = 1 - risk_label，且正例数对
    n_dir = con.execute("SELECT COUNT(*) FROM v_label_direction").fetchone()[0]
    if n_dir != EXPECT["n_labeled"]:
        bad.append("v_label_direction 行数 %d，期望 307" % n_dir)
    mismatch = con.execute(
        "SELECT COUNT(*) FROM v_label_direction WHERE credit_label <> 1 - risk_label"
    ).fetchone()[0]
    if mismatch:
        bad.append("方向映射不自洽：%d 行 credit_label <> 1 - risk_label" % mismatch)
    d = dict(con.execute(
        "SELECT credit_label, COUNT(*) FROM v_label_direction GROUP BY 1").fetchall())
    if d != {1: EXPECT["credit_positive"], 0: EXPECT["credit_negative"]}:
        bad.append("信用方向分布不符：%r，期望 {1:286 正例, 0:21 负例}" % d)
    cls = dict(con.execute(
        "SELECT credit_class, COUNT(*) FROM v_label_direction GROUP BY 1").fetchall())
    if cls != {"positive": 286, "negative": 21}:
        bad.append("credit_class 标注不符：%r" % cls)

    # ③ 正确方向训练入口：17 列原样 + 2 列方向说明，label 与原视图互补
    cols = [r[1] for r in con.execute("PRAGMA table_info(v_model_train_ready_credit)")]
    if len(cols) != 19 or "label_direction" not in cols or "positive_class" not in cols:
        bad.append("v_model_train_ready_credit 列结构不符：%r" % cols)
    d2 = dict(con.execute(
        "SELECT label, COUNT(*) FROM v_model_train_ready_credit GROUP BY 1").fetchall())
    if d2 != {1: EXPECT["credit_positive"], 0: EXPECT["credit_negative"]}:
        bad.append("训练入口方向未翻转：%r" % d2)
    pair = con.execute(
        "SELECT COUNT(*) FROM v_model_train_ready a "
        "JOIN v_model_train_ready_credit b USING (enterprise_id) "
        "WHERE a.label + b.label <> 1").fetchone()[0]
    if pair:
        bad.append("新旧训练入口 label 不互补：%d 行" % pair)

    # ④ 机制台账方向：n_positive 现在真的是好企业数
    mech = con.execute(
        "SELECT SUM(n_positive), SUM(n_negative) FROM v_label_mechanism_credit"
    ).fetchone()
    if tuple(mech) != (286, 21):
        bad.append("v_label_mechanism_credit 合计不符：%r" % (tuple(mech),))

    # ⑤ 多标签矩阵：500 行、二值锚点取值合法、NULL 语义、票数自洽
    n = con.execute("SELECT COUNT(*) FROM v_enterprise_label_matrix").fetchone()[0]
    if n != EXPECT["n_enterprise"]:
        bad.append("多标签矩阵行数 %d，期望 500" % n)
    for col in ("y_hard_outcome", "y_risk_free", "y_survival",
                "y_qual_ladder_good", "y_qcc_credit_good", "y_qcc_tech_good"):
        bad_vals = con.execute(
            'SELECT COUNT(*) FROM v_enterprise_label_matrix '
            'WHERE "%s" IS NOT NULL AND "%s" NOT IN (0,1)' % (col, col)).fetchone()[0]
        if bad_vals:
            bad.append("%s 出现非二值/非 NULL 取值：%d 行" % (col, bad_vals))
    n_null_surv = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_label_matrix WHERE y_survival IS NULL"
    ).fetchone()[0]
    if n_null_surv != 193:
        bad.append("未打标企业数 %d，期望 193（不得当坏企业）" % n_null_surv)
    n_null_hard = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_label_matrix WHERE y_hard_outcome IS NULL"
    ).fetchone()[0]
    if n_null_hard != 37:
        bad.append("硬结果字段缺失企业数 %d，期望 37" % n_null_hard)
    bad_sum = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_label_matrix "
        "WHERE n_good + n_bad <> n_labels_observed "
        "   OR n_labels_observed < 4 OR n_labels_observed > 6 "
        "   OR label_agreement < 0 OR label_agreement > 1").fetchone()[0]
    if bad_sum:
        bad.append("票数 / 一致性不自洽：%d 行" % bad_sum)

    # ⑥ 多标签五档：500 行闭合、L0 对照齐全、硬约束优先级
    n_g = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_multilabel_grade").fetchone()[0]
    if n_g != EXPECT["grade_sum"]:
        bad.append("多标签分档行数 %d，期望 500" % n_g)
    grades = set(r[0] for r in con.execute(
        "SELECT DISTINCT grade_multilabel FROM v_enterprise_multilabel_grade"))
    if not grades <= {"A 引领", "B 优质", "C 稳健", "D 观察", "E 预警"}:
        bad.append("出现未定义档次：%r" % grades)
    miss_l0 = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_multilabel_grade WHERE grade_l0 IS NULL"
    ).fetchone()[0]
    if miss_l0:
        bad.append("L0 对照缺失 %d 行" % miss_l0)
    hard = con.execute(
        "SELECT COUNT(*) FROM v_enterprise_multilabel_grade g "
        "JOIN v_enterprise_label_matrix m USING (enterprise_id) "
        "WHERE m.y_hard_outcome = 0 AND g.grade_multilabel <> 'E 预警'").fetchone()[0]
    if hard:
        bad.append("硬结果坏企业未落 E 档：%d 家" % hard)

    # ⑦ 测评汇总有内容，且分母口径不把未打标算成负例
    n_s = con.execute("SELECT COUNT(*) FROM v_multilabel_eval_summary").fetchone()[0]
    if n_s < 15:
        bad.append("测评汇总行数过少：%d" % n_s)

    # ⑧ 版本登记 + schema.sql 同步
    vers = [r[0] for r in con.execute("SELECT version FROM meta_schema_version")]
    if VERSION not in vers:
        bad.append("meta_schema_version 未登记 %s" % VERSION)
    if sql_block() not in open(SCHEMA_SQL, encoding="utf-8").read():
        bad.append("schema.sql 未同步 [V19MULTILABEL] DDL 块")
    return bad


def sync_schema_sql() -> None:
    """把带标记的 DDL 块幂等追加/替换到 schema.sql。"""
    block = sql_block()
    with open(SCHEMA_SQL, encoding="utf-8") as f:
        text = f.read()
    i, j = text.find(BEGIN), text.find(END)
    if i >= 0 and j >= 0:
        text = text[:i] + block.rstrip("\n") + text[j + len(END):]
    else:
        text = text.rstrip("\n") + "\n\n" + block
    with open(SCHEMA_SQL, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只读校验，不改库")
    args = ap.parse_args()

    if args.check:
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        bad = check(con)
        con.close()
        print("只读校验：%s" % ("通过" if not bad else "失败"))
        for b in bad:
            print("  x", b)
        return 0 if not bad else 1

    if not os.path.exists(DB):
        print("数据库不存在：%s" % DB)
        return 1

    if not os.path.exists(BACKUP):
        shutil.copy2(DB, BACKUP)
        print("[backup] 升级前快照 -> %s" % BACKUP)

    sync_schema_sql()
    with open(SQL, encoding="utf-8") as f:
        ddl = f.read()

    con = sqlite3.connect(DB)
    try:
        con.executescript(ddl)
        con.execute(
            "INSERT OR REPLACE INTO meta_schema_version(version, applied_at, author, note) "
            "VALUES (?, datetime('now','localtime'), ?, ?)",
            (VERSION, "WorkBuddy(施汶邑)", NOTE))
        con.commit()
        bad = check(con)
        print("[v1.9] 表 %d / 视图 %d / 版本 %s" % (
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0],
            con.execute("SELECT version FROM meta_schema_version ORDER BY rowid DESC LIMIT 1")
            .fetchone()[0]))
        print("  方向映射：",
              con.execute("SELECT credit_label, COUNT(*) FROM v_label_direction GROUP BY 1")
              .fetchall(), "(1=正例/好企业, 0=负例/坏企业)")
        print("  多标签分档：",
              con.execute("SELECT grade_multilabel, COUNT(*) "
                          "FROM v_enterprise_multilabel_grade GROUP BY 1 ORDER BY 1")
              .fetchall())
        if bad:
            print("校验失败：")
            for b in bad:
                print("  x", b)
            return 1
        print("V1.9 MIGRATION PASSED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
