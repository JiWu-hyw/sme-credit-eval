/**
 * 路由冒烟测试（FE-00 验收项：七路由可跳转）
 *
 * 用 Vite 的 SSR 通道真实编译并渲染 App + 七个页面组件，
 * 断言每个路由都能渲染出对应页面骨架，且过程中无 Vue 运行时告警。
 * 每次渲染都新建 app 实例 —— 复用实例会触发 v-scx 重复注入的假告警。
 *
 * P1–P7 全部已交付真实页面，「占位面板」这一项目前没有任何路由可以断言 ——
 * 保留这个分支是为了下次新增路由时它还能拦住「只挂了个壳」的页面。
 *
 * 每交付一页，这里就跟着改一行 —— 这是「已交付」的登记簿：
 * 忘改的话路由冒烟会红，正好逼着人确认新页面真渲染出来了，而不是只剩一个占位壳。
 *
 * 用法：node scripts/route-smoke.mjs
 */
import { createPinia } from 'pinia'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'

/** 路径严格对齐《前端页面与交互原型说明》v1.0 §三 的路由表 */
const CASES = [
  // P1 已交付：断言页头（正文依赖异步接口，交给 scripts/p1-smoke.mjs）
  ['/', 'P1', '概览', false, 'GET /api/overview/summary'],
  // P2 已交付（FE-11）：断言真实页头，而不是占位面板
  ['/data', 'P2', '数据与特征', false, '个区块 · 4 个接口'],
  // P3 已交付（FE-12）：断言真实页头，而不是占位面板
  ['/graph', 'P3', '时空图谱', false, '共 4 个区块 · 3 个接口'],
  // P4 / P5 已交付：断言真实区块标题
  ['/model', 'P4', '模型与对比', false, 'GET /api/model/benchmark'],
  ['/explain', 'P5', '可解释性', false, 'GET /api/explain/global'],
  // P6 已交付（FE-15）：断言真实页头（两个接口并列），而不是占位面板
  ['/forecast', 'P6', '前瞻预测', false, 'GET /api/forecast/survival'],
  // P7 已交付（FE-16）：断言真实页头里的接口清单，而不是占位面板
  ['/enterprise/u0001', 'P7', '单企业深度诊断', false, 'GET /api/explain/local/{userid}'],
]

const vite = await createServer({
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
})

let failed = 0

const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

try {
  const { default: App } = await vite.ssrLoadModule('/src/App.vue')
  const { ROUTES, NAV_ROUTES } = await vite.ssrLoadModule('/src/router/index.ts')

  /**
   * 在**服务端渲染之前**把骨架屏延迟调到 0。
   *
   * 页面里 `useAsyncSection` 默认 `skeletonDelay = 300ms`，本意是「本地 mock 秒回时
   * 不要闪一下骨架」，但在 Node 里没有任何定时器会跑，那 300ms 永远不会到点：
   * `showSkeleton` 一直是 false，于是 SSR 出来的只有「骨架之外的壳」。
   * 更要紧的是 —— `App.vue` 里的 `<Suspense>` 会等待所有 async setup，
   * 而 `load()` 是在 `await loader()` 的 microtask 里推进的，等 Suspense 吃完
   * microtask 队列时数据**已经到了**，所以真实浏览器首屏是正文，不是骨架。
   *
   * 结论：拿 SSR 断言「正文区块」本身是错的。这里先把阈值打到 0（让 SSR 至少能
   * 把骨架/正文之一完整渲染出来），正文断言交给 scripts/p4p5-smoke.mjs 在真浏览器里做。
   */
  const prevSkeletonDelay = process.env.VITE_SKELETON_DELAY_MS
  process.env.VITE_SKELETON_DELAY_MS = '0'

  /** 新建一套 router + app，渲染指定路径，返回 html 与 Vue 告警列表 */
  async function render(path) {
    const warnings = []
    const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
    const app = createSSRApp(App)

    app.use(createPinia())
    app.use(router)
    app.config.warnHandler = (msg) => warnings.push(msg)

    await router.push(path)
    await router.isReady()

    return { router, html: await renderToString(app), warnings }
  }

  for (const [path, code, title, isPlaceholder, marker] of CASES) {
    const { html, warnings } = await render(path)
    const checks = [
      // 编号要精确匹配，不能用 includes('P1')——P1 是 P1x 的前缀，全量计数会串
      ['页面编号', new RegExp(`placeholder__code[^>]*>${code}<`).test(html) || html.includes(`>${code}<`)],
      ['页面标题', html.includes(title)],
      // 占位页断言「占位面板」；已交付页面断言自己的**页头**（页头是同步渲染的，
      // 正文区块要等异步数据，那条留给真浏览器冒烟）
      isPlaceholder ? ['占位面板', html.includes('该页面尚未实现')] : ['页头标识', html.includes(marker ?? '')],
      ['导航项', (html.match(/data-testid="nav-item"/g) ?? []).length >= 7],
    ]
    const bad = checks.filter(([, ok]) => !ok).map(([name]) => name)

    report(
      bad.length === 0 && warnings.length === 0,
      `${path.padEnd(20)} ${code} ${title}`,
      (bad.length ? `  ← 未通过：${bad.join('、')}` : '') +
        (warnings.length ? `  ← 告警：${warnings.join(' | ')}` : ''),
    )
  }

  const notFound = await render('/not-a-page')
  report(notFound.html.includes('404'), '/not-a-page 兜底 404')

  // P7 的 userid 是可选的：无参时落到匿名队列第一家，且**不改写地址栏**
  const noParam = await render('/enterprise')
  report(
    noParam.html.includes('P7') && noParam.html.includes('匿名企业 u0001'),
    '/enterprise（无 userid）落到队列第一家 u0001（地址栏保持 /enterprise）',
  )

  // 导航项按 NAV_ROUTES 算，不是按 ROUTES——ROUTES 还含 404 兜底路由，
  // 用 ROUTES 过滤 count 永远多一条（这条曾经误报了很久）。
  const navCount = NAV_ROUTES.length
  report(navCount === 8, `导航项数量 = ${navCount}（期望 8，P8 批量评估 FE-20 新增）`)

  // 导航一律用 name 解析：P7 必须解析为 /enterprise，而不是带占位符的模板串
  const probe = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  for (const item of NAV_ROUTES) {
    const resolved = probe.resolve({ name: item.name })
    const ok =
      resolved.name === item.name && typeof resolved.path === 'string' && !resolved.path.includes(':')
    report(ok, `导航 ${item.meta.code} ${item.name} → ${resolved.path}`)
  }

  // `/overview` 不是契约路由，必须落到兜底而不是静默重定向
  const stray = probe.resolve('/overview')
  report(stray.name === 'not-found', `/overview 未定义 → 落到 ${String(stray.name)}`)
} finally {
  await vite.close()
}

console.log(failed === 0 ? '\n路由冒烟测试全部通过' : `\n存在 ${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
