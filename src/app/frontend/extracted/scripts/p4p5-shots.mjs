/**
 * P4 / P5 页面截图（交付物用）
 *
 * 与 p2-shots.mjs 同一套做法：真浏览器 + 1440 宽 + 2 倍像素，
 * 整页截一张、每个区块再截一张，方便评审只看局部。
 *
 * 用法：npm run build && npm run shots:p4p5
 * 产出：docs/preview/p4-*.png、docs/preview/p5-*.png
 */
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

  /** 区块截图：`#id` 挂在 h2 上，要裁的是它的最近 section */
  const shotSection = async (name, id) => {
    await page.evaluate((sel) => {
      const sec = document.querySelector(sel)?.closest('section')
      if (sec) window.scrollTo({ top: sec.getBoundingClientRect().top + window.scrollY - 8, behavior: 'instant' })
    }, id)
    await page.waitForTimeout(200)
    const box = await page.locator(id).locator('xpath=ancestor::section[1]').boundingBox()
    const scrollY = await page.evaluate(() => window.scrollY)
    await page.screenshot({
      path: resolve(outDir, `${name}.png`),
      fullPage: true,
      clip: { x: box.x, y: box.y + scrollY, width: box.width, height: box.height },
    })
    console.log('saved', name)
  }

  // ---------------- P4
  await page.goto(`${BASE}/model`, { waitUntil: 'networkidle' })
  await settle()
  await page.screenshot({ path: resolve(outDir, 'p4-full.png'), fullPage: true })
  console.log('saved p4-full')
  // FE-13：A1–A4 四个区块各一张（A1 合成图 / A2 基线 / A3 曲线族 / A4 噪声地板）
  await shotSection('p4-a1-architecture', '#a1')
  await shotSection('p4-a2-benchmark', '#a2')
  await shotSection('p4-a3-roc-pr-ks', '#a3')
  await shotSection('p4-a4-noise-floor', '#a4')
  await shotSection('p4-t1-holdout', '#holdout-table')
  await shotSection('p4-t2-cv', '#cv')

  // P5 的截图已迁到 scripts/p5-shots.mjs（FE-14：A13 有了蜂群图、A15 有阶段切换，
  // 按区块裁已经不够用，改成按图裁）。本脚本只负责 P4。

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}
