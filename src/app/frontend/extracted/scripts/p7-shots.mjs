/**
 * P7 单企业深度诊断 截图（FE-16 交付物）
 *
 * 与 p5-shots / p6-shots 同一套做法（真浏览器 + 1440 宽 + 2 倍像素 + 按元素裁）：
 *   p7-full              整页
 *   p7-head              档案头部（匿名徽标 + 未接入字段 + 选择器）
 *   p7-score             信用评分仪表盘（五档色带 + PD + 档位序数）
 *   p7-radar             三类维度雷达（缺维度留缺口，不是画 0）
 *   p7-waterfall         SHAP 瀑布（22 段：E[f(x)] → 20 项 → f(x)）
 *   p7-waterfall-sorted  切成「按贡献方向」后的同一张图（证明排序真的重排）
 *   p7-graph             局部子图（中心 + 一跳邻居）
 *   p7-heat              时序贡献热力图
 *   p7-named-*           同一 URL 换成 u0007（高分组企业）：结构一模一样、数值全变
 *   p7-degrade-*         缺契约时的区块级降级（不白屏）
 *
 * 验收里那条「匿名态与真实态切换无需改结构」，光看一张截图是证明不了的 ——
 * 所以除了 u0001，还截一组 u0007：(a) 区块数、区块顺序、图表个数一致；
 * (b) 分数、PD、档位、瀑布形状全部不同。两张图并排看，就是这条验收的证据。
 *
 * 用法：npm run shots:p7
 */
import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer as createHttp } from 'node:http'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11573
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

  /** 裁某个元素（整页截图 + clip，避免 sticky 头重复出现） */
  const shotEl = async (name, selector, target = page) => {
    const el = target.locator(selector).first()
    await el.scrollIntoViewIfNeeded()
    await target.waitForTimeout(250)
    const box = await el.boundingBox()
    if (!box) throw new Error(`元素不可见：${selector}`)
    const y = box.y + (await target.evaluate(() => window.scrollY)) - 12
    await target.screenshot({
      path: resolve(outDir, `${name}.png`),
      clip: { x: 0, y, width: 1440, height: Math.min(box.height + 24, 6000) },
      fullPage: true,
    })
    console.log(`  ✓ ${name}.png`)
  }

  await page.goto(`${BASE}/enterprise/u0001`, { waitUntil: 'networkidle' })
  await settle()
  await page.waitForTimeout(700)

  console.log('截图：')
  await page.screenshot({ path: resolve(outDir, 'p7-full.png'), fullPage: true })
  console.log('  ✓ p7-full.png')

  await shotEl('p7-head', '[data-testid="p7-enterprise-head"]')
  await shotEl('p7-score', '[data-testid="p7-section-score"]')
  await shotEl('p7-radar', '[data-testid="p7-section-radar"]')
  await shotEl('p7-waterfall', '[data-testid="p7-waterfall-block"]')
  await shotEl('p7-graph', '[data-testid="p7-local-graph-block"]')
  await shotEl('p7-heat', '[data-testid="p7-heat-block"]')

  const sortBtn = page.locator('[data-testid="p7-sort-gradient"]')
  if (await sortBtn.count()) {
    await sortBtn.first().click()
    await page.waitForTimeout(500)
    await shotEl('p7-waterfall-sorted', '[data-testid="p7-waterfall-block"]')
    await page.locator('[data-testid="p7-sort-magnitude"]').first().click()
    await page.waitForTimeout(300)
  } else {
    console.log('  ! 未找到排序按钮，跳过 p7-waterfall-sorted')
  }

  /* ---------------- 换一家企业：结构不变、数值全变 ---------------- */
  const picker = page.locator('[data-testid="p7-enterprise-picker"]')
  if (await picker.count()) {
    await picker.selectOption('u0007')
    await page.waitForTimeout(900)
    await shotEl('p7-named-head', '[data-testid="p7-enterprise-head"]')
    await shotEl('p7-named-score', '[data-testid="p7-section-score"]')
    await shotEl('p7-named-waterfall', '[data-testid="p7-waterfall-block"]')
    await page.screenshot({ path: resolve(outDir, 'p7-named-full.png'), fullPage: true })
    console.log('  ✓ p7-named-full.png')
  } else {
    console.log('  ! 未找到企业选择器，跳过 p7-named-*')
  }

  /* --------------------- 降级：stub 后端返回畸形信封 --------------------- */
  const detailJson = JSON.parse(readFileSync(resolve(here, '../outputs/contracts/enterprise_detail.json'), 'utf8'))
  const localJson = JSON.parse(readFileSync(resolve(here, '../outputs/contracts/shap_local.json'), 'utf8'))
  const STUB_PORT = PORT + 1
  /**
   * 畸形白名单：命中的端点返回 `{code:0, data:{}}` ——
   * 信封合法（不是 HTML、不是 500），但业务字段全缺。
   * 这正是「契约文件被截断 / 字段被改名」的样子，也是页面结构守卫存在的理由。
   * real 模式（不兜底）下才能看到降级分支；auto 模式会拿 mock 兜底，截出来跟正常页一样。
   */
  let malformed = []
  const REAL = {
    '/api/enterprise/u0001': detailJson,
    '/api/explain/local/u0001': localJson,
  }
  const stub = createHttp((req, res) => {
    const url = (req.url ?? '').split('?')[0]
    const bad = malformed.some((p) => url.includes(p))
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(
      JSON.stringify({
        code: 0,
        message: 'ok',
        data: bad ? {} : (REAL[url]?.data ?? {}),
        meta: bad ? { dataset: 'real_panel', schema_version: '1.0', noise_floor: null } : (REAL[url]?.meta ?? {}),
      }),
    )
  })
  await new Promise((r) => stub.listen(STUB_PORT, '127.0.0.1', r))

  const DG = PORT + 2
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

  const dctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
  const dpage = await dctx.newPage()

  const openDegraded = async (paths) => {
    malformed = paths
    await dpage.goto(`http://127.0.0.1:${DG}/enterprise/u0001`, { waitUntil: 'networkidle' })
    await dpage.waitForTimeout(1400)
  }

  await openDegraded(['/api/enterprise/u0001'])
  await dpage.screenshot({
    path: resolve(outDir, 'p7-degrade-detail.png'),
    fullPage: true,
    clip: { x: 0, y: 0, width: 1440, height: 1300 },
  })
  console.log('  ✓ p7-degrade-detail.png')

  await openDegraded(['/api/enterprise/u0001', '/api/explain/local/u0001'])
  await dpage.screenshot({
    path: resolve(outDir, 'p7-degrade-all.png'),
    fullPage: true,
    clip: { x: 0, y: 0, width: 1440, height: 1300 },
  })
  console.log('  ✓ p7-degrade-all.png')

  await dctx.close()
  await degrade.close()
  await new Promise((r) => stub.close(r))

  const real = warnings.filter((w) => !w.includes('v-scx'))
  console.log(real.length ? `\n控制台告警 ${real.length} 条：\n${real.slice(0, 5).join('\n')}` : '\n控制台无告警')
} finally {
  await browser.close()
  await server.close()
}
