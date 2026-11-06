# -*- coding: utf-8 -*-
"""缺口登记册守卫测试。

防的是同一类事故：某天有人为了「让报表好看」把 T5 硬填了，
库里看起来完整了，评审一问口径就崩。

三条断言：
  1. 登记册里每个字段都真实存在于库中（登记册不能指向不存在的列）
  2. 状态为「设计性 NULL / 源未接入」的列，实测必须 100% 为空
  3. 上述列一旦有值，必须是「先改登记册、再改库」，不能悄悄回填

用法：python tests/test_field_gap_registry.py
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
REG = os.path.join(ROOT, "configs", "field_gap_registry.json")
MUST_BE_EMPTY = {"null_by_design", "source_missing"}

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


def _cols(cur, table):
    return {r[1] for r in cur.execute("PRAGMA table_info(%s)" % table)}


def main():
    with open(REG, encoding="utf-8") as f:
        reg = json.load(f)
    items = reg["items"]
    con = sqlite3.connect(DB)
    cur = con.cursor()

    def a_exists():
        bad = []
        for it in items:
            if it["table"] not in {
                r[0] for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }:
                bad.append("%s（表缺失）" % it["table"])
            elif it["field"] not in _cols(cur, it["table"]):
                bad.append("%s.%s（列缺失）" % (it["table"], it["field"]))
        _assert(not bad, "; ".join(bad))

    def b_empty():
        dirty = []
        for it in items:
            if it["status"] not in MUST_BE_EMPTY:
                continue
            t, c = it["table"], it["field"]
            n = cur.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            nn = cur.execute('SELECT COUNT("%s") FROM %s' % (c, t)).fetchone()[0]
            if nn:
                dirty.append("%s.%s 有 %d/%d 个非空值" % (t, c, nn, n))
        _assert(not dirty, "登记为空却出现值（口径被破坏）：" + "; ".join(dirty))

    def c_enum():
        allowed = set(reg["meta"]["status_enum"])
        bad = [i["field"] for i in items if i["status"] not in allowed]
        _assert(not bad, "非法状态：%s" % ", ".join(bad))

    def e_substitutes():
        """替身必须真的落在库里且有值 —— 否则「替身」只是 PPT 上的一句话。"""
        expect = {
            "patent_lapse_ratio": 400, "patent_reject_ratio": 400,
            "n_patent_lapsed": 400, "n_patent_rejected": 400,
            "latest_report_year": 400, "latest_rev_wan": 5,
            "latest_net_profit_wan": 5, "latest_total_assets_wan": 5,
            "latest_tax_wan": 5, "latest_emp_num": 10,
        }
        bad = []
        for col, floor in expect.items():
            tab = "dws_tech_feature" if col in (
                "patent_lapse_ratio", "patent_reject_ratio",
                "n_patent_lapsed", "n_patent_rejected") else "dws_growth_feature"
            if col not in _cols(cur, tab):
                bad.append("%s.%s 列缺失" % (tab, col))
                continue
            nn = cur.execute('SELECT COUNT("%s") FROM %s' % (col, tab)).fetchone()[0]
            if nn < floor:
                bad.append("%s 只有 %d 个非空（期望≥%d）" % (col, nn, floor))
        # 申请年：申请号能解出年份的都应补上
        left = cur.execute(
            "SELECT COUNT(*) FROM fact_patent WHERE app_year IS NULL").fetchone()[0]
        if left:
            bad.append("app_year 仍缺 %d 条" % left)
        _assert(not bad, "; ".join(bad))

    def d_fields():
        need = {"table", "field", "indicator", "status", "reason", "remedy",
                "impact", "decided_at", "decided_by"}
        bad = []
        for i in items:
            miss = need - set(i)
            if miss:
                bad.append("%s 缺 %s" % (i.get("field", "?"), ",".join(sorted(miss))))
        _assert(not bad, "; ".join(bad))

    print("缺口登记册守卫（%d 项登记）" % len(items))
    check("登记字段均存在于库", a_exists)
    check("设计性 NULL 未被回填", b_empty)
    check("状态枚举合法", c_enum)
    check("每条登记信息完整", d_fields)
    check("替身已落库且有值", e_substitutes)
    con.close()

    print("=" * 60)
    if FAIL:
        print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
        return 1
    print("✅ FIELD GAP REGISTRY TESTS PASSED（通过 %d 项）" % len(PASS))
    return 0


def _assert(cond, detail=None):
    assert cond, "实际值：%r" % (detail,)


if __name__ == "__main__":
    sys.exit(main())
