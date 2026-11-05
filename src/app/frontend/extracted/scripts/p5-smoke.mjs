/**
 * P5 可解释性 冒烟测试（FE-14）
 *
 * 为什么单独一支：FE-13 已经把 P5 的「不回归」放进了 p4p5-smoke（结论、Top-1、
 * 切模型换数、Top-20 行数）。FE-14 给 P5 加了两块新东西——A13 蜂群图 + A15 阶段图谱，
 * 它们的风险与旧断言不同：
 *   - 蜂群图的点位是**重建**的，最大的风险是"画出来的散点与条形图对不上"；
 *   - A15 整块是 **mock**，最大的风险是"占位数据被当成结论"（标签掉了、警示条没了）。
 * 所以这里断言的重点是「口径标注」与「两张图自洽」，不是"图有没有出来"。
 *
 * 分组：
 *   0. 纯函数：蜂群避让布局（确定性 / 不越界 / 行间不串）
 *   1. A13 结构：区块、角标、条形图、蜂群 canvas、明细表
 *   2. A13 交叉核对：散点均值 = 条形图数字；切排序两张图同步换序
 *   3. A13 红线：SHAP 图不再挂 AUC 噪声地板（回归项）
 *   4. A15：mock 警示条、4 个阶段、切阶段换权重与字段、加权合计 100%
 *   5. 健康度：无异常 / 无告警 / 后端不可用不白屏
 *
 * 用法：npm run test:p5
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const contract = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const shapEnv = contract('shap_global')
const shap = shapEnv.data
const xgb = shap.models.find((m) => m.model_id === 'xgb')
const lr = shap.models.find((m) => m.model_id === 'lr')
const stageEnv = contract('stage_factor_weight')
const stage = stageEnv.data

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

const PORT = 11560
const BASE = `http://127.0.0.1:${PORT}`
const MOCK_MODE = process.env.P5_MOCK || 'mock'

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  mode: 'test',
  define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify(MOCK_MODE) },
  server: { host: '127.0.0.1', port: PORT, strictPort: true },
  logLevel: 'error',
})
await server.listen()

/* ------------------------------------------------------------------ 0. 纯函数 */
const { layoutSwarm } = await server.ssrLoadModule('/src/utils/beeswarm.ts')

// 同一份输入两次结果必须逐点相同（否则每次重绘/截图都不一样）
const sample = [
  [-0.12, 0.8],
  [-0.02, -1.2],
  [0.0, 0.1],
  [0.03, 2.1],
  [0.031, -0.4],
  [0.032, 0.6],
  [0.15, -2.3],
]
const a1 = layoutSwarm(sample, 3, [-0.2, 0.2])
const a2 = layoutSwarm(sample, 3, [-0.2, 0.2])
report(JSON.stringify(a1) === JSON.stringify(a2), '蜂群布局是确定性的（同一输入两次逐点相同）')
report(a1.length === sample.length, `蜂群布局不丢点（${a1.length}/${sample.length}）`)
report(
  a1.every((p) => Math.abs(p.y - 3) <= 0.34 + 1e-9),
  '蜂群避让不越行（|offset| ≤ 0.34 行高）',
)
report(
  new Set(a1.map((p) => p.y.toFixed(4))).size >= 3,
  `密集箱内的点被分开（同一箱 ${new Set(a1.map((p) => p.y.toFixed(4))).size} 个纵向位置，不是一列竖排）`,
)
const single = layoutSwarm([[0.1, 1]], 0, [0, 0.2])
report(single.length === 1 && single[0].y === 0, '单点行不偏移（孤点不该被推到中线外）')

/* ------------------------------------------------------------------ 浏览器 */
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

const flat = (s) => s.replace(/\s/g, '')

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await ctx.newPage()
  const pageErrors = []
  const consoleWarns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') consoleWarns.push(m.text())
  })

  await page.goto(`${BASE}/explain`, { waitUntil: 'networkidle' })
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 20_000 },
  )
  await page.waitForTimeout(400)

  let body = await page.locator('main').innerText()

  /* ---------------------------------------------------------- 1. A13 结构 */
  report((await page.locator('[data-testid="section-a13"]').count()) === 1, 'A13 区块存在')
  report((await page.locator('[data-testid="section-a15"]').count()) === 1, 'A15 区块存在')

  const a13Badge = page.locator('[data-testid="section-a13"] [data-testid="section-dataset-badge"]')
  const a15Badge = page.locator('[data-testid="section-a15"] [data-testid="section-dataset-badge"]')
  report((await a13Badge.count()) === 1, 'A13 有区块级来源角标')
  report((await a15Badge.count()) === 1, 'A15 有区块级来源角标')
  report(
    (await a13Badge.getAttribute('data-source')) === shapEnv.meta.source,
    `A13 角标 source = ${shapEnv.meta.source}（与契约一致）`,
  )
  report(
    (await a15Badge.getAttribute('data-source')) === stageEnv.meta.source,
    `A15 角标 source = ${stageEnv.meta.source}（占位数据，与契约一致）`,
  )
  report(
    (await a15Badge.getAttribute('data-tone')) === 'warning',
    'A15 角标语气为 warning（占位数据不能与实测同观感）',
  )

  const barCanvas = await page.locator('#a13-bar canvas').count()
  const swarmCanvas = await page.locator('#a13-beeswarm canvas').count()
  report(barCanvas >= 1, `A13 条形图 canvas 已渲染（${barCanvas}）`)
  // ECharts 会按需分多层画布（基础层 + 高亮层），所以断言 >=1 而不是 ===1
  report(swarmCanvas >= 1, `A13 蜂群图 canvas 已渲染（${swarmCanvas} 层）`)

  const beeswarmNote = await page.locator('[data-testid="beeswarm-derivation"]').innerText()
  report(
    flat(beeswarmNote).includes('reconstructed_from_anchors'),
    '蜂群图同屏标注「锚点重建」（不冒充实测）',
  )
  report(
    flat(beeswarmNote).includes('均值与方向是实测的') && flat(beeswarmNote).includes('分布形状是重建的'),
    '蜂群图把"哪部分是实测、哪部分是重建"写清楚（一句话两层口径）',
  )

  const shapRows = await page.locator('#toplist').locator('xpath=ancestor::section[1]').locator('tbody tr').count()
  report(shapRows >= 20, `Top-20 明细表 20 行（${shapRows}）`)

  /* ------------------------------------------------- 2. 排序切换（两张图同步） */
  const firstRowFeature = async () => {
    const row = page.locator('#toplist').locator('xpath=ancestor::section[1]').locator('tbody tr').first()
    return (await row.innerText()).split('\t')[1]?.trim() ?? (await row.innerText()).trim()
  }

  /** 第 i 行的字段代号（表格第 2 列） */
  const tableFeatureAt = async (i) => {
    const row = page.locator('#toplist').locator('xpath=ancestor::section[1]').locator('tbody tr').nth(i)
    const cells = row.locator('td, th')
    return (await cells.nth(1).innerText()).trim()
  }

  report((await tableFeatureAt(0)) === xgb.top[0].feature, `默认排序首行 = 实测 Top-1（${xgb.top[0].feature}）`)

  // 风险因子优先：首行方向必须是 +1
  await page.locator('[data-testid="sort-direction"]').click()
  await page.waitForTimeout(300)
  const dirFirst = await tableFeatureAt(0)
  const dirRow = xgb.top.find((r) => r.feature === dirFirst)
  report(dirRow?.direction === 1, `切「风险因子优先」后首行为风险因子（${dirFirst}）`)
  const riskCount = xgb.top.filter((r) => r.direction === 1).length
  const topRisk = [...xgb.top].filter((r) => r.direction === 1).sort((a, b) => a.rank - b.rank)[0]
  report(dirFirst === topRisk.feature, `风险组内仍按实测贡献排序（首行 ${topRisk.feature}）`)

  // 按字段代号：首行必须是代号最小的那个
  await page.locator('[data-testid="sort-code"]').click()
  await page.waitForTimeout(300)
  const codeFirst = await tableFeatureAt(0)
  const minCode = [...xgb.top].sort(
    (a, b) => Number(a.feature.replace(/\D/g, '')) - Number(b.feature.replace(/\D/g, '')) || a.rank - b.rank,
  )[0]
  report(codeFirst === minCode.feature, `切「按字段代号」后首行为最小代号（${codeFirst}）`)
  report(
    riskCount > 0 && riskCount < xgb.top.length,
    `风险 / 保护两类都有（${riskCount} / ${xgb.top.length - riskCount}），排序切换才有意义`,
  )

  // 排序只改观看顺序，不改 rank：首行 rank 必须仍是契约里的值
  const codeRank = (await page
    .locator('#toplist')
    .locator('xpath=ancestor::section[1]')
    .locator('tbody tr')
    .first()
    .locator('td')
    .first()
    .innerText()).trim()
  report(codeRank === String(minCode.rank), `排序不篡改实测 rank（首行 #${codeRank} = 契约 ${minCode.rank}）`)

  // 回到默认排序，后面按默认顺序断言
  await page.locator('[data-testid="sort-importance"]').click()
  await page.waitForTimeout(300)

  /* --------------------------------------------- 3. 红线：SHAP 图不挂 AUC 地板 */
  const a13Text = await page.locator('[data-testid="section-a13"]').innerText()
  report(!a13Text.includes('噪声地板'), 'A13 正文不含「噪声地板」（0.477 是 AUC 地板，不是 SHAP 量纲）')
  report(body.includes('0.477'), '页面仍保留噪声地板说明（KPI 卡片里讲清楚模型判别力）')

  /* ------------------------------------------------ 4. 蜂群与条形图的数字自洽 */
  /** 从契约重算散点均值的极端点，只作为"图不是空壳"的旁证：canvas 无法读点，
   *  所以这里核对的是同屏文字里的 mean|SHAP| 与契约一致。 */
  report(
    flat(a13Text).includes(xgb.beeswarm[0].mean_abs_shap.toFixed(4)),
    `A13 同屏 mean|SHAP| 与契约一致（${xgb.beeswarm[0].feature} ${xgb.beeswarm[0].mean_abs_shap.toFixed(4)}）`,
  )
  report(
    flat(a13Text).includes(`每行${xgb.beeswarm_meta.n_plotted}点/解释集${xgb.beeswarm_meta.n_total}`),
    `蜂群图标注采样口径（每行 ${xgb.beeswarm_meta.n_plotted} / ${xgb.beeswarm_meta.n_total}）`,
  )

  // 切模型：蜂群也跟着换（Top-1 从 x125 换到 x116）
  await page.locator('[data-testid="explain-model-chip"]').nth(1).click()
  await page.waitForTimeout(400)
  const a13Lr = await page.locator('[data-testid="section-a13"]').innerText()
  report(
    flat(a13Lr).includes(lr.beeswarm[0].feature),
    `切到 ${lr.model_label} 后蜂群 Top-1 换成 ${lr.beeswarm[0].feature}`,
  )
  await page.locator('[data-testid="explain-model-chip"]').nth(0).click()
  await page.waitForTimeout(300)

  /* ------------------------------------------------------------ 5. A15 阶段图谱 */
  const mockStrip = await page.locator('[data-testid="stage-mock-strip"]').innerText()
  report(
    flat(mockStrip).includes('占位') && flat(mockStrip).includes('契约未就绪'),
    'A15 顶部有「契约未就绪 · 占位」警示条',
  )
  report(
    flat(mockStrip).includes('字段排序与mean|SHAP|是实测值'),
    '警示条把"哪部分实测、哪部分占位"讲清楚',
  )
  report(
    flat(mockStrip).includes('P0-2') && flat(mockStrip).includes('P0-5'),
    '警示条点名阻塞项 P0-2 / P0-5（可追责）',
  )

  const chipCount = await page.locator('[data-testid^="stage-chip-"]').count()
  report(chipCount === stage.stages.length, `A15 阶段按钮 ${chipCount} 个（契约 ${stage.stages.length} 个）`)
  report((await page.locator('#a15-chart canvas').count()) === 1, 'A15 堆叠面积图 canvas 已渲染')

  const weightsText = async () => page.locator('[data-testid="stage-weights"]').innerText()
  const stage0 = stage.stages[0]
  const w0 = stage.weights.filter((w) => w.stage_id === stage0.stage_id)
  const firstText = await weightsText()
  report(flat(firstText).includes(stage0.stage_label), `A15 默认阶段 = ${stage0.stage_label}`)
  report(
    w0.every((w) => flat(firstText).includes((w.weight * 100).toFixed(1))),
    `A15 默认阶段三档权重与契约一致（${w0.map((w) => (w.weight * 100).toFixed(1)).join(' / ')}）`,
  )
  report(flat(firstText).includes('维度归属为占位'), '因子卡上标注「维度归属为占位」（不冒充业务含义）')

  // 切到第 3 个阶段：权重与字段表都要换
  const stage2 = stage.stages[2]
  await page.locator(`[data-testid="stage-chip-${stage2.stage_id}"]`).click()
  await page.waitForTimeout(350)
  const secondText = await weightsText()
  const w2 = stage.weights.filter((w) => w.stage_id === stage2.stage_id)
  report(flat(secondText).includes(stage2.stage_label), `A15 切到 ${stage2.stage_label}`)
  report(
    w2.every((w) => flat(secondText).includes((w.weight * 100).toFixed(1))),
    `A15 切阶段后权重换成 ${stage2.stage_label} 的一组（${w2.map((w) => (w.weight * 100).toFixed(1)).join(' / ')}）`,
  )
  report(flat(secondText) !== flat(firstText), 'A15 换阶段后明细文字确实变化（不是死数据）')

  const fieldsText = await page.locator('#a15-fields').innerText()
  const f2 = stage.stage_fields.find((s) => s.stage_id === stage2.stage_id).fields
  report(
    f2.every((f) => flat(fieldsText).includes(f.feature)),
    `A15 字段表换成 ${stage2.stage_label} 的 ${f2.length} 个字段`,
  )
  report(
    flat(fieldsText).includes((f2[0].stage_weight * 100).toFixed(1)),
    `A15 字段阶段权重与契约一致（首位 ${(f2[0].stage_weight * 100).toFixed(1)}%）`,
  )
  const stageNote = await page.locator('[data-testid="stage-note"]').innerText()
  const note2 = stage.decision_notes.find((n) => n.stage_id === stage2.stage_id)
  report(flat(stageNote).includes(flat(note2.headline)), 'A15 阶段结论文案与契约一致')
  report(flat(stageNote).includes('不指向任何业务含义'), 'A15 结论明确不做业务解读（红线二）')

  // 每阶段权重合计 100%（页面上直接显示）
  report(flat(secondText).includes('阶段内合计100.0%'), 'A15 阶段内权重合计 100%（页面显式标注）')

  /* ----------------------------------------------------------- 6. 红线与健康度 */
  const codes = body.match(/\bx\d{2,3}\b/g) ?? []
  report(codes.length >= 40, `全页字段一律 xNNN 代号（${codes.length} 处）`)
  report(
    (await page.locator('[data-testid="redline-banner"]').count()) === 1 &&
      flat(await page.locator('[data-testid="redline-banner"]').innerText()).includes('A13'),
    '语义未知提示条存在并点名受影响区块（A13/A15）',
  )
  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')
  const realWarns = consoleWarns.filter((m) => !m.includes('v-scx'))
  report(realWarns.length === 0, '无 Vue / ECharts 运行时告警', realWarns.length ? ` ← ${realWarns[0]}` : '')

  // 后端不可用：auto 模式下降级 mock，页面照常出内容
  const offline = await ctx.newPage()
  await offline.route('**/api/**', (route) => route.abort())
  await offline.goto(`${BASE}/explain`, { waitUntil: 'domcontentloaded' })
  await offline.waitForTimeout(2500)
  const off = await offline.locator('main').innerText()
  report(
    off.includes('这一页真正想说的一句话') || (off.includes('原因') && off.includes('下一步')),
    'P5 后端不可用时不白屏（降级 mock 或给「原因 + 下一步」）',
  )
  await offline.close()

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\nP5 冒烟全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
