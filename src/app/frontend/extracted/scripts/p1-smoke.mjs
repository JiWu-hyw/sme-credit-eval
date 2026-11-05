/**
 * P1 概览页冒烟测试
 *
 * 这页最怕的不是「打不开」，而是**数字对不上**：概览抄了一遍 P4 的 AUC、
 * P5 的解释占比、P2 的中位月数，任何一个源契约改了而概览没跟上，
 * 就会出现「两页看着矛盾」——这种错误在截图评审里极难发现。
 *
 * 所以核心断言是**交叉核对**：把概览上显示的每个 KPI，
 * 与它声明的 source 契约里的对应值逐字比对。
 *
 * 断言分四组：
 *   1. 结构：结论块、4 个 KPI、6 步链路、3 条缺口、7 个阶段格子；
 *   2. 交叉核对：概览的每个数字 == 源契约的数字；
 *   3. 红线：语义未知字段只显代号、真实面板带噪声地板；
 *   4. 交互与健康度：阶段格子里的链接可跳、无异常无告警、后端不可用不白屏。
 *
 * 用法：npm run test:p1
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const contract = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const overview = contract('overview').data
const benchmark = contract('benchmark').data
const shap = contract('shap_global').data
const quality = contract('data_quality').data
const depth = contract('panel_depth').data
const pipeline = contract('data_pipeline').data

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

const PORT = 11560
const BASE = `http://127.0.0.1:${PORT}`
const MOCK_MODE = process.env.P1_MOCK || 'mock'

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  mode: 'test',
  define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify(MOCK_MODE) },
  server: { host: '127.0.0.1', port: PORT, strictPort: true },
  logLevel: 'error',
})
await server.listen()

const CHROMIUM_CANDIDATES = [
  process.env.CHROMIUM_PATH,
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/usr/bin/google-chrome',
].filter(Boolean)
const browser = await chromium.launch({
  executablePath: CHROMIUM_CANDIDATES.find((p) => existsSync(p)),
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await ctx.newPage()

  const pageErrors = []
  const consoleWarns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') consoleWarns.push(m.text())
  })

  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 20_000 },
  )
  const text = await page.locator('main').innerText()
  const flat = text.replace(/\s/g, '')

  // ------------------------------------------------ 1. 结构
  report((await page.locator('#verdict').count()) === 1, 'P1 一句话结论区块存在')
  report(text.includes(overview.headline.text.slice(0, 16)), 'P1 结论文案与契约一致')

  const kpiCards = await page.locator('.kpi').count()
  report(kpiCards === 4, `P1 KPI 卡片 4 张（实际 ${kpiCards}）`)

  const chainItems = await page.locator('.chain__item').count()
  report(chainItems === 6, `P1 方法链路 6 步（实际 ${chainItems}）`)
  report(
    (await page.locator('.chain').count()) === 1 &&
      (await page.locator('.chain__item').first().innerText()).includes('userid'),
    'P1 链路第 1 步是按 userid 分组切分（顺序不能错）',
  )
  // 每一步都得有代码落点，不能是流程图上的空盒子
  const implCount = await page.locator('.chain__impl').count()
  report(implCount === 6, `P1 六步都标了代码落点（实际 ${implCount}）`)

  const gapCards = await page.locator('.gap').count()
  report(gapCards === 3, `P1 数据缺口 3 条（实际 ${gapCards}）`)
  report(
    text.includes('P0-1') && text.includes('P0-2') && text.includes('P0-3'),
    'P1 三条缺口都有编号（可与任务清单对齐）',
  )

  const stageCells = await page.locator('.stage').count()
  report(stageCells === 7, `P1 交付进度 7 格（实际 ${stageCells}）`)
  const delivered = await page.locator('.stage[data-state="delivered"]').count()
  report(
    delivered === overview.stage_progress.summary.delivered,
    `P1 已交付格数 = ${delivered}（契约 ${overview.stage_progress.summary.delivered}）`,
  )

  // ------------------------------------------------ 2. 交叉核对（本脚本的重点）
  const rowLr = benchmark.levels.find((l) => l.level_id === 'row').models.find((m) => m.model_id === 'lr')
  const compXgb = benchmark.levels
    .find((l) => l.level_id === 'company')
    .models.find((m) => m.model_id === 'xgb')

  /**
   * 必须取**第 i 张 KPI 卡片上的值**去比对，不能用整页文本 includes。
   * 第一版就是 includes：把概览里行级 AUC 改成 0.612 之后测试照样全绿——
   * 因为 P4 的 0.596 还出现在别的句子里（「噪声地板差 0.119」那段注释）。
   * 那就是一条假测试，等于没写。
   */
  const kpiValue = async (idx) => (await page.locator('.kpi').nth(idx).locator('.card__value').innerText())
    .replace(/\s/g, '')

  const kpiChecks = [
    [0, '行级 AUC', rowLr.metrics_mean.auc.toFixed(3), 'P4 levels[row].lr.metrics_mean.auc'],
    [1, '企业级 AUC', compXgb.metrics_mean.auc.toFixed(3), 'P4 levels[company].xgb.metrics_mean.auc'],
    [2, '解释占比', `${((1 - shap.models[0].unknown_field_share) * 100).toFixed(1)}%`, 'P5 unknown_field_share 互补'],
    [3, '中位月数', `${depth.coverage.median_months}个月`, 'P2 panel_depth.coverage.median_months'],
  ]
  for (const [idx, name, expected, src] of kpiChecks) {
    const actual = await kpiValue(idx)
    report(actual.startsWith(expected), `概览 KPI「${name}」= ${actual}，与 ${src} 一致`)
  }

  // 差值也取卡片内的 hint，而不是整页文本
  const aucHint = await page.locator('.kpi').nth(0).locator('.card__hint').innerText()
  const gap = (rowLr.metrics_mean.auc - benchmark.noise_floor.value).toFixed(3)
  report(aucHint.includes(gap), `概览行级 AUC 卡片注明「高出 ${gap}」，与 P4 算式一致`)

  report(
    shap.models.every((m) => m.top.length === 20) && overview.headline.subtext.includes('Top-20'),
    '概览自称「Top-20 里没有一个语义已知字段」，与 P5 实际 top 长度 20 相符',
  )

  // 缺口描述里引用的数字也必须对得上
  report(
    flat.includes(`${(quality.summary.mean_missing_rate * 100).toFixed(1)}%`),
    `概览链路第 2 步「平均缺失率 ${(quality.summary.mean_missing_rate * 100).toFixed(1)}%」与 P2 一致`,
  )
  report(
    flat.includes(depth.coverage.enterprises_with_panel.toLocaleString('en-US')) ||
      flat.includes(String(depth.coverage.enterprises_with_panel)),
    `概览「${depth.coverage.enterprises_with_panel} 家企业」与 P2 一致`,
  )

  // 链路状态必须与真实链路契约一致（不能概览说就绪、P2 说阻断）
  const pipelineStates = overview.method_chain.steps.map((s) => s.status)
  report(
    pipelineStates.includes('pending') && pipelineStates.includes('synthetic_only'),
    '概览链路如实标了 pending / synthetic_only，没有把未接入的步骤画成已完成',
  )
  report(
    pipeline.nodes.some((n) => n.status === 'blocked'),
    'P2 链路里确实存在 blocked 节点（概览的「被阻断」说法有据）',
  )

  // ------------------------------------------------ 3. 红线
  report(text.includes('0.477'), 'P1 真实面板带噪声地板 0.477（红线三）')
  report(!text.includes('0.9149'), 'P1 不出现合成图 0.9149（红线四）')
  const codes = text.match(/\bx\d{2,3}\b/g) ?? []
  report(codes.length >= 1, `P1 语义未知字段以 xNNN 代号出现（${codes.length} 处）`)

  // ------------------------------------------------ 4. 交互与健康度
  const p2Link = page.locator('.stage[data-state="delivered"] a', { hasText: '数据与特征' })
  report((await p2Link.count()) === 1, 'P1 交付进度里的已交付格子是可点链接')
  await p2Link.click()
  await page.waitForURL(`${BASE}/data`, { timeout: 5000 })
  report(page.url().endsWith('/data'), 'P1 → P2 跳转生效')

  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')
  const realWarns = consoleWarns.filter((m) => !m.includes('v-scx'))
  report(realWarns.length === 0, '无 Vue 运行时告警', realWarns.length ? ` ← ${realWarns[0]}` : '')

  // 后端不可用
  const offline = await ctx.newPage()
  await offline.route('**/api/**', (route) => route.abort())
  await offline.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await offline.waitForTimeout(2000)
  const off = await offline.locator('main').innerText()
  const degraded = off.includes('一句话结论')
  const shownError = off.includes('原因') && off.includes('下一步')
  report(
    (degraded || shownError) && off.replace(/\s/g, '').length > 40,
    'P1 后端不可用时不白屏（降级 mock 或给「原因 + 下一步」）',
    degraded ? '（已降级 mock）' : shownError ? '（已给错误态）' : '',
  )
  await offline.close()

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
