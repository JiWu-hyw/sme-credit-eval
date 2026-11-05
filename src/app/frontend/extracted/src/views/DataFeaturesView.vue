<script setup lang="ts">
/**
 * P2 数据与特征（FE-11）
 *
 * 四个区块：
 *   A01 数据接入链路   → /api/data/pipeline
 *   A02 数据质量诊断   → /api/data/quality（缺失率热力图 + 缩尾前后箱线图）
 *   A03 面板观测纵深   → /api/data/panel-depth（每企业观测月数直方图）
 *   A04 特征体系三级树 → /api/features/tree（可折叠 + 字段详情浮层）
 *
 * 并承载「数据导入」入口（FE-20 的入口先以禁用态挂在这里，等 FE-20 交付后打开）。
 *
 * 取数纪律（FE-04 约定）：
 *   - 一律走 `useAsyncSection`：>300ms 才亮骨架，秒回不闪；
 *   - 空态 / 错误态一律 `StatePanel`：原因 + 下一步，错误态带重试；
 *   - 每个区块独立取数，一个接口挂了不让整页白屏。
 */
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import DataPipelineDiagram from '@/components/data/DataPipelineDiagram.vue'
import DataQualityPanel from '@/components/data/DataQualityPanel.vue'
import FeatureTreePanel from '@/components/data/FeatureTreePanel.vue'
import PanelDepthPanel from '@/components/data/PanelDepthPanel.vue'
import { useAsyncSection, type SectionStatus } from '@/composables/useAsyncSection'
import {
  fetchDataPipeline,
  fetchDataQuality,
  fetchFeatureTree,
  fetchPanelDepth,
  getMockMode,
} from '@/api'
import type { DataPipeline, DataQuality, FeatureTree, PanelDepth } from '@/types/contracts'
import type { ContractMeta, DatasetKind } from '@/types'
import { NOISE_FLOOR } from '@/utils/redlines'
import type { ReadableError } from '@/utils/errors'

/* ------------------------------ 取数 ------------------------------ */

const pipeline = useAsyncSection(fetchDataPipeline)
const quality = useAsyncSection(fetchDataQuality)
const depth = useAsyncSection(fetchPanelDepth)
const tree = useAsyncSection(fetchFeatureTree)

const sections = [pipeline, quality, depth, tree]

/** 区块级 meta：四个接口的 meta 必须同源同 n_samples，取第一个非空作页面口径 */
const pageMeta = computed<ContractMeta>(() => {
  const metas = sections.map((s) => (s.data.value as { meta?: ContractMeta } | null)?.meta)
  return (metas.find((m) => !!m) ?? {}) as ContractMeta
})

const dataset = computed<DatasetKind>(() => (pageMeta.value.dataset as DatasetKind) ?? 'real_panel')
const nSamples = computed(() => pageMeta.value.n_samples)
const noiseFloor = computed(() => pageMeta.value.noise_floor ?? NOISE_FLOOR)

/** 全文口径：demo 占位数据必须显式告示，不能当成实测 */
const isPlaceholder = computed(() => pageMeta.value.source === 'demo_placeholder')
const disclaimer = computed(() => String(pageMeta.value.disclaimer ?? ''))

const anyError = computed(() => sections.filter((s) => s.isError.value).length)
const mockMode = computed(() => getMockMode())

/**
 * 把异步区块摊平成**原始值**再进模板。
 *
 * 踩过的坑：`useAsyncSection` 返回的是普通对象，里面装着 ref / computed
 * （`showSkeleton`、`isError`、`isEmpty` 都是 computed，`status` / `data` 是 ref）。
 * 模板里写 `pipeline.showSkeleton` 时，vue 只解包**setup 返回值这一层**，
 * 不会再往里解包，于是 `v-if` 拿到的是个永远为真的 ref 对象 ——
 * 骨架屏再也下不去，`payload()` 也会因为拿到 ref 而返回 null（dev 实测卡死在此）。
 * 这里统一摊成原始值，条件判断与取数都老实了。
 */
function sectionView<T>(section: {
  status: { value: SectionStatus }
  data: { value: unknown }
  error: { value: ReadableError | null }
  isReady: { value: boolean }
  isEmpty: { value: boolean }
  isError: { value: boolean }
  showSkeleton: { value: boolean }
  retry: () => Promise<void>
}) {
  // 注意：`useAsyncSection` 存的**就是信封本身**（code/message/data/meta），
  // 所以载荷在 `section.data.value.data`，不是 `section.data.value`。
  const env = section.data.value as { data?: T } | null
  // 防御：即便信封形状不对（例如网关回落了 HTML），也不要让一块脏数据炸掉整页
  const raw = env?.data
  return {
    status: section.status.value,
    isLoading: section.status.value === 'loading',
    isReady: section.isReady.value,
    isEmpty: section.isEmpty.value,
    isError: section.isError.value,
    showSkeleton: section.showSkeleton.value,
    error: section.error.value ?? { reason: '', nextStep: '', detail: '' },
    payload: (raw && typeof raw === 'object' ? (raw as T) : null),
    retry: () => void section.retry(),
  }
}

/**
 * 四区块状态的原始值快照。
 *
 * 单独抽这一份，是因为模板里 `pipeline.status` 拿到的是 **ref 本身**（见上面 sectionView
 * 的注释），`=== 'loading'` 永远不成立，骨架屏既不下也不换。走 computed 摊平后，
 * 模板只比较字符串，逻辑与数据两头都干净。
 */
const statusOf = computed(() => ({
  pipeline: pipeline.status.value,
  quality: quality.status.value,
  depth: depth.status.value,
  tree: tree.status.value,
}))

/**
 * 结构守卫：区块组件依赖 `nodes` / `fields` / `bins` / `roots` 这几个数组。
 * 一旦信封里缺了它们（网关回落、旧版本接口、字段改名），组件内 `.filter()` 会抛错，
 * 而 onErrorCaptured 会把**整页**替换成错误态 —— 一个字段缺失不该毁掉四个区块。
 * 这里统一判一下，缺失就走该区块自己的「空态」，其余区块照常。
 */
function hasArrays(payload: unknown, keys: string[]): boolean {
  if (!payload || typeof payload !== 'object') return false
  const p = payload as Record<string, unknown>
  return keys.every((k) => Array.isArray(p[k]))
}

const vPipeline = computed(() => sectionView<DataPipeline>(pipeline))
const vQuality = computed(() => sectionView<DataQuality>(quality))
const vDepth = computed(() => sectionView<PanelDepth>(depth))
const vTree = computed(() => sectionView<FeatureTree>(tree))

const pipelineOk = computed(() => hasArrays(vPipeline.value.payload, ['nodes', 'edges']))
const qualityOk = computed(() =>
  hasArrays(vQuality.value.payload, ['fields', 'missing_matrix', 'winsor']),
)
const depthOk = computed(() => hasArrays(vDepth.value.payload, ['bins']))
const treeOk = computed(() => hasArrays(vTree.value.payload, ['roots']))

function retryAll() {
  sections.forEach((s) => {
    if (s.isError.value) void s.retry()
  })
}

const BLOCKS = [
  { code: 'A01', title: '数据接入链路', desc: '数据源 → 处理节点 → 数据集，并标注每步是否就绪' },
  { code: 'A02', title: '数据质量诊断', desc: '缺失率热力图（字段 × 年）+ 缩尾前后箱线图' },
  { code: 'A03', title: '面板观测纵深', desc: '每企业观测月数分布，判断时间维可行的建模方式' },
  { code: 'A04', title: '特征体系三级树', desc: '技术要素 / 成长轨迹 / 融合特征，点字段看详情' },
]
</script>

<template>
  <div class="page">
    <!-- 页头 -->
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P2 数据与特征</h1>
        <span class="page__sub">共 {{ BLOCKS.length }} 个区块 · 4 个接口</span>
        <span class="page__mode" :data-mode="mockMode">数据模式：{{ mockMode }}</span>
        <span class="page__meta">
          数据来源 <code>{{ dataset }}</code>
          <template v-if="nSamples">· n={{ nSamples }}</template>
        </span>
      </div>
      <p class="page__lede">
        数据是这条链路的起点：模型能走多远，取决于这里能接进来什么。本页把「接了哪些源、质量如何、
        纵深够不够、特征怎么组织」四件事摊开，目的是让评审一眼看出瓶颈在数据而不在算法。
      </p>
    </header>

    <!-- 红线二：语义未知统一提示条（页面级只挂一条） -->
    <RedLineBanner :scope="'P2 数据与特征'" />

    <!-- demo 占位声明：只有 source=demo_placeholder 时才出现 -->
    <aside v-if="isPlaceholder" class="page__notice" role="status">
      <strong>本页为结构同构占位数据</strong>
      <span>{{ disclaimer || '真实信贷面板尚未接入 data/processed，数值仅用于页面结构与交互联调。' }}</span>
    </aside>

    <!-- 数据导入入口（FE-20）：先以禁用态挂出入口，交付后打开 -->
    <section class="page__import" aria-labelledby="import-title">
      <div class="page__import-body">
        <h2 id="import-title" class="page__import-title">数据导入</h2>
        <p class="page__import-desc">
          批量评估模式的上传入口（FE-20）：6 列冻结模板（序号、企业名称、证书编号、来源年份、批次、发证日期），
          CSV 需 UTF-8 BOM，证书编号需匹配 <code>^GR\d{12}$</code>。
        </p>
      </div>
      <div class="page__import-actions">
        <button type="button" class="page__import-btn" disabled aria-disabled="true">
          选择文件（待 FE-20 交付）
        </button>
        <span class="page__import-hint">本页只负责「数据与特征」的诊断视图，导入流程跳外部批次页</span>
      </div>
    </section>

    <!-- 全页错误汇总：两个以上失败时给一次批量重试 -->
    <StatePanel
      v-if="anyError >= 2"
      variant="error"
      title="部分区块未能加载"
      :reason="`${anyError} 个接口请求失败，已失败区块各自给出原因；其余区块不受影响。`"
      next-step="先点「全部重试」；若仍失败，确认后端 /api/data/* 与 /api/features/* 已启动，或切 mock 模式。"
      :retry="retryAll"
      retry-text="全部重试"
    />

    <!-- A01 -->
    <section class="page__block" aria-labelledby="a01">
      <header class="page__block-head">
        <span class="page__code">A01</span>
        <h2 id="a01" class="page__block-title">数据接入链路</h2>
        <p class="page__block-desc">{{ BLOCKS[0].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.pipeline === 'loading'" :kpi-count="0" :charts="[360]" />
      <StatePanel
        v-else-if="statusOf.pipeline === 'error'"
        variant="error"
        title="数据接入链路未能加载"
        :reason="vPipeline.error.reason"
        :next-step="vPipeline.error.nextStep"
        :detail="vPipeline.error.detail"
        :retry="vPipeline.retry"
      />
      <StatePanel
        v-else-if="statusOf.pipeline === 'empty' || !pipelineOk"
        variant="empty"
        title="暂无接入链路"
        reason="接口返回的不是数据接入链路结构（缺 nodes / edges），可能是网关回落了页面或契约版本不符。"
        next-step="运行 python scripts/build_data_contracts.py --demo 生成契约后刷新；线上环境确认 /api 未被静态站回落。"
      />
      <DataPipelineDiagram v-else :pipeline="vPipeline.payload!" />
    </section>

    <!-- A02 -->
    <section class="page__block" aria-labelledby="a02">
      <header class="page__block-head">
        <span class="page__code">A02</span>
        <h2 id="a02" class="page__block-title">数据质量诊断</h2>
        <p class="page__block-desc">{{ BLOCKS[1].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.quality === 'loading'" :kpi-count="4" :charts="[320]" />
      <StatePanel
        v-else-if="statusOf.quality === 'error'"
        variant="error"
        title="数据质量诊断未能加载"
        :reason="vQuality.error.reason"
        :next-step="vQuality.error.nextStep"
        :detail="vQuality.error.detail"
        :retry="vQuality.retry"
      />
      <StatePanel
        v-else-if="statusOf.quality === 'empty' || !qualityOk"
        variant="empty"
        title="暂无质量诊断结果"
        reason="接口返回的不是质量诊断结构（缺 fields / missing_matrix / winsor）。"
        next-step="运行 python scripts/build_data_contracts.py --demo 生成契约后刷新。"
      />
      <DataQualityPanel
        v-else
        :quality="vQuality.payload!"
        :dataset="dataset"
        :n-samples="nSamples"
      />
    </section>

    <!-- A03 -->
    <section class="page__block" aria-labelledby="a03">
      <header class="page__block-head">
        <span class="page__code">A03</span>
        <h2 id="a03" class="page__block-title">面板观测纵深</h2>
        <p class="page__block-desc">{{ BLOCKS[2].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.depth === 'loading'" :kpi-count="0" :charts="[320]" />
      <StatePanel
        v-else-if="statusOf.depth === 'error'"
        variant="error"
        title="面板纵深数据未能加载"
        :reason="vDepth.error.reason"
        :next-step="vDepth.error.nextStep"
        :detail="vDepth.error.detail"
        :retry="vDepth.retry"
      />
      <StatePanel
        v-else-if="statusOf.depth === 'empty' || !depthOk"
        variant="empty"
        title="暂无面板纵深数据"
        reason="接口返回的不是面板纵深结构（缺 bins）。"
        next-step="运行 python scripts/build_data_contracts.py --demo 生成契约后刷新。"
      />
      <PanelDepthPanel
        v-else
        :depth="vDepth.payload!"
        :dataset="dataset"
        :n-samples="nSamples"
        :noise-floor="noiseFloor"
      />
    </section>

    <!-- A04 -->
    <section class="page__block" aria-labelledby="a04">
      <header class="page__block-head">
        <span class="page__code">A04</span>
        <h2 id="a04" class="page__block-title">特征体系三级树</h2>
        <p class="page__block-desc">{{ BLOCKS[3].desc }}</p>
      </header>

      <PageSkeleton v-if="statusOf.tree === 'loading'" :kpi-count="0" :charts="[360]" />
      <StatePanel
        v-else-if="statusOf.tree === 'error'"
        variant="error"
        title="特征体系树未能加载"
        :reason="vTree.error.reason"
        :next-step="vTree.error.nextStep"
        :detail="vTree.error.detail"
        :retry="vTree.retry"
      />
      <StatePanel
        v-else-if="statusOf.tree === 'empty' || !treeOk"
        variant="empty"
        title="暂无特征体系"
        reason="接口返回的不是特征树结构（缺 roots）。"
        next-step="运行 python scripts/build_data_contracts.py --demo 生成契约后刷新。"
      />
      <FeatureTreePanel v-else :tree="vTree.payload!" />
    </section>

    <!-- 页脚：下一步动作，指向尚未完成的坑 -->
    <footer class="page__foot cluster">
      <span>下一步：</span>
      <RouterLink :to="{ name: 'graph' }" class="page__link">P3 时空图谱</RouterLink>
      <span class="page__foot-sep">·</span>
      <span>数据缺口 P0-1（userid→企业映射键）与 P0-2（x21–x200 字段字典）未补齐前，本页字段只能显示代号。</span>
    </footer>

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
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--risk-watch);
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__notice strong {
  color: var(--risk-watch);
}

.page__import {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
}

.page__import-body {
  flex: 1 1 420px;
}

.page__import-title {
  font-size: 14px;
  font-weight: 600;
}

.page__import-desc {
  margin-top: var(--space-1);
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-muted);
}

.page__import-desc code {
  font-family: var(--font-mono);
  font-size: 11px;
}

.page__import-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-1);
}

.page__import-btn {
  min-height: var(--hit-target-min);
  padding: 0 var(--space-4);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  color: var(--color-text-subtle);
  font-size: 13px;
  cursor: not-allowed;
}

.page__import-hint {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.page__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.page__block-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.page__code {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 11px;
}

.page__block-title {
  font-size: 16px;
  font-weight: 600;
}

.page__block-desc {
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__foot {
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__link {
  color: var(--color-accent);
}

.page__foot-sep {
  color: var(--color-text-subtle);
}
</style>
