"""生成 05_数据库 交付目录：一致性快照 + 说明。

为什么要 VACUUM INTO 而不是直接复制文件：
    直接 copy 会连 WAL/空闲页一起搬，文件里可能残留已删数据与碎片。VACUUM INTO
    生成的是一份重新整理过、事务一致的库，体积也小一点，接收方拿到就能开。

README 的表格不手写，全部现算，避免"说明里的行数和库里的不一致"。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT = os.path.join(ROOT, "deliverables", "05_数据库")
SNAPSHOT = os.path.join(OUT, "zjtech_credit.db")

# Release 附件：文件名是 README 里的下载链接，不能改。
# 04_数据字典与数据集 + 05_数据库 = 11 个文件，加 MANIFEST.txt 共 12 项。
RELEASE_ZIP = os.path.join(ROOT, "deliverables", "zjtech_credit_data.zip")
RELEASE_ARCHIVE = os.path.join(ROOT, "deliverables", "zjtech_credit_data_v1.3.0.zip")
RELEASE_PARTS = ["04_数据字典与数据集", "05_数据库"]

# 分层：按表名前缀归类，未知前缀归「其他」
LAYERS = [
    ("meta_", "META 元数据", "库自身的版本、数据源、入库日志"),
    ("ods_", "ODS 贴源", "原始采集结果，不做业务加工，可回溯"),
    ("dim_", "DIM 维度", "企业 / 发明人 / IPC / 资质类型等实体主档"),
    ("fact_", "FACT 事实", "专利、资质、融资等事件型明细"),
    ("bridge_", "BRIDGE 桥接", "多对多关联（专利-发明人、专利-IPC）"),
    ("dws_", "DWS 汇总", "企业级汇总宽表（技术 / 成长 / 融合三张）"),
    ("ads_", "ADS 应用", "打标样本、as-of 面板、图谱、生存期，直接喂模型与接口"),
    ("ext_", "EXT 外部", "项目外部对照样本（不在本项目统计口径内）"),
]

# 关键表的用途一句话；没写到的按分层说明兜底
NOTES = {
    "dim_enterprise": "500 家浙江专精特新企业主档，`enterprise_id` 为 ENT+12hex",
    "fact_patent": "专利明细；**按行数是 32,969，按申请号去重是 30,103 件**",
    "fact_patent_citation": "**空表**：引文库源数据缺失，已登记为 source_missing",
    "fact_qualification": "高企资格认定明细，标签就来自这里",
    "fact_funding_event": "融资事件 506 条 / 182 家（2019–2026）；`source_id` 506/506 指向 `zj_500family`（A7 回填）",
    "ads_label": "307 家打标结果，21 断档样本",
    "ads_model_dataset": "500 行，含 193 行 `split='excluded'`（窗口未闭合，不是续存样本）",
    "ads_feature_asof": "长表形态的 as-of 特征（feature_name/feature_value）",
    "ads_graph_node": "异质图节点（企业/专利/IPC/发明人/投资机构 5 类）",
    "ads_graph_edge": "异质图边（files_patent/belongs_ipc/invented_by/funded_by/tech_similar）",
    "ads_survival_period": "生存分析期表 1,479 行",
    "ext_msme_sample": "外部对照组 1,707 行，**不属于本项目 500 家样本**",
    "log_match": "企业名称匹配日志，含未匹配上的人工复核线索",
}
VIEW_NOTES = {
    "v_model_train_ready": "**训练唯一入口**：307 行 × 17 列，已切 split、已剔常量特征",
    "v_model_dataset_asof_flat": "614 行 as-of 面板，含 excluded；训练前须过滤 cohort 切面",
    "v_model_dataset_flat": "500 行企业级宽表（后视口径，勿直接训练）",
    "v_patent_canonical": "专利去重视图，30,346 行（去重后仍略多于 30,103 件，差异见报告）",
    "v_enterprise_profile": "企业画像宽视图，接口 `enterprise/{userid}` 在用",
    "v_data_coverage": "13 行覆盖率总览",
    "v_asof_feature_coverage": "58 行 as-of 特征覆盖明细",
    "v_enterprise_grade_l0": (
        "**v1.7 新增** 五档规则基线（A 44 / B 95 / C 275 / D 83 / E 3，合计 500）；"
        "静态快照对照基线，非 as-of 训练口径，不得入模验证；"
        "禁止用第三方等级反向验证（循环定义）"
    ),
    "v_feature_coverage_effective": (
        "**v1.8 新增** 有效特征覆盖：把 m_funding / investor_prestige 的覆盖从旁路表"
        "ads_feature_asof_funding 按双键并入，零覆盖 14 → 12；"
        "取数仍须 LEFT JOIN v_feature_asof_funding，原 v_asof_feature_coverage 保持不动"
    ),
    "v_graph_slice_annual": (
        "**v1.8 新增** 图谱年度切片台账：29 年 × 4 指标累计规模，"
        "按 fact_patent.app_year <= T；物理 time_slice 仍全为 0"
    ),
    "v_graph_slice_annual_edge": (
        "**v1.8 新增** 边级年度切片（files_patent / belongs_ipc / invented_by）；"
        "查询必须带 WHERE time_slice = :T，IPC 节点 ID 已剥 WIPO 版本号后缀"
    ),
    "v_label_direction": (
        "**v1.9 新增** 标签方向对照（307 行）：正例=好企业=资质存续(286)，"
        "负例=坏企业=资质断档(21)；底表 ads_label 未原地 UPDATE"
    ),
    "v_model_train_ready_credit": (
        "**v1.9 新增** 正确方向训练入口：label=1-原 label，286 正例 / 21 负例；"
        "历史 AUC≈0.52 翻成信用方向为 0.48，都表示无判别力"
    ),
    "v_label_mechanism_credit": (
        "**v1.9 新增** 标签机制台账（正确方向）：n_positive 现在真是好企业数，"
        "合计 286 / 21，与旧 v_label_mechanism 互补"
    ),
    "v_enterprise_label_matrix": (
        "**v1.9 / P1 多标签测评** 500 行 × 6 个二值锚点 + 3 个有序锚点；"
        "一律 1=好/正例、0=坏/负例、NULL=不可判定；B/C 轴同源不独立"
    ),
    "v_enterprise_multilabel_grade": (
        "**v1.9 / P1** 多标签五档（A 81 / B 176 / C 157 / D 83 / E 3）+ L0 对照，"
        "同档 294 家(58.8%)；分档复用锚点形成循环定义，不得当外部效度"
    ),
    "v_multilabel_eval_summary": (
        "**v1.9 / P1** 交叉测评长表：档次 → 各锚点正例率、与 L0 一致率、锚点覆盖率；"
        "资质存续 / 硬结果两列是循环定义，只用于暴露口径"
    ),
}


def layer_of(name: str) -> tuple[str, str]:
    for prefix, label, desc in LAYERS:
        if name.startswith(prefix):
            return label, desc
    return "其他", "未归类的辅助对象"


def build(db_path: str = DB) -> dict:
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(SNAPSHOT):
        os.remove(SNAPSHOT)
    t0 = time.time()
    con = sqlite3.connect(db_path)
    try:
        con.execute("VACUUM INTO ?", (SNAPSHOT,))
    finally:
        con.close()
    secs = time.time() - t0

    c = sqlite3.connect("file:%s?mode=ro" % SNAPSHOT, uri=True)
    try:
        objs = list(c.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type DESC, name"))
        rows = {}
        for typ, name in objs:
            rows[name] = c.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
        tables = [n for t, n in objs if t == "table"]
        views = [n for t, n in objs if t == "view"]
        n_cols = sum(len(list(c.execute('PRAGMA table_info("%s")' % n)))
                     for n in tables + views)
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        fk_bad = len(list(c.execute("PRAGMA foreign_key_check")))
        n_ent = c.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
        n_pat = c.execute("SELECT COUNT(DISTINCT app_no) FROM fact_patent").fetchone()[0]
        n_tr = c.execute("SELECT COUNT(*) FROM v_model_train_ready").fetchone()[0]
        n_pos = c.execute("SELECT SUM(label) FROM v_model_train_ready").fetchone()[0]
        n_fund = c.execute("SELECT COUNT(*) FROM fact_funding_event").fetchone()[0]
        n_fund_src = c.execute(
            "SELECT COUNT(source_id) FROM fact_funding_event").fetchone()[0]
        ver = c.execute("SELECT version FROM meta_schema_version "
                        "ORDER BY applied_at DESC LIMIT 1").fetchone()[0]
    finally:
        c.close()

    size_mb = os.path.getsize(SNAPSHOT) / 1024 / 1024

    L, A = [], None
    A = L.append
    A("# 数据库\n")
    A("`zjtech_credit.db` —— 本项目全部数据的唯一来源，**%d MB**，SQLite 3。" % round(size_mb))
    A("")
    A("这份是从工作库 `VACUUM INTO` 出来的**一致快照**（不是文件拷贝），"
      "体积已整理、无残留空闲页，收到即可直接打开。耗时 %.1fs。" % secs)
    A("")
    A("## 体检\n")
    A("| 项 | 结果 |")
    A("|---|---|")
    A("| `PRAGMA integrity_check` | **%s** |" % integrity)
    A("| 外键冲突 | **%d** |" % fk_bad)
    A("| schema 版本 | `%s` |" % ver)
    A("| 表 / 视图 | **%d / %d** |" % (len(tables), len(views)))
    A("| 字段合计 | **%d** |" % n_cols)
    A("| 企业 | **%d** 家 |" % n_ent)
    A("| 专利 | **%s** 件（按 `app_no` 去重） |" % format(n_pat, ","))
    A("| 训练集 | **%d** 行，断档样本 **%d** |" % (n_tr, n_pos))
    A("| 融资事件血缘 | **%d / %d** 行有 `source_id`（`zj_500family`） |"
      % (n_fund_src, n_fund))
    A("")
    A("## 怎么打开\n")
    A("**只读打开**（推荐，防止手滑改库）：")
    A("")
    A("```python")
    A("import sqlite3")
    A("con = sqlite3.connect('file:zjtech_credit.db?mode=ro', uri=True)")
    A("print(con.execute('SELECT COUNT(*) FROM v_model_train_ready').fetchone())")
    A("```")
    A("")
    A("命令行：`sqlite3 -readonly zjtech_credit.db`，或 DB Browser for SQLite 打开后勾只读。")
    A("")
    A("## 表清单（%d 张）\n" % len(tables))
    A("| 层 | 表 | 行数 | 说明 |")
    A("|---|---|---|---|")
    cur_layer = None
    for name in tables:
        lab, desc = layer_of(name)
        if lab != cur_layer:
            cur_layer = lab
        A("| %s | `%s` | %s | %s |"
          % (lab, name, format(rows[name], ","), NOTES.get(name, desc)))
    A("")
    A("## 视图（%d 个）\n" % len(views))
    A("| 视图 | 行数 | 说明 |")
    A("|---|---|---|")
    for name in views:
        A("| `%s` | %s | %s |" % (name, format(rows[name], ","),
                                  VIEW_NOTES.get(name, "—")))
    A("")
    A("## 四条使用红线\n")
    A("1. **训练只认 `v_model_train_ready`**。它已经挡掉三个坑：193 行假续存样本、"
      "伪重复（一家两行）、4 个常量特征。")
    A("2. **专利数用 `COUNT(DISTINCT app_no)`**。`COUNT(*)` 得 32,969，实际 30,103 件，虚高 9%。")
    A("3. **用 `ads_model_dataset` 前先过滤** `split != 'excluded'`，"
      "`excluded` 是窗口未闭合，不是续存样本。")
    A("4. **`ext_msme_sample` 不属于本项目**。那是外部对照组 1,707 行，别混进 500 家样本统计。")
    A("")
    A("字段逐列含义见上级目录 `04_数据字典与数据集/数据字典.md`（%d 列全覆盖）。" % n_cols)
    A("")
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    return {"path": SNAPSHOT, "mb": round(size_mb, 1), "secs": round(secs, 1),
            "tables": len(tables), "views": len(views), "cols": n_cols,
            "integrity": integrity, "fk_bad": fk_bad,
            "funding_src": (n_fund_src, n_fund)}


def build_release_zip() -> dict:
    """重打 Release 附件：04_数据字典与数据集 + 05_数据库 + MANIFEST.txt。

    复用 pack_delivery.pack()，MANIFEST 里每个文件带 sha256，接收方可校验。
    旧附件先本地留档为 zjtech_credit_data_v1.3.0.zip（add-new-no-overwrite），
    GitHub 上 v1.3.0 Release 的附件本身不会被这次打包改动。
    """
    from src.data.pack_delivery import pack as pack_parts

    if os.path.exists(RELEASE_ZIP) and not os.path.exists(RELEASE_ARCHIVE):
        shutil.copy2(RELEASE_ZIP, RELEASE_ARCHIVE)
    return pack_parts(os.path.basename(RELEASE_ZIP), RELEASE_PARTS)


if __name__ == "__main__":
    r = build()
    print("✅ 数据库快照 %(path)s" % r)
    print("   %(mb).1f MB / %(tables)d 表 + %(views)d 视图 / %(cols)d 列" % r)
    print("   integrity=%(integrity)s 外键冲突=%(fk_bad)d 耗时 %(secs).1fs" % r)
    print("   融资事件血缘 %(funding_src)s" % r)
    z = build_release_zip()
    print("📦 Release 附件 %(path)s（%(n)d 项 / %(mb).2f MB / testzip=%(bad)s）" % z)
