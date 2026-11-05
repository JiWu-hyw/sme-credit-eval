/**
 * 生产构建端到端校验（复现线上白屏的那套环境）
 *
 * 为什么不能只跑 dev 冒烟：
 *   线上是「vite build 产物 + 静态 SPA 托管」。事故恰恰出在这层——
 *   托管把 /api/* 回落成 index.html，前端拿到 HTML 当数据用，整页变错误态。
 *   所以必须在 **dist + 静态服务** 上再验一遍。
 *
 * 断言：
 *   ① /api/* 不再回 200 + HTML（服务端已改为 404 + JSON）；
 *   ② 八个路由都能打开且不白屏（正文非空、无错误态）；
 *   ③ P4 / P5 正文真的在（不是只闪一下骨架）；
 *   ④ 刷新（reload）后仍然有正文 —— 直接对应用户反馈的「一闪而过」。
 *
 * 用法：node scripts/dist-check.mjs
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const repo = resolve(here, '..')

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

const PORT = 11530
const BASE = `http://127.0.0.1:${PORT}`

/**
 * 托管脚本的位置有两种历史布局（仓库里在 serve/server.mjs，交付包里被压平到根目录），
 * 所以这里按「存在的那个」选，不要再写死一条路径 —— 写死的结果是交付包一解压就
 * "静态服务没起来"，而它其实是能起来的（FE-14 踩到）。
 */
const SERVER_CANDIDATES = [
  resolve(repo, 'server.mjs'), // 交付包内自带（FE-20 起），本仓库直接可用
  resolve(repo, '../serve/server.mjs'), // 旧仓库布局：静态服务在同级 serve/
  resolve(repo, '../server.mjs'), // 旧交付包：被压平到上级目录
]
const serverScript = SERVER_CANDIDATES.find((p) => existsSync(p))
if (!serverScript) throw new Error(`找不到静态托管脚本：${SERVER_CANDIDATES.join(' / ')}`)
const server = spawn(process.execPath, [serverScript], {
  env: { ...process.env, PORT: String(PORT) },
  stdio: 'ignore',
})

const waitServer = async () => {
  for (let i = 0; i < 60; i += 1) {
    try {
      const r = await fetch(`${BASE}/`)
      if (r.ok) return true
    } catch {
      /* 还没起来 */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  return false
}

const CHROMIUM_CANDIDATES = [
  process.env.CHROMIUM_PATH,
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/usr/bin/google-chrome',
].filter(Boolean)

let browser
try {
  if (!(await waitServer())) throw new Error('静态服务没起来')

  // ① 接口路径不得回落成 HTML
  const apiRes = await fetch(`${BASE}/api/data/pipeline`)
  const apiBody = await apiRes.text()
  report(apiRes.status === 404, `/api/* 不再回落 index.html（状态 ${apiRes.status}）`)
  report(!apiBody.trimStart().startsWith('<'), '/api/* 响应的不是 HTML')

  browser = await chromium.launch({
    executablePath: CHROMIUM_CANDIDATES.find((p) => existsSync(p)),
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  })
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()

  const pageErrors = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))

  const ROUTES = [
    ['/', '一句话结论'],
    ['/data', '数据接入链路'],
    ['/graph', '时空图谱'],
    ['/model', '结论先行'],
    ['/explain', '这一页真正想说的一句话'],
    ['/forecast', '生存力预测'],
    ['/enterprise', '信用评分与档位'],
    ['/batch', '批量评估'],
  ]

  for (const [path, marker] of ROUTES) {
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(800)
    const text = await page.locator('main').innerText()
    const blank = text.replace(/\s/g, '').length < 40
    report(!blank && text.includes(marker), `${path.padEnd(14)} 打开有正文（${marker}）`)
  }

  // ④ 刷新后仍有正文 —— 用户报的「只闪过一瞬间」就是刷新后没了
  for (const path of ['/model', '/explain', '/forecast', '/enterprise', '/batch']) {
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
    const text = await page.locator('main').innerText()
    const hasContent = text.replace(/\s/g, '').length > 200 && !text.includes('出了点问题')
    report(hasContent, `${path} 刷新后正文仍在（不白屏）`, `（${text.length} 字）`)
  }

  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')

  await ctx.close()
} finally {
  if (browser) await browser.close()
  server.kill('SIGTERM')
}

console.log(failed === 0 ? '\n生产构建校验全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
