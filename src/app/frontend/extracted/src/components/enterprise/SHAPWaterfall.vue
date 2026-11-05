<script setup lang="ts">
/**
 * P7 区块四 · SHAP 瀑布图（FE-16 / A20）
 *
 * 从 E[f(x)]（队列平均企业的 log-odds）出发，逐段加上各特征贡献 φ，
 * 最后一段必须精确落在 f(x)。这张图的说服力全在「一段一段加过去正好到总分」——
 * 任何一处配平不上，读者核对一次就再也不会信它。所以：
 *   · 分段由 `waterfallSegments()` 算出，**不在这里二次加工**；
 *     配平失败时该函数直接抛错，冒烟脚本据此断言（见 scripts/p7-smoke.mjs）；
 *   · 图上方常驻一遍「Σφ + E[f(x)] = f(x)」的等式，把可加性摆在明面上；
 *   · 两种排序：按 |φ|（默认，看谁影响最大）/ 按贡献方向（SHAP 论文常规，看总体被推还是被拉）。
 *
 * 量纲提醒：横轴是 **log-odds**，不是分数、不是概率。写成分数会让读者以为「14.2 分」。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import type { DatasetKind } from '@/types'
import type { LocalExplainEntry } from '@/types/contracts'
import { gradientSort, magnitudeSort, waterfallSegments, type WaterfallSegment } from '@/utils/enterprise'
import { CHART_HEIGHT } from '@/charts/sizes'

const props = defineProps<{
  entry: LocalExplainEntry
  dataset: DatasetKind
  nSamples?: number
}>()

type SortMode = 'magnitude' | 'gradient'
const sortMode = ref<SortMode>('magnitude')

const SORTS: Array<{ id: SortMode; label: string; hint: string }> = [
  { id: 'magnitude', label: '按影响大小', hint: '按 |φ| 降序：谁把分数拉得最多' },
  { id: 'gradient', label: '按贡献方向', hint: '风险因子在前、保护因子在后（SHAP 常规排序）' },
]

const sorted = computed(() =>
  sortMode.value === 'gradient' ? gradientSort(props.entry.features) : magnitudeSort(props.entry.features),
)

/**
 * 分段与绘制。`waterfallSegments` 在配平失败时抛错 —— 这里接住并降级为一条醒目提示，
 * 而不是让整页白屏：评审当场最需要知道的是「这张图不可信」，不是「页面崩了」。
 */
const built = computed(() => {
  try {
    const segs = waterfallSegments(
      props.entry.base_value,
      sorted.value.map((f) => ({ feature: f.feature, phi: f.phi, semantic_known: f.semantic_known })),
      props.entry.score_logit,
    )
    return { segs, error: null as string | null }
  } catch (e) {
    return { segs: [], error: e instanceof Error ? e.message : String(e) }
  }
})

const segments = computed(() => built.value.segs)

const option = computed(() => {
  const segs = segments.value
  const cats = segs.map((s) => (s.kind === 'base' ? 'E[f(x)]' : s.kind === 'total' ? 'f(x)' : s.key))
  const colorOf = (s: (typeof segs)[number]) => {
    if (s.kind === 'base') return '#8a939e'
    if (s.kind === 'total') return '#1f2933'
    return (s.delta ?? 0) > 0 ? '#b3312c' : '#2f5d8a'
  }

  const [yMin, yMax] = axisRange(segs)
  // 参考线数据（E[f(x)] 灰、f(x) 黑）：custom 系列拿不到自定义字段，所以放独立数组由 dataIndex 索引
  const refs = [
    { level: segs[0]?.to ?? 0, color: '#8a939e' },
    { level: segs[segs.length - 1]?.to ?? 0, color: '#1f2933' },
  ]
  return {
    animation: false,
    grid: { left: 78, right: 96, top: 28, bottom: 56 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: Array<{ dataIndex: number }>) => {
        const s = segs[ps[0].dataIndex]
        if (!s) return ''
        if (s.kind === 'base') return `<b>E[f(x)]</b><br/>队列平均企业：${s.to.toFixed(3)}（log-odds）`
        if (s.kind === 'total') return `<b>f(x) 本企业</b><br/>${s.to.toFixed(3)}（log-odds）`
        return `<b>${s.key}</b><br/>Δφ = ${s.delta! >= 0 ? '+' : ''}${s.delta!.toFixed(4)}<br/>区间 ${s.from.toFixed(3)} → ${s.to.toFixed(3)}`
      },
    },
    xAxis: {
      type: 'category',
      // category 轴的 data 必须与每个 custom 数据项的第一维（序号 0..n-1）一一对应，
      // 否则这 22 项找不到自己的列，全部叠到最后一列（第二版就是这样）。
      data: cats.map((_, i) => i),
      axisLabel: {
        formatter: (v: number) => cats[v] ?? '',
        fontSize: 9,
        interval: 0,
        rotate: 46,
        color: '#5b6673',
        fontFamily: 'monospace',
      },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      min: yMin,
      max: yMax,
      // 刻度标签保留一位小数，避免出现 -2.3000000000000003 这种浮点尾巴。
      axisLabel: {
        fontSize: 10,
        color: '#5b6673',
        formatter: (v: number) => v.toFixed(1),
      },
      type: 'value',
      name: 'log-odds',
      nameTextStyle: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        /**
         * **画成「浮动柱」而不是「叠层柱」**。
         *
         * 第一版用透明底柱 + 叠层反色，结果 `E[f(x)]`（−2.12）与 `f(x)`（−1.64）两根基准柱
         * 根本画不出来 —— ECharts 对负值的堆叠按「负向累积」处理，透明底柱一旦负数就整条消失，
         * 图上只剩下一排小方块。校验字段（`__wf`）是对的，图是错的，这种错肉眼一看就像「数据太少」。
         *
         * 现在每个柱子直接给 `[low, high]` 区间：正贡献从 from 长到 to，负贡献反过来，
         * 基准柱从 0 长到基线值。连线仍用 custom 画「上一段终点 → 下一段起点」。
         */
        type: 'custom',
        // 单项值的坐标轴没有 category 维度，`params.dataIndex` 恒为 0 —— 必须显式给 `value: [序号, 数值]`，
        // ECharts 才会为每项单独调一次 renderItem（第一版给单值，结果 22 根柱子叠在同一个 x 上）。
        data: segs.map((sg, i) => ({ value: [i, sg.high] })),
        renderItem: (
          params: { dataIndex: number; dataIndexInside?: number },
          api: {
            coord: (p: [number, number]) => [number, number]
            size: (p: [number, number]) => [number, number]
          },
        ) => {
          /**
           * ⚠️ renderItem 里**不要再做任何业务计算**（不再读闭包里的 segs、不再算 delta 文案）。
           *
           * 踩过的坑：一开始在 renderItem 内部读闭包变量 `segs[idx]` 并拼字符串，
           * 开发态热更新后整张图**图元全消失、只剩坐标轴**，且不报任何错。
           * Vite HMR 会让闭包指向一份「新模块里的旧 segs」，取到 undefined 又不抛异常。
           * 现在把颜色和文案都放进 `data`（ECharts 自己的数据层），renderItem 只做坐标换算，
           * 一份数据一份图，HMR 也好、切企业也好都不会拿到半截数据。
           */
          // ⚠️ custom 系列**不会**把 data 上的自定义字段（itemStyle / label / seg）回传给 renderItem，
          // 第一个参数只有 `dataIndex` / `dataIndexInside`。硬读 `params.data.seg` 会抛
          // 「Cannot read properties of undefined」，然后整块被外壳的 onErrorCaptured 接走、整页变错误态
          // （这一步踩了两次：第一次以为是 HMR，第二次才把堆栈打出来看）。
          // 正确做法就是用 dataIndex 回闭包取原分段 —— 闭包里就是这次 option 的那份 segs。
          const i = params.dataIndex
          const seg = segs[i]
          if (!seg) return { type: 'group', children: [] }
          const band = api.size([1, 0])[0]
          const w = Math.max(7, band * 0.72)
          // 基准柱（E[f(x)] / f(x)）从画布底部整柱长出，特征柱只画自己的那一小段 ——
          // 这是 SHAP 瀑布图的常规画法：两根整柱是标尺，中间的台阶才是信息。
          const isRef = seg.kind !== 'feature'
          const [x0] = api.coord([i, 0])
          const [, yAnchor] = api.coord([i, isRef ? yMin : seg.low])
          const [, yTo] = api.coord([i, seg.high])
          const top = Math.min(yAnchor, yTo)
          const h = Math.max(1.5, Math.abs(yTo - yAnchor))
          const text =
            seg.kind === 'feature'
              ? // 微不足道的项不标数字：20 段台阶里末尾几项只有 1e-3 量级，
                // 标出来就是一排互相压字的小数（第一版截图里那串「-0.000」）。
                Math.abs(seg.delta!) < 0.004
                ? ''
                : `${seg.delta! >= 0 ? '+' : ''}${seg.delta!.toFixed(3)}`
              : seg.to.toFixed(2)
          return {
            type: 'group',
            children: [
              {
                type: 'rect',
                shape: { x: x0 - w / 2, y: top, width: w, height: h, r: 1 },
                style: { fill: colorOf(seg), opacity: 0.95 },
              },
              {
                type: 'text',
                style: {
                  x: x0,
                  y: top - 4,
                  text,
                  textAlign: 'center',
                  textVerticalAlign: 'bottom',
                  fill: '#5b6673',
                  font: '9px sans-serif',
                },
              },
            ],
          }
        },
        // 参考线（f(x) 与 E[f(x)]）不在 custom 系列上挂 markLine —— custom 系列不支持 markLine，
        // 挂了会让整个 series 判为非法、图元静默不画。改成第三条 custom 系列画两条水平虚线。

      },
      {
        /**
         * 分段之间的「台阶」连线。ECharts 的柱图不会自己连，用 custom 逐段画一条水平短虚线，
         * 从上一段末端画到下一段起点。没有这条线，读者会以为每一段都从 0 开始长起来。
         */
        type: 'custom',
        silent: true,
        z: 3,
        renderItem: (
          params: { dataIndex: number; coordSys: { x: number; width: number } },
          api: { coord: (p: [number, number]) => [number, number] },
        ) => {
          const idx = params.dataIndex
          const a = segs[idx]
          const b = segs[idx + 1]
          if (!b) return { type: 'group', children: [] }
          const [x0] = api.coord([idx, a.to])
          const [x1] = api.coord([idx + 1, a.to])
          const [, y] = api.coord([idx, a.to])
          const half = Math.max(2, (x1 - x0) * 0.28)
          return {
            type: 'line',
            shape: { x1: x0 + half, y1: y, x2: x1 - half, y2: y },
            style: { stroke: '#b9c0c9', lineWidth: 1, lineDash: [3, 3] },
          }
        },
        // ⚠️ custom 系列的 value **不参与** y 轴极值计算（ECharts 的既定行为）。
        // 于是必须自己把范围告诉坐标轴：下面 `yAxis.min/max` 用 axisRange 显式给死。
        // 反过来，如果这里写成 `[i, y]` 想「顺便把轴撑开」，两个 custom 系列会一起把轴拉到 0…25
        // （第三、四版都翻在这上面：图不报错，就是一片空白，截图看不出是 bug 还是没数据）。
        data: segs.map((_, i) => ({ value: [i, segs[i]?.to ?? 0] })),
      },
      {
        /** 两条水平参考虚线：f(x)（本企业）与 E[f(x)]（队列均值），用 custom 画，不挂 markLine */
        type: 'custom',
        silent: true,
        z: 1,
        renderItem: (
          params: { dataIndex: number },
          api: { coord: (p: [number, number]) => [number, number] },
        ) => {
          const i = params.dataIndex
          const ref = refs[i]
          if (!ref) return { type: 'group', children: [] }
          const [, y] = api.coord([0, ref.level])
          const left = api.coord([-0.4, ref.level])[0]
          const right = api.coord([segs.length - 0.6, ref.level])[0]
          return {
            type: 'line',
            shape: { x1: left, y1: y, x2: right, y2: y },
            style: { stroke: ref.color, lineWidth: 1, lineDash: [4, 4] },
          }
        },
        // 同样要给 `[序号, 数值]`：单值项在 category 轴上拿不到独立列，两条线会挤在同一处
        data: refs.map((r, i) => ({ value: [i * (segs.length - 1), r.level] })),
      },
    ],
  }
})

/**
 * ⚠️ 柱型不是 'bar'，是 **'custom'**（自己画矩形）。
 *
 * 第二轮用 `bar + value:[low,high]` 画浮动柱，图**整块空白**：这套 option 里没有 `dataset`，
 * 浮点柱要配 `encode` / 正确的数据结构才认，`value:[a,b]` 在 category 轴上会被当成两列。
 * 与其跟柱图的取值规则较劲，不如直接按像素坐标画矩形 —— 瀑布图本来就是「一堆矩形 + 一堆连线」，
 * 自己画反而更可控：正负色、基准柱、零线全部一次到位，也不受 stacking 影响。
 */
/**
 * 纵轴范围：包住所有分段 + 0（0 这条线必须留出来，否则读者看不到「相对 0 的偏离有多远」），
 * 再向上留出 0.3 的标签余量。不搞「缩短刻度」那套视觉放大 —— 那是把微小差异画成大差异。
 */
function axisRange(segs: WaterfallSegment[]): [number, number] {
  // 空数组必须先兜住：`Math.min(...[])` 返回 Infinity，ECharts 收到 Infinity 会抛
  // 「Invalid data」并让整个区块被 onErrorCaptured 接走（表现为整页变错误态）。
  if (!segs.length) return [-3, 1]
  /**
   * 纵轴只框住**台阶轨迹**，不把 0 拉进来。
   *
   * 早先版本把 0 也算进范围，于是轴域是 −2.2…0，而 20 段台阶全挤在 −2.12…−1.64 这条带上：
   * 图上只剩两根顶天立地的基准柱，中间是 20 条看不清的细线，读者第一反应是「图挂了」。
   * 现在轴域跟着轨迹走（上下各留 12% 余量），基准柱改成从画布底部整柱长出，
   * 台阶之间的量级差才看得见。0 这条线不再画出 —— 量纲是 log-odds，
   * 读者要看的是「相对队列均值偏了多少」，不是「离 0 有多远」。
   */
  const pts: number[] = []
  for (const s of segs) {
    pts.push(s.to)
    if (s.kind === 'feature') pts.push(s.from)
  }
  const lo = Math.min(...pts)
  const hi = Math.max(...pts)
  const pad = Math.max((hi - lo) * 0.12, 0.05)
  return [Number((lo - pad).toFixed(3)), Number((hi + pad).toFixed(3))]
}

const eq = computed(() => {
  const sumPhi = props.entry.features.reduce((a, b) => a + b.phi, 0) + props.entry.rest.phi
  return {
    base: props.entry.base_value,
    sumPhi: Number(sumPhi.toFixed(4)),
    total: props.entry.score_logit,
  }
})

const maxAbs = computed(() => Math.max(...props.entry.features.map((f) => Math.abs(f.phi)), 1e-6))
</script>

<template>
  <div class="wf" data-testid="p7-waterfall-block">
    <ChartPanel
      v-if="!built.error"
      title="局部归因瀑布（从队列均值到本企业，单位 log-odds）"
      :dataset="dataset"
      :n-samples="nSamples"
      :noise-floor="null"
      :option="option"
      :height="CHART_HEIGHT.bar + 120"
    >
      <div class="wf__toolbar">
        <div class="wf__sort" role="group" aria-label="瀑布图排序">
          <button
            v-for="s in SORTS"
            :key="s.id"
            type="button"
            class="wf__sort-btn"
            :class="{ 'is-active': sortMode === s.id }"
            :aria-pressed="sortMode === s.id"
            :title="s.hint"
            :data-testid="`p7-sort-${s.id}`"
            @click="sortMode = s.id"
          >
            {{ s.label }}
          </button>
        </div>
        <span class="wf__eq" data-testid="p7-additivity">
          Σφ（{{ eq.sumPhi }}）+ E[f(x)]（{{ eq.base }}）= f(x)（{{ eq.total }}）
        </span>
      </div>
      <ul class="wf__legend">
        <li><span class="wf__dot wf__dot--up" />推高违约概率（φ &gt; 0）</li>
        <li><span class="wf__dot wf__dot--down" />压低违约概率（φ &lt; 0）</li>
        <li><span class="wf__dot wf__dot--base" />E[f(x)] 队列均值 / f(x) 本企业</li>
      </ul>
    </ChartPanel>

    <div v-else class="wf__broken" role="alert" data-testid="p7-waterfall-broken">
      <p class="wf__broken-title">瀑布图未绘制：分段累加与总分对不上</p>
      <p class="wf__broken-text">{{ built.error }}</p>
      <p class="wf__broken-text">
        这时正确的做法是承认这份局部归因不可用（而不是把图硬画出来）。请算法侧核对
        <code>shap_local.json</code> 的 φ 是否满足 Σφ + base = score_logit。
      </p>
    </div>

    <div class="wf__table-wrap">
      <table class="tbl" data-testid="p7-waterfall-table">
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">字段</th>
            <th scope="col">φ（log-odds）</th>
            <th scope="col">相对幅度</th>
            <th scope="col">本企业方向</th>
            <th scope="col">全局方向</th>
            <th scope="col">本地 vs 全局</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in sorted" :key="r.feature" :data-testid="`p7-wf-row-${r.feature}`">
            <td class="mono">{{ r.rank }}</td>
            <th scope="row"><UnknownFieldTag :field="r.feature" :semantic-known="r.semantic_known" /></th>
            <td class="mono" :data-dir="r.phi > 0 ? 'up' : 'down'">
              {{ r.phi > 0 ? '+' : '' }}{{ r.phi.toFixed(4) }}
            </td>
            <td>
              <span class="bar">
                <span
                  class="bar__fill"
                  :class="r.phi > 0 ? 'bar__fill--up' : 'bar__fill--down'"
                  :style="{ width: `${(Math.abs(r.phi) / maxAbs) * 100}%` }"
                />
              </span>
            </td>
            <td class="mono">{{ r.direction > 0 ? '风险 ↑' : '保护 ↓' }}</td>
            <td class="mono">{{ r.global_direction > 0 ? '风险 +' : '保护 −' }}</td>
            <td>
              <span class="flag" :data-ok="r.consistent_with_global ? 'yes' : 'no'">
                {{ r.consistent_with_global ? '同向' : '反向' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="wf__rest">
        {{ entry.rest.note }}
      </p>
      <p class="wf__rest">
        「本地 vs 全局」列怎么读：全局方向是该字段**本身**的性质（值越大风险越高 = 风险 +），
        本地方向是这家企业**这一项**把分数推向哪边。两者「反向」不代表模型自相矛盾 ——
        恰恰说明这家企业在一个保护性字段上取值偏低（或反之），于是同一根柱子被推向另一边。
        反向项（本图 {{ sorted.filter((r) => !r.consistent_with_global).length }} / {{ sorted.length }}）通常集中在弱因子上；
        若某家企业全部反向，才需要怀疑归因链路。
      </p>
    </div>
  </div>
</template>

<style scoped>
.wf {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.wf__toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-2);
}

.wf__sort {
  display: flex;
  gap: 4px;
}

.wf__sort-btn {
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: 11.5px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.wf__sort-btn.is-active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.wf__eq {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.wf__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
  font-size: 11px;
  color: var(--color-text-muted);
}

.wf__dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 5px;
  border-radius: 2px;
}

.wf__dot--up {
  background: #b3312c;
}

.wf__dot--down {
  background: #2f5d8a;
}

.wf__dot--base {
  background: #8a939e;
}

.wf__broken {
  padding: var(--space-5);
  border: 1px solid var(--risk-high-risk);
  border-left-width: 3px;
  border-radius: var(--radius-md);
  background: var(--risk-high-risk-bg);
}

.wf__broken-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--risk-high-risk);
}

.wf__broken-text {
  margin-top: var(--space-2);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text);
}

.wf__table-wrap {
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.tbl th,
.tbl td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border);
  text-align: right;
  white-space: nowrap;
}

.tbl th[scope='col']:nth-child(2),
.tbl th[scope='row'] {
  text-align: left;
}

.tbl thead th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.mono {
  font-family: var(--font-mono);
}

[data-dir='up'] {
  color: #b3312c;
}

[data-dir='down'] {
  color: #2f5d8a;
}

.bar {
  display: inline-block;
  width: 90px;
  height: 8px;
  border-radius: 4px;
  background: var(--color-surface-muted);
  vertical-align: middle;
  overflow: hidden;
}

.bar__fill {
  display: block;
  height: 100%;
  border-radius: 4px;
}

.bar__fill--up {
  background: #b3312c;
}

.bar__fill--down {
  background: #2f5d8a;
}

.flag {
  padding: 0 5px;
  border: 1px solid currentColor;
  border-radius: 8px;
  font-size: 10.5px;
}

.flag[data-ok='yes'] {
  color: #1f7a4d;
}

.flag[data-ok='no'] {
  color: #c98a12;
}

.wf__rest {
  margin-top: var(--space-2);
  font-size: 11px;
  color: var(--color-text-subtle);
}
</style>
