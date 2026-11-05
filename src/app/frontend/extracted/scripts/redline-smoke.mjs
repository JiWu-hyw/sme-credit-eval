/**
 * 红线冒烟测试（FE-03 三条数据口径红线的「机制级」验收）
 *
 * 为什么单独一条脚本：红线不是文案，是**机制**。这里不打页面，直接测那层机制
 * 会不会在有人图省事时失效：
 *
 *   红线一（契约 §6.3）同一图表不得混排 real_panel / synthetic_graph
 *        → assertSingleDataset：混排必须抛错；单一来源必须原样返回；
 *          'mixed' 是合法标注，但不能与另外两个并存。
 *   红线二（契约 §6.5）语义未知字段只显代号
 *        → assertFieldDisplayable：把 x037 包装成「营业收入」必须抛错；
 *          x037 / x_37 之类代号形式必须放行。
 *   红线三（契约 §6.4）真实面板性能图同屏噪声地板
 *        → hasNoiseFloor：real_panel 缺 floor 判不合格，synthetic_graph 不要求；
 *          并且 ChartPanel 的 dataset 是**必填 prop**（缺角标编译不过），
 *          再加一层源码断言：ChartPanel 必须内嵌 DatasetBadge 与 NoiseFloorNote。
 *
 * 另附组件渲染断言：DatasetBadge 对 synthetic_graph 必须走 warning 基调
 * （合成数据用于架构验证，观感上不能与实证结论一致）。
 *
 * 用法：node scripts/redline-smoke.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const read = (rel) => readFileSync(resolve(root, rel), 'utf8')

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}

/** 期望抛 RedLineError 的调用 */
const throws = (fn) => {
  try {
    fn()
    return null
  } catch (e) {
    return e
  }
}

const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' })

try {
  const { assertSingleDataset, assertFieldDisplayable, hasNoiseFloor, isUnknownFieldCode, NOISE_FLOOR } =
    await vite.ssrLoadModule('/src/utils/redlines.ts')
  const { default: DatasetBadge } = await vite.ssrLoadModule('/src/components/DatasetBadge.vue')
  const { default: NoiseFloorNote } = await vite.ssrLoadModule('/src/components/NoiseFloorNote.vue')

  // ------------------------------------------------------------ 红线一
  const mixedErr = throws(() => assertSingleDataset(['real_panel', 'synthetic_graph']))
  report(mixedErr !== null, '红线一：真实面板 + 合成图同图 → 抛错')
  report(
    mixedErr !== null && /混排|不得同图/.test(String(mixedErr.message)),
    '红线一：报错文案说清「不得同图」，不是裸 500',
  )
  report(assertSingleDataset(['real_panel', 'real_panel']) === 'real_panel', '红线一：重复来源视为单一来源')
  report(assertSingleDataset(['synthetic_graph']) === 'synthetic_graph', '红线一：单一合成来源放行')
  report(assertSingleDataset([]) === 'real_panel', '红线一：空来源回落到 real_panel（不静默产出无角标图）')
  report(
    throws(() => assertSingleDataset(['mixed', 'real_panel'])) !== null,
    '红线一：mixed 标注不能与具体来源并存',
  )

  // ------------------------------------------------------------ 红线二
  const wrapErr = throws(() => assertFieldDisplayable('营业收入', false))
  report(wrapErr !== null, '红线二：把未知字段包装成业务名 → 抛错')
  report(
    wrapErr !== null && /代号/.test(String(wrapErr.message)),
    '红线二：报错点名「只允许显示代号」',
  )
  report(throws(() => assertFieldDisplayable('x037', false)) === null, '红线二：x037 代号放行')
  report(throws(() => assertFieldDisplayable('X_37', false)) === null, '红线二：x_37 / 大写变体放行')
  report(
    throws(() => assertFieldDisplayable('营业收入', true)) === null,
    '红线二：语义已确认字段可用业务名',
  )
  report(isUnknownFieldCode('x021') && !isUnknownFieldCode('营业收入'), '红线二：代号判定正则正确')
  report(NOISE_FLOOR === 0.477, `红线三：噪声地板常量 = 0.477（实际 ${NOISE_FLOOR}）`)

  // ------------------------------------------------------------ 红线三
  report(hasNoiseFloor('real_panel', 0.477), '红线三：真实面板 + 地板 → 合格')
  report(!hasNoiseFloor('real_panel', null), '红线三：真实面板缺地板 → 不合格')
  report(!hasNoiseFloor('real_panel', undefined), '红线三：真实面板 undefined 地板 → 不合格')
  report(hasNoiseFloor('synthetic_graph', null), '红线三：合成图不强制地板（不适用）')

  // ------------------------------------------------ 机制固化：源码层不许绕过
  const chartPanelSrc = read('src/components/ChartPanel.vue')
  report(/DatasetBadge/.test(chartPanelSrc), '机制：ChartPanel 内嵌 DatasetBadge（角标不可省略）')
  report(
    /NoiseFloorNote/.test(chartPanelSrc) && /real_panel/.test(chartPanelSrc),
    '机制：real_panel 自动挂 NoiseFloorNote（不靠调用方记得传）',
  )
  report(
    /dataset:\s*DatasetKind/.test(chartPanelSrc) && !/dataset\?:/.test(chartPanelSrc),
    '机制：ChartPanel 的 dataset 是必填 prop（缺角标编译不过）',
  )
  const badgeSrc = read('src/components/DatasetBadge.vue')
  report(!/close|dismiss/i.test(badgeSrc.replace(/\/\*[\s\S]*?\*\//g, '')), '机制：角标没有关闭状态')

  // ------------------------------------------------ 组件渲染：角标基调
  const badge = async (dataset, nSamples) =>
    renderToString(createSSRApp({ render: () => h(DatasetBadge, { dataset, nSamples }) }))

  const realHtml = await badge('real_panel', 1017)
  report(/data-tone="neutral"/.test(realHtml), '角标：real_panel 走中性基调')
  report(realHtml.includes('真实信贷面板') && realHtml.includes('n=1,017'), '角标：中文来源 + 样本量同时可见')

  const synHtml = await badge('synthetic_graph')
  report(/data-tone="warning"/.test(synHtml), '角标：synthetic_graph 走警示基调（不得与实证同观感）')
  report(synHtml.includes('合成图数据'), '角标：合成来源文案明确')

  const mixHtml = await badge('mixed')
  report(/data-tone="warning"/.test(mixHtml) && mixHtml.includes('混合'), '角标：mixed 同样警示')

  const noteHtml = await renderToString(
    createSSRApp({ render: () => h(NoiseFloorNote, { floor: 0.477, reference: 0.59 }) }),
  )
  report(noteHtml.includes('0.477'), '地板注记：印出 0.477 具体数值')
  report(noteHtml.includes('0.59') && noteHtml.includes('0.113'), '地板注记：给出「高出地板多少」的差值')
  report(/低于此值即与随机无异/.test(noteHtml), '地板注记：说明低于地板的含义')
} finally {
  await vite.close()
}

console.log(failed === 0 ? '\n红线冒烟测试全部通过' : `\n存在 ${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
