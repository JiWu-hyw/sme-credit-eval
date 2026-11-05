<script setup lang="ts">
/**
 * 数据来源角标（红线一，FE-03）
 *
 * 契约 §6.3：任一图表容器必须带数据来源角标，**不可关闭、不可省略**。
 * 本组件设计成「只能被 ChartPanel 内嵌」——没有关闭状态、没有交互绑定，
 * 业务代码拿不到「无角标」的容器。
 */
import { computed } from 'vue'

import { DATASET_LABELS, DATASET_TONE, type DatasetKind } from '@/types'

const props = withDefaults(
  defineProps<{
    dataset: DatasetKind
    nSamples?: number
  }>(),
  { nSamples: undefined },
)

const tone = computed(() => DATASET_TONE[props.dataset] ?? 'neutral')
const label = computed(() => DATASET_LABELS[props.dataset] ?? String(props.dataset))
const sampleText = computed(() =>
  props.nSamples === undefined ? '' : `n=${props.nSamples.toLocaleString('zh-CN')}`,
)
</script>

<template>
  <span class="dataset-badge" :data-tone="tone" :data-dataset="dataset" data-testid="dataset-badge">
    <span class="dataset-badge__dot" aria-hidden="true" />
    {{ label }}
    <span v-if="sampleText" class="dataset-badge__n">{{ sampleText }}</span>
  </span>
</template>

<style scoped>
.dataset-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: 11px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: 11px;
  white-space: nowrap;
}

.dataset-badge[data-tone='warning'] {
  border-color: var(--risk-watch);
  background: var(--risk-watch-bg);
  color: var(--risk-watch);
}

.dataset-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-subtle);
}

.dataset-badge[data-tone='warning'] .dataset-badge__dot {
  background: var(--risk-watch);
}

.dataset-badge__n {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-text-subtle);
}
</style>
