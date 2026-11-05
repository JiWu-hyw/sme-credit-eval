import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })
const PORT = 11591
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
const cands = [process.env.CHROMIUM_PATH, '/usr/bin/chromium', '/usr/bin/google-chrome'].filter(Boolean)
const browser = await chromium.launch({ executablePath: cands.find((p) => existsSync(p)), args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
const page = await ctx.newPage()
const logs = []
page.on('console', (m) => logs.push(`${m.type()}: ${m.text()}`))
page.on('pageerror', (e) => logs.push('pageerror: ' + e))
await page.goto(`${BASE}/explain`, { waitUntil: 'networkidle' })
await page.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, { timeout: 20000 })
await page.waitForTimeout(800)
await page.screenshot({ path: resolve(outDir, '_tmp-p5-full.png'), fullPage: true })
const shot = async (name, sel) => {
  const box = await page.locator(sel).first().boundingBox()
  if (!box) return console.log('no box', sel)
  const y = await page.evaluate(() => window.scrollY)
  await page.screenshot({ path: resolve(outDir, `_tmp-${name}.png`), fullPage: true, clip: { x: box.x, y: box.y + y, width: box.width, height: box.height } })
  console.log('saved', name, Math.round(box.height))
}
await shot('a13-bar', '#a13-bar')
await shot('a13-beeswarm', '#a13-beeswarm')
await shot('a15', '[data-testid="section-a15"]')
console.log(logs.filter((l) => !l.startsWith('log')).slice(0, 10).join('\n'))
await ctx.close(); await browser.close(); await server.close()
