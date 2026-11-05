/**
 * 最小静态托管（交付包自带的 `npm run preview:dist` 入口）
 *
 * 为什么要在交付包里放一个只有 100 行的静态服务器：
 *   1. **复现线上的那层**：线上事故（`/api/*` 被回落成 index.html）只在这层出现，
 *      dev server 永远复现不了。`scripts/dist-check.mjs` 需要有东西能起在 dist 上。
 *   2. **零依赖**：只用 node 内置的 `http` / `fs`。评审电脑上 `npm ci` 可能跑不通，
 *      但只要有 node 就能把构建产物起起来看一眼。
 *
 * 三条约定（就是线上 SPA 托管该有的样子）：
 *   · `/api/*` 未命中 → **404 + JSON**，绝不回落 index.html
 *     （回落成 HTML 会让前端把一段 `<html>` 当数据用，整页白屏 —— 这是真实事故）；
 *   · 其余未命中且没有扩展名 → 回落 index.html（history 路由）；
 *   · 命中目录 / 无扩展名路径找不到文件时也回落，但只对 GET / HEAD。
 *
 * 用法：PORT=8080 node server.mjs   （默认端口 8080，静态根 = 本文件同级的 dist/）
 */
import { createServer } from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(fileURLToPath(new URL('.', import.meta.url)), 'dist')
const PORT = Number(process.env.PORT ?? 8080)
const HOST = process.env.HOST ?? '127.0.0.1'

/** 扩展名 → Content-Type（够用即可，不引 mime 包） */
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.csv': 'text/csv; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
}

const json = (res, status, payload) => {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(payload))
}

const server = createServer((req, res) => {
  const method = req.method ?? 'GET'
  if (method !== 'GET' && method !== 'HEAD') {
    json(res, 405, { code: 'METHOD_NOT_ALLOWED', message: `${method} 不被支持` })
    return
  }

  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`)
  const pathname = decodeURIComponent(url.pathname)

  // ① 接口路径绝不回落 HTML —— 这条是这套服务存在的首要理由
  if (pathname.startsWith('/api/')) {
    json(res, 404, {
      code: 'NOT_FOUND',
      message: `接口未实现：${pathname}`,
      hint: '本包只托管前端静态产物；后端未随包分发。演示请在前端用 VITE_USE_MOCK=mock。',
    })
    return
  }

  // ② 静态文件（防目录穿越：normalize 后必须仍在 ROOT 内）
  const target = normalize(join(ROOT, pathname))
  if (target.startsWith(ROOT) && existsSync(target) && statSync(target).isFile()) {
    const type = TYPES[extname(target).toLowerCase()] ?? 'application/octet-stream'
    res.writeHead(200, {
      'Content-Type': type,
      // 带 hash 的产物长缓存，index.html 不缓存（发版后立刻生效）
      'Cache-Control': /\/assets\//.test(pathname) ? 'public, max-age=31536000, immutable' : 'no-cache',
    })
    if (method === 'HEAD') {
      res.end()
      return
    }
    createReadStream(target).pipe(res)
    return
  }

  // ③ history 路由回落：只有无扩展名的路径才回落（`/batch` → index.html；
  //    `/assets/old.js` 找不到就该 404，回落成 HTML 会让浏览器把 HTML 当 JS 执行）
  if (extname(pathname) === '') {
    const index = join(ROOT, 'index.html')
    if (existsSync(index)) {
      res.writeHead(200, { 'Content-Type': TYPES['.html'], 'Cache-Control': 'no-cache' })
      if (method === 'HEAD') {
        res.end()
        return
      }
      createReadStream(index).pipe(res)
      return
    }
  }

  json(res, 404, { code: 'NOT_FOUND', message: `找不到 ${pathname}` })
})

server.listen(PORT, HOST, () => {
  console.log(`静态托管已启动：http://${HOST}:${PORT}  （根目录 ${ROOT}）`)
})
