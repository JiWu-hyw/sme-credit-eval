<script setup lang="ts">
/**
 * A11 ROC / PR / KS 曲线族（FE-13）
 *
 * 三张图各管一件事：
 *   - ROC：区分度全貌，对角线是"随机"，噪声地板 0.477 换成一条"等效随机"参考线；
 *   - PR：正例只占 10.7%，precision 的"随机水平"就是 0.107 —— 必须画出来，
 *     否则读者会把 0.13 当成"很差"，真实含义是"只比随机略好一点点"；
 *   - KS：FPR / TPR 双曲线 + 间隔带，间隔最大值即 KS。实测 KS 与曲线族里的
 *     合成 KS 对不上时以实测为准（图上直接并列两个数，不做隐藏）。
 *
 * 模型可单选（曲线族叠 4 条会糊成一团，ROC 区间又窄）。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import { CHART_HEIGHT } from '@/charts/sizes'
import type { DatasetKind } from '@/types'
import type { CurveSeries, RocPrKs } from '@/types/contracts'

const props = defineProps<{
  data: RocPrKs
  dataset: DatasetKind
  noiseFloor: number | null
}>()

const COLOR: Record<string, string> = {
  lr: '#2f5d8a',
  xgb: '#8a939e',
  gat: '#d0622a',
  mlp_nograph: '#c98a12',
}

const activeId = ref(props.data.series[0]?.model_id ?? '')
const series = computed<CurveSeries | undefined>(
  () => props.data.series.find((s) => s.model_id === activeId.value) ?? props.data.series[0],
)

/**
 * ROC 的轴必须用 log 刻度（FE-13 踩坑）：
 * AUC≈0.58 的模型，TPR 要到 FPR≈0.05 才明显爬起来，FPR 0–0.05 这一段占线性轴的 5%，
 * 线性刻度下曲线会被压成"贴着左边墙、贴着上边墙"的直角折线，看起来像跑坏了。
 * 改用 log 轴后，左上角那一段（真正体现区分度的部分）被拉开，且与 AUC 数值自洽。
 * `logBase: 10` + min 0.001：三个数量级，足够覆盖 FPR 从 0.1% 到 100%。
 */
const ROC_GRID = { left: 56, right: 20, top: 32, bottom: 44 }
const FPR_MIN = 0.001

const rocOption = computed(() => {
  const s = series.value
  if (!s) return {}
  const pts = s.roc.fpr.map((f, i) => [Math.max(f, FPR_MIN), s.roc.tpr[i]])
  const ksIdx = s.ks.curve.gap.indexOf(Math.max(...s.ks.curve.gap))
  return {
    animation: false,
    grid: ROC_GRID,
    tooltip: {
      trigger: 'axis',
      formatter: (ps: Array<{ value: number[] }>) => {
        const p = ps.find((x) => Array.isArray(x.value))
        return p ? `FPR ${(p.value[0] * 100).toFixed(2)}%<br/>TPR ${p.value[1].toFixed(3)}` : ''
      },
    },
    xAxis: {
      type: 'log',
      logBase: 10,
      min: FPR_MIN,
      max: 1,
      name: 'FPR（对数刻度）',
      nameLocation: 'middle',
      nameGap: 28,
      nameTextStyle: { fontSize: 10, color: '#8a939e' },
      axisLabel: { fontSize: 10, color: '#5b6673', formatter: (v: number) => `${+(v * 100).toFixed(1)}%` },
      splitLine: { lineStyle: { color: '#eef1f5' } },
      minorSplitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: 'TPR',
      min: 0,
      max: 1,
      nameTextStyle: { fontSize: 10, color: '#8a939e' },
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        name: '随机（对角线，log 轴上弯曲）',
        type: 'line',
        showSymbol: false,
        lineStyle: { color: '#b9c0c9', type: 'dotted', width: 1 },
        data: [FPR_MIN, 0.01, 0.1, 1].map((f) => [f, f]),
      },
      {
        name: `${s.model_label} ROC（AUC ${s.auc.toFixed(4)}）`,
        type: 'line',
        showSymbol: false,
        lineStyle: { color: COLOR[s.model_id] ?? '#2f5d8a', width: 2 },
        data: pts,
        markPoint: {
          symbol: 'circle',
          symbolSize: 8,
          itemStyle: { color: '#b3312c' },
          label: {
            fontSize: 9,
            color: '#b3312c',
            position: 'right',
            formatter: `KS 点 ${s.ks.measured.toFixed(3)}`,
          },
          data: [
            {
              coord: [
                Math.max(s.ks.curve.fpr[ksIdx] ?? 0.1, FPR_MIN),
                s.ks.curve.tpr[ksIdx] ?? 0.5,
              ],
            },
          ],
        },
      },
    ],
  }
})

const prOption = computed(() => {
  const s = series.value
  if (!s) return {}
  return {
    animation: false,
    grid: { left: 48, right: 16, top: 28, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: Array<{ value: number[] }>) => {
        const p = ps.find((x) => Array.isArray(x.value))
        return p ? `Recall ${p.value[0].toFixed(3)}<br/>Precision ${p.value[1].toFixed(3)}` : ''
      },
    },
    xAxis: {
      type: 'value',
      name: 'Recall',
      nameLocation: 'end',
      min: 0,
      max: 1,
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    yAxis: {
      type: 'value',
      name: 'Precision',
      min: 0,
      max: 0.6,
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        name: `随机基线（正例占比 ${(props.data.prevalence * 100).toFixed(1)}%）`,
        type: 'line',
        showSymbol: false,
        lineStyle: { color: '#b3312c', type: 'dashed', width: 1 },
        data: [[0, props.data.prevalence], [1, props.data.prevalence]],
      },
      {
        name: `${s.model_label} PR（AP ${s.ap.toFixed(4)}）`,
        type: 'line',
        showSymbol: false,
        lineStyle: { color: COLOR[s.model_id] ?? '#2f5d8a', width: 2 },
        data: s.pr.recall.map((r, i) => [r, s.pr.precision[i]]),
      },
    ],
  }
})

const ksOption = computed(() => {
  const s = series.value
  if (!s) return {}
  const c = s.ks.curve
  const idx = c.gap.indexOf(Math.max(...c.gap))
  return {
    animation: false,
    grid: { left: 48, right: 20, top: 28, bottom: 40 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, left: 0, itemWidth: 14, itemHeight: 8, textStyle: { fontSize: 11, color: '#5b6673' } },
    xAxis: {
      type: 'category',
      name: '分数阈值（由高到低：阈值降 → 抓得更宽）',
      nameLocation: 'middle',
      nameGap: 26,
      data: c.threshold.map((t) => t.toFixed(2)),
      axisLabel: { fontSize: 9, color: '#8a939e', interval: 4 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dde1e7' } },
    },
    yAxis: {
      type: 'value',
      name: '累计占比',
      min: 0,
      max: 1,
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        name: 'TPR（正例累计）',
        type: 'line',
        showSymbol: false,
        lineStyle: { color: '#2f5d8a', width: 2 },
        data: c.tpr,
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(201,138,18,0.12)' },
          data: [
            [
              { xAxis: String(c.threshold[Math.max(0, idx - 2)]) },
              { xAxis: String(c.threshold[Math.min(c.threshold.length - 1, idx + 2)]) },
            ],
          ],
        },
        markPoint: {
          symbol: 'pin',
          symbolSize: 26,
          itemStyle: { color: '#c98a12' },
          label: { fontSize: 9, color: '#fff', formatter: 'KS' },
          data: [{ coord: [String(c.threshold[idx]), c.tpr[idx]] }],
        },
      },
      {
        name: 'FPR（负例累计）',
        type: 'line',
        showSymbol: false,
        lineStyle: { color: '#8a939e', width: 2, type: 'dashed' },
        data: c.fpr,
      },
      {
        name: '间隔（TPR − FPR）',
        type: 'bar',
        barWidth: 6,
        itemStyle: { color: 'rgba(201,138,18,0.55)' },
        data: c.gap.map((g, i) => (i === idx ? g : 0)),
      },
    ],
  }
})

const measuredGap = computed(() => {
  const s = series.value
  if (!s) return 0
  return s.ks.measured - s.ks.value
})
</script>

<template>
  <div class="a11">
    <div class="a11__tabs" role="tablist" aria-label="选择模型查看曲线族">
      <button
        v-for="s in data.series"
        :key="s.model_id"
        type="button"
        role="tab"
        class="a11__tab"
        :aria-selected="s.model_id === (series?.model_id ?? '')"
        :data-testid="`curve-model-${s.model_id}`"
        :style="{ '--c': COLOR[s.model_id] ?? '#2f5d8a' }"
        @click="activeId = s.model_id"
      >
        {{ s.model_label }}<small>AUC {{ s.auc.toFixed(3) }}</small>
      </button>
    </div>

    <div class="grid-12">
      <div class="col-6">
        <ChartPanel
          title="ROC 曲线"
          :dataset="dataset"
          :n-samples="data.n_test"
          :noise-floor="noiseFloor"
          :option="rocOption"
          :height="CHART_HEIGHT.line"
        />
      </div>
      <div class="col-6">
        <ChartPanel
          title="PR 曲线（含随机基线）"
          :dataset="dataset"
          :n-samples="data.n_test"
          :noise-floor="null"
          :option="prOption"
          :height="CHART_HEIGHT.line"
        >
          <p class="a11__note">
            红色虚线是"掷骰子"的 precision = 正例占比 {{ (data.prevalence * 100).toFixed(1) }}%。
            模型 precision 0.13 只比它高 0.02——PR 图比 ROC 图更诚实地暴露了这个问题。
          </p>
        </ChartPanel>
      </div>
    </div>

    <ChartPanel
      title="KS 曲线：TPR / FPR 双曲线与最大间隔"
      :dataset="dataset"
      :n-samples="data.n_test"
      :noise-floor="null"
      :option="ksOption"
      :height="CHART_HEIGHT.line"
    >
      <p class="a11__note" data-testid="ks-facts">
        合成曲线 KS = <b class="mono">{{ series?.ks.value.toFixed(4) }}</b>，实测 KS =
        <b class="mono">{{ series?.ks.measured.toFixed(4) }}</b>
        （差 {{ measuredGap >= 0 ? '+' : '' }}{{ measuredGap.toFixed(4) }}）。
        曲线族只用于看形状，<b>结论一律以实测 KS 为准</b>；两者差异来自"逐样本得分是合成的"，
        不是模型跑了两遍。
      </p>
      <p v-if="series" class="a11__deriv">
        <span class="a11__deriv-tag">重建口径</span>{{ series.derivation }}
      </p>
    </ChartPanel>

    <ul class="a11__notes">
      <li v-for="n in data.notes" :key="n">{{ n }}</li>
    </ul>
  </div>
</template>

<style scoped>
.a11 {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.a11__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.a11__tab {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  min-height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 12px;
  cursor: pointer;
}

.a11__tab small {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-text-subtle);
}

.a11__tab[aria-selected='true'] {
  border-color: var(--c);
  background: var(--color-surface-muted);
  color: var(--color-text);
  font-weight: 600;
  box-shadow: inset 3px 0 0 var(--c);
}

.a11__note {
  margin-top: var(--space-2);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.a11__deriv {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--risk-watch);
  background: var(--risk-watch-bg);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.a11__deriv-tag {
  flex: 0 0 auto;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--risk-watch);
  color: #fff;
  font-size: 10px;
  align-self: flex-start;
}

.a11__notes {
  margin: 0;
  padding-left: 1.1em;
  font-size: 11.5px;
  line-height: 1.9;
  color: var(--color-text-muted);
}

.mono {
  font-family: var(--font-mono);
}
</style>
