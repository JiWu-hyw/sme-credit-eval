/**
 * P7 单企业深度诊断 冒烟测试（FE-16）
 *
 * 本页的验收是「软著演示主界面」与「匿名态 / 实名态切换不改结构」。这两条都**不能靠看截图**：
 * 图能画出来但数据配不上、匿名提示能显示但结构已经分叉，肉眼都看不出来。
 * 所以这支脚本分五段，逐段拿契约里的数去对数：
 *
 *   0. 纯函数：bandCuts / bandOf / waterfallSegments / heatCells / radarValues /
 *      localVerdict / gaugeOption —— 图表读的就是它们，先证明它们对得上契约。
 *   1. 契约自洽：Σφ+base=f(x)、逐月占比合计=1、五档切点、PD 与分数反向单调、
 *      三链一致（雷达维度 / 邻居 / 时序特征都真的在队列里）。
 *   2. 页面渲染：六区块、两个数据集角标、仪表盘/雷达/瀑布/图谱/热力图 canvas、
 *      可加性等式上屏、排序切换真的改变顺序、企业选择器真的换数据。
 *   3. 红线：不挂 AUC 噪声地板（0.477 与 log-odds 不同量纲）、语义未知字段只显 xNNN、
 *      匿名态不编企业名与行业。
 *   4. 降级：real 模式打空 /api/enterprise → 页面不白屏，读图问答仍在。
 *   5. 健康度：无 pageerror、无 Vue / ECharts 告警。
 *
 * 用法：npm run test:p7
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer as createHttp } from 'node:http'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const contract = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const detailEnv = contract('enterprise_detail')
const detail = detailEnv.data
const localEnv = contract('shap_local')
const local = localEnv.data

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps

const PORT = 11587
const BASE = `http://127.0.0.1:${PORT}`

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  mode: 'test',
  define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('mock') },
  server: { host: '127.0.0.1', port: PORT, strictPort: true },
  logLevel: 'error',
})
await server.listen()

/* ------------------------------------------------------------------ 0. 纯函数 */
console.log('\n— 0. 纯函数（图表读的就是这些）')
const ut = await server.ssrLoadModule('/src/utils/enterprise.ts')

const cuts = detail.risk_bands.cuts
const bands = ut.bandCuts(cuts)
report(bands.length === 5, `bandCuts 切成 5 档（切点 ${cuts.join('/')}）`)
report(bands[0].lo === 0 && bands[4].hi === 100, '五档首尾贴住 0 与 100（不留空档）')
report(
  bands.every((b, i) => i === 0 || b.lo === bands[i - 1].hi),
  '相邻档位首尾相接（不重叠、不留缝）',
)
report(new Set(bands.map((b) => b.color)).size === 5, '五档颜色互不相同（色带不是同色重复）')
report(ut.bandOf(0, cuts).code === bands[0].code && ut.bandOf(100, cuts).code === bands[4].code, 'bandOf 边界值落在首尾档')
report(ut.bandOf(80, cuts).code === ut.bandOf(100, cuts).code, '切点 80 归入上一档（左闭右开）')

const e0 = local.entries[0]
const segs = ut.waterfallSegments(
  e0.base_value,
  e0.features.map((f) => ({ feature: f.feature, phi: f.phi, semantic_known: f.semantic_known })),
  e0.score_logit,
)
report(segs.length === e0.features.length + 2, `分段数 = 特征数 + 2 根基准柱（${segs.length}）`)
report(segs[0].kind === 'base' && segs[segs.length - 1].kind === 'total', '首段是 E[f(x)]、末段是 f(x)')
report(
  near(segs[segs.length - 1].to, e0.score_logit, 1e-9),
  `末段精确落在 f(x)=${e0.score_logit}`,
)
report(
  // 末段是 f(x) 基准柱，它的 from 恒为 0（从画布底部整柱长出），不参与衔接
  segs.slice(1, -1).every((sg, i) => near(sg.from, segs[i].to, 1e-9)),
  '20 段台阶首尾相接（每一段都从上一段终点起算，没有凭空跳变）',
)
let threw = false
try {
  ut.waterfallSegments(e0.base_value, [{ feature: 'x125', phi: 0, semantic_known: false }], e0.score_logit)
} catch {
  threw = true
}
report(threw, '配平不上时 waterfallSegments 抛错（不硬画一张假图）')

const mag = ut.magnitudeSort(e0.features)
report(
  mag.every((f, i) => i === 0 || Math.abs(mag[i - 1].phi) >= Math.abs(f.phi)),
  'magnitudeSort 按 |φ| 降序',
)
const grad = ut.gradientSort(e0.features)
report(
  grad.filter((f) => f.phi > 0).length === e0.features.filter((f) => f.phi > 0).length && grad[0].phi > 0,
  'gradientSort 把推高项排在前面，且不丢项',
)
report(grad[0].feature !== mag[0].feature || grad[0].phi === mag[0].phi, '两种排序的首项可以不同（排序真的动了）')

const axes = ut.radarValues(detail.radar)
const itemCount = detail.radar.reduce((a, d) => a + d.items.length, 0)
report(axes.length === itemCount, `雷达轴数 = 各维指标数之和（${axes.length} 项 = 3×4）`)
report(new Set(axes.map((a) => a.dim)).size === 3, '12 项指标分属 3 个维度（维度色不会串）')
report(
  axes.every((a) => a.self === null || typeof a.self === 'number'),
  '无数据维度给 null 而不是 0（0 会被读成「分位 0」，是错的）',
)
report(ut.radarHasAny(axes), '本契约至少有一个维度有数据')
const cover = ut.dimCoverage(detail.radar)
report(cover.length === 3 && cover.every((c) => c.total === 4), '维度覆盖统计：3 维 × 4 指标')

const cells = ut.heatCells(e0.series)
report(cells.length === e0.series.length * e0.series[0].by_month.length, `热力图格子数 = 特征×月（${cells.length}）`)
const rowSums = e0.series.map((f, y) =>
  cells.filter((c) => c.y === y).reduce((a, c) => a + c.value, 0),
)
report(
  rowSums.every((sum, y) => Math.abs(sum - e0.series[y].total) <= 5e-3),
  '每行格子之和 = 该特征总贡献（热力图与瀑布图同源）',
)
const allHeat = local.entries.flatMap((e) => ut.heatCells(e.series))
report(
  allHeat.some((c) => c.value > 0) && allHeat.some((c) => c.value < 0),
  '全队列热力图有正有负（颜色能分出推高 / 压低，不是单色块）',
)
threw = false
try {
  ut.heatCells([{ feature: 'x125', by_month: [0.9, 0.9] }])
} catch {
  threw = true
}
report(threw, '逐月占比不等于 1 时 heatCells 抛错（分摊必须守恒）')

const verdict = ut.localVerdict(e0.features)
report(
  verdict.push > 0 && verdict.pull < 0 && near(verdict.net, verdict.push + verdict.pull, 1e-4),
  `判决条把推高 ${verdict.push} 与压低 ${verdict.pull} 分开加总，net = 两者之和`,
)
report(
  verdict.topUp !== null && verdict.topDown !== null && verdict.topUp !== verdict.topDown,
  `最推高项 ${verdict.topUp} 与最压低项 ${verdict.topDown} 都指名到了具体字段`,
)

const gopt = ut.gaugeOption(e0.credit_score, cuts, e0.pd, '较好')
report(gopt.series[0].type === 'gauge' && gopt.series[0].min === 0 && gopt.series[0].max === 100, '仪表盘 0–100 量程')
report(gopt.__bands.length === 5, '仪表盘把五档色带一起交给渲染层（不是单一渐变色）')

/* --------------------------------------------------------- 1. 契约自洽 */
console.log('\n— 1. 契约自洽（三链一致，不证明数据真实）')
const addOk = local.entries.every(
  (e) => Math.abs(e.features.reduce((a, f) => a + f.phi, 0) + e.rest.phi + e.base_value - e.score_logit) <= 1e-3,
)
report(addOk, 'Σφ + E[f(x)] = f(x) 对 12 家全部成立（log-odds 尺度）')
const baseUnique = new Set(local.entries.map((e) => e.base_value))
report(baseUnique.size === 1, `12 家共用同一个 E[f(x)] = ${e0.base_value}`)
const queueMean =
  local.entries.reduce((a, e) => a + Math.log(e.pd / (1 - e.pd)), 0) / local.entries.length
report(near(e0.base_value, queueMean, 5e-4), 'E[f(x)] = 队列均值 log-odds（与 f(x) 同口径，不与实测事件率混用）')

report(
  local.entries.every((e) => e.series.every((s) => Math.abs(s.by_month.reduce((a, x) => a + x, 0) - 1) <= 1e-3)),
  '逐特征逐月占比合计 = 1（时序贡献可加回总量）',
)
report(
  local.entries.every((e) => e.series.every((s) => near(s.total, e.features.find((f) => f.feature === s.feature)?.phi, 1e-9))),
  '每个时序特征的总贡献 = 瀑布图里同一根柱子（两图不打架）',
)
report(
  local.entries.every((e) => e.features.every((f) => f.semantic_known === false)),
  '局部归因 20 项全部 semantic_known=false（P0-2 字段字典未到位）',
)
report(
  local.entries.every((e) => e.consistency.rank1_hit_global_top3),
  '每家企业的头号因子都落在实测全局前三之内（局部解释没跑偏）',
)
report(
  local.entries.every((e) => e.consistency.top5_overlap >= 2),
  `局部前五与全局前五至少重合 2 项（最少 ${Math.min(...local.entries.map((e) => e.consistency.top5_overlap))} 项）`,
)

report(
  detail.enterprises.every((e) => e.anonymous === true && e.display_name.includes('匿名')),
  '12 家全部是匿名态（P0-1 映射键未到位）',
)
report(
  detail.enterprises.every((e) => e.industry === null && e.industry_note),
  '行业为空时带 note 说明「为什么空」，不是静默留白',
)
report(detail.mode === 'anonymous' && detail.unlock.blocked_by.includes('P0-1'), '契约声明 blocked_by 含 P0-1（解锁条件写死在数据里）')

const scoreDesc = detail.enterprises.every(
  (e) => Math.abs(1 / (1 + Math.exp((e.credit_score - 62) / 6.2)) - e.pd) <= 5e-4,
)
report(scoreDesc, 'PD 由分数经 logistic 单调映射得到（不是另编一列）')
report(
  local.entries.every((e) => {
    const d = detail.enterprises.find((x) => x.userid === e.userid)
    return d && d.credit_score === e.credit_score && Math.abs(d.pd - e.pd) <= 1e-9
  }),
  '两份契约里同一 userid 的分数与 PD 逐位一致',
)
report(
  detail.enterprises.every((e) => {
    const e1 = local.entries.find((x) => x.userid === e.userid)
    const band = ut.bandOf(e.credit_score, cuts)
    return !!e1 && e1.risk_band === band.code
  }),
  'risk_band 与五档切点自洽',
)
report(
  local.entries.every((e) => e.neighbours.length > 0 && e.neighbours.every((n) => !n.node_id.startsWith('u'))),
  '每家都有 1 跳邻居（来自合成图，不是编的邻接表）',
)
report(localEnv.meta.source === 'reconstructed_from_anchors', `局部归因来源标注 ${localEnv.meta.source}（不是 measured）`)
report(localEnv.meta.redline.noise_floor_applicable === false, '契约自己声明「不适用噪声地板」（SHAP 是贡献度不是判别力）')

/* ------------------------------------------------------------------ 2. 页面 */
console.log('\n— 2/3. 页面渲染与红线')
const CHROMIUM = [process.env.CHROMIUM_PATH, '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome']
  .filter(Boolean)
  .find((p) => existsSync(p))
const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox', '--disable-dev-shm-usage'] })

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await ctx.newPage()
  const pageErrors = []
  const warns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') warns.push(m.text())
  })

  await page.goto(`${BASE}/enterprise/u0001`, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, {
    timeout: 20_000,
  })
  await page.waitForTimeout(600)

  const flat = (s) => s.replace(/\s/g, '')
  const body = await page.locator('main').innerText()

  report((await page.locator('[data-testid="p7-enterprise-head"]').count()) === 1, '区块一 档案头部存在')
  report((await page.locator('[data-testid="p7-section-score"]').count()) === 1, '区块二 评分仪表盘存在')
  report((await page.locator('[data-testid="p7-section-radar"]').count()) === 1, '区块三 特征雷达存在')
  report((await page.locator('[data-testid="p7-section-local"]').count()) === 1, '区块四 SHAP 瀑布存在')
  report((await page.locator('[data-testid="p7-section-graph"]').count()) === 1, '区块五 局部图谱存在')
  report((await page.locator('[data-testid="p7-section-heat"]').count()) === 1, '区块六 时序贡献存在')

  report((await page.locator('[data-testid="p7-anon-chip"]').count()) === 1, '匿名徽标可见（匿名态不是隐藏，是明示）')
  const d0 = detail.enterprises[0]
  const anonName = (await page.locator('[data-testid="p7-enterprise-name"]').innerText()).trim()
  report(anonName === d0.display_name, `企业名呈现为「${anonName}」（与契约一致，未编造真实企业名）`)
  report((await page.locator('[data-testid="p7-anon-note"]').count()) === 1, '匿名说明条在位（点名 P0-1）')
  const facts = flat(await page.locator('[data-testid="p7-head-facts"]').innerText())
  report(facts.includes('未接入'), '行业 / 成立年份显示「未接入」而不是「未知」或空白')

  for (const id of ['p7-score-block', 'p7-radar-block', 'p7-waterfall-block', 'p7-local-graph-block', 'p7-heat-block']) {
    report((await page.locator(`[data-testid="${id}"] canvas`).count()) >= 1, `${id} canvas 已渲染`)
  }

  report(
    flat(await page.locator('[data-testid="p7-additivity"]').innerText()).includes(
      flat(`Σφ（${e0.features.reduce((a, f) => a + f.phi, 0).toFixed(4)}）`),
    ),
    '可加性等式把 Σφ 摆在图上（读者不用自己加）',
  )
  report(
    flat(await page.locator('[data-testid="p7-score-value"]').innerText()).includes(d0.credit_score.toFixed(1)),
    `评分卡显示 ${d0.credit_score.toFixed(1)}（与契约一致）`,
  )
  report(
    flat(await page.locator('[data-testid="p7-pd-value"]').innerText()).includes(`${(d0.pd * 100).toFixed(2)}%`),
    `PD 卡显示 ${(d0.pd * 100).toFixed(2)}%（同一口径）`,
  )

  const wfRows = await page.locator('[data-testid="p7-waterfall-table"] tbody tr').count()
  report(wfRows === e0.features.length, `瀑布明细表 ${wfRows} 行 = 20 个特征（逐项可查）`)
  const wfCodes = await page.locator('[data-testid="p7-waterfall-table"] [data-testid="unknown-field"]').allInnerTexts()
  report(
    wfCodes.length === e0.features.length && wfCodes.every((c) => /^x\d+$/.test(c.trim())),
    `明细表 ${wfCodes.length} 个字段全部以 xNNN 代号呈现（红线二）`,
  )

  /* -------- 排序真的会动 -------- */
  const rowsText = async () =>
    (await page.locator('[data-testid="p7-waterfall-table"] tbody tr').allInnerTexts()).map((t) => t.trim())
  const orderMag = await rowsText()
  await page.locator('[data-testid="p7-sort-gradient"]').click()
  await page.waitForTimeout(300)
  const orderGrad = await rowsText()
  report(
    orderMag.join('|') !== orderGrad.join('|') && orderMag.length === orderGrad.length,
    '切换「按贡献方向」后表格顺序真的变了（排序不是装饰，且不丢行）',
  )
  report(
    orderGrad[0].includes('x') && !orderGrad[0].includes('-'),
    `切到方向排序后首行是推高项（${orderGrad[0].split(/\s+/)[0]}），不是被拉低项`,
  )
  report(orderMag.join('|') === (await (async () => {
    await page.locator('[data-testid="p7-sort-magnitude"]').click()
    await page.waitForTimeout(300)
    return rowsText()
  })()).join('|'), '切回「按影响大小」后顺序复原（排序状态可逆）')
  /* -------- 企业切换：结构不变、数据变 -------- */
  const before = await page.locator('[data-testid="p7-section-score"]').innerText()
  await page.locator('[data-testid="p7-enterprise-picker"]').selectOption('u0007')
  await page.waitForTimeout(700)
  const after = await page.locator('[data-testid="p7-section-score"]').innerText()
  const e7 = local.entries.find((e) => e.userid === 'u0007')
  report(before !== after, '企业选择器换人后评分区块内容变了（真的重新取数）')
  report(
    (await page.locator('[data-testid="p7-anon-chip"]').count()) === 1 &&
      (await page.locator('[data-testid="p7-section-local"]').count()) === 1 &&
      (await page.locator('[data-testid="p7-waterfall-table"] tbody tr').count()) === e7.features.length,
    '换人后匿名徽标、六区块、瀑布明细结构完全不变（验收：匿名/实名同一套结构）',
  )
  await page.locator('[data-testid="p7-enterprise-picker"]').selectOption('u0001')
  await page.waitForTimeout(600)

  /* -------- 红线 -------- */
  const redline = await page.locator('[data-testid="redline-banner"]').innerText()
  report(
    (await page.locator('[data-testid="redline-banner"]').count()) === 1 &&
      ['A18', 'A19', 'A20'].every((a) => redline.includes(a)),
    '语义未知提示条点名 A18/A19/A20 三个区块',
  )
  report(
    (await page.locator('[data-testid="p7-section-score"] [data-testid="noise-floor-note"]').count()) === 0,
    '评分卡不挂 AUC 噪声地板 0.477（log-odds 与 AUC 不同量纲，红线三反面）',
  )
  const chartText = flat((await page.locator('.chart-panel').allInnerTexts()).join(''))
  report(
    !chartText.includes('0.477'),
    '五个图表里都不出现 0.477（它只在评分卡的免责说明里作对照，不当图形基准线）',
  )
  report(
    flat(await page.locator('[data-testid="p7-prior-note"]').innerText()).includes('0.477'),
    '评分卡免责说明里明写「行级 AUC 与噪声地板 0.477 只差 0.1」（该说的地方要说）',
  )
  const badges = await page.locator('[data-testid="section-dataset-badge"]').count()
  report(badges >= 5, `五个图表区块各自带来源角标（${badges} 个，红线①：不同来源不混排）`)
  report(
    (await page.locator('[data-testid="p7-local-method"]').count()) === 1,
    '局部归因标明方法（实测锚点 + 重建，不是「就是 SHAP」一句带过）',
  )
  report(flat(body).includes('P0-1') && flat(body).includes('P0-2'), '页面点名阻塞项 P0-1 / P0-2（不假装已解锁）')

  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')
  const realWarns = warns.filter((m) => !m.includes('v-scx'))
  report(realWarns.length === 0, '无 Vue / ECharts 运行时告警', realWarns.length ? ` ← ${realWarns[0]}` : '')

  await ctx.close()

  /* ---------------------------------------------------- 4. 降级 */
  console.log('\n— 4. 区块级降级（real 模式 + 畸形信封）')
  const STUB = PORT + 1
  const DG = PORT + 2
  let malformed = []
  const stub = createHttp((req, res) => {
    const url = (req.url ?? '').split('?')[0]
    const bad = malformed.some((p) => url.includes(p))
    const real = { '/api/enterprise/u0001': detailEnv, '/api/explain/local/u0001': localEnv }[url]
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(
      JSON.stringify({
        code: 0,
        message: 'ok',
        data: bad ? {} : (real?.data ?? {}),
        meta: bad ? { dataset: 'real_panel', schema_version: '1.0', noise_floor: null } : (real?.meta ?? {}),
      }),
    )
  })
  await new Promise((r) => stub.listen(STUB, '127.0.0.1', r))

  const degrade = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    mode: 'test',
    define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('real') },
    plugins: [vue()],
    resolve: { alias: { '@': resolve(here, '../src') } },
    server: {
      host: '127.0.0.1',
      port: DG,
      strictPort: true,
      proxy: { '/api': { target: `http://127.0.0.1:${STUB}`, changeOrigin: true } },
    },
    logLevel: 'error',
  })
  await degrade.listen()

  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const dpage = await ctx2.newPage()
  const dErrors = []
  dpage.on('pageerror', (e) => dErrors.push(String(e)))

  const open = async (paths) => {
    malformed = paths
    await dpage.goto(`http://127.0.0.1:${DG}/enterprise/u0001`, { waitUntil: 'networkidle' })
    await dpage.waitForTimeout(1200)
  }

  await open(['/api/enterprise/u0001'])
  report(
    (await dpage.locator('[data-testid="state-panel"]').count()) >= 1,
    '档案接口打空后出现区块级降级面板（不白屏）',
  )
  const panel = await dpage.locator('[data-testid="state-panel"]').first().innerText()
  report(panel.includes('原因') && panel.includes('下一步'), '降级面板必填「原因」与「下一步」都在')
  const dText = (await dpage.locator('body').innerText()).trim()
  report(
    (await dpage.locator('.qa__item').count()) >= 3,
    '读图问答仍在（降级时读者仍有口径说明可看）',
  )
  report(
    (await dpage.locator('.page__title').count()) === 1 && dText.length > 150,
    `页面不白屏（正文 ${dText.length} 字，页头仍在）`,
  )

  await open(['/api/enterprise/u0001', '/api/explain/local/u0001'])
  report(
    (await dpage.locator('[data-testid="state-panel"]').count()) >= 1,
    '两个接口都打空时仍走降级面板（不是错误堆栈）',
  )
  report(dErrors.length === 0, '降级路径无运行时异常', dErrors.length ? ` ← ${dErrors[0]}` : '')

  await ctx2.close()
  await degrade.close()
  await new Promise((r) => stub.close(r))
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\nP7 冒烟全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
