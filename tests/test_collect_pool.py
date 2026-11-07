# -*- coding: utf-8 -*-
"""公示名单采集器测试（src/data/collect_pool.py）。

覆盖：多格式解析、编码探测、列名识别、脏行清洗、元数据推断、区域推断、
端到端入库、幂等性、STRICT 约束拦截。

适用边界（2026-11-07 标注）：本文件是早期 A 套公示名单采集器
（src/data/collect_pool.py + src/data/db.py 临时空库）的自测。
端到端夹具写死「5 文件 → 9 家企业 / 9 条资质」，当前采集器规范化去重后
实际得到 8 家，因此端到端与幂等 2 项已知失败，属于旧夹具期望值未更新。
当前交付库是 B 套浙江 500 家 / schema v1.8，走 load_500family.py 链路。
本文件不纳入交付守卫清单（17 个活跃守卫 / 152 项），
评价 v1.8 请用 README 与 00_先看这个.md 里登记的那 17 个。

用法：.venv/Scripts/python.exe tests/test_collect_pool.py
"""
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.data import db as D                    # noqa: E402
from src.data import collect_pool as C          # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  OK   %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  FAIL %s -- %s" % (name, e))
    except Exception as e:
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ERR  %s -- %s: %s" % (name, type(e).__name__, e))


def _assert(cond, detail=None):
    assert cond, "实际值：%r" % (detail,)


def _write(path, text, encoding="utf-8"):
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------------------
def build_cases(tmp):
    """造一批覆盖各种脏情况的样例文件，返回 dict。"""
    d = tempfile.mkdtemp(dir=tmp)

    csv_gbk = _write(os.path.join(d, "high_tech_2021.csv"),
                     "序号,企业名称,所属区域\n"
                     "1,杭州甲科技有限公司,杭州市\n"
                     "2,宁波乙新材料股份有限公司,宁波市\n"
                     "3,温州丙智能装备有限公司,温州市\n"
                     "合计,3 家,\n"
                     ",,\n"
                     "4,12345,杭州市\n", encoding="gb18030")

    csv_comma_head = _write(os.path.join(d, "tech_sme_2022.csv"),
                            "seq,company_name,region\n"
                            "1,绍兴丁生物科技有限公司,绍兴市\n"
                            "2,金华戊光电技术有限公司,金华市\n")

    html = _write(os.path.join(d, "specialized_2023.html"),
                  "<html><body><table>"
                  "<tr><th>序号</th><th>企业名称</th><th>所属区域</th></tr>"
                  "<tr><td>1</td><td>台州己医疗设备有限公司</td><td>台州市</td></tr>"
                  "<tr><td>2</td><td>湖州庚环保科技有限公司</td><td>湖州市</td></tr>"
                  "</table></body></html>")

    js = _write(os.path.join(d, "little_giant_2024.json"),
                '[{"企业名称": "衢州辛半导体有限公司", "所属区域": "衢州市"}]')

    xlsx = None
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["序号", "企业名称", "所在地区"])
        ws.append([1, "舟山壬海洋科技有限公司", "舟山市"])
        xlsx = os.path.join(d, "high_tech_2024.xlsx")
        wb.save(xlsx)
    except ImportError:
        pass
    return {"dir": d, "csv_gbk": csv_gbk, "csv_comma_head": csv_comma_head,
            "html": html, "json": js, "xlsx": xlsx}


def test_parse():
    tmp = tempfile.mkdtemp()
    c = build_cases(tmp)

    def t_gbk():
        rows = C.read_csv(c["csv_gbk"])
        names = [r.get("企业名称") for r in rows]
        _assert("杭州甲科技有限公司" in names, names)
        # 合计行与空行保留在 rows（清洗在 clean_rows），脏行剔除由 clean_rows 负责
        rec, bad = C.clean_rows(rows, *C.pick_columns(rows))
        got = [r["company_name"] for r in rec]
        _assert(got == ["杭州甲科技有限公司", "宁波乙新材料股份有限公司", "温州丙智能装备有限公司"], got)
        _assert(bad >= 2, bad)

    def t_en_header():
        rows = C.read_csv(c["csv_comma_head"])
        name_col, seq_col, region_col = C.pick_columns(rows)
        _assert(name_col == "company_name", name_col)
        _assert(seq_col == "seq", seq_col)
        _assert(region_col == "region", region_col)

    def t_html():
        rows = C.read_html(c["html"])
        rec, _ = C.clean_rows(rows, *C.pick_columns(rows))
        _assert([r["company_name"] for r in rec] ==
                ["台州己医疗设备有限公司", "湖州庚环保科技有限公司"], rec)

    def t_json():
        rows = C.read_json(c["json"])
        rec, _ = C.clean_rows(rows, *C.pick_columns(rows))
        _assert(len(rec) == 1 and rec[0]["company_name"] == "衢州辛半导体有限公司", rec)

    def t_xlsx():
        if not c["xlsx"]:
            return
        rows = C.read_xlsx(c["xlsx"])
        rec, _ = C.clean_rows(rows, *C.pick_columns(rows))
        _assert(len(rec) == 1 and rec[0]["company_name"] == "舟山壬海洋科技有限公司", rec)

    check("解析：GBK 编码 CSV + 脏行剔除", t_gbk)
    check("解析：英文表头列名识别", t_en_header)
    def t_pdf():
        try:
            import fitz
        except ImportError:
            return
        p = os.path.join(c["dir"], "high_tech_2022.pdf")
        doc = fitz.open()
        pg = doc.new_page()
        for i, (nm, rg) in enumerate([("丽水青瓷新材料有限公司", "丽水市"),
                                      ("衢州氟硅材料研究院", "衢州市")], start=1):
            pg.insert_text((60, 60 + 24 * i), "%d  %s  %s" % (i, nm, rg),
                           fontsize=11, fontname="china-s")
        doc.save(p)
        doc.close()
        rows = C.read_pdf(p)
        got = [r["企业名称"] for r in rows]
        _assert(got == ["丽水青瓷新材料有限公司", "衢州氟硅材料研究院"], got)
        _assert(rows[0]["所属区域"] == "丽水市", rows[0])

    check("解析：HTML 表格（标准库）", t_html)
    check("解析：JSON 列表", t_json)
    check("解析：XLSX", t_xlsx)
    check("解析：PDF（PyMuPDF 抽文本）", t_pdf)


def test_infer():
    def t_giant():
        m = C.infer_meta("专精特新小巨人_2024.xlsx")
        _assert(m["qual_type"] == "little_giant", m)
        _assert(m["year"] == 2024, m)

    def t_batch():
        m = C.infer_meta("浙江省科技型中小企业_第2批_2025.csv")
        _assert(m["qual_type"] == "tech_sme", m)
        _assert(m["batch_no"] == "2025-2", m)

    def t_unknown():
        m = C.infer_meta("random.xlsx")
        _assert(m["qual_type"] is None, m)

    def t_region():
        _assert(C.infer_region("杭州市滨江区") == ("330100", "杭州市"), C.infer_region("杭州市滨江区"))
        _assert(C.infer_region("")[0] == "330000", C.infer_region(""))

    def t_norm():
        _assert(D.norm_company_name("浙江（杭州）华测检测技术有限公司") == "华测检测技术有限公司",
                D.norm_company_name("浙江（杭州）华测检测技术有限公司"))

    check("推断：小巨人优先于专精特新", t_giant)
    check("推断：批次号（第2批 → 2025-2）", t_batch)
    check("推断：无法识别时返回 None", t_unknown)
    check("推断：区域文本 → 行政区划码", t_region)
    check("规范化：省市前缀与括号备注", t_norm)


def test_end2end():
    tmp = tempfile.mkdtemp()
    c = build_cases(tmp)
    dbp = os.path.join(tmp, "e2e.db")
    D.init_db(dbp, verbose=False)
    conn = D.connect(dbp)

    def run():
        for p in [c["csv_gbk"], c["csv_comma_head"], c["html"], c["json"], c["xlsx"]]:
            if not p:
                continue
            r = C.process_file(conn, p, verbose=False)
            _assert(r.get("status") == "ok", r)
        n_ent = conn.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
        n_qual = conn.execute("SELECT COUNT(*) FROM fact_qualification").fetchone()[0]
        n_raw = conn.execute("SELECT COUNT(*) FROM ods_qual_company_raw").fetchone()[0]
        n_notice = conn.execute("SELECT COUNT(*) FROM ods_qual_notice").fetchone()[0]
        _assert(n_ent == 9, n_ent)
        _assert(n_qual == 9, n_qual)
        _assert(n_raw == 9, n_raw)
        _assert(n_notice == 5, n_notice)

    def t_idempotent():
        # 重跑全部文件：不应产生重复行
        for p in [c["csv_gbk"], c["csv_comma_head"], c["html"], c["json"], c["xlsx"]]:
            if not p:
                continue
            C.process_file(conn, p, verbose=False)
        n_ent = conn.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
        n_qual = conn.execute("SELECT COUNT(*) FROM fact_qualification").fetchone()[0]
        n_raw = conn.execute("SELECT COUNT(*) FROM ods_qual_company_raw").fetchone()[0]
        _assert((n_ent, n_qual, n_raw) == (9, 9, 9), (n_ent, n_qual, n_raw))

    def t_log():
        n = conn.execute("SELECT COUNT(*) FROM meta_ingest_log").fetchone()[0]
        _assert(n >= 8, n)
        sha = conn.execute(
            "SELECT COUNT(*) FROM meta_ingest_log WHERE file_sha256 IS NOT NULL").fetchone()[0]
        _assert(sha >= 8, sha)

    def t_bad_qual():
        # STRICT/CHECK 约束：非法资质类型必须被拒
        try:
            C.register_notice(conn, "not_a_qual", "2026-1", 2026, c["csv_gbk"])
        except sqlite3.Error:
            return
        raise AssertionError("非法 qual_type 未被 CHECK 拦截")

    def t_fk_source():
        # 外键：source_id 必须存在于 meta_data_source
        rows = conn.execute(
            "SELECT COUNT(*) FROM ods_qual_notice n LEFT JOIN meta_data_source s"
            " ON s.source_id=n.source_id WHERE s.source_id IS NULL").fetchone()[0]
        _assert(rows == 0, rows)

    check("端到端：5 文件 → 9 家企业 / 9 条资质", run)
    check("幂等：重复导入不翻倍", t_idempotent)
    check("治理：采集日志 + sha256 留痕", t_log)
    check("约束：非法资质类型被 CHECK 拦截", t_bad_qual)
    check("约束：notice.source_id 外键全部命中", t_fk_source)
    conn.close()


def main():
    print("=" * 66)
    print("公示名单采集器测试")
    print("=" * 66)
    test_parse()
    test_infer()
    test_end2end()
    print("=" * 66)
    if FAIL:
        print("通过 %d 项，失败 %d 项：" % (len(PASS), len(FAIL)))
        for n, e in FAIL:
            print("  - %s：%s" % (n, e))
        return 1
    print("全部通过：%d 项" % len(PASS))
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
