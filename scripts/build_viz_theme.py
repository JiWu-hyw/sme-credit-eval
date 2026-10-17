#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_viz_theme.py — 由 configs/viz_theme.yaml 生成前端主题产物（唯一颜色来源的下游）

归属：后端 / 数据管道（吕键）。输入是本域契约 configs/viz_theme.yaml，故生成器由后端持有。
契约定位：viz_theme.yaml 是前端样式 / ECharts / 论文配图的「唯一颜色来源」；
         本脚本是它唯一的下游生成器，产物「禁止手工编辑」。

产物（默认路径，相对仓库根）：
    frontend/src/styles/theme.css   —— CSS 自定义属性（--xxx）
    frontend/src/styles/theme.ts    —— TS 颜色映射常量（键名与 yaml 完全一致）

命名转写规则（_ → -）：
    yaml 的 snake_case 键 → CSS 变量名时，下划线 _ 转连字符 -。
      例：risk_band code=high_risk  →  --risk-high-risk
          entity_category.field      →  --entity-field
          brand.text_muted           →  --color-text-muted
    而 TS/JS 对象键名一律「以 yaml 为准」（原样 snake_case，不做转写），
    以免与算法侧 Python 出现第三套写法。

用法：
    python scripts/build_viz_theme.py                 # 生成
    python scripts/build_viz_theme.py --check         # 校验产物是否与 yaml 同步（CI 用，非 0 退出＝已漂移）
    python scripts/build_viz_theme.py --out-css X --out-ts Y
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("[FATAL] 需要 pyyaml：pip install pyyaml\n")
    sys.exit(2)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_YAML = os.path.join(REPO_ROOT, "configs", "viz_theme.yaml")
DEFAULT_CSS = os.path.join(REPO_ROOT, "frontend", "src", "styles", "theme.css")
DEFAULT_TS = os.path.join(REPO_ROOT, "frontend", "src", "styles", "theme.ts")

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def load_and_validate(path: str) -> dict:
    with open(path, "rb") as f:
        raw = f.read()
    data = yaml.safe_load(raw.decode("utf-8"))

    errs = []
    if not isinstance(data, dict):
        errs.append("顶层必须是 mapping")
    for key in ("meta", "brand", "risk_band", "entity_category", "market"):
        if key not in data:
            errs.append(f"缺少必需段: {key}")
    if errs:
        raise ValueError("configs/viz_theme.yaml 结构错误: " + "; ".join(errs))

    # brand 颜色校验
    for k, v in data["brand"].items():
        if not (isinstance(v, str) and HEX_RE.match(v)):
            raise ValueError(f"brand.{k} 不是合法 #RRGGBB 颜色: {v!r}")

    # risk_band 校验
    rb = data["risk_band"]
    items = rb.get("items")
    if not isinstance(items, list) or len(items) != 5:
        raise ValueError("risk_band.items 必须恰好 5 档")
    seen_band, seen_code = set(), set()
    for it in items:
        for f in ("band", "code", "label", "reg_class", "color", "color_bg"):
            if f not in it:
                raise ValueError(f"risk_band item 缺字段 {f}: {it}")
        if it["band"] in seen_band:
            raise ValueError(f"risk_band band 重复: {it['band']}")
        if it["code"] in seen_code:
            raise ValueError(f"risk_band code 重复: {it['code']}")
        seen_band.add(it["band"])
        seen_code.add(it["code"])
        for cf in ("color", "color_bg"):
            if not HEX_RE.match(str(it[cf])):
                raise ValueError(f"risk_band code={it['code']} 的 {cf} 非法: {it[cf]!r}")
    if seen_band != {1, 2, 3, 4, 5}:
        raise ValueError("risk_band band 必须为 1..5")

    # entity_category 校验
    for k, v in data["entity_category"].items():
        if not (isinstance(v, str) and HEX_RE.match(v)):
            raise ValueError(f"entity_category.{k} 不是合法颜色: {v!r}")

    # market 校验（涨跌色：涨红跌绿）
    for k, v in data["market"].items():
        if not (isinstance(v, str) and HEX_RE.match(v)):
            raise ValueError(f"market.{k} 不是合法 #RRGGBB 颜色: {v!r}")

    return data, raw


def css_var(s: str) -> str:
    """snake_case → kebab-case（_ → -），用于 CSS 变量名。"""
    return s.strip().lower().replace("_", "-")


def header_comment(source_rel: str, sha: str, version: str, lang: str) -> str:
    open_c, close_c = ("/*", "*/") if lang == "css" else ("/*", "*/")
    return (
        f"{open_c} =====================================================================\n"
        f" * 由 scripts/build_viz_theme.py 自动生成 —— 禁止手工编辑。\n"
        f" * 唯一来源：{source_rel}（version {version}，sha256 {sha[:16]}…）\n"
        f" * 命名转写规则（_ → -）：yaml 的 snake_case 键 → CSS 变量名时下划线转连字符；\n"
        f" *   TS/JS 键名一律以 yaml 为准（原样 snake_case，不转写）。\n"
        f" * 重新生成：python scripts/build_viz_theme.py\n"
        f" {close_c}\n"
    )


def build_css(data: dict, source_rel: str, sha: str) -> str:
    L = [header_comment(source_rel, sha, data["meta"]["version"], "css"), "", ":root {"]

    L.append("  /* ---- brand（主题主色） ---- */")
    for k, v in data["brand"].items():
        L.append(f"  --color-{css_var(k)}: {v};")

    L.append("")
    L.append("  /* ---- risk band（信用风险五档色带） ---- */")
    for it in data["risk_band"]["items"]:
        c = css_var(it["code"])
        L.append(f"  --risk-{c}: {it['color']};")
        L.append(f"  --risk-{c}-bg: {it['color_bg']};")

    L.append("")
    L.append("  /* ---- entity category（六类实体固定映射） ---- */")
    for k, v in data["entity_category"].items():
        L.append(f"  --entity-{css_var(k)}: {v};")

    L.append("")
    L.append("  /* ---- market（涨跌色：A 股惯例 涨红跌绿） ---- */")
    for k, v in data["market"].items():
        L.append(f"  --market-{css_var(k)}: {v};")

    L.append("")
    L.append("  /* ---- meta / compliance ---- */")
    L.append(f"  --viz-theme-version: \"{data['meta']['version']}\";")
    disc = " ".join(str(data["compliance"]["disclaimer"]).split())
    L.append(f"  --compliance-disclaimer: \"{disc}\";")
    L.append("}")
    L.append("")
    return "\n".join(L)


def _ts_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def build_ts(data: dict, source_rel: str, sha: str) -> str:
    L = [header_comment(source_rel, sha, data["meta"]["version"], "ts"), ""]
    L.append("export const VIZ_THEME_META = {")
    L.append(f'  source: "{source_rel}",')
    L.append(f'  version: {_ts_val(data["meta"]["version"])},')
    L.append(f'  name: {_ts_val(data["meta"].get("name", ""))},')
    L.append(f'  sha256: "{sha}",')
    L.append("} as const;")
    L.append("")

    L.append("export const BRAND = {")
    for k, v in data["brand"].items():
        L.append(f"  {k}: {_ts_val(v)},")
    L.append("} as const;")
    L.append("")

    L.append("export type RiskBand = {")
    L.append("  band: number; code: string; label: string; reg_class: string;")
    L.append("  color: string; color_bg: string;")
    L.append("};")
    L.append("")
    L.append("export const RISK_BANDS: readonly RiskBand[] = [")
    for it in data["risk_band"]["items"]:
        L.append("  {")
        L.append(f'    band: {it["band"]}, code: {_ts_val(it["code"])}, label: {_ts_val(it["label"])},')
        L.append(f'    reg_class: {_ts_val(it["reg_class"])}, color: {_ts_val(it["color"])},')
        L.append(f'    color_bg: {_ts_val(it["color_bg"])},')
        L.append("  },")
    L.append("] as const;")
    L.append("")

    L.append("export const RISK_COLOR_BY_CODE: Record<string, string> = {")
    for it in data["risk_band"]["items"]:
        L.append(f'  {it["code"]}: {_ts_val(it["color"])},')
    L.append("};")
    L.append("")

    L.append("export const RISK_COLOR_BY_BAND: Record<number, string> = {")
    for it in data["risk_band"]["items"]:
        L.append(f'  {it["band"]}: {_ts_val(it["color"])},')
    L.append("};")
    L.append("")

    L.append("export const ENTITY_COLORS = {")
    for k, v in data["entity_category"].items():
        L.append(f"  {k}: {_ts_val(v)},")
    L.append("} as const;")
    L.append("")

    L.append("// 涨跌色：A 股惯例 涨红跌绿（与 risk_band 属不同语义域）")
    L.append("export const MARKET_COLORS = {")
    for k, v in data["market"].items():
        L.append(f"  {k}: {_ts_val(v)},")
    L.append("} as const;")
    L.append("")

    disc = " ".join(str(data["compliance"]["disclaimer"]).split())
    L.append("export const COMPLIANCE = {")
    L.append(f"  disclaimer: {_ts_val(disc)},")
    L.append("} as const;")
    L.append("")
    return "\n".join(L)


def write_or_check(path: str, content: str, check: bool) -> bool:
    if check:
        if not os.path.exists(path):
            print(f"[MISSING] {path}")
            return False
        with open(path, "r", encoding="utf-8", newline="") as f:
            cur = f.read()
        ok = cur == content
        print(("[OK]      " if ok else "[DRIFT]   ") + path)
        return ok
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"[WROTE]   {path}  ({len(content.encode('utf-8'))} bytes)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="由 viz_theme.yaml 生成前端主题产物")
    ap.add_argument("--yaml", default=DEFAULT_YAML, help="输入 yaml（默认 configs/viz_theme.yaml）")
    ap.add_argument("--out-css", default=DEFAULT_CSS, help="输出 theme.css")
    ap.add_argument("--out-ts", default=DEFAULT_TS, help="输出 theme.ts")
    ap.add_argument("--check", action="store_true", help="仅校验产物是否与 yaml 同步（不写盘）")
    args = ap.parse_args()

    ypath = os.path.abspath(args.yaml)
    if not os.path.exists(ypath):
        sys.stderr.write(f"[FATAL] 找不到 {ypath}\n")
        return 2

    try:
        data, raw = load_and_validate(ypath)
    except Exception as e:
        sys.stderr.write(f"[FATAL] {e}\n")
        return 2

    sha = hashlib.sha256(raw).hexdigest()
    source_rel = os.path.relpath(ypath, REPO_ROOT).replace("\\", "/")

    css = build_css(data, source_rel, sha)
    ts = build_ts(data, source_rel, sha)

    ok_css = write_or_check(os.path.abspath(args.out_css), css, args.check)
    ok_ts = write_or_check(os.path.abspath(args.out_ts), ts, args.check)

    if args.check:
        if ok_css and ok_ts:
            print("[PASS] 产物与 viz_theme.yaml 同步")
            return 0
        print("[FAIL] 产物已漂移，请运行：python scripts/build_viz_theme.py")
        return 1

    print(f"[DONE] 来源 {source_rel} {data['meta']['version']} sha256 {sha[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
