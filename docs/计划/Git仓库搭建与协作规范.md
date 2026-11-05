# Git 仓库搭建与协作规范（T00 落地版）

| 项    | 内容                              |
| ---- | ------------------------------- |
| 负责人  | 吕键（后端，牵头代码仓库）                   |
| 平台   | **GitHub**（2026-10-14 确定）       |
| 版本   | v1.2                            |
| 日期   | 2026-10-16                      |
| 配套文件 | `.gitignore`（同目录，可直接使用）         |
| 关联文档 | 《后端工作总览_v1.0》§8 任务时间线 / §3 云端盘点 |

---

## 1. 仓库基本信息

| 项            | 建议值                                                                          | 说明                                                       |
| ------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------- |
| 仓库名          | `sme-credit-eval`                                                            | 备选：`tech-sme-credit` / `credit-assessment-stgnn`；确定前全组通报 |
| 可见性          | **Private**                                                                  | 含未公开数据口径与代码，软著登记前保密；软著受理后可评估转 Public                     |
| 描述           | 融合技术要素与成长轨迹的可解释 STGNN 中小科技企业信用评估                                             | —                                                        |
| 默认分支         | `main`                                                                       | `git init -b main` 直接建主干                                 |
| 选 GitHub 的收益 | ① commit 历史 + PR 记录是**软著创作过程的直接佐证**；② Actions 可跑自动化测试；③ Issues 可做任务跟踪（与看板互补） | —                                                        |

---

## 2. 创建步骤（约 15 分钟）

### 2.1 网页端建空仓库

1. GitHub → 右上 **+** → **New repository**；
2. Owner 选谁？（建议用**负责人/导师账号**建，你是协作者——避免人走 repo 走；若你自己账号建，软著材料要能解释权属）；
3. **不要**勾选 "Add a README" / "Add .gitignore"（保持空仓库，本地首次推送带全部内容）；
4. 创建后复制仓库地址：`https://github.com/<owner>/sme-credit-eval.git`。

### 2.2 本地初始化与首次推送

在**已汇总好代码的本地目录**执行（当前代码在 tdrive，先把 `src/ frontend/ web/ docs/ outputs/ configs/ tests/ notebooks/` + 根目录文件下载汇总到一个本地文件夹，数据文件不要拉）：

```bash
cd /path/to/sme-credit-eval      # 代码汇总目录
git init -b main
git remote add origin https://github.com/<owner>/sme-credit-eval.git

# 把 .gitignore 先就位（配套文件），再添加
git add .
git status                        # 核对：不应出现 .parquet/.xlsx/.pt/.venv
git commit -m "chore: 初始化仓库，迁入 src/web/docs/outputs 等既有代码与文档"
git push -u origin main
```

### 2.3 邀请协作者

Settings → Collaborators → **Add people**，需要三人的 GitHub 用户名：

| 成员  | 角色    | 权限建议  |
| --- | ----- | ----- |
| 施汶邑 | 算法    | Write |
| 刘钰泉 | 前端    | Write |
| 袁雪茹 | 数据/文档 | Write |

> ⚠️ **欠缺材料：三人的 GitHub 用户名**（或邮箱）——建群接龙收集，这是邀请前唯一要准备的东西。

### 2.4 分支保护（防误推 main）

Settings → Branches → Add rule：`main` 勾选 **Require a pull request before merging** + **Require approvals: 1**。三人小团队可放宽为"允许自己合并自己的 PR"，但保留 PR 记录（软著证据链）。

---

## 3. 仓库目录结构（承接 tdrive 现状，不折腾）

```
sme-credit-eval/
├── src/
│   ├── data/            # preprocess.py / load_scibert.py
│   │   ├── collectors/          #（待建）cnipa.py / qcc.py / recruit.py / news.py
│   │   └── entity_resolution.py #（待建）P0① 映射
│   ├── app/             # export_dashboard.py / local_explain.py /（未来）FastAPI 服务
│   ├── model/           # 施汶邑：STGNN
│   ├── graph/           # 施汶邑：图谱构建
│   └── explain/         # 施汶邑：可解释性
├── frontend/            # 刘钰泉：Vue3+Vite 工程（src/{api,charts,components,router,stores,styles,types,utils,views}、vite.config.ts、package.json）——与 web/ 并存，详见下方"frontend/ 与 web/ 并存说明"
├── web/                 # 刘钰泉：静态演示版 index.html / dashboard_data.json / vendor/（旧 demo，保留作轻量预览，新功能只在 frontend/ 推进）
├── outputs/             # 算法产出：shap_global.json / baseline_results.json 等
├── docs/                # 特征工程字典、设计文档、计划、模板
├── configs/             # 配置（当前云端为空，建库后补默认配置）
├── notebooks/           # 探索性分析（当前云端为空）
├── tests/               # 测试（当前云端为空，优先补 preprocess 的零泄漏单测）
├── data/                # 只放 README.md：说明数据在 tdrive「数据」目录，不入库
├── run_web.py           # demo 启动入口
├── run_demo.py          # demo 入口
├── requirements.txt     # 依赖（固定版本）
├── README.md            # 项目说明 + 快速开始
├── .gitignore           # 配套文件，已就位
└── LICENSE              # 软著登记前可先不加
```

> **frontend/ 与 web/ 并存说明（v1.1 新增）**：经 2026-10-15 口径裁决，`frontend/` 为当前活跃前端工程（Vue3+Vite，依据《前端页面与交互原型说明》§八），`web/` 保留为早期静态 demo。二者并存是时间差产物而非冲突：所有新前端功能只在 `frontend/` 推进，`web/` 仅作轻量预览、不再扩展。可视化主题色由 `configs/viz_theme.yaml`（见 §11 变更记录）单一来源生成，前端 `src/styles/theme.css` 由后端脚本 `scripts/build_viz_theme.py` 覆盖写入，禁止手工编辑。

---

## 4. 大文件与数据管理（Git 与 tdrive 分工）

| 内容                                                                   | 放哪               | 规则                                                                 |
| -------------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------ |
| 代码 / 文档 / 配置 / 测试                                                    | **GitHub**       | 唯一版本管理源，代码只以 Git 为准                                                |
| 真实数据 parquet（`zhejiang_ALL_data(1).parquet` 等）、`output_part(2).xlsx` | **tdrive「数据」目录** | 不进 Git；`data/` 目录只留 README 指路                                      |
| 模型权重（.pt/.pth）                                                       | 本地 / tdrive      | 不进 Git（可脚本复现）；GitHub LFS 免费额度 1GB/月，非必要不用                          |
| 交付物（PPT、报告、软著材料）                                                     | tdrive + 本地      | 走既有"本地准备→确认后上传"流程                                                  |
| 前端 `dashboard_data.json`（137KB）                                      | Git（在 `web/` 内）  | 小文件可入库，但它是**生成产物**——建议提交 + 在 README 注明由 `export_dashboard.py` 重新生成 |

> 若未来确需入库中等文件：`git lfs track "*.parquet"` 后正常提交，GitHub 会自动走 LFS。

---

## 5. Commit 规范（Conventional Commits，中文简述）

### 5.1 格式

```
<type>(<scope>): <中文简述，不超过 50 字>
```

### 5.2 type 对照

| type       | 用途        | 示例                                                |
| ---------- | --------- | ------------------------------------------------- |
| `feat`     | 新功能       | `feat(data): 新增 entity_resolution 实体解析模块`         |
| `fix`      | 修 bug     | `fix(app): 修复 dashboard 导出时 explanations 键为字符串类型` |
| `docs`     | 文档        | `docs: 新增后端工作总览 v1.0 与 Git 协作规范`                  |
| `refactor` | 重构（不改行为）  | `refactor(data): 抽取 build() 中的标准化管线`              |
| `test`     | 测试        | `test(data): 补充 preprocess 零泄漏分组切分单测`             |
| `chore`    | 构建/杂务/初始化 | `chore: 初始化仓库，迁入既有代码与文档`                          |
| `perf`     | 性能        | `perf(data): parquet 读取改列裁剪，内存降 60%`              |

### 5.3 scope 对照（按目录）

`data`（src/data）/ `app`（src/app）/ `model` / `graph` / `explain` / `web` / `frontend`（前端 Vue3+Vite 工程，v1.1 新增）/ `docs` / `configs` / `ci`

> **scope 口径补充（v1.1 新增）**：
>
> - **`docs` 具双重身份**：既是"文档"（如设计说明），也是"契约/规范"载体（如本规范、数据字典、可视化清单）。凡触及契约/口径的改动，commit scope 仍记 `docs`，但 PR 描述须标注"契约变更"并同步更新对应文档内版本号。
> - **`scripts/` 不设独立 scope**：`scripts/`（如 `collect_pool.py`/`match_patents.py`/`pdf_to_csv.py`/`build_viz_theme.py`）为后端数据/生成辅助脚本，按其功能归入 `data`/`configs` 等现有 scope，不在 commit scope 中单列（避免 scope 膨胀、软著归类发散）。

### 5.4 为什么定这么细

1. **软著源码整理**：T72 时按 `feat|fix|refactor` 过滤即可生成开发过程编年史，比翻日志强十倍；
2. code review 一眼看出动没动算法/数据口径——**动了 `data`/`model` scope 的 PR，必须 @施汶邑 会签**（口径变更影响全链）。

### 5.5 硬规矩

- 禁止 `update`、`修改`、`final`、`wip` 这类无信息量 message；
- 一个 commit 只做一件事；数据/模型口径类改动必须写清"旧口径→新口径"；
- 不提交密钥、真实数据、`.venv`（.gitignore 已挡，push 前再 `git status` 确认一次）。

---

## 6. 分支策略（轻量，三人团队）

```
main（受保护，只接受 PR 合并）
 └── feature/xxx（短命分支，1–3 天合并，如 feature/entity-resolution）
```

```bash
# 日常开发
git switch main && git pull
git switch -c feature/entity-resolution
# ... 开发 ...
git push -u origin feature/entity-resolution
# 网页端开 PR → 自审或搭档 review → 合并 → 删分支
```

- **「直推 main」限期限例外条款（v1.1 新增）**：仅在**演示/评审窗口期**（中期检查 2026-11、结题答辩 2027-03 前后各 ≤7 天）允许绕过 PR 直推 main 用于紧急 demo 修复；其余时间一律走 `feature/` + PR。每次直推须在对应 PR 或 issue 中补"直推记录（原因+时间+影响文件）"，且窗口期结束后 **1 个工作日内**必须补开一个 `chore` 类 PR 说明该直推。
- **分支命名规范（v1.2 成文）**：统一 `<type>/<kebab-简述>`——全小写、连字符 `-` 分隔，**禁止中文 / 空格 / 下划线**；
  - `type` ∈ {`feature`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`}，与 §5.2 commit type 对齐；
  - 简述 2–5 个英文词、总长 ≤40 字符、说人话（`feature/entity-resolution`，**禁止** `feature/dev1`、`feature/aa` 这类无信息名）；
  - **一个分支只做一件事**，合并后立即删除；禁止长期挂 `develop`/`dev` 等集成分支（三人团队保持「主干 + 短命分支」）。
- 算法侧施汶邑同理：`feature/` 前缀 + 命名说人话（`feature/gat-attention`）。

---

## 7. 首次提交清单（Checklist）

- [ ] `.gitignore` 就位（配套文件已写好，直接用）
- [ ] `git status` 确认无 parquet/xlsx/pt/venv/密钥
- [ ] 首条 commit：`chore: 初始化仓库，迁入 src/web/docs/outputs 等既有代码与文档`
- [ ] `data/README.md`：写明真实数据在 tdrive「数据」目录（file_id: `HCQOQNDwGbMA`）
- [ ] README.md 补齐：项目简介、目录结构、`run_web.py` 快速开始、分工表
- [ ] 三名协作者邀请发出
- [ ] main 分支保护规则开启
- [ ] 把仓库地址写进《后端工作总览》§3，替换"代码在 tdrive"的旧描述

---

## 8. 建库后顺手解决的两件事

1. **`export_dashboard.py` / `local_explain.py` 访问异常**：tdrive 上这两个文件连续两次 `InvalidAccessKeyId`。建库时从**任何能打开 sharepoint/网页预览的渠道**补一份进去，或以 Git 版本为准，tdrive 那份标注"以 Git 为准"。
2. **看板更新**：仓库建好后，把新版任务看板里 T00 标 🟡→✅，并把仓库地址贴进去。

---

## 9. 待确认事项

| # | 事项                                          | 找谁    | 状态  |
| - | ------------------------------------------- | ----- | --- |
| 1 | 仓库名最终定版（`sme-credit-eval`？）                 | 全组群确认 | 待定  |
| 2 | Owner 用负责人账号还是你的账号（权属影响软著）                  | 你/负责人 | 待定  |
| 3 | 施汶邑/刘钰泉/袁雪茹的 GitHub 用户名                     | 群接龙   | 待收集 |
| 4 | 代码汇总目录：把 tdrive 现有 src/web/docs/outputs 下到哪 | 你本地决定 | 待定  |

---

## 10. 命令速查（Windows Git Bash）

```bash
git init -b main                          # 初始化主干
git remote add origin <url>               # 关联远端
git add . && git status                   # 暂存并核对
git commit -m "type(scope): 简述"          # 提交
git push -u origin main                   # 首推
git switch -c feature/xxx                 # 开分支
git log --oneline --graph                 # 看历史
git log --format="%s" --grep="feat"       # 软著素材：导出 feat 类提交
```

---

## 11. 变更记录（Changelog）

| 版本   | 日期         | 变更摘要                                                                                                                                                                  |
| ---- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.2 | 2026-10-16 | ① §2.2 首次汇总下载清单补 `frontend/`（与 §3 目录树一致，避免按 §2.2 抄时漏出前端工程）；② §6 分支命名由「仅有示例」补成**成文规范**（type 对齐 commit、全小写 kebab、合并即删） |
| v1.1 | 2026-10-16 | ① §3 补 `frontend/`（Vue3+Vite 活跃工程）与 `web/`（旧静态 demo）并存说明；② §5.3 加 `frontend` scope 并注 `docs` 双重身份、`scripts/` 不设独立 scope；③ §6 增「直推 main」限期限例外条款；④ 附可视化主题 yaml 键名口径（见下） |

> **可视化主题键名口径（v1.1 新增 · 关联 `configs/viz_theme.yaml`）**：主题色唯一来源为 `configs/viz_theme.yaml`，由后端脚本 `scripts/build_viz_theme.py` 生成前端 `src/styles/theme.css`（CSS 变量）与 `theme.ts`（TS 常量）。键名强制约定：
>
> - 风险五档 `risk_band` code = `excellent` / `good` / `watch` / `warning` / `high_risk`（**第五档为 `high_risk`，禁用别名 `high` / `high-risk`**）；
> - 技术领域键 = `field`（**禁用别名 `tech_domain` / `techField`**）；
> - 颜色仅允许 `#RRGGBB`；CSS 变量命名 `_→-`（`--risk-high-risk`），TS 键原样 snake_case（`high_risk`）；


> - 新增颜色/键前须先在 yaml 登记并加入 `forbidden_aliases`，杜绝第三套写法。
