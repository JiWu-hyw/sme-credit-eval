<script setup lang="ts">
/**
 * A10 基线对比：分组柱 + 雷达 + 消融（FE-13）
 *
 * 三张图回答三个不同的问题，不能合并：
 *   ① 分组柱「谁比谁高」——指标之间量纲不同，必须分指标组；
 *   ② 雷达「谁偏科」——六轴量程各不相同，只读形状不读面积（图上已注明）；
 *   ③ 消融「每个组件值多少」——去一个组件掉多少 AUC，看的是 Δ 而不是绝对值。
 *
 * 交互：模型可勾选（至少保留 1 个，否则图是空的；全不选时自动回退全选并给提示，
 * 和 P3 图例筛选同一套交互约定）。
 */
import { computed, ref, watch } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import { CHART_HEIGHT } from '@/charts/sizes'
import type { DatasetKind } from '@/types'
import type { Benchmark } from '@/types/contracts'

const props = defineProps<{
  benchmark: Benchmark
  dataset: DatasetKind
  noiseFloor: number | null
}>()

const COLOR: Record<string, string> = {
  lr: '#b9c0c9',
  xgb: '#8a939e',
  gat: '#2f5d8a',
  mlp_nograph: '#c98a12',
}

const grouped = computed(() => props.benchmark.grouped)
const radar = computed(() => props.benchmark.radar)
const ablation = computed(() => props.benchmark.ablation)

/* ------------------------------ 勾选模型 ------------------------------ */
const allIds = computed(() => grouped.value?.models.map((m) => m.model_id) ?? [])
const selected = ref<string[]>([...allIds.value])
// 契约刷新（例如 dataset 切换后接口换了数据）时，勾选重置为"全选"，避免残留旧选择
watch(allIds, (ids) => {
  selected.value = [...ids]
})
const fallbackNotice = ref(false)
function toggle(id: string) {
  const next = selected.value.includes(id)
    ? selected.value.filter((x) => x !== id)
    : [...selected.value, id]
  if (next.length === 0) {
    // 全关 → 回退全选（否则用户看到空坐标轴且无从恢复）
    selected.value = [...allIds.value]
    fallbackNotice.value = true
    return
  }
  fallbackNotice.value = false
  selected.value = allIds.value.filter((x) => next.includes(x))
}
const picked = computed(() => (grouped.value?.models ?? []).filter((m) => selected.value.includes(m.model_id)))

/* ------------------------------ 分组柱 ------------------------------ */
const barOption = computed(() => {
  const g = grouped.value
  if (!g) return {}
  return {
    animation: false,
    grid: { left: 52, right: 16, top: 40, bottom: 44 },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 12,
      itemHeight: 8,
      textStyle: { fontSize: 11, color: '#5b6673' },
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category',
      data: g.metrics.map((k) => g.metric_labels[k] ?? k),
      axisLabel: { fontSize: 11, color: '#5b6673' },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dde1e7' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 0.8,
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: picked.value.map((m) => ({
      name: m.model_label,
      type: 'bar',
      barMaxWidth: 18,
      itemStyle: { color: COLOR[m.model_id] ?? '#b9c0c9' },
      data: g.metrics.map((k) => m.values[k]),
      label: { show: true, position: 'top', fontSize: 9, color: '#8a939e', formatter: (p: { value: number }) => Number(p.value).toFixed(2) },
    })),
  }
})

/* ------------------------------ 雷达 ------------------------------ */
const radarOption = computed(() => {
  const r = radar.value
  if (!r) return {}
  const series = (radar.value?.series ?? []).filter((s) => selected.value.includes(s.model_id))
  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      // 轴上是归一化值（0–1），提示框必须换回原始指标值，否则读者会以为 AUC 0.73
      formatter: (p: { name?: string }) => {
        const raw = series.find((s) => s.model_label === p.name)?.values ?? []
        const rows = r.indicators.map((ind, i) => {
          const v = raw[i]
          return `${ind.label}：<b>${typeof v === 'number' ? v.toFixed(4) : '—'}</b>（轴量程 ${ind.min}–${ind.max}）`
        })
        return [`<b>${p.name ?? ''}</b>`, ...rows].join('<br/>')
      },
    },
    legend: { top: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: '#5b6673' } },
    /**
     * 六轴量程各自不同是刻意的（精确率 0.10–0.16 与召回率 0.30–0.75 不可直接比长短）：
     * 若统一量程，精确率会被压成一条贴圆心的直线，看不出差异。
     *
     * ⚠️ ECharts 5 的雷达会**无条件**对每个 indicator 做 alignScaleTicks
     * （node_modules/echarts/lib/coord/radar/Radar.js 里直接调，没有 alignTicks 开关），
     * 一旦 indicator 显式写了 min/max（哪怕写 0–1），刻度凑不齐整齐值就刷
     * "The ticks may be not readable when set min: ..., and alignTicks: true"。
     * 这不是配置错误、也不影响画面，但会让"无 Vue 运行时告警"的验收项红掉。
     *
     * 解法：**数据侧归一化，轴侧不写 min/max**（实测 splitNumber≥5 时零告警），
     * 真实量程写进轴名与提示框：
     *   - 轴名 `AUC\n0.45–0.62`，读者一眼知道这根轴在比什么；
     *   - 提示框换回原始值（toFixed(4)），避免把归一化后的 0.73 误读成 AUC。
     * 收益：零告警 + 量程不再"藏在配置里"，比原来的写法更不容易误读。
     */
    radar: {
      radius: '56%',
      center: ['50%', '54%'],
      shape: 'polygon',
      indicatorGap: 8,
      splitNumber: 5,
      indicator: r.indicators.map((i) => ({ name: `${i.label}\n${i.min}–${i.max}` })),
      axisName: { fontSize: 10, color: '#5b6673', lineHeight: 13 },
      splitLine: { lineStyle: { color: '#eef1f5' } },
      splitArea: { areaStyle: { color: ['#ffffff', '#fafbfc'] } },
      axisLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        type: 'radar',
        symbolSize: 3,
        data: series.map((s) => ({
          name: s.model_label,
          // 归一化到各自量程的 0–1（见上方注释：避开雷达的无条件 alignTicks 告警）
          value: s.values.map((v, i) => {
            const ind = r.indicators[i]
            if (!ind || ind.max === ind.min) return 0
            return Math.min(1, Math.max(0, (v - ind.min) / (ind.max - ind.min)))
          }),
          lineStyle: { width: 1.8, color: COLOR[s.model_id] ?? '#b9c0c9' },
          itemStyle: { color: COLOR[s.model_id] ?? '#b9c0c9' },
          areaStyle: { opacity: 0.05 },
        })),
      },
    ],
  }
})

/* ------------------------------ 消融 ------------------------------ */
const ABL_COLOR: Record<string, string> = {
  full: '#2f5d8a',
  no_graph: '#c98a12',
  no_temporal: '#d0622a',
  no_fusion: '#8a939e',
  no_attention: '#8a939e',
  no_temporal_edge: '#b9c0c9',
}

const ablationOption = computed(() => {
  const a = ablation.value
  if (!a) return {}
  // 按 Δ 从 0 往下排（完整模型在最上），反事实项单独用虚线描边
  const bars = [...a.bars]
  return {
    animation: false,
    grid: { left: 150, right: 72, top: 16, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: Array<{ dataIndex: number; value: number }>) => {
        const b = bars[ps[0].dataIndex]
        return [
          `<b>${b.label}</b>`,
          b.description,
          `AUC ${b.auc.toFixed(4)}（Δ ${b.delta_auc >= 0 ? '+' : ''}${b.delta_auc.toFixed(4)}）`,
          b.counterfactual ? '⚠ 反事实项：不是"去掉组件"，是"额外允许一种信息"' : '',
        ]
          .filter(Boolean)
          .join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      min: Math.min(...bars.map((b) => b.auc)) - 0.01,
      max: a.base_auc + 0.005,
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    yAxis: {
      type: 'category',
      data: bars.map((b) => b.label),
      axisLabel: { fontSize: 11, color: '#1f2933' },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        data: bars.map((b) => ({
          value: b.auc,
          itemStyle: {
            color: ABL_COLOR[b.ablation_id] ?? '#b9c0c9',
            borderType: b.counterfactual ? 'dashed' : 'solid',
            borderColor: b.counterfactual ? '#1f2933' : 'transparent',
            borderWidth: b.counterfactual ? 1 : 0,
          },
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            color: b.delta_auc < -0.01 ? '#b3312c' : '#5b6673',
            formatter: b.ablation_id === 'full' ? '基准' : `${b.delta_auc >= 0 ? '+' : ''}${b.delta_auc.toFixed(4)}`,
          },
        })),
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#b3312c', type: 'dashed', width: 1 },
          data: [{ xAxis: props.noiseFloor ?? 0.477 }],
          label: { formatter: `噪声地板 ${props.noiseFloor ?? 0.477}`, fontSize: 10, color: '#b3312c', position: 'end' },
        },
      },
    ],
  }
})

const derivedCount = computed(() => (ablation.value?.bars ?? []).filter((b) => b.derived).length)
</script>

<template>
  <div class="a10">
    <div class="a10__picker" role="group" aria-label="勾选参与对比的模型">
      <span class="a10__picker-label">参与对比</span>
      <button
        v-for="m in grouped?.models ?? []"
        :key="m.model_id"
        type="button"
        class="a10__chip"
        :aria-pressed="selected.includes(m.model_id)"
        :data-testid="`model-chip-${m.model_id}`"
        :style="{ '--chip': COLOR[m.model_id] ?? '#b9c0c9' }"
        @click="toggle(m.model_id)"
      >
        {{ m.model_label }}
      </button>
      <span v-if="fallbackNotice" class="a10__fallback" data-testid="model-fallback">
        全部取消会看不到对比，已自动恢复全选
      </span>
    </div>

    <div class="grid-12">
      <div class="col-7">
        <ChartPanel
          title="留出集指标分组对比（同一指标下多模型并排）"
          :dataset="dataset"
          :n-samples="benchmark.holdout.n_test"
          :noise-floor="noiseFloor"
          :option="barOption"
          :height="CHART_HEIGHT.bar"
        >
          <p class="a10__note">
            准确率 0.47–0.51 看起来"接近一半"，但正例只占 10.7%——全猜"不会违约"的准确率就有 89%。
            这组柱子里唯一该看的是 AUC 与 KS 两组的相对位置，其余四项是"成对读"的补充。
          </p>
        </ChartPanel>
      </div>
      <div class="col-5">
        <ChartPanel
          title="六项指标雷达（只看是否偏科，不比面积）"
          :dataset="dataset"
          :n-samples="benchmark.holdout.n_test"
          :noise-floor="noiseFloor"
          :option="radarOption"
          :height="CHART_HEIGHT.bar"
          :merged-components="['radar']"
        >
          <p class="a10__note" data-testid="radar-note">
            每根轴的量程标在轴名里（如「AUC 0.45–0.62」），图形已按各自量程归一化到 0–1，
            所以<b>长度只在同一根轴内可比</b>，跨轴、跨模型的面积都没有意义。{{ radar?.note }}
          </p>
        </ChartPanel>
      </div>
    </div>

    <ChartPanel
      title="消融实验：每去掉一个组件，AUC 掉多少"
      :dataset="dataset"
      :n-samples="benchmark.holdout.n_test"
      :noise-floor="noiseFloor"
      :option="ablationOption"
      :height="300"
    >
      <ul class="a10__abl" data-testid="ablation-list">
        <li v-for="b in ablation?.bars ?? []" :key="b.ablation_id" :data-counterfactual="b.counterfactual">
          <span class="a10__abl-label">{{ b.label }}</span>
          <span class="a10__abl-desc">{{ b.description }}</span>
          <span class="a10__abl-delta mono">{{ b.delta_auc >= 0 ? '+' : '' }}{{ b.delta_auc.toFixed(4) }}</span>
          <span v-if="b.counterfactual" class="a10__abl-tag">反事实</span>
        </li>
      </ul>
      <p class="a10__note">
        {{ ablation?.note }}<template v-if="derivedCount > 0">
          本图有 <b>{{ derivedCount }}/{{ ablation?.bars.length }}</b> 条为按 Δ 构造（derived），
          只有"完整模型"是实测值。</template>
      </p>
    </ChartPanel>
  </div>
</template>

<style scoped>
.a10 {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.a10__picker {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.a10__picker-label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.a10__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: 15px;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 12px;
  cursor: pointer;
}

.a10__chip::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: var(--chip);
  opacity: 0.45;
}

.a10__chip[aria-pressed='true'] {
  border-color: var(--chip);
  color: var(--color-text);
  background: var(--color-surface-muted);
}

.a10__chip[aria-pressed='true']::before {
  opacity: 1;
}

.a10__fallback {
  font-size: 11px;
  color: var(--risk-watch);
}

.a10__note {
  margin-top: var(--space-2);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.a10__abl {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--space-2) var(--space-4);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
}

.a10__abl li {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: baseline;
  gap: var(--space-2);
  padding: 6px 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 12px;
}

.a10__abl-label {
  font-weight: 600;
}

.a10__abl-desc {
  color: var(--color-text-muted);
  font-size: 11.5px;
}

.a10__abl-delta {
  font-family: var(--font-mono);
  font-size: 12px;
}

.a10__abl-tag {
  padding: 0 5px;
  border: 1px dashed var(--color-text-subtle);
  border-radius: 8px;
  font-size: 10px;
  color: var(--color-text-subtle);
}

.mono {
  font-family: var(--font-mono);
}
</style>
