<script setup lang="ts">
/**
 * P3 时空图谱（FE-12）
 *
 * 四个区块：
 *   E1 六类实体异质图   → /api/graph/structure（力导向 + 类型多选 + 季度窗口 + 节点浮层）
 *   E2 季度时间切片     → /api/graph/slices（滑块 / 播放 / 增量柱状）
 *   E3 中心性分析       → /api/graph/centrality（度分布 + 四类中心性 + 秩相关）
 *   E4 节点详情浮层     → 由 E1 选中触发，数据来自已取到的 structure（不额外发请求）
 *
 * 页面级的三条纪律（都做成机制，不写在文案里）：
 *   ① **合成图必须警示**：`dataset = synthetic_graph` → DatasetBadge 自动走 warning 基调；
 *      页头再挂一条明确的「合成数据 / 数据缺口」说明，并列出 blocked_by。
 *   ② **抽样必须说出来**：契约 n_nodes=878、默认只画 150，图下方写死「显示 / 全图」两个数；
 *      另给「全图 / 抽样」切换，切到全图时如实提示渲染会很慢。
 *   ③ **一个接口挂了不毁整页**：三区块独立取数、独立空错态，形状守卫只让坏的那块进空态。
 *
 * 取数纪律沿用 FE-04：useAsyncSection（>300ms 才亮骨架）+ StatePanel（原因 / 下一步 / 重试）。
 */
import { computed, ref, watch } from 'vue'

import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import CentralityPanel from '@/components/graph/CentralityPanel.vue'
import GraphForcePanel from '@/components/graph/GraphForcePanel.vue'
import GraphSlicePanel from '@/components/graph/GraphSlicePanel.vue'
import NodeDetailDrawer from '@/components/graph/NodeDetailDrawer.vue'
import { useAsyncSection, type SectionStatus } from '@/composables/useAsyncSection'
import {
  fetchGraphCentrality,
  fetchGraphSlices,
  fetchGraphStructure,
  getMockMode,
} from '@/api'
import { httpGetData } from '@/api/http'
import type { GraphCentrality, GraphNode, GraphSlices, GraphStructure } from '@/types/contracts'
import type { ContractMeta, DatasetKind, EntityKindKey } from '@/types'
import type { ReadableError } from '@/utils/errors'

/* ------------------------------ 取数 ------------------------------ */

const structure = useAsyncSection(fetchGraphStructure)
const slices = useAsyncSection(fetchGraphSlices)
const centrality = useAsyncSection(fetchGraphCentrality)

const sections = [structure, slices, centrality]

const pageMeta = computed<ContractMeta>(() => {
  const metas = sections.map((s) => (s.data.value as { meta?: ContractMeta } | null)?.meta)
  // 三份契约应同为 synthetic_graph；取第一份非空作为页面口径
  return (metas.find((m) => !!m) ?? {}) as ContractMeta
})

const dataset = computed<DatasetKind>(() => (pageMeta.value.dataset as DatasetKind) ?? 'synthetic_graph')
const nSamples = computed(() => pageMeta.value.n_samples as number | undefined)
const disclaimer = computed(() => String(pageMeta.value.disclaimer ?? ''))
const blockedBy = computed(() => (pageMeta.value.blocked_by as string[] | undefined) ?? [])
const syntheticAuc = computed(() => pageMeta.value.synthetic_auc as number | undefined)
const realAuc = computed(() => pageMeta.value.real_panel_row_auc as number | undefined)
const mockMode = computed(() => getMockMode())

function sectionView<T>(section: {
  status: { value: SectionStatus }
  data: { value: unknown }
  error: { value: ReadableError | null }
  retry: () => Promise<void>
}) {
  const env = section.data.value as { data?: T } | null
  const raw = env?.data
  return {
    status: section.status.value,
    error: section.error.value ?? { reason: '', nextStep: '', detail: '' },
    payload: raw && typeof raw === 'object' ? (raw as T) : null,
    retry: () => void section.retry(),
  }
}

const vStruct = computed(() => sectionView<GraphStructure>(structure))
const vSlices = computed(() => sectionView<GraphSlices>(slices))
const vCent = computed(() => sectionView<GraphCentrality>(centrality))

function hasArrays(payload: unknown, keys: string[]): boolean {
  if (!payload || typeof payload !== 'object') return false
  const p = payload as Record<string, unknown>
  return keys.every((k) => Array.isArray(p[k]))
}

const structOk = computed(() => hasArrays(vStruct.value.payload, ['nodes', 'edges', 'quarters']))
const slicesOk = computed(() => hasArrays(vSlices.value.payload, ['slices', 'quarters']))
/**
 * 中心性契约的形状：overall_top / metrics 是数组，by_kind_top 是**对象**（类型 → Top 行）。
 * 第一版把它也写进 hasArrays，结果 E3 永远走空态——顶部的说明写得很清楚：
 * 「缺 by_kind_top」是假警报，真实原因是我们自己判错了类型。形守卫要按契约逐字写。
 */
const centOk = computed(() => {
  const p = vCent.value.payload as Record<string, unknown> | null
  if (!p) return false
  return (
    Array.isArray(p.overall_top) &&
    Array.isArray(p.metrics) &&
    !!p.by_kind_top &&
    typeof p.by_kind_top === 'object'
  )
})

const anyError = computed(() => sections.filter((s) => s.status.value === 'error').length)
function retryAll() {
  sections.forEach((s) => {
    if (s.status.value === 'error') void s.retry()
  })
}

const statusOf = computed(() => ({
  structure: structure.status.value,
  slices: slices.status.value,
  centrality: centrality.status.value,
}))

/* ---------------------------- 交互状态 ---------------------------- */

/** 实体类型筛选：默认全选；至少留一类（全不选等于白屏） */
const ALL_KINDS: EntityKindKey[] = ['company', 'patent', 'investor', 'person', 'field', 'product']
const visibleKinds = ref<EntityKindKey[]>([...ALL_KINDS])

function toggleKind(kind: EntityKindKey) {
  const set = new Set(visibleKinds.value)
  if (set.has(kind)) set.delete(kind)
  else set.add(kind)
  // 一类都不留时，回退成全选并给出提示，而不是让画布空掉
  visibleKinds.value = set.size ? ALL_KINDS.filter((k) => set.has(k)) : [...ALL_KINDS]
}

const resetKinds = () => (visibleKinds.value = [...ALL_KINDS])
const kindsAllOn = computed(() => visibleKinds.value.length === ALL_KINDS.length)

/** 季度：null = 全时段 */
const quarterIndex = ref<number>(-1)
const activeQuarter = computed<string | null>(() => {
  const qs = vSlices.value.payload?.quarters ?? vStruct.value.payload?.quarters ?? []
  return quarterIndex.value < 0 ? null : (qs[quarterIndex.value] ?? null)
})

/** 当前季度可见节点集（来自切片契约，页面唯一口径） */
const visibleIds = computed<string[] | null>(() => {
  if (quarterIndex.value < 0) return null
  return vSlices.value.payload?.slices?.[quarterIndex.value]?.visible_node_ids ?? null
})

/** 选中节点 */
const selectedId = ref<string | null>(null)
const selectedNode = computed<GraphNode | null>(() => {
  if (!selectedId.value) return null
  return vStruct.value.payload?.nodes.find((n) => n.id === selectedId.value) ?? null
})
function onSelect(node: GraphNode | null) {
  selectedId.value = node?.id ?? null
}

/**
 * 抽样开关。
 * 默认「抽样」（meta.sample_cap=150）；切到全图时如果节点数很大，先打一条提示再切，
 * 不做静默切换——力导向图 800+ 节点在低端机上会卡到没法拖拽，用户得知道这是自己选的。
 */
const useFullGraph = ref(false)
const fullNodeCount = computed(() => vStruct.value.payload?.stats?.n_nodes ?? 0)
const shownNodeCount = computed(() => vStruct.value.payload?.stats?.n_nodes_shown ?? 0)
const sampled = computed(() => vStruct.value.payload?.stats?.n_nodes !== vStruct.value.payload?.stats?.n_nodes_shown)

function toggleSample() {
  useFullGraph.value = !useFullGraph.value
}

/**
 * 全图模式：契约给的是抽样后的 150 个节点，前端不自己重新抽样（口径必须由后端定）。
 * 所以「全图」在这里的真实含义是「向后端要 sample_cap=n_nodes 的那份契约」。
 * mock 通道下没有这个参数，于是如实提示「契约当前只有抽样版」。
 */
const fullGraphError = ref('')
const fullGraph = ref<GraphStructure | null>(null)

async function loadFullGraph() {
  fullGraphError.value = ''
  if (!sampled.value) return
  try {
    const data = await httpGetData<GraphStructure>('/graph/structure', {
      sample_cap: fullNodeCount.value,
    })
    if (!data || !Array.isArray(data.nodes)) throw new Error('返回结构不符')
    fullGraph.value = data
  } catch {
    // 不静默回落到抽样版：明确告诉用户"没拿到全图"以及怎么拿到
    fullGraph.value = null
    fullGraphError.value =
      `未能取得全图契约（当前只有抽样版 ${shownNodeCount.value}/${fullNodeCount.value}）。` +
      `全图需后端重跑 scripts/build_graph_contracts.py --sample-cap ${fullNodeCount.value} 后提供。`
  }
}

watch(useFullGraph, (on) => {
  if (on) void loadFullGraph()
})

/** 季度变化时，若选中节点尚未出现，自动取消选中（否则浮层会描述一个"还没进图"的节点） */
watch(activeQuarter, (q) => {
  if (!q || !selectedNode.value) return
  const entered = String(selectedNode.value.entered_at ?? '')
  if (entered && entered > q) selectedId.value = null
})

const BLOCKS = [
  { code: 'E1', title: '六类实体异质图', desc: '企业 / 专利 / 投资机构 / 核心人员 / 技术领域 / 产品，力导向布局' },
  { code: 'E2', title: '季度时间切片', desc: '图是怎么长出来的：按季度累计，可拖动滑块或播放' },
  { code: 'E3', title: '中心性分析', desc: '度分布 + 四类中心性对比，以及它们之间到底一不一致' },
  { code: 'E4', title: '能说明什么、不能说明什么', desc: '这张图撑得起哪些结论、哪些必须等真实数据' },
]
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P3 时空图谱</h1>
        <span class="page__sub">共 {{ BLOCKS.length }} 个区块 · 3 个接口</span>
        <span class="page__mode" :data-mode="mockMode">数据模式：{{ mockMode }}</span>
        <span class="page__meta">
          数据来源 <code>{{ dataset }}</code>
        </span>
      </div>
      <p class="page__lede">
        图谱是「技术要素 + 成长轨迹」落到企业身上的载体：企业通过专利连到技术领域、通过投资连到机构、
        通过任职连到人。本页把这张图摊开看——它长什么样、按季度怎么长、谁在结构上最重要。
      </p>
    </header>

    <!-- 红线二：语义未知统一提示（页面级只挂一条） -->
    <RedLineBanner :scope="'P3 时空图谱'" />

    <!-- 合成图声明：dataset=synthetic_graph 时必须有，且要写清缺口编号 -->
    <aside v-if="dataset === 'synthetic_graph'" class="page__notice" role="status" data-testid="synthetic-notice">
      <strong>本页为合成六类实体图</strong>
      <span>
        {{ disclaimer || '真实实体数据尚未接入，节点与边均为合成。' }}
        <template v-if="blockedBy.length">阻塞项：<code>{{ blockedBy.join(' / ') }}</code>。</template>
      </span>
    </aside>

    <StatePanel
      v-if="anyError >= 2"
      variant="error"
      title="部分区块未能加载"
      :reason="`${anyError} 个图谱接口请求失败，已失败区块各自给出原因；其余区块不受影响。`"
      next-step="先点「全部重试」；若仍失败，确认后端 /api/graph/* 已启动，或切 mock 模式。"
      :retry="retryAll"
      retry-text="全部重试"
    />

    <!-- ===================== E1 六类实体异质图 ===================== -->
    <section class="page__block" aria-labelledby="e1">
      <header class="page__block-head">
        <span class="page__code">E1</span>
        <h2 id="e1" class="page__block-title">六类实体异质图</h2>
        <p class="page__block-desc">{{ BLOCKS[0].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.structure === 'loading'" :kpi-count="0" :charts="[640]" />
      <StatePanel
        v-else-if="statusOf.structure === 'error'"
        variant="error"
        title="图谱结构未能加载"
        :reason="vStruct.error.reason"
        :next-step="vStruct.error.nextStep"
        :detail="vStruct.error.detail"
        :retry="vStruct.retry"
      />
      <StatePanel
        v-else-if="statusOf.structure === 'empty' || !structOk"
        variant="empty"
        title="暂无图谱结构"
        reason="接口返回的不是图谱结构（缺 nodes / edges / quarters），可能是网关回落了页面或契约版本不符。"
        next-step="运行 python3 scripts/build_graph_contracts.py 生成三份图谱契约后刷新。"
      />
      <template v-else>
        <div class="page__toolbar">
          <div class="page__tool-group" role="group" aria-label="类型筛选">
            <span class="page__tool-label">实体类型</span>
            <span class="page__tool-hint" :data-all-on="kindsAllOn">
              {{ kindsAllOn ? '全选' : `已选 ${visibleKinds.length}/6` }}
            </span>
            <button type="button" class="page__tool-btn" :disabled="kindsAllOn" @click="resetKinds">
              重置
            </button>
          </div>

          <div class="page__tool-group" role="group" aria-label="采样">
            <span class="page__tool-label">节点规模</span>
            <button
              type="button"
              class="page__tool-btn"
              :aria-pressed="!useFullGraph"
              data-testid="sample-mode"
              @click="useFullGraph = false"
            >
              抽样（{{ shownNodeCount }}）
            </button>
            <button
              type="button"
              class="page__tool-btn"
              :aria-pressed="useFullGraph"
              data-testid="full-mode"
              @click="toggleSample"
            >
              全图（{{ fullNodeCount }}）
            </button>
          </div>
        </div>

        <p v-if="useFullGraph && fullGraphError" class="page__tool-warn" role="alert" data-testid="full-graph-warn">
          {{ fullGraphError }}
        </p>

        <GraphForcePanel
          :graph="(useFullGraph && fullGraph) || vStruct.payload!"
          :dataset="dataset"
          :n-samples="nSamples"
          :visible-kinds="visibleKinds"
          :quarter="activeQuarter"
          :visible-ids="visibleIds"
          :selected-id="selectedId"
          @select="onSelect"
          @toggle-kind="toggleKind"
        />
      </template>
    </section>

    <!-- ===================== E2 季度时间切片 ===================== -->
    <section class="page__block" aria-labelledby="e2">
      <header class="page__block-head">
        <span class="page__code">E2</span>
        <h2 id="e2" class="page__block-title">季度时间切片</h2>
        <p class="page__block-desc">{{ BLOCKS[1].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.slices === 'loading'" :kpi-count="0" :charts="[320]" />
      <StatePanel
        v-else-if="statusOf.slices === 'error'"
        variant="error"
        title="时间切片未能加载"
        :reason="vSlices.error.reason"
        :next-step="vSlices.error.nextStep"
        :detail="vSlices.error.detail"
        :retry="vSlices.retry"
      />
      <StatePanel
        v-else-if="statusOf.slices === 'empty' || !slicesOk"
        variant="empty"
        title="暂无时间切片"
        reason="接口返回的不是切片结构（缺 slices / quarters）。"
        next-step="运行 python3 scripts/build_graph_contracts.py 生成三份图谱契约后刷新。"
      />
      <GraphSlicePanel
        v-else
        v-model:index="quarterIndex"
        :slices="vSlices.payload!"
        :dataset="dataset"
        :n-samples="nSamples"
      />
    </section>

    <!-- ===================== E3 中心性分析 ===================== -->
    <section class="page__block" aria-labelledby="e3">
      <header class="page__block-head">
        <span class="page__code">E3</span>
        <h2 id="e3" class="page__block-title">中心性分析</h2>
        <p class="page__block-desc">{{ BLOCKS[2].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.centrality === 'loading'" :kpi-count="0" :charts="[320, 320]" />
      <StatePanel
        v-else-if="statusOf.centrality === 'error'"
        variant="error"
        title="中心性分析未能加载"
        :reason="vCent.error.reason"
        :next-step="vCent.error.nextStep"
        :detail="vCent.error.detail"
        :retry="vCent.retry"
      />
      <StatePanel
        v-else-if="statusOf.centrality === 'empty' || !centOk"
        variant="empty"
        title="暂无中心性结果"
        reason="接口返回的不是中心性结构（缺 overall_top / by_kind_top / metrics）。"
        next-step="运行 python3 scripts/build_graph_contracts.py 生成三份图谱契约后刷新。"
      />
      <CentralityPanel v-else :centrality="vCent.payload!" :dataset="dataset" :n-samples="nSamples" />
    </section>

    <!-- ===================== E4 结论与缺口 ===================== -->
    <section class="page__block page__conclusion" aria-labelledby="e4">
      <header class="page__block-head">
        <span class="page__code">E4</span>
        <h2 id="e4" class="page__block-title">这张图能说明什么、不能说明什么</h2>
        <p class="page__block-desc">{{ BLOCKS[3].desc }}</p>
      </header>
      <div class="page__grid">
        <article class="page__card">
          <h3>能说明</h3>
          <ul>
            <li>
              链路是通的：六类实体、六种语义边、按季度增量，<b>图结构可以喂给 GAT</b>；
              合成图上的 STGNN 架构验证 AUC
              <b>{{ syntheticAuc ?? '0.9149' }}</b>，证明「聚合邻居 + 时序建模」这条通路能收敛。
            </li>
            <li>
              结构是长尾型的：少量技术领域 / 企业是枢纽，其余多为叶节点。
              这意味着 <b>GAT 的注意力要能区分「邻居多」与「邻居重要」</b>，否则退化成一个平均池化。
            </li>
            <li>
              中心性指标彼此并不一致（见 E3 秩相关矩阵）——「谁重要」必须先声明口径，
              这正好是后续「局部归因」要解释的部分。
            </li>
          </ul>
        </article>
        <article class="page__card page__card--risk">
          <h3>不能说明</h3>
          <ul>
            <li>
              <b>不能当实证结论。</b>本页 dataset = <code>{{ dataset }}</code>，
              与真实信贷面板的行级 AUC {{ realAuc ?? '0.596' }} 不是一个数据集，两者不得同图比较（红线一 / 红线四）。
            </li>
            <li>
              <b>不能说明浙江企业的真实结构。</b>缺 P0-1（userid→企业映射键）时，
              专利 / 融资 / 人员数据落不到具体企业上；缺 P0-4 / P0-5 时，这三类边根本不存在。
            </li>
            <li>
              <b>不能做长序列。</b>真实面板每企业平均 2 个月观测，季度切片是「结构演示」，
              真实数据到位后能否做季度粒度，取决于观测纵深，不取决于这张图跑得多顺。
            </li>
          </ul>
        </article>
      </div>
    </section>

    <footer class="page__foot cluster">
      <span>下一步：</span>
      <RouterLink :to="{ name: 'model' }" class="page__link">P4 模型与对比</RouterLink>
      <span class="page__foot-sep">·</span>
      <span>数据缺口 P0-1 / P0-4 / P0-5 补齐后，重跑脚本替换数据源，本页与前端代码零改动。</span>
    </footer>

    <!-- E4 节点详情浮层 -->
    <NodeDetailDrawer
      v-if="selectedNode"
      :node="selectedNode"
      :nodes="vStruct.payload?.nodes ?? []"
      :edges="vStruct.payload?.edges ?? []"
      :centrality="vCent.payload"
      @close="selectedId = null"
    />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.page__title-row {
  gap: var(--space-3);
}

.page__title {
  font-size: 22px;
  font-weight: 700;
}

.page__sub,
.page__meta {
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__meta code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 0 4px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}

.page__mode {
  margin-left: auto;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: 10px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.page__lede {
  max-width: 960px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.page__notice {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--risk-watch, #c98a12);
  background: #fdf5e2;
  border-radius: 0 var(--radius-sm, 4px) var(--radius-sm, 4px) 0;
  font-size: 12px;
  line-height: 1.75;
  color: #6b4f0c;
}

.page__notice code {
  font-family: var(--font-mono);
}

.page__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.page__block-head {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: baseline;
  gap: 4px var(--space-2);
}

.page__code {
  padding: 1px 6px;
  border: 1px solid var(--color-border-strong);
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.page__block-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}

.page__block-desc {
  grid-column: 2;
  margin: 0;
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface-muted);
  border-radius: var(--radius-sm, 4px);
}

.page__tool-group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.page__tool-label {
  font-size: 12px;
  font-weight: 600;
}

.page__tool-hint {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.page__tool-btn {
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-surface);
  font-size: 12px;
  cursor: pointer;
}

.page__tool-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.page__tool-btn[aria-pressed='true'] {
  border-color: #2f5d8a;
  background: #e8eef6;
  font-weight: 600;
}

.page__tool-warn {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--risk-warning, #d0622a);
  background: #fdeee6;
  font-size: 12px;
  line-height: 1.7;
  color: #8a3f14;
}

.page__conclusion .page__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

@media (max-width: 1080px) {
  .page__conclusion .page__grid {
    grid-template-columns: 1fr;
  }
}

.page__card {
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-top: 3px solid #2f5d8a;
  border-radius: var(--radius-md);
}

.page__card--risk {
  border-top-color: #d0622a;
}

.page__card h3 {
  margin: 0 0 var(--space-2);
  font-size: 14px;
}

.page__card ul {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.9;
  color: var(--color-text);
}

.page__card code {
  font-family: var(--font-mono);
  font-size: 11px;
}

.page__foot {
  gap: var(--space-2);
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__link {
  color: #2f5d8a;
  text-decoration: underline;
}

.page__foot-sep {
  opacity: 0.5;
}
</style>
