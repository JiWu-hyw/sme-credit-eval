<script setup lang="ts">
/**
 * A08 模型架构图（FE-13）
 *
 * 为什么不画成"论文架构图"：评审要看的是**链路是否真的这样接的**。
 * 所以五个节点按 `architecture.nodes/edges` 的契约顺序横向排布，边上写明张量流向，
 * 节点下方给关键超参（层数 / 头数 / hidden / 输出口径），右侧挂"这段链路对应哪份契约"。
 *
 * 用 SVG 手绘而不是 ECharts GraphChart：这张图节点固定、坐标确定，
 * 力导向会把"输入 → 输出"的阅读顺序打乱，反而更难读。
 */
import { computed } from 'vue'

import type { ModelArchitecture } from '@/types/contracts'

const props = defineProps<{ architecture: ModelArchitecture }>()

const NODE_W = 176
const GAP = 52
const H = 132

const nodes = computed(() => props.architecture.nodes)
const width = computed(() => nodes.value.length * NODE_W + (nodes.value.length - 1) * GAP)
const index = computed(() => Object.fromEntries(nodes.value.map((n, i) => [n.id, i])))
const xOf = (i: number) => i * (NODE_W + GAP)

const edges = computed(() =>
  props.architecture.edges
    .map((e) => {
      const a = index.value[e.from]
      const b = index.value[e.to]
      if (a === undefined || b === undefined) return null
      return { ...e, x1: xOf(a) + NODE_W, x2: xOf(b), mx: (xOf(a) + NODE_W + xOf(b)) / 2 }
    })
    .filter((e): e is NonNullable<typeof e> => e !== null),
)

/** 超参标签：由 detail 里第一个 "；" 之前的部分截断展示，避免节点里文字溢出 */
const detailOf = (d?: string) => (d ? d.split('；')[0] : '')
</script>

<template>
  <figure class="arch" data-testid="model-architecture">
    <svg :viewBox="`0 0 ${width} ${H}`" :width="'100%'" :height="H" role="img" aria-label="STGNN 模型架构：输入时空演化图 → GAT 空间聚合 → GRU 时间编码 → 注意力融合 → MLP 输出">
      <defs>
        <marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-border-strong)" />
        </marker>
      </defs>

      <!-- 边 -->
      <g v-for="e in edges" :key="`${e.from}-${e.to}`">
        <path
          :d="`M ${e.x1} 56 C ${e.mx} 56, ${e.mx} 56, ${e.x2} 56`"
          fill="none"
          stroke="var(--color-border-strong)"
          stroke-width="1.5"
          marker-end="url(#arch-arrow)"
        />
        <text :x="e.mx" :y="44" text-anchor="middle" class="arch__edge-label">{{ e.label }}</text>
      </g>

      <!-- 节点 -->
      <g v-for="(n, i) in nodes" :key="n.id" :data-kind="n.kind">
        <rect
          :x="xOf(i)"
          y="20"
          :width="NODE_W"
          :height="72"
          rx="8"
          :fill="n.kind === 'input' ? 'var(--color-accent-soft)' : n.kind === 'layer' ? 'var(--color-surface)' : 'var(--risk-excellent-bg)'"
          :stroke="n.kind === 'output' ? 'var(--risk-excellent)' : 'var(--color-accent)'"
          stroke-width="1.2"
        />
        <text :x="xOf(i) + NODE_W / 2" y="48" text-anchor="middle" class="arch__title">{{ n.label }}</text>
        <text :x="xOf(i) + NODE_W / 2" y="68" text-anchor="middle" class="arch__detail">
          {{ detailOf(n.detail) }}
        </text>
        <text
          v-if="n.detail && n.detail.includes('；')"
          :x="xOf(i) + NODE_W / 2"
          y="84"
          text-anchor="middle"
          class="arch__detail2"
        >
          {{ n.detail.split('；').slice(1).join('；') }}
        </text>
      </g>
    </svg>

    <figcaption class="arch__foot">
      <span v-if="architecture.loss"><b>损失</b>{{ architecture.loss }}</span>
      <span v-if="architecture.optimizer"><b>优化器</b>{{ architecture.optimizer }}</span>
    </figcaption>
    <ul v-if="architecture.notes?.length" class="arch__notes">
      <li v-for="n in architecture.notes" :key="n">{{ n }}</li>
    </ul>
  </figure>
</template>

<style scoped>
.arch {
  margin: 0;
  overflow-x: auto;
}

.arch__title {
  font-size: 12.5px;
  font-weight: 600;
  fill: var(--color-text);
}

.arch__detail {
  font-size: 10.5px;
  fill: var(--color-text-muted);
}

.arch__detail2 {
  font-size: 10px;
  fill: var(--color-text-subtle);
}

.arch__edge-label {
  font-size: 10px;
  fill: var(--color-text-subtle);
}

.arch__foot {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin-top: var(--space-2);
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.arch__foot b {
  margin-right: 6px;
  color: var(--color-text);
}

.arch__notes {
  margin: var(--space-2) 0 0;
  padding-left: 1.1em;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-muted);
}
</style>
