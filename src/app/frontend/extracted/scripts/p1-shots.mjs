/**
 * P1 概览截图（交付物用）
 *
 * 用法：npm run build && npm run shots:p1
 * 产出：docs/preview/p1-full.png、p1-chain.png、p1-gaps.png
 */
import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11552
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
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, {
    timeout: 20_000,
  })

  await page.screenshot({ path: resolve(outDir, 'p1-full.png'), fullPage: true })
  console.log('saved p1-full')

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

  await shotSection('p1-chain', '#chain')
  await shotSection('p1-gaps', '#gaps')

  await ctx.close()
} finally {
  await browser.close()
  await server.close()
}
