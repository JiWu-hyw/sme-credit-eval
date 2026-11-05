/**
 * P8 批量评估 截图（FE-20 交付物）
 *
 * 与 p6/p7-shots 同一套做法（真浏览器 + 1440 宽 + 2 倍像素 + 按元素裁），
 * 但这一页的截图有个特殊要求：**必须截"跑起来"的过程**，不能只截一张完成态。
 * 因为这一页最容易被糊过去的地方正是「进度条其实不会动」，静态图看不出来。
 *
 *   p8-import          导入区（拖拽 + 六条文件级校验 + 8 行预览）
 *   p8-progress-live   提交后 1 秒：进度 29/100，KPI 标「非终局」，导出按钮**不在**
 *   p8-progress-done   跑完：进度 100/100，失败原因分组可见
 *   p8-result          结果表（失败行就在表里，带着原因）
 *   p8-result-failed   切到「被拦下」：9 行全在，每行指名了字段
 *   p8-result-trace    展开行追溯（面板记录 / 分数来源 / 契约文件）
 *   p8-export          导出区块（11 列清单 + 失败行处理说明）
 *   p8-compare-warn    跨批次对比的拦截（as_of 不同 → 先说不不可比）
 *   p8-jobs            任务列表（6 种状态）
 *   p8-full            整页
 *   p8-degrade         接口不可用时的降级（不白屏，问答区仍在）
 *
 * 用法：npm run shots:p8
 */
import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer as createHttp } from 'node:http'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11575
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
  const warnings = []
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') warnings.push(`[${m.type()}] ${m.text()}`)
  })
  page.on('pageerror', (e) => warnings.push(`[pageerror] ${e.message}`))

  const settle = (target = page) =>
    target.waitForFunction(() => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0, null, {
      timeout: 20_000,
    })

  const shotEl = async (name, selector, target = page) => {
    const el = target.locator(selector).first()
    await el.scrollIntoViewIfNeeded()
    await target.waitForTimeout(250)
    const box = await el.boundingBox()
    if (!box) throw new Error(`元素不可见：${selector}`)
    const y = box.y + (await target.evaluate(() => window.scrollY)) - 12
    await target.screenshot({
      path: resolve(outDir, `${name}.png`),
      clip: { x: 0, y, width: 1440, height: Math.min(box.height + 24, 8000) },
      fullPage: true,
    })
    console.log(`  ✓ ${name}.png`)
  }

  await page.goto(`${BASE}/batch`, { waitUntil: 'networkidle' })
  await settle()
  await page.waitForTimeout(600)

  console.log('截图（运行前）：')
  await shotEl('p8-import', '[data-testid="p8-section-import"]')
  await shotEl('p8-jobs', '[data-testid="p8-section-jobs"]')

  /* ---------------- 提交，抓一张"真的在跑"的进度 ---------------- */
  await page.locator('[data-testid="p8-use-demo"]').click()
  await page.waitForFunction(() => !!document.querySelector('[data-testid="p8-file-checks"]'), null, { timeout: 10_000 })
  await page.waitForTimeout(400)
  await shotEl('p8-import-checked', '[data-testid="p8-section-import"]')

  await page.locator('[data-testid="p8-submit"]').click()
  await page.waitForFunction(() => !!document.querySelector('[data-testid="p8-receipt"]'), null, { timeout: 10_000 })
  // 等到「已处理」超过 0 且还没跑完：这才是进度条真的在动的证据
  await page.waitForFunction(
    () => {
      const el = document.querySelector('[data-testid="p8-progress-text"]')
      const m = el ? /已处理\s*(\d+)\s*\/\s*(\d+)/.exec(el.textContent.replace(/\s+/g, ' ')) : null
      return m && Number(m[1]) > 0 && Number(m[1]) < Number(m[2])
    },
    null,
    { timeout: 15_000 },
  )
  await page.waitForTimeout(300)
  console.log('截图（运行中）：')
  await shotEl('p8-progress-live', '[data-testid="p8-section-progress"]')
  const liveDownload = await page.locator('[data-testid="p8-download"]').count()

  /* ---------------- 等跑完 ---------------- */
  await page.waitForFunction(() => !!document.querySelector('[data-testid="p8-download"]'), null, { timeout: 30_000 })
  await page.waitForTimeout(600)

  console.log('截图（完成态）：')
  await shotEl('p8-progress-done', '[data-testid="p8-section-progress"]')
  await shotEl('p8-result', '[data-testid="p8-section-result"]')

  await page.locator('[data-testid="p8-status-failed"]').click()
  await page.waitForTimeout(400)
  await shotEl('p8-result-failed', '[data-testid="p8-section-result"]')
  await page.locator('[data-testid="p8-status-all"]').click()
  await page.waitForTimeout(300)

  // 展开第 1 行：追溯面板
  await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').first().click()
  await page.waitForTimeout(400)
  await shotEl('p8-result-trace', '[data-testid="p8-trace"]')
  await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').first().click()
  await page.waitForTimeout(200)

  await shotEl('p8-export', '[data-testid="p8-section-export"]')

  /* ---------------- 跨批次对比的拦截 ---------------- */
  // 先切到历史批次再打开对比：两个 as_of 不同时才看得到"先拦一次"
  await page.locator('[data-testid="p8-section-jobs"] .job[data-job="job_0e6b31a7"]').click()
  await page.waitForTimeout(700)
  await page.locator('[data-testid="p8-compare"]').click()
  await page.waitForTimeout(500)
  if (await page.locator('[data-testid="p8-compare-warning"]').count()) {
    await shotEl('p8-compare-warn', '[data-testid="p8-section-compare"]')
  } else {
    console.log('  ! 本次未触发跨快照拦截（两个批次 as_of 相同），跳过 p8-compare-warn')
  }

  await page.goto(`${BASE}/batch`, { waitUntil: 'networkidle' })
  await settle()
  await page.waitForTimeout(700)
  await page.screenshot({ path: resolve(outDir, 'p8-full.png'), fullPage: true })
  console.log('  ✓ p8-full.png')

  /* --------------------- 降级：stub 后端返回畸形信封 --------------------- */
  const STUB_PORT = PORT + 1
  const DG = PORT + 2
  const stub = createHttp((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    // 信封合法、data 为空：正是「后端接口还没上线 / 契约被改名」的样子
    res.end(JSON.stringify({ code: 0, message: 'ok', data: {}, meta: { dataset: 'real_panel' } }))
  })
  await new Promise((r) => stub.listen(STUB_PORT, '127.0.0.1', r))

  const degrade = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    mode: 'test',
    define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('real') },
    plugins: [vue()],
    resolve: { alias: { '@': resolve(here, '../src') } },
    server: {
      host: '127.0.0.1',
      port: DG,
      strictPort: true,
      proxy: { '/api': { target: `http://127.0.0.1:${STUB_PORT}`, changeOrigin: true } },
    },
    logLevel: 'error',
  })
  await degrade.listen()

  const dpage = await ctx.newPage()
  dpage.on('pageerror', (e) => warnings.push(`[pageerror/degrade] ${e.message}`))
  await dpage.goto(`http://127.0.0.1:${DG}/batch`, { waitUntil: 'networkidle' })
  await dpage.waitForTimeout(1500)
  await shotEl('p8-degrade', '[data-testid="state-panel"]', dpage)
  await dpage.screenshot({ path: resolve(outDir, 'p8-degrade-full.png'), fullPage: true })
  console.log('  ✓ p8-degrade-full.png')

  await degrade.close()
  await new Promise((r) => stub.close(r))
  await dpage.close()

  if (liveDownload > 0) {
    console.log('  ! 警告：运行中的任务已经出现了导出按钮（不该出现）')
  }
  if (warnings.length) {
    console.log(`\n控制台异常 ${warnings.length} 条：`)
    for (const w of warnings.slice(0, 10)) console.log(`  ${w}`)
    process.exitCode = 1
  } else {
    console.log('\n无控制台异常')
  }
} finally {
  await browser.close()
  await server.close()
}
