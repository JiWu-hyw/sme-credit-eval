<script setup lang="ts">
/**
 * A03 面板观测纵深（FE-11 区块三）
 *
 * 一个直方图 + 三句结论。结论不是装饰：纵深浅是「为什么不做长序列建模」的唯一证据。
 *
 * 为什么不直接上图了事：
 *   直方图只能说明「大量企业只有 1–3 个月」，读者未必能推到「所以 GRU 拿不到序列」。
 *   旁边把中位数、均值、可用序列比例写死，评审一眼能看到「长序列建模不可行」的依据。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { PanelDepth } from '@/types/contracts'
import { CHART_HEIGHT } from '@/charts/sizes'
import { num } from '@/utils/format'
import { assertSingleDataset } from '@/utils/redlines'

const props = defineProps<{
  depth: PanelDepth
  dataset: 'real_panel' | 'synthetic_graph' | 'mixed'
  nSamples?: number
  noiseFloor?: number | null
}>()

const dataset = computed(() => assertSingleDataset([props.dataset]))

const totalEnterprises = computed(() => props.depth.bins.reduce((s, b) => s + b.enterprises, 0))

/** 可做序列建模的比例：≥6 个月才算「能喂 GRU/TCN」 */
const feasibleRatio = computed(() => {
  const ok = props.depth.bins.filter((b) => (b.hi ?? b.lo ?? 0) >= 6).reduce((s, b) => s + b.enterprises, 0)
  return totalEnterprises.value ? ok / totalEnterprises.value : 0
})

const barOption = computed(() => ({
  animation: false,
  grid: { left: 56, right: 24, top: 32, bottom: 44 },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (ps: Array<{ dataIndex: number }>) => {
      const b = props.depth.bins[ps[0]?.dataIndex ?? 0]
      if (!b) return ''
      return `${b.range}<br/>企业 ${num(b.enterprises)} 家 · 观测 ${num(b.observations)} 条`
    },
  },
  xAxis: {
    type: 'category',
    data: props.depth.bins.map((b) => b.range),
    axisLabel: { fontSize: 11 },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    name: '企业数',
    nameTextStyle: { fontSize: 11 },
    axisLabel: { fontSize: 11 },
    splitLine: { lineStyle: { color: '#e6e9ee' } },
  },
  series: [
    {
      type: 'bar',
      name: '企业数',
      data: props.depth.bins.map((b) => b.enterprises),
      barMaxWidth: 56,
      itemStyle: { color: 'var(--color-accent)', borderRadius: [3, 3, 0, 0] },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#b3312c', type: 'dashed' },
        label: { formatter: `中位数 ${props.depth.coverage.median_months} 月`, fontSize: 11, color: '#b3312c' },
        data: [{ xAxis: props.depth.bins.findIndex((b) => (b.lo ?? 0) <= props.depth.coverage.median_months && props.depth.coverage.median_months <= (b.hi ?? 0)) }],
      },
    },
  ],
}))
</script>

<template>
  <div class="depth grid-12">
    <div class="col-8">
      <ChartPanel
        title="每企业观测月数分布（A03）"
        :dataset="dataset"
        :n-samples="nSamples"
        :noise-floor="noiseFloor"
        :option="barOption"
        :height="CHART_HEIGHT.bar"
      >
        <p class="depth__note">
          共 {{ num(totalEnterprises) }} 家企业进入面板，观测总数
          {{ num(depth.coverage.observations_total) }} 条。横轴为单企业可用的连续观测月数。
        </p>
      </ChartPanel>
    </div>

    <div class="col-4 stack">
      <div class="depth__card">
        <span class="depth__card-label">中位数 / 均值</span>
        <strong class="depth__card-value">
          {{ depth.coverage.median_months }} / {{ depth.coverage.mean_months?.toFixed(1) ?? '—' }}
          <small>月</small>
        </strong>
      </div>
      <div class="depth__card">
        <span class="depth__card-label">≥6 个月可做序列的企业</span>
        <strong class="depth__card-value">{{ (feasibleRatio * 100).toFixed(1) }}<small>%</small></strong>
      </div>
      <div class="depth__verdict" :data-ok="depth.coverage.long_sequence_feasible">
        <p class="depth__verdict-title">
          {{ depth.coverage.long_sequence_feasible ? '长序列建模可行' : '长序列建模不可行' }}
        </p>
        <p class="depth__verdict-text">
          时间维以「{{ depth.coverage.temporal_unit ?? '跨申请月图切片' }}」实现：
          把每月观测当作一张静态图切片，用 GRU 跨切片传递隐状态，而不是要求单企业有长连续序列。
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.depth__note {
  margin-top: var(--space-2);
  font-size: 12px;
  color: var(--color-text-muted);
}

.depth__card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.depth__card-label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.depth__card-value {
  font-family: var(--font-mono);
  font-size: 22px;
  color: var(--color-text);
}

.depth__card-value small {
  margin-left: 4px;
  font-size: 12px;
  color: var(--color-text-subtle);
}

.depth__verdict {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border, var(--color-border));
  border-left: 3px solid var(--risk-watch);
  border-radius: var(--radius-md);
  background: var(--risk-watch-bg);
}

.depth__verdict[data-ok='true'] {
  border-left-color: var(--risk-good);
  background: var(--risk-good-bg);
}

.depth__verdict-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.depth__verdict-text {
  margin-top: var(--space-1);
  font-size: 12px;
  line-height: 1.65;
  color: var(--color-text-muted);
}
</style>
