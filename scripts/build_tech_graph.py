"""命令行入口：构建企业级「技术相似图谱」（写入 ads_graph_node / ads_graph_edge）。

核心逻辑已统一到 ``src.data.load_500family.build_tech_graph``（单一事实源），
本脚本只保留独立 CLI，便于「不跑完整 ETL、只想单独重建图谱」的场景：

    python scripts/build_tech_graph.py                 # 对默认库重建图谱
    python scripts/build_tech_graph.py --db PATH      # 指定库
    python scripts/build_tech_graph.py --dry-run      # 只校验库与基础表行数

注意：完整 ETL（``python -m src.data.load_500family``）会自动调用同一函数，
      正常流程无需再手动执行本脚本；两份入口共用一份逻辑，避免漂移。
"""
import argparse
import json
import os
import sqlite3
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from src.data import load_500family as loader  # noqa: E402

DB_PATH = os.path.join(BACKEND_ROOT, "data", "db", "zjtech_credit.db")


def main(argv=None):
    ap = argparse.ArgumentParser(description="重建企业级技术相似图谱（统一 ETL 同一函数）")
    ap.add_argument("--db", default=DB_PATH, help="目标数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只校验库与基础数据，不写库")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("数据库不存在：%s" % args.db, file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        if args.dry_run:
            for t in ("dim_enterprise", "fact_patent", "bridge_patent_ipc", "dim_ipc"):
                n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                print("  %-22s %d" % (t, n))
            return 0
        summary = loader.build_tech_graph(conn, verbose=True)
    finally:
        conn.close()

    print("\n===== 技术相似图谱构建完成（统一 ETL 同一函数）=====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
