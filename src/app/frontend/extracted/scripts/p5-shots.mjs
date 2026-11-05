/**
 * P5 可解释性 截图（FE-14 交付物）
 *
 * 与 p4p5-shots.mjs 相同的做法（真浏览器 + 1440 宽 + 2 倍像素），但 P5 的区块
 * 从"一张图一个区块"变成了"一个区块两张图"，所以这里按 **图** 裁而不是按 **区块** 裁：
 *   p5-full            整页
 *   p5-a13-bar         A13 条形图（含角标 / 图例 / 说明）
 *   p5-a13-beeswarm    A13 蜂群图（含重建口径注记）
 *   p5-a13-toplist     Top-20 明细表
 *   p5-a13-direction   切到「风险因子优先」后的条形图（证明排序是真换序）
 *   p5-a15-stack       A15 堆叠面积（默认 种子期）
 *   p5-a15-stage       切到 成长期 后的整块 A15（含权重明细与字段表）
 *   p5-lr              整页切到 Logistic 后的对照
 *
 * 用法：npm run shots:p5
 */
import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11571
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

const candidates = [process.env.CHROMIUM_PATH, '/usr/bin/chromium', '/usr/bin/google-chrome'].filter(Boolean)
const browser = await chromium.launch({
  executablePath: candidates.find((p) => existsSync(p)),
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
  const page = await ctx.newPage()

  const settle = () =>
    page.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, {
      timeout: 20_000,
    })

  /** 裁某个元素（整页截图 + clip，保证上方 sticky 头不会重复出现） */
  const shotEl = async (name, selector) => {
    const el = page.locator(selector).first()
    await el.scrollIntoViewIfNeeded()
    await page.waitForTimeout(250)
    const box = await el.boundingBox()
    if (!box) {
      console.log('SKIP', name, '(no box)')
      return
    }
    const y = await page.evaluate(() => window.scrollY)
    await page.screenshot({
      path: resolve(outDir, `${name}.png`),
      fullPage: true,
      clip: { x: box.x, y: box.y + y, width: box.width, height: box.height },
    })
    console.log('saved', name)
  }

  await page.goto(`${BASE}/explain`, { waitUntil: 'networkidle' })
  await settle()
  // 截图期的两种「浮动元素挡住裁剪区顶部」必须一起治：
  //   1. 固定头（position: fixed）在整页截图里会压到裁剪区顶上；
  //   2. 表头（.tbl thead th）是 sticky，滚动后贴住视口顶部，clip 裁到表格时它正好
  //      落在裁剪区上边缘，把表头压掉一半（Top-20 那张踩过）。
  // 但**只在截图期**取消 sticky——页面本身的固定表头是给人用的，不能为了截图改样式。
  // 且必须在 goto **之后**注入：goto 会重新加载文档，之前注入的样式会被清掉（踩过）。
  await page.addStyleTag({
    content: [
      '.app-header, .skip-link { visibility: hidden !important; }',
      '.tbl thead th { position: static !important; }',
      '.table-scroll, .table-scroll--tall { overflow: visible !important; max-height: none !important; }',
    ].join('\n'),
  })
  await page.waitForTimeout(600)

  await page.screenshot({ path: resolve(outDir, 'p5-full.png'), fullPage: true })
  console.log('saved p5-full')

  await shotEl('p5-a13-bar', '#a13-bar [data-testid="chart-panel"]')
  await shotEl('p5-a13-beeswarm', '#a13-beeswarm [data-testid="chart-panel"]')
  await shotEl('p5-a13-toplist', '#a13-toplist')
  await shotEl('p5-a15-stack', '[data-testid="section-a15"]')

  // 排序切换：证明「可切换排序」是真交互
  await page.locator('[data-testid="sort-direction"]').click()
  await page.waitForTimeout(400)
  await shotEl('p5-a13-direction', '#a13-bar [data-testid="chart-panel"]')
  await page.locator('[data-testid="sort-importance"]').click()
  await page.waitForTimeout(300)

  // 阶段切换：证明「动态决策图谱」是真换数
  await page.locator('[data-testid="stage-chip-s2"]').click()
  await page.waitForTimeout(400)
  await shotEl('p5-a15-stage', '[data-testid="section-a15"]')

  // 模型切换：条形图 + 蜂群图一起换
  await page.locator('[data-testid="explain-model-chip"]').nth(1).click()
  await page.waitForTimeout(500)
  await page.screenshot({ path: resolve(outDir, 'p5-lr.png'), fullPage: true })
  console.log('saved p5-lr')

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}
