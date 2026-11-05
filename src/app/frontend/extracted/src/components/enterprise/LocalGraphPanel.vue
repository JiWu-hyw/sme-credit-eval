<script setup lang="ts">
/**
 * P7 区块五 · 局部图谱高亮（FE-16 / A14 的企业视角）
 *
 * 从全图里切出「以某企业为圆心、1 跳」的局部子图，其余压暗。
 * 与 P3 的全图不同，这一块的目的是**回答这家企业连了谁**，所以：
 *   · 圆心节点固定居中、半径固定，邻居按实体类型环形排布（同类型排在一起，能看出「专利多还是投资人多」）；
 *   · 每条边标出关系类型（申请 / 授权 / 投资 / 任职 / 归属领域 / 产出产品）；
 *   · 图的角标必须写 `synthetic_graph`：这些邻居来自六类实体**合成图**，
 *     真实共现关系要等 P0-4（专利）/ P0-5（投资事件），不能让读者以为「这家企业真的投了谁」。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { DatasetKind, EntityKindKey } from '@/types'
import { ENTITY_KIND_LABELS } from '@/types'
import { ENTITY_COLORS } from '@/styles/theme'
import type { LocalExplainEntry } from '@/types/contracts'
import { CHART_HEIGHT } from '@/charts/sizes'

const props = defineProps<{
  entry: LocalExplainEntry
  enterpriseName: string
  dataset: DatasetKind
  nSamples?: number
}>()

const EDGE_LABELS: Record<string, string> = {
  apply: '申请',
  grant: '授权',
  invest: '投资',
  employ: '任职',
  field_of: '归属领域',
  produce: '产出产品',
}

/** 环形布局：圆心固定 (0,0)，邻居按类型分组后依次排在圆上 */
const ring = computed(() => {
  const nbrs = props.entry.neighbours
  if (!nbrs.length) return []
  // 按类型分组（稳定顺序：企业外部→专利→投资机构→人员→领域→产品）
  const ORDER: EntityKindKey[] = ['company', 'patent', 'investor', 'person', 'field', 'product']
  const sorted = [...nbrs].sort(
    (a, b) => ORDER.indexOf(a.kind as EntityKindKey) - ORDER.indexOf(b.kind as EntityKindKey),
  )
  const r = 220
  return sorted.map((n, i) => {
    const angle = (2 * Math.PI * i) / sorted.length - Math.PI / 2
    return { ...n, x: Math.round(Math.cos(angle) * r), y: Math.round(Math.sin(angle) * r * 0.72) }
  })
})

const option = computed(() => {
  const nodes = [
    {
      id: 'center',
      name: `企业 ${props.entry.userid}`,
      x: 0,
      y: 0,
      symbolSize: 34,
      itemStyle: { color: ENTITY_COLORS.company, borderColor: '#1f2933', borderWidth: 2 },
      label: { show: true, position: 'bottom', fontSize: 10, distance: 6 },
    },
    ...ring.value.map((n) => ({
      id: n.node_id,
      name: n.label,
      x: n.x,
      y: n.y,
      symbolSize: 16 + Math.min(10, n.label.length),
      itemStyle: { color: ENTITY_COLORS[n.kind] ?? '#8a939e', opacity: 0.92 },
      label: { show: true, position: 'right', fontSize: 9, color: '#5b6673' },
    })),
  ]
  const links = ring.value.map((n) => ({
    source: 'center',
    target: n.node_id,
    label: { show: true, formatter: EDGE_LABELS[n.edge] ?? n.edge, fontSize: 9, color: '#5b6673' },
    lineStyle: { color: '#b9c0c9', width: 1.4, curveness: 0.06 },
  }))

  return {
    animation: false,
    tooltip: {
      formatter: (p: { dataType?: string; data?: Record<string, unknown> }) => {
        if (p.dataType === 'edge') return String(p.data?.label ?? '')
        const n = p.data as { name?: string; quarter?: string } | undefined
        return n ? `${n.name}${n.quarter ? `<br/>首现：${n.quarter}` : ''}` : ''
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'none',
        roam: true,
        draggable: true,
        data: nodes,
        links,
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 6,
        emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
      },
    ],
  }
})

const kindCount = computed(() => {
  const m = new Map<string, number>()
  for (const n of props.entry.neighbours) m.set(n.kind, (m.get(n.kind) ?? 0) + 1)
  return [...m.entries()].map(([k, v]) => ({ kind: k, label: ENTITY_KIND_LABELS[k as EntityKindKey] ?? k, n: v }))
})
</script>

<template>
  <div class="local-graph" data-testid="p7-local-graph-block">
    <ChartPanel
      v-if="ring.length"
      title="局部子图：以本企业为中心的一跳邻居"
      :dataset="dataset"
      :n-samples="nSamples"
      :noise-floor="null"
      :option="option"
      :height="CHART_HEIGHT.force"
    >
      <ul class="local-graph__legend">
        <li v-for="k in kindCount" :key="k.kind">
          <span class="local-graph__dot" :style="{ background: ENTITY_COLORS[k.kind as EntityKindKey] ?? '#8a939e' }" />
          {{ k.label }} × {{ k.n }}
        </li>
      </ul>
      <p class="local-graph__note" data-testid="p7-neighbour-note">{{ entry.neighbour_note }}</p>
    </ChartPanel>

    <div v-else class="local-graph__empty" data-testid="p7-local-graph-empty">
      <p class="local-graph__empty-title">该企业在图中没有可展示的一跳邻居</p>
      <p class="local-graph__empty-text">
        合成图抽样（sample_cap）可能没覆盖到该节点，也可能是真实关系数据（P0-4 / P0-5）尚未接入。
        这里不画空图，避免让人误以为「这家企业没有任何外部关系」。
      </p>
    </div>
  </div>
</template>

<style scoped>
.local-graph__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
  font-size: 11px;
  color: var(--color-text-muted);
}

.local-graph__dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 5px;
  border-radius: 50%;
}

.local-graph__note {
  margin-top: var(--space-2);
  font-size: 11px;
  line-height: 1.7;
  color: var(--color-text-subtle);
}

.local-graph__empty {
  padding: var(--space-6);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: center;
}

.local-graph__empty-title {
  font-size: 13px;
  font-weight: 600;
}

.local-graph__empty-text {
  max-width: 680px;
  margin: var(--space-2) auto 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}
</style>
