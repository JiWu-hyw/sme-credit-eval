# -*- coding: utf-8 -*-
"""数据字典完整性守卫测试。

防的是这类事故：库结构改了（加表、加列、加视图），但数据字典没重跑，
交付出去的是过期文档 —— 评审照着字典写 SQL，字段不存在或口径已变。

此前实测到的具体欠账：8 个视图只有名字没有字段明细（137 列无说明），
`ods_qcc_annual` 等 3 张表写着「DDL 未写表注释」，头部把 544 表列说成全库列数。

五条断言：
  1. 字典文件存在，且导出时间不早于库文件修改时间（不陈旧）
  2. 章节覆盖：每个表、每个视图都有独立的字段明细段
  3. 字段级一一对应：字典列集合 == PRAGMA 实测列集合（不多不少）
  4. 零空说明：每个字段都有中文说明，没有留白
  5. CSV 版同源：行数一致、含对象类型列、可用于并库映射

用法：python tests/test_data_dictionary.py
"""
import csv
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
MD = os.path.join(ROOT, "outputs", "data_dictionary.md")
CSV = os.path.join(ROOT, "outputs", "data_dictionary.csv")

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:                       # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ✗ %s —— %s: %s" % (name, type(e).__name__, e))


def live_objects():
    """从库里取真实对象与字段：{对象名: (类型, [列名])}。"""
    con = sqlite3.connect(DB)
    cur = con.cursor()
    out = {}
    for typ in ("table", "view"):
        # 先取全量再嵌套查询：同游标内嵌套 execute 会截断外层迭代
        names = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'",
            (typ,)).fetchall()]
        for name in names:
            cols = [r[1] for r in cur.execute('PRAGMA table_info("%s")' % name).fetchall()]
            out[name] = ("表" if typ == "table" else "视图", cols)
    con.close()
    return out


def parse_md():
    """解析字典正文：返回 ({对象名: [列名...]}, {对象名: [说明...]}) 与章节标题集合。"""
    text = open(MD, encoding="utf-8").read()
    cur_obj, cols, notes = None, {}, {}
    for line in text.splitlines():
        h = re.match(r"^###\s+`([^`]+)`", line)
        if h:
            cur_obj = h.group(1)
            cols.setdefault(cur_obj, [])
            notes.setdefault(cur_obj, [])
            continue
        if line.startswith("## "):
            cur_obj = None
            continue
        f = re.match(r"^\|\s*\d+\s*\|\s*`([^`]+)`\s*\|", line)
        if f and cur_obj:
            cols[cur_obj].append(f.group(1))
            # 最后一列是说明
            cell = line.strip().rstrip("|").split("|")[-1].strip()
            notes[cur_obj].append(cell)
    return cols, notes, text


def main():
    print("数据字典守卫")
    cols, notes, text = parse_md()
    live = live_objects()

    def a_not_stale():
        assert os.path.exists(MD), "字典文件不存在：%s" % MD
        assert os.path.getsize(MD) > 10000, "字典文件异常过小"
        assert os.path.getmtime(MD) >= os.path.getmtime(DB) - 1, (
            "字典比库旧：库改于 %s，字典导出于 %s —— 请重跑 "
            "python -m src.data.export_data_dictionary"
            % (os.path.getmtime(DB), os.path.getmtime(MD)))

    def b_all_objects_covered():
        miss_t = [t for t, (k, _) in live.items() if k == "表" and t not in cols]
        miss_v = [v for v, (k, _) in live.items() if k == "视图" and v not in cols]
        assert not miss_t, "以下表无字段明细段：%s" % ",".join(sorted(miss_t))
        assert not miss_v, "以下视图无字段明细段：%s" % ",".join(sorted(miss_v))

    def c_columns_match():
        diff = []
        for obj, (kind, lc) in live.items():
            dc = cols.get(obj)
            if dc is None:
                diff.append("%s（缺整段）" % obj)
                continue
            if set(dc) != set(lc):
                extra = sorted(set(dc) - set(lc))
                lost = sorted(set(lc) - set(dc))
                diff.append("%s（多 %s / 少 %s）" % (obj, extra or "-", lost or "-"))
        assert not diff, "字典与库结构不一致：\n    " + "\n    ".join(diff[:6])

    def d_no_blank_note():
        blank = []
        for obj in cols:
            for col, note in zip(cols[obj], notes[obj]):
                if not note or note in ("—", "-"):
                    blank.append("%s.%s" % (obj, col))
        assert not blank, "%d 个字段无说明：%s" % (len(blank), ",".join(blank[:8]))

    def e_csv_in_sync():
        rows = list(csv.DictReader(open(CSV, encoding="utf-8-sig")))
        total = sum(len(c) for _, c in live.values())
        assert len(rows) == total, "CSV %d 行 ≠ 库内字段总数 %d" % (len(rows), total)
        assert "对象类型" in rows[0], "CSV 缺「对象类型」列（表/视图无法区分）"
        got = {(r["对象类型"], r["对象名"], r["字段名"]) for r in rows}
        want = {(k, o, c) for o, (k, cs) in live.items() for c in cs}
        assert got == want, "CSV 与库结构不一致：缺 %s / 多 %s" % (
            sorted(want - got)[:3], sorted(got - want)[:3])
        assert all(r["字段说明"].strip() for r in rows), "CSV 存在空说明"

    check("字典存在且不早于库", a_not_stale)
    check("表与视图均有字段明细", b_all_objects_covered)
    check("字段级一一对应", c_columns_match)
    check("零空说明", d_no_blank_note)
    check("CSV 与库同源", e_csv_in_sync)

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ DATA DICTIONARY TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
