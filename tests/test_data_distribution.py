# -*- coding: utf-8 -*-
"""企查查数据「全部公开」分发口径守卫（10 项，只读）。

2026-11-12 用户最终拍板「数据全部都上传公开」，推翻同日早些时候的
「去标识 + 学术声明 + 从 Release 撤下」中间方案。本守卫守四件事：
  1. 口径定稿：登记册 v5 / policies.data_distribution，旧「待拍板」表述清零；
  2. 两份声明齐备且写明来源、公开范围、建议用途与残余风险；
  3. 完整库未脱敏：交付快照与工作库自然人字段逐值一致，无 PID_… 假名化残留；
  4. 分发链路闭合：RELEASE_PARTS 含 05_数据库、无撤下教程、生成源与守卫数字同源。

运行：python tests/test_data_distribution.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

DB_WORK = ROOT / "data" / "db" / "zjtech_credit.db"
DB_PUB = ROOT / "deliverables" / "05_数据库" / "zjtech_credit.db"
REG = ROOT / "configs" / "field_gap_registry.json"
DEL = ROOT / "deliverables"
D_ALG = DEL / "01_算法端"

PROVENANCE = DEL / "DATA_PROVENANCE.md"
LICENSE = DEL / "DATA_LICENSE.md"
TAKEDOWN = DEL / "Release_数据库附件撤下教程.md"

PERSON_FIELDS = (
    ("ods_qcc_person", "person_name", 2079),
    ("ods_qcc_judicial", "applicant", 34),
    ("ods_qcc_axis_a_snapshot", "person_names_raw", 490),
    ("ods_qcc_axis_a_snapshot", "case_sample_raw", 6),
)
# 只禁「断言式旧口径」，不误伤新文案里「不做去标识化 / 不撤下 / 不私下交付」的否定说明
STALE_PHRASES = (
    "RELEASE_PARTS` 已移除", "RELEASE_PARTS 已移除",
    "不再包含 `05_数据库/`", "自 v1.5.0 起不再包含数据库",
    "改**私下渠道**", "发布版做自然人字段去标识化",
    "服务条款与公开分发范围待确认", "Release 附件发布前需单独拍板",
    "Release_数据库附件撤下教程.md", "PID_",
)
DOC_FILES = (
    ROOT / "README.md", DEL / "00_先看这个.md", DEL / "RELEASE_v1.5.0.md",
    DEL / "03_交付说明" / "delivery_spec.md", PROVENANCE, LICENSE,
    D_ALG / "product_node_report.md", D_ALG / "axis_a_judicial_report.md",
)
SRC_FILES = (
    ROOT / "src" / "data" / "export_delivery.py",
    ROOT / "src" / "data" / "export_product_node_report.py",
    ROOT / "src" / "data" / "export_axis_a_judicial_report.py",
    ROOT / "src" / "data" / "export_db_bundle.py",
    ROOT / "src" / "data" / "export_data_provenance.py",
    # 这两个迁移脚本的 meta_data_source.quota_note 会写进库，也要跟着定稿
    ROOT / "src" / "data" / "apply_v2_0_axis_a.py",
    ROOT / "src" / "data" / "apply_v2_1_product_node.py",
)
FAIL: list[tuple[str, str]] = []


def read(p) -> str:
    return Path(p).read_text(encoding="utf-8")


def con(path) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(), uri=True)


def check(name, fn):
    try:
        fn()
        print("  ✓ " + name)
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, str(e)))
        print("  ✗ %s -> %s" % (name, e))


def a_registry_decided():
    r = json.loads(read(REG))
    assert r["meta"]["version"] == "v5", "登记册未升 v5"
    p = r["policies"]["data_distribution"]
    for k in ("rule", "source", "public_scope", "forbid", "history",
              "residual_risk", "contract", "check", "decided_at", "decided_by"):
        assert k in p and p[k].strip(), "policy 缺 %s" % k
    assert p["decided_at"] == "2026-11-12"
    assert "全部公开" in p["rule"] and "禁止加入任何去标识化步骤" in p["rule"]
    assert "不脱敏" in p["public_scope"], "public_scope 未写明不脱敏"
    assert "非官方 API 采购" in p["source"], "来源未写明非采购 API"
    assert "保持不动" in p["history"], "history 未写明历史附件不动"
    for k in ("v2_0_axis_a_judicial", "v2_1_product_node"):
        cav = r["policies"][k]["release_caveat"]
        assert "已定稿" in cav and "全部公开" in cav, (
            "%s 的 release_caveat 仍是旧口径：%r" % (k, cav))


def b_no_stale_wording():
    for p in DOC_FILES + SRC_FILES:
        t = read(p)
        for kw in STALE_PHRASES:
            assert kw not in t, "%s 残留旧口径 %r" % (p.relative_to(ROOT), kw)
    for p in (ROOT / "README.md", DEL / "00_先看这个.md",
              DEL / "RELEASE_v1.5.0.md", PROVENANCE):
        assert "全部公开" in read(p), "%s 未写明全部公开" % p.name
    for p in (D_ALG / "product_node_report.md",
              D_ALG / "axis_a_judicial_report.md"):
        t = read(p)
        assert "待单独拍板" not in t, "%s 仍写「待单独拍板」" % p.name
        assert "DATA_PROVENANCE.md" in t, "%s 未指向数据来源声明" % p.name
    c = con(DB_WORK)
    try:
        rows = c.execute(
            "SELECT source_id, quota_note FROM meta_data_source "
            "WHERE source_id IN ('qcc_judicial_person','qcc_trademark')").fetchall()
        assert len(rows) == 2, "库内企查查来源登记条数异常：%d" % len(rows)
        for sid, note in rows:
            assert "待确认" not in note and "全部公开" in note, (
                "库内 meta_data_source.%s 仍是旧口径：%r" % (sid, note))
    finally:
        c.close()


def c_provenance_complete():
    t = read(PROVENANCE)
    for kw in ("口径定稿", "数据全部都上传公开", "登录态批量查询",
               "非官方 API 采购", "未做任何脱敏", "个人信息保护法",
               "平台服务条款", "无法追溯已发生的下载", "DATA_LICENSE.md",
               "ods_qcc_person.person_name", "05_数据库"):
        assert kw in t, "DATA_PROVENANCE.md 缺关键表述 %r" % kw
    assert "2,079" in t and "490" in t, "公开范围行数未现场回算"


def d_license_complete():
    t = read(LICENSE)
    for kw in ("不是开源软件许可证", "转发或再分发", "科研", "商业使用",
               "个人信息保护法", "未做去标识化", "无担保", "撤回"):
        assert kw in t, "DATA_LICENSE.md 缺关键条款 %r" % kw
    assert "不是对公开分发的限制" in t, "使用条款不应限制公开分发"


def e_no_takedown_doc():
    assert not TAKEDOWN.exists(), "口径已改为全部公开，撤下教程仍存在"
    src = read(ROOT / "src" / "data" / "export_data_provenance.py")
    for f in ("DATA_PROVENANCE.md", "DATA_LICENSE.md"):
        assert f in src, "生成源未产出 %s" % f
    assert "撤下教程" not in src, "生成源仍产出撤下教程"
    assert "撤下教程" not in read(ROOT / "src" / "data" / "export_delivery.py")


def f_published_db_not_deidentified():
    assert DB_PUB.exists(), "缺完整库交付快照"
    c = con(DB_PUB)
    try:
        for t, col, expect in PERSON_FIELDS:
            n = c.execute(
                'SELECT COUNT(*) FROM "%s" WHERE "%s" IS NOT NULL '
                'AND TRIM("%s") <> ""' % (t, col, col)).fetchone()[0]
            assert n == expect, "%s.%s 非空 %d 行，期望 %d（疑似脱敏）" % (
                t, col, n, expect)
            bad = c.execute(
                'SELECT COUNT(*) FROM "%s" WHERE "%s" LIKE ?' % (t, col),
                ("PID_%",)).fetchone()[0]
            assert bad == 0, "%s.%s 残留 %d 行 PID_… 假名化值" % (t, col, bad)
        names = [r[0] for r in c.execute("SELECT person_name FROM ods_qcc_person")]
        zh = sum(1 for x in names if x and re.search(r"[一-鿿]", str(x)))
        # 2079 行是「非空」总数，其中约 20 行为英文 / 符号名；此处只守
        # 「中文姓名仍大量存在」，逐值一致性交给 g_person_values_identical。
        assert zh > 2000, "交付快照自然人中文姓名仅 %d 行，疑似被替换" % zh
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall(), "存在外键冲突"
    finally:
        c.close()


def g_person_values_identical():
    """快照与工作库：自然人字段逐行同值（VACUUM 副本，未被脱敏）。"""
    a, b = con(DB_WORK), con(DB_PUB)
    try:
        for t, col, _ in PERSON_FIELDS:
            q = 'SELECT "%s" FROM "%s" ORDER BY rowid' % (col, t)
            x = a.execute(q).fetchall()
            y = b.execute(q).fetchall()
            assert x == y, "%s.%s 与工作库不一致（%d / %d 行）" % (
                t, col, len(x), len(y))
    finally:
        a.close()
        b.close()


def h_structure_identical():
    def snap(path):
        c = con(path)
        try:
            objs = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
            cols = sum(c.execute('SELECT COUNT(*) FROM pragma_table_info("%s")' % n)
                       .fetchone()[0] for n in objs)
            counts = {n: c.execute('SELECT COUNT(*) FROM "%s"' % n).fetchone()[0]
                      for n in objs if not n.startswith("v_")}
            return objs, cols, counts
        finally:
            c.close()
    x, y = snap(DB_WORK), snap(DB_PUB)
    assert x[0] == y[0], "交付快照对象清单与工作库不一致"
    assert x[1] == y[1] == 1068, "字段数漂移：%d / %d" % (x[1], y[1])
    assert x[2] == y[2], "交付快照表行数与工作库不一致"


def i_release_parts():
    import src.data.export_db_bundle as E
    assert E.RELEASE_PARTS == ["04_数据字典与数据集", "05_数据库"], (
        "Release 附件不含完整库：%r" % (E.RELEASE_PARTS,))
    src = read(ROOT / "src" / "data" / "export_db_bundle.py")
    for kw in ("全部公开", "不要再往本脚本加脱敏逻辑", "六条使用红线", "--no-zip"):
        assert kw in src, "export_db_bundle.py 缺 %r" % kw
    for kw in ("DEIDENTIFY", "wb_pid", "假名化", "七条使用红线", "hashlib"):
        assert kw not in src, "export_db_bundle.py 残留脱敏逻辑 %r" % kw


def j_sources_and_baseline():
    for p in (ROOT / "src" / "data" / "export_delivery.py",
              ROOT / "README.md", DEL / "00_先看这个.md",
              DEL / "03_交付说明" / "delivery_spec.md",
              DEL / "RELEASE_v1.5.0.md"):
        assert "322 项" in read(p), "%s 守卫项数未同步为 322" % p.name
    t = read(ROOT / "src" / "data" / "export_delivery.py")
    for kw in ("test_data_distribution.py", "28 个测试文件", "全部公开",
               "export_data_provenance"):
        assert kw in t, "export_delivery.py 未同步 %r" % kw
    n = len(list((ROOT / "tests").glob("test_*.py")))
    assert n == 28, "tests/ 下应为 28 个守卫文件，实得 %d" % n


def main():
    print("=" * 68)
    print("  企查查数据分发口径守卫（最终定稿：数据全部公开）")
    print("=" * 68)
    for name, fn in [
        ("登记册 v5 / data_distribution 全部公开定稿", a_registry_decided),
        ("旧「待拍板 / 不挂库 / 撤下」表述清零", b_no_stale_wording),
        ("DATA_PROVENANCE.md 来源 / 范围 / 风险齐备", c_provenance_complete),
        ("DATA_LICENSE.md 允许转发、禁止商用齐备", d_license_complete),
        ("撤下教程已删除、生成源只出两份声明", e_no_takedown_doc),
        ("交付快照完整库未脱敏、无 PID_… 残留", f_published_db_not_deidentified),
        ("自然人字段与工作库逐行同值", g_person_values_identical),
        ("两库对象 / 字段 / 行数完全一致", h_structure_identical),
        ("RELEASE_PARTS 含 05_数据库、脚本无脱敏逻辑", i_release_parts),
        ("生成源同源、基线 28 文件 322 项", j_sources_and_baseline),
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
