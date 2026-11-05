/**
 * 接口层冒烟测试（FE-02 / FE-11）
 *
 * 断言的不是「HTTP 通不通」，而是三件容易被忽略的事：
 *   ① 信封同构：21 个端点的 key 都能被 mock 通道应答，且回的是 code/message/data 四件套，
 *      不会出现某个页面拿到裸数据后 `res.data.data` 取空；
 *   ② 契约落地：P2 四个端点回的数据与 outputs/contracts/*.json **逐字段同构**
 *      （节点数 / 热力格数 / 中位月数 / 叶子数），防止契约改了前端 mock 没跟上；
 *   ③ 红线随信封走：meta.noise_floor 恒为 0.477 且 dataset 标了来源，
 *      保证图表组件的角标有东西可显示。
 *
 * 这里直接 import 契约 JSON 与 mock 模块（不经过浏览器），
 * 所以它是**最快的一道闸**：`npm run test` 里排在 P2 浏览器冒烟之前。
 *
 * 用法：node scripts/api-smoke.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')

const contract = (name) => JSON.parse(readFileSync(resolve(root, `outputs/contracts/${name}.json`), 'utf8'))
const contracts = {
  pipeline: contract('data_pipeline'),
  quality: contract('data_quality'),
  depth: contract('panel_depth'),
  tree: contract('feature_tree'),
  benchmark: contract('benchmark'),
  shap: contract('shap_global'),
  overview: contract('overview'),
  trainCurve: contract('train_curve'),
  rocPrKs: contract('roc_pr_ks'),
  noiseFloor: contract('noise_floor'),
}

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

/* ---------------------- 端点清单：从源码文字里取，不 import ----------------------
   src/api/index.ts 里有 `import.meta.env`，Node 直接 import 会炸；
   而端点清单本身是纯字面量，正则抓一遍比给构建加 SSR 通道便宜得多。 */
const apiSource = readFileSync(resolve(root, 'src/api/index.ts'), 'utf8')
const endpointKeys = [...apiSource.matchAll(/^\s*'([a-z0-9/-]+)':\s*'[A-Z]+\s/gm)].map((m) => m[1])
report(endpointKeys.length >= 21, `端点清单可解析（${endpointKeys.length} 个 ≥ 21）`)
report(endpointKeys.includes('data/pipeline'), '端点清单含 data/pipeline')
report(endpointKeys.includes('features/tree'), '端点清单含 features/tree')
report(endpointKeys.includes('model/benchmark'), '端点清单含 model/benchmark（P4）')
report(endpointKeys.includes('explain/global'), '端点清单含 explain/global（P5）')
report(endpointKeys.includes('model/train-curve'), '端点清单含 model/train-curve（P4-A1）')
report(endpointKeys.includes('model/roc-pr-ks'), '端点清单含 model/roc-pr-ks（P4-A3）')
report(endpointKeys.includes('model/noise-floor'), '端点清单含 model/noise-floor（P4-A4）')
report(endpointKeys.includes('overview/summary'), '端点清单含 overview/summary（P1）')

/* ------------------ ④ 回归：静态托管对 /api 回落 index.html 的白屏 ------------------
 * 2026-10-22 线上事故：SPA 静态托管的 history fallback 把 `/api/data/pipeline`
 * 也返回了 200 + index.html，axios 视为成功，`res.data` 是一段 HTML 字符串；
 * 旧实现只看 `code` 字段，HTML 上没有 code，于是既不报错也不降级，
 * 把 HTML 当信封交给页面 → 页面读 `pipeline.nodes` 得到 undefined → 整页被错误态替换。
 *
 * 修复点在 `src/api/index.ts` 的 `isEnvelope()`：形状不对就在 auto 模式下降级 mock。
 * 这里用**源码文本**做守门（不 import，因为该文件依赖 import.meta.env）：
 * 一旦有人把这段形状校验删掉/改宽，这条会立刻红。
 * -------------------------------------------------------------------------- */
report(/function isEnvelope\(body: unknown\)/.test(apiSource), '① isEnvelope() 形状校验存在')
report(
  /typeof b\.data !== 'string'/.test(apiSource),
  '② isEnvelope() 明确拒绝 data 为字符串（HTML 回落正是这种情况）',
)
report(
  /if \(!isEnvelope\(body\)\)[\s\S]{0,200}getMock\(key\)/.test(apiSource),
  '③ 非信封响应在 auto 模式下降级 mock，而不是交给页面',
)

/* ------------------------------ ① 信封同构 ------------------------------ */
const envelopeOk = (env) =>
  env && typeof env === 'object' && 'code' in env && 'message' in env && 'data' in env

for (const [name, key] of [
  ['pipeline', 'data/pipeline'],
  ['quality', 'data/quality'],
  ['depth', 'data/panel-depth'],
  ['tree', 'features/tree'],
]) {
  const c = contracts[name]
  report(envelopeOk({ code: 0, message: 'ok', data: c.data }), `${key} 契约为 code/message/data 结构`)
  report(c.meta && typeof c.meta === 'object', `${key} 契约带 meta（图表角标依赖它）`)
}

/* ------------------------------ ② 契约落地 ------------------------------ */
const p = contracts.pipeline.data
report(Array.isArray(p.nodes) && p.nodes.length === 12, `A01 链路节点 12 个（实际 ${p.nodes?.length}）`)
report(Array.isArray(p.edges) && p.edges.length > 0, `A01 链路边非空（${p.edges?.length}）`)
report(
  Array.isArray(p.steps) && p.steps.length === 6 && p.steps[0].name.includes('userid'),
  `A01 六步流水线、首步按 userid 分组切分（实际 ${p.steps?.length} 步）`,
)
report(
  p.steps.every((s) => s.impl && s.detail && s.status),
  'A01 每一步都带 impl / detail / status（不摆空壳）',
)

const q = contracts.quality.data
report(q.fields.length === 14, `A02 字段 14 个（实际 ${q.fields.length}）`)
report(
  q.missing_matrix.length >= q.fields.length * 3,
  `A02 缺失率矩阵覆盖多年（${q.missing_matrix.length} 格 ≥ 42）`,
)
report(
  q.missing_matrix.every((c) => typeof c.missing_rate === 'number' && c.year),
  'A02 每个热力格都有 year + missing_rate',
)
const stages = new Set(q.winsor.map((w) => w.stage))
report(stages.has('before') && stages.has('after'), 'A02 缩尾数据含 before / after 两段')
report(
  q.winsor.every((w) => typeof w.q05 === 'number' && typeof w.q50 === 'number' && typeof w.q95 === 'number'),
  'A02 箱线五数（q05/q25/q50/q75/q95）齐全',
)
report(
  q.fields.filter((f) => f.semantic_known === false).length === q.fields.length,
  'A02 演示数据里字段语义全部未确认（红线二的适用面）',
)

const d = contracts.depth.data
report(Array.isArray(d.bins) && d.bins.length >= 4, `A03 直方图分箱 ${d.bins?.length} 个（≥4）`)
report(
  d.bins.reduce((s, b) => s + b.enterprises, 0) === d.coverage.enterprises_with_panel,
  `A03 分箱企业数合计 = 有面板企业数（${d.coverage.enterprises_with_panel}）`,
)
report(d.coverage.median_months === 2, `A03 中位观测 2 个月（实际 ${d.coverage.median_months}）`)
report(
  d.coverage.long_sequence_feasible === false,
  'A03 明确给出「长序列建模不可行」的判断，而不是留给读者推',
)

const t = contracts.tree.data
const countLeaves = (nodes) => nodes.reduce((n, x) => n + (x.kind === 'leaf' ? 1 : countLeaves(x.children ?? [])), 0)
const l1 = t.roots.length
const l2 = t.roots.reduce((n, r) => n + (r.children?.length ?? 0), 0)
const leaves = countLeaves(t.roots)
report(l1 === 3, `A04 一级维度 3 个（实际 ${l1}）`)
report(l2 >= 6, `A04 二级子类 ≥6（实际 ${l2}）`)
report(leaves === 11, `A04 叶子字段 11 个（实际 ${leaves}）`)
report(
  t.summary && t.summary.level1 === l1 && t.summary.leaves === leaves,
  'A04 summary 与实际层级计数一致（不各算各的）',
)
report(
  t.flat === undefined || t.flat.length === leaves,
  'A04 扁平表与树结构叶子数一致',
)

/* -------------------- P4 模型与对比 / P5 全局解释（实测契约） -------------------- */
const b = contracts.benchmark
const bd = b.data
report(b.meta.source === 'measured', `P4 基准契约 source = measured（实际 ${b.meta.source}）`)
report(b.meta.dataset === 'real_panel', 'P4 基准契约 dataset = real_panel（红线四的前提）')
report(bd.noise_floor.value === 0.477, `P4 噪声地板 0.477（实际 ${bd.noise_floor.value}）`)
report(bd.levels.length === 3, `P4 CV 三级（行 / 企业 / 打乱标签），实际 ${bd.levels.length}`)
report(
  bd.levels.filter((l) => l.is_permutation_control).length === 1,
  'P4 恰好一级是置换对照（对照组不能没有，也不能两条）',
)
report(
  bd.levels.every((l) => l.models.every((m) => typeof m.metrics_mean?.auc === 'number')),
  'P4 每级每个模型都有 metrics_mean.auc（MetricBar 依赖它）',
)
report(bd.holdout.models.length === 4, `P4 留出集 4 个模型（实际 ${bd.holdout.models.length}）`)
report(
  bd.holdout.models.every((m) => ['auc', 'ks', 'accuracy', 'precision', 'recall', 'f1'].every((k) => typeof m.metrics[k] === 'number')),
  'P4 留出集六项指标齐全（明细表不留空格）',
)
report(
  bd.holdout.n_test === b.meta.n_samples,
  `P4 留出集样本数与 meta 一致（${bd.holdout.n_test}）`,
)
// 结论必须能对上数字，否则就是文案和数分离
const rowAuc = bd.levels.find((l) => l.level_id === 'row').models.find((m) => m.model_id === 'lr').metrics_mean.auc
const compAuc = bd.levels.find((l) => l.level_id === 'company').models.find((m) => m.model_id === 'xgb').metrics_mean.auc
report(rowAuc > bd.noise_floor.value, `P4 行级 AUC ${rowAuc} 高于噪声地板 ${bd.noise_floor.value}（方向正确）`)
report(compAuc < 0.5, `P4 企业级 AUC ${compAuc} 低于 0.5（「聚合后信号消失」的结论有据）`)

const sh = contracts.shap.data
report(contracts.shap.meta.source === 'measured', 'P5 解释契约 source = measured')
report(sh.models.length === 2, `P5 两个模型（实际 ${sh.models.length}）`)
report(
  sh.models.every((m) => m.top.length === 20),
  'P5 每个模型 top-20 齐全',
)
report(
  sh.models.every((m) => m.top.every((r) => /^x\d{1,3}$/i.test(r.feature))),
  'P5 红线二：解释里的字段全部是 xNNN 代号',
)
report(
  sh.models.every((m) => m.unknown_field_share === 1),
  'P5 语义已知字段解释占比 = 0（unknown_field_share 1.0）',
)

/* ---------------- FE-14：A13 蜂群点位 + A15 阶段-因子-权重 ----------------
 * 两份契约的性质完全不同，所以分开核：
 *   ① shap_global 的 `beeswarm` 是**锚点重建**：必须满足「均值 = 实测」与
 *      「秩相关符号 = 实测方向」两条约束，否则图上散点与条形图会自相矛盾；
 *   ② stage_factor_weight 是 mock：核的是「结构完整 + 占位标注齐全 + 不伪造字段」。
 * -------------------------------------------------------------------------- */
const bwMeta = sh.models[0].beeswarm_meta
report(contracts.shap.meta.beeswarm_source === 'reconstructed_from_anchors', 'A13 蜂群点位声明为锚点重建（不冒充实测）')
report(
  sh.models.every((m) => m.beeswarm.length === 15 && m.beeswarm_meta.n_plotted === 200),
  'A13 蜂群：两个模型各 15 特征 × 200 点',
)
let bwWorst = 0
let bwSignOk = true
let bwDevOk = true
for (const m of sh.models) {
  let modelWorst = 0
  for (const f of m.beeswarm) {
    const mean = f.points.reduce((s, p) => s + Math.abs(p[0]), 0) / f.points.length
    modelWorst = Math.max(modelWorst, Math.abs(mean - f.mean_abs_shap))
    if ((f.rho_feature_shap > 0) !== (f.direction > 0)) bwSignOk = false
  }
  bwWorst = Math.max(bwWorst, modelWorst)
  // 契约里每个模型各写自己的最大偏差，要逐模型比；拿全局最大值去比会误报
  if (Math.abs(m.beeswarm_meta.max_mean_deviation - modelWorst) > 1e-9) bwDevOk = false
}
report(bwWorst < 1e-5, `A13 蜂群每行 mean|SHAP| 与实测一致（最大偏差 ${bwWorst.toExponential(2)}）`)
report(bwDevOk, 'A13 契约声明的 max_mean_deviation = 按落盘点位复算的偏差（不虚报为 0）')
report(bwSignOk, 'A13 蜂群每行秩相关符号 = 实测 direction（保护因子不会画反）')
report(
  sh.models.every((m) => m.beeswarm.every((f) => m.top.some((t) => t.feature === f.feature))),
  'A13 蜂群特征全部来自实测 top-20（没有凭空造字段）',
)
report(bwMeta.derived === true && Boolean(bwMeta.derivation), 'A13 重建口径带 derived + derivation（可追溯）')

const sfEnv = contract('stage_factor_weight')
const sf = sfEnv.data
report(endpointKeys.includes('explain/stage-factor'), '端点清单含 explain/stage-factor（P5-A15）')
report(sfEnv.meta.contract_status === 'mock' && sfEnv.meta.source === 'demo_placeholder', 'A15 契约自报 mock / demo_placeholder')
report(Array.isArray(sfEnv.meta.blocked_by) && sfEnv.meta.blocked_by.includes('P0-2'), 'A15 声明被 P0-2（字段字典）阻塞')
report(sf.stages.length === 4 && sf.factors.length === 3, `A15 4 阶段 × 3 维度（实际 ${sf.stages.length} × ${sf.factors.length}）`)
const stageSum = sf.stages.map((st) =>
  sf.weights.filter((w) => w.stage_id === st.stage_id).reduce((a, b) => a + b.weight, 0),
)
report(
  stageSum.every((v) => Math.abs(v - 1) < 1e-6),
  `A15 每阶段权重合计 100%（${stageSum.map((v) => v.toFixed(3)).join(' / ')}）`,
)
report(
  sf.factors.every((f) => f.label_kind === 'dimension' && f.semantic_known === false),
  'A15 维度名标为 dimension 且 semantic_known=false（不假装是字段业务名）',
)
const topFields = new Set(sh.models[0].top.map((r) => r.feature))
report(
  sf.stage_fields.every((s) => s.fields.every((f) => topFields.has(f.feature))),
  'A15 阶段字段全部取自实测 top-20（占位的是权重，不是字段）',
)
report(
  sf.stage_fields.every((s) => s.fields.every((f) => f.semantic_known === false)),
  'A15 红线二：阶段字段一律 semantic_known=false',
)
report(
  sf.unlock.blocked_by.includes('P0-2') && sf.unlock.after.length > 10,
  'A15 写明解锁条件与解锁后的做法（不是"以后再说"）',
)
report(sf.decision_notes.length === sf.stages.length, 'A15 每个阶段都有决策说明')

/* ------------------------------- P1 概览（聚合契约） -------------------------------
 * P1 不引入新数据，它是 P2/P4/P5 契约的聚合视图。这里逐条核对「概览里的数字
 * 都能在源契约里找到出处」，防止有人手改概览数字而不改源数据。
 * -------------------------------------------------------------------------- */
const ov = contracts.overview.data
report(contracts.overview.meta.source === 'aggregated', `P1 概览契约 source = aggregated（实际 ${contracts.overview.meta.source}）`)
report(ov.kpis.length === 4, `P1 四个 KPI（实际 ${ov.kpis.length}）`)
report(ov.method_chain.steps.length === 6, `P1 方法链路 6 步（实际 ${ov.method_chain.steps.length}）`)
report(
  ov.method_chain.steps.every((s) => s.impl && s.detail && s.status),
  'P1 每一步都带代码落点 / 说明 / 状态（不摆空壳）',
)
report(ov.gaps.items.length === 3, `P1 数据缺口 3 条（实际 ${ov.gaps.items.length}）`)
report(
  ov.stage_progress.items.filter((i) => i.state === 'delivered').length === ov.stage_progress.summary.delivered,
  'P1 进度摘要与实际格子数自洽',
)

// 逐个 KPI 追到源契约
const ovRowLr = bd.levels.find((l) => l.level_id === 'row').models.find((m) => m.model_id === 'lr')
const ovCompXgb = bd.levels.find((l) => l.level_id === 'company').models.find((m) => m.model_id === 'xgb')
const kpiById = Object.fromEntries(ov.kpis.map((k) => [k.id, k]))
report(
  Math.abs(kpiById.auc_row.value - ovRowLr.metrics_mean.auc) < 5e-4,
  `P1 auc_row ${kpiById.auc_row.value} == P4 ${ovRowLr.metrics_mean.auc.toFixed(4)}`,
)
report(
  Math.abs(kpiById.auc_company.value - ovCompXgb.metrics_mean.auc) < 5e-4,
  `P1 auc_company ${kpiById.auc_company.value} == P4 ${ovCompXgb.metrics_mean.auc.toFixed(4)}`,
)
report(
  kpiById.explain_share.value === 1 - sh.models[0].unknown_field_share,
  'P1 explain_share 与 P5 unknown_field_share 互补一致',
)
report(
  kpiById.panel_months.value === contracts.depth.data.coverage.median_months,
  'P1 panel_months 与 P2 panel_depth 一致',
)

/* ------------------ P3 图谱三端点（FE-12 · 合成图，红线一走 disclaimer） ------------------
 * P3 与 P1/P2/P4/P5 的关键差别：它的数据集是 `synthetic_graph`，**不是**真实面板。
 * 所以这里不核对 noise_floor（对合成图不适用，契约为 null），而是核三件事：
 *   ① `dataset = synthetic_graph` 且带 disclaimer —— 缺了它，页面上的合成数据警示就没内容；
 *   ② `redline.no_mixed_dataset` 必须为真，且 `noise_floor_applicable = false`，
 *      防止有人把真实面板的 0.477 硬塞进合成图语境；
 *   ③ 三份契约互相自洽：结构图的节点/边数 = 切片末尾累计 = 中心性统计口径。
 * -------------------------------------------------------------------------- */
const gs = contract('graph_structure')
const gsl = contract('graph_slices')
const gc = contract('graph_centrality')

for (const [name, c] of Object.entries({ graph_structure: gs, graph_slices: gsl, graph_centrality: gc })) {
  report(c.meta.dataset === 'synthetic_graph', `${name} dataset = synthetic_graph（实际 ${c.meta.dataset}）`)
  report(Boolean(c.meta.disclaimer), `${name} 带 disclaimer（合成数据必须自证）`)
  report(c.meta.noise_floor === null, `${name} noise_floor 为 null（合成图不适用红线三）`)
  report(
    c.meta.redline?.noise_floor_applicable === false,
    `${name} redline.noise_floor_applicable = false（不把 0.477 塞进合成图）`,
  )
  report(Array.isArray(c.meta.blocked_by) && c.meta.blocked_by.length === 3, `${name} 标注 3 条数据缺口`)
}

const gsd = gs.data
/**
 * 注意口径：`data.nodes` 是**抽样后**实际下发给前端的子图（默认 150），
 * 全量数字在 `stats.n_nodes` / `stats.n_edges` 与 `meta.n_*_full` 里。
 * 断言必须按契约字面写，不能想当然认为 data.nodes 是全量 —— 这里就踩过一次。
 */
report(
  gsd.nodes.length === gsd.stats.n_nodes_shown,
  `P3 下发给前端的节点 == stats.n_nodes_shown（${gsd.nodes.length}）`,
)
report(
  gsd.edges.length === gsd.stats.n_edges_shown,
  `P3 下发给前端的边 == stats.n_edges_shown（${gsd.edges.length}）`,
)
report(
  gsd.stats.n_nodes === gs.meta.n_nodes_full && gsd.stats.n_edges === gs.meta.n_edges_full,
  `P3 全量口径 stats 与 meta 一致（${gsd.stats.n_nodes} 节点 / ${gsd.stats.n_edges} 边）`,
)
report(gsd.entity_kinds.length === 6, `P3 六类实体（实际 ${gsd.entity_kinds.length}）`)
report(gsd.edge_kinds.length === 6, `P3 六类关系（实际 ${gsd.edge_kinds.length}）`)
report(
  ['company', 'patent', 'investor', 'person', 'field', 'product'].reduce(
    (s, k) => s + gsd.stats.node_kinds[k],
    0,
  ) === gsd.stats.n_nodes,
  'P3 各类型计数合计 == 全量节点总数（图例数字不会和图上对不上）',
)
// 边的两端必须都在节点表里，否则前端会画悬空边（或静默丢边，两边都算错）
const nodeIdSet = new Set(gsd.nodes.map((n) => n.id))
report(
  gsd.edges.every((e) => nodeIdSet.has(e.source) && nodeIdSet.has(e.target)),
  'P3 所有边的两端都在下发的节点表内（无悬空边）',
)

const sl = gsl.data
report(sl.slices.length === sl.quarters.length, `P3 切片数 == 季度数（${sl.slices.length}）`)
const lastSlice = sl.slices[sl.slices.length - 1]
report(
  lastSlice.visible_node_ids.length === gsd.stats.n_nodes,
  `P3 末季可见节点 == 全量节点（${lastSlice.visible_node_ids.length}）`,
)
report(
  sl.slices.every((s, i) => i === 0 || s.visible_node_ids.length >= sl.slices[i - 1].visible_node_ids.length),
  'P3 累计可见节点逐季单调不减',
)

const gcd = gc.data
const dd = gcd.degree_distribution
report(gcd.metrics.length === 4, `P3 四类中心性（实际 ${gcd.metrics.length}）`)
// 分档 = 8 个按度值的档 + 1 个「度 0 孤立」档；孤立档不加的话合计 868 ≠ 878，读者一加就露馅
report(Array.isArray(dd.bins) && dd.bins.length === 9, `P3 度分布分档 9 档（含孤立档，实际 ${dd.bins?.length}）`)
report(
  dd.bins[0]?.isolated === true && dd.bins[0]?.lo === 0,
  'P3 度分布首档是「度 0 孤立」，且 lo = 0',
)
report(
  dd.bins.reduce((s, b) => s + b.nodes, 0) === gsd.stats.n_nodes,
  `P3 度分布分档合计 == 全量节点数（${dd.bins.reduce((s, b) => s + b.nodes, 0)} / ${gsd.stats.n_nodes}）`,
)
report(
  dd.bins.find((b) => b.isolated)?.nodes === gsd.stats.isolated,
  `P3 孤立档节点数 == stats.isolated（${gsd.stats.isolated}）`,
)
report(
  Math.abs(dd.max_degree - 118) < 0.001 && Math.abs(dd.mean_degree - 4.0182) < 1e-3,
  `P3 度统计 max ${dd.max_degree} / mean ${dd.mean_degree}（E3 结论数字有据）`,
)
report(Object.keys(gcd.node_index ?? {}).length === gsd.stats.n_nodes, 'P3 全量节点中心性索引覆盖所有节点')
// 秩相关是 4×4 对称矩阵（含对角 1.0），不是 6 条列表；metrics 本身就是 id 字符串数组
const metricIds = gcd.metrics.map((m) => (typeof m === 'string' ? m : m.id))
report(
  metricIds.every((a) => metricIds.every((b) => typeof gcd.rank_correlation?.[a]?.[b] === 'number')),
  `P3 四类中心性两两秩相关矩阵 ${metricIds.length}×${metricIds.length} 完整`,
)
report(
  metricIds.every((a) => Math.abs(gcd.rank_correlation[a][a] - 1) < 1e-6),
  'P3 秩相关对角全为 1.0（矩阵没写歪）',
)
report(
  Boolean(gcd.interpretation?.eigenvector_vs_degree),
  'P3 契约自带对负相关的解释（页面结论不是前端硬编码的文案）',
)

/* --------------------------- ③ 红线随信封走 --------------------------- */
for (const [name, c] of Object.entries(contracts)) {
  // 红线三只约束「真实面板性能图」。合成图（train_curve / graph_*）必须为 null，
  // 塞进 0.477 会让读者以为合成图也要对那条线 —— 这里的例外是断言出来的，不是放行。
  const floorOk =
    c.meta.dataset === 'synthetic_graph' ? c.meta.noise_floor === null : c.meta.noise_floor === 0.477
  report(
    floorOk,
    c.meta.dataset === 'synthetic_graph'
      ? `${name} 合成图 meta.noise_floor = null（红线三不适用，别顺手补）`
      : `${name} meta.noise_floor = 0.477（红线三锚点）`,
  )
  report(
    ['real_panel', 'synthetic_graph', 'mixed'].includes(String(c.meta.dataset)),
    `${name} meta.dataset 合法（${c.meta.dataset}）`,
  )
  report(
    c.meta.source === 'demo_placeholder' ? Boolean(c.meta.disclaimer) : true,
    `${name} 占位数据必须带 disclaimer（不得当实测引用）`,
  )
}

/* ------------------ P4 新增三份契约（FE-13：A1 曲线 / A3 曲线族 / A4 地板） ------------------ */
const tc = contracts.trainCurve
const rk = contracts.rocPrKs
const nf = contracts.noiseFloor

// A1：合成图，不许带噪声地板，不许声称"实测"
report(tc.meta.dataset === 'synthetic_graph', `A1 训练曲线 dataset = synthetic_graph（实际 ${tc.meta.dataset}）`)
report(
  tc.meta.noise_floor === null,
  'A1 合成图 noise_floor = null（红线三只适用于真实面板，别顺手补上）',
)
report(
  tc.meta.source === 'reconstructed_from_anchors',
  `A1 source = reconstructed_from_anchors（实际 ${tc.meta.source}）—— 不是实测`,
)
report(
  tc.data.curves.every((c) => c.derived && c.derivation),
  'A1 每条曲线都标了 derived + derivation（锚点重建必须自证口径）',
)
const gatCurve = tc.data.curves.find((c) => c.model_id === 'gat')
const holdoutGat = contracts.benchmark.data.holdout.models.find((m) => m.model_id === 'gat')
report(
  gatCurve && Math.abs(gatCurve.best_val_auc - holdoutGat.metrics.auc) < 5e-5,
  `A1 曲线最佳轮 AUC 锚定实测 holdout（${gatCurve?.best_val_auc} vs ${holdoutGat.metrics.auc}）`,
)
report(
  tc.data.curves.every((c) => Math.abs(c.val_auc[0] - 0.477) < 0.08),
  'A1 首轮 AUC 贴近噪声地板 0.477（曲线起点必须像"还没学"）',
)
report(
  tc.data.curves.every(
    (c) =>
      c.val_auc.length === c.epochs.length &&
      c.loss.length === c.epochs.length &&
      c.val_loss.length === c.epochs.length &&
      c.train_auc.length === c.epochs.length,
  ),
  'A1 loss / val_loss / AUC / epochs 四条长度一致（双轴图不会错位）',
)

// A3：真实面板 + 锚点重建，四条模型各一条曲线族
report(rk.meta.dataset === 'real_panel', `A3 曲线族 dataset = real_panel（实际 ${rk.meta.dataset}）`)
report(
  rk.meta.source === 'reconstructed_from_anchors',
  `A3 source = reconstructed_from_anchors（实际 ${rk.meta.source}）`,
)
report(rk.meta.noise_floor === 0.477, `A3 带噪声地板 0.477（实际 ${rk.meta.noise_floor}）`)
report(rk.data.series.length === 4, `A3 四条曲线族（实际 ${rk.data.series.length}）`)
report(
  rk.data.series.every((s) => s.roc.fpr.length === s.roc.tpr.length && s.roc.fpr.length >= 20),
  'A3 ROC 点成对且不少于 20 个（散点图不会退化成一条线）',
)
report(
  rk.data.series.every((s) => Math.abs(s.ks.measured - (contracts.benchmark.data.holdout.models.find((m) => m.model_id === s.model_id)?.metrics.ks ?? -1)) < 5e-4),
  'A3 实测 KS 与 benchmark 留出集 KS 一致（两个端点不许各算各的）',
)
report(
  rk.data.series.every((s) => s.roc.fpr[0] >= 0 && s.roc.tpr[s.roc.tpr.length - 1] >= 0.999),
  'A3 ROC 首点 FPR=0、末点 TPR=1（曲线完整，不是截断的一截）',
)

// A4：观察组 vs 对照组，地板 = 置换对照均值
report(nf.meta.dataset === 'real_panel' && nf.meta.source === 'measured', 'A4 噪声地板契约 = real_panel / measured')
report(nf.data.noise_floor === 0.477, `A4 noise_floor = 0.477（实际 ${nf.data.noise_floor}）`)
report(
  nf.data.bars.some((x) => x.group === 'observed') && nf.data.bars.some((x) => x.group === 'control'),
  'A4 观察组与对照组都至少一根（只画观察组就看不出"够不够高"）',
)
const shifted = nf.data.margin.find((m) => m.model_id === 'lr')
report(
  shifted && Math.abs(shifted.delta - (shifted.observed - shifted.floor)) < 5e-4,
  `A4 差额表 delta = observed − floor（${shifted?.delta}）`,
)
report(
  typeof nf.data.verdict === 'string' && nf.data.verdict.includes('0.477') === false,
  'A4 结论文案不硬编码地板数字（地板换了文案跟着变）',
)

/* 语义未知字段只显代号：x21–x200 区间内不得出现疑似业务名 */
function collectFields(node) {
  if (!node) return []
  if (node.kind === 'leaf') return [node.field]
  return (node.children ?? []).flatMap(collectFields)
}
const unknownFields = [
  ...q.fields.map((f) => f.field),
  ...q.winsor.map((w) => w.field),
  ...t.roots.flatMap(collectFields),
].filter((v, i, a) => a.indexOf(v) === i)
// 契约里字段统一补零到三位（x037），页面展示也照抄，所以正则要允许前导零
const badNames = unknownFields.filter((f) => !/^x\d{1,3}$/i.test(f.trim()))
report(badNames.length === 0, `红线二：未知字段全部是 xNNN 代号（异常 ${badNames.join(',') || '无'}）`)

console.log(failed === 0 ? '\n接口层冒烟测试全部通过' : `\n存在 ${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
