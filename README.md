# 融合技术要素与成长轨迹的中小科技企业信用智能评估方法构建

<!-- DIRECTION_NOTICE:v1 -->
> **方向声明（2026-11-09 纠正，v1.9）**：本项目研究企业好坏 / 信用评价，
> **正例 = 好企业 = 资质存续（286 家，`label_value=0`）**；
> **负例 = 坏企业 = 资质断档（21 家，`label_value=1`）**。
> `ads_label.label_value` 与 `v_model_train_ready.label` 是风险预警方向（1=断档），
> 为兼容已发布契约与历史 AUC 保留原数值，未原地 UPDATE；
> 正确方向的训练入口是 `v_model_train_ready_credit`，逐企业对照见 `v_label_direction`。
> 下文「断档样本」= 负例、「续存样本」= 正例；`n_positive` / `positive_rate`
> 等历史字段名仍指风险方向，不代表企业好坏口径的正例。
> 多标签测评见 `deliverables/01_算法端/multilabel_eval_report.md`。
<!-- /DIRECTION_NOTICE:v1 -->



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
│   ├── audit/                issue ① 证据快照（只读，21 家逐户表 / 287 家官方名单 / 7 条公告清单 / 更名标注 PNG）
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
├── tests/                    单元测试 / 集成测试（298 项守卫，26 文件）
│                             test_api_live.py（35 接口探活）
│                             test_contracts.py（契约数值回查 SQLite）
│                             test_train_ready.py / test_data_dictionary.py
│                             test_label_spec.py / test_patent_dedup.py
│                             test_grant_definition.py / test_field_gap_registry.py
│                             test_graph_v2.py（5 类节点 / 边陈旧 / 权重区分度）
│                             test_graph_id_docs.py（IPC ID 口径 / 字典枚举一致性 / 分队列 AUC）
│                             test_label_mechanism.py（n_events 泄漏 / 5 折断档样本下限）
│                             test_patent_master.py（专利唯一口径 30,103）
│                             test_schema.py / test_collect_pool.py / selfcheck.py
│
├── configs/                  配置文件
│                             enums.json（代码运行时权威源，load_entity_cn 直读）
│                             enums.yaml（人读审阅版，带注释与 meta）
│                             viz_theme.yaml / data_gaps.yaml / db_schema_manifest.json
│                             field_gap_registry.json（17 空字段 / 10 替身 / 4 陷阱）
│                             data_gaps.mirror.json（data_gaps.yaml 的类型化镜像）
│
└── _archive/                 归档区（不删除策略）
    ├── 2026-10-22/           第一批：根目录散落文件、旧版WebDemo、重复残件
    └── 2026-10-28/           第二批：前端旧目录、旧版答复文档
```

**测试文件范围（2026-11-11 更新）**：`tests/` 下 26 个 `test_*.py` **全部纳入交付守卫，
共 298 项全绿**。新增 `test_official_cancel_check.py`（10 项）守卫官方撤销公告证据链、
21 家逐户判定与口径隔离；`test_axis_a_judicial.py`（12 项）守卫 v2.0 A 轴三表四视图、
490 行表头脏行剔除、三类金额分列、复合事件候选与已发布标签未动、997 列字典与交付散文件同源；
`test_credit_direction.py`（20 项）守卫 P1-续 的 AUC 方向恒等式、
双方向打分对称性、分切面 / 分队列复核与重跑 SHAP；纯净检出缺 `outputs/` 产物时按项目
约定显式 SKIP，不误报失败。其中 `test_schema.py`（38 项）与 `test_collect_pool.py`（16 项）源自早期
A 套 27 表链路自测，已于 11-09 适配 B 套 v1.8：IPC 断言改为校验 A–H 八个部类齐全
（B 套 `dim_ipc` 是全量 11,616 行字典）、桥表改为按本次插入的 `patent_id` 定点校验、
覆盖度视图改为断言「基线 500 + 本次插入 1 家」；采集器补齐 `requirements.txt` 已声明的
`openpyxl>=3.1` 后 5 个夹具恢复为 9 家企业，XLSX 解析路径真实覆盖。
`test_collect_pool.py` 全程用临时库，与交付库相互独立；若环境缺 openpyxl，XLSX 项
显式记为 SKIP、端到端期望值按实际夹具数动态计算，既不静默算通过也不会误报失败。
详见 `deliverables/00_先看这个.md` 第五节。

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

启动后 `GET /docs` 可看 OpenAPI。共 **35 个接口**（v1.8 新增 `/api/data/datasets`、
`/api/enterprise/grade`），实测：

| 状态 | 数量 | 说明 |
|---|---|---|
| 有数据 | 33 | `code=0` 且返回真实数据 |
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
| 1 | 不要引用单一 AUC / 准确率 | 只有 21 个断档样本，`val` 集只有 2 个，只够验证方法论 |
| 2 | `score.value` 恒为 `null` 是设计 | 无可验证模型，只提供 `proxy_score`（`validated: false`），前端不得渲染成「信用分」 |
| 3 | 两套数据不要混看 | A 信贷面板（1,714 家匿名 / 3,465 行）与 B 浙江高企（500 家 / 307 打标）样本、特征、标签都不同，只是共用同一套 API |

### 6.4 四个已知坑

- 专利数必须 `COUNT(DISTINCT app_no)`。`COUNT(*)` 得 32,969 行，实际 **30,103 件**，虚高 9%。
  v1.4 起有唯一口径视图 `v_patent_master`（一行一个申请号，差额记在 `join_status` 里），
  对外报数直接 `SELECT COUNT(*) FROM v_patent_master`。
- `ads_model_dataset` 里 193 行 `split='excluded'` 是「认定窗口未闭合」，**不是续存样本**；
  当续存样本用会把断档样本占比从 6.8% 稀释到 4.2%。
- `n_events` 就是标签的定义式：认定 1 次的 21 家 100% 断档样本，≥2 次的 286 家 0% 断档样本。
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
| 时间切片 | 单切片（`time_slice=0`） | 物理表仍是全期单切片；v1.8 起年度切片走 `v_graph_slice_annual*` 两个视图 |

**边类型：CHECK 保留 8 个枚举位，本轮实际产出 5 类。** `co_inventor`（专利-专利共同发明人）
语义已由 `invented_by` 二跳覆盖，`cites` / `qualifies` 无源数据，三者均不产出——
不要按枚举位假设有这三种边。

重建命令：`python -m src.data.build_graph_v2`（幂等，可重复跑；`--dry-run` 只核对陈旧缺口）。
企业节点无孤立点（0 家无任何边），5 家企业没有技术相似边但都有专利边。

#### 年度切片（v1.8 已落库内视图，物理表仍是全期单切片）

评审报告 §5.3 原定「算法端自建」，v1.8 起把切片视图落进库，可直接查：

| 视图 | 内容 |
|---|---|
| `v_graph_patent_timeline` | 每个 T 的专利规模台账（v1.4 起有） |
| `v_graph_slice_annual` | 29 年 × 4 指标累计台账（116 行），`n_cumulative` 单调不减 |
| `v_graph_slice_annual_edge` | 边级年度切片，查询**必须**带 `WHERE time_slice = :T` |

```sql
SELECT * FROM v_graph_slice_annual      WHERE time_slice = 2021;  -- 该年累计规模
SELECT * FROM v_graph_slice_annual_edge WHERE time_slice = 2021;  -- 该年三类边明细
```

**物理表口径不变**：`ads_graph_node.time_slice` / `ads_graph_edge.time_slice` 仍全为 0
（全期累计，与既有契约绑定，且 `build_graph_v2` 幂等重建会写回 0）；
年度切片一律走视图，禁止 `UPDATE` 物理表。

自建时按 `fact_patent.app_year <= T` 取数，**不要从 `v_patent_master` 取**：
那是按申请号去重的主视图（30,103 件），其 `patent_id_repr` 与桥表 `patent_id`
只命中 42,079 / 51,795 条 IPC 边、66,660 / 77,899 条发明人边，会漏约 19%。
旧版这里的三条 SQL 用了并不存在的 `v_patent_master.patent_id` 列，v1.8 已修正：

```sql
-- T 年的企业→专利边
SELECT 'enterprise', p.enterprise_id, 'patent', p.patent_id, 'files_patent', :T, 1.0
  FROM fact_patent p
 WHERE p.enterprise_id IS NOT NULL AND p.app_year <= :T;

-- T 年的专利→IPC 边（IPC 节点 ID 需剥 WIPO 版本号后缀）
SELECT 'patent', p.patent_id, 'ipc',
       CASE WHEN instr(b.ipc_code,'(')>0
            THEN substr(b.ipc_code,1,instr(b.ipc_code,'(')-1) ELSE b.ipc_code END,
       'belongs_ipc', :T, CASE WHEN b.is_main THEN 1.0 ELSE 0.5 END
  FROM fact_patent p JOIN bridge_patent_ipc b ON b.patent_id = p.patent_id
 WHERE p.app_year <= :T;

-- T 年的专利→发明人边
SELECT 'patent', p.patent_id, 'inventor', i.inventor_id, 'invented_by', :T,
       CASE WHEN i.is_first THEN 1.0 ELSE 0.5 END
  FROM fact_patent p JOIN bridge_patent_inventor i ON i.patent_id = p.patent_id
 WHERE p.app_year <= :T;
```

末年累计对账：`n_edge_files_patent` 32,203、`n_edge_invented_by` 77,899 与
`ads_graph_edge` 逐类型一致；`n_edge_belongs_ipc` 51,795 与预置图 51,776
差 19 条，来自 IPC 版本号归一后的并边，不是漏数。

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

### 6.5.2 融资特征 as-of 补算（v1.6 新增）

评审报告 §2.3(3) 明说「融资维度可做『有没有融过、融了几轮』」，但 as-of 层此前把
`m_funding` / `investor_prestige` 整列置 NULL，登记理由「本地无时间维」「本地无数据」
与事实不符（`event_year` 506/506 全填、`prestige_rank` 352/506 有值）。已补算：

```sql
SELECT * FROM v_feature_asof_funding;   -- 614 行 = 307 家 × 2 口径
```

| 特征 | 口径 | cohort 覆盖 |
|---|---|---|
| `m_funding` | 窗口内（event_year ≤ as_of）有融资事件 0/1 | 60/307 家 |
| `n_funding_events` | 窗口内融资次数（「融了几轮」） | 121 次 / 60 家 |
| `investor_prestige_best/_latest` | 窗口内最好/最近机构等级（1=头部 VC…4=个人天使） | 59/307 家 |

**必须知道的限制**：覆盖是 MNAR（能融资的企业本身更活跃），不是随机子集；
不进 `v_model_train_ready`（那是 17 列既有口径），补算命令
`python -m src.data.build_asof_funding`（幂等）。

**接入口径（v1.7 固化，守卫 `tests/test_funding_join_contract.py`）**：
`v_model_dataset_asof_flat` 与 `dataset_asof_flat.csv` 里的
`m_funding` / `investor_prestige` **两列恒 NULL** —— 这是设计，不是漏补；
按评审报告 §4.1 数「零覆盖特征」仍会得到 14 个。要用融资特征必须按
`(enterprise_id, as_of_policy)` **双键** `LEFT JOIN v_feature_asof_funding`：

```sql
SELECT d.*, f.m_funding AS m_funding_asof, f.n_funding_events,
       f.investor_prestige_best, f.investor_prestige_latest
FROM v_model_dataset_asof_flat d
LEFT JOIN v_feature_asof_funding f
  ON  f.enterprise_id = d.enterprise_id
  AND f.as_of_policy  = d.as_of_policy;
```

只按 `enterprise_id` 单键连接会把 614 行撑成 1,228 行伪重复。
主宽表 29 列结构与 `ASOF_VER`（特征口径版本）绑定，**禁止原地 UPDATE** 回写补算值。
交付说明 `deliverables/00_先看这个.md` 与数据字典同步写明了这条契约；
两者按 `.gitignore` 约定不进 Git，随 Release 附件分发。

### 6.5.3 企业技术与资质梯队分档 L0（v1.7 新增）

方案见 `docs/论文/企业分档多标签体系方案-v1.0.md`。视图 `v_enterprise_grade_l0`
把 500 家企业按字典序分层规则划成五档：E 预警 → D 观察 → A 引领 → B 优质 → C 稳健，
实测 **3 / 83 / 44 / 95 / 275** 家（合计 500）。每行带 `hit_rule` 与 8 个判据中间量，
可逐企业审计；`grade_ver = l0_v1.0`，判据或口径变更必须升版。

```sql
SELECT grade, COUNT(*) FROM v_enterprise_grade_l0 GROUP BY grade;
```

三条使用边界：① 这是**静态快照对照基线**，不是 as-of 训练口径，不得入模验证；
② A/B 档判据用了 `qcc_tech_level_ord` / `qcc_credit_level_ord`，禁止用同字段反向验证（循环定义）；
③ `admin_penalty_cnt` 语义与字段名不符，已排除；`label_value_qual_survival = -1`
的 193 家未打标企业不是续存样本，不参与 D 档判定。

升版命令 `python -m src.data.apply_v1_7`（幂等，执行前自动留 `data/db/zjtech_credit_pre_v17.db`
回滚点），守卫 `python tests/test_enterprise_grade_l0.py`（11 项，含与方案原文 SQL 逐企业独立对账）。
另外 3 个空 `label_task`（标签任务）已在 `configs/field_gap_registry.json` 的 `label_tasks` 段
登记为「源未接入」，as-of 切面口径约定固化在同文件 `policies.as_of`。

### 6.5.4 口径一致性与 A/B 数据隔离（v1.8 新增）

**有效特征覆盖**：`v_asof_feature_coverage` 只扫 `ads_feature_asof`，把
`m_funding` / `investor_prestige` 报成 0 覆盖；真实值在旁路表
`ads_feature_asof_funding`。新增 `v_feature_coverage_effective` 按
`(as_of_policy, feature_name)` 把这两列替换为旁路表实测，并标
`coverage_source` / `requires_left_join`。cohort 口径零覆盖特征 **14 → 12**，
其余 12 个真无数据源，一个都不补。原视图保持不动，`ASOF_VER` 与主宽表
43 列结构不变，取数仍按 §6.5.2 的双键 `LEFT JOIN`。

```sql
SELECT * FROM v_feature_coverage_effective
 WHERE as_of_policy='cohort'
   AND feature_name IN ('m_funding','investor_prestige');
```

**A/B 两套数据隔离**：`/api/data/pipeline?dataset=A_credit_panel|B_proxy_label_500`
只返回对应数据块，默认 `all` 保持向后兼容；`/api/data/datasets` 给两套数据的登记台账。
任何取值都带 `meta.allow_cross_join=false`。A 是 10 月信贷面板
（1,714 家 / 匿名 x1–x200 / 75 个违约样本），B 是本项目 500 家高企（资质存续弱标签），
禁止跨集合并排序或共用同一 AUC 坐标。`/api/model/benchmark` 标
`dataset_scope=A_credit_panel`，`/api/factors/importance` 标
`mixed_in_one_response=true`（同一响应内并列展示，仍不得合并排序）。

**分档端点**：`/api/enterprise/grade` 返回 `v_enterprise_grade_l0` 实测五档，
契约 `outputs/contracts/enterprise_grade.json` 带 `usage_boundary`，
明确不是标签、不是信用分、不得报准确率、不得入模验证。

升版命令 `python -m src.data.apply_v1_8`（幂等，执行前自动留
`data/db/zjtech_credit_pre_v18.db` 回滚点，`--check` 只读校验）；
守卫 `python tests/test_v1_8_views.py`（8 项）与
`python tests/test_dataset_isolation.py`（8 项）。

### 6.6 数据获取

数据库 153 MB（当前工作库 **v2.0：37 表 / 33 视图 / 997 列**；VACUUM 交付快照 131 MB），含 500 家真实企业明细，不入 Git 历史，走 **[Releases](../../releases/latest)** 附件：

```bash
curl -L -O https://github.com/JiWu-hyw/sme-credit-eval/releases/latest/download/zjtech_credit_data.zip
unzip zjtech_credit_data.zip -d .          # 得到 05_数据库/zjtech_credit.db
mkdir -p data/db && cp 05_数据库/zjtech_credit.db data/db/
```

附件里还带数据字典（md + csv）、代理标签说明书、字段缺口登记册、3 个数据集 CSV。
只想建空库不需要数据时，用 `src/data/schema.sql`。

**版本边界**：v1.3.0 Release 附件是 **v1.6 快照**（34 表 / 19 视图 / 804 列）；
**v1.4.0（已发布，保持不变）** 是 **v1.8 快照**（34 表 / 23 视图 / 837 列），
附件含 837 列字典、3 个数据集 CSV、标签说明书、缺口登记册、VACUUM 库快照与
`MANIFEST.txt` 的 sha256 清单，说明见 `deliverables/RELEASE_v1.4.0.md`。
**本地工作库版本沿革**：v1.9（34 表 / 29 视图 / 912 列）新增 6 个视图做标签方向纠正
与多标签测评（P1，零采集），未改任何事实表数据、未改主宽表 29 特征结构；
**v2.0（当前，37 表 / 33 视图 / 997 列）** 接入企查查回传的失信被执行人 / 被执行人 /
终本案件 / 主要人员，新增 `ods_qcc_judicial`（34 条 / 6 户）、`ods_qcc_person`
（清洗后 2079 人 / 490 户）、`ods_qcc_axis_a_snapshot`（500 行）三表与
`v_axis_a_judicial` / `v_enterprise_person_summary` / `v_axis_a_compound_event` /
`v_axis_a_grade_cross` 四视图；数据字典 912 → 997 列同步重生成，报告见
`deliverables/01_算法端/axis_a_judicial_report.md`。复合负面事件 6 家（3 家新增 E 候选）
是**并列候选锚点**，`ads_label`、多标签五档、训练入口、`DEFINITION_VER` 逐项未改。
算法端要拿到新库需另发 **v1.5.0** Release（附件同名 `zjtech_credit_data.zip`，
**不覆盖 v1.4.0**），说明见 `deliverables/RELEASE_v1.5.0.md`。
历史：v1.7 新增五档视图与标签任务登记；v1.8 新增 3 个口径一致性视图、分档端点与 A/B 隔离。

> **打包节奏（约定）**：`deliverables/` 里的**散文件随时整合到最新**——目前 01/03/04/05
> 目录与数据字典、说明书、报告、数据库一致性快照均已同步到 v2.0；
> **zip 与上传冻结**：9 个历史 zip 与 `zjtech_credit_data.zip` 未重打、SHA-256 未变，
> GitHub Release 附件仍是 v1.9 快照。待全部改完确认后，才一次性重打包并上传。

**2026-11-09 晚间修订（仍记 v1.9，不升版本）**：两个年度切片视图 `v_graph_slice_annual*`
改为以 `ads_graph_edge` 为唯一边源，修掉旧定义与物理图 IPC 边差 19 条的同源 bug；
新增 `idx_gedge_dst_typ` / `idx_gedge_src_typ` 两个查询索引并用 `CROSS JOIN + INDEXED BY`
固定连接顺序（全表 `COUNT(*)` 847,677 行约 0.9s，改回普通 JOIN 会劣化到 >170s）。
**未 UPDATE 任何底表行、未增删表/视图/列**，仍是 34 表 / 29 视图 / 912 列。
配套产物 `outputs/graph_temporal_contract.md`（STGNN 输入契约）、
`outputs/field_gap_decisions.md`（17 条字段级 NULL 定稿）与
`outputs/shap_legacy_notice.md`（前端 mock 契约方向警告），已整合进 `01_算法端/`。
A7 另把 `fact_funding_event.source_id` 从 0/506 回填为 506/506 `zj_500family`——
**只补行级血缘列**，金额 / 估值等业务列用 SHA-256 前后指纹对账未变，
schema 结构未改、版本仍 v1.8；变更凭证 `outputs/funding_provenance_backfill.json`，
幂等脚本 `python -m src.data.apply_funding_provenance`。
新旧两版各自独立 Release，**v1.4.0 不覆盖 v1.3.0 附件**；旧附件本地留档为
`deliverables/zjtech_credit_data_v1.3.0.zip`。全量交付包另见
`wenlv_full_delivery_20261109.zip`（40 文件）/ `wenlv_delivery_20261109.zip`（38 文件）；
11-07 的两个旧包本地保留，不覆盖。

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
