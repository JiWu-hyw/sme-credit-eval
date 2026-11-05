<script setup lang="ts">
/**
 * A01 数据接入链路（FE-11 区块一）
 *
 * 三列布局：数据源 → 处理节点 → 数据集，用 SVG 贝塞尔连线串起来。
 * 选 SVG 而不是 ECharts graph：链路只有十几个节点、结构固定，
 * SVG 能用 CSS 变量上色、能被 SSR 渲染（冒烟测试要断言 DOM），
 * ECharts canvas 在无头环境里拿不到结构。
 *
 * 状态色沿用「已就绪 / 部分 / 待接入 / 阻塞 / 仅合成」五档，
 * 阻塞节点（主键映射）单独标红：它卡住了全部外部数据接入，是当前第一优先级。
 */
import { computed } from 'vue'

import type { DataPipeline, PipelineNode, PipelineStatus } from '@/types/contracts'
import { statusMeta } from '@/utils/format'

const props = defineProps<{ pipeline: DataPipeline }>()

const COLUMNS: Array<{ kind: PipelineNode['kind']; title: string; hint: string }> = [
  { kind: 'source', title: '数据源', hint: '真实 / 待接入分开标注' },
  { kind: 'process', title: '处理节点', hint: '预处理 · 主键映射 · 图构建' },
  { kind: 'dataset', title: '数据集', hint: '模型与前端消费的对象' },
]

const COL_W = 240
const ROW_H = 78
const PAD_TOP = 12
const GAP = 96

const byKind = computed(() =>
  Object.fromEntries(COLUMNS.map((c) => [c.kind, props.pipeline.nodes.filter((n) => n.kind === c.kind)])),
)

/** 节点在画布里的坐标（列 x 按序号，行 y 顺排） */
const pos = computed(() => {
  const map = new Map<string, { x: number; y: number; w: number; h: number }>()
  COLUMNS.forEach((col, ci) => {
    const list = byKind.value[col.kind] ?? []
    list.forEach((n, ri) => {
      map.set(n.id, { x: ci * (COL_W + GAP), y: PAD_TOP + ri * ROW_H, w: COL_W, h: 52 })
    })
  })
  return map
})

const canvasHeight = computed(() => {
  const maxRows = Math.max(...COLUMNS.map((c) => (byKind.value[c.kind] ?? []).length), 1)
  return PAD_TOP * 2 + maxRows * ROW_H
})

const canvasWidth = computed(() => COLUMNS.length * COL_W + (COLUMNS.length - 1) * GAP)

/** 连线：从左块右边中点到右块左边中点，三次贝塞尔 */
const paths = computed(() =>
  props.pipeline.edges
    .map((e) => {
      const a = pos.value.get(e.from)
      const b = pos.value.get(e.to)
      if (!a || !b) return null
      const x1 = a.x + a.w
      const y1 = a.y + a.h / 2
      const x2 = b.x
      const y2 = b.y + b.h / 2
      const mx = (x1 + x2) / 2
      return { id: `${e.from}->${e.to}`, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, status: e.status, label: e.label }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null),
)

const TONE_COLOR: Record<string, string> = {
  ok: 'var(--risk-good)',
  partial: 'var(--risk-watch)',
  warn: 'var(--risk-watch)',
  blocked: 'var(--risk-high-risk)',
  pending: 'var(--color-border-strong)',
}

function toneOf(status?: PipelineStatus) {
  return statusMeta(status).tone
}

/** 就绪率：只统计处理节点（数据集里含合成图，直接算会虚高） */
const readiness = computed(() => {
  const procs = byKind.value.process ?? []
  const ready = procs.filter((n) => n.status === 'ready').length
  return { ready, total: procs.length }
})
</script>

<template>
  <div class="pipeline">
    <div class="pipeline__head cluster">
      <p class="pipeline__summary">
        处理节点就绪 <strong>{{ readiness.ready }}/{{ readiness.total }}</strong>
        · 数据源 {{ (byKind.source ?? []).length }} 个（其中待接入
        {{ (byKind.source ?? []).filter((n) => n.status === 'pending').length }} 个）
      </p>
      <ul class="pipeline__legend" aria-label="状态图例">
        <li v-for="(t, k) in { ready: 'ok', partial: 'partial', pending: 'pending', blocked: 'blocked', synthetic_only: 'warn' }" :key="k">
          <span class="pipeline__dot" :style="{ background: TONE_COLOR[t as string] }" />
          {{ statusMeta(k as string).label }}
        </li>
      </ul>
    </div>

    <div class="pipeline__canvas-wrap">
      <svg
        class="pipeline__canvas"
        :viewBox="`0 0 ${canvasWidth} ${canvasHeight}`"
        :width="canvasWidth"
        :height="canvasHeight"
        role="img"
        :aria-label="`数据接入链路：${pipeline.nodes.length} 个节点、${pipeline.edges.length} 条边`"
        data-testid="pipeline-canvas"
      >
        <!-- 列标题 -->
        <g>
          <text
            v-for="(col, ci) in COLUMNS"
            :key="col.kind"
            :x="ci * (COL_W + GAP) + 4"
            y="0"
            class="pipeline__col-title"
          >
            {{ col.title }}
          </text>
        </g>

        <!-- 连线 -->
        <g>
          <path
            v-for="p in paths"
            :key="p.id"
            :d="p.d"
            class="pipeline__edge"
            :style="{ stroke: TONE_COLOR[toneOf(p.status)], strokeDasharray: toneOf(p.status) === 'pending' ? '4 4' : undefined }"
          />
        </g>

        <!-- 节点 -->
        <g v-for="n in pipeline.nodes" :key="n.id" data-testid="pipeline-node" :data-node-id="n.id" :data-status="n.status">
          <template v-if="pos.get(n.id)">
            <rect
              :x="pos.get(n.id)!.x"
              :y="pos.get(n.id)!.y + 14"
              :width="pos.get(n.id)!.w"
              :height="pos.get(n.id)!.h"
              rx="6"
              class="pipeline__node"
              :style="{ stroke: TONE_COLOR[toneOf(n.status)] }"
            />
            <circle
              :cx="pos.get(n.id)!.x + 14"
              :cy="pos.get(n.id)!.y + 14 + 26"
              r="4"
              :style="{ fill: TONE_COLOR[toneOf(n.status)] }"
            />
            <text :x="pos.get(n.id)!.x + 26" :y="pos.get(n.id)!.y + 14 + 22" class="pipeline__node-name">
              {{ n.name }}
            </text>
            <text :x="pos.get(n.id)!.x + 26" :y="pos.get(n.id)!.y + 14 + 40" class="pipeline__node-status">
              {{ statusMeta(n.status).label }}
            </text>
          </template>
        </g>
      </svg>
    </div>

    <!-- 文字版链路（无障碍 + 评审可复制；SVG 读屏不友好） -->
    <ol class="pipeline__steps">
      <li v-for="s in pipeline.steps ?? []" :key="s.order" class="pipeline__step">
        <span class="pipeline__step-no">{{ s.order }}</span>
        <span class="pipeline__step-name">{{ s.name }}</span>
        <code class="pipeline__step-impl">{{ s.impl }}</code>
        <span class="pipeline__step-detail">{{ s.detail }}</span>
        <span class="pipeline__badge" :data-tone="statusMeta(s.status).tone">
          {{ statusMeta(s.status).label }}
        </span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.pipeline {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.pipeline__summary {
  font-size: 13px;
  color: var(--color-text-muted);
}

.pipeline__summary strong {
  color: var(--color-text);
  font-family: var(--font-mono);
}

.pipeline__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin: 0 0 0 auto;
  padding: 0;
  list-style: none;
  font-size: 12px;
  color: var(--color-text-muted);
}

.pipeline__legend li {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.pipeline__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.pipeline__canvas-wrap {
  overflow-x: auto;
}

.pipeline__canvas {
  display: block;
  min-width: 640px;
}

.pipeline__col-title {
  font-size: 11px;
  fill: var(--color-text-subtle);
  font-family: var(--font-sans);
}

.pipeline__edge {
  fill: none;
  stroke-width: 1.4;
  opacity: 0.75;
}

.pipeline__node {
  fill: var(--color-surface);
  stroke-width: 1.2;
}

.pipeline__node-name {
  font-size: 12px;
  fill: var(--color-text);
  font-family: var(--font-sans);
}

.pipeline__node-status {
  font-size: 10px;
  fill: var(--color-text-subtle);
  font-family: var(--font-mono);
}

.pipeline__steps {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--color-border);
}

.pipeline__step {
  display: grid;
  grid-template-columns: 24px 140px 200px 1fr auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border);
  font-size: 12px;
}

.pipeline__step-no {
  font-family: var(--font-mono);
  color: var(--color-text-subtle);
}

.pipeline__step-name {
  font-weight: 600;
}

.pipeline__step-impl {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.pipeline__step-detail {
  color: var(--color-text-muted);
  line-height: 1.5;
}

.pipeline__badge {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  white-space: nowrap;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
}

.pipeline__badge[data-tone='ok'] {
  background: var(--risk-good-bg);
  color: var(--risk-good);
}

.pipeline__badge[data-tone='partial'],
.pipeline__badge[data-tone='warn'] {
  background: var(--risk-watch-bg);
  color: var(--risk-watch);
}

.pipeline__badge[data-tone='blocked'] {
  background: var(--risk-high-risk-bg);
  color: var(--risk-high-risk);
}

@media (max-width: 1023px) {
  .pipeline__step {
    grid-template-columns: 24px 1fr auto;
  }

  .pipeline__step-impl,
  .pipeline__step-detail {
    grid-column: 2 / -1;
  }
}
</style>
