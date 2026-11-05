/**
 * P6 前瞻预测 冒烟测试（FE-15）
 *
 * 本页两条验收是「曲线含置信区间」与「缺契约时区块级降级不白屏」。
 * 这两条都**不能只靠肉眼看图**——带子可以是空的、降级可以压根没触发。
 * 所以这支脚本的重点是「拿契约里的数去对数」+「真造一次契约缺失」：
 *
 *   0. 纯函数：survivalAt / atRiskAt / bandPolygon / calibrationPoints / formatP
 *      —— 图表读的就是它们，先证明它们对得上契约里的 s12/s24/at-risk。
 *   1. 契约自洽：CI 必须包含点估计且单调不升、Greenwood 下界 ≥ 0、
 *      log-rank/Cox/校准各项在合理域内（这些是「估计器写对了」的最小证据）。
 *   2. A16 渲染：区块、角标、KM canvas、三条曲线、置信带开关真的增减 custom series、
 *      分层 chip 真的过滤、Cox 表行数与契约一致、6 个字段全是 xNNN。
 *   3. A17 渲染：指标卡数字与契约逐项相等、校准图 10 个分箱、估值条 8 条、表格数值一致。
 *   4. 红线：两张图都不挂 AUC 噪声地板（0.477 与生存率/概率不同量纲）。
 *   5. 降级：用 stub 后端（real 模式）只打空 A16 → A16 降级、A17 照常；
 *      两块都打空 → 页头/降级说明/读图问答仍在，不白屏。
 *   6. 健康度：无 pageerror、无 Vue/ECharts 告警。
 *
 * 用法：npm run test:p6
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

const survEnv = contract('survival')
const surv = survEnv.data
const finEnv = contract('financing')
const fin = finEnv.data

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps

const PORT = 11586
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
const fc = await server.ssrLoadModule('/src/utils/forecast.ts')

const lowCurve = surv.curves.find((c) => c.stratum_id === 'low')
const lowStratum = surv.strata.find((s) => s.stratum_id === 'low')

const at12 = fc.survivalAt(lowCurve.points, 12)
report(!!at12 && near(at12.s, lowStratum.s12, 2e-5), `低风险层 S(12) 查表命中（${at12?.s.toFixed(5)}）`)
const at24 = fc.survivalAt(lowCurve.points, 24)
report(!!at24 && near(at24.s, lowStratum.s24, 2e-5), `低风险层 S(24) 查表命中（${at24?.s.toFixed(5)}）`)
report(
  !!at24 && near(at24.lo, lowStratum.s24_ci[0], 2e-5) && near(at24.hi, lowStratum.s24_ci[1], 2e-5),
  'S(t) 的置信上下界与契约 s24_ci 逐位一致',
)
report(fc.survivalAt(lowCurve.points, 6.5) !== null, '非网格月份（6.5）也能取到阶跃值（step 语义）')
report(fc.atRiskAt(lowCurve.points, 0) === lowCurve.points[0][4], 'at-risk(t=0) = 该层入组人数')
report(fc.atRiskAt(lowCurve.points, 24) <= fc.atRiskAt(lowCurve.points, 0), 'at-risk 随随访单调不增')

const poly = fc.bandPolygon(lowCurve.points)
report(poly.length === lowCurve.points.length * 2, `置信带多边形 = 2×点数（${poly.length}）`)
report(
  poly.slice(0, lowCurve.points.length).every((p, i) => p[0] === lowCurve.points[i][0]) &&
    poly.slice(0, lowCurve.points.length).every((p, i) => p[1] === lowCurve.points[i][2]),
  '多边形前半段沿下界正向走',
)

report(fc.formatP(2.23e-7) === 'p < 0.001', 'p 值小于 0.001 时渲染为「p < 0.001」（不是 p = 0.000）')
report(fc.formatP(0.00203) === 'p = 0.002', `p 值 0.00203 渲染为 ${fc.formatP(0.00203)}`)
report(fc.formatP(0.31) === 'p = 0.310', `p 值 0.31 渲染为 ${fc.formatP(0.31)}`)

const calPts = fc.calibrationPoints(fin.calibration.bins)
report(calPts.length === fin.calibration.bins.length, `校准点 = 分箱数（${calPts.length}）`)
report(
  calPts.every((p, i) => p[0] === fin.calibration.bins[i].p_mean && p[4] === fin.calibration.bins[i].n),
  '校准点 [预测均值, 观测, 下界, 上界, n] 与契约逐箱一致',
)
report(calPts.every((p) => p[3] >= p[1] && p[1] >= p[2]), '每个分箱 Wilson 区间都包含观测率')
report(fc.intervalWidth(fin.valuation.bars[0]) > 0, '估值区间宽度 > 0')

/* --------------------------------------------------------- 1. 契约自洽（估计器自证） */
console.log('\n— 1. 契约自洽（估计器自证，不证明数据真实）')
const ciMonotone = surv.curves.every((c) =>
  c.points.every((p, i) => i === 0 || p[1] <= c.points[i - 1][1] + 1e-12),
)
report(ciMonotone, 'KM 曲线逐层单调不升（乘积极限估计的必要条件）')
const ciContains = surv.curves.every((c) =>
  c.points.every((p) => p[2] <= p[1] + 1e-12 && p[1] <= p[3] + 1e-12),
)
report(ciContains, '每个时间点的置信区间都包含点估计')
const ciBounded = surv.curves.every((c) => c.points.every((p) => p[2] >= -1e-12 && p[3] <= 1 + 1e-12))
report(ciBounded, '置信区间落在 [0,1]（Greenwood 线性近似可能越界，这里未越界）')
report(
  surv.strata.every((s) => s.s24_ci[1] - s.s24_ci[0] > 0),
  '三层 S(24) 区间宽度均 > 0（低风险层没退化成 [1,1]）',
  `　最窄 ${Math.min(...surv.strata.map((s) => (s.s24_ci[1] - s.s24_ci[0]) * 100)).toFixed(2)}pp`,
)
report(
  surv.strata.every((s) => s.n > 0 && s.events >= 0),
  '每层样本量与事件数有效',
)
report(
  new Set(surv.strata.map((s) => s.s24)).size === 3 &&
    surv.strata[0].s24 > surv.strata[1].s24 &&
    surv.strata[1].s24 > surv.strata[2].s24,
  '三层 S(24) 严格递减（低 > 中 > 高）',
)
report(surv.logrank.p < 1e-6 && surv.logrank.df === surv.strata.length - 1, 'log-rank p 极小且 df = 层数-1')
report(
  surv.cox.covariates.every((c) => near(c.hr, Math.exp(c.coef), 1e-3)),
  'Cox 表 HR = exp(β)（逐行核对）',
)
report(
  surv.cox.covariates.every((c) => c.hr_lo < c.hr && c.hr < c.hr_hi),
  '每行 HR 的 95%CI 都包含点估计',
)
report(
  surv.cox.covariates.every((c) => (c.coef > 0) === (c.hr > 1)),
  'β 符号与 HR 方向一致（>1 为风险因子）',
)
report(surv.cox.concordance > 0.5 && surv.cox.concordance < 1, `Harrell C = ${surv.cox.concordance}（0.5–1 之间）`)
report(
  surv.cox.max_recovery_deviation < 0.35,
  `拟合 β 与生成 β 的最大偏差 ${surv.cox.max_recovery_deviation.toFixed(4)}（反演自校验）`,
)

report(fin.calibration.bins.reduce((n, b) => n + b.n, 0) === fin.calibration.n, '十分位分箱样本合计 = 校准样本量')
report(
  fin.calibration.bins.every((b) => b.obs_lo <= b.obs && b.obs <= b.obs_hi),
  '每箱观测率都落在自身 Wilson 区间内',
)
report(
  fin.calibration.bins.every((b, i) => i === 0 || b.p_mean >= fin.calibration.bins[i - 1].p_mean),
  '分箱按预测概率升序（否则曲线会自交）',
)
report(fin.calibration.brier < fin.calibration.brier_baseline, 'Brier 低于「恒用总体发生率」基准（有增量信息）')
report(
  Math.abs(fin.calibration.reliability_slope - 1) < 0.35 && fin.calibration.ece < 0.08,
  `可靠性斜率 ${fin.calibration.reliability_slope} · ECE ${fin.calibration.ece}`,
)
report(
  fin.valuation.bars.every((b) => 0 < b.p10 && b.p10 < b.p50 && b.p50 < b.p90),
  '每条估值区间严格 P10 < P50 < P90',
)
report(
  fin.valuation.bars.every((b) => near(b.width_ratio, b.p90 / b.p10, 0.01)),
  'width_ratio = P90 / P10（逐条核对）',
)

/* ------------------------------------------------------------------ 2. 浏览器 */
console.log('\n— 2/3/4. 页面渲染与红线')
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

  await page.goto(`${BASE}/forecast`, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, {
    timeout: 20_000,
  })
  await page.waitForTimeout(500)

  const body = await page.locator('main').innerText()
  const flat = (s) => s.replace(/\s/g, '')

  /* -------- A16 结构 -------- */
  report((await page.locator('[data-testid="section-a16"]').count()) === 1, 'A16 区块存在')
  report((await page.locator('[data-testid="section-a17"]').count()) === 1, 'A17 区块存在')
  const a16Badge = page.locator('[data-testid="section-a16"] [data-testid="section-dataset-badge"]')
  const a17Badge = page.locator('[data-testid="section-a17"] [data-testid="section-dataset-badge"]')
  report((await a16Badge.count()) === 1 && (await a17Badge.count()) === 1, '两个区块各有独立来源角标')
  report(
    (await a16Badge.getAttribute('data-source')) === survEnv.meta.source,
    `A16 角标 source = ${survEnv.meta.source}（与契约一致）`,
  )
  report(
    (await a17Badge.getAttribute('data-source')) === finEnv.meta.source,
    `A17 角标 source = ${finEnv.meta.source}（与契约一致）`,
  )
  report((await page.locator('#a16-km canvas').count()) >= 1, 'KM 图 canvas 已渲染')
  report((await page.locator('#a17-calibration canvas').count()) >= 1, '校准图 canvas 已渲染')
  report((await page.locator('#a17-valuation canvas').count()) >= 1, '估值区间图 canvas 已渲染')

  /* -------- A16 置信区间（验收①） -------- */
  report(
    (await page.locator('[data-testid="survival-summary"] [data-testid="survival-ci"]').count()) >= 2,
    '摘要卡里 S(12)/S(24) 各自带 95% 置信区间（验收①）',
  )
  const atRiskRows = await page.locator('[data-testid="survival-at-risk"] tbody tr').count()
  report(atRiskRows === surv.strata.length, `风险集表 ${atRiskRows} 行 = 层数（暴露分母在缩）`)

  const coxRows = await page.locator('[data-testid="survival-cox"] tbody tr').count()
  report(coxRows === surv.cox.covariates.length, `Cox 表 ${coxRows} 行 = 契约协变量数`)
  const coxCodes = await page.locator('[data-testid="survival-cox"] [data-testid="unknown-field"]').allInnerTexts()
  report(
    coxCodes.length === surv.cox.covariates.length && coxCodes.every((c) => /^x\d+$/.test(c.trim())),
    `Cox 协变量 ${coxCodes.length} 个全部以 xNNN 代号呈现（红线二）`,
  )
  report(
    flat(await page.locator('[data-testid="survival-cox"]').innerText()).includes('p<0.001'),
    'Cox 表把极小 p 值渲染为「p < 0.001」（不是 p = 0.000）',
  )

  /* -------- 交互：分层 chip -------- */
  const chipHigh = page.locator('[data-testid="survival-stratum-chip-high"]')
  report((await chipHigh.count()) === 1, '高风险层 chip 存在')
  await chipHigh.click()
  await page.waitForTimeout(350)
  const summaryHigh = await page.locator('[data-testid="survival-summary"]').innerText()
  report(
    flat(summaryHigh).includes(flat(`${(surv.strata[2].s24 * 100).toFixed(1)}%`)),
    `切到高风险层后摘要卡显示 S(24)=${(surv.strata[2].s24 * 100).toFixed(1)}%（真的换了数）`,
  )
  await page.locator('[data-testid="survival-stratum-chip-all"]').click()
  await page.waitForTimeout(300)

  /* -------- A17 数字对账 -------- */
  const calBody = flat(await page.locator('[data-testid="section-a17"]').innerText())
  report(calBody.includes(fin.calibration.brier.toFixed(3)), `Brier ${fin.calibration.brier.toFixed(3)} 上屏`)
  report(calBody.includes(fin.calibration.ece.toFixed(3)), `ECE ${fin.calibration.ece.toFixed(3)} 上屏`)
  report(calBody.includes(fin.calibration.auc.toFixed(3)), `AUC ${fin.calibration.auc.toFixed(3)} 上屏`)
  const binRows = await page.locator('[data-testid="calibration-bins"] tbody tr').count()
  report(binRows === fin.calibration.bins.length, `分箱明细 ${binRows} 行 = 10 个等频十分位`)
  const valRows = await page.locator('[data-testid="valuation-table"] tbody tr').count()
  report(valRows === fin.valuation.bars.length, `估值表 ${valRows} 行 = 契约 ${fin.valuation.bars.length} 条`)
  const top = fin.valuation.bars.reduce((a, b) => (b.p50 > a.p50 ? b : a))
  report(
    flat(await page.locator('[data-testid="valuation-table"]').innerText()).includes(top.p50.toLocaleString('zh-CN')),
    `P50 最大值 ${top.p50} 出现在表里（${top.entity}）`,
  )

  /* -------- 红线 -------- */
  const noiseNotes = await page.locator('[data-testid="section-a16"] [data-testid="noise-floor-note"]').count()
  const noiseNotes17 = await page.locator('[data-testid="section-a17"] [data-testid="noise-floor-note"]').count()
  report(noiseNotes === 0 && noiseNotes17 === 0, '两张图都不挂 AUC 噪声地板 0.477（量纲不同，红线三反面）')
  report(
    (await page.locator('[data-testid="redline-banner"]').count()) === 1 &&
      flat(await page.locator('[data-testid="redline-banner"]').innerText()).includes('A16'),
    '语义未知提示条存在并点名 A16',
  )
  report(
    (await page.locator('[data-testid="forecast-mock-strip"]').count()) === 1 &&
      flat(await page.locator('[data-testid="forecast-mock-strip"]').innerText()).includes('P0-3'),
    'mock 声明条点名 P0-3（占位数据摆在最上面）',
  )
  report(
    body.includes('现场算') && body.includes('合成'),
    '页面明说「队列合成、统计量现场算」两件事分开讲',
  )

  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')
  const realWarns = warns.filter((m) => !m.includes('v-scx'))
  report(realWarns.length === 0, '无 Vue / ECharts 运行时告警', realWarns.length ? ` ← ${realWarns[0]}` : '')

  await ctx.close()

  /* ---------------------------------------------------- 5. 区块级降级（验收②） */
  console.log('\n— 5. 区块级降级（real 模式 + 畸形信封，验收②）')
  const STUB = PORT + 1
  const DG = PORT + 2
  let malformed = []
  const stub = createHttp((req, res) => {
    const url = (req.url ?? '').split('?')[0]
    const bad = malformed.some((p) => url.includes(p))
    const real = { '/api/forecast/survival': survEnv, '/api/forecast/financing': finEnv }[url]
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
    server: { host: '127.0.0.1', port: DG, strictPort: true, proxy: { '/api': { target: `http://127.0.0.1:${STUB}`, changeOrigin: true } } },
    logLevel: 'error',
  })
  await degrade.listen()

  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const dpage = await ctx2.newPage()
  const dErrors = []
  dpage.on('pageerror', (e) => dErrors.push(String(e)))

  const open = async (paths) => {
    malformed = paths
    await dpage.goto(`http://127.0.0.1:${DG}/forecast`, { waitUntil: 'networkidle' })
    await dpage.waitForTimeout(1200)
  }

  await open(['/api/forecast/survival'])
  report((await dpage.locator('#a16-km').count()) === 0, 'A16 打空后 KM 图撤掉（不画半截图）')
  report(
    (await dpage.locator('[data-testid="section-a16"] [data-testid="state-panel"]').count()) === 1,
    'A16 出现区块级降级面板（原因 + 下一步）',
  )
  const a16PanelText = await dpage.locator('[data-testid="section-a16"] [data-testid="state-panel"]').innerText()
  report(
    a16PanelText.includes('原因') && a16PanelText.includes('下一步'),
    '降级面板必填「原因」与「下一步」都在',
  )
  report((await dpage.locator('#a17-calibration').count()) === 1, 'A17 校准图不受 A16 影响（区块级隔离）')
  report(
    (await dpage.locator('[data-testid="section-a17"] [data-testid="state-panel"]').count()) === 0,
    'A17 没有降级面板',
  )

  await open(['/api/forecast/survival', '/api/forecast/financing'])
  const dText = (await dpage.locator('body').innerText()).trim()
  report(
    (await dpage.locator('[data-testid="state-panel"]').count()) === 2,
    '两块都打空时出现 2 个降级面板',
  )
  report(
    (await dpage.locator('.page__title').count()) === 1 && dText.length > 200,
    `页面不白屏（正文 ${dText.length} 字，页头仍在）`,
  )
  report(
    (await dpage.locator('.qa__item').count()) >= 3,
    '读图问答仍在（降级时读者仍有口径说明可看）',
  )
  report(dErrors.length === 0, '降级路径无运行时异常', dErrors.length ? ` ← ${dErrors[0]}` : '')

  await ctx2.close()
  await degrade.close()
  await new Promise((r) => stub.close(r))
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\nP6 冒烟全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
