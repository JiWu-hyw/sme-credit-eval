<script setup lang="ts">
import { computed } from 'vue'

import DatasetBadge from './DatasetBadge.vue'
import EChartBase from '../charts/EChartBase.vue'
import NoiseFloorNote from './NoiseFloorNote.vue'

import { CHART_HEIGHT } from '../charts/sizes'
import type { DatasetKind } from '../types'
import type { EChartsOption } from '../charts/echarts'

/**
 * 图表容器（红线一 / 红线三的强制落点，FE-03）。
 *
 * 为什么要有它：红线要求「任一图表容器缺角标即不合格」。把 EChartBase 包在
 * ChartPanel 里并让 `dataset` 成为**必填 prop**，业务代码想要画图就必须提供
 * 数据来源——TypeScript 层直接把「缺角标」变成编译错误，比约定和 review 可靠。
 *
 * - 角标内嵌渲染、不可关闭（DatasetBadge 本身无关闭状态）；
 * - `dataset === 'real_panel'` 且传入 `noiseFloor` 时自动同屏渲染噪声地板注记
 *   （红线三：真实面板性能图必须带地板，当前 0.477）；
 * - 同图混排的运行期守卫见 `utils/redlines.ts › assertSingleDataset`，
 *   由各页面在组装 series 前调用。
 */
const props = withDefaults(
  defineProps<{
    /** 图表标题（可追溯要求：每图有出处） */
    title: string
    /** 数据来源（meta.dataset），必填 → 缺角标即编译不过 */
    dataset: DatasetKind
    /** meta.n_samples，随角标展示 */
    nSamples?: number
    /** meta.noise_floor；real_panel 性能图必须传（红线三） */
    noiseFloor?: number | null
    option: EChartsOption
    /**
     * 高度：数字按 px。默认取 `charts/sizes.ts › CHART_HEIGHT.line`（320）。
     * 力导向图请显式传 `CHART_HEIGHT.force`（640）；热力图传 `'auto'` 由容器决定。
     */
    height?: number | string
    loading?: boolean
    /**
     * 坐标系组件（FE-12 新增）：radar / polar / geo 这类坐标系不是 series 的子属性，
     * EChartBase 用 replaceMerge:['series'] 合并时**不会**重建它们 —— 表现为雷达图画不出来
     * （series 有了，radar 坐标系没注册，整块空白，控制台还不报错）。
     * 需要坐标系的图在这里声明，EChartBase 会把它一并 replaceMerge。
     */
    mergedComponents?: string[]
  }>(),
  { height: CHART_HEIGHT.line, loading: false, nSamples: undefined, noiseFloor: undefined },
)

/**
 * 红线三只在「有明确数值地板」时成立。
 *
 * FE-13 修：原来判断写成 `noiseFloor !== undefined`，于是显式传 `null`（不适用，
 * 例如 A3 的 PR / KS 图 —— 它们的指标不是 AUC，拿 0.477 去比毫无意义）也会渲染注记，
 * 结果图上出现「噪声地板 AUC = —，低于此值即与随机无异」这种半句话。
 * 现在改成「必须是有限数字」才算需要注记；`null` / `undefined` 一律不显示。
 */
const showNoiseFloor = computed(
  () => props.dataset === 'real_panel' && typeof props.noiseFloor === 'number' && Number.isFinite(props.noiseFloor),
)

/**
 * 高度归一：`'auto'` 交给父容器（热力图自适应），其余原样下发。
 * 不在这里做 `number → 'xxxpx'` 的转换——EChartBase 可能依赖数字做尺寸计算，
 * 少一次类型漂移就少一个「图表高度突然变成 0」的排查。
 */
const chartHeight = computed(() => (props.height === 'auto' ? '100%' : props.height))

/**
 * 图表内点击透传（FE-12 新增，P3 力导向图用来选中节点）。
 *
 * 为什么不让业务侧直接 import EChartBase：红线一要求所有图表都走 ChartPanel 的角标，
 * 绕过去就等于给自己留后门。这里只做一层转发——ECharts 的 `click` 事件带 series 数据，
 * 由 EChartBase 绑定后原样抛上来，ChartPanel 不认识业务、不做解析。
 */
const emit = defineEmits<{
  (e: 'chart-click', payload: { dataType?: string; data?: Record<string, unknown> }): void
}>()
</script>

<template>
  <figure class="chart-panel" data-testid="chart-panel">
    <figcaption class="chart-panel__head">
      <span class="chart-panel__title">{{ title }}</span>
      <!-- 角标：不可关闭、不可省略（红线一） -->
      <DatasetBadge :dataset="dataset" :n-samples="nSamples" />
    </figcaption>
    <EChartBase
      :option="option"
      :height="chartHeight"
      :loading="loading"
      :merged-components="mergedComponents"
      @chart-click="emit('chart-click', $event)"
    />
    <!-- 噪声地板：真实面板性能图同屏（红线三） -->
    <NoiseFloorNote v-if="showNoiseFloor" :floor="noiseFloor" />
    <slot />
  </figure>
</template>

<style scoped>
.chart-panel {
  margin: 0;
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.chart-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.chart-panel__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
</style>
