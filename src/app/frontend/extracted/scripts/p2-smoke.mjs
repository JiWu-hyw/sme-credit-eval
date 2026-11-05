/**
 * P2 数据与特征页冒烟测试（FE-11 验收项）
 *
 * 为什么用真浏览器（Playwright + Chromium）而不是 SSR 渲染字符串：
 *   ① 四个区块都是「先 loading、拿数再画」，`renderToString` 不等异步，
 *      拍到的永远是一排骨架屏（本脚本第一版 11 项假失败就是这么来的）；
 *   ② A04 的折叠 / 字段详情浮层 / Esc 关闭是纯交互，字符串断言测不到；
 *   ③ ECharts 要 canvas，jsdom 下 `getContext` 是空的。
 * 结论：验收项里有交互，就得在真浏览器里点一遍。
 *
 * 断言分六组：
 *   1. 四区块齐备：A01 链路 / A02 热力图+箱线图 / A03 直方图 / A04 特征树；
 *   2. 特征树交互：折叠切 aria-expanded、点叶子开浮层、Esc 关闭；
 *   3. 红线：语义未知字段只出现 xNNN 代号、页面只挂一条统一提示条、
 *      图表容器带数据来源角标、真实面板带噪声地板 0.477；
 *   4. 契约一致性：节点数 / 叶子数 / 热力格子数 / 纵深中位数与 outputs/contracts 对得上；
 *   5. 渲染健康度：无运行时异常、无 Vue 告警；
 *   6. 状态机：后端不可用时不白屏。
 *
 * 用法：npm run test:p2
 *      脚本自己起 vite dev server（11500 端口），跑完自动关。
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const contract = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const contracts = {
  pipeline: contract('data_pipeline'),
  quality: contract('data_quality'),
  depth: contract('panel_depth'),
  tree: contract('feature_tree'),
}

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

const PORT = 11500
const BASE = `http://127.0.0.1:${PORT}`

// 不依赖后端：四个接口直接吃契约同构 mock。
// 注意：`mode: 'test'` 只会读 .env.test，**不会**读 shell 里的 VITE_USE_MOCK，
// 所以这里用 define 把开关直接钉进产物，跑 `npm run test:p2` 就不用再背环境变量。
// 想验「后端不可用」的真实链路时传 P2_MOCK=real 覆盖。
const MOCK_MODE = process.env.P2_MOCK || 'mock'
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  mode: 'test',
  define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify(MOCK_MODE) },
  server: { host: '127.0.0.1', port: PORT, strictPort: true },
  logLevel: 'error',
})
await server.listen()

// 优先用环境变量指定的浏览器，其次是系统 Chromium（CI 里不必再下 120MB）
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
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()

  const pageErrors = []
  const consoleWarns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') consoleWarns.push(m.text())
  })

  // ---------------------------------------------------------------- 整页加载
  await page.goto(`${BASE}/data`, { waitUntil: 'networkidle' })
  console.log('→', await page.title())
  // 四个区块的骨架屏全部消失 = 四个接口都落地了
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 20_000 },
  )

  const text = await page.locator('main').innerText()

  // -------------------------------------------------- 1. 四区块齐备
  report((await page.locator('#a01').count()) === 1 && text.includes('数据接入链路'), 'A01 数据接入链路区块存在')
  report((await page.locator('[data-testid="pipeline-canvas"]').count()) === 1, 'A01 链路图已渲染（SVG 画布）')
  const nodeCount = await page.locator('[data-testid="pipeline-node"]').count()
  report(nodeCount === contracts.pipeline.data.nodes.length, `A01 链路节点数与契约一致（${nodeCount} 个）`)
  report(text.includes('按 userid 分组切分'), 'A01 展示 6 步流水线的具体实现（禁止行级随机切分）')

  report((await page.locator('#a02').count()) === 1 && text.includes('数据质量诊断'), 'A02 数据质量诊断区块存在')
  report(text.includes('缺失率热力图'), 'A02 缺失率热力图存在')
  const heatCells = await page.locator('.heat__cell').count()
  report(
    heatCells >= contracts.quality.data.missing_matrix.length,
    `A02 热力格子数不少于契约（${heatCells} ≥ ${contracts.quality.data.missing_matrix.length}）`,
  )
  const chartPanels = await page.locator('[data-testid="chart-panel"]').count()
  report(chartPanels >= 2, `A02 缩尾箱线图示在 ChartPanel 容器内（共 ${chartPanels} 个图表容器）`)
  report(text.includes('缩尾前后'), 'A02 缩尾前后箱线图标题存在')

  // 回归（2026-10-22 发现）：箱线图 x 轴是 category 轴，坐标会被吸附到整数刻度；
  // 原实现用 idx±0.18 做「前 / 后」偏移，两组箱体被吸附到同一刻度、灰色永远看不见。
  //
  // 断言方式：ECharts 把实例 id 写在容器 DOM 的 `_echarts_instance_` 属性上，
  // 但 `getInstanceByDom` 不在页面全局。退一步用**像素判据**：
  // 灰 (#b9c0c9) 只属于「缩尾前」那一组，原实现里它被红箱完整覆盖 → 灰像素数≈0。
  // 注意：A02 区块的 `<section>` 没有 id（id 挂在标题 h2 上），这里改用标题定位
  const a02Section = page.locator('section', { has: page.locator('#a02') })
  const boxCanvas = a02Section.locator('[data-testid="chart-panel"] canvas').first()
  await boxCanvas.scrollIntoViewIfNeeded({ timeout: 5000 })
  const greyPixels = await boxCanvas.evaluate((c) => {
    const ctx = c.getContext('2d')
    if (!ctx) return -1
    const data = ctx.getImageData(0, 0, c.width, c.height).data
    let n = 0
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i]
      const g = data[i + 1]
      const b = data[i + 2]
      const a = data[i + 3]
      // #b9c0c9 附近（填充 opacity 0.85，放宽容差吃掉抗锯齿）
      if (a > 128 && Math.abs(r - 185) <= 26 && Math.abs(g - 192) <= 26 && Math.abs(b - 201) <= 26) n += 1
    }
    return n
  })
  report(greyPixels > 500, `A02 缩尾前（灰）与缩尾后（红）两组箱体同时可见（灰像素 ${greyPixels}）`)
  report((await page.locator('#a03').count()) === 1 && text.includes('面板观测纵深'), 'A03 面板观测纵深区块存在')
  report(text.includes('每企业观测月数分布'), 'A03 每企业观测月数直方图存在')
  report(
    text.includes(String(contracts.depth.data.coverage.median_months)),
    `A03 展示中位观测月数（契约 ${contracts.depth.data.coverage.median_months}）`,
  )
  report(text.includes('长序列建模不可行'), 'A03 给出「长序列不可行」结论，而不是只摆图')

  report(
    (await page.locator('#a04').count()) === 1 && text.includes('特征体系三级树'),
    'A04 特征体系三级树区块存在',
  )
  report(text.includes('数据导入'), '页面承载「数据导入」入口（FE-20 挂点）')

  // -------------------------------------------------- 2. 特征树交互
  const toggles = await page.locator('.ftree__toggle').count()
  report(toggles >= 6, `特征树分支可折叠（${toggles} 个折叠按钮，≥6）`)
  const leaves = await page.locator('.ftree__leaf').count()
  report(leaves === contracts.tree.data.flat.length, `叶子数与契约一致（${leaves} 个）`)

  const firstToggle = page.locator('.ftree__toggle').first()
  const before = await firstToggle.getAttribute('aria-expanded')
  await firstToggle.click()
  const after = await firstToggle.getAttribute('aria-expanded')
  report(
    before === 'true' && after === 'false',
    '点折叠按钮：aria-expanded 由 true 变 false',
    `（${before} → ${after}）`,
  )
  await firstToggle.click()
  report((await firstToggle.getAttribute('aria-expanded')) === 'true', '再点一次能展开回来（折叠不是单向的）')

  // 但此时「全部收起」下没有任何可见叶子（v-show 隐藏），先展开第二级再点
  await page.locator('.ftree__toggle--l2').first().click()
  await page.locator('.ftree__leaf').first().click()
  const modal = page.locator('[data-testid="leaf-modal"]')
  await modal.waitFor({ state: 'visible', timeout: 5000 })
  report(true, '点叶子打开字段详情浮层')
  const dialog = page.locator('[data-testid="leaf-modal"] [role="dialog"]')
  report(
    (await dialog.getAttribute('aria-modal')) === 'true' && (await dialog.getAttribute('aria-labelledby')) !== null,
    '浮层内层面板带 role="dialog" + aria-modal + aria-labelledby',
  )
  const modalText = await modal.innerText()
  report(modalText.includes('语义'), '浮层展示 semantic_known 状态')
  report(
    modalText.includes('label_ref') || modalText.includes('feature_dict'),
    '浮层给出 label_ref 间接引用，不自拟业务名',
  )
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => !document.querySelector('[data-testid="leaf-modal"]'), null, {
    timeout: 3000,
  })
  report((await page.locator('[data-testid="leaf-modal"]').count()) === 0, 'Esc 关闭浮层')

  report(text.includes('全部展开') && text.includes('全部收起'), '提供「全部展开 / 全部收起」')

  // -------------------------------------------------- 3. 红线
  const banners = await page.locator('[data-testid="redline-banner"]').count()
  report(banners === 1, '红线二：全页只有一条「语义未知」统一提示条', `（${banners} 条）`)
  const unknownTags = await page.locator('[data-testid="unknown-field"]').count()
  report(unknownTags >= 10, `红线二：未知字段以代号标签渲染（${unknownTags} 处）`)
  const shownCodes = await page.locator('[data-testid="unknown-field"]').allInnerTexts()
  report(
    shownCodes.length > 0 && shownCodes.every((c) => /^x\d{2,3}$/i.test(c.trim())),
    '红线二：未知字段只显示 xNNN 代号，无业务名包装',
    shownCodes.length ? `（样例 ${shownCodes.slice(0, 3).join(', ')}）` : '（未匹配到）',
  )
  const badges = await page.locator('[data-testid="dataset-badge"]').count()
  report(badges >= 1, `红线一：图表容器带数据来源角标（${badges} 处）`)
  report(text.includes('0.477'), '红线三：真实面板图同屏噪声地板 0.477')
  report(
    (await page.locator('[data-testid="noise-floor-note"]').count()) >= 1,
    '红线三：噪声地板以固定组件挂在图上',
  )

  // -------------------------------------------------- 4. 渲染健康度
  report(pageErrors.length === 0, '页面无运行时异常', pageErrors.length ? `（${pageErrors[0]}）` : '')
  report(
    consoleWarns.length === 0,
    '控制台无 Vue 告警',
    consoleWarns.length ? `（${consoleWarns[0].slice(0, 100)}）` : '',
  )

  // -------------------------------------------------- 5. 可读性：热力格不靠颜色
  const cell = page.locator('.heat__cell').first()
  await cell.hover()
  const cellTitle = (await cell.getAttribute('title')) ?? ''
  report(cellTitle.length > 0, '热力格带 title（悬停可读，不依赖颜色）', cellTitle ? `（${cellTitle}）` : '')

  // -------------------------------------------------- 6. 后端不可用
  const offlinePage = await ctx.newPage()
  await offlinePage.route('**/api/**', (route) => route.abort())
  await offlinePage.goto(`${BASE}/data`, { waitUntil: 'domcontentloaded' })
  // auto 模式会降级 mock；real 模式才走错误态。两种都必须不白屏。
  await offlinePage.waitForTimeout(2000)
  const offlineText = await offlinePage.locator('main').innerText()
  // auto 模式会降级 mock（正常出图）；real 模式走错误态（给原因 + 下一步）。两种都算通过。
  // 判据要宽松但可证伪：页面没退化成白屏，且至少给了一种可读的交代
  const degradedToMock = offlineText.includes('数据接入链路') && offlineText.includes('按 userid 分组切分')
  const shownError = (offlineText.includes('原因') && offlineText.includes('下一步')) || offlineText.includes('加载失败')
  report(
    (degradedToMock || shownError) && offlineText.replace(/\s/g, '').length > 40,
    '后端不可用时不白屏（降级 mock 出图，或给「原因 + 下一步」）',
    degradedToMock ? '（已降级 mock）' : shownError ? '（已给错误态）' : '',
  )
  await offlinePage.close()

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
