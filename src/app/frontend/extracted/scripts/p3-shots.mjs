/**
 * P3 时空图谱 截图（交付物用）
 *
 * 验收要求「可缩放拖拽、可筛选、可切季度、可看中心性、点节点有详情」，
 * 这五件事全发生在 canvas 和浮层里，光靠文字交付说明没法自证。
 * 所以这里用真浏览器把整页 + 四个区块 + 两个交互中间态各截一张。
 *
 * 与 p2-shots.mjs 的两点差别：
 *   1. 力导向图节点落位是随机的，截图前先等 canvas 停止变化（连拍两次像素一致），
 *      否则截出来常常是"刚散开一半"的半成品；
 *   2. 浮层与筛选态是交互之后才有的，不能只截静态区块，
 *      所以额外截「筛掉专利后的图」和「节点详情浮层」两张。
 *
 * 用法：npm run build && npm run shots:p3
 * 产出：docs/preview/p3-*.png
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11551
const BASE = `http://127.0.0.1:${PORT}`

// 交付截图固定走 mock（契约同构数据），开关钉进产物，不依赖 shell 环境变量
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  mode: 'test',
  define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('mock') },
  server: { host: '127.0.0.1', port: PORT, strictPort: true },
  logLevel: 'error',
})
await server.listen()

const candidates = [process.env.CHROMIUM_PATH, '/usr/bin/chromium', '/usr/bin/google-chrome'].filter(Boolean)
const browser = await chromium.launch({
  executablePath: candidates.find((p) => existsSync(p)),
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
  const page = await ctx.newPage()
  await page.goto(`${BASE}/graph`, { waitUntil: 'networkidle' })
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 20_000 },
  )
  await page.waitForTimeout(600)

  /**
   * 等力导向图收敛。
   *
   * ECharts 的力导向布局是一帧一帧迭代的，`networkidle` 只说明网络空了，
   * 图可能才刚散开。这里每 250ms 取一次 canvas 像素的哈希，连续两次相同即认为静止。
   * 最多等 6 秒 —— 节点多的时候确实收敛得慢，等不到就拿"最接近静止"的那一帧。
   */
  const waitForLayout = async () => {
    const hash = async () => {
      const buf = await page.locator('[data-testid="chart-panel"] canvas').first().screenshot()
      return createHash('md5').update(buf).digest('hex')
    }
    let prev = await hash()
    for (let i = 0; i < 24; i += 1) {
      await page.waitForTimeout(250)
      const next = await hash()
      if (next === prev) return
      prev = next
    }
  }
  await waitForLayout()

  // #e0x 是区块标题（h2 → header → section），真正要截的是外层 section。
  // 元素截图会把视口外滚进来的部分也算进去（页面顶部有 sticky 头部），
  // 所以先滚到区块顶部、等布局稳定，再按 bbox 在整页坐标里裁一张。
  const shot = async (name, selector) => {
    if (!selector) {
      await page.screenshot({ path: resolve(outDir, `${name}.png`), fullPage: true })
      console.log('saved', name)
      return
    }
    const target = page.locator(`${selector} >> xpath=../..`)
    await page.evaluate((sel) => {
      const el = document.querySelector(sel)
      const sec = el?.closest('section')
      if (sec) window.scrollTo({ top: sec.getBoundingClientRect().top + window.scrollY - 8, behavior: 'instant' })
    }, selector)
    await page.waitForTimeout(250)
    const box = await target.boundingBox()
    const scrollY = await page.evaluate(() => window.scrollY)
    await page.screenshot({
      path: resolve(outDir, `${name}.png`),
      fullPage: true,
      clip: { x: box.x, y: box.y + scrollY, width: box.width, height: box.height },
    })
    console.log('saved', name)
  }

  await shot('p3-full', null)
  await shot('p3-e1-graph', '#e1')
  await shot('p3-e2-slice', '#e2')
  await shot('p3-e3-centrality', '#e3')
  await shot('p3-e4-reading', '#e4')

  // 筛选态：把「专利」关掉，直接看出 630 → 108 的降幅，比文字说明有说服力
  await page.locator('.graph-legend__btn', { hasText: '专利' }).first().click()
  await page.waitForTimeout(900)
  await shot('p3-e1-filtered', '#e1')
  await page.locator('.graph-legend__btn', { hasText: '专利' }).first().click()
  await page.waitForTimeout(600)

  /**
   * 节点浮层：截整个遮罩（抽屉 + 灰底），单截抽屉看不出"这是个浮层"。
   *
   * 点节点前必须先滚动：`page.mouse.click` 用视口坐标，而 `boundingBox()` 的 y
   * 是文档坐标。力导向图在这种页面里通常在首屏以下，不滚动就有一半点击落在视口外。
   */
  const canvas = page.locator('[data-testid="chart-panel"] canvas').first()
  await canvas.scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  const box = await canvas.boundingBox()
  let opened = false
  // 力导向图节点位置随机，按 16px 网格扫一遍直到命中；命中后立刻停手，
  // 因为点空白处会取消选中、把抽屉关掉。
  outer: for (let y = box.y + 20; y < box.y + box.height - 20; y += 16) {
    for (let x = box.x + 20; x < box.x + box.width - 20; x += 16) {
      if (y < 80 || y > 900) continue
      await page.mouse.click(x, y)
      if (await page.locator('[data-testid="node-drawer"]').count()) {
        opened = true
        break outer
      }
    }
  }
  if (opened) {
    await page.waitForTimeout(500)
    await page.screenshot({ path: resolve(outDir, 'p3-e1-node-drawer.png') })
    console.log('saved p3-e1-node-drawer')
  } else {
    console.log('SKIP p3-e1-node-drawer（没点中节点，跳过浮层截图）')
  }

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}
