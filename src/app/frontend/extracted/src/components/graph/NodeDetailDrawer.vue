<script setup lang="ts">
/**
 * 节点详情浮层（FE-12 区块一的配套）
 *
 * 点节点弹出右侧抽屉，回答四件事：这是什么、它连了谁、它在图上有多重要、还剩哪些待补数据。
 *
 * 两条纪律写在实现里而不是文档里：
 *   1. **合成数据必须自证**：标题下方常驻一条警示——节点来自 synthetic_graph，
 *      企业名是「合成企业#001」这种占位名，不能当真实企业引用（红线三）；
 *   2. **"重要"必须指明是哪个中心性**：度、介数、接近、特征向量四者秩相关并不一致
 *      （契约里甚至出现负相关），只说「排名第 3」而不说是哪个指标，等于误导。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { GraphNode, GraphEdge, GraphCentrality, CentralityRow } from '@/types/contracts'
import { ENTITY_KIND_LABELS } from '@/types'
import { ENTITY_COLORS } from '@/styles/theme'

const props = defineProps<{
  node: GraphNode
  nodes: GraphNode[]
  edges: GraphEdge[]
  centrality?: GraphCentrality | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const nodeMap = computed(() => {
  const m = new Map<string, GraphNode>()
  for (const n of props.nodes) m.set(n.id, n)
  return m
})

const EDGE_LABELS: Record<string, string> = {
  apply: '申请',
  grant: '授权',
  invest: '投资',
  employ: '任职',
  field_of: '归属领域',
  produce: '产出产品',
}

interface Neighbour {
  id: string
  label: string
  kind: GraphNode['kind']
  relation: string
  direction: 'out' | 'in'
}

const neighbours = computed<Neighbour[]>(() => {
  const out: Neighbour[] = []
  for (const e of props.edges) {
    if (e.source === props.node.id) {
      const t = nodeMap.value.get(e.target)
      if (t) out.push({ id: t.id, label: t.label, kind: t.kind, relation: EDGE_LABELS[e.kind] ?? e.kind, direction: 'out' })
    } else if (e.target === props.node.id) {
      const s = nodeMap.value.get(e.source)
      if (s) out.push({ id: s.id, label: s.label, kind: s.kind, relation: EDGE_LABELS[e.kind] ?? e.kind, direction: 'in' })
    }
  }
  return out
})

/** 按关系聚合：一屏列出 5 条「授权」比列 50 条更有用 */
const neighbourGroups = computed(() => {
  const map = new Map<string, Neighbour[]>()
  for (const nb of neighbours.value) {
    const key = `${nb.relation}·${nb.direction === 'out' ? '出边' : '入边'}`
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(nb)
  }
  return Array.from(map.entries()).map(([key, items]) => ({ key, items }))
})

/** 该节点在四类中心性里的位次（总节点数来自契约 overall_top 的规模不可靠，用节点的百分位） */
/**
 * 中心性行：先查 Top 榜（有原始量），查不到再退到 node_index（只有百分位）。
 * 退路是必须的——第一版只查榜，点开 90% 的节点都看不到中心性。
 */
const centralityRow = computed<Partial<CentralityRow> | null>(() => {
  const id = props.node.id
  const hits = (props.centrality?.overall_top ?? []).find((r) => r.id === id)
  if (hits) return hits
  const byKind = props.centrality?.by_kind_top?.[props.node.kind] ?? []
  const inKind = byKind.find((r) => r.id === id)
  if (inKind) return inKind
  const idx = props.centrality?.node_index?.[id]
  if (!idx) return null
  return {
    id,
    label: idx.label,
    kind: idx.kind,
    degree: idx.degree,
    degree_pct: idx.degree_pct,
    betweenness_pct: idx.betweenness_pct,
    closeness_pct: idx.closeness_pct,
    eigenvector_pct: idx.eigenvector_pct,
  }
})

/** 属性（白名单顺序 + 剩余字段折叠） */
const ATTR_ORDER = ['city', 'stage', 'risk_label', 'role', 'tier', 'granted', 'ipc_prefix', 'citations', 'entered_at', 'alias']
const ATTR_LABELS: Record<string, string> = {
  city: '城市',
  stage: '生命周期阶段',
  risk_label: '风险标签',
  role: '角色',
  tier: '机构梯队',
  granted: '是否授权',
  ipc_prefix: 'IPC 主分类',
  citations: '被引次数',
  entered_at: '进入图季度',
  alias: '别名',
}

function fmt(v: unknown): string {
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (v === null || v === undefined || v === '') return '—'
  return String(v)
}

const attrs = computed(() =>
  ATTR_ORDER.filter((k) => props.node[k] !== undefined && props.node[k] !== null).map((k) => ({
    key: k,
    label: ATTR_LABELS[k] ?? k,
    value: fmt(props.node[k]),
  })),
)

const tone = computed(() => ENTITY_COLORS[props.node.kind] ?? '#7a7a7a')

/* 可访问性：Esc 关闭 + 打开时把焦点移到抽屉本体（读屏会先念标题） */
const panel = ref<HTMLElement | null>(null)
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.stopPropagation()
    emit('close')
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
})

watch(
  () => props.node.id,
  () => nextTick(() => panel.value?.focus()),
  { immediate: true },
)
</script>

<template>
  <aside
    ref="panel"
    class="node-drawer"
    role="dialog"
    aria-modal="true"
    aria-labelledby="node-drawer-title"
    tabindex="-1"
    data-testid="node-drawer"
  >
    <header class="node-drawer__head">
      <span class="node-drawer__kind" :style="{ background: tone }">
        {{ ENTITY_KIND_LABELS[node.kind] ?? node.kind }}
      </span>
      <h3 id="node-drawer-title" class="node-drawer__title" data-testid="node-drawer-title">
        {{ node.label }}
      </h3>
      <button type="button" class="node-drawer__close" aria-label="关闭详情" @click="emit('close')">×</button>
    </header>

    <p class="node-drawer__warn" role="note">
      合成图节点（synthetic_graph）：名称与属性均为占位，不对应任何真实企业。
    </p>

    <dl class="node-drawer__kv">
      <div><dt>节点编号</dt><dd class="mono" data-testid="node-drawer-id">{{ node.id }}</dd></div>
      <div><dt>度</dt><dd class="mono" data-testid="node-drawer-degree">{{ node.degree ?? neighbours.length }}</dd></div>
      <div v-for="a in attrs" :key="a.key"><dt>{{ a.label }}</dt><dd>{{ a.value }}</dd></div>
    </dl>

    <section v-if="centralityRow" class="node-drawer__sec" aria-labelledby="nd-cent">
      <h4 id="nd-cent" class="node-drawer__sub">四类中心性（百分位）</h4>
      <ul class="node-drawer__bars">
        <li v-for="m in (['degree', 'betweenness', 'closeness', 'eigenvector'] as const)" :key="m">
          <span class="node-drawer__bar-label">{{ centrality?.metric_labels?.[m] ?? m }}</span>
          <span class="node-drawer__bar">
            <i :style="{ width: `${Math.round((centralityRow[`${m}_pct`] ?? 0) * 100)}%` }" />
          </span>
          <span class="mono node-drawer__bar-val">{{ Math.round((centralityRow[`${m}_pct`] ?? 0) * 100) }}%</span>
        </li>
      </ul>
      <p class="node-drawer__hint">百分位 = 在全部节点里的排名位置，不是绝对数值；四类指标口径不同，不能横向比大小。</p>
    </section>

    <section class="node-drawer__sec" aria-labelledby="nd-nb">
      <h4 id="nd-nb" class="node-drawer__sub">
        关联实体 <span class="node-drawer__count">{{ neighbours.length }} 条</span>
      </h4>
      <p v-if="!neighbours.length" class="node-drawer__empty">
        该节点在当前图中没有连线（度为 0）。孤立节点在 GAT 聚合时拿不到邻居信息，建模前需要剔除或单独处理。
      </p>
      <div v-for="g in neighbourGroups" :key="g.key" class="node-drawer__group">
        <span class="node-drawer__group-name">{{ g.key }} · {{ g.items.length }}</span>
        <ul class="node-drawer__nb">
          <li v-for="nb in g.items.slice(0, 12)" :key="`${nb.id}-${nb.relation}`">
            <span class="node-drawer__dot" :style="{ background: ENTITY_COLORS[nb.kind] ?? '#7a7a7a' }" aria-hidden="true" />
            {{ nb.label }}
            <span class="mono node-drawer__nb-id">{{ nb.id }}</span>
          </li>
          <li v-if="g.items.length > 12" class="node-drawer__more">… 另有 {{ g.items.length - 12 }} 条</li>
        </ul>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.node-drawer {
  position: fixed;
  top: 64px;
  right: 0;
  bottom: 0;
  z-index: 40;
  width: min(400px, 92vw);
  padding: var(--space-4);
  overflow-y: auto;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border-strong);
  box-shadow: -8px 0 24px rgb(15 23 42 / 12%);
}

.node-drawer:focus {
  outline: none;
}

.node-drawer__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.node-drawer__kind {
  padding: 1px 7px;
  border-radius: 9px;
  font-size: 11px;
  color: #fff;
}

.node-drawer__title {
  flex: 1;
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}

.node-drawer__close {
  width: 26px;
  height: 26px;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  background: transparent;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.node-drawer__warn {
  margin: var(--space-2) 0 var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid #c98a12;
  background: #fdf5e2;
  font-size: 12px;
  line-height: 1.7;
  color: #6b4f0c;
}

.node-drawer__kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px var(--space-3);
  margin: 0 0 var(--space-3);
  font-size: 12px;
}

.node-drawer__kv div {
  display: flex;
  gap: 6px;
  min-width: 0;
}

.node-drawer__kv dt {
  color: var(--color-text-muted);
  white-space: nowrap;
}

.node-drawer__kv dd {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mono {
  font-family: var(--font-mono);
}

.node-drawer__sec {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.node-drawer__sub {
  margin: 0 0 var(--space-2);
  font-size: 13px;
  font-weight: 600;
}

.node-drawer__count {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-muted);
}

.node-drawer__bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.node-drawer__bars li {
  display: grid;
  grid-template-columns: 92px 1fr 40px;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.node-drawer__bar {
  height: 7px;
  border-radius: 4px;
  background: var(--color-surface-muted);
  overflow: hidden;
}

.node-drawer__bar i {
  display: block;
  height: 100%;
  background: #2f5d8a;
}

.node-drawer__bar-val {
  text-align: right;
  font-size: 11px;
  color: var(--color-text-muted);
}

.node-drawer__hint,
.node-drawer__empty {
  margin: var(--space-2) 0 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.node-drawer__group {
  margin-top: var(--space-2);
}

.node-drawer__group-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-muted);
}

.node-drawer__nb {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}

.node-drawer__nb li {
  display: flex;
  align-items: center;
  gap: 6px;
}

.node-drawer__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: 0 0 auto;
}

.node-drawer__nb-id {
  font-size: 11px;
  color: var(--color-text-muted);
}

.node-drawer__more {
  color: var(--color-text-muted);
  font-style: italic;
}
</style>
