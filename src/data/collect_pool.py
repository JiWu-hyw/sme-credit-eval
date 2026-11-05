# -*- coding: utf-8 -*-
"""公示名单采集器（方案 A 第一步）：原始名单文件 → ods → dim/fact。

定位
----
开源数据路线 A 的入口。三层（四类）资质公示名单是本项目**唯一免费可得的样本池**，
也是标签（高新资格动态 = 认定→复核）与成长轨迹（资质晋级序列）的来源。

工作方式（两条腿，先离线后在线）
--------------------------------
1. 离线解析（主力，当前可用）：人工从官网下载名单文件，丢进 data/raw/qual_list/，
   本脚本解析 → ods_qual_notice / ods_qual_company_raw → 清洗进 dim_enterprise /
   fact_qualification。支持 csv / txt / xlsx / xls / html / json，自动猜编码与列名。
2. 联网抓取（可选）：--fetch-url 直接抓网页表格（依赖 requests，未装则提示）。
   政务站点多有人机校验，故不作为主线；拿到 HTML 后走同一套解析器。

设计原则
--------
- ods 保持原貌（不做名称规范化、不去重），dim/fact 才做业务判断 —— 可回溯、可重跑。
- 幂等：同一 notice(qual_type, batch_no, region_code) 重复导入只更新，不产生重复行。
- 每次导入写 meta_ingest_log（含文件 sha256），保证实验可复现。
- 零第三方强依赖：csv/xlsx/html/json 解析尽量用标准库（xlsx 需 openpyxl，已在环境内）。

用法
----
    # 解析单个文件（显式指定资质类型与年度）
    python -m src.data.collect_pool --file data/raw/qual_list/high_tech_2021.csv \
        --qual-type high_tech --year 2021 --batch 2021-1

    # 批量扫描目录（从文件名推断资质类型/年度/批次）
    python -m src.data.collect_pool --dir data/raw/qual_list

    # 只预览不入库
    python -m src.data.collect_pool --dir data/raw/qual_list --dry-run

    # 自检：造合成样例跑端到端（写入临时库，不污染正式库）
    python -m src.data.collect_pool --selftest
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.data.db import (  # noqa: E402
    DB_PATH, ROOT, connect, make_enterprise_id, norm_company_name, now_iso,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_DIR = os.path.join(ROOT, "data", "raw", "qual_list")

QUAL_TYPE_ALIASES = {
    "科技型中小企业": "tech_sme", "科技型": "tech_sme", "省科技型": "tech_sme",
    "tech_sme": "tech_sme", "sme": "tech_sme",
    "高新技术企业": "high_tech", "高新": "high_tech", "高企": "high_tech",
    "high_tech": "high_tech", "hightech": "high_tech",
    "专精特新小巨人": "little_giant", "小巨人": "little_giant", "little_giant": "little_giant",
    "专精特新中小企业": "specialized", "专精特新": "specialized", "specialized": "specialized",
}

NAME_COL_HINTS = [
    "企业名称", "公司名称", "单位名称", "机构名称", "申报单位", "申报企业",
    "企业名称（全称）", "企业名称(全称)", "主体名称", "承担单位", "项目承担单位",
    "name", "company", "company_name", "enterprise", "enterprisename",
]
SEQ_COL_HINTS = ["序号", "编号", "no", "seq", "index", "序号（no）"]
REGION_COL_HINTS = [
    "所属区域", "所在地区", "所在区域", "地区", "区县", "所属区县", "地市", "所属地市",
    "辖区", "归属地", "区域", "region", "city", "area", "district",
]
# 需要跳过的列（绝不当成企业名）
SKIP_COL_HINTS = ["信用代码", "信用代码", "法人", "联系人", "电话", "地址", "备注", "email", "邮箱"]

# 浙江省行政区划（用于从名单里的区域文本推断 region_code）
# 资质类型 → 数据源登记 ID（外键必须命中 meta_data_source，否则写入被拒）
QUAL_SOURCE = {
    "tech_sme": "zj_tech_sme",
    "high_tech": "zj_high_tech",
    "specialized": "zj_specialized",
    "little_giant": "zj_specialized",   # 小巨人名单同属专精特新体系，共用来源登记
}

CITY_CODE = {
    "杭州": ("330100", "杭州市"), "宁波": ("330200", "宁波市"), "温州": ("330300", "温州市"),
    "嘉兴": ("330400", "嘉兴市"), "湖州": ("330500", "湖州市"), "绍兴": ("330600", "绍兴市"),
    "金华": ("330700", "金华市"), "衢州": ("330800", "衢州市"), "舟山": ("330900", "舟山市"),
    "台州": ("331000", "台州市"), "丽水": ("331100", "丽水市"),
    "浙江省": ("330000", "浙江省"), "浙江": ("330000", "浙江省"), "省级": ("330000", "浙江省"),
}
DEFAULT_REGION_CODE = "330000"
DEFAULT_REGION_NAME = "浙江省"

# 明显不是企业名的值（表头/合计/说明行）
JUNK_PATTERNS = re.compile(
    r"(^合计|^小计|^总计|^备注|^说明|^注[：:]|^\*{2,}|^-+$|^—+$|企业名称|序号|^\d+$)"
)


# ---------------------------------------------------------------------------
# 一、文件读取：多格式 + 多编码
# ---------------------------------------------------------------------------
def _read_text(path: str) -> str:
    """按候选编码解码，政务站点导出多为 GBK。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gb18030", "utf-8", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_csv(path: str, sep: str = "") -> list:
    """CSV/TXT → list[dict]。无表头的单列文件视为纯企业名列表。"""
    text = _read_text(path)
    if not sep:
        # 表头行里出现次数最多的分隔符
        first = text.splitlines()[0] if text.splitlines() else ""
        counts = {d: first.count(d) for d in (",", "\t", ";", "|")}
        sep = max(counts, key=counts.get) if max(counts.values()) > 0 else ","
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []

    header = [c.strip() for c in rows[0]]
    looks_like_header = any(_is_name_col(h) or _is_seq_col(h) or _is_region_col(h) for h in header)
    if looks_like_header:
        out = []
        for r in rows[1:]:
            rec = {}
            for i, h in enumerate(header):
                rec[h or ("col_%d" % i)] = r[i].strip() if i < len(r) else ""
            out.append(rec)
        return out
    # 无表头
    if len(header) == 1:
        return [{"企业名称": r[0].strip()} for r in rows]
    width = max(len(r) for r in rows)
    return [{"col_%d" % i: (r[i].strip() if i < len(r) else "") for i in range(width)} for r in rows]


def read_xlsx(path: str) -> list:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("读取 xlsx 需要 openpyxl：pip install openpyxl")
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets:
        data = [[("" if c is None else str(c).strip()) for c in row]
                for row in ws.iter_rows(values_only=True)]
        data = [r for r in data if any(c for c in r)]
        if not data:
            continue
        header = data[0]
        for r in data[1:]:
            rec = {}
            for i, h in enumerate(header):
                rec[h or ("col_%d" % i)] = r[i] if i < len(r) else ""
            rows.append(rec)
    wb.close()
    return rows


def read_html(path: str) -> list:
    """用标准库提取网页表格（避免 bs4 依赖）；取行数最多的那张表。"""
    text = _read_text(path)

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables, self.cur, self.row, self.cell, self.buf = [], None, None, None, []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.cur = []
            elif tag == "tr" and self.cur is not None:
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell, self.buf = [], []

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append(re.sub(r"\s+", " ", "".join(self.buf)).strip())
                self.cell, self.buf = None, []
            elif tag == "tr" and self.row is not None:
                if any(c for c in self.row):
                    self.cur.append(self.row)
                self.row = None
            elif tag == "table" and self.cur is not None:
                self.tables.append(self.cur)
                self.cur = None

        def handle_data(self, d):
            if self.cell is not None:
                self.buf.append(d)

    p = TableParser()
    p.feed(text)
    if not p.tables:
        return []
    tbl = max(p.tables, key=len)
    if len(tbl) < 2:
        return []
    header = tbl[0]
    out = []
    for r in tbl[1:]:
        rec = {}
        for i, h in enumerate(header):
            rec[h or ("col_%d" % i)] = r[i] if i < len(r) else ""
        out.append(rec)
    return out


def read_pdf(path: str) -> list:
    """PDF 名单 → list[dict]。高新备案名单多为 PDF 附件，用 PyMuPDF 抽文本再按行识别。

    识别规则：行内匹配到企业名后缀（有限公司/研究院/合伙企业…）即视为一条记录；
    行尾若出现地市名则回填所属区域。无法保证 100%，故一律先落 ods 供人工抽检。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("读取 PDF 需要 PyMuPDF：pip install pymupdf")
    doc = fitz.open(path)
    lines = []
    try:
        for page in doc:
            for ln in page.get_text("text").splitlines():
                ln = re.sub(r"\s+", " ", ln).strip()
                if ln:
                    lines.append(ln)
    finally:
        doc.close()

    suffix = (r"(?:股份有限公司|有限责任公司|有限公司|股份公司|集团公司|研究院|研究所|"
              r"合伙企业|工作室|工厂|制造厂|技术中心|实验室)")
    pat = re.compile(r"^[\s\d\.、\-]*([一-龥A-Za-z0-9（）()·\-]{4,60}?" + suffix + r")")
    rows, seq = [], 0
    seen = set()
    for ln in lines:
        m = pat.match(ln)
        if not m:
            continue
        name = m.group(1).strip()
        if name in seen:
            continue
        seen.add(name)
        seq += 1
        region = ""
        for city in CITY_CODE:
            if city in ln and city != "浙江":
                region = city + "市" if city != "省级" else ""
                break
        rows.append({"序号": seq, "企业名称": name, "所属区域": region})
    return rows


def read_json(path: str) -> list:
    obj = json.loads(_read_text(path))
    if isinstance(obj, list):
        return [dict(x) if isinstance(x, dict) else {"企业名称": str(x)} for x in obj]
    for key in ("rows", "data", "list", "items", "records"):
        if isinstance(obj, dict) and isinstance(obj.get(key), list):
            return [dict(x) if isinstance(x, dict) else {"企业名称": str(x)} for x in obj[key]]
    raise RuntimeError("JSON 结构无法识别，期望 list 或含 rows/data/list 的对象")


def read_table(path: str) -> list:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt", ".tsv"):
        return read_csv(path)
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    if ext == ".xls":
        raise RuntimeError("旧版 .xls 需要 xlrd（pip install xlrd）；建议另存为 .xlsx 后再导入")
    if ext in (".html", ".htm", ".mhtml"):
        return read_html(path)
    if ext == ".pdf":
        return read_pdf(path)
    if ext == ".json":
        return read_json(path)
    raise RuntimeError("不支持的文件类型：%s" % ext)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 二、列名识别与行清洗
# ---------------------------------------------------------------------------
def _norm_header(h: str) -> str:
    return re.sub(r"[\s（）()【】\[\]：:：*·、/\\-]", "", str(h or "")).lower()


def _is_name_col(h: str) -> bool:
    n = _norm_header(h)
    if not n:
        return False
    if any(k in n for k in [x for x in SKIP_COL_HINTS]):
        return False
    return any(_norm_header(k) in n or n in _norm_header(k) for k in NAME_COL_HINTS)


def _is_seq_col(h: str) -> bool:
    n = _norm_header(h)
    return any(_norm_header(k) == n for k in SEQ_COL_HINTS)


def _is_region_col(h: str) -> bool:
    n = _norm_header(h)
    return any(_norm_header(k) in n or n in _norm_header(k) for k in REGION_COL_HINTS)


def pick_columns(rows: list) -> tuple:
    """从首行字段里挑出 企业名/序号/区域 三列。返回 (name_col, seq_col, region_col)。"""
    if not rows:
        return (None, None, None)
    cols = list(rows[0].keys())
    name_col = next((c for c in cols if _is_name_col(c)), None)
    if name_col is None:
        # 兜底：取平均长度最大的文本列（企业名通常最长）
        cand = []
        for c in cols:
            vals = [str(r.get(c, "")) for r in rows[:50] if str(r.get(c, "")).strip()]
            if not vals:
                continue
            avg = sum(len(v) for v in vals) / len(vals)
            if avg >= 6 and not re.match(r"^\d+$", vals[0]):
                cand.append((avg, c))
        name_col = max(cand)[1] if cand else None
    seq_col = next((c for c in cols if _is_seq_col(c)), None)
    region_col = next((c for c in cols if _is_region_col(c)), None)
    return (name_col, seq_col, region_col)


def clean_rows(rows: list, name_col: str, seq_col: str, region_col: str) -> tuple:
    """清洗行 → (records, n_bad)。record = dict(company_name, seq_no, region_raw, raw_payload)"""
    records, bad = [], 0
    seen = set()
    for r in rows:
        name = str(r.get(name_col, "") or "").strip() if name_col else ""
        if not name or len(name) < 4 or JUNK_PATTERNS.search(name):
            bad += 1
            continue
        if re.match(r"^[\d\W_]+$", name):
            bad += 1
            continue
        seq = None
        if seq_col:
            m = re.search(r"\d+", str(r.get(seq_col, "") or ""))
            seq = int(m.group()) if m else None
        region = str(r.get(region_col, "") or "").strip() if region_col else ""
        payload = {k: v for k, v in r.items()
                   if k not in (name_col, seq_col, region_col) and str(v or "").strip()}
        key = (name, region)
        if key in seen:          # 同一文件内去重（ods 也不该存重复行）
            bad += 1
            continue
        seen.add(key)
        records.append({
            "company_name": name,
            "seq_no": seq,
            "region_raw": region,
            "raw_payload": json.dumps(payload, ensure_ascii=False) if payload else None,
        })
    return records, bad


def infer_region(region_raw: str) -> tuple:
    """区域文本 → (region_code, region_name)。识别不出则落到浙江省。"""
    s = str(region_raw or "")
    for city, (code, nm) in CITY_CODE.items():
        if city in s:
            return (code, nm)
    return (DEFAULT_REGION_CODE, DEFAULT_REGION_NAME)


# ---------------------------------------------------------------------------
# 三、元数据推断（批量模式）
# ---------------------------------------------------------------------------
def infer_meta(path: str) -> dict:
    """从文件名/路径推断 资质类型、年度、批次。显式 CLI 参数优先。"""
    stem = os.path.basename(path)
    qual = None
    for kw, qt in QUAL_TYPE_ALIASES.items():
        if kw in stem:
            qual = qt
            break
    # 关键词长的优先：'专精特新小巨人' 必须在 '专精特新' 之前命中
    if qual == "specialized" and ("小巨人" in stem or "little_giant" in stem):
        qual = "little_giant"
    years = re.findall(r"(20\d{2})", stem)
    year = int(years[0]) if years else None
    m = re.search(r"第\s*([一二三四1234])\s*批", stem)
    if m:
        batch_map = {"一": 1, "二": 2, "三": 3, "四": 4}
        tok = m.group(1)
        batch_no = "%s-%s" % (year, batch_map.get(tok, tok)) if year else str(tok)
    elif year:
        batch_no = "%s-1" % year
    else:
        batch_no = "unknown"
    return {"qual_type": qual, "year": year, "batch_no": batch_no}


# ---------------------------------------------------------------------------
# 四、入库
# ---------------------------------------------------------------------------
def ensure_source(conn, source_id: str) -> str:
    """确保 meta_data_source 里存在该 source_id（缺则补一条兜底登记，避免外键失败）。"""
    row = conn.execute("SELECT source_id FROM meta_data_source WHERE source_id=?",
                       (source_id,)).fetchone()
    if row:
        return source_id
    conn.execute(
        "INSERT OR IGNORE INTO meta_data_source"
        "(source_id, source_name, source_type, url, license, fetch_mode, cost_cny,"
        " quota_note, owner, coverage, is_active) "
        "VALUES (?,?,'official_list',NULL,'public','manual',0,'采集器自动补登记',"
        "'吕键','企业样本池',1)",
        (source_id, source_id + "（自动补登记）"),
    )
    conn.commit()
    return source_id


def register_notice(conn, qual_type, batch_no, year, path, source_url=None,
                    region_code=DEFAULT_REGION_CODE, region_name=DEFAULT_REGION_NAME,
                    issuer=None) -> int:
    """登记公告批次（幂等），返回 notice_id。"""
    source_id = ensure_source(conn, QUAL_SOURCE.get(qual_type, qual_type + "_list"))
    cur = conn.execute(
        "SELECT notice_id FROM ods_qual_notice WHERE qual_type=? AND batch_no=? AND region_code=?",
        (qual_type, batch_no, region_code),
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE ods_qual_notice SET file_path=?, fetched_at=?, raw_payload=? WHERE notice_id=?",
            (path, now_iso(), json.dumps({"sha256": sha256_of(path)}, ensure_ascii=False),
             row["notice_id"]),
        )
        conn.commit()
        return row["notice_id"]
    cur = conn.execute(
        "INSERT INTO ods_qual_notice"
        "(qual_type, batch_no, publish_date, year, region_code, region_name, issuer,"
        " source_url, source_id, file_path, raw_payload, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (qual_type, batch_no, None, year, region_code, region_name, issuer, source_url,
         source_id, path,
         json.dumps({"sha256": sha256_of(path)}, ensure_ascii=False), now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def insert_raw(conn, notice_id: int, records: list) -> int:
    """落 ods_qual_company_raw。同 notice 内按企业名幂等（重跑不翻倍）。"""
    exist = {r["company_name"] for r in conn.execute(
        "SELECT company_name FROM ods_qual_company_raw WHERE notice_id=?", (notice_id,))}
    n = 0
    for rec in records:
        if rec["company_name"] in exist:
            continue
        conn.execute(
            "INSERT INTO ods_qual_company_raw"
            "(notice_id, seq_no, company_name, region_raw, raw_payload, ingested_at) "
            "VALUES (?,?,?,?,?,?)",
            (notice_id, rec["seq_no"], rec["company_name"], rec["region_raw"],
             rec["raw_payload"], now_iso()),
        )
        n += 1
    conn.commit()
    return n


def _upsert_enterprise(conn, eid, display, norm, region_code, region_name,
                       qual_type, rank, year) -> None:
    """企业主表 upsert：取 min(first_qual_year) / max(last_qual_year, max_qual_rank)。"""
    ts = now_iso()
    row = conn.execute(
        "SELECT name_display, region_code, region_name, enterprise_type, first_qual_year,"
        " last_qual_year, max_qual_rank FROM dim_enterprise WHERE enterprise_id=?", (eid,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO dim_enterprise"
            "(enterprise_id, name_display, name_norm, credit_code, region_code, region_name,"
            " enterprise_type, first_qual_year, last_qual_year, max_qual_rank, is_listed,"
            " patent_cnt, created_at, updated_at) "
            "VALUES (?,?,?,NULL,?,?,?,?,?,?,0,NULL,?,?)",
            (eid, display, norm, region_code, region_name, qual_type, year, year, rank, ts, ts),
        )
        return
    conn.execute(
        "UPDATE dim_enterprise SET"
        " name_display=?, region_code=COALESCE(?, region_code), region_name=COALESCE(?, region_name),"
        " enterprise_type=?, first_qual_year=MIN(COALESCE(first_qual_year, ?), ?),"
        " last_qual_year=MAX(COALESCE(last_qual_year, ?), ?),"
        " max_qual_rank=MAX(COALESCE(max_qual_rank, 0), ?), updated_at=? "
        "WHERE enterprise_id=?",
        (display, region_code or None, region_name or None, qual_type,
         year, year, year, year, rank, ts, eid),
    )
    if row["name_display"] and row["name_display"] != display:
        # 同一代理键出现不同写法 → 记别名，供后续人工核对（防误合并）
        try:
            conn.execute(
                "INSERT OR IGNORE INTO dim_enterprise_alias"
                "(enterprise_id, alias_name, alias_norm, source, confidence, evidence, created_at) "
                "VALUES (?,?,?,'qual_notice',1.0,?,?)",
                (eid, display, norm, "同一规范化名下出现不同公示写法，需人工复核", ts),
            )
        except sqlite3.IntegrityError:
            pass


def promote(conn, notice_id: int, qual_type: str, year, batch_no: str,
            default_region_code=DEFAULT_REGION_CODE, default_region_name=DEFAULT_REGION_NAME) -> dict:
    """ods → dim/fact：规范化企业名、建主数据、写资质事实。"""
    qt = conn.execute(
        "SELECT level_rank, valid_years FROM dim_qual_type WHERE qual_type=?", (qual_type,)
    ).fetchone()
    rank = qt["level_rank"] if qt else 1
    valid_years = qt["valid_years"] if qt and qt["valid_years"] else None
    valid_until = ("%d-12-31" % (year + valid_years)) if (year and valid_years) else None

    stat = {"raw": 0, "ent_new": 0, "qual_new": 0, "renewal": 0, "bad": 0}
    rows = conn.execute(
        "SELECT raw_id, seq_no, company_name, region_raw FROM ods_qual_company_raw "
        "WHERE notice_id=? ORDER BY raw_id", (notice_id,)
    ).fetchall()
    stat["raw"] = len(rows)

    before_ent = conn.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    for r in rows:
        display = r["company_name"]
        norm = norm_company_name(display)
        if not norm or len(norm) < 4:
            stat["bad"] += 1
            continue
        eid = make_enterprise_id(norm)
        code, nm = infer_region(r["region_raw"])
        if code == DEFAULT_REGION_CODE and not r["region_raw"]:
            code, nm = default_region_code, default_region_name
        _upsert_enterprise(conn, eid, display, norm, code, nm, qual_type, rank, year)

        # 是否复评：同资质类型上已有更早年份的记录
        prev = conn.execute(
            "SELECT COUNT(*) FROM fact_qualification WHERE enterprise_id=? AND qual_type=? AND year<?",
            (eid, qual_type, year if year else 0),
        ).fetchone()[0]
        is_renewal = 1 if prev > 0 else 0
        cur = conn.execute(
            "INSERT OR IGNORE INTO fact_qualification"
            "(enterprise_id, qual_type, batch_no, year, publish_date, region_code, status,"
            " is_renewal, valid_until, notice_id, raw_id, match_score) "
            "VALUES (?,?,?,?,NULL,?,'announced',?,?,?,?,1.0)",
            (eid, qual_type, batch_no, year, code, is_renewal, valid_until, notice_id, r["raw_id"]),
        )
        if cur.rowcount > 0:
            stat["qual_new"] += 1
            stat["renewal"] += is_renewal
    conn.commit()
    stat["ent_new"] = conn.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0] - before_ent
    return stat


def log_ingest(conn, source_id, stage, target_table, file_path, rows_in, rows_ok, rows_bad,
               started, status="ok", message=None) -> None:
    sha = sha256_of(file_path) if file_path and os.path.exists(file_path) else None
    conn.execute(
        "INSERT INTO meta_ingest_log"
        "(source_id, stage, target_table, file_path, file_sha256, rows_in, rows_ok, rows_bad,"
        " started_at, finished_at, status, message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, stage, target_table, file_path, sha, rows_in, rows_ok, rows_bad,
         started, now_iso(), status, message),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 五、主流程
# ---------------------------------------------------------------------------
def process_file(conn, path, qual_type=None, year=None, batch_no=None,
                 region_code=DEFAULT_REGION_CODE, region_name=DEFAULT_REGION_NAME,
                 issuer=None, source_url=None, do_promote=True, dry_run=False,
                 verbose=True) -> dict:
    started = now_iso()
    guess = infer_meta(path)
    qual_type = qual_type or guess["qual_type"]
    year = year if year is not None else guess["year"]
    batch_no = batch_no or guess["batch_no"]
    if not qual_type:
        return {"file": path, "status": "skipped",
                "message": "无法识别资质类型，请用 --qual-type 指定（tech_sme/high_tech/specialized/little_giant）"}

    try:
        rows = read_table(path)
    except Exception as e:  # noqa: BLE001 - 采集器需要容错继续
        return {"file": path, "status": "fail", "message": "读取失败：%s" % e}

    name_col, seq_col, region_col = pick_columns(rows)
    if not name_col:
        return {"file": path, "status": "fail", "message": "未识别到企业名列（可用 --name-col 指定）"}
    records, bad = clean_rows(rows, name_col, seq_col, region_col)

    res = {"file": os.path.basename(path), "qual_type": qual_type, "year": year,
           "batch_no": batch_no, "rows_in": len(rows), "rows_ok": len(records),
           "rows_bad": bad, "name_col": name_col, "region_col": region_col}

    if verbose:
        print("[parse] %-42s 类型=%-12s 年=%-5s 批次=%-8s 行=%d→有效=%d 弃=%d 名列=%s"
              % (os.path.basename(path), qual_type, year, batch_no,
                 len(rows), len(records), bad, name_col))

    if dry_run:
        res["status"] = "dry_run"
        res["sample"] = [r["company_name"] for r in records[:3]]
        return res

    notice_id = register_notice(conn, qual_type, batch_no, year, path,
                                source_url=source_url, region_code=region_code,
                                region_name=region_name, issuer=issuer)
    n_raw = insert_raw(conn, notice_id, records)
    res["notice_id"] = notice_id
    res["raw_new"] = n_raw
    res["status"] = "ok"

    source_id = QUAL_SOURCE.get(qual_type, qual_type + "_list")
    log_ingest(conn, source_id, "parse", "ods_qual_company_raw", path,
               len(rows), n_raw, bad, started,
               status="warn" if bad > len(rows) * 0.5 else "ok",
               message="name_col=%s; region_col=%s" % (name_col, region_col))

    if do_promote:
        st = promote(conn, notice_id, qual_type, year, batch_no,
                     default_region_code=region_code, default_region_name=region_name)
        res.update(st)
        log_ingest(conn, source_id, "build", "dim_enterprise/fact_qualification",
                   path, st["raw"], st["qual_new"], st["bad"], started)
        if verbose:
            print("         └ 入库：主数据新增 %d 家 / 资质事实新增 %d 条（其中复评 %d）"
                  % (st["ent_new"], st["qual_new"], st["renewal"]))
    return res


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_page(url: str, out_dir: str, timeout: int = 25) -> tuple:
    """抓取公告网页 → 存 HTML，并扫描附件链接（政务名单正文常挂在附件里）。

    返回 (html_path, [附件链接...])。附件不自动下载：政务站点常有人机校验，
    且附件地址多为相对路径，交给人工下载更稳（下载后放进同一目录即可批量解析）。
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("抓取网页需要 requests：pip install requests")
    os.makedirs(out_dir, exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    slug = re.sub(r"[^0-9A-Za-z一-龥]+", "_", url.split("//")[-1])[:60]
    path = os.path.join(out_dir, (slug or "page") + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    links = re.findall(r'href=["\']([^"\']+\.(?:xlsx?|pdf|docx?|zip|csv))["\']',
                       resp.text, re.I)
    return path, sorted(set(links))


def scan_dir(directory: str) -> list:
    exts = (".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".html", ".htm", ".json", ".pdf")
    out = []
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.startswith("~$") or f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in exts:
                out.append(os.path.join(root, f))
    return sorted(out)


# ---------------------------------------------------------------------------
# 六、自检（合成样例端到端）
# ---------------------------------------------------------------------------
def _make_samples(dirpath: str) -> list:
    os.makedirs(dirpath, exist_ok=True)
    files = []

    # 1) CSV（GBK 编码，模拟政务导出）
    p1 = os.path.join(dirpath, "high_tech_2021.csv")
    with open(p1, "w", encoding="gb18030", newline="") as f:
        f.write("序号,企业名称,所属区域\n")
        for i, (nm, rg) in enumerate([
            ("杭州云栖智能科技有限公司", "杭州市"),
            ("宁波海天新材股份有限公司", "宁波市"),
            ("温州正泰电器元件有限公司", "温州市"),
            ("浙江（杭州）华测检测技术有限公司", "杭州市"),
            ("绍兴柯桥轻纺机械有限公司", "绍兴市"),
        ], start=1):
            f.write("%d,%s,%s\n" % (i, nm, rg))
        f.write("合计,5 家,\n")  # 应被清洗掉
    files.append(p1)

    # 2) XLSX（2024 复核名单，含复评企业）
    p2 = os.path.join(dirpath, "high_tech_2024.xlsx")
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["序号", "企业名称", "所在地区"])
        for i, (nm, rg) in enumerate([
            ("杭州云栖智能科技有限公司", "杭州市"),
            ("嘉兴晶科能源新材料有限公司", "嘉兴市"),
            ("金华锂威新能源科技有限公司", "金华市"),
        ], start=1):
            ws.append([i, nm, rg])
        wb.save(p2)
        files.append(p2)
    except ImportError:
        pass

    # 3) HTML 表格（专精特新）
    p3 = os.path.join(dirpath, "specialized_2023.html")
    rows = [("台州迈得医疗自动化设备有限公司", "台州市"),
            ("湖州美欣达环保科技有限公司", "湖州市")]
    html = ["<html><body><table>", "<tr><th>序号</th><th>企业名称</th><th>所属区域</th></tr>"]
    for i, (nm, rg) in enumerate(rows, start=1):
        html.append("<tr><td>%d</td><td>%s</td><td>%s</td></tr>" % (i, nm, rg))
    html.append("</table></body></html>")
    with open(p3, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    files.append(p3)

    # 4) PDF（高新备案名单常见格式）；造不出中文 PDF 或解析不出则跳过，不影响其余断言
    try:
        import fitz  # PyMuPDF
        p4 = os.path.join(dirpath, "high_tech_2022.pdf")
        doc = fitz.open()
        pg = doc.new_page()
        for i, (nm, rg) in enumerate([
            ("丽水青瓷新材料有限公司", "丽水市"),
            ("衢州氟硅材料研究院", "衢州市"),
            ("台州汽车零部件股份有限公司", "台州市"),
        ], start=1):
            pg.insert_text((60, 60 + 24 * i), "%d  %s  %s" % (i, nm, rg),
                           fontsize=11, fontname="china-s")
        doc.save(p4)
        doc.close()
        if len(read_pdf(p4)) >= 1:      # 自校验：中文能抽出来才算数
            files.append(p4)
    except Exception:                    # noqa: BLE001 - PDF 为可选能力
        pass
    return files


def selftest(verbose: bool = True) -> int:
    """端到端自检：合成 3 类文件 → 临时库跑通 → 断言。失败返回非 0。"""
    tmpdir = tempfile.mkdtemp(prefix="qual_pool_")
    dbp = os.path.join(tmpdir, "selftest.db")
    files = _make_samples(os.path.join(tmpdir, "qual_list"))
    n_pdf = sum(1 for f in files if f.lower().endswith(".pdf"))
    pdf_rows = len(read_pdf(files[[i for i, f in enumerate(files)
                                  if f.lower().endswith(".pdf")][0]])) if n_pdf else 0

    import src.data.db as dbmod
    dbmod.init_db(dbp, verbose=False)
    conn = connect(dbp)
    ok = True
    try:
        total = []
        for p in files:
            r = process_file(conn, p, verbose=verbose)
            total.append(r)
            if r.get("status") != "ok":
                ok = False
                print("[FAIL] %s → %s" % (r["file"], r.get("message")))

        n_ent = conn.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
        n_qual = conn.execute("SELECT COUNT(*) FROM fact_qualification").fetchone()[0]
        n_raw = conn.execute("SELECT COUNT(*) FROM ods_qual_company_raw").fetchone()[0]
        n_evt = conn.execute("SELECT COUNT(*) FROM meta_ingest_log").fetchone()[0]

        checks = [
            ("企业数 = 9 + PDF %d（跨批次去重后唯一）" % pdf_rows, n_ent, 9 + pdf_rows, "eq"),
            ("资质事实 = 10 + PDF %d" % pdf_rows, n_qual, 10 + pdf_rows, "eq"),
            ("ods 原始行 = 10 + PDF %d（合计行已剔除）" % pdf_rows, n_raw, 10 + pdf_rows, "eq"),
            ("采集日志 ≥ 4 条", n_evt, 4, "ge"),
        ]
        # 复评判定：云栖 2021 首次认定、2024 复核 → is_renewal=1
        # 注意 name_norm 已去省市前缀，此处按展示名定位
        rn = conn.execute(
            "SELECT q.is_renewal FROM fact_qualification q JOIN dim_enterprise e USING(enterprise_id)"
            " WHERE e.name_display='杭州云栖智能科技有限公司' AND q.year=2024"
        ).fetchone()
        # 名称规范化：'浙江（杭州）华测检测技术有限公司' 应去省市前缀 + 去括号
        n1 = conn.execute(
            "SELECT COUNT(*) FROM dim_enterprise WHERE name_norm='华测检测技术有限公司'"
        ).fetchone()[0]
        checks.append(("2024 复核被判为 is_renewal=1", rn["is_renewal"] if rn else None, 1, "eq"))
        checks.append(("名称规范化去前缀+括号生效", n1, 1, "eq"))

        print("\n[selftest] ---- 断言 ----")
        for label, got, want, op in checks:
            good = (got >= want) if op == "ge" else (got == want)
            if not good:
                ok = False
            print("  %-4s %-38s got=%s want=%s%s"
                  % ("PASS" if good else "FAIL", label, got, ">=" if op == "ge" else "==", want))
        print("[selftest] 临时库：%s" % dbp)
    finally:
        conn.close()
    print("[selftest] 结果：%s" % ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 七、CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="公示名单采集器：名单文件 → ods → dim/fact（方案 A 样本池）")
    ap.add_argument("--file", action="append", default=[], help="名单文件（可重复）")
    ap.add_argument("--dir", default=DEFAULT_DIR, help="批量扫描目录（默认 data/raw/qual_list）")
    ap.add_argument("--qual-type", choices=sorted(set(QUAL_TYPE_ALIASES.values())),
                    help="资质类型（显式指定优先于文件名推断）")
    ap.add_argument("--year", type=int, help="认定/备案年度")
    ap.add_argument("--batch", help="批次号，如 2025-2")
    ap.add_argument("--region-code", default=DEFAULT_REGION_CODE)
    ap.add_argument("--region-name", default=DEFAULT_REGION_NAME)
    ap.add_argument("--issuer", help="发文单位")
    ap.add_argument("--source-url", help="公告原文链接")
    ap.add_argument("--fetch-url", action="append", default=[],
                    help="抓取公告网页（可重复）；存 HTML 并列出附件链接")
    ap.add_argument("--no-promote", action="store_true", help="只落 ods，不生成 dim/fact")
    ap.add_argument("--dry-run", action="store_true", help="只解析预览，不写库")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--selftest", action="store_true", help="合成样例端到端自检")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    files = list(args.file)
    if args.fetch_url:
        outdir = os.path.join(args.dir, "_fetched")
        for url in args.fetch_url:
            try:
                p, links = fetch_page(url, outdir)
                print("[fetch] %s -> %s" % (url, p))
                for lk in links[:10]:
                    print("        附件：%s" % lk)
                if args.dry_run:
                    continue
                files.append(p)
            except Exception as e:  # noqa: BLE001
                print("[fetch] 失败 %s：%s" % (url, e))
    if not files:
        if not os.path.isdir(args.dir):
            os.makedirs(args.dir, exist_ok=True)
            print("[info] 已创建名单存放目录：%s" % args.dir)
            print("[info] 请从官网下载公示名单放入该目录后再运行（详见 "
                  "docs/数据采集-公示名单操作手册.md）")
            return 0
        files = scan_dir(args.dir)
    if not files:
        print("[warn] 目录为空：%s" % args.dir)
        return 0

    if not os.path.exists(args.db):
        print("[error] 数据库不存在，先执行：python -m src.data.db --init")
        return 1
    conn = connect(args.db)

    print("[run] 共 %d 个文件 → %s" % (len(files), args.db))
    summary = []
    for p in files:
        r = process_file(conn, p, qual_type=args.qual_type, year=args.year, batch_no=args.batch,
                         region_code=args.region_code, region_name=args.region_name,
                         issuer=args.issuer, source_url=args.source_url,
                         do_promote=not args.no_promote, dry_run=args.dry_run)
        summary.append(r)
    conn.close()

    ok = sum(1 for r in summary if r.get("status") in ("ok", "dry_run"))
    print("\n[summary] 成功 %d / 共 %d" % (ok, len(summary)))
    for r in summary:
        if r.get("status") not in ("ok", "dry_run"):
            print("  [跳过/失败] %s：%s" % (r["file"], r.get("message")))
    return 0 if ok == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
