/**
 * P4 模型与对比 / P5 可解释性 冒烟测试
 *
 * 为什么要单独一个真浏览器脚本：
 *   这两页的正文全部依赖异步接口，`renderToString` 只拿得到骨架（见
 *   scripts/route-smoke.mjs 顶部的说明）。能证明「页面真有内容」的只有浏览器。
 *
 * 断言分六组：
 *   1. 正文落地：骨架屏消失、结论块出现、两个模型 / 三条水平线都在；
 *   2. 数值对契约：页面上的 AUC / 噪声地板必须与 outputs/contracts 一字不差
 *      （防止有人把实测数改回「好看的」占位值）；
 *   3. 四区块 + 区块级角标（FE-13）：A1–A4 各自存在、各自一颗 section 角标，
 *      dataset / source 必须逐一等于对应契约的 meta（不跨区块复用、不吃缓存）；
 *   4. 红线：真实面板必须带噪声地板 0.477；合成图 0.9149 只许出现在 A1 语境，
 *      且 A2–A4 的正文里一个都不许有；语义未知字段只出现 xNNN 代号；
 *   5. 交互：A1 训练曲线切模型换数、A10 勾选取消模型、全关自动回退；
 *      P5 的模型切换会真的换数（xgb ↔ lr 的 Top-1 字段不同）；
 *   6. 渲染健康度 + 后端不可用不白屏。
 *
 * 用法：npm run test:p4p5
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const contract = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const benchmarkEnv = contract('benchmark')
const benchmark = benchmarkEnv.data
const shap = contract('shap_global').data
const trainCurve = contract('train_curve')
const rocPrKs = contract('roc_pr_ks')
const noiseFloor = contract('noise_floor')

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

const PORT = 11510
const BASE = `http://127.0.0.1:${PORT}`
const MOCK_MODE = process.env.P4P5_MOCK || 'mock'

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

const settle = (page) =>
  page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 20_000 },
  )

/** 去掉所有空白，方便做「数字是否原样出现」的断言 */
const flat = (s) => s.replace(/\s/g, '')

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()

  const pageErrors = []
  const consoleWarns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') consoleWarns.push(m.text())
  })

  // ============================================================ P4 /model
  await page.goto(`${BASE}/model`, { waitUntil: 'networkidle' })
  await settle(page)
  const p4 = await page.locator('main').innerText()
  const p4f = flat(p4)

  report((await page.locator('#verdict').count()) === 1, 'P4 结论先行区块存在')
  report(p4.includes('实测结果'), 'P4 页头标注「实测结果」（不是占位数据）')

  // --- 数值对契约
  const rowLr = benchmark.levels.find((l) => l.level_id === 'row').models.find((m) => m.model_id === 'lr')
  const compXgb = benchmark.levels
    .find((l) => l.level_id === 'company')
    .models.find((m) => m.model_id === 'xgb')
  const gat = benchmark.holdout.models.find((m) => m.model_id === 'gat')
  const mlp = benchmark.holdout.models.find((m) => m.model_id === 'mlp_nograph')
  const gain = gat.metrics.auc - mlp.metrics.auc

  report(p4f.includes(rowLr.metrics_mean.auc.toFixed(3)), `P4 行级 LR AUC 与契约一致（${rowLr.metrics_mean.auc.toFixed(3)}）`)
  report(
    p4f.includes(compXgb.metrics_mean.auc.toFixed(3)),
    `P4 企业级 XGB AUC 与契约一致（${compXgb.metrics_mean.auc.toFixed(3)}）`,
  )
  report(p4f.includes(gain.toFixed(3)), `P4 图增益与契约一致（GAT − MLP = ${gain.toFixed(3)}）`)
  report(
    p4f.includes(String(benchmark.holdout.n_test)) && p4f.includes(String(benchmark.holdout.n_pos)),
    `P4 留出集规模与契约一致（${benchmark.holdout.n_test} / 正例 ${benchmark.holdout.n_pos}）`,
  )

  // --- 留出集明细表：四行模型齐全
  const holdoutRows = await page.locator('table.tbl tbody tr').count()
  report(
    holdoutRows >= benchmark.holdout.models.length,
    `P4 留出集明细表行数 = ${holdoutRows}（契约 ${benchmark.holdout.models.length} 个模型）`,
  )

  // --- 红线：噪声地板必须出现；合成图 0.9149 只许待在 A1 里
  report(p4.includes('0.477'), 'P4 真实面板带噪声地板 0.477（红线三）')
  const datasetTags = await page.locator('[data-testid="chart-panel"]').count()
  report(datasetTags >= 2, `P4 两张图都在 ChartPanel 容器内（共 ${datasetTags} 个）`)

  /** 区块正文（不含区块标题里的角标）—— 用来做"某个数字有没有越界"的断言 */
  const sectionText = async (id) => {
    const loc = page.locator(`[data-testid="section-${id}"]`)
    return (await loc.count()) === 0 ? null : loc.innerText()
  }

  const a1 = await sectionText('a1')
  const a2 = await sectionText('a2')
  const a3 = await sectionText('a3')
  const a4 = await sectionText('a4')
  report(
    [a1, a2, a3, a4].every((t) => t !== null),
    'P4 四区块 A1–A4 全部渲染（各自独立取数，未整页白屏）',
  )

  report(
    (a1 ?? '').includes('0.9149') && (a1 ?? '').includes('合成图'),
    'P4 合成图 AUC 0.9149 出现在 A1 并同屏标注「合成图」（红线四：标注清楚即可提）',
  )
  report(
    [a2, a3, a4].every((t) => !(t ?? '').includes('0.9149')),
    'P4 A2/A3/A4 正文不含 0.9149（红线四：不同数据集不同图）',
  )

  // --- 区块级角标：dataset / source 必须等于各自契约的 meta，逐个字节对账
  const expectBadge = [
    ['A1', 'a1', trainCurve.meta],
    ['A2', 'a2', benchmarkEnv.meta],
    ['A3', 'a3', rocPrKs.meta],
    ['A4', 'a4', noiseFloor.meta],
  ]
  const badgeAll = await page.locator('[data-testid="section-dataset-badge"]').count()
  report(badgeAll === 4, `P4 恰好 4 颗区块级来源角标（实际 ${badgeAll}）`)
  for (const [label, id, meta] of expectBadge) {
    const badge = page.locator(`[data-testid="section-${id}"] [data-testid="section-dataset-badge"]`).first()
    const ds = (await badge.count()) ? await badge.getAttribute('data-dataset') : null
    const sr = (await badge.count()) ? await badge.getAttribute('data-source') : null
    report(
      ds === meta.dataset && sr === meta.source,
      `P4 ${label} 角标 = ${meta.dataset} / ${meta.source}`,
      ds === meta.dataset && sr === meta.source ? '' : `（实际 ${ds} / ${sr}）`,
    )
  }
  const badgeA1 = await page
    .locator('[data-testid="section-a1"] [data-testid="section-dataset-badge"]')
    .first()
    .innerText()
  report(
    badgeA1.includes('不与真实面板对比'),
    'P4 A1 角标带「合成图，不与真实面板对比」永久标记',
  )

  // --- A1 训练曲线：facts 数字对契约
  const gatCurve = trainCurve.data.curves.find((c) => c.model_id === 'gat')
  const a1f = flat(a1 ?? '')
  report(
    a1f.includes(String(gatCurve.best_epoch)) && a1f.includes(gatCurve.best_val_auc.toFixed(4)),
    `P4 A1 默认模型最佳轮 / 最佳 AUC 对契约（epoch ${gatCurve.best_epoch} / ${gatCurve.best_val_auc.toFixed(4)}）`,
  )

  // --- A3：合成 KS 与实测 KS 并列对账（A3 默认选中曲线族里的第一个模型，不是 A1 的 gat）
  const a3DefaultId = rocPrKs.data.series[0].model_id
  const a3Series = rocPrKs.data.series.find((s) => s.model_id === a3DefaultId)
  report(
    flat(a3 ?? '').includes(a3Series.ks.measured.toFixed(4)) &&
      flat(a3 ?? '').includes(a3Series.ks.value.toFixed(4)),
    `P4 A3 合成 / 实测 KS 并列且对契约（${a3Series.model_label}：${a3Series.ks.value.toFixed(4)} / ${a3Series.ks.measured.toFixed(4)}）`,
  )

  // --- A4：地板 + 观察组 + 差额表
  report(
    (await page.locator('[data-testid="noise-floor-block"]').count()) === 1 &&
      (await page.locator('[data-testid="noise-margin-table"]').count()) === 1,
    'P4 A4 噪声地板区块与差额表均存在',
  )
  const floorBar = noiseFloor.data.bars.find((b) => b.bar_id === 'row_lr')
  report(
    flat(a4 ?? '').includes(floorBar.auc.toFixed(4)),
    `P4 A4 观察组 AUC 对契约（行级 LR ${floorBar.auc.toFixed(4)}）`,
  )

  // --- 语义未知字段只出现代号
  const p4Codes = p4.match(/\bx\d{2,3}\b/g) ?? []
  report(
    p4Codes.length >= benchmark.holdout.selected_features.length * 0.9,
    `P4 特征口径以 xNNN 代号展示（${p4Codes.length} 处）`,
  )
  report(!/[\u4e00-\u9fa5]{2,}(率|额|收入|利润|负债)$/.test(p4), 'P4 未出现伪造的业务字段名（红线二）')

  // --- CV 卡片：三级（行 / 企业 / 打乱标签）
  const cvCards = await page.locator('.cv-card').count()
  report(cvCards === benchmark.levels.length, `P4 CV 卡片数 = ${cvCards}（契约 ${benchmark.levels.length} 级）`)
  const controlCards = await page.locator('.cv-card[data-control="true"]').count()
  report(controlCards === 1, `P4 恰好 1 张置换对照卡（实际 ${controlCards}）`)

  // --- 交互：A1 切到另一个模型，facts 必须真的换数
  const otherCurve = trainCurve.data.curves.find((c) => c.model_id !== 'gat')
  await page.locator(`[data-testid="curve-tab-${otherCurve.model_id}"]`).click()
  await page.waitForTimeout(300)
  const factsAfter = flat(await page.locator('[data-testid="curve-facts"]').innerText())
  report(
    factsAfter.includes(otherCurve.best_val_auc.toFixed(4)),
    `P4 A1 切到 ${otherCurve.model_label} 后最佳 AUC 换成 ${otherCurve.best_val_auc.toFixed(4)}`,
  )

  // --- 交互：A10 取消一个模型 → 再取消到全空 → 自动回退全选
  const chips = page.locator('[data-testid^="model-chip-"]')
  const chipCount = await chips.count()
  report(chipCount >= 4, `P4 A10 模型勾选控件 ${chipCount} 个（契约 holdout ${benchmark.holdout.models.length} 个模型）`)
  await page.locator(`[data-testid="model-chip-${otherCurve.model_id}"]`).click()
  await page.waitForTimeout(200)
  const pressedAfterOff = await page
    .locator(`[data-testid="model-chip-${otherCurve.model_id}"]`)
    .getAttribute('aria-pressed')
  report(pressedAfterOff === 'false', 'P4 A10 取消勾选后该模型 chip 变为未选中')

  // 全关：每次点击后 DOM 会重排（Vue 按 selected 顺序重渲 chip），所以**每轮都重新查询**，
  // 不能持有 nth(i) 的 locator 跨点击复用——那会点到"已经变成另一个模型"的按钮上（FE-13 踩过）。
  const offAll = async () => {
    // 先记下"已选集合"，每轮点一个**当前仍在选中**的模型，直到只剩最后一个
    let guard = 0
    while (guard++ < chipCount + 2) {
      const chosen = await page.locator('[data-testid^="model-chip-"][aria-pressed="true"]').count()
      if (chosen <= 1) break
      await page.locator('[data-testid^="model-chip-"][aria-pressed="true"]').last().click()
      await page.waitForTimeout(120)
    }
    // 最后一击：取消唯一剩下的那个 → 触发"全关自动回退"
    await page.locator('[data-testid^="model-chip-"][aria-pressed="true"]').first().click()
    await page.waitForTimeout(300)
  }
  await offAll()
  const pressedNone = await page.locator('[data-testid^="model-chip-"][aria-pressed="true"]').count()
  const fallbackShown = await page.locator('[data-testid="model-fallback"]').count()
  report(
    pressedNone === chipCount && fallbackShown >= 1,
    `P4 A10 全部取消后自动回退全选（${pressedNone}/${chipCount}）并给出提示`,
  )

  // ============================================================ P5 /explain
  await page.goto(`${BASE}/explain`, { waitUntil: 'networkidle' })
  await settle(page)
  let p5 = await page.locator('main').innerText()

  report((await page.locator('#verdict').count()) === 1, 'P5 结论区块存在')
  report(p5.includes(shap.conclusion.slice(0, 12)), 'P5 结论文案与契约一致')
  report(p5.includes('100.0%') || p5.includes('100%'), 'P5 语义未知字段解释占比 100% 已呈现')

  const xgbTop1 = shap.models.find((m) => m.model_id === 'xgb').top[0]
  const lrTop1 = shap.models.find((m) => m.model_id === 'lr').top[0]
  report(p5.includes(xgbTop1.feature), `P5 默认展示 XGBoost 的 Top-1 字段（${xgbTop1.feature}）`)

  // --- 交互：切到 LR，Top-1 必须换人
  const lrTab = page.getByRole('button', { name: /Logistic|LR/ }).first()
  report((await lrTab.count()) === 1, 'P5 模型切换控件存在')
  await lrTab.click()
  await page.waitForTimeout(400)
  p5 = await page.locator('main').innerText()
  report(
    p5.includes(lrTop1.feature) && p5.includes(lrTop1.mean_abs_shap.toFixed(4)),
    `P5 切到 Logistic 后 Top-1 换成 ${lrTop1.feature}（mean|SHAP| ${lrTop1.mean_abs_shap.toFixed(4)}）`,
  )

  // --- Top-20 明细表
  const shapRows = await page.locator('#toplist').locator('xpath=ancestor::section[1]').locator('tbody tr').count()
  report(shapRows >= 20, `P5 Top-20 明细表行数 = ${shapRows}`)

  // --- 红线
  const p5Codes = p5.match(/\bx\d{2,3}\b/g) ?? []
  report(p5Codes.length >= 20, `P5 字段一律 xNNN 代号（${p5Codes.length} 处）`)

  // ---------------------------------------------------------- 健康度
  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')
  const realWarns = consoleWarns.filter((m) => !m.includes('v-scx'))
  report(realWarns.length === 0, '无 Vue 运行时告警', realWarns.length ? ` ← ${realWarns[0]}` : '')

  // ---------------------------------------------------------- 后端不可用
  const offline = await ctx.newPage()
  await offline.route('**/api/**', (route) => route.abort())
  await offline.goto(`${BASE}/model`, { waitUntil: 'domcontentloaded' })
  await offline.waitForTimeout(2500)
  const off = await offline.locator('main').innerText()
  const okOffline =
    (off.includes('结论先行') || off.includes('7') === false) && off.replace(/\s/g, '').length > 40
  const degraded = off.includes('结论先行')
  const shownError = off.includes('原因') && off.includes('下一步')
  report(
    (degraded || shownError) && okOffline,
    'P4 后端不可用时不白屏（降级 mock 或给「原因 + 下一步」）',
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
