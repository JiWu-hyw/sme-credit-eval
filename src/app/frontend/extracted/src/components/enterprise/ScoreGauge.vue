<script setup lang="ts">
/**
 * P7 区块二 · 信用评分仪表盘（FE-16 / A18）
 *
 * 一个仪表盘要把四件事同时说清：分数（0–100）、档位（五档色带）、PD、以及「这个分数有多可信」。
 * 前三件是图，第四件必须写在图下面 —— 单独一个 78 分放进界面，任何人都会当成'78 分的企业'，
 * 而实测行级 AUC 只有 0.59（噪声地板 0.477），这个分数的排序能力非常有限。
 *
 * 两条本组件特有的纪律：
 *   ① **五档色带的方向不能画反**：高分区在右（优良），低分区在左（高风险）；
 *   ② 「为什么没有真实 PD」必须说 —— P0-3 标签口径未定，这里的 PD 是分数经单调映射得到的
 *      相对口径，不是校准过的违约概率。挂在仪表盘正下方，不藏进 tooltip。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { DatasetKind } from '@/types'
import type { EnterpriseRow } from '@/types/contracts'
import { RISK_BANDS } from '@/styles/theme'
import { bandCuts, bandOf, gaugeOption } from '@/utils/enterprise'
import { CHART_HEIGHT } from '@/charts/sizes'

const props = defineProps<{
  enterprise: EnterpriseRow
  cuts: number[]
  dataset: DatasetKind
  /** meta.prior（可能为 null：阻塞态） */
  prior: {
    base_rate: number
    base_rate_note: string
    noise_floor: number
    row_auc: number
    auc_note: string
  } | null
  nSamples?: number
}>()

const band = computed(() => bandOf(props.enterprise.credit_score, props.cuts))
const bands = computed(() => bandCuts(props.cuts))
const option = computed(() =>
  gaugeOption(props.enterprise.credit_score, props.cuts, props.enterprise.pd, band.value.label),
)

const scoreFmt = computed(() => props.enterprise.credit_score.toFixed(1))
const pdFmt = computed(() => `${(props.enterprise.pd * 100).toFixed(2)}%`)
/**
 * 档位序数（1=优良 … 5=高风险）：直接取 RISK_BANDS 的 band 字段，
 * 不按切点算 —— 切点改成非等距（比如 45/58/72/85）时，按 lo/20 推出来的序数会全错。
 */
const bandRank = computed(() => RISK_BANDS.find((b) => b.code === band.value.code)?.band ?? 0)
</script>

<template>
  <div class="score" data-testid="p7-score-block">
    <div class="score__gauge">
      <ChartPanel
        title="信用评分（0–100）与违约概率"
        :dataset="dataset"
        :n-samples="nSamples"
        :noise-floor="null"
        :option="option"
        :height="CHART_HEIGHT.line"
        :merged-components="['series']"
      >
        <ul class="score__legend" aria-label="五档色带">
          <li v-for="b in bands" :key="b.code" :data-active="b.code === band.code">
            <span class="score__swatch" :style="{ background: b.color }" />
            {{ b.label }}
            <span class="score__range">{{ b.lo }}–{{ b.hi === 100 ? 100 : b.hi - 1 }}</span>
          </li>
        </ul>
      </ChartPanel>
    </div>

    <div class="score__cards">
      <div class="score__card" data-testid="p7-score-value">
        <span class="score__label">信用分</span>
        <strong class="score__value">{{ scoreFmt }}</strong>
        <span class="score__tag" :style="{ color: band.color, borderColor: band.color }">{{ band.label }}</span>
      </div>
      <div class="score__card" data-testid="p7-pd-value">
        <span class="score__label">PD（相对口径）</span>
        <strong class="score__value">{{ pdFmt }}</strong>
        <span class="score__hint">
          由分数经单调映射得到，用于排序；<b>不是校准后的违约概率</b>（P0-3 标签口径未定）。
        </span>
      </div>
      <div class="score__card">
        <span class="score__label">档位序数</span>
        <strong class="score__value">{{ bandRank.toFixed(0) }} / 5</strong>
        <span class="score__hint">序数取自 viz_theme 的 band 字段：1=优良 … 5=高风险（与 reg_class 一一对应）。</span>
      </div>

      <aside v-if="prior" class="score__prior" data-testid="p7-prior-note">
        <span class="score__prior-title">这个分数能说明什么</span>
        <p>{{ prior.auc_note }}</p>
        <p class="score__prior-sub">
          参考：24 个月事件率 {{ (prior.base_rate * 100).toFixed(1) }}%（占位口径）· 噪声地板 AUC
          {{ prior.noise_floor }}
        </p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.score {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
}

@media (max-width: 1023px) {
  .score {
    grid-template-columns: minmax(0, 1fr);
  }
}

.score__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
  font-size: 11px;
  color: var(--color-text-muted);
}

.score__legend li {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 6px;
  border-radius: 10px;
  border: 1px solid transparent;
}

.score__legend li[data-active='true'] {
  border-color: var(--color-border-strong);
  background: var(--color-surface-muted);
  color: var(--color-text);
  font-weight: 600;
}

.score__swatch {
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.score__range {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-text-subtle);
}

.score__cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  align-content: start;
}

.score__card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  min-width: 0;
}

.score__label {
  font-size: 11px;
  color: var(--color-text-muted);
}

.score__value {
  font-family: var(--font-mono);
  font-size: 24px;
  font-weight: 700;
}

.score__tag {
  align-self: flex-start;
  margin-top: 2px;
  padding: 0 6px;
  border: 1px solid currentColor;
  border-radius: 8px;
  font-size: 11px;
}

.score__hint {
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--color-text-subtle);
}

.score__prior {
  grid-column: 1 / -1;
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--risk-watch);
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
  font-size: 11.5px;
  line-height: 1.75;
  color: var(--color-text);
}

.score__prior-title {
  display: block;
  margin-bottom: 2px;
  font-weight: 700;
  color: var(--risk-watch);
}

.score__prior-sub {
  color: var(--color-text-muted);
}
</style>
