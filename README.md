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
│   ├── data/                 模块一：采集与预处理管道 + 数据层导出
│   │                         collect_pool.py / preprocess.py / db.py
│   │                         schema.sql / load_scibert.py
│   │                         load_500family.py / build_asof_features.py
│   │                         build_qual_dataset.py / build_contracts.py   ← 契约生成
│   │                         export_data_dictionary.py / export_label_spec.py
│   │                         export_field_gap_registry.py / export_delivery.py
│   │                         export_db_bundle.py / pack_delivery.py
│   ├── graph/                模块二：时空演化图谱构建
│   │                         panel_graph.py / synthetic.py
│   ├── model/                模块三 + 五：STGNN / DSTGNN / 预测
│   │                         stgnn.py / gat_panel.py / train_demo.py
│   │                         baseline.py / metrics.py
│   ├── explain/              模块四：可解释性分析
│   │                         global_shap.py
│   └── app/                  原型系统
│       ├── backend/          后端 API：api.py / run_api.py
│       │                     db_views.py（只读数据视图层）
│       │                     import_validate.py（导入字段级校验）
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
│                             contracts/   ← 11 个接口契约，API 运行时直读
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
├── tests/                    单元测试 / 集成测试（96 项守卫）
│                             test_api_live.py（33 接口探活）
│                             test_contracts.py（契约数值回查 SQLite）
│                             test_train_ready.py / test_data_dictionary.py
│                             test_label_spec.py / test_patent_dedup.py
│                             test_grant_definition.py / test_field_gap_registry.py
│                             test_graph_v2.py（5 类节点 / 边陈旧 / 权重区分度）
│                             test_graph_id_docs.py（IPC ID 口径 / 字典枚举一致性 / 分队列 AUC）
│                             test_label_mechanism.py（n_events 泄漏 / 5 折正例下限）
│                             test_patent_master.py（专利唯一口径 30,103）
│                             test_schema.py / test_collect_pool.py / selfcheck.py
│
├── configs/                  配置文件
│                             enums.json（代码运行时权威源，load_entity_cn 直读）
│                             enums.yaml（人读审阅版，带注释与 meta）
│                             viz_theme.yaml / data_gaps.yaml / db_schema_manifest.json
│                             field_gap_registry.json（17 空字段 / 7 替身 / 4 陷阱）
│                             data_gaps.mirror.json（data_gaps.yaml 的类型化镜像）
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
- 后端启动：`python -m src.app.backend.run_api --port 8000`
  （**必须用 `-m`**：`run_api.py` 里 `uvicorn.run("src.app.backend.api:app")` 要按模块路径解析，
  直接 `python src/app/backend/run_api.py` 会因 `sys.path[0]` 变成该子目录而导入失败）

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

## 六、接口与数据契约

### 6.1 接口现状

启动后 `GET /docs` 可看 OpenAPI。共 **33 个接口**，实测：

| 状态 | 数量 | 说明 |
|---|---|---|
| 有数据 | 31 | `code=0` 且返回真实数据 |
| 需带文件 | 2 | `batch/upload`、`data/import`，不带文件返回 `IMPORT_REJECTED` 是正确行为 |
| 空壳 / 占位 / 报错 | 0 | — |

> **「空壳」的定义**：HTTP 200、`code:0`、`data` 非 null，但内部字段全是 null。
> 只看 `code` 会以为可用，实际渲染是空白。此前 `company/diagnose` 就是这样。

### 6.2 契约文件

`outputs/contracts/` 下 11 个 JSON，是前端可以直接当 mock 的数据源。

**每个文件顶层强制带 `provenance`，缺了这个字段生成脚本会直接抛错拒绝写出。** 等级决定展示口径：

| `provenance.kind` | 展示方式 | 本轮哪些 |
|---|---|---|
| `measured` | 直接画图、可引用数值 | data_quality、panel_depth、graph_structure、graph_slices、survival |
| `derived` | 同上，数值由实测算出 | feature_tree、graph_centrality |
| `partial` | 画已知部分，`missing` 列出缺哪些 | train_curve、stage_factor、financing |
| `reconstructed` | 画曲线，但必须注明「由锚点重建」 | roc_pr_ks |

`explain/local/{userid}` 不预生成文件，按企业实时算（复用 `proxy_score` 的贡献分解，
带 `model_based=false` / `is_shap=false`）。

### 6.3 三条口径红线（引用数据前必读）

| # | 红线 | 为什么 |
|---|---|---|
| 1 | 不要引用单一 AUC / 准确率 | 只有 21 个正例，`val` 集只有 2 个，只够验证方法论 |
| 2 | `score.value` 恒为 `null` 是设计 | 无可验证模型，只提供 `proxy_score`（`validated: false`），前端不得渲染成「信用分」 |
| 3 | 两套数据不要混看 | A 信贷面板（1,714 家匿名 / 3,465 行）与 B 浙江高企（500 家 / 307 打标）样本、特征、标签都不同，只是共用同一套 API |

### 6.4 四个已知坑

- 专利数必须 `COUNT(DISTINCT app_no)`。`COUNT(*)` 得 32,969 行，实际 **30,103 件**，虚高 9%。
  v1.4 起有唯一口径视图 `v_patent_master`（一行一个申请号，差额记在 `join_status` 里），
  对外报数直接 `SELECT COUNT(*) FROM v_patent_master`。
- `ads_model_dataset` 里 193 行 `split='excluded'` 是「认定窗口未闭合」，**不是负例**；
  当负例用会把正例率从 6.8% 稀释到 4.2%。
- `n_events` 就是标签的定义式：认定 1 次的 21 家 100% 正例，≥2 次的 286 家 0% 正例。
  **它绝不可进特征**（`tests/test_label_mechanism.py` 会拦），台账见 `v_label_mechanism`。
- 观察窗起点固定 2019，2021 队列只有 3 年、2022 队列有 4 年。跨队列比专利数前先看
  `v_train_ready_window_fixed`——统一压到 3 年后，队列均值差从 7.6 件降到 4.2 件。

### 6.5 图谱口径（v1.4 重建 / v1.5 修 IPC 口径）

| 项 | v1.3 | 现在 |
|---|---|---|
| 节点类型 | 3 类（企业 / 专利 / IPC） | **5 类**（+ 发明人 15,955 / 投资机构 572） |
| 节点 / 边 | 45,077 / 101,491 | **61,584 / 171,210** |
| 边语义 | files_patent / belongs_ipc / tech_similar | + **invented_by**（77,899）/ **funded_by**（583） |
| `files_patent` | 29,751（**陈旧，漏 2,452 条**） | 32,203，与已匹配专利逐条对账，缺口 0 |
| `tech_similar` | Jaccard 全量连边 19,945 条，权重中位 0.138、≥0.3 仅 663 条 | TF-IDF 余弦 + top-20，8,749 条，权重中位 **0.268**、p90 **0.500**、≥0.3 **3,574** 条；覆盖企业 294 → **490** |
| IPC 节点 ID | 混入 20 个带 WIPO 版本号后缀的串（`A61K31/426(2006.01)`），同一分类分裂成两个节点 | 已归一，31 条边并入无后缀节点；节点 11,608 → **11,588** |
| 时间切片 | 单切片（`time_slice=0`） | 预置图仍是全期单切片；按下方 SQL 自建年度切片 |

**边类型：CHECK 保留 8 个枚举位，本轮实际产出 5 类。** `co_inventor`（专利-专利共同发明人）
语义已由 `invented_by` 二跳覆盖，`cites` / `qualifies` 无源数据，三者均不产出——
不要按枚举位假设有这三种边。

重建命令：`python -m src.data.build_graph_v2`（幂等，可重复跑；`--dry-run` 只核对陈旧缺口）。
企业节点无孤立点（0 家无任何边），5 家企业没有技术相似边但都有专利边。

#### 自建年度切片（评审报告 §5.3 明确由算法端做，后端不返工）

`v_graph_patent_timeline` 给出每个 T 的规模台账，切片本身按 `app_year <= T` 现场取：

```sql
-- T 年的企业→专利边（files_patent 的切片版）
SELECT 'enterprise' AS src_type, p.enterprise_id AS src_id,
       'patent' AS dst_type, p.patent_id AS dst_id,
       'files_patent' AS edge_type, :T AS time_slice, 1.0 AS weight
  FROM v_patent_master p
 WHERE p.enterprise_id IS NOT NULL
   AND p.app_year <= :T;

-- T 年的专利→IPC 边（经专利间接切片，IPC 节点随之自然收敛）
SELECT 'patent', b.patent_id, 'ipc', b.ipc_code, 'belongs_ipc', :T,
       CASE WHEN b.is_main THEN 1.0 ELSE 0.5 END
  FROM bridge_patent_ipc b
  JOIN v_patent_master p ON p.patent_id = b.patent_id
 WHERE p.app_year <= :T;

-- T 年的专利→发明人边
SELECT 'patent', i.patent_id, 'inventor', i.inventor_id, 'invented_by', :T,
       CASE WHEN i.is_first THEN 1.0 ELSE 0.5 END
  FROM bridge_patent_inventor i
  JOIN v_patent_master p ON p.patent_id = i.patent_id
 WHERE p.app_year <= :T;
```

两条口径提醒：① IPC 节点 ID 已剥版本号后缀，自建时若从 `bridge_patent_ipc.ipc_code`
直接取，需自行做同样的 `regexp_replace` 式清洗，否则会和预置图节点对不上；
② 专利节点 ID 用 `fact_patent.patent_id`（中国专利为 CN 公开号，另有 4 件 JP/TW 同族），
**不是** `app_no`——两者在库内不等值。

立项书写的六类实体里仍差「领域」「产品」：领域可由 IPC 部/大类等价替代（已在图内），
产品在现有源数据中**完全没有**，登记为缺口，不做任何推断回填。

### 6.5.1 特征效应的分队列口径（v1.5 新增）

评审报告 §3.3 / §4.2 的结论是「单一特征的全样本 AUC 混入了队列效应，不能直接当证据」。
这条已固化成视图，引用任何特征效应前先过一遍：

```sql
SELECT feature_name, cohort_scope, n_pos, auc, stability_note
  FROM v_feature_discrimination          -- 一行 = 一个特征 × (all/2021/2022)
 ORDER BY feature_name, cohort_scope;

SELECT * FROM v_feature_cohort_gap;      -- 只列队列间差距 ≥0.20 的，带 verdict
```

实测：`inv_patent_ratio` 全样本 0.619，拆开是 2021 队列 **0.784** / 2022 队列 **0.503**；
`core_staff_stability` 全样本 0.523，拆开是 **0.654 / 0.404**——后者报告没列，但同样是
「全样本看起来没用、某队列里很强」的类型。这类特征的全样本 AUC 不得单独引用。

### 6.6 数据获取

数据库 153 MB（33 表 / 18 视图 / 788 列；VACUUM 快照 120 MB），含 500 家真实企业明细，不入 Git 历史，走 **[Releases](../../releases/latest)** 附件：

```bash
curl -L -O https://github.com/JiWu-hyw/sme-credit-eval/releases/latest/download/zjtech_credit_data.zip
unzip zjtech_credit_data.zip -d .          # 得到 05_数据库/zjtech_credit.db
mkdir -p data/db && cp 05_数据库/zjtech_credit.db data/db/
```

附件里还带数据字典（788 列 md + csv）、代理标签说明书、字段缺口登记册、3 个数据集 CSV。
只想建空库不需要数据时，用 `src/data/schema.sql`。

## 七、归档规则

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

## 八、协作分工

分工口径以 `docs/计划/项目指令更新-成员角色提示词.md` 为准。
`README` 旧版中的分工表存在两个互相矛盾的版本，已统一到 2026-10-13 调整后的口径。

---

## 九、待办

- [x] ~~前端第二批迁移：`dist/` 构建产物 + 70 张预览截图~~ → 已完成，实际 **66 张**（8 个增量包，p1–p8，包间无重名，取并集）
- [x] ~~核对 `src/app/backend/` 中 `export_dashboard.py`、`local_explain.py` 是否需合入归档的 `(1)` 版本逻辑~~ → 已完成：`(1)` 版为**后续修订版**（非残件），已覆盖正式文件；`enums.json` 同步回迁 `configs/`
- [ ] 补齐 `docs/周报/`、`docs/政策建议/` 内容
- [ ] `notebooks/` 补入探索性分析记录
- [ ] `src/app/frontend/` 空目录待定：源码在 zip 内未展开，若需网盘内直接浏览单文件需拆包上传
