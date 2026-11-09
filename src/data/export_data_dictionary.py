"""导出完整数据字典（只读，不改库）。

数据来源三合一：
  1) src/data/schema.sql  —— 表/列中文注释、CHECK 枚举口径
  2) PRAGMA table_info    —— 真实类型 / 非空 / 默认 / 主键
  3) 库内实际统计          —— 行数、每列非空率（判断能不能直接用于建模）

产出：
  outputs/data_dictionary.md   人读版（汇报 / 评审 / 交接）
  outputs/data_dictionary.csv  机器读版（每行一个字段，直接做并库字段映射表）

用法：
  python -m src.data.export_data_dictionary
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from src.data.column_notes import (
    note_for,
    table_comment,
    view_comment,
    view_note_for,
)

DB = Path("data/db/zjtech_credit.db")
DDL = Path("src/data/schema.sql")
OUT_MD = Path("outputs/data_dictionary.md")
OUT_CSV = Path("outputs/data_dictionary.csv")

LAYER_CN = {
    "meta": "meta 元数据层",
    "ods": "ods 原始落库层",
    "dim": "dim 维度主表层",
    "fact": "fact 业务事实层",
    "bridge": "fact 多值桥接表",
    "dws": "dws 特征汇总层",
    "ads": "ads 应用/标签层",
    "ext": "ext 外部对照样本",
    "log": "log 审计轨迹",
}


# ---------------- 1) 解析 DDL ----------------
def parse_ddl(text: str) -> dict[str, dict]:
    """返回 {表名: {comment, columns:{列:注释}, checks:{列:[枚举]}, raw}}。"""
    out: dict[str, dict] = {}
    lines = text.splitlines()
    i = 0
    pending_comment = ""
    while i < len(lines):
        line = lines[i]
        m = re.match(r"\s*CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(", line)
        if not m:
            if line.strip().startswith("--") and line.strip():
                c = line.strip().lstrip("-").strip()
                # 跳过纯分隔线、[XXX:BEGIN] 之类的结构标记，避免污染表注释
                if c and re.search(r"[\u4e00-\u9fff]", c) and not re.fullmatch(r"[=\-#*_\s]+", c):
                    pending_comment = c
            elif line.strip():
                pending_comment = ""
            i += 1
            continue

        table = m.group(1)
        body: list[str] = []
        i += 1
        while i < len(lines) and not re.match(r"^\s*\)\s*STRICT", lines[i]):
            body.append(lines[i])
            i += 1
        block = "\n".join(body)

        cols, checks = {}, {}
        for ln in body:
            cm = re.match(r"\s+(\w+)\s+(TEXT|INTEGER|REAL|BLOB)\b(.*)$", ln)
            if cm:
                name, rest = cm.group(1), cm.group(3)
                note = ""
                if "--" in rest:
                    rest, note = rest.split("--", 1)
                    note = note.strip()
                cols[name] = note
                in_m = re.search(r"CHECK\s*\(\s*" + re.escape(name) + r"\s+IN\s*\(([^)]*)\)", block)
                if in_m:
                    checks[name] = [v.strip().strip("'\"") for v in in_m.group(1).split(",")]

        out[table] = {"comment": pending_comment, "columns": cols, "checks": checks}
        pending_comment = ""
        i += 1
    return out


# ---------------- 2) 库内统计 ----------------
def stats_for(cur: sqlite3.Cursor, obj: str) -> dict:
    """统计一个表或视图：行数 + 每列非空率 + 索引（视图无索引）。

    注意：SQLite 里 COUNT("列名") 对不存在的列会退化成字符串字面量，
    返回总行数造成假性 100% 覆盖，因此列名一律取自 PRAGMA table_info。
    """
    n = cur.execute(f'SELECT COUNT(*) FROM "{obj}"').fetchone()[0]
    cols = []
    pragma = cur.execute(f'PRAGMA table_info("{obj}")').fetchall()
    for cid, cname, ctype, notnull, dflt, pk in pragma:
        filled = cur.execute(f'SELECT COUNT("{cname}") FROM "{obj}"').fetchone()[0]
        cols.append({
            "name": cname, "type": ctype or "—", "notnull": bool(notnull),
            "default": dflt, "pk": bool(pk),
            "filled": filled,
            "filled_pct": (100.0 * filled / n) if n else 0.0,
        })
    return {"rows": n, "cols": cols}


def scan_db(con: sqlite3.Connection) -> dict:
    cur = con.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    views = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")]

    info, vinfo = {}, {}
    for t in tables:
        info[t] = stats_for(cur, t)
        info[t]["indexes"] = [r[1] for r in cur.execute(f'PRAGMA index_list("{t}")')]
    for v in views:
        vinfo[v] = stats_for(cur, v)
        vinfo[v]["indexes"] = []
    return {"tables": info, "views": vinfo}


def layer_of(t: str) -> str:
    for pre in ("meta", "ods", "dim", "fact", "bridge", "dws", "ads", "ext", "log"):
        if t.startswith(pre + "_") or t == pre:
            return pre
    return "other"


def main() -> None:
    ddl = parse_ddl(DDL.read_text(encoding="utf-8"))
    con = sqlite3.connect(DB)
    db = scan_db(con)

    # MSME 87 个匿名特征的真实语义（落库时记在 name_map 里）
    msme_feat: dict[str, str] = {}
    try:
        row = con.execute("SELECT name_map FROM ext_msme_sample LIMIT 1").fetchone()
        if row and row[0]:
            msme_feat = json.loads(row[0]).get("feat_names", {})
    except sqlite3.Error:
        pass
    con.close()

    def resolve_note(table: str, column: str) -> str:
        """注释优先级：DDL 行内注释 > MSME 语义 > 人工补充 > 空。"""
        ddl_note = ddl.get(table, {}).get("columns", {}).get(column, "")
        if ddl_note:
            return ddl_note
        if table == "ext_msme_sample" and column in msme_feat:
            return f"MSME 原字段：{msme_feat[column]}"
        return note_for(table, column)

    tables = db["tables"]
    views = db["views"]
    total_cols = sum(len(v["cols"]) for v in tables.values())
    total_view_cols = sum(len(v["cols"]) for v in views.values())
    filled_tables = [t for t, v in tables.items() if v["rows"] > 0]
    empty_tables = [t for t, v in tables.items() if v["rows"] == 0]

    # ---------- CSV ----------
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["对象类型", "对象名", "对象说明", "对象行数", "字段名", "类型", "必填", "默认值",
                    "主键", "非空行数", "非空率%", "枚举口径", "字段说明"])
        for t in sorted(tables, key=lambda x: (layer_of(x), x)):
            v = tables[t]
            d = ddl.get(t, {})
            for c in v["cols"]:
                w.writerow([
                    "表", t, table_comment(t, d.get("comment", "")), v["rows"], c["name"], c["type"],
                    "是" if c["notnull"] else "", c["default"] if c["default"] is not None else "",
                    "是" if c["pk"] else "", c["filled"], f"{c['filled_pct']:.1f}",
                    " / ".join(d.get("checks", {}).get(c["name"], [])),
                    resolve_note(t, c["name"]),
                ])
        for vw in sorted(views):
            v = views[vw]
            for c in v["cols"]:
                w.writerow([
                    "视图", vw, view_comment(vw), v["rows"], c["name"], c["type"],
                    "", "", "", c["filled"], f"{c['filled_pct']:.1f}",
                    "", view_note_for(vw, c["name"]),
                ])

    # ---------- Markdown ----------
    L = []
    L.append("# 标准数据库 · 数据字典（自动导出）\n")
    L.append(f"> **数据库**：`data/db/zjtech_credit.db`（SQLite，STRICT tables）")
    L.append(f"> **导出时间**：{datetime.now():%Y-%m-%d %H:%M}　｜　**导出方式**：`src/data/export_data_dictionary.py`（只读）")
    L.append(f"> **结构来源**：`src/data/schema.sql` 注释 + `PRAGMA` 真实结构 + 库内实际统计")
    L.append(f"> **口径依据**：`特征工程字典.md`（15 指标定义）、`configs/db_schema_manifest.json`（v1.0 结构清单）\n")

    L.append("## 1. 总览\n")
    L.append("| 项 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 表 | **{len(tables)}** 张（{len(filled_tables)} 张有数据 / {len(empty_tables)} 张空） |")
    L.append(f"| 视图 | **{len(views)}** 个 |")
    L.append(f"| 字段 | **{total_cols + total_view_cols}** 列（表 {total_cols} + 视图 {total_view_cols}） |")
    L.append("| 分层 | meta / ods / dim / fact / bridge / dws / ads / ext |")
    L.append(f"| 空表 | {('`' + '`、`'.join(empty_tables) + '`') if empty_tables else '无'} |")
    L.append("")
    L.append("> 字段总数按“表 + 视图”口径统计。若只数物理表为 "
             f"{total_cols} 列，加上视图展开的 {total_view_cols} 列为 {total_cols + total_view_cols} 列。")
    L.append("")

    # 分层索引
    L.append("## 2. 表清单（按分层）\n")
    L.append("| 层 | 表名 | 行数 | 列数 | 说明 |")
    L.append("|---|---|---|---|---|")
    for t in sorted(tables, key=lambda x: (layer_of(x), x)):
        v = tables[t]
        desc = table_comment(t, ddl.get(t, {}).get("comment", "")) or "—"
        L.append(f"| {layer_of(t)} | `{t}` | {v['rows']:,} | {len(v['cols'])} | {desc} |")
    L.append("")

    # 逐表字段
    L.append("## 3. 表字段明细\n")
    for t in sorted(tables, key=lambda x: (layer_of(x), x)):
        v = tables[t]
        d = ddl.get(t, {})
        L.append(f"### `{t}`　—　{table_comment(t, d.get('comment', '')) or '（DDL 未写表注释）'}")
        L.append(f"行数 **{v['rows']:,}** ｜ 字段 **{len(v['cols'])}** ｜ 索引 {', '.join('`'+i+'`' for i in v['indexes']) if v['indexes'] else '无'}\n")
        L.append("| # | 字段 | 类型 | 必填 | 主键 | 非空率 | 枚举口径 | 说明 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(v["cols"], 1):
            enum = " / ".join(d.get("checks", {}).get(c["name"], []))
            note = resolve_note(t, c["name"])
            L.append(f"| {i} | `{c['name']}` | {c['type']} | {'✓' if c['notnull'] else ''} "
                     f"| {'✓' if c['pk'] else ''} | {c['filled_pct']:.0f}% | {enum} | {note} |")
        L.append("")

    # 视图
    L.append("## 4. 视图清单\n")
    L.append("| 视图 | 行数 | 列数 | 用途 |")
    L.append("|---|---|---|---|")
    for vw in sorted(views):
        v = views[vw]
        L.append(f"| `{vw}` | {v['rows']:,} | {len(v['cols'])} | {view_comment(vw) or '—'} |")
    L.append("")
    L.append("> 视图是逻辑表，不单独占存储；列类型由底层表达式推断，"
             "因此没有「必填 / 主键 / 枚举约束」这几栏，只保留非空率。")
    L.append("")

    # 逐视图字段
    L.append("## 5. 视图字段明细\n")
    for vw in sorted(views):
        v = views[vw]
        L.append(f"### `{vw}`　—　{view_comment(vw) or '（未登记视图注释）'}")
        L.append(f"行数 **{v['rows']:,}** ｜ 字段 **{len(v['cols'])}**\n")
        L.append("| # | 字段 | 类型 | 非空率 | 说明 |")
        L.append("|---|---|---|---|---|")
        for i, c in enumerate(v["cols"], 1):
            L.append(f"| {i} | `{c['name']}` | {c['type']} | {c['filled_pct']:.0f}% "
                     f"| {view_note_for(vw, c['name'])} |")
        L.append("")

    # 口径说明
    L.append("## 6. 关键口径（混用会直接算错）\n")
    L.append("`v_patent_canonical` 的两个授权列含义不同：\n")
    L.append("| 列 | 语义 | 判据 | 该用在哪 |")
    L.append("|---|---|---|---|")
    L.append("| `is_granted` | **此刻仍在权** | 法律状态为授权/有效 | 当前有效专利数、维持率 |")
    L.append("| `is_granted_ever` | **曾经获得过授权** | 公告号 kind code ∈ B/U/S/C（授权公告独有），或 is_granted=1 "
             "| 授权率、授权数、技术转化率 |")
    L.append("")
    L.append("已归属专利 29,582 件中，`is_granted`=1 为 15,753 件，`is_granted_ever`=1 为 23,693 件 —— "
             "差 7,940 件是「授权后失效」，用前者算授权率会低估 33.5%。")
    L.append("")
    L.append("建模入口口径：\n")
    L.append("| 视图 | 样本量 | 能否用于训练 | 理由 |")
    L.append("|---|---|---|---|")
    L.append("| `v_model_dataset_asof_flat` | 614 行 / **307 家** | ✅ 可以 | 时点取数，无后视泄漏；"
             "每家 2 行（两种 as_of_policy），须按 enterprise_id 分组切分 |")
    L.append("| `v_model_dataset_flat` | 500 行 / 500 家 | ❌ 不可以 | 静态口径含后视信息，"
             "且 `split='excluded'` 的 193 行 label=0 是占位假续存样本 |")
    L.append("")

    # 与 v1.0 的差异
    L.append("## 7. 与 v1.0 手写字典的差异\n")
    L.append("| 项 | v1.0（2026-10-14） | 本字典（现状） |")
    L.append("|---|---|---|")
    L.append("| 表数 | 27 表 + 5 视图 | %d 表 + %d 视图 |" % (len(tables), len(views)))
    L.append("| 数据 | 全部为空，仅字典种子 | %d 张有数据，%d 张空 |" % (len(filled_tables), len(empty_tables)))
    L.append("| 表字段级说明 | 仅 8 张核心表 | 全部 %d 列（含非空率） |" % total_cols)
    L.append("| 视图字段级说明 | 无（只有视图名） | 全部 %d 列 |" % total_view_cols)
    L.append("| 机器可读版 | 无 | `data_dictionary.csv`，"
             "%d 行（表 %d + 视图 %d），含对象类型列 |"
             % (total_cols + total_view_cols, total_cols, total_view_cols))
    L.append("")

    md = "\n".join(L)
    OUT_MD.write_text(md, encoding="utf-8")

    print(f"✅ 已导出 {OUT_MD}（{len(md):,} 字符）")
    print(f"✅ 已导出 {OUT_CSV}")
    print(f"   表 {len(tables)} / 视图 {len(views)}")
    print(f"   字段：表 {total_cols} + 视图 {total_view_cols} = {total_cols + total_view_cols}")
    print(f"   有数据 {len(filled_tables)} 张，空表 {empty_tables}")
    no_note = sum(1 for t in tables for c in tables[t]["cols"] if not resolve_note(t, c["name"]))
    no_vnote = sum(1 for v in views for c in views[v]["cols"] if not view_note_for(v, c["name"]))
    print(f"   表字段说明覆盖：{total_cols - no_note} / {total_cols}（缺 {no_note}）")
    print(f"   视图字段说明覆盖：{total_view_cols - no_vnote} / {total_view_cols}（缺 {no_vnote}）")
    no_tc = [t for t in tables if not table_comment(t, ddl.get(t, {}).get("comment", ""))]
    no_vc = [v for v in views if not view_comment(v)]
    print(f"   对象说明覆盖：缺表注释 {no_tc or '无'}，缺视图注释 {no_vc or '无'}")


if __name__ == "__main__":
    main()
