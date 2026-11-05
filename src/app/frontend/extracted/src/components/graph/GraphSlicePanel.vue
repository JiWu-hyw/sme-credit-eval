<script setup lang="ts">
/**
 * E3 季度时间切片（FE-12 区块二）
 *
 * 三块内容：季度滑块 / 播放、每季度增量柱状图、切片结论。
 *
 * 设计上的两个取舍：
 *   1. **滑块是"截至某季度"的累计视图**，不是"当季快照"。真实面板每企业平均 2 个月观测，
 *      做不了长序列，所以我们用「累计切片」表现图是怎么长出来的——这比「当季只有 N 个节点」
 *      更接近企业真实进入图谱的方式（企业一旦出现就不会消失）。
 *   2. **播放默认关**，点了才动。评审在截图/录屏时页面自己乱跳会被当成 bug。
 *
 * 无障碍：滑块用 `<input type="range">`，键盘左右键可用；播放/暂停按钮带 aria-pressed。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { GraphSlices } from '@/types/contracts'
import type { DatasetKind } from '@/types'
import { ENTITY_KIND_LABELS } from '@/types'
import { CHART_HEIGHT } from '@/charts/sizes'
import { fixed, num } from '@/utils/format'
import { assertSingleDataset } from '@/utils/redlines'

const props = defineProps<{
  slices: GraphSlices
  dataset: DatasetKind
  nSamples?: number
  /** 当前季度索引；-1 = 全时段 */
  index: number
}>()

const emit = defineEmits<{
  (e: 'update:index', value: number): void
}>()

const dataset = computed(() => assertSingleDataset([props.dataset]))
const quarters = computed(() => props.slices.quarters)
const list = computed(() => props.slices.slices)

const activeIndex = computed(() => (props.index < 0 ? list.value.length - 1 : props.index))
const current = computed(() => list.value[activeIndex.value])

/* ------------------------------- 播放 ------------------------------- */

const playing = ref(false)
let timer: ReturnType<typeof setInterval> | undefined

function stop() {
  playing.value = false
  if (timer !== undefined) {
    clearInterval(timer)
    timer = undefined
  }
}

function toggle() {
  if (playing.value) {
    stop()
    return
  }
  playing.value = true
  // 从全时段点播放：从头开始，符合「看它怎么长出来」的预期
  if (props.index < 0) emit('update:index', 0)
  timer = setInterval(() => {
    const next = (props.index < 0 ? 0 : props.index) + 1
    if (next >= list.value.length - 1) {
      emit('update:index', list.value.length - 1)
      stop()
      return
    }
    emit('update:index', next)
  }, 900)
}

watch(
  () => props.index,
  () => {
    // 用户手动拖了滑块就停止播放，否则会打架
    if (playing.value && props.index < 0) stop()
  },
)

onBeforeUnmount(stop)

/* ------------------------------ 图表 ------------------------------ */

/**
 * 只用一个纵轴（累计节点 / 累计边）。
 *
 * 第一版用了双 y 轴：左轴「新增」、右轴「累计」，两条量级相近的曲线叠在一起，
 * 读者根本分不清哪条读哪个轴——这是典型的双轴误导。现在改成：
 *   - 面积叠层：累计节点 + 累计边（同一单位「条」，可以叠）；
 *   - 柱：当季新增节点（单位相同、口径独立，读左轴也不冲突）；
 *   - 参考线：当前季度。
 * 「新增」与「累计」的关系在 tooltip 里逐季给出具体数，不靠两条线的相对高度去猜。
 */
const option = computed(() => ({
  animation: false,
  grid: { left: 62, right: 28, top: 46, bottom: 42 },
  legend: {
    top: 0,
    left: 'center',
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { fontSize: 11 },
  },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (ps: Array<{ dataIndex: number }>) => {
      const s = list.value[ps[0]?.dataIndex ?? 0]
      if (!s) return ''
      const byKind = Object.entries(s.new_by_kind)
        .filter(([, v]) => v > 0)
        .map(([k, v]) => `${ENTITY_KIND_LABELS[k as keyof typeof ENTITY_KIND_LABELS] ?? k} ${v}`)
        .join(' · ')
      return [
        `<b>${s.quarter}</b>`,
        `当季新增：节点 ${num(s.new_nodes)} · 边 ${num(s.new_edges)}`,
        `截至当季：节点 ${num(s.cum_nodes)} · 边 ${num(s.cum_edges)}`,
        byKind ? `当季构成：${byKind}` : '',
      ].filter(Boolean).join('<br/>')
    },
  },
  xAxis: {
    type: 'category',
    data: quarters.value,
    axisTick: { show: false },
    axisLabel: {
      fontSize: 11,
        formatter: (v: string) => (v === current.value?.quarter && props.index >= 0 ? `{now|${v}}` : v),
      rich: { now: { fontSize: 11, fontWeight: 'bold', color: '#1b1f24' } },
    },
  },
  yAxis: {
    type: 'value',
    name: '条数',
    nameTextStyle: { fontSize: 11, padding: [0, 0, 0, -22] },
    axisLabel: { fontSize: 11 },
    splitLine: { lineStyle: { color: '#e6e9ee' } },
  },
  series: [
    {
      name: '累计边（面积）',
      type: 'line',
      stack: 'cum',
      areaStyle: { color: 'rgba(157,182,207,0.55)' },
      lineStyle: { width: 1, color: '#9db6cf' },
      symbol: 'none',
      data: list.value.map((s) => s.cum_edges),
      z: 1,
    },
    {
      name: '累计节点（面积）',
      type: 'line',
      stack: 'cum',
      areaStyle: { color: 'rgba(47,93,138,0.35)' },
      lineStyle: { width: 1, color: '#2f5d8a' },
      symbol: 'none',
      data: list.value.map((s) => s.cum_nodes),
      z: 2,
    },
    {
      name: '当季新增节点（柱）',
      type: 'bar',
      data: list.value.map((s) => s.new_nodes),
      itemStyle: { color: '#c08a1e', borderRadius: [2, 2, 0, 0] },
      barMaxWidth: 22,
      z: 3,
      // 参考线挂在柱系列上：位置就是「当前季度」，用户在滑块上动哪一格，线就跟着走
      markLine: props.index >= 0 && current.value
        ? {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#b3312c', type: 'dashed', width: 1 },
            label: { formatter: current.value.quarter, fontSize: 10, color: '#b3312c', position: 'insideEndTop' },
            data: [{ xAxis: current.value.quarter }],
          }
        : undefined,
    },
  ],
}))

const summary = computed(() => props.slices.summary)

/**
 * 当季新增构成：把 new_by_kind 拆成条。
 * 右侧面板原来只有滑块+一句话，下方一大片留白；补上这个以后，"这一季新进来的都是什么"
 * 不用去 tooltip 里翻——而它恰好是判断「图谱是在补专利还是在补企业」的关键。
 */
const kindRows = computed(() => {
  const by = current.value?.new_by_kind ?? {}
  const total = Object.values(by).reduce((s, v) => s + v, 0) || 1
  return (Object.keys(by) as Array<keyof typeof ENTITY_KIND_LABELS>)
    .map((k) => ({
      kind: String(k),
      label: ENTITY_KIND_LABELS[k] ?? String(k),
      value: by[k as string] ?? 0,
      share: (by[k as string] ?? 0) / total,
    }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
})
</script>

<template>
  <div class="slice">
    <ChartPanel
      title="季度切片：图是怎么长出来的"
      :dataset="dataset"
      :n-samples="nSamples"
      :option="option"
      :height="CHART_HEIGHT.bar"
    />

    <div class="slice__ctrl card">
      <div class="slice__row">
        <button
          type="button"
          class="slice__play"
          :aria-pressed="playing"
          data-testid="slice-play"
          @click="toggle"
        >
          {{ playing ? '⏸ 暂停' : '▶ 播放' }}
        </button>

        <label class="slice__slider">
          <span class="slice__label">时间窗口</span>
          <input
            type="range"
            min="0"
            :max="list.length - 1"
            step="1"
            :value="activeIndex"
            data-testid="slice-slider"
            :aria-valuetext="`截至 ${current?.quarter ?? ''}`"
            @input="emit('update:index', Number(($event.target as HTMLInputElement).value))"
          />
        </label>

        <button
          type="button"
          class="slice__all"
          :aria-pressed="index < 0"
          data-testid="slice-all"
          @click="emit('update:index', -1)"
        >
          全时段
        </button>

        <span class="slice__now" data-testid="slice-quarter">
          {{ index < 0 ? '全时段' : `截至 ${current?.quarter}` }}
        </span>
      </div>

      <ol class="slice__ticks" aria-label="季度刻度">
        <li
          v-for="(q, i) in quarters"
          :key="q"
          :class="{ 'is-on': i === activeIndex && index >= 0 }"
        >
          <button type="button" class="slice__tick" @click="emit('update:index', i)">{{ q }}</button>
        </li>
      </ol>

      <div v-if="kindRows.length" class="slice__kinds" data-testid="slice-kinds">
        <span class="slice__kinds-title">{{ index < 0 ? '全季合计' : `${current?.quarter} 新增构成` }}</span>
        <ul>
          <li v-for="r in kindRows" :key="r.kind">
            <span class="slice__kind-name">{{ r.label }}</span>
            <span class="slice__kind-bar"><i :style="{ width: `${Math.round(r.share * 100)}%` }" /></span>
            <span class="slice__kind-val">{{ r.value }}</span>
          </li>
        </ul>
      </div>

      <p class="slice__note" data-testid="slice-note">
        当前窗口累计 <b>{{ num(current?.cum_nodes ?? 0) }}</b> 节点 /
        <b>{{ num(current?.cum_edges ?? 0) }}</b> 条边；
        全时段共 {{ num(summary.n_nodes_total) }} 节点 / {{ num(summary.n_edges_total) }} 条边，
        {{ summary.n_slices }} 个季度切片，新增边峰值在 <b>{{ summary.peak_new_edges_quarter }}</b>，
        末季累计节点是首季的 <b>{{ fixed(summary.growth, 1) }} 倍</b>。
      </p>
    </div>
  </div>
</template>

<style scoped>
.slice {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 1fr);
  gap: var(--space-4);
  align-items: stretch;
}

@media (max-width: 1080px) {
  .slice {
    grid-template-columns: 1fr;
  }
}

.card {
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.slice__ctrl {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  /* 与左侧图表等高对齐（图表 320 + 内边距），避免右栏像一张"没做完的卡" */
  min-height: 360px;
}

.slice__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.slice__play,
.slice__all {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: 14px;
  background: var(--color-surface);
  font-size: 12px;
  cursor: pointer;
}

.slice__play[aria-pressed='true'],
.slice__all[aria-pressed='true'] {
  background: var(--color-accent-soft, #e8eef6);
  border-color: var(--color-accent, #2f5d8a);
}

.slice__slider {
  display: flex;
  flex: 1 1 160px;
  align-items: center;
  gap: var(--space-2);
}

.slice__slider input {
  flex: 1;
  accent-color: #2f5d8a;
}

.slice__label {
  font-size: 12px;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.slice__now {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
}

.slice__ticks {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.slice__tick {
  padding: 2px 7px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: transparent;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
  cursor: pointer;
}

.slice__ticks .is-on .slice__tick {
  border-color: #1b1f24;
  color: #1b1f24;
  font-weight: 600;
}

.slice__kinds {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.slice__kinds-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-muted);
}

.slice__kinds ul {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.slice__kinds li {
  display: grid;
  grid-template-columns: 72px 1fr 40px;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.slice__kind-bar {
  height: 7px;
  border-radius: 4px;
  background: var(--color-surface-muted);
  overflow: hidden;
}

.slice__kind-bar i {
  display: block;
  height: 100%;
  background: #2f5d8a;
}

.slice__kind-val {
  text-align: right;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.slice__note {
  margin: 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}
</style>
