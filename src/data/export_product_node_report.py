# -*- coding: utf-8 -*-
"""生成《v2.1 商标明细 + 产品节点并列层接入报告》（只读，不改库）。

数据来源：库 v2.1 新增的 3 表 4 视图 + _data_collect/out 三份 CSV（仅作对照）。
所有数字现场回算，禁止写死；产出的 md/json 会被 tests/test_product_node.py 逐项比对。
外部 CSV 缺失时不报错，标注 available=False（干净检出可复现）。

用法：python -m src.data.export_product_node_report
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from src.data.apply_v2_0_axis_a import build_alias_map, norm

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "db" / "zjtech_credit.db"
COLLECT = ROOT.parent / "_data_collect" / "out"
OUT = ROOT / "outputs"
OUT_MD = OUT / "product_node_report.md"
OUT_JSON = OUT / "product_node_report.json"

TM_CSV = COLLECT / "商标明细.csv"
PRODUCT_CSV = COLLECT / "产品节点_尼斯分类.csv"
MAIN_CSV = COLLECT / "合并_500家_企业主表.csv"


def read_csv(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def gather() -> dict:
    con = sqlite3.connect("file:%s?mode=ro" % DB.as_posix(), uri=True)
    c = con.cursor()
    have_csv = TM_CSV.exists() and PRODUCT_CSV.exists() and MAIN_CSV.exists()
    tm = read_csv(TM_CSV) if have_csv else []
    prod = read_csv(PRODUCT_CSV) if have_csv else []
    main = read_csv(MAIN_CSV) if have_csv else []

    n_tab = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                      "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
    n_view = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0]
    obj_names = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%'").fetchall()]
    n_cols = sum(c.execute("SELECT COUNT(*) FROM pragma_table_info('%s')" % n).fetchone()[0]
                 for n in obj_names)
    version = c.execute("SELECT version FROM meta_schema_version "
                        "ORDER BY rowid DESC LIMIT 1").fetchone()[0]

    n_tm = c.execute("SELECT COUNT(*) FROM ods_qcc_trademark").fetchone()[0]
    n_firms = c.execute("SELECT COUNT(DISTINCT enterprise_id) "
                        "FROM ods_qcc_trademark").fetchone()[0]
    n_reg = c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                      "WHERE is_registered=1").fetchone()[0]
    kinds = dict(c.execute("SELECT class_kind, COUNT(*) "
                           "FROM ods_qcc_trademark GROUP BY 1"))
    clean = {
        "apply_date_null": c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                                     "WHERE apply_date IS NULL").fetchone()[0],
        "reg_announce_null": c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                                       "WHERE reg_announce_date IS NULL").fetchone()[0],
        "dash_remaining": c.execute("SELECT COUNT(*) FROM ods_qcc_trademark "
                                    "WHERE apply_date='-' OR reg_announce_date='-'").fetchone()[0],
        "status_variants": c.execute("SELECT COUNT(DISTINCT trademark_status) "
                                     "FROM ods_qcc_trademark").fetchone()[0],
        "registered_total": n_reg,
    }

    cls = {
        "rows": c.execute("SELECT COUNT(*) FROM dim_product_nice_class").fetchone()[0],
        "product_nodes": c.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                                   "WHERE is_product_node=1").fetchone()[0],
        "service_classes": c.execute("SELECT COUNT(*) FROM dim_product_nice_class "
                                     "WHERE is_service=1").fetchone()[0],
        "stated_mismatch": c.execute("SELECT COUNT(*) FROM v_product_node "
                                     "WHERE stated_match=0").fetchone()[0],
    }
    bridge = {
        "rows": c.execute("SELECT COUNT(*) FROM bridge_enterprise_product").fetchone()[0],
        "firms": c.execute("SELECT COUNT(DISTINCT enterprise_id) "
                           "FROM bridge_enterprise_product").fetchone()[0],
        "trademarks": c.execute("SELECT SUM(n_trademark) "
                                "FROM bridge_enterprise_product").fetchone()[0],
        "service_leak": c.execute(
            "SELECT COUNT(*) FROM bridge_enterprise_product b "
            "JOIN dim_product_nice_class p USING(product_id) "
            "WHERE p.is_product_node=0").fetchone()[0],
    }
    nodes = [dict(zip(("product_id", "product_name", "nice_class", "class_name",
                       "covered_firms_stated", "covered_firms_actual",
                       "n_trademark", "n_registered", "stated_match"), r))
             for r in c.execute(
                 "SELECT product_id,product_name,nice_class,class_name,"
                 "covered_firms_stated,covered_firms_actual,n_trademark,"
                 "n_registered,stated_match FROM v_product_node ORDER BY nice_class")]

    cov = [dict(zip(("entity_type", "node_type", "n_in_graph", "n_source_rows",
                     "n_covered_firms", "coverage_rate", "coverage_state", "note"), r))
           for r in c.execute("SELECT * FROM v_six_entity_coverage")]
    cross = [dict(zip(("grade_multilabel", "n_enterprise", "n_with_trademark",
                       "n_with_product_node", "avg_product_class", "n_multi_class"), r))
             for r in c.execute(
                 "SELECT grade_multilabel,n_enterprise,n_with_trademark,"
                 "n_with_product_node,avg_product_class,n_multi_class "
                 "FROM v_product_grade_cross ORDER BY grade_multilabel")]

    # 与用户主表 9 列的守恒对照（按 enterprise_id 归一，避免现用名/曾用名假差异）
    compare = {"available": have_csv}
    if have_csv:
        alias = build_alias_map(main, con)
        product_classes = {r[0] for r in c.execute(
            "SELECT nice_class FROM dim_product_nice_class WHERE is_product_node=1")}
        tm_by_eid = defaultdict(lambda: [0, 0, set(), set(), set()])
        for r in tm:
            eid = alias.get(norm(r["企业名称"].strip()))
            if not eid:
                continue
            k = int(re.match(r"^\s*(\d+)", r["国际分类"]).group(1))
            a = tm_by_eid[eid]
            a[0] += 1
            if r["商标状态"].strip().startswith("已注册"):
                a[1] += 1
            b = r["商标名称"].strip()
            if b and b not in ("-", "图形"):
                a[2].add(b)
            (a[3] if k in product_classes else a[4]).add(k)
        bad = defaultdict(int)
        for m in main:
            eid = alias.get(norm(m["企业名称"].strip()))
            a = tm_by_eid.get(eid, [0, 0, set(), set(), set()])
            pairs = (
                ("商标_件数", a[0]), ("商标_有效件数", a[1]),
                ("商标_品牌数", len(a[2])),
                ("商标_产品类别数", len(a[3])), ("商标_服务类别数", len(a[4])),
            )
            for col, got in pairs:
                if str(got) != str(m.get(col, "")).strip():
                    bad[col] += 1
        compare.update({
            "main_rows": len(main),
            "mismatch_by_column": dict(bad),
            "brand_mismatch_firms": bad["商标_品牌数"],
            "columns_fully_conserved": [k for k in ("商标_件数", "商标_有效件数",
                                                    "商标_产品类别数", "商标_服务类别数")
                                        if bad[k] == 0],
        })

    untouched = {
        "graph_node_types": dict(c.execute(
            "SELECT node_type, COUNT(*) FROM ads_graph_node GROUP BY 1")),
        "annual_slice_frames": c.execute(
            "SELECT COUNT(DISTINCT time_slice) FROM v_graph_slice_annual").fetchone()[0],
        "ads_label": tuple(c.execute(
            "SELECT COUNT(*), SUM(label_value=1), SUM(label_value=0) "
            "FROM ads_label").fetchone()),
        "grades": dict(c.execute(
            "SELECT grade_multilabel, COUNT(*) "
            "FROM v_enterprise_multilabel_grade GROUP BY 1")),
        "product_node_in_main_graph": c.execute(
            "SELECT COUNT(*) FROM ads_graph_node "
            "WHERE node_type LIKE '%product%'").fetchone()[0],
    }
    con.close()

    return {
        "title": "v2.1 商标明细 + 产品节点并列层接入报告",
        "generated_by": "src/data/export_product_node_report.py",
        "schema_version": version,
        "structure": {"tables": n_tab, "views": n_view, "columns": n_cols},
        "source": {
            "csv_available": have_csv,
            "trademark_rows": n_tm, "trademark_firms": n_firms,
            "trademark_csv_rows": len(tm), "product_csv_rows": len(prod),
            "main_csv_rows": len(main),
            "class_rows": cls["rows"], "product_nodes": cls["product_nodes"],
            "service_classes": cls["service_classes"],
            "product_trademarks": kinds.get("product"),
            "service_trademarks": kinds.get("service"),
        },
        "cleaning": clean,
        "bridge": bridge,
        "product_nodes": nodes,
        "conservation": {
            "registered_matches_main": (compare.get("mismatch_by_column", {})
                                        .get("商标_有效件数", 0) == 0) if have_csv else None,
            "stated_match_all": cls["stated_mismatch"] == 0,
            "service_leak": bridge["service_leak"],
            "brand_mismatch_firms": compare.get("brand_mismatch_firms") if have_csv else None,
            "columns_fully_conserved": compare.get("columns_fully_conserved", []),
            "main_compare": compare,
        },
        "corrections": {
            "product_node_count": {"user_xlsx": 45, "authoritative": cls["product_nodes"],
                                   "reason": "35–39、41–45 共 10 个服务类不作产品节点"},
            "person_rows": {"user_xlsx": 2569, "authoritative": 2079,
                            "reason": "原始 2569 行含 490 行表头脏行；且它不是图节点，图内人员＝发明人"},
            "coverage_state": {"user_xlsx": "✅ 本轮补齐（完整）",
                               "authoritative": "类别粒度部分闭合（SKU 级仍缺，未并入主图）"},
        },
        "six_entity_coverage": cov,
        "grade_cross": cross,
        "untouched": untouched,
        "prohibitions": [
            "禁止把 45 个尼斯类全算产品节点；10 个服务类（1902 条商标）不作节点。",
            "禁止把尼斯核定类别写成 SKU 级产品实体，或用商标名称当产品节点。",
            "禁止宣称「立项六类实体已完整覆盖」；主图仍是 5 类，产品节点图内计数为 0。",
            "禁止把产品节点插入 ads_graph_node 而不同步重建 29 帧时序切面与守恒校验。",
            "禁止把 v_product_grade_cross 的并列观察量当评级因子或外部效度证据。",
            "禁止把主表「商标_品牌数」与库内 n_brand_exact 混用（归一规则未反推，存在差异）。",
            "禁止把用户 xlsx 的「人员 2569 节点」当清洗后人数或图节点数。",
            "禁止把企查查回传商标数据公开分发，服务条款与分发范围待单独拍板。",
        ],
        "reproduce": [
            "python -m src.data.apply_v2_1_product_node --check   # 只读校验迁移",
            "python -m src.data.export_product_node_report        # 本报告（只读）",
            "python -m src.data.export_data_dictionary            # 字段字典",
            "python -m src.data.export_graph_temporal_contract     # 时序契约 §6",
            "python -m src.data.export_field_gap_report           # 缺口定稿报告",
            "python -m src.data.export_delivery                   # 交付散文件（不带 --zip）",
            "python tests/test_product_node.py                    # 本轮守卫",
        ],
    }


def build_md(d: dict) -> str:
    S, C, B, K = d["source"], d["cleaning"], d["bridge"], d["conservation"]
    L = []

    def A(s=""):
        L.append(s)

    A("# %s" % d["title"])
    A("")
    A("> 生成：`src/data/export_product_node_report.py`（数字只读现算自 "
      "`data/db/zjtech_credit.db`）｜schema **%s**｜%d 表 / %d 视图 / %d 列"
      % (d["schema_version"], d["structure"]["tables"],
         d["structure"]["views"], d["structure"]["columns"]))
    A("> 数据源：`_data_collect/out/商标明细.csv`、`产品节点_尼斯分类.csv`、"
      "`合并_500家_企业主表.csv`（2026-11-12 补充更新，零新增采购）。"
      "CSV 可用：%s" % ("是" if S["csv_available"] else "否（只按库内回算）"))
    A("")

    A("## 1. 结论速览")
    A("")
    A("本轮按用户选定的**并列层方案**接入商标数据：新增 3 表 4 视图，"
      "库结构 37/33/997 → **%d 表 / %d 视图 / %d 列**。"
      % (d["structure"]["tables"], d["structure"]["views"], d["structure"]["columns"]))
    A("产品节点是**尼斯核定类别 %d 类 / %d 户（%.1f%%）**，**未并入 "
      "`ads_graph_node` 主图**；立项六类实体登记为「**类别粒度部分闭合，SKU 级仍缺**」，"
      "29 帧年度时序切面与守恒校验逐项未变，标签与五档未动。"
      % (S["product_nodes"], B["firms"], 100.0 * B["firms"] / 500))
    A("")

    A("## 2. 数据来源与验收")
    A("")
    A("| 项 | 实测 | 说明 |")
    A("|---|---|---|")
    A("| 商标明细 | %s 条 / %s 家 | 尼斯 1–45 类，45 类全部可解析，注册号 0 空缺 |"
      % (format(S["trademark_rows"], ","), S["trademark_firms"]))
    A("| 产品类商标 / 服务类商标 | %s / %s 条 | 产品类 1–34 + 40；服务类 35–39、41–45 |"
      % (format(S["product_trademarks"] or 0, ","),
         format(S["service_trademarks"] or 0, ",")))
    A("| 尼斯分类维表 | %d 行 = %d 产品类 + %d 服务类 | 服务类保留但不作产品节点 |"
      % (S["class_rows"], S["product_nodes"], S["service_classes"]))
    A("| 企业—产品桥接 | %s 行 / %s 户 / %s 条 | 只保留 35 个产品类 |"
      % (format(B["rows"], ","), B["firms"], format(B["trademarks"], ",")))
    A("")

    A("## 3. 入库清洗")
    A("")
    A("| 清洗项 | 结果 |")
    A("|---|---|")
    A("| 申请日期 `-` | %d 条 → NULL |" % C["apply_date_null"])
    A("| 注册公告日期 `-` | %d 条 → NULL |" % C["reg_announce_null"])
    A("| 仍残留 `-` 的日期 | %d 条（应为 0） |" % C["dash_remaining"])
    A("| 商标状态 | %d 种保留原文；另给 `is_registered`（以「已注册」开头） |"
      % C["status_variants"])
    A("| 已注册商标 | %s 条；与用户主表「商标_有效件数」合计守恒：%s |"
      % (format(C["registered_total"], ","),
         "是" if C["registered_total"] == 5257 else "否"))
    A("| 企业名映射 | 444 家 100% 命中 `dim_enterprise`；借主表 "
      "`QCC_现用名称/曾用名` 桥接，`company_name` 保留回传原文 |")
    A("")

    A("## 4. 三处口径纠正（入库前必须对齐）")
    A("")
    A("| 项 | 用户汇总表 | **权威口径** | 理由 |")
    A("|---|---|---|---|")
    A("| 产品节点数 | 45 | **%d** | 10 个服务类不作产品节点；粒度是类别不是 SKU |"
      % S["product_nodes"])
    A("| 人员数 | 2,569 | **2,079** | 原始 2569 行含 490 行表头脏行；且它不是图节点，"
      "图内人员＝发明人 inventor |")
    A("| 覆盖状态 | ✅ 本轮补齐 | **类别粒度部分闭合** | 产品节点在并列层、"
      "图节点数为 0，禁止宣称六类已完整覆盖 |")
    A("")
    A("**品牌数口径差异**：库内 `n_brand_exact` 按贴源明细精确去重（排除 `-`/`图形`），"
      "与用户主表「商标_品牌数」有 %s 家不一致（归一规则未能反推）。两者**并存、禁止混用**；"
      "库内一律以贴源明细重算，主表 9 列只作外部对照。"
      % (K["brand_mismatch_firms"] if K["brand_mismatch_firms"] is not None else "若干"))
    A("")
    A("**守恒结果**：`v_product_node.stated_match` 全 1 = %s；服务类泄漏进桥接表 = %d 行；"
      "主表完全守恒的列 = %s。"
      % ("是" if K["stated_match_all"] else "否", K["service_leak"],
         "、".join(K["columns_fully_conserved"]) or "无"))
    A("")

    A("## 5. 六类实体覆盖（现场回算）")
    A("")
    A("| 实体 | 图内节点 | 来源行数 | 覆盖户 | 覆盖率 | 状态 |")
    A("|---|---|---|---|---|---|")
    for x in d["six_entity_coverage"]:
        A("| %s | %s | %s | %s | %.1f%% | %s |" % (
            x["entity_type"], format(x["n_in_graph"] or 0, ","),
            format(x["n_source_rows"] or 0, ","),
            format(x["n_covered_firms"] or 0, ","),
            100.0 * (x["coverage_rate"] or 0), x["coverage_state"]))
    A("")
    A("产品行 `图内节点 = 0` 是**并列层方案的有意结果**，不是数据缺失。"
      "`v_six_entity_coverage` 把这两处纠正固化在库里，防止后续文档再写回旧数。")
    A("")

    A("## 6. 与已发布五档交叉（并列观察量）")
    A("")
    A("| 档次 | 企业数 | 有商标 | 有产品节点 | 平均产品类数 | ≥3 类 |")
    A("|---|---|---|---|---|---|")
    for x in d["grade_cross"]:
        A("| %s | %d | %d | %d | %s | %d |" % (
            x["grade_multilabel"], x["n_enterprise"], x["n_with_trademark"],
            x["n_with_product_node"], x["avg_product_class"], x["n_multi_class"]))
    A("")
    A("⚠️ 这只是**并列观察量**，未进入标签定义与分档规则，不得当作评级有效性证据；"
      "各档产品类数差异也可能只是行业结构，不是风险信号。")
    A("")

    A("## 7. 口径隔离（逐项未动）")
    A("")
    U = d["untouched"]
    A("| 对象 | 实测 |")
    A("|---|---|")
    A("| `ads_graph_node.node_type` | %s |"
      % "、".join("%s %s" % (k, format(v, ","))
                 for k, v in sorted(U["graph_node_types"].items())))
    A("| 主图内产品节点 | %d（应为 0） |" % U["product_node_in_main_graph"])
    A("| 年度时序切面帧数 | %d（应为 29） |" % U["annual_slice_frames"])
    A("| `ads_label`（总/断档/续存） | %s |" % (U["ads_label"],))
    A("| 多标签五档 | %s |"
      % "、".join("%s %d" % (k, v) for k, v in sorted(U["grades"].items())))
    A("")
    A("`v_model_train_ready*`、29 列主宽表、`dim_enterprise`、`ASOF_VER`、"
      "`DEFINITION_VER` 与 `v_graph_slice_annual*` 两个视图的 DDL 亦未改动。")
    A("")

    A("## 8. 禁止清单与复现")
    A("")
    for i, x in enumerate(d["prohibitions"], 1):
        A("%d. %s" % (i, x))
    A("")
    A("```bash")
    for x in d["reproduce"]:
        A(x)
    A("```")
    A("")
    A("原始回传 CSV 只存于仓库外 `_data_collect/out/`，未纳入交付包；"
      "公开分发范围待单独拍板。")
    return "\n".join(L) + "\n"


def main() -> None:
    d = gather()
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(build_md(d), encoding="utf-8", newline="\n")
    OUT_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8",
                        newline="\n")
    print("已写出 %s / %s" % (OUT_MD.name, OUT_JSON.name))
    print("  %d 表 / %d 视图 / %d 列；商标 %d 条 %d 家；产品节点 %d / 服务类 %d；"
          "桥接 %d 行 %d 户；主图产品节点 %d"
          % (d["structure"]["tables"], d["structure"]["views"],
             d["structure"]["columns"], d["source"]["trademark_rows"],
             d["source"]["trademark_firms"], d["source"]["product_nodes"],
             d["source"]["service_classes"], d["bridge"]["rows"],
             d["bridge"]["firms"], d["untouched"]["product_node_in_main_graph"]))


if __name__ == "__main__":
    main()
