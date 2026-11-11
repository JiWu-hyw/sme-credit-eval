"""issue ① 官方撤销公告核查守卫（10 项，只读）。

守的是三件事，任何一件被破坏都必须当场失败：
  1. 证据链：21 家逐户核查表、287 家官方名单、7 条公告清单（含 5 份扫描件读图、
     官方更名标注单页证据）与三层 0 命中比对的数字自洽；
  2. 口径隔离：审计断档率 14/307 与库内 21/307 不得混用，文档必须同时出现两者；
  3. 未改库：ads_label 仍是 307/21/286，DEFINITION_VER 仍是 v0.1。

同时核对交付目录与 outputs / data/audit 字节同源，防止「只改了一份、
另一份还是旧口径」这类历史踩过的坑。

运行：python tests/test_official_cancel_check.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # 与项目其他守卫一致：库用相对于仓库根的路径连接
sys.path.insert(0, str(ROOT))
AUDIT = ROOT / "data" / "audit"
OUT = ROOT / "outputs"
D_ALG = ROOT / "deliverables" / "01_算法端"
DB = ROOT / "data" / "db" / "zjtech_credit.db"

CHECK_CSV = AUDIT / "标签核查_21家断档逐户.csv"
OFFICIAL_CSV = AUDIT / "官方_取消高企资格_浙江名单.csv"
NOTICE_JSON = AUDIT / "公告清单_浙江宁波7条.json"
URL_JSON = AUDIT / "公告清单_原始URL.json"
RENAME_PNG = AUDIT / "证据_2026-02第12条_官方更名标注.png"
REPORT_MD = OUT / "official_cancel_check.md"
REPORT_JSON = OUT / "official_cancel_check.json"

DELIVERED = [
    ("official_cancel_check.md", REPORT_MD),
    ("official_cancel_check.json", REPORT_JSON),
    ("标签核查_21家断档逐户.csv", CHECK_CSV),
    ("官方_取消高企资格_浙江名单.csv", OFFICIAL_CSV),
    ("公告清单_浙江宁波7条.json", NOTICE_JSON),
    ("证据_2026-02第12条_官方更名标注.png", RENAME_PNG),
]

FAIL: list[tuple[str, str]] = []


def check(name, fn):
    try:
        fn()
        print("  ✓ " + name)
    except Exception as e:  # noqa: BLE001 - 守卫要把任何异常都当成失败
        FAIL.append((name, str(e)))
        print("  ✗ %s -> %s" % (name, e))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def a_evidence_files():
    for p in (CHECK_CSV, OFFICIAL_CSV, NOTICE_JSON, URL_JSON,
              RENAME_PNG, REPORT_MD, REPORT_JSON):
        assert p.exists() and p.stat().st_size > 0, "证据/报告缺失或为空：%s" % p
    for name, src in DELIVERED:
        d = D_ALG / name
        assert d.exists(), "交付目录缺 %s" % name


def b_per_enterprise_counts():
    rows = list(csv.DictReader(CHECK_CSV.open(encoding="utf-8-sig", newline="")))
    assert len(rows) == 21, "逐户核查表应为 21 行，实得 %d" % len(rows)
    alias = [r for r in rows if r["是否应修正为0"] == "是"]
    real = [r for r in rows if r["是否应修正为0"] == "否"]
    hit = [r for r in rows if r["官方撤销名单命中"] == "是"]
    assert len(alias) == 7, "改名假象应为 7 家，实得 %d" % len(alias)
    assert len(real) == 14, "真断档候选应为 14 家，实得 %d" % len(real)
    assert not hit, "官方撤销名单出现命中，需重新核查：%r" % [r["企业名称"] for r in hit]
    assert all(r["官方撤销名单命中"] == "否" for r in rows), "命中列出现非「否」取值"


def c_official_name_list():
    rows = list(csv.DictReader(OFFICIAL_CSV.open(encoding="utf-8-sig", newline="")))
    assert len(rows) == 287, "官方名单应为 287 条（7 份公告全覆盖），实得 %d" % len(rows)
    c = Counter(r["公告年月"] for r in rows)
    assert dict(c) == {"2022-12": 4, "2023-03": 205, "2023-12": 14,
                       "2025-07": 4, "2026-02": 13, "2026-08": 47}, (
        "公告年月分布漂移（2023-12 应为 14 条、非 16 条）：%r" % dict(c))
    names = [r["企业名称"] for r in rows]
    assert len(set(names)) == 287 and all(n.strip() for n in names), (
        "官方名单存在重名或空名")
    assert sum(c.values()) == 287, "按公告年月合计与 287 家不符"


def d_notice_manifest():
    d = json.loads(read(NOTICE_JSON))
    notices = d["公告"]
    assert d.get("全国栏目公告数") == 585, "全国公告数未登记 585"
    assert len(notices) == 7, "浙江宁波公告应为 7 条，实得 %d" % len(notices)
    scanned = [n for n in notices if n["公告类型"] == "扫描件"]
    assert len(scanned) == 5, "扫描件公告应为 5 条，实得 %d" % len(scanned)
    assert sum(n["公告标题家数"] for n in scanned) == 68, "扫描件标题家数应为 68"
    assert sum(n["公告标题家数"] for n in notices) == 287, "7 份公告合计应为 287 家"
    assert all(n["PDF已落盘"] for n in notices), "仍有公告 PDF 未落盘"
    assert all(n.get("已提取家数") == n["公告标题家数"] for n in notices), (
        "存在公告提取家数与标题声明不一致（漏读）")
    assert d["PDF份数"] == 7 and d["PDF缺失份数"] == 0, "PDF 落盘统计漂移"
    assert d["扫描件读图页数"] == 19 and d["渲染页总数"] == 27, (
        "渲染页统计漂移：5 份扫描件 19 页 + 2 份文本型 8 页 = 27 页")
    feb = next(n for n in notices if n["公告年月"] == "2026-02"
               and n["认定机构"] == "浙江省")
    assert feb["更名标注"]["序号"] == 12 and (
        feb["更名标注"]["更名后"] == "浙江建凡贸易有限公司"), (
        "2026-02 第 12 条官方更名标注证据缺失")


def e_report_numbers():
    t = read(REPORT_MD)
    for kw in ("7 家改名假象", "14 家真断档", "0 家", "4.56%", "6.84%",
               "口径隔离", "保留指标", "未改库", "21 家逐户",
               "287 家", "三层 0 命中", "更名标注", "1.3%"):
        assert kw in t, "报告缺关键表述 %r" % kw
    assert "14/307" in t and "21/307" in t, "报告未同时给出两个断档率"
    assert "qual_survival_proxy_v0.1" in t, "报告未标注库内定义版本未升版"
    assert "无漏读" in t, "报告未声明公告全覆盖无漏读"
    assert "不再存在「未落盘 / 待 OCR」项" in t, (
        "报告未声明扫描件核读 / PDF 落盘缺口已闭合")
    for stale in ("PDF 未落盘；", "需 OCR / 人工核读", "标题共 68 家）未核读",
                  "证据链尚未「完全排除」"):
        assert stale not in t, "报告残留未闭合旧措辞 %r" % stale


def f_report_json():
    p = json.loads(read(REPORT_JSON))
    assert p["verdict"] == "代理标签量的是资质再申报行为，不是官方撤销结论", (
        "JSON 结论漂移：%r" % p.get("verdict"))
    pe = p["per_enterprise_check"]
    assert (pe["断档样本数"], pe["改名假象数"], pe["真断档候选数"],
            pe["官方撤销名单命中数"]) == (21, 7, 14, 0), "JSON 逐户数字漂移"
    src = p["source_channel"]
    assert (src["浙江宁波公告数"], src["已提取家数"], src["标题声明家数"],
            src["PDF份数"], src["PDF缺失份数"], src["扫描件读图页数"]) == (
            7, 287, 287, 7, 0, 19), "JSON 公告覆盖数字漂移"
    assert src["无漏读"] is True, "JSON 未声明无漏读"
    assert p["official_names"]["已提取记录数"] == 287, "JSON 官方名单条数漂移"
    assert p["official_names"]["去重企业名数"] == 287, "JSON 去重企业名数漂移"
    cm = p["coverage_match"]
    assert (cm["样本企业数"], cm["样本命中数"], cm["已标注企业数"],
            cm["已标注命中数"], cm["断档企业数"], cm["断档命中数"]) == (
            500, 0, 307, 0, 21, 0), "JSON 三层 0 命中比对漂移"
    assert p["official_rename_evidence"]["更名后"] == "浙江建凡贸易有限公司", (
        "JSON 缺官方更名标注佐证")
    assert p["label_position"]["并列锚点数"] == 6, "未登记为 6 个锚点之一"
    assert p["label_position"]["是否独立"] is False, "C 轴不得标为独立"
    a = p["a_axis"]
    assert a["完整正例集合"] == 287 and a["与500家样本交集"] == 0 and (
        a["对本样本可用正例数"] == 0), "issue ② A 轴定位漂移：样本内 0 交集不得写成已补齐"
    db = p["db_state"]
    assert (db["n_label"], db["n_fail"], db["n_keep"]) == (307, 21, 286), (
        "JSON 库内状态漂移")
    assert len(p["not_closed"]) >= 4, "未闭合事项未列全"


def g_delivery_same_source():
    for name, src in DELIVERED:
        d = D_ALG / name
        assert md5(d) == md5(src), "交付份与源份不同源：%s" % name
    # 标签说明书与多标签测评报告的两处副本也要同源
    for dst in (D_ALG / "label_spec.md",
                ROOT / "deliverables" / "04_数据字典与数据集" / "代理标签说明书.md"):
        assert md5(dst) == md5(OUT / "label_spec.md"), "说明书副本不同源：%s" % dst
    for dst in (D_ALG / "multilabel_eval_report.md",
                ROOT / "deliverables" / "04_数据字典与数据集" / "多标签测评报告.md"):
        assert md5(dst) == md5(OUT / "multilabel_eval_report.md"), (
            "多标签测评报告副本不同源：%s" % dst)


def h_db_untouched():
    con = sqlite3.connect("file:data/db/zjtech_credit.db?mode=ro", uri=True)
    c = con.cursor()
    d = c.execute("SELECT COUNT(*), SUM(label_value=1), SUM(label_value=0) "
                  "FROM ads_label").fetchone()
    assert tuple(d) == (307, 21, 286), "ads_label 被改动：%r" % (tuple(d),)
    direction = dict(c.execute(
        "SELECT credit_label, COUNT(*) FROM v_label_direction GROUP BY 1").fetchall())
    assert direction == {0: 21, 1: 286}, "方向视图分布漂移：%r" % direction
    grades = dict(c.execute(
        "SELECT grade_multilabel, COUNT(*) FROM v_enterprise_multilabel_grade "
        "GROUP BY 1").fetchall())
    assert grades == {"A 引领": 81, "B 优质": 176, "C 稳健": 157,
                      "D 观察": 83, "E 预警": 3}, "多标签分档被改动：%r" % grades
    con.close()
    sys.path.insert(0, str(ROOT))
    from src.data.build_qual_dataset import DEFINITION_VER
    assert DEFINITION_VER == "qual_survival_proxy_v0.1", (
        "DEFINITION_VER 已升版，本守卫与报告口径需同步：%s" % DEFINITION_VER)


def i_isolation_in_docs():
    docs = [
        D_ALG / "label_spec.md",
        D_ALG / "dataset_card.md",
        D_ALG / "multilabel_eval_report.md",
        ROOT / "deliverables" / "03_交付说明" / "delivery_spec.md",
        ROOT / "deliverables" / "RELEASE_v1.5.0.md",
        ROOT / "deliverables" / "00_先看这个.md",
    ]
    for p in docs:
        t = read(p)
        assert "官方" in t and ("official_cancel_check" in t or "官方撤销公告" in t), (
            "%s 未登记官方撤销公告核查" % p.relative_to(ROOT))
    for p in docs[:5]:
        t = read(p)
        assert "4.56%" in t, "%s 缺审计断档率 4.56%%" % p.relative_to(ROOT)
        assert "口径隔离" in t or "禁止混用" in t or "审计结论" in t, (
            "%s 缺口径隔离声明" % p.relative_to(ROOT))
    reg = json.loads(read(ROOT / "configs" / "field_gap_registry.json"))
    task = next(t for t in reg["label_tasks"]["tasks"]
                if t["label_task"] == "qual_survival")
    q = task["official_cancel_check"]
    assert q["官方撤销名单命中数"] == 0 and q["改名假象数"] == 7, "登记册核查结论漂移"
    assert q["已提取企业名记录数"] == 287 and q["PDF缺失份数"] == 0, (
        "登记册未同步 287 家全覆盖 / 0 缺 PDF")
    m = q["全量比对_含别名"]
    assert (m["500家样本命中"], m["307家已标注命中"], m["21家断档命中"]) == (0, 0, 0), (
        "登记册三层 0 命中漂移")
    assert q["官方更名佐证"]["更名后"] == "浙江建凡贸易有限公司", (
        "登记册缺官方更名标注佐证")
    assert "保留指标" in task["label_position"], "登记册未写明「保留指标」定位"
    assert "official_cancel_check" in reg["policies"], "登记册缺 issue ① 策略"
    a = reg["policies"]["a_axis_hard_outcome"]
    assert "287 家" in a["rule"] and "交集为 0" in a["caveat"] and (
        "不能直接宣称 A 轴已补齐" in a["caveat"]), (
        "登记册缺 issue ② A 轴诚实口径（样本内 0 交集 → 未补齐）")
    assert "复合负面事件" in a["next"], "登记册未给出复合负面事件的下一步"


def j_reproducible():
    src = read(ROOT / "src" / "data" / "export_official_cancel_check.py")
    assert "不改库" in src and "口径隔离" in src, "生成源缺口径声明"
    delivery = read(ROOT / "src" / "data" / "export_delivery.py")
    for kw in ("official_cancel_check.md", "标签核查_21家断档逐户.csv",
               "证据_2026-02第12条_官方更名标注.png", "287 家",
               "298 项", "26 个测试文件"):
        assert kw in delivery, "export_delivery.py 未同步 %r" % kw
    for p in (ROOT / "README.md",
              ROOT / "deliverables" / "03_交付说明" / "delivery_spec.md",
              ROOT / "deliverables" / "RELEASE_v1.5.0.md",
              ROOT / "deliverables" / "00_先看这个.md",
              ROOT / "tests" / "test_funding_join_contract.py"):
        assert "298 项" in read(p), "%s 守卫项数未同步为 286" % p.relative_to(ROOT)
    n = len(list((ROOT / "tests").glob("test_*.py")))
    assert n == 26, "tests/ 下应为 25 个守卫文件，实得 %d" % n


def main():
    print("=" * 68)
    print("  官方撤销公告核查守卫（issue ①）")
    print("=" * 68)
    for name, fn in [
        ("证据快照与报告产物齐备", a_evidence_files),
        ("21 家逐户判定 7 改名 / 14 真断档 / 0 命中", b_per_enterprise_counts),
        ("官方名单 287 条与 7 个公告年月分布", c_official_name_list),
        ("7 份公告全覆盖 / 5 扫描件 19 页 / 0 缺 PDF / 更名佐证", d_notice_manifest),
        ("报告含结论、双断档率、三层 0 命中与口径隔离", e_report_numbers),
        ("JSON 结论、287 家覆盖与 issue ② 定位一致", f_report_json),
        ("交付目录与 outputs / audit 字节同源", g_delivery_same_source),
        ("库未改、DEFINITION_VER 未升版", h_db_untouched),
        ("口径隔离写入各交付文档与缺口登记册", i_isolation_in_docs),
        ("生成源可复现、守卫数字同步 26 文件 298 项", j_reproducible),
    ]:
        check(name, fn)
    print("=" * 68)
    if FAIL:
        print("失败 %d 项：" % len(FAIL))
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        return 1
    print("通过 10 项，失败 0 项")
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
