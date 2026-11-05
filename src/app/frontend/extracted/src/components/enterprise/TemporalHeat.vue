<script setup lang="ts">
/**
 * P7 区块六 · 时序贡献热力图（FE-16 / A14 的时间维）
 *
 * Top-4 特征 × 观测月，每格 = 该特征总贡献 × 当月分摊比例。
 *
 * 这一块必须守住一件事：**格值之和 = 该特征总贡献**。
 * 破了这条，热力图会在「月度」这一维上讲一个和瀑布图相反的故事（比如瀑布说 +0.27，
 * 热力图上四个月的格子加起来只有 0.18），而读者通常只看热力图不看等式。
 * 所以分摊比例之和 = 1 由 `heatCells()` 断言，不满足直接抛错、不画图。
 *
 * 还有一条现实约束要写在图上：真实面板企业级中位观测只有约 2 个月，
 * 8 个月的观测窗是**占位**，不是真的随访了 8 个月。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { DatasetKind } from '@/types'
import type { LocalExplainEntry } from '@/types/contracts'
import { heatCells } from '@/utils/enterprise'
import { CHART_HEIGHT } from '@/charts/sizes'

const props = defineProps<{
  entry: LocalExplainEntry
  dataset: DatasetKind
  nSamples?: number
  /** 真实面板的企业级中位观测月数（用于提示「8 个月是占位」） */
  medianMonths?: number
}>()

const built = computed(() => {
  try {
    return { cells: heatCells(props.entry.series), error: null as string | null }
  } catch (e) {
    return { cells: [], error: e instanceof Error ? e.message : String(e) }
  }
})

const months = computed(() => {
  const m = Math.max(...props.entry.series.map((s) => s.by_month.length), 0)
  return Array.from({ length: m }, (_, i) => `M${i + 1}`)
})

/**
 * 着色方式：**行内相对强度**，不是全图绝对值。
 *
 * 原因很实在：Top-4 特征的总贡献从 0.11 掉到 0.017（差 6 倍），若按全图绝对值上色，
 * 后三行会被压成一片几乎一样的浅色 —— 图看着干净，但「哪个月最热」这个唯一的问题答不出来。
 * 所以颜色按「该月 / 该行最大值」归一，**数字仍标绝对贡献**（格子里的小数就是真值），
 * 图例也写明「行内相对强度」。这样形状负责定位、数字负责量级，两者不打架。
 */
const option = computed(() => {
  const cells = built.value.cells
  const series = props.entry.series
  const rel = cells.map((c) => {
    const rowMax = Math.max(...series[c.y].by_month.map((sh) => Math.abs(series[c.y].total * sh)), 1e-9)
    return [c.x, c.y, Number((c.value / rowMax).toFixed(4))]
  })
  return {
    animation: false,
    grid: { left: 74, right: 24, top: 12, bottom: 52 },
    tooltip: {
      formatter: (p: { data: [number, number, number] }) => {
        const [x, y] = p.data
        const v = cells.find((c) => c.x === x && c.y === y)?.value ?? 0
        const f = series[y]
        return (
          `<b>${f.feature}</b> · ${months.value[x]}<br/>贡献 ${v >= 0 ? '+' : ''}${v.toFixed(4)}` +
          `<br/>该特征总贡献 ${f.total.toFixed(4)} × 当月分摊 ${(f.by_month[x] * 100).toFixed(1)}%`
        )
      },
    },
    xAxis: {
      type: 'category',
      data: months.value,
      splitArea: { show: true },
      axisLabel: { fontSize: 10, color: '#5b6673' },
    },
    yAxis: {
      type: 'category',
      data: series.map((s) => s.feature),
      inverse: true,
      splitArea: { show: true },
      axisLabel: { fontSize: 10, fontFamily: 'monospace', color: '#1f2933' },
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: 4,
      itemWidth: 12,
      itemHeight: 120,
      text: ['该行最强月', '最弱月'],
      textStyle: { fontSize: 9, color: '#5b6673' },
      // 与瀑布图同一套色：推高=红、压低=蓝，0 附近为白
      inRange: { color: ['#2f5d8a', '#e9eef4', '#b3312c'] },
    },
    series: [
      {
        type: 'heatmap',
        data: rel,
        label: {
          show: true,
          fontSize: 9,
          color: '#1f2933',
          // 标签标**绝对贡献**（真值），不标归一化后的颜色值
          formatter: (p: { data: [number, number, number] }) => {
            const v = cells.find((c) => c.x === p.data[0] && c.y === p.data[1])?.value ?? 0
            return v.toFixed(3)
          },
        },
        emphasis: { itemStyle: { borderColor: '#1f2933', borderWidth: 1 } },
      },
    ],
  }
})

const placeholderNote = computed(() => {
  const observed = props.entry.series[0]?.by_month.length ?? 0
  const median = props.medianMonths ?? 2
  if (observed <= median + 2) return null
  return `观测窗 ${observed} 个月高于真实面板的企业级中位（约 ${median} 个月）：这些月份是占位随访窗，用于验证「跨月切片」的交互，不是真实的 8 个月跟踪。`
})
</script>

<template>
  <div class="heat" data-testid="p7-heat-block">
    <ChartPanel
      v-if="!built.error && entry.series.length"
      title="时序贡献热力图（Top 特征 × 观测月）"
      :dataset="dataset"
      :n-samples="nSamples"
      :noise-floor="null"
      :option="option"
      :height="CHART_HEIGHT.bar"
    >
      <p class="heat__note">
        怎么看：<b>同一行各格加起来 = 该特征在瀑布图里的总贡献</b>；颜色按行内相对强度上色
        （每行最红的那个月 = 该特征贡献最集中的月份），格里的数字是绝对贡献，红正蓝负。
      </p>
      <p v-if="placeholderNote" class="heat__warn" data-testid="p7-heat-placeholder">
        {{ placeholderNote }}
      </p>
    </ChartPanel>

    <div v-else-if="built.error" class="heat__broken" role="alert" data-testid="p7-heat-broken">
      <p class="heat__broken-title">热力图未绘制：逐月分摊比例之和不为 1</p>
      <p class="heat__broken-text">{{ built.error }}</p>
    </div>

    <div v-else class="heat__empty" data-testid="p7-heat-empty">
      <p class="heat__empty-title">该企业没有逐月贡献数据</p>
      <p class="heat__empty-text">
        观测月数为 0，或契约未提供 series。真实面板中确有观测极短的企业，此时不做插值、不画空图。
      </p>
    </div>
  </div>
</template>

<style scoped>
.heat__note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.heat__warn {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid var(--risk-watch);
  background: var(--risk-watch-bg);
  font-size: 11px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.heat__broken,
.heat__empty {
  padding: var(--space-5);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.heat__broken {
  border: 1px solid var(--risk-high-risk);
  border-left-width: 3px;
  background: var(--risk-high-risk-bg);
}

.heat__broken-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--risk-high-risk);
}

.heat__broken-text,
.heat__empty-text {
  margin-top: var(--space-2);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.heat__empty-title {
  font-size: 13px;
  font-weight: 600;
}
</style>
