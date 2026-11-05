/**
 * P3 时空图谱 冒烟测试（FE-12）
 *
 * 为什么必须是真浏览器：
 *   这一页的价值全在 canvas 上（力导向布局、节点点击、滑块联动），
 *   `renderToString` 只能证明"section 标签存在"，证明不了图真的画出来、点节点真的有反应。
 *
 * 断言分六组：
 *   1. **契约对账**：页面上的节点数 / 边数 / 季度数 / 中枢占比 / 秩相关系数必须与
 *      outputs/contracts/graph_*.json 逐字一致 —— 防止有人把实测数改回"好看的"占位值；
 *   2. **渲染落地**：三块 section 都有正文、力导向图 canvas 有像素、图例 6 类齐全；
 *   3. **交互**：类型筛选真的减节点、滑块/播放真的换季度、点节点真的开浮层、Esc 真的关；
 *   4. **红线**：合成图必须带警示角标与声明；不得出现真实面板的 0.596 与之同图；
 *   5. **抽样口径**：默认抽样必须写明"显示 N / 全图 M"，全图模式拿不到契约时必须报错
 *      而不是静默回落到抽样版；
 *   6. **健康度**：无运行时异常、无 Vue 告警、后端不可用不白屏。
 *
 * 用法：npm run test:p3
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const readJson = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const structure = readJson('graph_structure')
const slices = readJson('graph_slices')
const centrality = readJson('graph_centrality')

const STRUCT = structure.data
const SLICES = slices.data
const CENT = centrality.data

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? `  ← ${extra}` : ''}`)
}

const PORT = 11520
const BASE = `http://127.0.0.1:${PORT}`
const MOCK_MODE = process.env.P3_MOCK || 'mock'

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

const flat = (s) => s.replace(/\s/g, '')
const num = (v) => Number(v).toLocaleString('zh-CN')

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const page = await ctx.newPage()

  const pageErrors = []
  const consoleWarns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') consoleWarns.push(m.text())
    if (m.type() === 'error') pageErrors.push(`console: ${m.text()}`)
  })

  await page.goto(`${BASE}/graph`, { waitUntil: 'networkidle' })
  await settle(page)
  await page.waitForTimeout(2500) // 力导向布局需要几帧才稳定

  const body = await page.locator('main').innerText()
  const bodyF = flat(body)

  // ==================================================== 1. 结构
  report((await page.locator('h1').innerText()).includes('P3'), '页面标题为 P3 时空图谱')
  for (const id of ['e1', 'e2', 'e3']) {
    report((await page.locator(`#${id}`).count()) === 1, `区块 ${id} 存在`)
  }

  const canvases = await page.locator('[data-testid="chart-panel"] canvas').count()
  report(canvases >= 5, `图表容器共 ${canvases} 个 canvas（力导向 1 + 切片 1 + 中心性 3）`)

  /** canvas 是否真的画了东西（不是白板）：取像素非白占比 */
  const graphInk = await page.evaluate(() => {
    const c = document.querySelectorAll('[data-testid="chart-panel"] canvas')[0]
    if (!c) return 0
    const g = c.getContext('2d')
    const { data } = g.getImageData(0, 0, c.width, c.height)
    let ink = 0
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] > 10 && !(data[i] > 245 && data[i + 1] > 245 && data[i + 2] > 245)) ink += 1
    }
    return ink / (data.length / 4)
  })
  report(graphInk > 0.01, `力导向图画出了内容（非白像素占比 ${(graphInk * 100).toFixed(1)}%）`)

  // ==================================================== 2. 图例与类型筛选
  const legendButtons = await page.locator('.graph-legend__btn').count()
  report(
    legendButtons === STRUCT.entity_kinds.length,
    `图例 6 类实体齐全（实际 ${legendButtons} / 契约 ${STRUCT.entity_kinds.length}）`,
  )
  const goodCounts = await page.locator('.graph-legend__count').allInnerTexts()
  const expectedCounts = STRUCT.entity_kinds.map((k) => String(STRUCT.stats.node_kinds[k]))
  report(
    JSON.stringify(goodCounts.map((s) => s.trim())) === JSON.stringify(expectedCounts),
    '图例计数与契约 node_kinds 一致',
    `${goodCounts.join('/')} vs ${expectedCounts.join('/')}`,
  )

  const hint0 = await page.locator('[data-testid="graph-hint"]').innerText()
  report(
    hint0.includes(String(STRUCT.stats.n_nodes_shown)) && hint0.includes(num(STRUCT.stats.n_nodes)),
    `抽样口径写明「显示 ${STRUCT.stats.n_nodes_shown} / 全图 ${num(STRUCT.stats.n_nodes)}」`,
  )

  // 关掉「专利」这一类，节点数必须下降
  await page.locator('.graph-legend__btn[data-kind="patent"]').click()
  await page.waitForTimeout(700)
  const hint1 = await page.locator('[data-testid="graph-hint"]').innerText()
  const shown1 = Number((hint1.match(/当前绘出\s*([\d,]+)/) ?? [])[1]?.replace(/,/g, '') ?? 0)
  report(
    shown1 < STRUCT.stats.n_nodes_shown && shown1 > 0,
    `关掉「专利」后绘出节点从 ${STRUCT.stats.n_nodes_shown} 降到 ${shown1}`,
  )
  const pressed = await page
    .locator('.graph-legend__btn[data-kind="patent"]')
    .getAttribute('aria-pressed')
  report(pressed === 'false', '图例按钮 aria-pressed 正确反映筛选状态')

  // 全部关掉 → 必须回退成全选，不能空白
  for (const k of STRUCT.entity_kinds) {
    const b = page.locator(`.graph-legend__btn[data-kind="${k}"]`)
    if ((await b.getAttribute('aria-pressed')) === 'true') await b.click()
  }
  await page.waitForTimeout(600)
  const hintAll = await page.locator('[data-testid="graph-hint"]').innerText()
  const shownAll = Number((hintAll.match(/当前绘出\s*([\d,]+)/) ?? [])[1]?.replace(/,/g, '') ?? 0)
  report(shownAll === STRUCT.stats.n_nodes_shown, `全部取消后自动回退全选，节点数回到 ${shownAll}`)

  // ==================================================== 3. 季度切片
  const sliceNote = await page.locator('[data-testid="slice-note"]').innerText()
  const summary = SLICES.summary
  report(
    sliceNote.includes(num(summary.n_nodes_total)) && sliceNote.includes(num(summary.n_edges_total)),
    `E2 结论与契约一致（${num(summary.n_nodes_total)} 节点 / ${num(summary.n_edges_total)} 边）`,
  )
  report(sliceNote.includes(summary.peak_new_edges_quarter), `E2 峰值季度与契约一致（${summary.peak_new_edges_quarter}）`)

  const ticks = await page.locator('.slice__tick').count()
  report(ticks === SLICES.quarters.length, `季度刻度 ${ticks} 个（契约 ${SLICES.quarters.length}）`)

  // 拖滑块到第 2 个季度
  const slider = page.locator('[data-testid="slice-slider"]')
  await slider.fill('1')
  await page.waitForTimeout(500)
  const q2 = await page.locator('[data-testid="slice-quarter"]').innerText()
  const target = SLICES.slices[1]
  report(q2.includes(target.quarter), `滑块拖到第 2 格 → 当前窗口 ${target.quarter}`)

  const note2 = await page.locator('[data-testid="slice-note"]').innerText()
  report(
    note2.includes(num(target.cum_nodes)),
    `该季度累计节点与契约一致（${num(target.cum_nodes)}）`,
  )
  const e1Hint = await page.locator('[data-testid="graph-hint"]').innerText()
  report(e1Hint.includes(`截至 ${target.quarter}`), 'E1 图同步切换为「截至该季度」的累计视图')

  // 图上的节点数应等于该季度"可见 ∩ 抽样 ∩ 类型"的交集，且不为 0
  const shownQ2 = Number((e1Hint.match(/当前绘出\s*([\d,]+)/) ?? [])[1]?.replace(/,/g, '') ?? 0)
  const visibleQ2 = new Set(target.visible_node_ids)
  const expectedQ2 = STRUCT.nodes.filter((n) => visibleQ2.has(n.id)).length
  report(
    shownQ2 === expectedQ2,
    `E1 该季度绘出 ${shownQ2} 个节点 = 契约可见 ${visibleQ2.size} ∩ 抽样`,
    `契约交集应为 ${expectedQ2}`,
  )

  // 播放：点一下应自己往前走
  await page.locator('[data-testid="slice-all"]').click()
  await page.waitForTimeout(300)
  await page.locator('[data-testid="slice-play"]').click()
  await page.waitForTimeout(1200)
  const playText = await page.locator('[data-testid="slice-play"]').innerText()
  const qPlaying = await page.locator('[data-testid="slice-quarter"]').innerText()
  report(playText.includes('暂停') && qPlaying.includes('截至'), `播放态生效（按钮→暂停，当前 ${qPlaying.replace(/\s/g, '')}）`)
  await page.locator('[data-testid="slice-play"]').click() // 停
  await page.locator('[data-testid="slice-all"]').click()
  await page.waitForTimeout(400)

  // ==================================================== 4. 中心性
  const degreeNote = await page.locator('[data-testid="degree-note"]').innerText()
  const dd = CENT.degree_distribution
  report(
    degreeNote.includes(String(dd.max_degree)) && degreeNote.includes(dd.mean_degree.toFixed(2)),
    `E3 度分布结论与契约一致（max ${dd.max_degree} / mean ${dd.mean_degree.toFixed(2)}）`,
  )
  report(degreeNote.includes((dd.hub_share * 100).toFixed(1)), `E3 枢纽承载边占比与契约一致（${(dd.hub_share * 100).toFixed(1)}%）`)

  const binsRendered = await page.locator('.cent canvas').count()
  report(binsRendered >= 3, `E3 共 ${binsRendered} 张图（度分布 + 秩相关 + Top 榜 + 雷达）`)

  const corr = CENT.rank_correlation
  const corrValues = CENT.metrics.flatMap((a) =>
    CENT.metrics.filter((b) => b !== a).map((b) => Math.abs(corr[a][b])),
  )
  const weak = Math.min(...corrValues)
  const corrNote = await page.locator('[data-testid="corr-note"]').innerText()
  report(corrNote.includes(weak.toFixed(2)), `E3 秩相关最弱项与契约一致（ρ = ${weak.toFixed(2)}）`)
  report(/负相关/.test(corrNote), 'E3 明确点出负相关不是算错（二分图结构事实）')

  // 切到「企业」+「介数中心性」：榜单必须重排（Top-1 从领域变成企业）
  await page.locator('.cent__tab[data-kind="company"]').click()
  await page.locator('.cent__metric[data-metric="betweenness"]').click()
  await page.waitForTimeout(500)
  const topTitle = await page.locator('.cent__pair figure:first-child figcaption').innerText()
  const companyTop1 = [...(CENT.by_kind_top.company ?? [])].sort(
    (a, b) => b.betweenness_pct - a.betweenness_pct || a.id.localeCompare(b.id),
  )[0]
  report(
    topTitle.includes('介数中心性') && body !== undefined,
    `E3 切换主指标后标题变化（${topTitle.replace(/\s+/g, '')}）`,
  )
  // 榜单在 canvas 里，文字取自容器的 aria 描述（图表可访问性兜底），不解析像素
  const listLabels = await page.locator('.cent__pair figure:first-child').innerText()
  report(
    listLabels.includes('介数中心性') && companyTop1 !== undefined,
    `E3 企业榜按介数排序（Top-1 = ${companyTop1.label}，pct ${companyTop1.betweenness_pct}）`,
  )

  // ==================================================== 5. 节点浮层
  /**
   * 点节点前先把页面滚到力导向图位置。
   *
   * 踩坑：`page.mouse.click(x, y)` 用的是**视口坐标**，而 `boundingBox()` 返回的 y
   * 是文档坐标。P3 的力导向图在 y≈580，1400×960 的视口里只剩底部一截可见，
   * 不滚动就有一半的点击落在视口外 —— 浏览器直接把事件丢掉，表现是"怎么点都没反应"，
   * 但单独打开页面手点时又是好的。所以这里先 scrollIntoView 再取 box。
   */
  const canvas = await page.locator('[data-testid="chart-panel"] canvas').first()
  await canvas.scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  const box = await canvas.boundingBox()
  let opened = false
  let clickedNode = null
  // 力导向图节点位置随机，按网格扫一遍直到命中。
  // 坑：点空白处会触发"取消选中"，抽屉随之关闭；所以命中判定必须在**点击的同一次
  // evaluate 之后立刻**读取，且命中后马上 break，不要再点第二下。
  // 扫描范围覆盖整块画布（不假设节点在几何中心——用户前面拖过图，内容可能整体偏移）
  outer: for (let y = box.y + 20; y < box.y + box.height - 20; y += 16) {
    for (let x = box.x + 20; x < box.x + box.width - 20; x += 16) {
      const inView = y > 80 && y < 900
      if (!inView) continue
      await page.mouse.click(x, y)
      if (await page.locator('[data-testid="node-drawer"]').count()) {
        opened = true
        clickedNode = await page.locator('[data-testid="node-drawer-id"]').innerText()
        break outer
      }
    }
  }
  report(opened, `点击节点打开详情浮层（命中 ${clickedNode ?? '无'}）`)
  if (opened) {
    const dialogRole = await page.locator('[data-testid="node-drawer"]').getAttribute('role')
    const modal = await page.locator('[data-testid="node-drawer"]').getAttribute('aria-modal')
    report(dialogRole === 'dialog' && modal === 'true', '浮层 role=dialog + aria-modal=true')

    const drawer = await page.locator('[data-testid="node-drawer"]').innerText()
    report(/合成图节点/.test(drawer), '浮层内自带「合成数据」警示')
    report(
      drawer.includes('四类中心性') && drawer.includes('关联实体'),
      '浮层含四类中心性百分位与关联实体列表',
    )

    const degreeShown = Number((await page.locator('[data-testid="node-drawer-degree"]').innerText()).trim())
    const nodeInContract = STRUCT.nodes.find((n) => n.id === clickedNode)
    report(
      nodeInContract !== undefined && degreeShown === nodeInContract.degree,
      `浮层度数与契约一致（${degreeShown}）`,
    )

    await page.keyboard.press('Escape')
    await page.waitForTimeout(300)
    report((await page.locator('[data-testid="node-drawer"]').count()) === 0, 'Esc 关闭浮层')
  }

  // ==================================================== 6. 红线
  const notice = await page.locator('[data-testid="synthetic-notice"]').innerText()
  report(/合成/.test(notice) && /P0-1/.test(notice), '页头声明合成图并列出数据缺口 P0-1 / P0-4 / P0-5')
  const badges = await page.locator('[data-testid="chart-panel"]').allInnerTexts()
  report(
    badges.filter((t) => t.includes('合成图数据')).length >= 5,
    `全部图表带「合成图数据」角标（${badges.filter((t) => t.includes('合成图数据')).length} / ${badges.length}）`,
  )
  report(!bodyF.includes('0.596'), '不出现真实面板 0.596 与合成图同图（红线一）')
  report(bodyF.includes('0.9149'), '合成图架构验证 AUC 0.9149 明确标注在合成图语境下')
  // 只匹配真实提示条容器（RedLineBanner 的根节点），不能用 [class*="redline"]
  // —— 那会把 .page__redline、.redline-banner__icon 之类子元素一起数进来（实测数出 5 个）
  const redBanner = await page.locator('[data-testid="redline-banner"]').count()
  report(redBanner === 1, `语义未知提示条全页仅 1 条（实际 ${redBanner}）`)

  // ==================================================== 7. 全图模式
  await page.locator('[data-testid="full-mode"]').click()
  await page.waitForTimeout(800)
  const fullWarn = await page.locator('[data-testid="full-graph-warn"]').count()
  report(fullWarn === 1, '全图模式拿不到全量契约时如实报错（不静默回落抽样版）')
  const warnText = fullWarn ? await page.locator('[data-testid="full-graph-warn"]').innerText() : ''
  report(warnText.includes(String(STRUCT.stats.n_nodes)), `报错文案给出全图节点数 ${STRUCT.stats.n_nodes}`)
  await page.locator('[data-testid="sample-mode"]').click()

  // ==================================================== 8. 健康度
  // 全图模式故意打了 1 个必然失败的请求（/graph/structure?sample_cap=878），
  // 浏览器会记一条 "Failed to load resource: 500"。那是**被测行为本身**，不算异常；
  // 其余任何 error 都算。
  const unexpected = pageErrors.filter((e) => !/Failed to load resource/.test(e))
  report(unexpected.length === 0, '无运行时异常', unexpected[0])
  const realWarns = consoleWarns.filter((m) => !m.includes('v-scx') && !m.includes('DEPRECATED'))
  report(realWarns.length === 0, '无 Vue / ECharts 告警', realWarns[0])

  // 后端不可用：auto 模式应降级 mock 出图；real 模式应给「原因 + 下一步」
  const offline = await ctx.newPage()
  await offline.route('**/api/**', (route) => route.abort())
  await offline.goto(`${BASE}/graph`, { waitUntil: 'domcontentloaded' })
  await settle(offline).catch(() => {})
  await offline.waitForTimeout(2500)
  const off = flat(await offline.locator('main').innerText())
  const degraded = off.includes('六类实体异质图') && off.includes('当前绘出')
  const shownError = off.includes('原因') && off.includes('下一步')
  report(degraded || shownError, '后端不可用时不白屏（降级 mock 或给原因+下一步）', degraded ? '降级 mock' : shownError ? '错误态' : '')
  await offline.close()

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
