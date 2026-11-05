# 前端协作规范（frontend/）

> 上游依据：《Git 仓库搭建与协作规范》v1.0（T00，吕键牵头，2026-10-14 定 GitHub）。
> 本文件**照搬** T00 的分支与 commit 约定，只补充前端工程特有的落地细则。
> T00 中 `data` / `model` 是 **scope**（按目录），不是 type —— 早期手写版本曾弄错，以此为准。

## 一、仓库与分支

| 项       | 值                                                                                      |
| -------- | --------------------------------------------------------------------------------------- |
| 平台     | GitHub **Private** 仓库（软著登记前保密）                                               |
| 仓库名   | `sme-credit-eval`（备选 `tech-sme-credit` / `credit-assessment-stgnn`，待全组通报定版） |
| 默认分支 | `main`，**受保护**：Require a pull request before merging + Require approvals: 1        |

```
main（受保护，只接受 PR 合并）
 └── feature/xxx（短命分支，1–3 天合并）
```

```bash
git switch main && git pull
git switch -c feature/frontend-fe02-api-layer
# ... 开发 ...
git push -u origin feature/frontend-fe02-api-layer
# 网页端开 PR → review → 合并 → 删分支
```

命名规则（与《后端裁决 · 口径冲突三条》v1.0 裁决二一致）：

```
feature/<scope>-<任务号>-<简述>
        │        │       └─ 小写连字符，说人话
        │        └─ 前端任务号（fe-02 等），便于与看板对账
        └─ T00 §5.3 的 scope（本工程用 frontend）
```

- 例：`feature/frontend-fe02-api-layer`、`feature/frontend-fe12-graph-force-layout`。
- 多人同时改同一 scope 时允许加责任人前缀：`feature/<姓名拼音>-<scope>-<简述>`（便于软著佐证区分创作主体）。
- 紧急 demo 修复可直推 `main`（中期检查窗口期特权），但**必须在 issue/PR 里补记录**。
- 不使用 `dev` 分支，不建长期个人分支。

## 二、Commit 约定

```
<type>(<scope>): <中文简述，不超过 50 字>
```

**type** 只允许这七种（T00 §5.2）：

| type       | 用途                 |
| ---------- | -------------------- |
| `feat`     | 新功能               |
| `fix`      | 修 bug               |
| `docs`     | 文档                 |
| `refactor` | 重构（不改行为）     |
| `test`     | 测试                 |
| `chore`    | 构建 / 杂务 / 初始化 |
| `perf`     | 性能                 |

**scope** 按目录取（T00 §5.3）：

`data`（src/data）/ `app`（src/app）/ `model` / `graph` / `explain` / `web`（旧静态 demo）/ `frontend`（本工程）/ `docs` / `configs` / `ci`

> `frontend` 是本工程新增的 scope：前端工程落在仓库根 `frontend/`（依据《前端页面与交互原型说明》v1.0 §8），
> 与 T00 §3 里 `web/`（`index.html` + `dashboard_data.json` + `vendor/` 旧静态 demo）并存，互不覆盖。

示例：

```
feat(frontend): 落地七页面路由与顶部导航骨架
fix(frontend): 修正 P7 动态路由导航失效问题
chore(frontend): 初始化 Vite + Vue3 工程与 ESLint 配置
refactor(frontend): 抽离统一响应包解析
```

**硬规矩**（T00 §5.5）：

- 禁止 `update`、`修改`、`final`、`wip` 这类无信息量 message；
- 一个 commit 只做一件事；
- 改动 `data` / `model` 口径的必须写清「旧口径 → 新口径」；
- 动了 `data` / `model` scope 的 PR **必须 @施汶邑 会签**（口径变更影响全链）；
- 不提交密钥、真实数据（`.parquet` / `.xlsx`）、`.venv`。

## 三、提交前自检（四项全绿 + 一次 git status）

```bash
npm run lint:check      # ESLint，0 error 0 warning
npm run type-check      # vue-tsc --noEmit
npm run test            # test:routes 七路由冒烟 + test:api 接口层冒烟
npm run build           # 生产构建
git status              # 核对：无 .parquet / .xlsx / .pt / .venv
```

`node_modules`、`dist`、`.env.local` 已在 `.gitignore` 中。

## 四、文件与数据落位（T00 §4）

| 内容                                    | 放哪                                            |
| --------------------------------------- | ----------------------------------------------- |
| 代码 / 文档 / 配置 / 测试               | **GitHub**（唯一版本管理源，代码只以 Git 为准） |
| 真实数据 `parquet` / `output_part.xlsx` | tdrive「数据」目录，**不进 Git**                |
| 模型权重 `.pt` / `.pth`                 | 本地 / tdrive，不进 Git（可脚本复现）           |
| 交付物（PPT、报告、软著材料）           | tdrive + 本地，走「本地准备 → 确认后上传」流程  |

## 五、代码约定（前端特有）

- 组件文件 `PascalCase.vue`，页面统一 `XxxView.vue`；路由文件 `src/router/`。
- 路径别名统一 `@/`，禁止 `../../` 多级相对路径。
- 接口一律走 `src/api/` 暴露的方法，**页面内禁止直接调用 axios**。
- 组件内**禁止硬编码色值 / 间距 / 图表高度**，一律用 `src/styles/tokens.css` 的 CSS 变量。
- 图表配置放 `src/charts/`，**按契约的 A 级编号命名**（A01–A18，见《可视化清单与图表数据契约》§三）。
- TypeScript `strict` 全开，禁止 `any` 兜底。
- 三条数据红线（违反即不合并）：
  1. 每个图表容器必须有数据来源角标，取值来自 `meta.dataset`，缺失即不合格；
  2. `real_panel` / `synthetic_graph` / `mixed` 不同来源的结果**不得同图混排**；
  3. `semantic_known: false` 的字段只显示原始代号（如 `x037`），不做任何业务化包装。

## 六、接口契约变更流程

1. 契约以《可视化清单与图表数据契约》v1.0 §6 为唯一仲裁依据；文件统一在 `outputs/contracts/`；
2. 后端（吕键）把契约暴露成 `/api/...` 接口，**不得改变字段名**；前端不直接读文件；
3. 接口未就绪时，前端使用契约文件**同构**的 mock，字段名与结构不得简化；
4. 每次联调后更新《前端页面与交互原型说明》的接口表并递增版本号；
5. 契约冻结后，切换 mock / 真实接口**不允许改动页面代码**。

## 七、关联任务

- 本工程初始化：FE-00
- 上游依赖：T00《Git 仓库搭建与协作规范》、《可视化清单与图表数据契约》v1.0、《前端页面与交互原型说明》v1.0、后端答复（FastAPI + 统一响应包 + 21 接口状态表）
- 下游消费：FE-01 ~ FE-21
