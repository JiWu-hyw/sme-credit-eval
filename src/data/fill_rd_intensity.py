"""补算 T7 研发投入强度 rd_intensity（文档授权替代口径，幂等可重跑）。

口径来源
--------
《信用评估指标体系》表2 T7：
  首选：研发费用 / 营业收入；
  **替代口径（默认）：研发人员数 / 员工总数**。

本地无研发费用与营业收入（年报单年、普遍不公示），故走替代口径。

分子（研发人员数代理）
----------------------
企业「近 3 年（app_year >= 2023）」授权/申请专利的 distinct 发明人数，
经 fact_patent → bridge_patent_inventor 关联。

为什么限定近 3 年
------------------
分母 annual_social_sec 是**当期**社保参保人数。若分子用历史累计发明人
（本库专利最早 1992 年），会出现「发明人 82 人 / 社保 1 人 = 82 倍」的
口径错配（实测 63 家 >1，最高 82 倍）。改为近 3 年后中位数 0.288→0.156、
>1 家数 63→24，与「专精特新企业研发人员占比 15% 左右」的常识一致。

分母（员工总数代理）
--------------------
dim_enterprise.annual_social_sec（社保参保人数，471/500 有值）。

口径边界（重要，勿擅自放宽）
---------------------------
1. **发明人 ≠ 研发人员**：专利发明人只是研发人员中被记录下来的子集，
   会低估真实研发队伍（尤其无专利申请年份）。因此本字段是**下界代理**。
2. **分母 <5 人视为不可靠**：实测 11 家社保参保仅 1–4 人，却有数十名
   专利发明人，属明显残值。这类企业 rd_intensity 置 NULL 并标
   ``denominator_unreliable``，不裁剪、不硬填。
3. **无近 3 年专利记录 → NULL 而非 0**：没有专利申请记录不等于没有研发
   投入（可能技术保密或未申请），填 0 会制造假阴性，故标 ``no_recent_inventor``。
4. **比率 >1 裁剪到 1.0**（理论上研发人员不能超过员工总数），
   标 ``capped_1`` 保留可追溯性；不做其他平滑。
5. 全库 rd_intensity_flag 由原先统一 'missing' 改为真实口径标记，
   便于下游按 flag 过滤或加权。

落库
----
- dws_tech_feature.rd_intensity / rd_intensity_flag
- ads_model_dataset.rd_intensity（同步，否则宽表仍为 NULL）
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "db" / "zjtech_credit.db"

RECENT_YEAR = 2023          # 分子取近 3 年（2023 起）
MIN_DENOMINATOR = 5         # 社保人数低于此值视为残值


FLAG_MAP = {
    "proxy_inventor_ratio": "proxy",
    "capped_1": "proxy",
    "missing_denominator": "missing",
    "denominator_unreliable": "missing",
    "no_recent_inventor": "missing",
}


def build_ratios(con: sqlite3.Connection) -> dict[str, tuple[float | None, str]]:
    """返回 {enterprise_id: (rd_intensity, reason)}。

    reason 是细分原因，仅用于统计输出；落库时按 CHECK 约束映射为
    reported / proxy / missing 三值（见 FLAG_MAP）。
    """
    inv = dict(con.execute(
        """
        SELECT f.enterprise_id, COUNT(DISTINCT b.inventor_id)
        FROM fact_patent f
        JOIN bridge_patent_inventor b ON f.patent_id = b.patent_id
        WHERE f.enterprise_id IS NOT NULL AND f.app_year >= ?
        GROUP BY 1
        """, (RECENT_YEAR,)
    ).fetchall())

    out: dict[str, tuple[float | None, str]] = {}
    for eid, social in con.execute(
        "SELECT enterprise_id, annual_social_sec FROM dim_enterprise"
    ):
        n_inv = inv.get(eid, 0)
        if social is None:
            out[eid] = (None, "missing_denominator")
        elif social < MIN_DENOMINATOR:
            out[eid] = (None, "denominator_unreliable")
        elif n_inv == 0:
            out[eid] = (None, "no_recent_inventor")
        else:
            raw = n_inv / social
            if raw > 1.0:
                out[eid] = (1.0, "capped_1")
            else:
                out[eid] = (round(raw, 4), "proxy_inventor_ratio")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写库")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    ratios = build_ratios(con)
    now = datetime.now().isoformat(timespec="seconds")

    from collections import Counter
    flag_cnt = Counter(f for _, f in ratios.values())
    vals = [v for v, _ in ratios.values() if v is not None]

    print(f"[T7 rd_intensity] 企业 {len(ratios)} 家")
    print(f"  可算 {len(vals)} 家 | 口径标记: {dict(flag_cnt)}")
    if vals:
        s = sorted(vals)
        print(f"  分布 min={s[0]:.4f} p25={s[len(s)//4]:.4f} "
              f"median={s[len(s)//2]:.4f} p75={s[3*len(s)//4]:.4f} max={s[-1]:.4f}")

    if args.dry_run:
        print("  [dry-run] 未写库")
        con.close()
        return

    cur = con.cursor()
    for eid, (val, reason) in ratios.items():
        cur.execute(
            "UPDATE dws_tech_feature SET rd_intensity=?, rd_intensity_flag=?, computed_at=? "
            "WHERE enterprise_id=?",
            (val, FLAG_MAP[reason], now, eid),
        )
        cur.execute(
            "UPDATE ads_model_dataset SET rd_intensity=? WHERE enterprise_id=?",
            (val, eid),
        )
    con.commit()

    n1 = con.execute("SELECT COUNT(rd_intensity) FROM dws_tech_feature").fetchone()[0]
    n2 = con.execute("SELECT COUNT(rd_intensity) FROM ads_model_dataset").fetchone()[0]
    print(f"  写入 dws_tech_feature: {n1}/500 有值")
    print(f"  同步 ads_model_dataset: {n2}/500 有值")
    print(f"  integrity: {con.execute('PRAGMA integrity_check').fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    main()
