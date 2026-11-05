/**
 * P6 前瞻预测 截图（FE-15 交付物）
 *
 * 与 p5-shots.mjs 同一套做法（真浏览器 + 1440 宽 + 2 倍像素 + 按图裁）：
 *   p6-full            整页
 *   p6-a16-km          A16 生存曲线（含三条分层曲线 + 置信带）
 *   p6-a16-single      切到「高风险层」后 —— 证明分层是真过滤，带子只留一条
 *   p6-a16-cox         Cox 系数表（代号 + HR + 森林条）
 *   p6-a17-calibration A17 概率校准曲线（含 45° 参考线与 Wilson 十字须）
 *   p6-a17-valuation   估值区间条（P10/P50/P90）
 *   p6-degrade-*       缺契约时的区块级降级（A16 挂、A16+A17 全挂）
 *
 * 降级截图的做法（这一块返工了三轮，踩的坑都写下来，因为它们是本页最容易自欺的地方）：
 *
 *   坑①：想用 page.route 通配拦截 /forecast/survival —— 一次都没命中。
 *        套件跑在 mock 模式下，call() 在 mode==='mock' 时直接 return getMock(key)，
 *        压根不发 HTTP。于是「降级截图」截到的其实是正常页（与 p6-full.png md5 相同）。
 *
 *   坑②：改成在浏览器里劫持 http.get —— 仍然没用。Vite dev 按需编译，
 *        page.evaluate 里 dynamic import 拿到的是另一个模块实例，补丁打空了。
 *
 *   坑③（最关键的一条认知错误）：改成 stub 后端返回 500 —— 还是没用。
 *        **因为 auto 模式的既定行为就是「后端挂了就用 mock 兜底」**，
 *        500 被 call() 吃掉、回落 getMock，页面照常渲染完整契约。
 *        也就是说：500 根本不是本页降级分支的触发条件。
 *
 *   正解：触发条件有两个，分别对应测试环境与线上：
 *     (a) `VITE_USE_MOCK=real`（不兜底的后端联调态）：端点失败一路抛到 error 分支；
 *     (b) `auto` 模式 + 200 畸形信封（`{code:0, data:{}}`，缺 curves/bins）：
 *         信封形状合法，但业务字段缺失，由页面的**结构守卫**拦下 → pending 分支。
 *     (b) 正是「契约文件被截断 / 字段改名」这种最难发现的故障，优先用它。
 *
 *   两组场景：
 *     p6-degrade-a16   只让 A16 畸形 → A16 降级，A17 与结论区照常；
 *     p6-degrade-all   A16 + A17 都畸形 → 两块都降级，页头 / 声明条 / 结论区仍在（不白屏）。
 *
 * 用法：npm run shots:p6
 */
import { existsSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer } from 'vite'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const outDir = resolve(here, '../docs/preview')
mkdirSync(outDir, { recursive: true })

const PORT = 11572
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
    target.waitForFunction(
      () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
      null,
      { timeout: 20_000 },
    )

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

  await page.goto(`${BASE}/forecast`, { waitUntil: 'networkidle' })
  await settle()
  await page.waitForTimeout(600)

  console.log('截图：')
  await page.screenshot({ path: resolve(outDir, 'p6-full.png'), fullPage: true })
  console.log('  ✓ p6-full.png')

  await shotEl('p6-a16-km', '#a16-km')
  await shotEl('p6-a16-cox', '[data-testid="survival-cox"]')

  const chip = page.locator('[data-testid="survival-stratum-chip-high"]')
  if (await chip.count()) {
    await chip.first().click()
    await page.waitForTimeout(500)
    await shotEl('p6-a16-single', '#a16-km')
    await page.locator('[data-testid="survival-stratum-chip-all"]').first().click()
    await page.waitForTimeout(300)
  } else {
    console.log('  ! 未找到分层 chip，跳过 p6-a16-single')
  }

  await shotEl('p6-a17-calibration', '#a17-calibration')
  await shotEl('p6-a17-valuation', '[data-testid="valuation-block"]')

  /* --------------------- 降级：stub 后端返回畸形信封 --------------------- */
  const { readFileSync } = await import('node:fs')
  const survivalJson = JSON.parse(readFileSync(resolve(here, '../outputs/contracts/survival.json'), 'utf8'))
  const financingJson = JSON.parse(readFileSync(resolve(here, '../outputs/contracts/financing.json'), 'utf8'))
  const { createServer: createHttp } = await import('node:http')
  const STUB_PORT = PORT + 1
  /**
   * 畸形白名单：命中的端点返回 `{code:0, data:{}}` ——
   * 信封合法（不是 HTML、不是 500），但业务字段全缺。
   * 这正是「契约文件被截断 / 字段被改名」的样子，也是页面结构守卫存在的理由。
   */
  let malformed = []
  /** 真契约：白名单外的端点必须返回**完整 body**，否则「只挂 A16」会连带把 A17 也打空 */
  const REAL = {
    '/api/forecast/survival': survivalJson,
    '/api/forecast/financing': financingJson,
  }
  const stub = createHttp((req, res) => {
    const url = (req.url ?? '').split('?')[0]
    const bad = malformed.some((p) => url.includes(p))
    const body = bad ? {} : (REAL[url] ?? {})
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(
      JSON.stringify({
        code: 0,
        message: 'ok',
        data: bad ? {} : body.data,
        meta: bad
          ? { dataset: 'real_panel', schema_version: '1.0', noise_floor: null }
          : body.meta,
      }),
    )
  })
  await new Promise((resolve) => stub.listen(STUB_PORT, '127.0.0.1', resolve))

  /**
   * 独立 vite server（不继承主 config）：/api 指向 stub，`VITE_USE_MOCK='real'`。
   * 用 real 而不是 auto —— auto 会在 500/畸形时回落 mock，测不到降级；
   * real 态下畸形信封会原样交给页面，由结构守卫判定 → pending 面板。
   */
  const degradeServer = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    mode: 'test',
    define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('real') },
    plugins: [(await import('@vitejs/plugin-vue')).default()],
    resolve: { alias: { '@': resolve(here, '../src') } },
    server: { host: '127.0.0.1', port: PORT + 2, strictPort: true, proxy: { '/api': { target: `http://127.0.0.1:${STUB_PORT}`, changeOrigin: true } } },
    logLevel: 'error',
  })
  await degradeServer.listen()
  const DGRADE = `http://127.0.0.1:${PORT + 2}`

  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
  const failPage = await ctx2.newPage()
  const failWarnings = []
  failPage.on('pageerror', (e) => failWarnings.push(`[pageerror] ${e.message}`))

  const openWithMalformed = async (paths) => {
    malformed = paths
    await failPage.goto(`${DGRADE}/forecast`, { waitUntil: 'networkidle' })
    try {
      await settle(failPage)
    } catch {
      /* 降级页没有骨架，超时正常 */
    }
    await failPage.waitForTimeout(900)
  }

  console.log('降级场景（real 模式 + 畸形信封，触发页面结构守卫）：')

  // ① 只让 A16 畸形
  await openWithMalformed(['/api/forecast/survival'])
  const kmGone = await failPage.locator('#a16-km').count()
  const a17Alive = await failPage.locator('#a17-calibration').count()
  const panelA16 = await failPage.locator('[data-testid="section-a16"] [data-testid="state-panel"]').count()
  const panelA17 = await failPage.locator('[data-testid="section-a17"] [data-testid="state-panel"]').count()
  const isolated = panelA16 === 1 && kmGone === 0 && a17Alive === 1 && panelA17 === 0
  console.log(
    `  · 只挂 A16 → A16 降级面板 ${panelA16} 个（KM 图${kmGone === 0 ? '已移除' : '仍在'}）` +
      `；A17 降级面板 ${panelA17} 个（校准图${a17Alive > 0 ? '仍在' : '丢失'}）` +
      `　${isolated ? '✓ 区块级隔离成立' : '✗ 区块未隔离'}`,
  )
  await shotEl('p6-degrade-a16', '[data-testid="section-a16"]', failPage)

  // ② A16 + A17 都畸形
  await openWithMalformed(['/api/forecast/survival', '/api/forecast/financing'])
  const bodyText = (await failPage.locator('body').innerText()).trim()
  const panels = await failPage.locator('[data-testid="state-panel"]').count()
  const head = await failPage.locator('.page__title').count()
  const ledger = await failPage.locator('[data-testid="forecast-mock-strip"]').count()
  const qa = await failPage.locator('.qa__item').count()
  /**
   * 双挂时的合格判据（刻意不含「结论区仍在」）：
   *   结论区三句话每条都要引用契约数字（S(24)、Brier、宽窄比），
   *   两契约都缺时它**本来就该不显示** —— 硬留一个空结论区反而是错的。
   *   要守住的是「页面仍在、说明仍在、降级原因可读」：页头 + 声明条 + 两个降级面板 + 读图问答。
   */
  /**
   * 声明条也不在判据里：`mock-strip` 的文案取自契约的 `meta.disclaimer` 与 `unlock`，
   * 契约缺了它就**没有话可说** —— 硬造一条"本区块为占位数据"的横幅反而是在撒谎
   * （此刻我们并不知道数据是什么来源）。降级面板里的「原因 + 下一步」已经把这层讲清楚了。
   */
  const noBlank = bodyText.length > 200 && panels === 2 && head === 1 && qa >= 3
  console.log(
    `  · 双挂 → 页面文本 ${bodyText.length} 字（0 = 白屏）· 降级面板 ${panels} 个` +
      ` · 页头${head ? '仍在' : '丢失'} · 读图问答 ${qa} 条` +
      ` · 声明条${ledger ? '在（有 disclaimer 可引用）' : '略（契约缺，无话可说）'}` +
      `　${noBlank ? '✓ 不白屏' : '✗ 检查降级'}`,
  )
  await failPage.screenshot({ path: resolve(outDir, 'p6-degrade-all.png'), fullPage: true })
  console.log('  ✓ p6-degrade-all.png')
  if (failWarnings.length) console.log(`  ! 降级页 pageerror ${failWarnings.length} 条：${failWarnings[0]}`)
  await ctx2.close()
  await degradeServer.close()
  await new Promise((resolve) => stub.close(resolve))

  console.log(`\n控制台告警 ${warnings.length} 条`)
  for (const w of warnings.slice(0, 10)) console.log(`  ${w}`)
  console.log(`\n输出目录：${outDir}`)
} finally {
  await browser.close()
  await server.close()
}
