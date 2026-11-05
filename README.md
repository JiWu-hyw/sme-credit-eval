# 融合技术要素与成长轨迹的中小科技企业信用智能评估方法构建

国家级大学生创新创业训练计划项目 · 项目资产库

> 面向浙江省中小科技企业，构建「技术要素 + 成长轨迹」双驱动的信用智能评估方法。
> 技术路线：数据采集与预处理 → 时空演化图谱 → STGNN / DSTGNN 建模 → SHAP 可解释性 → 可视化原型系统。

---

## 一、目录结构

```
.
├── README.md                 本文件
├── requirements.txt          Python 依赖
├── gitignore.txt             .gitignore 内容（下载后重命名为 .gitignore 使用）
│
├── data/                     数据资产
│   ├── raw/                  原始采集数据（企查查/知识产权局/竞赛授权数据集）
│   │                         浙江2021高新认定第一批公示名单.pdf 等 3 份
│   ├── processed/            清洗、标准化后的特征表
│   │                         2021_first_batch.csv / 2021_recognized_pool.csv
│   │                         2021_supplementary.csv / 2024_recheck.csv
│   ├── db/                   SQLite 数据库（zjtech_credit.db / _v2.db）
│   └── 浙江省500家专精特新中小企业/    500 家样本名单（xlsx，外部授权）
│
├── src/                      代码
│   ├── data/                 模块一：采集与预处理管道
│   │                         collect_pool.py / preprocess.py / db.py
│   │                         schema.sql / load_scibert.py
│   ├── graph/                模块二：时空演化图谱构建
│   │                         panel_graph.py / synthetic.py
│   ├── model/                模块三 + 五：STGNN / DSTGNN / 预测
│   │                         stgnn.py / gat_panel.py / train_demo.py
│   │                         baseline.py / metrics.py
│   ├── explain/              模块四：可解释性分析
│   │                         global_shap.py
│   └── app/                  原型系统
│       ├── backend/          后端 API：api.py / run_api.py
│       │                     export_dashboard.py / local_explain.py
│       └── frontend/         前端可视化（Vue 3 + Vite + TS）
│           └── frontend-source-2026-10-28.zip   ← 合并后的完整源码
│
├── scripts/                  模块一的实操采集脚本（独立于 src/data 的库代码）
│                             collect_pool.py / cnipa.py / match_patents.py
│                             pdf_to_csv.py / build_tech_features.py
│                             build_enums.py / build_viz_theme.py
│
├── outputs/                  实验结果与图表产物（模块三/四的证据链）
│                             baseline_results.json / gat_panel_results.json
│                             shap_global.json / shap_top_features.csv
│                             shap_global_summary.png
│
├── notebooks/                探索性分析与实验记录（当前待补充）
│
├── docs/
│   ├── 论文/                 方法与实验文档（7 份）
│   │                         数据管道架构设计 / 数据字典-标准数据库
│   │                         特征工程字典 / 实验结果报告-基线SHAP与GAT 等
│   ├── 软著/                 软件著作权材料
│   │                         可视化清单与图表数据契约.docx
│   │                         前端页面与交互原型说明.docx
│   │                         交付档案/  ← FE-04 ~ FE-20 共 8 版交付包及说明
│   ├── 政策建议/             政策建议摘要（待补充）
│   ├── 周报/                 每周进展简报（待补充）
│   ├── 计划/                 任务看板、甘特图、环境配置、待确认问题（13 份）
│   ├── 中期检查/             中期检查材料
│   └── 模板/                 立项/结题申报模板
│
├── tests/                    单元测试 / 集成测试
│                             test_schema.py / test_collect_pool.py / selfcheck.py
│
├── configs/                  配置文件
│                             enums.json（代码运行时权威源，load_entity_cn 直读）
│                             enums.yaml（人读审阅版，带注释与 meta）
│                             viz_theme.yaml / data_gaps.yaml / db_schema_manifest.json
│
└── _archive/                 归档区（不删除策略）
    ├── 2026-10-22/           第一批：根目录散落文件、旧版WebDemo、重复残件
    └── 2026-10-28/           第二批：前端旧目录、旧版答复文档
```

---

## 二、模块与目录对照

项目分五个研究模块，代码分散在 `src/`、`scripts/`、`outputs/` 三处，对应关系如下：

| 模块 | 内容 | 代码位置 | 产出位置 |
|---|---|---|---|
| 一 | 采集与预处理管道 | `src/data/`（库）<br>`scripts/`（执行脚本） | `data/raw/` → `data/processed/` |
| 二 | 时空演化图谱构建 | `src/graph/` | — |
| 三 | STGNN / DSTGNN 建模 | `src/model/` | `outputs/*_results.json` |
| 四 | 可解释性分析（SHAP） | `src/explain/` | `outputs/shap_*` |
| 五 | 预测与原型系统 | `src/model/`<br>`src/app/backend/`<br>`src/app/frontend/` | `src/app/frontend/dist/` |

> **为什么 `scripts/` 和 `outputs/` 不在 src 下？**
> `scripts/` 里的 7 个脚本是**实际跑数据**的命令行工具（下载公示名单 PDF、调 CNIPA 接口、匹配专利），与 `src/data/` 里可被 import 的库代码职责不同，混放会让人分不清「哪些能 import、哪些要 `python 跑`」。
> `outputs/` 里的 JSON/CSV/PNG 是**可复现性证据**，不属于源码，也不属于文档。

---

## 三、环境与依赖

```bash
# Python 端
pip install -r requirements.txt

# 前端（解压后）
cd src/app/frontend && unzip frontend-source-2026-10-28.zip -d . \
  && npm install && npm run dev
```

**关键配置**

- Python 版本：见 `requirements.txt`
- 前端栈：Vue 3.5 + Vite 6 + TypeScript(strict) + Pinia + Vue Router + ECharts
- 路径别名：`@ → ./src`，已在 `vite.config.ts`（`resolve.alias`）与 `tsconfig.json`（`paths`）**双处配置**
- 后端启动：`python src/app/backend/run_api.py`

---

## 四、前端源码说明

`src/app/frontend/frontend-source-2026-10-28.zip` 是**合并后的唯一权威源码包**（175 个文件，784 KB）。

**合并来源**：FE-15（全量，P6）打底 → FE-20（全量最新，P8）覆盖 → 补 3 个本地独有文件（`eslint.config.js` / `CONTRIBUTING.md` / `DOTFILES.md`）。

**已排除**（属构建产物，不重复入库）：`dist/`、`docs/preview/` 截图、`node_modules/`。

**历史交付包**：FE-04 / FE-11 / FE-12 / FE-13 / FE-14 / FE-15 / FE-16 / FE-20 及其交付说明，全部保留在 `docs/软著/交付档案/`，作为迭代过程证据。

---

## 五、数据层约定

| 层级 | 内容 | 是否入库 |
|---|---|---|
| `data/raw/` | 官方公示名单 PDF 原件，只读不改 | 不入库（体积大，含敏感信息） |
| `data/processed/` | 解析后的 CSV，可重复生成 | 视情况 |
| `data/db/` | SQLite 全文库 | 不入库 |

**字段口径与枚举取值**采用双版本：`configs/enums.json` 是代码运行时权威源，`export_dashboard.py` 的 `load_entity_cn()` 直接读取它，缺失会导致脚本无法运行；`configs/enums.yaml` 是人读审阅版，带注释与 meta，供人工核对。**两者需同步修改**，改一处必须改另一处。
**数据缺口登记**见 `configs/data_gaps.yaml`。

---

## 六、归档规则

**原则：归档不删除。** 所有被取代的文件移入 `_archive/<日期>/`，并在同批次目录下留 `RESTORE.md` 记录：

- 每个文件的 `file_id` 与原始父目录
- 归档理由（被谁取代 / 重复 / 误判修正）
- 回滚操作步骤

当前批次：

| 批次 | 账本 | 主要内容 |
|---|---|---|
| 2026-10-22 | `_archive/2026-10-22/RESTORE.md` | 根目录散落文件、注意事项、旧版WebDemo、重复残件 |
| 2026-10-28 | `_archive/2026-10-28/RESTORE.md` | 前端旧目录、旧版答复文档 v1.0/v1.1（另见该台账 §十 的纠错记录） |

---

## 七、协作分工

分工口径以 `docs/计划/项目指令更新-成员角色提示词.md` 为准。
`README` 旧版中的分工表存在两个互相矛盾的版本，已统一到 2026-10-13 调整后的口径。

---

## 八、待办

- [x] ~~前端第二批迁移：`dist/` 构建产物 + 70 张预览截图~~ → 已完成，实际 **66 张**（8 个增量包，p1–p8，包间无重名，取并集）
- [x] ~~核对 `src/app/backend/` 中 `export_dashboard.py`、`local_explain.py` 是否需合入归档的 `(1)` 版本逻辑~~ → 已完成：`(1)` 版为**后续修订版**（非残件），已覆盖正式文件；`enums.json` 同步回迁 `configs/`
- [ ] 补齐 `docs/周报/`、`docs/政策建议/` 内容
- [ ] `notebooks/` 补入探索性分析记录
- [ ] `src/app/frontend/` 空目录待定：源码在 zip 内未展开，若需网盘内直接浏览单文件需拆包上传
