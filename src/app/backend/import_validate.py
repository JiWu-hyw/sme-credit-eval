"""数据导入的字段级校验与 staging 落库。

两个导入接口（``POST /api/data/import``、``POST /api/batch/upload``）原来只做
文件名后缀检查然后入队等死。这里把「字段级校验」真正实现：

1. **解析**：CSV 用标准库；XLSX 用 zipfile + ElementTree 读 sheet1
   （环境无 openpyxl，不额外引依赖）。
2. **目标表识别**：拿表头去比对库里所有物理表的列名，取覆盖率最高者；
   覆盖率 <0.5 直接判为「无法识别」，不猜。
3. **字段级校验**：类型可转、NOT NULL、主键重复、企业 ID 格式与外键存在性、
   值域（枚举列）。
4. **staging 落库**：只写 ``stg_import_<table>``，绝不直接写核心表 ——
   导入是「先放缓冲区」，合表另走 ETL。

设计原则：**校验不过就不落库**，且错误逐行逐列给出，不做静默丢弃。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime
from typing import Any, Optional
from xml.etree import ElementTree as ET

ENT_ID_RE = re.compile(r"^ENT[0-9A-F]{12}$")
MAX_ERRORS_PER_CODE = 200          # 同类错误最多记 200 条，避免报告膨胀
TARGET_MIN_MATCH_RATIO = 0.5       # 表头至少半数能对上才认这张表

# 只允许导入到这些层；视图、meta、staging 自身不接受
_IMPORTABLE_PREFIX = ("ods_", "dim_", "fact_", "bridge_", "dws_", "ext_")
# 这些列不参与类型校验（SQLite 里常存 json / 数字混合）
_SKIP_TYPE_CHECK = {"payload", "raw_json", "extra", "note", "qcc_biz_scope"}


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _parse_csv(raw: bytes) -> tuple[list[str], list[list[str]]]:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法解码文件：试过 utf-8-sig / utf-8 / gbk / gb18030")
    # 嗅探分隔符
    sample = text[:4096]
    delim = ","
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    rd = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in rd if any((c or "").strip() for c in r)]
    if not rows:
        raise ValueError("文件为空")
    headers = [(h or "").strip() for h in rows[0]]
    return headers, [r for r in rows[1:]]


def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _parse_xlsx(raw: bytes) -> tuple[list[str], list[list[str]]]:
    """最小 XLSX 读取：sharedStrings + 第一张 sheet。够用，不引第三方库。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))

        sheet_path = next((n for n in names
                           if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")), None)
        if sheet_path is None:
            raise ValueError("XLSX 内找不到 worksheet")
        root = ET.fromstring(z.read(sheet_path))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

        grid: list[list[str]] = []
        for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", ns):
                ref = c.get("r") or ""
                m = re.match(r"([A-Z]+)(\d+)", ref)
                if not m:
                    continue
                letters = m.group(1)
                ci = 0
                for ch in letters:
                    ci = ci * 26 + (ord(ch) - 64)
                ci -= 1
                t = c.get("t")
                v = c.find("m:v", ns)
                val = v.text if v is not None and v.text is not None else ""
                if t == "s" and val.isdigit():
                    i = int(val)
                    val = shared[i] if i < len(shared) else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                cells[ci] = val
            if cells:
                width = max(cells) + 1
                grid.append([cells.get(i, "") for i in range(width)])

    grid = [r for r in grid if any((c or "").strip() for c in r)]
    if not grid:
        raise ValueError("XLSX 第一张表为空")
    headers = [(h or "").strip() for h in grid[0]]
    return headers, grid[1:]


def parse_upload(filename: str, raw: bytes) -> tuple[list[str], list[list[str]], str]:
    """返回 (表头, 数据行, 格式)。"""
    low = (filename or "").lower()
    if low.endswith(".csv") or low.endswith(".tsv"):
        return (*_parse_csv(raw), "csv")
    if low.endswith(".xlsx"):
        return (*_parse_xlsx(raw), "xlsx")
    if low.endswith(".xls"):
        raise ValueError("旧版 .xls 是二进制格式，请另存为 .xlsx 或 .csv 后上传")
    raise ValueError(f"不支持的文件类型：{filename}（仅 CSV / TSV / XLSX）")


# ---------------------------------------------------------------------------
# 目标表识别
# ---------------------------------------------------------------------------
def list_importable_tables(con: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for (name,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if not name.startswith(_IMPORTABLE_PREFIX):
            continue
        out[name] = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
    return out


def detect_target_table(con: sqlite3.Connection,
                        headers: list[str]) -> dict:
    """按表头与表列的 Jaccard 覆盖率排序，取第一。

    覆盖率 = |表头 ∩ 表列| / |表头|。低于阈值判为无法识别，不硬猜。
    """
    hs = {h for h in headers if h}
    if not hs:
        return {"table": None, "matched": [], "unknown": [], "ratio": 0.0,
                "candidates": [], "reason": "表头为空"}
    tables = list_importable_tables(con)
    scored = []
    for t, cols in tables.items():
        cs = set(cols)
        hit = hs & cs
        if not hit:
            continue
        scored.append({
            "table": t, "ratio": round(len(hit) / len(hs), 4),
            "matched": sorted(hit), "n_columns": len(cs),
        })
    scored.sort(key=lambda x: (-x["ratio"], -len(x["matched"]), x["table"]))
    best = scored[0] if scored else None
    if best is None or best["ratio"] < TARGET_MIN_MATCH_RATIO:
        return {
            "table": None,
            "matched": best["matched"] if best else [],
            "unknown": sorted(hs - set(best["matched"])) if best else sorted(hs),
            "ratio": best["ratio"] if best else 0.0,
            "candidates": scored[:5],
            "reason": f"最高覆盖率 {best['ratio'] if best else 0:.0%} < 阈值 "
                      f"{TARGET_MIN_MATCH_RATIO:.0%}，不猜目标表",
        }
    return {
        "table": best["table"], "matched": best["matched"],
        "unknown": sorted(hs - set(best["matched"])),
        "ratio": best["ratio"], "candidates": scored[:5],
        "reason": f"表头匹配 {len(best['matched'])}/{len(hs)}",
    }


# ---------------------------------------------------------------------------
# 字段级校验
# ---------------------------------------------------------------------------
def _sql_type_ok(col_type: str, val: str) -> bool:
    """SQLite 是弱类型，这里只拦「明显不对」的值。"""
    if val is None:
        return True
    s = str(val).strip()
    if s == "":
        return True
    t = (col_type or "").upper()
    if "INT" in t:
        return re.fullmatch(r"[+-]?\d+", s) is not None
    if any(k in t for k in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
        try:
            float(s.replace(",", ""))
            return True
        except ValueError:
            return False
    return True  # TEXT 不校验


def validate_rows(con: sqlite3.Connection, table: str, headers: list[str],
                  rows: list[list[str]], max_examples: int = 50) -> dict:
    """逐行逐列校验，返回结构化报告。"""
    info = list(con.execute(f'PRAGMA table_info("{table}")'))
    meta = {r[1]: {"type": r[2], "notnull": bool(r[3]), "pk": int(r[5])} for r in info}
    table_cols = set(meta)

    unknown_cols = [h for h in headers if h and h not in table_cols]
    errors: list[dict] = []
    warnings: list[dict] = []
    err_count: dict[str, int] = {}
    warn_count: dict[str, int] = {}

    def add(bucket: list, code: str, **kw) -> None:
        """计数与样例分离：样例最多 4×max_examples 条，计数始终准确。"""
        counter = err_count if bucket is errors else warn_count
        counter[code] = counter.get(code, 0) + 1
        if counter[code] > MAX_ERRORS_PER_CODE:
            return
        if len(bucket) < max_examples * 4:
            bucket.append({"code": code, **kw})

    # 枚举值域（取 dim_* 表里的 code/level 列做参考）
    enum_domains: dict[str, set[str]] = {}
    for col in ("enterprise_type", "qual_type", "max_qual_rank", "biz_status"):
        if col in table_cols and table not in ("dim_qual_type",):
            try:
                vals = {r[0] for r in con.execute(
                    f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 200')}
                if 0 < len(vals) <= 30:
                    enum_domains[col] = {str(v) for v in vals}
            except sqlite3.Error:
                pass

    # 企业 ID 集合（用于外键检查；只查一次）
    ent_ids: Optional[set[str]] = set()
    if "enterprise_id" in headers:
        try:
            ent_ids = {r[0] for r in con.execute(
                "SELECT enterprise_id FROM dim_enterprise")}
        except sqlite3.Error:
            ent_ids = None

    # 主键/唯一键重复检测
    pk_cols = [c for c, m in meta.items() if m["pk"]]
    key_cols = pk_cols if pk_cols else [c for c in ("enterprise_id",) if c in table_cols]
    # 主键列在文件里缺失时，查重做不了 —— 必须说出来，不能让调用方以为查过了
    key_present = [c for c in key_cols if c in headers]

    # 关联键：为空不会违反表约束，但会让该行在建模时无法归属企业
    # （库里 fact_patent.enterprise_id 本身就有 766 行为 NULL，所以只能报 warn 不能报 error）
    join_keys = [c for c in ("enterprise_id",) if c in table_cols]

    seen: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        rownum = i + 2  # Excel 行号：表头占 1
        rec = {}
        for j, h in enumerate(headers):
            if not h:
                continue
            val = row[j] if j < len(row) else ""
            rec[h] = val
        for h, m in meta.items():
            if h not in rec:
                continue
            raw = rec.get(h)
            sval = "" if raw is None else str(raw).strip()
            if m["notnull"] and sval == "":
                add(errors, "NOT_NULL_VIOLATION", row=rownum, column=h,
                    message=f"{h} 是 NOT NULL 列，但该行为空")
            if sval and h not in _SKIP_TYPE_CHECK and not _sql_type_ok(m["type"], sval):
                add(errors, "TYPE_MISMATCH", row=rownum, column=h, value=sval[:60],
                    message=f"{h} 类型为 {m['type']}，无法解析 '{sval[:40]}'")
            if h == "enterprise_id":
                if not sval:
                    add(warnings, "NULL_JOIN_KEY", row=rownum, column=h,
                        message="企业 ID 为空：表约束允许（库内 fact_patent 已有 766 行如此），"
                                "但该行无法归属企业，建模时会被丢弃")
                elif not ENT_ID_RE.match(sval):
                    add(errors, "BAD_ENTERPRISE_ID", row=rownum, column=h, value=sval[:60],
                        message="企业 ID 格式应为 ENT + 12 位十六进制（共 15 字符），"
                                "如 ENT2803CAE1D888")
                elif ent_ids is not None and sval not in ent_ids:
                    add(warnings, "FK_NOT_FOUND", row=rownum, column=h, value=sval,
                        message="该企业在 dim_enterprise 中不存在（可能是新企业，"
                                "需先导维度表）")
            if h in enum_domains and sval and sval not in enum_domains[h]:
                add(warnings, "VALUE_OUT_OF_DOMAIN", row=rownum, column=h, value=sval[:60],
                    message=f"{h} 不在现有取值域内（库内 {len(enum_domains[h])} 个取值）")
            if len(sval) > 60:
                add(warnings, "VALUE_TOO_LONG", row=rownum, column=h,
                    message=f"{h} 长度 {len(sval)} 超过 60")

        if key_present:
            key = tuple(str(rec.get(c, "")).strip() for c in key_present)
            if all(key):
                if key in seen:
                    add(errors, "DUPLICATE_KEY", row=rownum, column=",".join(key_present),
                        value="|".join(key),
                        message=f"与第 {seen[key]} 行主键重复")
                else:
                    seen[key] = rownum

    if key_cols and not key_present:
        add(warnings, "NO_KEY_COLUMN",
            column=",".join(key_cols),
            message=f"文件未提供主键列 {'/'.join(key_cols)}，本次**未做重复检测**；"
                    f"落库前需由 ETL 生成主键")

    for h in unknown_cols:
        add(warnings, "UNKNOWN_COLUMN", column=h,
            message=f"目标表 {table} 中不存在该列，落库时会忽略")

    for h in headers:
        if not h:
            add(warnings, "EMPTY_HEADER", column="(空表头)", message="存在空表头列，已跳过")

    n_err = sum(err_count.values())
    return {
        "target_table": table,
        "n_rows": len(rows),
        "n_columns": len(headers),
        "columns": [{
            "name": h, "in_table": h in table_cols,
            "type": meta.get(h, {}).get("type"),
            "notnull": meta.get(h, {}).get("notnull"),
            "null_count": sum(1 for r in rows
                              if (r[j] if j < len(r) else "").strip() == ""),
        } for j, h in enumerate(headers) if h],
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "n_errors": n_err,
            "n_warnings": sum(v for k, v in warn_count.items()
                              if k not in ("UNKNOWN_COLUMN", "NO_KEY_COLUMN")),
            "error_codes": err_count,
            "warning_codes": warn_count,
            "acceptable": n_err == 0,
            "n_unknown_columns": len(unknown_cols),
            "key_columns": key_cols,
            "key_columns_present": key_present,
            "dedup_checked": bool(key_present),
        },
    }


# ---------------------------------------------------------------------------
# staging 落库
# ---------------------------------------------------------------------------
def stage_rows(db_path: str, table: str, headers: list[str],
               rows: list[list[str]]) -> dict:
    """写入**独立的** staging 库 ``data/db/import_staging.db``。

    为什么不写进主库：
    - 主库是交付物，结构口径（33 表 / 698 列）被数据字典、说明书、PPT 引用，
      运行时建表会让这些数字漂移；
    - 导入是「缓冲区」语义，脏数据不该出现在数仓里，物理隔离最干净。

    表结构：批次号 + 原始行号 + 源表名 + 整行 JSON，便于回溯与二次清洗。
    """
    db_dir = os.path.dirname(os.path.abspath(db_path)) or "."
    staging_path = os.path.join(db_dir, "import_staging.db")
    batch = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + os.urandom(2).hex()
    con = sqlite3.connect(staging_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS stg_import (
                batch_id    TEXT NOT NULL,
                target_table TEXT NOT NULL,
                src_row_no  INTEGER,
                row_json    TEXT,
                imported_at TEXT
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_stg_batch ON stg_import(batch_id)")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = []
        for i, row in enumerate(rows):
            rec = {h: (row[j] if j < len(row) else "") for j, h in enumerate(headers) if h}
            payload.append((batch, table, i + 2,
                            json.dumps(rec, ensure_ascii=False), now))
        con.executemany(
            "INSERT INTO stg_import (batch_id,target_table,src_row_no,row_json,imported_at) "
            "VALUES (?,?,?,?,?)", payload)
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM stg_import WHERE batch_id=?",
                        (batch,)).fetchone()[0]
        return {"batch_id": batch, "staged_rows": n,
                "table": "stg_import", "staging_db": staging_path}
    finally:
        con.close()
