<script setup lang="ts">
/**
 * 区块级数据来源角标（FE-13，红线一的区块落地）
 *
 * 为什么 P4 需要它：P4 四区块里，A08/A09（训练曲线）是 `synthetic_graph`，
 * A10/A11/A12（基准 / 曲线族 / 噪声地板）是 `real_panel`。红线一要求「同一图表不得混排
 * 数据来源」，但更严格的一步是：**同一页面上不同区块也必须一眼看出各自来源**。
 * 页头那颗角标只能表达一个 dataset，无法表达"这页有两个来源"。
 *
 * 纪律：
 *   - 角标一律读 `meta.dataset` / `meta.source`，**不在前端判断、不缓存**；
 *   - `source` 非 measured 时追加「重建 / 规格」标记，语气升为 warning——
 *     曲线是锚点重建的，不能和实测指标用同一种观感；
 *   - 组件无关闭态，业务侧无法省略（与 DatasetBadge 同一设计）。
 */
import { computed } from 'vue'

import { DATASET_LABELS, DATASET_TONE, type DatasetKind } from '@/types'

const props = withDefaults(
  defineProps<{
    /** 该区块对应接口的 meta.dataset */
    dataset: DatasetKind
    /** 该区块对应接口的 meta.source */
    source?: string
    /** 接口路径，悬停可查 */
    endpoint?: string
    /** 最近一次取数时间（meta.generated_at），用于"提示已随数据刷新" */
    updatedAt?: string
  }>(),
  { source: undefined, endpoint: undefined, updatedAt: undefined },
)

const SOURCE_LABEL: Record<string, string> = {
  measured: '实测结果',
  reconstructed_from_anchors: '锚点重建',
  contract_spec: '契约规格',
  synthetic_graph: '合成图',
  demo_placeholder: '占位数据',
  aggregated: '聚合视图',
}

const datasetLabel = computed(() => DATASET_LABELS[props.dataset] ?? String(props.dataset))
const sourceLabel = computed(() => (props.source ? (SOURCE_LABEL[props.source] ?? props.source) : '来源未声明'))
const tone = computed(() => {
  if (props.source && props.source !== 'measured') return 'warning'
  return DATASET_TONE[props.dataset] ?? 'neutral'
})
const isSynthetic = computed(() => props.dataset === 'synthetic_graph')
</script>

<template>
  <span
    class="section-badge"
    :data-tone="tone"
    :data-dataset="dataset"
    :data-source="source ?? 'unknown'"
    data-testid="section-dataset-badge"
    :title="endpoint ? `${endpoint}｜meta.dataset=${dataset}｜meta.source=${source ?? '—'}` : undefined"
  >
    <span class="section-badge__dot" aria-hidden="true" />
    <span class="section-badge__dataset">{{ datasetLabel }}</span>
    <span class="section-badge__sep" aria-hidden="true">·</span>
    <span class="section-badge__source">{{ sourceLabel }}</span>
    <span v-if="isSynthetic" class="section-badge__tag">合成图，不与真实面板对比</span>
    <span v-if="updatedAt" class="section-badge__time">更新于 {{ updatedAt.slice(11, 16) }}</span>
  </span>
</template>

<style scoped>
.section-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: 11px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: 11px;
  white-space: nowrap;
}

.section-badge[data-tone='warning'] {
  border-color: var(--risk-watch);
  background: var(--risk-watch-bg);
  color: var(--risk-watch);
}

.section-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-subtle);
}

.section-badge[data-tone='warning'] .section-badge__dot {
  background: var(--risk-watch);
}

.section-badge__sep {
  color: var(--color-text-subtle);
}

.section-badge__tag {
  margin-left: 2px;
  padding: 0 5px;
  border-radius: 8px;
  border: 1px dashed currentColor;
  font-size: 10px;
}

.section-badge__time {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-text-subtle);
}
</style>
