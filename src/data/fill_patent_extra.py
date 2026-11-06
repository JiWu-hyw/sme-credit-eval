"""
fill_patent_extra.py — 把本地源 CSV 中能补的专利字段回填到 fact_patent。

填补目标（幂等 UPDATE，已填不覆盖，按 app_no 匹配）：
  1. abstract  ← 500家_专利明细.csv 的「摘要」列（TEXT，精确直补）
  2. n_claims  ← 同上「权利要求」文本 → 解析顶层权项编号最大值（INTEGER，近似）
  3. ipc_main  ← 合并_专利明细_并集.csv 的「IPC分类号」首个 IPC 片段（TEXT，精确；仅源有值部分）

说明：
  - n_claims 源 CSV 仅有权利要求「文本」无数字项数，此处用正则 (?:^|\\n)\\s*(\\d+)\\.
    取顶层权项编号最大值作为近似项数；覆盖低（约 831 条），结果标注为近似。
  - ipc_main 在并集 CSV 中本身仅 49.3% 有值，故只能补 DB 空且源有的少量行（约 12 条）。

用法：
  python -m src.data.fill_patent_extra            # 正式写库
  python -m src.data.fill_patent_extra --dry-run  # 仅统计待更新数，不写库
"""
import argparse
import csv
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent          # _backend/src/data
BACKEND = HERE.parent.parent                     # _backend
ROOT = BACKEND.parent                           # 工作区根
DB = BACKEND / "data/db/zjtech_credit.db"
RICH = ROOT / "_data_collect/out/500家_专利明细.csv"
UNION = ROOT / "_data_collect/out/合并_专利明细_并集.csv"

IPC_RE = re.compile(r"\s*([A-H]\d{2}[A-Z]\d+)")


def _split_ipc(s: str):
    """取 IPC 完整码中首个合法片段（如 B24B21/18 → B24B21）。"""
    if not s:
        return None
    m = IPC_RE.match(s.strip())
    return m.group(1) if m else None


def _n_claims(text: str):
    """从权利要求文本解析顶层权项编号最大值（近似项数）。"""
    if not text:
        return None
    nums = [int(x) for x in re.findall(r"(?:^|\n)\s*(\d+)\.", text)]
    return max(nums) if nums else None


def load_sources():
    rich = {}
    with open(RICH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            app = (row.get("申请号") or "").strip()
            if app:
                rich[app] = {
                    "abs": (row.get("摘要") or "").strip(),
                    "claims": (row.get("权利要求") or "").strip(),
                }
    union = {}
    with open(UNION, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            app = (row.get("申请号") or "").strip()
            if app:
                union[app] = (row.get("IPC分类号") or "").strip()
    return rich, union


def main(dry: bool = False):
    if not DB.exists():
        raise SystemExit(f"未找到主库: {DB}")
    rich, union = load_sources()
    con = sqlite3.connect(DB)
    cur = con.cursor()

    upd_abs = upd_clm = upd_ipc = 0
    # 先 fetchall 到内存，避免在遍历 cursor 时复用同一 cursor 执行 UPDATE 导致迭代中断
    rows = cur.execute(
        "SELECT app_no, abstract, n_claims, ipc_main FROM fact_patent"
    ).fetchall()
    for app, abs_now, clm_now, ipc_now in rows:
        if not app:
            continue
        # 1) abstract 精确补
        if not abs_now and app in rich and rich[app]["abs"]:
            upd_abs += 1
            if not dry:
                cur.execute(
                    "UPDATE fact_patent SET abstract=? WHERE app_no=?",
                    (rich[app]["abs"], app),
                )
        # 2) n_claims —— 已停用：源「权利要求」列仅含第 1 条权利要求（922/934 无第 2 条
        #    编号标记，文本 2000 字截断），解析出的项数恒为 1，是假值。按诚实数据原则置 NULL，
        #    待回企查查原页重抓完整权利要求后再启用（见 2026-11-05 会话记录）。
        # 3) ipc_main 精确补
        if not ipc_now and app in union and union[app]:
            ipc = _split_ipc(union[app])
            if ipc:
                upd_ipc += 1
                if not dry:
                    cur.execute(
                        "UPDATE fact_patent SET ipc_main=? WHERE app_no=?",
                        (ipc, app),
                    )

    if not dry:
        con.commit()
    con.close()
    mode = "DRY-RUN（未写库）" if dry else "已写库"
    print(f"[{mode}] 待/已更新: abstract={upd_abs}, n_claims(近似)={upd_clm}, ipc_main={upd_ipc}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅统计待更新数，不写库")
    args = ap.parse_args()
    main(dry=args.dry_run)
