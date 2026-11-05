/**
 * 线上 P3 验收（发布后跑一次，确认「本地绿的」在公网上也是绿的）
 *
 * 本地冒烟跑的是 dev server，线上跑的是构建产物 + 静态托管 + 访问控制（sharecode）。
 * 这三件事任何一件都可能让页面变形，所以发布后必须再验一遍真链接。
 *
 * 只验 P3 的要点，不复刻 43 项：区块齐备、力导向图有像素、图例可筛、滑块可切、
 * 点节点开浮层、红线角标 6/6、无运行时异常。
 *
 * 用法：node scripts/online-p3-check.mjs "<分享链接>"
 */
import { existsSync } from 'node:fs'
import { chromium } from 'playwright'

const link = process.argv[2]
if (!link) {
  console.error('用法：node scripts/online-p3-check.mjs "<分享链接>"')
  process.exit(2)
}
const url = new URL('/graph', link)
url.search = new URL(link).search // sharecode 是访问凭据，子路径必须带上，否则是「链接不完整」页

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra ? `  ← ${extra}` : ''}`)
}

const candidates = [process.env.CHROMIUM_PATH, '/usr/bin/chromium', '/usr/bin/google-chrome'].filter(Boolean)
const browser = await chromium.launch({
  executablePath: candidates.find((p) => existsSync(p)),
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await page.goto(url.toString(), { waitUntil: 'networkidle' })
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 30_000 },
  )
  await page.waitForTimeout(1500)

  report((await page.locator('h1.page__title').innerText()).includes('P3 时空图谱'), '线上页头为 P3 时空图谱')
  for (const id of ['e1', 'e2', 'e3', 'e4']) {
    report((await page.locator(`#${id}`).count()) > 0, `线上区块 ${id} 存在`)
  }

  const canvases = await page.locator('[data-testid="chart-panel"] canvas').count()
  report(canvases >= 5, `线上图表 canvas ${canvases} 个（≥5）`)

  // 像素法：canvas 真有东西，不是空白占位
  const canvas = page.locator('[data-testid="chart-panel"] canvas').first()
  const shot = await canvas.screenshot()
  const png = shot.toString('base64')
  report(png.length > 20_000, `力导向图 canvas 非空白（base64 ${Math.round(png.length / 1024)} KB）`)

  // 筛选
  const before = await page.locator('.graph-legend__btn', { hasText: '专利' }).first().getAttribute('aria-pressed')
  await page.locator('.graph-legend__btn', { hasText: '专利' }).first().click()
  await page.waitForTimeout(600)
  const after = await page.locator('.graph-legend__btn', { hasText: '专利' }).first().getAttribute('aria-pressed')
  report(before === 'true' && after === 'false', '线上图例可筛选（aria-pressed true→false）')
  await page.locator('.graph-legend__btn', { hasText: '专利' }).first().click()
  await page.waitForTimeout(400)

  // 季度切到 2024Q2：用原生 range input 直接设值 + 派发 input 事件（线上没有"刻度按钮"）
  await page.locator('[data-testid="slice-slider"]').evaluate((el) => {
    el.value = '1'
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await page.waitForTimeout(600)
  const body = await page.locator('main').innerText()
  report(/2024Q2/.test(body), '线上滑块可切季度（当前 2024Q2）')

  // 节点浮层
  await canvas.scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  const box = await canvas.boundingBox()
  let opened = false
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
  report(opened, '线上点节点可开浮层')
  if (opened) {
    const drawer = await page.locator('[data-testid="node-drawer"]').innerText()
    report(/四类中心性/.test(drawer) && /关联实体/.test(drawer), '线上浮层含四类中心性与关联实体')
    await page.keyboard.press('Escape')
    await page.waitForTimeout(300)
    report((await page.locator('[data-testid="node-drawer"]').count()) === 0, '线上 Esc 关闭浮层')
  }

  const badges = await page.locator('[data-testid="dataset-badge"]').count()
  const charts = await page.locator('[data-testid="chart-panel"]').count()
  report(badges === charts && charts > 0, `线上每张图都带数据来源角标（${badges}/${charts}）`)
  report((await page.locator('[data-testid="synthetic-notice"]').count()) === 1, '线上合成图声明条存在')

  const realErrors = errors.filter((e) => !/Failed to load resource/.test(e))
  report(realErrors.length === 0, '线上无运行时异常', realErrors[0] ?? '')

  await ctx.close()
} finally {
  await browser.close()
}

console.log(failed === 0 ? '\n线上 P3 验收全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
