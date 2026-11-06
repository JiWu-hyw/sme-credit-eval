"""回收未匹配专利的归属（复用既有匹配规范，幂等可重跑）。

背景
----
fact_patent 里 3,218 条专利的 enterprise_id 为 NULL（占 9.8%），但它们的
``assignee_raw`` 100% 有值。逐条核查发现绝大多数申请人其实就在 500 家企业
名单里（或其曾用名里），只是当初匹配流程没走到这一步，属于**可回收的匹配欠账**，
不是外部企业专利。

匹配策略（按置信度降序，命中即停）
--------------------------------
1. ``exact``  申请人全名 == dim_enterprise.name_display       conf 1.00
2. ``alias``  申请人 == dim_enterprise_alias.alias_name       conf 0.95
3. ``fuzzy``  双方归一化后相等（去空格/括号/标点/连字符）      conf 0.90

多申请人处理
------------
``assignee_raw`` 可能是「A公司,张三,B公司」这种联合申请。按逗号/分号拆分后
逐个尝试，命中第一个即归属该企业。这与库内既有 52 条联合申请专利的处理一致
（一条专利只归一家主企业，不重复计数）。

不匹配的部分
------------
实测 758 条（143 家申请人）确实不属于 500 家企业名单，如「浙江环动机器人关节
科技股份有限公司」「浙江省环境科技股份有限公司」。这些是采集过程的副产品，
**保持 NULL 不强行归属**——乱归属会污染 patent_cnt 与全部专利特征。

副作用与后续
------------
匹配后 enterprise_id 改变，以下需重跑才能对齐（本脚本只做匹配 + patent_cnt）：
- dws_tech_feature 的专利计数与派生特征
- fill_rd_intensity（研发人员数分子依赖专利归属）
- ads_feature_asof / ads_survival_period（时点与生存特征）
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "db" / "zjtech_credit.db"

_PUNCT = re.compile(r"[\s\-—·・\(\)（）【】\[\]、,，.。/／]")
_SPLIT = re.compile(r"[,，;；]")


def norm(s: str | None) -> str:
    return _PUNCT.sub("", s or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    ents = {r[0]: r[1] for r in cur.execute(
        "SELECT enterprise_id, name_display FROM dim_enterprise")}
    name2eid = {nm: eid for eid, nm in ents.items()}
    norm2eid: dict[str, str] = {}
    for eid, nm in ents.items():
        norm2eid.setdefault(norm(nm), eid)
    alias2eid: dict[str, str] = {}
    for eid, an in cur.execute(
            "SELECT enterprise_id, alias_name FROM dim_enterprise_alias"):
        if an:
            alias2eid.setdefault(an, eid)

    rows = cur.execute(
        "SELECT patent_id, raw_id, assignee_raw FROM fact_patent "
        "WHERE enterprise_id IS NULL").fetchall()
    print(f"[未匹配专利] 待处理 {len(rows):,} 条")

    hits: list[tuple[str, str | None, str, str, float]] = []
    for pid, raw_id, raw in rows:
        for token in [t.strip() for t in _SPLIT.split(raw or "") if t.strip()]:
            if token in name2eid:
                hits.append((pid, raw_id, name2eid[token], "exact", 1.00))
                break
            if token in alias2eid:
                hits.append((pid, raw_id, alias2eid[token], "alias", 0.95))
                break
            eid = norm2eid.get(norm(token))
            if eid:
                hits.append((pid, raw_id, eid, "fuzzy", 0.90))
                break

    from collections import Counter
    print(f"  可回收 {len(hits):,} 条 | 方式分布 {dict(Counter(h[3] for h in hits))}")
    print(f"  保持未匹配 {len(rows)-len(hits):,} 条（确属外部企业）")

    if args.dry_run:
        print("  [dry-run] 未写库")
        con.close()
        return

    now = datetime.now().isoformat(timespec="seconds")
    mid = cur.execute("SELECT COALESCE(MAX(CAST(match_id AS INTEGER)),0) FROM log_match").fetchone()[0]
    for pid, raw_id, eid, method, conf in hits:
        cur.execute(
            "UPDATE fact_patent SET enterprise_id=?, match_method=?, match_confidence=? "
            "WHERE patent_id=? AND enterprise_id IS NULL",
            (eid, method, conf, pid),
        )
        mid += 1
        cur.execute(
            "INSERT OR IGNORE INTO log_match "
            "(match_id, stage, patent_id, raw_id, raw_name, candidate_ent, method, score, "
            " decision, reviewed_by, reviewed_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(mid), "patent_assignee_rematch", pid, raw_id, None, eid, method, conf,
             "accepted", "auto_rematch_v1", now, now),
        )

    # 重算 patent_cnt，使其与专利表实际件数一致
    cur.execute("""
        UPDATE dim_enterprise SET patent_cnt = COALESCE((
            SELECT COUNT(*) FROM fact_patent f
            WHERE f.enterprise_id = dim_enterprise.enterprise_id), 0)
    """)
    con.commit()

    left = cur.execute(
        "SELECT COUNT(*) FROM fact_patent WHERE enterprise_id IS NULL").fetchone()[0]
    mis = cur.execute("""
        SELECT COUNT(*) FROM dim_enterprise e
        WHERE e.patent_cnt <> (SELECT COUNT(*) FROM fact_patent f
                               WHERE f.enterprise_id = e.enterprise_id)
    """).fetchone()[0]
    print(f"  剩余未匹配: {left:,}")
    print(f"  patent_cnt 与专利表不一致的企业: {mis}")
    print(f"  integrity: {cur.execute('PRAGMA integrity_check').fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    main()
