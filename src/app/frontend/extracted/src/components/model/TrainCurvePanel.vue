<script setup lang="ts">
/**
 * A09 训练曲线（FE-13，loss / AUC 双轴）
 *
 * 双轴图在本项目里是有争议的——P3 的季度切片就是因为双轴读不懂而改成单轴。
 * 但这里双轴是**必要**的：loss（0.25–0.70）与 AUC（0.45–0.60）量级与方向都不同，
 * 拆成两张图反而看不出"损失降、AUC 升"的同步关系。代价是要把轴的归属写在轴标题上，
 * 且两条线一律实线 / 虚线区分，不靠颜色单打独斗（色觉不友好时仍可读）。
 *
 * 标三样东西：
 *   ① 噪声地板 0.477（红色虚线）—— 曲线从地板附近起步，说明模型一开始什么也没学到；
 *   ② 最优轮（早停回滚点）—— 实测 AUC 就挂在这一轮；
 *   ③ 早停轮（如果早停了）—— 与最优轮差 patience 轮，两个点必须分开画，
 *      否则读者会以为"训练到最后一轮才最好"。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import { CHART_HEIGHT } from '@/charts/sizes'
import type { DatasetKind } from '@/types'
import type { TrainCurve, TrainCurveSeries } from '@/types/contracts'

const props = defineProps<{
  data: TrainCurve
  dataset: DatasetKind
  noiseFloor: number | null
  /** 选中的模型（必须来自 props.data.curves） */
  modelId: string
}>()

const emit = defineEmits<{ (e: 'update:modelId', v: string): void; (e: 'chart-click', p: unknown): void }>()

const curve = computed<TrainCurveSeries | undefined>(() =>
  props.data.curves.find((c) => c.model_id === props.modelId) ?? props.data.curves[0],
)

const option = computed(() => {
  const c = curve.value
  if (!c) return {}
  const floor = props.data.highlight.noise_floor
  return {
    animation: false,
    grid: { left: 56, right: 64, top: 36, bottom: 44 },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 18,
      itemHeight: 8,
      textStyle: { fontSize: 11, color: '#5b6673' },
      data: ['训练损失（左轴）', '验证损失（左轴）', '验证 AUC（右轴）'],
    },
    tooltip: {
      trigger: 'axis',
      formatter: (ps: Array<{ seriesName: string; value: number; axisValue: number | string }>) => {
        const head = `epoch ${ps[0]?.axisValue ?? ''}`
        return [head, ...ps.map((p) => `${p.seriesName}：${Number(p.value).toFixed(4)}`)].join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      name: 'epoch',
      nameLocation: 'end',
      nameGap: 8,
      data: c.epochs.map(String),
      axisLabel: { fontSize: 10, color: '#8a939e', interval: Math.max(0, Math.floor(c.epochs.length / 10)) },
      axisLine: { lineStyle: { color: '#dde1e7' } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: 'loss（左）',
        nameTextStyle: { fontSize: 10, color: '#8a939e', align: 'left' },
        min: 0.2,
        max: 0.8,
        axisLabel: { fontSize: 10, color: '#5b6673' },
        splitLine: { lineStyle: { color: '#eef1f5' } },
      },
      {
        type: 'value',
        name: 'AUC（右）',
        nameTextStyle: { fontSize: 10, color: '#8a939e', align: 'right' },
        min: 0.44,
        max: 0.72,
        axisLabel: { fontSize: 10, color: '#5b6673' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '训练损失（左轴）',
        type: 'line',
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { width: 1.6, color: '#b9c0c9' },
        data: c.loss,
      },
      {
        name: '验证损失（左轴）',
        type: 'line',
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { width: 1.6, type: 'dashed', color: '#8a939e' },
        data: c.val_loss,
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#c98a12', type: 'dotted', width: 1 },
          label: { formatter: '最优轮', fontSize: 10, color: '#c98a12', position: 'insideEndTop' },
          data: [{ xAxis: String(c.best_epoch) }],
        },
      },
      {
        name: '验证 AUC（右轴）',
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        smooth: 0.2,
        lineStyle: { width: 2, color: '#2f5d8a' },
        areaStyle: { color: 'rgba(47,93,138,0.08)' },
        data: c.val_auc,
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            {
              yAxis: floor,
              lineStyle: { color: '#b3312c', type: 'dashed', width: 1 },
              label: { formatter: `噪声地板 ${floor}`, fontSize: 10, color: '#b3312c', position: 'insideStartTop' },
            },
            {
              xAxis: String(c.stop_epoch),
              lineStyle: { color: '#5b6673', type: 'dotted', width: 1 },
              label: {
                formatter: c.stopped_early ? `早停 @${c.stop_epoch}` : '跑满轮次',
                fontSize: 10,
                color: '#5b6673',
                position: 'insideEndTop',
              },
            },
          ],
        },
        markPoint: {
          symbol: 'pin',
          symbolSize: 26,
          itemStyle: { color: '#2f5d8a' },
          label: { fontSize: 9, color: '#fff', formatter: '最佳' },
          data: [{ coord: [String(c.best_epoch), c.best_val_auc] }],
        },
      },
    ],
  }
})

const active = computed(() => curve.value)
</script>

<template>
  <div class="curve">
    <div class="curve__tabs" role="tablist" aria-label="选择模型查看训练曲线">
      <button
        v-for="c in data.curves"
        :key="c.model_id"
        type="button"
        role="tab"
        class="curve__tab"
        :aria-selected="c.model_id === (active?.model_id ?? '')"
        :data-testid="`curve-tab-${c.model_id}`"
        @click="emit('update:modelId', c.model_id)"
      >
        {{ c.model_label }}
      </button>
    </div>

    <ChartPanel
      :title="`训练曲线：${active?.model_label ?? ''}（loss / AUC 双轴）`"
      :dataset="dataset"
      :noise-floor="null"
      :option="option"
      :height="CHART_HEIGHT.line"
    >
      <template v-if="active">
        <dl class="curve__facts" data-testid="curve-facts">
          <div><dt>最佳轮</dt><dd class="mono">{{ active.best_epoch }}</dd></div>
          <div><dt>最佳验证 AUC</dt><dd class="mono">{{ active.best_val_auc.toFixed(4) }}</dd></div>
          <div><dt>早停轮</dt><dd class="mono">{{ active.stop_epoch }}{{ active.stopped_early ? '' : '（未触发）' }}</dd></div>
          <div><dt>patience</dt><dd class="mono">{{ active.patience }}（monitor={{ active.monitor ?? 'val_loss' }}）</dd></div>
          <div><dt>最终验证 AUC</dt><dd class="mono">{{ active.final_val_auc.toFixed(4) }}</dd></div>
        </dl>
        <p class="curve__note">
          曲线起点就贴着噪声地板 {{ data.highlight.noise_floor }}——第 1 轮的 AUC 约
          {{ active.val_auc[0].toFixed(3) }}，说明没学之前模型等于随机。右端"最佳"钉在 epoch
          {{ active.best_epoch }}，该点即实测 holdout AUC {{ active.best_val_auc.toFixed(4) }}；
          {{ active.stopped_early ? `早停在 ${active.stop_epoch} 轮，回滚到最优轮权重。` : '本次跑满轮次，未触发早停。' }}
        </p>
        <p v-if="active.derivation" class="curve__deriv">
          <span class="curve__deriv-tag">重建口径</span>{{ active.derivation }}
        </p>
      </template>
    </ChartPanel>
  </div>
</template>

<style scoped>
.curve {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.curve__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.curve__tab {
  min-height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 12px;
  cursor: pointer;
}

.curve__tab[aria-selected='true'] {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}

.curve__facts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-5);
  margin: var(--space-3) 0 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
}

.curve__facts div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.curve__facts dt {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.curve__facts dd {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.mono {
  font-family: var(--font-mono);
}

.curve__note {
  margin-top: var(--space-2);
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.curve__deriv {
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

.curve__deriv-tag {
  flex: 0 0 auto;
  padding: 0 6px;
  border-radius: 8px;
  background: var(--risk-watch);
  color: #fff;
  font-size: 10px;
  align-self: flex-start;
}
</style>
