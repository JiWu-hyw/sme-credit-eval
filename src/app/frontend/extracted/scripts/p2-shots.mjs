/**
 * P2 页面截图（交付物用）
 *
 * 验收要求「四区块齐备 + 特征树可折叠可展开」，光靠文字交付不够直观，
 * 这里用生产构建产物（vite preview）跑一遍真浏览器，把四个区块和浮层各截一张。
 *
 * 用法：npm run build && npm run shots:p2
 * 产出：docs/preview/p2-*.png
 */
import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11550
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
  await page.goto(`${BASE}/data`, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, {
    timeout: 20_000,
  })

  // #a0x 是区块标题（h2 → header → section），真正要截的是外层 section。
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
    await page.waitForTimeout(200)
    const box = await target.boundingBox()
    const scrollY = await page.evaluate(() => window.scrollY)
    await page.screenshot({
      path: resolve(outDir, `${name}.png`),
      fullPage: true,
      clip: { x: box.x, y: box.y + scrollY, width: box.width, height: box.height },
    })
    console.log('saved', name)
  }

  await shot('p2-full', null)
  await shot('p2-a01-pipeline', '#a01')
  await shot('p2-a02-quality', '#a02')
  await shot('p2-a03-depth', '#a03')
  await shot('p2-a04-tree', '#a04')

  // 浮层：截整个遮罩（抽屉 + 灰底），单截抽屉看不出「这是个浮层」。
  // 抽屉高度自适应后不再是整屏，遮罩截图能同时说明层级与内容。
  await page.locator('.ftree__toggle--l2').first().click()
  await page.locator('.ftree__leaf').first().click()
  await page.locator('[data-testid="leaf-modal"] [role="dialog"]').waitFor({ state: 'visible' })
  await page.waitForTimeout(250)
  await page.screenshot({ path: resolve(outDir, 'p2-a04-leaf-modal.png') })
  console.log('saved p2-a04-leaf-modal')

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}
