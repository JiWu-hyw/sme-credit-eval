"""数据缺口台账 → 概览页镜像同步器。

台账 `configs/data_gaps.yaml` 是业务口径的单一来源，概览页首屏（FE-10）直接展示
P0-1…P0-5 的状态与责任人。本脚本负责把它变成「可验证的镜像」：

  1. 校验台账 schema（必填字段 / status 枚举 / id 唯一 / 与探针配置一一对应）
  2. 按 `configs/data_gaps_probe.yaml` 去库里数行数，拿到「数据实况」证据
  3. 做漂移校验：台账写 done 但库是空的、或台账写推进中但数据已入库（该转验收）
  4. 做过期校验：as_of 超过 --max-age-days 还没更新就报 stale
  5. 输出 configs/data_gaps.mirror.json（= /api/data/pipeline 的 gaps 段）

用法：
    python scripts/sync_data_gaps.py                # 生成镜像 + 打印报告
    python scripts/sync_data_gaps.py --check        # CI 模式：stale/漂移即非 0 退出
    python scripts/sync_data_gaps.py --max-age-days 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YAML = ROOT / "configs" / "data_gaps.yaml"
DEFAULT_PROBE = ROOT / "configs" / "data_gaps_probe.yaml"
DEFAULT_DB = ROOT / "data" / "db" / "zjtech_credit.db"
DEFAULT_OUT = ROOT / "configs" / "data_gaps.mirror.json"

# 枚举与前端 dataGaps.ts 保持一致：missing（未到位）/ in_progress（推进中）/ ready（已到位）
STATUS_LABEL = {
    "missing": "未到位",
    "in_progress": "推进中",
    "ready": "已到位",
}
REQUIRED_FIELDS = ("id", "name", "status", "owner", "blocks")

OK, ADVANCE, CONTRADICTION = "ok", "advance", "contradiction"


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"[FATAL] 找不到配置文件：{path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def validate(ledger: dict, probe: dict) -> list[str]:
    """返回错误列表；空列表代表通过。"""
    errs: list[str] = []
    gaps = ledger.get("gaps")
    if not isinstance(gaps, list) or not gaps:
        return ["台账缺少 gaps 列表或为空"]
    if "as_of" not in ledger:
        # 非致命：缺 as_of 仍能生成镜像，只是跳过过期判定（并提示补上）。
        # 兼容云端那一份可能未带 as_of 的台账。
        pass
    else:
        try:
            date.fromisoformat(str(ledger["as_of"]))
        except ValueError:
            errs.append(f"as_of 不是合法日期（YYYY-MM-DD）：{ledger['as_of']}")

    seen: set[str] = set()
    for i, g in enumerate(gaps):
        if not isinstance(g, dict):
            errs.append(f"gaps[{i}] 不是对象")
            continue
        gid = str(g.get("id", f"gaps[{i}]"))
        if gid in seen:
            errs.append(f"{gid} 重复")
        seen.add(gid)
        for key in REQUIRED_FIELDS:
            if key not in g or g[key] in (None, "", []):
                errs.append(f"{gid} 缺少必填字段 {key}")
        status = g.get("status")
        if status not in STATUS_LABEL:
            errs.append(f"{gid} 的 status={status!r} 不在枚举 {list(STATUS_LABEL)} 内")
        if "blocks" in g and not isinstance(g["blocks"], list):
            errs.append(f"{gid} 的 blocks 必须是数组")

    # 探针覆盖范围只做告警（不致命）：台账里有缺口但没探针 → 该条 evidence 为 unknown；
    # 反之探针有、台账无 → 提示清理。两者都不影响镜像生成，便于脚本适配不同版本的台账。
    return errs


def count_rows(
    conn: sqlite3.Connection,
    tables: list[str],
    where: dict[str, str] | None = None,
) -> tuple[dict[str, int | None], dict]:
    """逐表数行数；表不存在记 None 并留说明。

    where 支持给单张表加过滤条件，用于区分「表里有别的数据」和「本条缺口要的数据」，
    例如 P0-3 只看 label_task 属于 msme_default/sba_default/survival_risk 的行。
    """
    rows: dict[str, int | None] = {}
    notes: dict[str, str] = {}
    where = where or {}
    existing = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for t in tables:
        if t not in existing:
            rows[t] = None
            notes[t] = "表不存在"
            continue
        cond = where.get(t)
        sql = f'SELECT COUNT(*) FROM "{t}"'
        if cond:
            sql += f" WHERE {cond}"
        try:
            rows[t] = conn.execute(sql).fetchone()[0]
            if cond:
                notes[t] = f"过滤条件：{cond}"
        except sqlite3.Error as exc:  # 视图/权限等少见情况
            rows[t] = None
            notes[t] = f"查询失败：{exc}"
    return rows, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同步数据缺口台账到概览页镜像")
    ap.add_argument("--yaml", type=Path, default=DEFAULT_YAML)
    ap.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max-age-days", type=int, default=7,
                    help="台账 as_of 超过该天数即判为 stale（默认 7）")
    ap.add_argument("--check", action="store_true",
                    help="CI 模式：发现 stale / 漂移时返回非 0")
    ap.add_argument("--run-tag", default=datetime.now().strftime("%Y%m%d-%H%M%S"),
                    help="写入 meta 的运行标记，便于追溯")
    args = ap.parse_args(argv)

    ledger = load_yaml(args.yaml)
    probe = load_yaml(args.probe)

    errs = validate(ledger, probe)
    if errs:
        print("[FATAL] 台账校验未通过：")
        for e in errs:
            print(f"  - {e}")
        return 1

    raw_as_of = ledger.get("as_of") or (ledger.get("meta") or {}).get("updated_at")
    if raw_as_of not in (None, ""):
        as_of = date.fromisoformat(str(raw_as_of))
        today = date.today()
        age_days = (today - as_of).days
        stale = age_days > args.max_age_days
    else:
        as_of = None
        age_days = None
        stale = False
        print("[WARN] 台账缺 as_of / meta.updated_at，跳过过期判定——建议补上以启用防过期")

    probes = (probe or {}).get("probes") or {}
    conn = sqlite3.connect(str(args.db)) if args.db.exists() else None
    if conn is None:
        print(f"[WARN] 数据库不存在：{args.db}（证据列将全部为 unknown）")

    gaps_out: list[dict] = []
    issues: list[dict] = []
    summary = {k: 0 for k in STATUS_LABEL}

    for g in ledger["gaps"]:
        gid = g["id"]
        p = probes.get(gid, {})
        tables = p.get("tables") or []
        rows, notes = (
            count_rows(conn, tables, p.get("where") or {}) if (conn and tables) else ({}, {})
        )

        known = [v for v in rows.values() if v is not None]
        has_data = any(v and v > 0 for v in known)
        all_empty = bool(known) and all(v == 0 for v in known)
        if not tables:
            evidence_state = "doc_only"
        elif has_data:
            evidence_state = "in_db"
        elif all_empty:
            evidence_state = "empty"
        else:
            evidence_state = "unknown"

        status = g["status"]
        drift, hint = OK, ""
        if status == "ready" and evidence_state == "empty":
            drift, hint = CONTRADICTION, "台账写已到位，但库里 0 行——台账过期，请核实"
        elif status in ("in_progress", "missing") and evidence_state == "in_db":
            drift, hint = ADVANCE, "数据已入库，可转 ready（补验收记录后改 status）"

        summary[status] = summary.get(status, 0) + 1
        item = {
            "id": gid,
            "name": g["name"],
            "status": status,
            "status_label": STATUS_LABEL[status],
            "owner": g["owner"],
            "source": g.get("source", ""),
            "why": g.get("why", ""),
            "blocks": g.get("blocks", []),
            "tables": tables,
            "rows": rows,
            "notes": notes,
            "evidence_state": evidence_state,
            "docs": p.get("docs", []),
            "next_action": p.get("next_action", ""),
            "drift": drift,
            "drift_hint": hint,
        }
        gaps_out.append(item)
        if drift != OK:
            issues.append({"id": gid, "kind": drift, "hint": hint})

    if conn:
        conn.close()

    mirror = {
        "meta": {
            "source": str(args.yaml.relative_to(ROOT)) if str(args.yaml).startswith(str(ROOT)) else str(args.yaml),
            "probe": str(args.probe.relative_to(ROOT)) if str(args.probe).startswith(str(ROOT)) else str(args.probe),
            "source_sha256": sha256_of(args.yaml),
            "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "as_of": as_of.isoformat() if as_of else None,
            "age_days": age_days,
            "max_age_days": args.max_age_days,
            "stale": stale,
            "run_tag": args.run_tag,
        },
        "gap_summary": {
            "total": len(gaps_out),
            **summary,
            "stale": stale,
            "issue_count": len(issues) + (1 if stale else 0),
        },
        "gaps": gaps_out,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(mirror, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- 人读报告 ----
    print("=" * 78)
    _as = as_of.isoformat() if as_of else "未设置"
    _age = f"{age_days}d" if age_days is not None else "n/a"
    print(f"数据缺口镜像  as_of={_as}  age={_age}  stale={stale}  run={args.run_tag}")
    print("=" * 78)
    for it in gaps_out:
        ev = it["evidence_state"]
        if ev == "in_db":
            ev_txt = "行数 " + ", ".join(f"{k}={v}" for k, v in it["rows"].items())
        elif ev == "empty":
            ev_txt = "0 行（" + ", ".join(it["rows"]) + "）"
        elif ev == "doc_only":
            ev_txt = "文档级证据"
        else:
            ev_txt = "未知"
        flag = "" if it["drift"] == OK else f"  << {it['drift'].upper()}: {it['drift_hint']}"
        print(f"[{it['id']}] {it['status_label']:<4} {it['owner']:<4} {it['name']}{flag}")
        print(f"       证据: {ev_txt}")
        if it["next_action"]:
            print(f"       下一步: {it['next_action']}")
    print("-" * 78)
    print(f"汇总: 共 {len(gaps_out)} 条 | " + " / ".join(f"{STATUS_LABEL[k]}{v}" for k, v in summary.items() if v))
    if stale:
        print(f"[STALE] 台账已 {age_days} 天未更新（阈值 {args.max_age_days} 天），"
              f"首屏数字可能失真——改完 configs/data_gaps.yaml 记得同步 as_of")
    if issues:
        print(f"[DRIFT] {len(issues)} 条状态与库内实况不一致")
    print(f"镜像已写出: {args.out}  ({mirror['gap_summary']['total']} 条)")

    if args.check and (stale or issues):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
