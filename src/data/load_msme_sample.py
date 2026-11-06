"""
load_msme_sample.py — 方案D：载入 Kaggle MSME 信贷对照样本到 ext_msme_sample。

来源：Kaggle「MSME Credit Data by 30S-CR」v4（CC BY-NC 4.0，已登记 kaggle_msme）
  1707 家中国中小微企业 × 89 列（Enterprise_id + Label + 87 特征）。
  违约率 5.975%（102/1707），与本库弱标签正类率 6.8% 相当——天然对照条件。

映射规则
--------
* Enterprise_id → sample_id 保持原值（整数主键）
* Label → label（0/1），label_task='msme_default'
* 87 特征按列序映射 f01..f87（与 schema ext_msme_sample 列名一致）
* 文本列（Province/Enterprise_type/Sector）原样入 f 列（STRICT 表列是 TEXT 兼容？——
  f01..f87 定义为 REAL，文本会因 STRICT 拒绝。处理：文本列编码为类别码 + name_map
  记录映射；数值列直接入。）
* name_map：JSON 记录 f01..f87 → 原始列名（schema 注释要求的回填）
* source_id='kaggle_msme'

用法：python -m src.data.load_msme_sample [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
ROOT = BACKEND.parent
DB = BACKEND / "data/db/zjtech_credit.db"
SRC = ROOT / "_data_collect/out/datasets/kaggle_msme/MSME Credit Data by 30S-CR.xlsx"

LABEL_TASK = "msme_default"
SOURCE_ID = "kaggle_msme"
TEXT_COLS = {"Province", "Enterprise_type", "Sector"}   # 唯一 3 个文本列


def main(dry: bool = False) -> int:
    import pandas as pd
    df = pd.read_excel(SRC)
    assert df.shape == (1707, 89), f"数据形状异常: {df.shape}"
    feat_cols = [c for c in df.columns if c not in ("Enterprise_id", "Label")]
    assert len(feat_cols) == 87, f"特征列数异常: {len(feat_cols)}"

    # 文本列 → 类别码（name_map 记录编码表）
    code_maps: dict[str, dict[str, int]] = {}
    name_map: dict[str, str] = {}
    matrix = df[feat_cols].copy()
    for i, c in enumerate(feat_cols, start=1):
        fid = f"f{i:02d}"
        name_map[fid] = str(c)
        if c in TEXT_COLS:
            cats = sorted(matrix[c].astype(str).unique())
            cm = {v: k for k, v in enumerate(cats)}
            code_maps[str(c)] = {str(k): int(v) for k, v in
                                 ((cat, code) for cat, code in cm.items())}
            matrix[c] = matrix[c].astype(str).map(cm).astype(float)
        else:
            matrix[c] = pd.to_numeric(matrix[c], errors="coerce")

    con = sqlite3.connect(DB)
    cur = con.cursor()
    n = len(df)
    now = datetime.now().isoformat(timespec="seconds")
    if not dry:
        cur.execute("DELETE FROM ext_msme_sample")
        placeholders = ",".join(["?"] * 87)
        cols_sql = ",".join(f"f{i:02d}" for i in range(1, 88))
        rows = []
        for ridx in range(n):
            rows.append((
                int(df.iloc[ridx]["Enterprise_id"]),
                int(df.iloc[ridx]["Label"]),
                LABEL_TASK,
                *[None if pd.isna(v) else float(v) for v in matrix.iloc[ridx]],
                json.dumps({"feat_names": name_map, "text_codes": code_maps},
                           ensure_ascii=False),
                SOURCE_ID,
            ))
        cur.executemany(
            f"INSERT INTO ext_msme_sample (sample_id, label, label_task, {cols_sql},"
            f" name_map, source_id) VALUES (?,?,?,{placeholders},?,?)",
            rows)
        con.commit()
    n_db = cur.execute("SELECT COUNT(*) FROM ext_msme_sample").fetchone()[0]
    pos = cur.execute("SELECT SUM(label) FROM ext_msme_sample WHERE label=1").fetchone()[0]
    con.close()
    mode = "DRY-RUN" if dry else "写库"
    print(f"[{mode}] ext_msme_sample: {n_db} 行 | 正类 {pos} | "
          f"文本列编码 {len(code_maps)} 列 | name_map 已回填")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(main(dry=args.dry_run))
