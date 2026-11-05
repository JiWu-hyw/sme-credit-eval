<script setup lang="ts">
/**
 * E1 六类实体异质图（FE-12 区块一）
 *
 * 力导向图本身不难，难的是「一张图别撒谎」：
 *   1. **抽样必须说出来**。契约给的 878 个节点不可能都塞进 640px 的画布，
 *      后端已按「度优先 + 每类保底」抽到 meta.sample_cap，前端必须在图上明确写
 *      「显示 150 / 878」，并把抽样规则写进说明——否则评审会以为全图就这点东西。
 *   2. **切片是"累计视图"，不是"当季快照"**。契约里 visible_node_ids 是「截至该季度
 *      已出现」的节点，图上必须写「截至 2024Q3」，让人知道边是累积的。
 *   3. **点击选中要能撑起详情浮层的定位**。选中节点后高亮其 1 邻居、其余压暗，
 *      这样「这个企业的关联实体是谁」不用再翻表格。
 *
 * 组件不做取数、不做筛选状态管理（那是页面的活），只吃 props + 抛事件：
 * 这样冒烟脚本可以直接对着契约数值断言，不必反向解析内部 state。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { GraphStructure, GraphNode, GraphEdge } from '@/types/contracts'
import type { DatasetKind, EntityKindKey } from '@/types'
import { ENTITY_KIND_LABELS } from '@/types'
import { ENTITY_COLORS } from '@/styles/theme'
import { CHART_HEIGHT } from '@/charts/sizes'
import type { EChartsOption } from '@/charts/echarts'
import { assertSingleDataset } from '@/utils/redlines'

const props = defineProps<{
  graph: GraphStructure
  dataset: DatasetKind
  nSamples?: number
  /** 选中的实体类型（空数组 = 全选） */
  visibleKinds: EntityKindKey[]
  /** 当前季度；null = 全时段 */
  quarter: string | null
  /**
   * 该季度「截至当时已出现」的节点集合，直接来自 graph_slices.json 的 visible_node_ids。
   *
   * 为什么不让本组件自己按 entered_at 算：第一版就是这么干的，结果与契约对不上
   * （页面 72 个、契约 60 个）。原因是专利 / 人员的 entered_at 取的是「连边最早季度」，
   * 而切片契约把企业按 entered_at、其余节点按首条边统计，两边口径本来就有一处差异。
   * 口径只能有一个来源——由后端切片契约给，前端只做交集。
   */
  visibleIds: string[] | null
  /** 当前选中节点 */
  selectedId: string | null
}>()

const emit = defineEmits<{
  (e: 'select', node: GraphNode | null): void
  (e: 'toggle-kind', kind: EntityKindKey): void
}>()

const dataset = computed(() => assertSingleDataset([props.dataset]))

const EDGE_LABELS: Record<string, string> = {
  apply: '申请',
  grant: '授权',
  invest: '投资',
  employ: '任职',
  field_of: '归属领域',
  produce: '产出产品',
}

const nodeIndex = computed(() => {
  const map = new Map<string, GraphNode>()
  for (const n of props.graph.nodes) map.set(n.id, n)
  return map
})

/** 当前季度可见节点集合；null 表示不按时间裁（全时段） */
const visibleByTime = computed<Set<string> | null>(() => {
  if (!props.quarter || !props.visibleIds) return null
  return new Set(props.visibleIds)
})

const visibleKindsSet = computed(
  () => new Set<EntityKindKey>(props.visibleKinds.length ? props.visibleKinds : props.graph.entity_kinds),
)

/** 邻居索引：选中节点时高亮 1 跳邻居 */
const neighbourMap = computed(() => {
  const map = new Map<string, Set<string>>()
  for (const e of props.graph.edges) {
    if (!map.has(e.source)) map.set(e.source, new Set())
    if (!map.has(e.target)) map.set(e.target, new Set())
    map.get(e.source)!.add(e.target)
    map.get(e.target)!.add(e.source)
  }
  return map
})

const focusSet = computed<Set<string> | null>(() => {
  if (!props.selectedId) return null
  const s = new Set<string>([props.selectedId])
  for (const nb of neighbourMap.value.get(props.selectedId) ?? []) s.add(nb)
  return s
})

interface RenderPayload {
  nodes: Array<Record<string, unknown>>
  links: Array<Record<string, unknown>>
}

/** 真正画到画布上的节点与边（三类过滤叠加：类型 / 季度 / 无悬空边） */
const render = computed<RenderPayload>(() => {
  const timeSet = visibleByTime.value
  const kept = props.graph.nodes.filter(
    (n) => visibleKindsSet.value.has(n.kind) && (!timeSet || timeSet.has(n.id)),
  )
  const keptIds = new Set(kept.map((n) => n.id))
  const links = props.graph.edges.filter((e) => keptIds.has(e.source) && keptIds.has(e.target))

  const focus = focusSet.value
  const nodes = kept.map((n) => {
    const dim = focus ? !focus.has(n.id) : false
    const isSel = props.selectedId === n.id
    const color = ENTITY_COLORS[n.kind] ?? '#7a7a7a'
    const base = 6 + Math.min(18, Math.sqrt(Number(n.degree ?? 1)) * 3.2)
    return {
      id: n.id,
      name: n.label,
      symbolSize: isSel ? base * 1.6 : base,
      category: props.graph.entity_kinds.indexOf(n.kind),
      value: Number(n.degree ?? 0),
      itemStyle: {
        color,
        opacity: dim ? 0.16 : 1,
        borderColor: isSel ? '#1b1f24' : 'transparent',
        borderWidth: isSel ? 2 : 0,
      },
      label: { show: isSel || (focus ? focus.has(n.id) : false) },
    }
  })

  const linkData = links.map((e: GraphEdge) => {
    const dim = focus ? !(focus.has(e.source) && focus.has(e.target)) : false
    const active = focus && !dim
    return {
      source: e.source,
      target: e.target,
      value: EDGE_LABELS[e.kind] ?? e.kind,
      lineStyle: {
        opacity: dim ? 0.05 : active ? 0.85 : 0.35,
        width: active ? 1.8 : 1,
        curveness: 0.08,
      },
    }
  })

  return { nodes, links: linkData }
})

const option = computed<EChartsOption>(() => ({
  animation: false,
  tooltip: {
    trigger: 'item',
    confine: true,
    formatter: (p: { dataType?: string; data?: Record<string, unknown> }) => {
      const d = (p.data ?? {}) as Record<string, unknown>
      if (p.dataType === 'edge') {
        return `${String(d.source)} → ${String(d.target)}<br/>语义：${String(d.value)}`
      }
      const node = nodeIndex.value.get(String(d.id))
      if (!node) return String(d.name ?? '')
      return [
        `<b>${node.label}</b>`,
        `类型：${ENTITY_KIND_LABELS[node.kind] ?? node.kind}`,
        `编号：${node.id}`,
        `度：${node.degree ?? 0}`,
        node.entered_at ? `进入图：${node.entered_at}` : '',
      ].filter(Boolean).join('<br/>')
    },
  },
  legend: [{ show: false }],
  series: [
    {
      type: 'graph',
      layout: 'force',
      roam: true,
      zoom: zoom.value,
      draggable: true,
      coordinateSystem: undefined,
      force: { repulsion: 62, edgeLength: [28, 78], gravity: 0.09, friction: 0.16, layoutAnimation: false },
      emphasis: { focus: 'adjacency', lineStyle: { width: 2.4 } },
      label: { show: false, position: 'right', fontSize: 10, color: '#3c4148' },
      edgeSymbol: ['none', 'none'],
      categories: props.graph.entity_kinds.map((k) => ({ name: ENTITY_KIND_LABELS[k] ?? k })),
      data: render.value.nodes,
      links: render.value.links,
      lineStyle: { color: 'source', opacity: 0.35 },
    },
  ],
}))

/**
 * 力导向图没有坐标轴，"缩放拖拽"靠 roam（鼠标滚轮 + 拖拽），
 * 但触控板 / 键盘用户未必能触发，所以额外给一组按钮。
 * 按钮改的是 series.zoom，EChartBase 的 deep watch 会把它合进当前实例，
 * 不需要在这里拿实例——拿实例反而要猜 dom 结构。
 */
const zoom = ref(1)
const zoomBy = (factor: number) => {
  zoom.value = Math.min(4, Math.max(0.25, zoom.value * factor))
}

/** 点击节点 → 抛给页面开浮层；点空白 → 取消选中 */
function onClick(params: { dataType?: string; data?: Record<string, unknown> }) {
  if (params.dataType !== 'node') {
    emit('select', null)
    return
  }
  const node = nodeIndex.value.get(String(params.data?.id))
  emit('select', node ?? null)
}

const samplesHint = computed(() => {
  const s = props.graph.stats
  const shown = render.value.nodes.length
  const full = s.n_nodes
  return {
    shown,
    full,
    edgesShown: render.value.links.length,
    sampled: full > shown,
  }
})

/** 图例：类型 → 当前可见节点计数（图例本身就是筛选器） */
const legendItems = computed(() =>
  props.graph.entity_kinds.map((k) => ({
    kind: k,
    label: ENTITY_KIND_LABELS[k] ?? k,
    color: ENTITY_COLORS[k] ?? '#7a7a7a',
    total: props.graph.stats.node_kinds?.[k] ?? 0,
    active: visibleKindsSet.value.has(k),
  })),
)
</script>

<template>
  <ChartPanel
    title="六类实体异质图（力导向）"
    :dataset="dataset"
    :n-samples="nSamples"
    :option="option"
    :height="CHART_HEIGHT.force"
    @chart-click="onClick"
  >
    <template #default>
      <div class="graph-tools">
        <ul class="graph-legend" role="list" aria-label="实体类型筛选">
          <li v-for="item in legendItems" :key="item.kind">
            <button
              type="button"
              class="graph-legend__btn"
              :class="{ 'is-off': !item.active }"
              :aria-pressed="item.active"
              :data-kind="item.kind"
              @click="emit('toggle-kind', item.kind)"
            >
              <span class="graph-legend__dot" :style="{ background: item.color }" aria-hidden="true" />
              {{ item.label }}
              <span class="graph-legend__count">{{ item.total }}</span>
            </button>
          </li>
        </ul>

        <div class="graph-zoom">
          <button type="button" class="graph-zoom__btn" aria-label="放大" @click="zoomBy(1.25)">＋</button>
          <button type="button" class="graph-zoom__btn" aria-label="缩小" @click="zoomBy(0.8)">－</button>
          <button type="button" class="graph-zoom__btn" aria-label="重置视图" @click="zoomBy(1 / zoom)">复位</button>
        </div>
      </div>

      <p class="graph-hint" data-testid="graph-hint">
        当前绘出 <b>{{ samplesHint.shown }}</b> 个节点 / <b>{{ samplesHint.edgesShown }}</b> 条边
        <template v-if="samplesHint.sampled">（全图 {{ samplesHint.full }} 节点，按「度优先 + 每类保底」抽样）</template>
        <template v-else>（全图完整绘出）</template>
        <span v-if="quarter">· 时间窗口：截至 {{ quarter }}（累计视图）</span>
        <span v-else>· 时间窗口：全时段</span>
      </p>
    </template>
  </ChartPanel>
</template>

<style scoped>
.graph-tools {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.graph-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.graph-legend__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
  font-size: 12px;
  color: var(--color-text);
  cursor: pointer;
}

.graph-legend__btn:hover {
  border-color: var(--color-border-strong);
}

.graph-legend__btn.is-off {
  opacity: 0.45;
  text-decoration: line-through;
}

.graph-legend__btn[aria-pressed='true'] {
  background: var(--color-surface-muted);
}

.graph-legend__dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}

.graph-legend__count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.graph-zoom {
  display: flex;
  gap: 4px;
  flex: 0 0 auto;
}

.graph-zoom__btn {
  min-width: 34px;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm, 4px);
  background: var(--color-surface);
  font-size: 12px;
  cursor: pointer;
}

.graph-hint {
  margin: var(--space-2) 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}
</style>
