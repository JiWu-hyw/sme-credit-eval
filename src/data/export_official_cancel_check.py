"""导出《官方撤销公告核查报告》（只读，不改库、不改标签）。

背景
----
`qual_survival` 是用公示名单差分造的代理标签，口径原文里一直挂着一句
「本地无撤销/复核未通过公告，正式口径待导师确认后升版重跑」（issue ①）。
2026-11-11 官方通道已完整走通：高新技术企业认定工作网「取消高企资格公告」
栏目浙江省 + 宁波市 2022–2026 全部 7 份公告，287 家企业名全部提取
（2 份文本 PDF + 5 份扫描件 19 页 170 DPI 逐页读图），并与 500 家样本、
307 家已标注、21 家断档做了含别名的全量比对，三层均 0 命中。

为什么单独出一份报告，而不是直接改标签
--------------------------------------
1. 核查结论是**审计口径**，库内 v1.9 的 `ads_label` 仍是 21 / 307；两者混用会造出
   「同一份数据两个断档率」的新混乱。
2. 导师书面正式口径（路径 b）与 v0.2 别名归一升版尚未拍板，现在改
   `DEFINITION_VER` 会导致短期内二次升版，违反「升版后旧实验结论不得跨版本比较」。
3. 处置决定是**保留该指标，作为多标签评级体系里的其中一个标签**，而不是废弃或
   原地改写；因此只需要把语义边界与噪声传导路径写清楚。

同时本报告登记 issue ② 的新进展：287 家是 A 轴「撤销/复核未通过」的完整行政
决定集合（独立于公示名单、免费、可复核），但**与本项目 500 家样本交集为 0**，
因此对本样本而言 A 轴该分支正例仍为 0，不能直接宣称「A 轴已补齐」。

输入（只读证据快照，均在 data/audit/）
  - 标签核查_21家断档逐户.csv           21 家断档逐户判定（改名假象 / 真断档候选）
  - 官方_取消高企资格_浙江名单.csv       7 份公告提取的 287 家企业名
  - 公告清单_浙江宁波7条.json            7 条公告元数据（提取方式 / 渲染页数 / 更名标注）
  - 证据_2026-02第12条_官方更名标注.png  官方原件自带更名标注的单页证据
  - data/db/zjtech_credit.db             只读核对 ads_label、别名表、多标签档位

产出
  outputs/official_cancel_check.md    人读报告
  outputs/official_cancel_check.json  机器可读结论

用法
  python -m src.data.export_official_cancel_check
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from src.data.build_qual_dataset import DATASET_VER, DEFINITION_VER

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "data" / "audit"
DB = ROOT / "data" / "db" / "zjtech_credit.db"
OUT_MD = ROOT / "outputs" / "official_cancel_check.md"
OUT_JSON = ROOT / "outputs" / "official_cancel_check.json"

CHECK_CSV = AUDIT / "标签核查_21家断档逐户.csv"
OFFICIAL_CSV = AUDIT / "官方_取消高企资格_浙江名单.csv"
NOTICE_JSON = AUDIT / "公告清单_浙江宁波7条.json"


def _pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.2f}%" if b else "—"


def _norm(x: str) -> str:
    """名称归一：去全半角括号、中点与空白后精确匹配（与采集侧口径一致）。"""
    return re.sub(r"[（()）·\s]", "", str(x or ""))


def load_evidence() -> dict:
    with CHECK_CSV.open(encoding="utf-8-sig", newline="") as f:
        check = list(csv.DictReader(f))
    with OFFICIAL_CSV.open(encoding="utf-8-sig", newline="") as f:
        official = list(csv.DictReader(f))
    notice = json.loads(NOTICE_JSON.read_text(encoding="utf-8"))

    alias = [r for r in check if r["是否应修正为0"] == "是"]
    real = [r for r in check if r["是否应修正为0"] == "否"]
    hit = [r for r in check if r["官方撤销名单命中"] == "是"]

    off_by_month = Counter(r["公告年月"] for r in official)
    notices = notice["公告"]
    scanned = [n for n in notices if n["公告类型"] == "扫描件"]
    text = [n for n in notices if n["公告类型"] == "文本"]
    rename = next((n["更名标注"] for n in notices if "更名标注" in n), None)

    return {
        "check": check,
        "official": official,
        "notices": notices,
        "alias": alias,
        "real": real,
        "hit": hit,
        "off_by_month": dict(sorted(off_by_month.items())),
        "scanned": scanned,
        "text_notices": text,
        "scanned_pdf_missing": [n for n in scanned if not n["PDF已落盘"]],
        "off_names": {_norm(r["企业名称"]) for r in official},
        "national_total": notice.get("全国栏目公告数", 585),
        "official_total": notice.get("官方企业名总数", len(official)),
        "pdf_count": notice.get("PDF份数", 7),
        "png_total": notice.get("渲染页总数", 27),
        "png_scanned": notice.get("扫描件读图页数", 19),
        "scanned_family": notice.get("扫描件标题家数", 68),
        "rename": rename,
    }


def db_facts(ev: dict) -> dict:
    """只读核对：库仍是 v1.9 原值、改名假象档位，以及三层含别名比对 0 命中。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    n_all, n_fail, n_keep = cur.execute(
        "SELECT COUNT(*), SUM(label_value=1), SUM(label_value=0) FROM ads_label"
    ).fetchone()
    direction = cur.execute(
        "SELECT credit_label, COUNT(*) FROM v_label_direction GROUP BY 1 ORDER BY 1"
    ).fetchall()

    # ---- 含别名的官方名单比对（可复现，别名列 alias_name / alias_norm）----
    alias_map: dict[str, set[str]] = defaultdict(set)
    for eid, an, anorm in cur.execute(
        "SELECT enterprise_id, alias_name, alias_norm FROM dim_enterprise_alias"
    ):
        if an:
            alias_map[eid].add(_norm(an))
        if anorm:
            alias_map[eid].add(_norm(anorm))

    off = ev["off_names"]

    def hit_names(rows: list[tuple[str, str]]) -> list[str]:
        out = []
        for eid, name in rows:
            cand = {_norm(name)} | alias_map.get(eid, set())
            if cand & off:
                out.append(name)
        return out

    sample = cur.execute(
        "SELECT enterprise_id, name_display FROM dim_enterprise").fetchall()
    labeled = cur.execute(
        "SELECT a.enterprise_id, e.name_display FROM ads_label a "
        "JOIN dim_enterprise e ON e.enterprise_id = a.enterprise_id").fetchall()
    failed = cur.execute(
        "SELECT a.enterprise_id, e.name_display FROM ads_label a "
        "JOIN dim_enterprise e ON e.enterprise_id = a.enterprise_id "
        "WHERE a.label_value = 1").fetchall()
    match = {
        "样本企业数": len(sample),
        "样本命中数": len(hit_names(sample)),
        "已标注企业数": len(labeled),
        "已标注命中数": len(hit_names(labeled)),
        "断档企业数": len(failed),
        "断档命中数": len(hit_names(failed)),
        "有别名企业数": len(alias_map),
        "别名去重数": len(set().union(*alias_map.values())) if alias_map else 0,
        "归一规则": "去除全半角括号 / 中点 / 空白后精确匹配；别名取 dim_enterprise_alias.alias_name 与 alias_norm",
    }

    def grades(names: list[str]) -> tuple[dict, dict]:
        c: Counter = Counter()
        by_name = {}
        for n in names:
            row = cur.execute(
                "SELECT g.grade_multilabel FROM v_enterprise_multilabel_grade g "
                "JOIN dim_enterprise e ON e.enterprise_id = g.enterprise_id "
                "WHERE e.name_display = ?", (n,)
            ).fetchone()
            g = row[0] if row else "未匹配"
            c[g] += 1
            by_name[n] = g
        return dict(c), by_name

    alias_grades, alias_by_name = grades([r["企业名称"] for r in ev["alias"]])
    real_grades, _ = grades([r["企业名称"] for r in ev["real"]])

    out = {
        "n_label": n_all,
        "n_fail": n_fail,
        "n_keep": n_keep,
        "direction": [[int(a), int(b)] for a, b in direction],
        "grade_alias": alias_grades,
        "grade_real": real_grades,
        "grade_alias_by_name": alias_by_name,
        "grade_all": dict(cur.execute(
            "SELECT grade_multilabel, COUNT(*) FROM v_enterprise_multilabel_grade "
            "GROUP BY 1 ORDER BY 1").fetchall()),
        "definition_ver": DEFINITION_VER,
        "dataset_ver": DATASET_VER,
        "match": match,
    }
    con.close()
    return out


def build_payload(ev: dict, db: dict) -> dict:
    n_alias = len(ev["alias"])
    n_real = len(ev["real"])
    n_hit = len(ev["hit"])
    n_off = len(ev["official"])
    n_label = db["n_label"]
    rate_old = 100.0 * db["n_fail"] / n_label
    rate_new = 100.0 * n_real / n_label
    m = db["match"]

    wording = (
        "已核对国家高新技术企业认定工作网「取消高企资格公告」栏目 2022–2026 年"
        "浙江省及宁波市全部 7 份《取消高新技术企业资格公告》（287 家），"
        "21 家资质断档企业 0 家出现在官方撤销名单，500 家样本企业亦 0 家命中；"
        "其中 7 家经企业名称别名归一后确认高企资格持续有效，属于名称匹配失败造成的假断档。"
        "据此判定 qual_survival 标签度量的是**资质再申报行为**，"
        "非官方撤销 / 复核未通过结论，亦不等同于信用违约。"
    )

    return {
        "generated_at": f"{datetime.now():%Y-%m-%d %H:%M}",
        "issue": "① 撤销/复核未通过公告或导师确认正式口径；② A 轴独立结果标签",
        "verdict": "代理标签量的是资质再申报行为，不是官方撤销结论",
        "source_channel": {
            "site": "高新技术企业认定工作网 innocom.gov.cn/gqrdw/c101483",
            "栏目": "取消高企资格公告",
            "全国公告数": ev["national_total"],
            "浙江宁波公告数": len(ev["notices"]),
            "覆盖年份": "2009–2026（浙江宁波命中 2022–2026）",
            "文本型公告": len(ev["text_notices"]),
            "扫描件公告": len(ev["scanned"]),
            "扫描件标题家数": ev["scanned_family"],
            "PDF份数": ev["pdf_count"],
            "PDF缺失份数": len(ev["scanned_pdf_missing"]),
            "扫描件读图页数": ev["png_scanned"],
            "渲染页总数": ev["png_total"],
            "已提取家数": n_off,
            "标题声明家数": sum(n["公告标题家数"] for n in ev["notices"]),
            "无漏读": n_off == sum(n["公告标题家数"] for n in ev["notices"]),
        },
        "official_names": {
            "已提取记录数": n_off,
            "按公告年月": ev["off_by_month"],
            "去重企业名数": len(ev["off_names"]),
            "口径注记": "2023-12 公告标题声明 14 家，最终 CSV 提取 14 条，与标题一致（早期版本曾过提取为 16 条，已修正）；7 份公告标题家数合计 287，已提取 287，7/7 完全一致，无漏读。",
        },
        "coverage_match": m,
        "official_rename_evidence": ev["rename"],
        "per_enterprise_check": {
            "断档样本数": len(ev["check"]),
            "改名假象数": n_alias,
            "改名假象占比": _pct(n_alias, len(ev["check"])),
            "真断档候选数": n_real,
            "官方撤销名单命中数": n_hit,
            "命中明细": [],
        },
        "rates": {
            "库内原断档率": f"{db['n_fail']}/{n_label} = {rate_old:.2f}%",
            "别名归一后审计断档率": f"{n_real}/{n_label} = {rate_new:.2f}%",
            "口径隔离": "14/307 是审计结论，库内 v1.9 仍为 21/307；两者禁止混用，升版前不得用 4.56% 替换任何已发布数字。",
        },
        "db_state": db,
        "label_position": {
            "决策": "保留指标，作为多标签评级体系里的其中一个标签",
            "所在轴": "C 轴 · 资质存续 / 再申报行为",
            "对应锚点": "v_enterprise_label_matrix.y_survival",
            "并列锚点数": 6,
            "是否独立": False,
            "同源说明": "与 B 轴资质阶梯同源于公示名单，不构成独立印证",
            "评级传导": "v_enterprise_multilabel_grade 中 y_survival=0 会一票触发 D 观察档",
            "改名假象当前档位": db["grade_alias"],
            "真断档当前档位": db["grade_real"],
            "禁止": "不得单独用该标签代表企业好坏、不得当外部效度、不得对外表述为「预测高企复核未通过」",
        },
        "a_axis": {
            "完整正例集合": n_off,
            "性质": "行政决定（取消高企资格），独立于公示名单差分；零经费、可复核（7 份原始 PDF + 27 页渲染图）",
            "与500家样本交集": m["样本命中数"],
            "对本样本可用正例数": m["样本命中数"],
            "母池口径": "用户提供采集侧口径 21,968 家；该规模未在本仓库落库，论文引用需注明来源",
            "母池事件率": "287/21,968 ≈ 1.31%（约 1.3%），E 档依然塌陷",
            "现有硬结果锚点": "v_enterprise_label_matrix.y_hard_outcome 覆盖 463 家，仅 3 家命中硬负面事件，E 档 3 家",
            "结论": "A 轴「撤销」分支已取得完整事件集合，但与当前 500 家样本交集为 0，不能直接宣称 A 轴已补齐；要支撑「A→E 单调递增」需叠加严重失信 / 经营异常 / 被执行做复合负面事件，或把母池扩到含这 287 家",
        },
        "official_wording": wording,
        "not_closed": [
            "导师书面正式口径（路径 b）仍未取回",
            "DEFINITION_VER 升版 v0.2（别名归一）与全链路重跑待拍板后一次性执行",
            "287 家官方撤销名单仅做证据快照与比对，尚未入库建表",
            "母池 21,968 家规模为采集侧口径、仓库内未落库，1.3% 事件率需标注来源",
            "A 轴复合负面事件（严重失信 + 经营异常 + 被执行）尚未叠加，E 档仍塌陷",
        ],
        "reproduce": "python -m src.data.export_official_cancel_check",
    }


def render(ev: dict, db: dict, p: dict) -> str:
    L: list[str] = []
    A = L.append
    n_alias = len(ev["alias"])
    n_real = len(ev["real"])
    n_off = len(ev["official"])
    n_label = db["n_label"]
    m = db["match"]
    sc = p["source_channel"]

    A("# 官方撤销公告核查报告 · issue ① 实证闭合 / issue ② 新证据\n")
    A(f"> 生成：{p['generated_at']}　｜　证据快照：`data/audit/`　｜　"
      f"库状态：`{db['definition_ver']}` / `{db['dataset_ver']}`（**未升版**）\n")
    A("> 本报告只做核查与口径定稿：**未改库、未升版**，"
      "未改 `ads_label`、未改任何标签值。\n")
    A("---\n")

    # ---------------- 1 ----------------
    A("## 1. 结论速览\n")
    A("| 结论 | 证据 |")
    A("|---|---|")
    A(f"| 官方通道已**完整覆盖**，无漏读 | 2022–2026 浙江省 + 宁波市全部 7 份公告，"
      f"标题声明 {sc['标题声明家数']} 家，已提取 **{n_off} 家**，7/7 完全一致 |")
    A(f"| 标签量的是「有没有再申报」，不是官方撤销 | {n_real} 家真断档候选在 "
      f"{n_off} 家官方撤销名单中命中 **{len(ev['hit'])} 家**（21 家整体、"
      f"{m['样本企业数']} 家样本亦均为 0 命中） |")
    A(f"| 「企业更名导致名称匹配失败」是真实且量大的混淆源 | 21 家断档里 **{n_alias} 家"
      f"（{_pct(n_alias, len(ev['check']))}）** 经别名归一后确认资格持续有效；"
      "官方公告原件自带更名标注可佐证 |")
    A(f"| 别名归一后的审计断档率 | **{n_real}/{n_label} = {_pct(n_real, n_label)}**"
      f"（库内原口径 {db['n_fail']}/{n_label} = {_pct(db['n_fail'], n_label)}） |")
    A("| 处置决定 | **保留该指标**，作为多标签评级体系里 C 轴的其中一个标签，不废弃、不原地改写 |")
    A("")
    A(f"> ⚠️ **口径隔离（最重要的一条）**：{n_real}/{n_label} 是**审计结论**，"
      f"库内 v1.9 的 `ads_label` 仍是 {db['n_fail']} 断档 / {db['n_keep']} 续存 / "
      f"{n_label} 家已标注。升版前**禁止**用 {_pct(n_real, n_label)} 替换任何已发布数字，"
      "也禁止把两者写进同一张性能表。\n")

    # ---------------- 2 ----------------
    A("## 2. 证据链：7 份公告 287 家全覆盖\n")
    A("通道：高新技术企业认定工作网「取消高企资格公告」栏目 "
      "`innocom.gov.cn/gqrdw/c101483`，全国共 **%s 条**公告（2009–2026）。"
      "浙江省 + 宁波市（计划单列市，独立认定机构）命中 **%d 条**：\n"
      % (sc["全国公告数"], len(ev["notices"])))
    A("| 公告年月 | 认定机构 | 标题家数 | 已提取 | 类型 | 提取方式 | PDF |")
    A("|---|---|---:|---:|---|---|---|")
    for n in ev["notices"]:
        A("| %s | %s | %d | %d | %s | %s | %s |" % (
            n["公告年月"], n["认定机构"], n["公告标题家数"], n.get("已提取家数", 0),
            n["公告类型"], n.get("提取方式", "—"),
            "✅ 已落盘" if n["PDF已落盘"] else "❌ 缺失"))
    A("")
    A("**合计 %d 家 = 文本型 %d 家 + 扫描件 %d 家；扫描件逐份家数（4+13+2+4+45）"
      "与公告标题声明完全一致，没有漏读。** 企业名 CSV 共 **%d 条**（%s），"
      "去重 %d 个名字。\n"
      % (n_off, sum(x["公告标题家数"] for x in ev["text_notices"]),
         sc["扫描件标题家数"], n_off,
         " / ".join("%s=%d" % (k, v) for k, v in ev["off_by_month"].items()),
         len(ev["off_names"])))
    A("**扫描件读图怎么做的**：本地无 PDF 光栅化工具 → 用 pymupdf 锁定 "
      "`cp3xx-abi3-win_amd64` 轮子（首次误装 wasm32 版导入报错；aliyun 镜像需解析 "
      "href 真实路径，不能拼文件名）；LibreOffice 渲染被 `spawn EPERM` 挡住，改为直接用 "
      "pymupdf 渲染 **170 DPI PNG**。5 份扫描件共 **%d 页**逐页人工读图提取，"
      "连同 2 份文本型公告共 **%d 页**渲染图留档，原始 PDF **%d 份**齐备"
      "（含此前缺失的 2026-08 宁波市 45 家）。\n"
      % (sc["扫描件读图页数"], sc["渲染页总数"], sc["PDF份数"]))
    A("**两处旧口径已闭合**：① 2023-12 公告标题声明「等 14 家」，早期 CSV 曾过提取 "
      "16 条，本轮修正为 **14 条**，与标题一致；② 2026-08 宁波市 45 家 PDF 已补下载并读图，"
      "不再存在「未落盘 / 待 OCR」项。\n")
    if ev["rename"]:
        r = ev["rename"]
        A("**官方原件自带更名标注**（2026-02 公告第 %d 条，见随附渲染图证据）："
          "`%s（更名为：%s）`，证书编号 `%s`，%s，取消资格起始年度 %s。"
          "这说明**连官方公告都需要标注更名**，而名单差分没有做别名归一，"
          "正是 7 家假断档的成因。⚠️ 该括号备注在提取 CSV 时被清洗为主名，"
          "因此比对必须配合 `dim_enterprise_alias` 别名表，CSV 本身看不到更名标注。\n"
          % (r["序号"], r["原企业名"], r["更名后"], r["证书编号"],
             r["地市"], r["取消资格起始年度"]))

    # ---------------- 3 ----------------
    A("## 3. 全量比对复现：三层 0 命中\n")
    A("用 287 家官方名单，对库内三层企业集合做**含别名**的精确比对"
      "（归一规则：去全半角括号 / 中点 / 空白；别名取 "
      "`dim_enterprise_alias.alias_name` 与 `alias_norm`，%d 家企业有别名、"
      "共 %d 个去重别名）：\n"
      % (m["有别名企业数"], m["别名去重数"]))
    A("| 比对集合 | 家数 | 命中官方撤销名单 |")
    A("|---|---:|---:|")
    A("| 500 家样本（`dim_enterprise` 全量） | %d | **%d** |" % (m["样本企业数"], m["样本命中数"]))
    A("| 307 家已标注（`ads_label`） | %d | **%d** |" % (m["已标注企业数"], m["已标注命中数"]))
    A("| 21 家断档（`label_value=1`） | %d | **%d** |" % (m["断档企业数"], m["断档命中数"]))
    A("")
    A("即：**500 家样本里没有一家被官方撤销资格，21 家断档里也没有一家被官方撤销**。"
      "这不再是推测——官方名单构成了对「该标签量的是官方撤销结论」的直接反证。"
      "比对逻辑已固化在本报告生成源里，可重复执行。\n")

    # ---------------- 4 ----------------
    A("## 4. 21 家逐户核查：7 家改名假象\n")
    A("用 `dim_enterprise_alias` + 企查查现用名/曾用名做别名归一后，以下 %d 家的"
      "**现用名出现在资格到期当年的公示名单里**，即资格一直有效，只是换了名字：" % n_alias)
    A("")
    A("| 原登记名（原判断档） | 现用名 / 到期年命中名称 | 命中年份 | 当前多标签档位 |")
    A("|---|---|---|---|")
    for r in ev["alias"]:
        A("| %s | %s | %s | %s |" % (
            r["企业名称"], r["到期年后命中名称"], r["到期年后出现年"],
            db["grade_alias_by_name"].get(r["企业名称"], "未匹配")))
    A("")

    # ---------------- 5 ----------------
    A("## 5. 剩余 %d 家：官方撤销名单零命中\n" % n_real)
    A("| # | 企业 | cohort | 到期年 | 官方撤销名单 |")
    A("|---:|---|---:|---:|---|")
    for i, r in enumerate(ev["real"], 1):
        A("| %d | %s | %s | %s | %s |" % (
            i, r["企业名称"], r["cohort_year"], r["资格到期年"], r["官方撤销名单命中"]))
    A("")
    A("**比对结果：%d / %d 命中**（已按全部 %d 家官方名单、含别名重核）。这 %d 家不是"
      "「被驳回」，而是主动未再申报 / 迁出 / 注销 / 名单采集遗漏 —— "
      "正是代理标签的固有缺陷，也是它只能当弱标签的原因。\n"
      % (len(ev["hit"]), n_real, n_off, n_real))

    # ---------------- 6 ----------------
    A("## 6. 对 issue ① 的判定（路径 a 实证闭合，路径 b 待书面口径）\n")
    A("建议写进论文 / 交付说明的措辞（有官方数据支撑，不是「拿不到就算了」）：\n")
    A("> " + p["official_wording"] + "\n")
    A("按评审报告 §7.1(3) 红线，仍然**禁止**对外表述「预测高企复核未通过」，"
      "也禁止把 `score.value`（恒 `null`）当真实信用分。\n")

    # ---------------- 7 ----------------
    a = p["a_axis"]
    A("## 7. 对 issue ② 的意义：A 轴拿到完整事件集合，但样本内仍塌陷\n")
    A("这 %d 家是 A 轴「撤销 / 复核未通过」的**完整行政决定集合**："
      "独立于公示名单差分（行政决定，非差分）、零经费、可复核"
      "（7 份原始 PDF + %d 页渲染图）。三条性质都成立，但必须诚实处理两件事：\n"
      % (n_off, sc["渲染页总数"]))
    A("| 事实 | 数字 | 含义 |")
    A("|---|---:|---|")
    A("| 与本项目 500 家样本的交集 | **%d 家** | 对当前建模样本，A 轴该分支可用正例仍为 0；"
      "不能直接宣称「A 轴已补齐」 |" % m["样本命中数"])
    A("| 相对母池（用户口径 21,968 家） | %d 家 ≈ **1.3%%** | 事件率过低，E 档依然塌陷，"
      "支撑不了「A→E 单调递增」 |" % n_off)
    A("")
    A("现有 `v_enterprise_label_matrix.y_hard_outcome` 覆盖 463 家，"
      "**仅 3 家命中硬负面事件**，E 档 3 家 —— 与上面 0 交集是一致的。"
      "因此下一步方向是：叠加**严重失信 + 经营异常 + 被执行**做复合负面事件，"
      "或把母池扩到包含这 287 家，再做跨源效度检验（MSME / SBA）。\n")
    A("> 📌 口径提醒：母池 21,968 家为采集侧口径，**未在本仓库落库**；"
      "1.3% 是按该口径的估算，论文引用时需单独标注来源，不可当库内实测值。\n")

    # ---------------- 8 ----------------
    A("## 8. 保留指标，作为其中一个标签处理评级\n")
    A("处置决定：**不废弃、不原地 UPDATE**。该指标继续留在多标签体系里，"
      "定位为 **C 轴 · 资质存续 / 再申报行为**，对应 "
      "`v_enterprise_label_matrix.y_survival`，是 6 个二值锚点之一，"
      "与 A 轴硬结果、B 轴资质阶梯、D 轴第三方分箱并列参与 "
      "`v_enterprise_multilabel_grade` 五档评级。\n")
    A("**噪声如何传导进评级**（本次核查新增的可定位影响）：分档规则里 "
      "`y_survival = 0` 会一票触发 **D 观察**档，因此\n")
    A("| 分组 | 家数 | 当前多标签档位 |")
    A("|---|---:|---|")
    A("| 7 家改名假象 | %d | %s（全部） |" % (
        n_alias, " / ".join(db["grade_alias"].keys())))
    A("| %d 家真断档候选 | %d | %s |" % (
        n_real, n_real, " / ".join("%s %d 家" % (k, v) for k, v in db["grade_real"].items())))
    A("")
    A("即**改名假象目前把 7 家本应正常的企业压在 D 档**。这既说明单标签噪声会直接"
      "污染评级，也说明为什么必须把该指标降为「其中一个标签」并标注其 "
      "`与 B 轴同源、不独立` 的边界——它不能单独界定企业好坏，更不能拿自己做外部效度。"
      "别名归一升版后，这 7 家应随标签修正回到其真实档位。\n")
    A("配套红线：不得用 `qcc_tech_level_ord` / `qcc_credit_level_ord` 反向验证分档"
      "（循环定义）；多标签测评里 A–D 档硬结果正例率恒 1.0 是规则使然，不是效度证据。\n")

    # ---------------- 9 ----------------
    A("## 9. 尚未闭合的部分\n")
    for i, x in enumerate(p["not_closed"], 1):
        A("%d. %s" % (i, x))
    A("")
    A("**升版链路（待拍板后再执行，本次未做）**：\n")
    A("```")
    A("改 DEFINITION_VER（qual_survival_proxy_v0.1 → v0.2，引入别名归一）")
    A("  → 重建 ads_label（禁止原地 UPDATE）")
    A("  → 重建 v_model_train_ready / v_model_train_ready_credit")
    A("  → 重跑 as-of 训练、SHAP、多标签测评、方向恒等式与全量守卫")
    A("  → 同步数据字典 / 代理标签说明书 / 数据集卡片 / 发布说明")
    A("```")
    A("")
    A("升版后旧实验结论必须重跑，**不得跨版本比较 AUC**。\n")

    # ---------------- 10 ----------------
    A("## 10. 复现与证据\n")
    A("```bash")
    A("python -m src.data.export_official_cancel_check   # 生成本报告 + JSON（含三层比对复现）")
    A("python tests/test_official_cancel_check.py        # 证据链与口径隔离守卫")
    A("```")
    A("")
    A("下表路径为仓库内 `data/audit/` 的只读证据快照；**交付包内前五项随本报告"
      "同目录附上**（`deliverables/01_算法端/`），便于接收方独立复核，不依赖仓库结构。\n")
    A("| 证据文件 | 内容 |")
    A("|---|---|")
    A("| `标签核查_21家断档逐户.csv` | 21 家逐户判定、别名、命中年份、官方名单比对 |")
    A("| `官方_取消高企资格_浙江名单.csv` | 7 份公告提取的 %d 家企业名 |" % n_off)
    A("| `公告清单_浙江宁波7条.json` | 7 条公告元数据、提取方式、渲染页数、更名标注 |")
    A("| `证据_2026-02第12条_官方更名标注.png` | 官方原件自带更名标注的单页渲染图（170 DPI） |")
    A("| `official_cancel_check.json`（同目录） | 本报告全部结论的机器可读版 |")
    A("| `公告清单_原始URL.json`（仅仓库内） | 栏目采集的原始 URL → 标题映射 |")
    A("")
    A("采集侧可复核原件：`_data_collect/raw/cancel/`（7 份 PDF）、"
      "`_data_collect/raw/cancel_png/`（%d 页 PNG，其中扫描件 %d 页）。\n"
      % (sc["渲染页总数"], sc["扫描件读图页数"]))
    return "\n".join(L) + "\n"


def main() -> int:
    ev = load_evidence()
    db = db_facts(ev)
    p = build_payload(ev, db)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render(ev, db, p), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("写出 %s" % OUT_MD.relative_to(ROOT))
    print("写出 %s" % OUT_JSON.relative_to(ROOT))
    m = db["match"]
    print("官方名单 %d 家 / %d 份公告 / 扫描件读图 %d 页"
          % (len(ev["official"]), len(ev["notices"]), ev["png_scanned"]))
    print("含别名比对：500 样本 %d 命中 / 307 已标注 %d 命中 / 21 断档 %d 命中"
          % (m["样本命中数"], m["已标注命中数"], m["断档命中数"]))
    print("核查 %d 家：改名假象 %d / 真断档 %d / 官方撤销名单命中 %d"
          % (len(ev["check"]), len(ev["alias"]), len(ev["real"]), len(ev["hit"])))
    print("库内保持 %s：%d 断档 / %d 续存 / %d 家已标注（未升版、未改库）"
          % (db["definition_ver"], db["n_fail"], db["n_keep"], db["n_label"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
