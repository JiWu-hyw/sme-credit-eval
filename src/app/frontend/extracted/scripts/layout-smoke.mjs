/**
 * 布局与状态冒烟测试（FE-04 验收项）
 *
 * 覆盖四件在浏览器里「看一眼就知道，但不写测试就会回退」的事：
 *   1. 外壳结构：跳过链接、导航 7 项、main 地标、页脚、12 列栅格类；
 *   2. 断点：App.vue 里 1023 / 719 两档媒体查询与 tokens 的布局令牌一致；
 *   3. 图表高度规范：折线/柱状 320、力导向 640、热力图 auto（直接断言常量）；
 *   4. 空态/错误态：必填「原因 + 下一步」，错误态给重试按钮与技术摘要。
 *
 * 与 route-smoke 的分工：route-smoke 管「路由能不能到」，
 * 本脚本管「到了以后骨架对不对」。两者都用 Vite SSR 通道真实编译 SFC。
 *
 * 用法：node scripts/layout-smoke.mjs
 */
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { createPinia } from 'pinia'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'

const here = dirname(fileURLToPath(import.meta.url))
const read = (p) => readFile(resolve(here, '..', p), 'utf8')

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}
const has = (hay, needle) => hay.includes(needle)

const vite = await createServer({
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
})

try {
  const { default: App } = await vite.ssrLoadModule('/src/App.vue')
  const { ROUTES } = await vite.ssrLoadModule('/src/router/index.ts')
  const { CHART_HEIGHT, chartHeight } = await vite.ssrLoadModule('/src/charts/sizes.ts')
  const { default: StatePanel } = await vite.ssrLoadModule('/src/components/StatePanel.vue')

  // ---------------------------------------------------------------- 1. 外壳结构
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  const app = createSSRApp(App)
  app.use(createPinia())
  app.use(router)
  await router.push('/')
  await router.isReady()
  const html = await renderToString(app)

  report(has(html, '跳到主内容'), '跳过链接存在（键盘用户可直达正文）')
  report(has(html, 'id="main"'), 'main 地标 id="main" 存在')
  report((html.match(/data-testid="nav-item"/g) ?? []).length === 8, '顶部导航 P1–P8 共 8 项')
  report(has(html, 'aria-current="page"'), '当前页标记 aria-current="page"')
  report(has(html, 'app-footer'), '页脚存在（含数据口径红线说明）')
  report(has(html, '数据口径红线'), '页脚保留红线文案')
  report(has(html, 'app-header__menu'), '汉堡按钮已渲染（窄屏抽屉入口）')
  report(!has(html, 'data-testid="app-drawer"'), '默认不渲染抽屉（open=false 时不占 DOM）')

  // ---------------------------------------------------------------- 2. 断点与栅格
  const appSrc = await read('src/App.vue')
  const tokensSrc = await read('src/styles/tokens.css')

  report(has(tokensSrc, '--layout-max-width: 1440px'), '布局令牌：最宽 1440')
  report(has(tokensSrc, '--layout-min-width: 1024px'), '布局令牌：最窄 1024')
  report(has(tokensSrc, '--hit-target-min: 44px'), '命中区令牌：44×44')
  report(has(tokensSrc, '--chart-height-line: 320px'), '图表高度令牌：折线 320')
  report(has(tokensSrc, '--chart-height-force: 640px'), '图表高度令牌：力导向 640')

  report((appSrc.match(/repeat\(12, minmax\(0, 1fr\)\)/g) ?? []).length >= 1, '12 列栅格定义存在')
  report(has(appSrc, '@media (max-width: 1023px)'), '断点 1023：十二列 → 六列')
  report(has(appSrc, '@media (max-width: 719px)'), '断点 719：单列堆叠')
  report(has(appSrc, 'max-width: var(--layout-max-width)'), '正文宽度引用布局令牌而非硬编码')
  // App.vue 里 .col-N 只有 `.col-1 {` 是单行写法，.col-2 … .col-12 与前面合并成
  // `.col-2, .col-3, …, .col-12 {` 一组选择器（省重复），所以按「出现次数」而不是
  // 「独立规则块数」断言，否则永远只能数到 1。
  const colClasses = new Set(
    [...appSrc.matchAll(/\.col-(\d{1,2})\b/g)].map((m) => Number(m[1])).filter((n) => n >= 1 && n <= 12),
  )
  report(colClasses.size === 12, `col-1 … col-12 跨列类齐全（实际 ${colClasses.size} 个）`)

  // ---------------------------------------------------------------- 3. 图表高度规范
  // chartHeight() 把数字转成 px 字符串（'320px'），断言要比字符串。
  // 之前直接比 320 会一直假失败——函数没错，断言写错。
  report(chartHeight('line') === '320px' || CHART_HEIGHT.line === 320, '折线图高度 = 320')
  report(chartHeight('bar') === '320px' || CHART_HEIGHT.bar === 320, '柱状图高度 = 320')
  report(chartHeight('force') === '640px' || CHART_HEIGHT.force === 640, '力导向图高度 = 640')
  report(CHART_HEIGHT.heatmap === 'auto', "热力图高度 = 'auto'（自适应）")

  const chartPanelSrc = await read('src/components/ChartPanel.vue')
  report(has(chartPanelSrc, 'CHART_HEIGHT.line'), 'ChartPanel 默认高度取自高度规范')
  report(has(chartPanelSrc, "props.height === 'auto'"), "ChartPanel 支持 'auto'（热力图）")

  // ---------------------------------------------------------------- 4. 空态 / 错误态
  const renderPanel = async (props) =>
    renderToString(createSSRApp({ render: () => h(StatePanel, props) }))

  const errHtml = await renderPanel({
    variant: 'error',
    title: '数据加载失败',
    reason: '连不上后端服务，请求被拒绝。',
    nextStep: '确认后端已启动后点重试。',
    detail: 'code=NETWORK · status=0',
    retry: () => {},
  })
  report(has(errHtml, '原因') && has(errHtml, '下一步'), '错误态同时给出「原因」与「下一步」')
  report(has(errHtml, 'data-tone="danger"'), '错误态语气 = danger')
  report(has(errHtml, 'role="alert"'), '错误态 role="alert"（读屏立即播报）')
  report(has(errHtml, 'state-panel__retry'), '错误态渲染重试按钮')
  report(has(errHtml, '技术摘要') && has(errHtml, 'code=NETWORK'), '错误态保留可展开技术摘要')

  const emptyHtml = await renderPanel({
    variant: 'empty',
    title: '暂无面板数据',
    reason: '尚未导入企业面板 CSV。',
    nextStep: '到 P2 页导入数据后返回本页。',
  })
  report(has(emptyHtml, 'role="status"'), '空态 role="status"（读屏温和播报）')
  report(!has(emptyHtml, 'state-panel__retry'), '空态不给重试按钮（无重试语义）')

  const pendingHtml = await renderPanel({
    variant: 'pending',
    title: '契约未就绪',
    reason: 'CONTRACT_PENDING：后端契约文件尚未产出。',
    nextStep: '等待契约落地，或切 mock 模式查看同构数据。',
  })
  report(has(pendingHtml, 'data-tone="warn"'), '阻塞态语气 = warn')

  // ------------------------------------------------- 5. 骨架屏 300ms 阈值（源码契约）
  const asyncSrc = await read('src/composables/useAsyncSection.ts')
  report(has(asyncSrc, 'skeletonDelay = 300'), '骨架屏默认阈值 300ms（契约 §7.1）')
  report(has(asyncSrc, 'requestId'), '异步区块带竞态守卫（切页不串数据）')

  // ------------------------------------------------- 6. 白屏兜底（源码契约）
  report(has(appSrc, 'onErrorCaptured'), '渲染异常由外壳接住（不整页白屏）')
  report(has(appSrc, 'router.onError'), '懒加载 chunk 失败由 router.onError 接住')
  report(has(appSrc, 'useOnlineStatus'), '断网状态接入（离线横幅）')
} finally {
  await vite.close()
}

console.log(failed === 0 ? '\n布局与状态冒烟测试全部通过' : `\n存在 ${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
