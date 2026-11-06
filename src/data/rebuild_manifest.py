"""重建 `configs/db_schema_manifest.json` 的结构部分（表/列/行数），保留 indicators 与 views。

为什么需要：manifest 是「库结构的第二份真相」，手工维护必然漂移。
改了库结构（ALTER TABLE）之后跑一次，让清单与库对齐。

用法：
  python -m src.data.rebuild_manifest [--check]
    --check  只比对不写入，发现不一致时退出码 1（可挂到 CI/提交前钩子）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("data/db/zjtech_credit.db")
MAN = Path("configs/db_schema_manifest.json")


def build(con: sqlite3.Connection) -> dict[str, dict]:
    tabs = {}
    names = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in names:
        cols = [{"name": r[1], "type": r[2], "notnull": bool(r[3]),
                 "default": r[4], "pk": bool(r[5])}
                for r in con.execute(f"PRAGMA table_info({t})")]
        n_rows = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        tabs[t] = {"columns": cols, "n_cols": len(cols), "n_rows": n_rows}
    return tabs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    man = json.loads(MAN.read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    tabs = build(con)
    con.close()

    total_cols = sum(v["n_cols"] for v in tabs.values())
    old = man.get("tables", {})
    diff = []
    for t, v in tabs.items():
        if t not in old:
            diff.append(f"+ 表 {t}")
        elif old[t]["n_cols"] != v["n_cols"]:
            diff.append(f"~ {t} 列数 {old[t]['n_cols']} → {v['n_cols']}")
    for t in old:
        if t not in tabs:
            diff.append(f"- 表 {t} 已不存在")

    print(f"库：{len(tabs)} 表 / {total_cols} 列")
    print("差异：", ("无" if not diff else ""))
    for d in diff:
        print("  ", d)
    if args.check:
        return 1 if diff else 0

    man["tables"] = tabs
    man["total_tables"] = len(tabs)
    man["total_columns"] = total_cols
    man["generated_at"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    MAN.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {MAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
