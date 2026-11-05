<script setup lang="ts">
/**
 * 批量评估 P8（FE-20）
 *
 * 这一页要把「100 条名单进去、91 个分出来、9 条被拦下且说得出为什么」整条链路跑通，
 * 并且在任何一步都不假装知道得比数据多。
 *
 * ## 六个区块 + 一条主线
 *
 *   ① 导入   —— 文件级预校验（类型 / 大小 / 6 列 / 证书编号正则 / 序号连续）+ 行预览
 *   ② 进度   —— 轮询 `GET /api/batch/{job_id}`，只显示「已处理 x / 共 y 行」与已处理部分的统计
 *   ③ 结果   —— 排序 / 按档筛选 / 关键词 / 只看异常；失败行**不隐藏**，可点开追溯
 *   ④ 导出   —— CSV 列名复用 Q3 模板 6 列 + 评估结果 5 列，失败行分数留空
 *   ⑤ 对比   —— 跨批次（as_of 不同）先拦一次，再给逐行差异
 *   ⑥ 任务   —— 6 个任务覆盖排队 / 处理中 / 完成 / 历史 / 空名单 / 解析失败
 *
 * ## 这一页最容易骗人的四个地方，以及各自的处理
 *
 *   1. **进度条**：`setInterval` 自己数数最省事，但那不是「进度」。
 *      这里的进度来自接口返回的 `rows_done`（mock 模式下每次轮询真的推进 24 行）。
 *   2. **成功率的分母**：写 `rows_ok / rows_total` 而不是 `rows_ok / rows_done`，
 *      处理中才不会被算成「91% 成功」。
 *   3. **失败行**：默认展示（状态筛选默认「全部」），且每条带字段名与原因，
 *      分成「数据错」与「映射缺」两类 —— 前者改文件能解决，后者改文件没用。
 *   4. **跨批次**：`as_of` 不同就直接说「不可比」，用户想看得先点一次确认。
 *
 * 三条红线照旧：不混排数据来源（本页只有一个来源：真实面板的 P7 契约直取）、
 * 字段语义未知只显代号（`x125`）、AUC 口径不与 SHAP 同图（本页不出 AUC 图，只在文字里注一次）。
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import SectionBadge from '@/components/model/SectionBadge.vue'
import BatchImport from '@/components/batch/BatchImport.vue'
import BatchProgress from '@/components/batch/BatchProgress.vue'
import BatchResultTable from '@/components/batch/BatchResultTable.vue'
import BatchExport from '@/components/batch/BatchExport.vue'
import BatchCompare from '@/components/batch/BatchCompare.vue'
import BatchJobList from '@/components/batch/BatchJobList.vue'
import { fetchBatchJob, fetchBatchStatus, submitBatch } from '@/api'
import { pollDelay } from '@/utils/batch'
import type { SheetRow } from '@/api/mock/batch'
import batchNotesContract from '../../outputs/contracts/batch_notes.json'
import type { BatchJob, BatchNote, BatchStatusContract } from '@/types/contracts'
import type { ContractMeta, DatasetKind } from '@/types'

const route = useRoute()
const router = useRouter()

/* ------------------------------ 任务列表 ------------------------------ */

const statusContract = shallowRef<BatchStatusContract | null>(null)
const statusMeta = ref<ContractMeta>({})
const statusError = ref<string | null>(null)
const listLoading = ref(true)

const jobs = computed<BatchJob[]>(() => statusContract.value?.jobs ?? [])
/**
 * 读图问答：接口通了就用接口的 `notes`；接口不可用时退回契约文件里的同一份
 * （`batch_notes.json` 是静态契约，与 `batch_status.json` 的 notes 同源同一次生成）。
 *
 * 为什么不在降级时把问答区一起藏掉：这一块讲的是「这页能证明什么、不能证明什么」，
 * 恰恰是页面上最不依赖后端的部分 —— 后端挂了，口径说明更该留着。
 */
const notes = computed<BatchNote[]>(
  () => statusContract.value?.notes ?? (batchNotesContract.notes as unknown as BatchNote[]),
)
const exportColumns = computed(() => statusContract.value?.export_columns ?? [])

const activeId = ref<string | null>(null)
/**
 * ⚠️ 用 `shallowRef` 装契约，所以 computed **不会**因为 mutation 而失效 ——
 * 轮询里必须整体替换 `statusContract.value`（新对象 + 新数组），
 * `activeJob` 才会重新计算。踩过一次：直接 push 进 jobs 数组，接口在推进、
 * 页面却停在 0 / 100，看起来像"后端卡住了"。
 */
const activeJob = computed<BatchJob | null>(
  () => jobs.value.find((j) => j.job_id === activeId.value) ?? jobs.value[0] ?? null,
)

async function loadStatus() {
  listLoading.value = true
  statusError.value = null
  try {
    const env = await fetchBatchStatus()
    statusContract.value = env.data
    statusMeta.value = (env.meta ?? {}) as ContractMeta
    if (!activeId.value) {
      // 路由参数优先（可分享「这个任务」的链接），否则落到契约声明的默认任务
      const fromQuery = typeof route.query.job === 'string' ? route.query.job : ''
      activeId.value =
        jobs.value.find((j) => j.job_id === fromQuery)?.job_id ??
        env.data.default_job_id ??
        jobs.value[0]?.job_id ??
        null
    }
  } catch (e) {
    statusError.value = e instanceof Error ? e.message : String(e)
  } finally {
    listLoading.value = false
  }
}

void loadStatus()

/* ------------------------------ 轮询 ------------------------------ */

/**
 * 轮询：只对**未完成**的任务开定时器，且间隔退避（1s → 2s → 5s）。
 *
 * 为什么不在 `watch` 里 `setTimeout` 递归：切换任务时旧定时器很容易漏清，
 * 结果是两个任务同时在刷、进度条一通乱跳。这里用单一的 `timer` 句柄 + 每次轮询前先清。
 */
let timer: ReturnType<typeof setTimeout> | undefined
let startedAt = 0
const polling = ref(false)

function stopPolling() {
  if (timer !== undefined) {
    clearTimeout(timer)
    timer = undefined
  }
  polling.value = false
}

function schedulePoll(jobId: string) {
  stopPolling()
  startedAt = Date.now()
  polling.value = true
  const tick = async () => {
    try {
      const env = await fetchBatchJob(jobId)
      const next = env.data
      if (next && statusContract.value) {
        const idx = statusContract.value.jobs.findIndex((j) => j.job_id === jobId)
        const list = [...statusContract.value.jobs]
        if (idx >= 0) list[idx] = next
        else list.unshift(next)
        statusContract.value = { ...statusContract.value, jobs: list }
      }
      // 以「队列里此刻的真实状态」为准：`next` 是接口返回的同一对象引用，
      // 判断当前是否还要继续，必须看列表里那份（mock 下二者是同一个对象，但真实后端不是）
      const now = statusContract.value?.jobs.find((j) => j.job_id === jobId) ?? next
      if (next && now && (now.status === 'processing' || now.status === 'queued')) {
        timer = setTimeout(tick, pollDelay(Date.now() - startedAt))
        return
      }
    } catch {
      // 轮询失败不打断页面：把下一次拉长到 5s 再试，最多由用户手动「刷新」
    }
    stopPolling()
  }
  timer = setTimeout(tick, 400)
}

/**
 * 只盯「任务的 id + 状态」两件事。
 *
 * ⚠️ 踩坑：最初这里 `watch(() => activeJob.value?.job_id)`，
 * 但 `activeJob` 是**对象引用**，轮询里替换 `statusContract` 后 `job_id` 没变，
 * watch 不会重跑 —— 看起来一切正常，实际上 `schedulePoll` 在提交那一刻被
 * `immediate` 的那次 watch 用**旧任务状态**提前停掉了（当时任务还是 processing 但 id 是默认任务）。
 * 现在把 status 一并作为依赖，并且只在「确实需要」时才重排定时器。
 */
watch(
  () => [activeJob.value?.job_id, activeJob.value?.status] as const,
  ([id, status]) => {
    if (!id) return
    if (`${route.query.job ?? ''}` !== id) {
      // replace 是异步导航；这里不 await —— 它只影响 URL 可分享性，不该阻塞进度显示
      void router.replace({ query: { ...route.query, job: id } })
    }
    if (status === 'processing' || status === 'queued') {
      if (!polling.value) schedulePoll(id)
    } else {
      stopPolling()
    }
  },
  { immediate: true },
)

onBeforeUnmount(stopPolling)

/* ------------------------------ 提交 ------------------------------ */

const submitting = ref(false)
const submitted = ref<{ jobId: string; rows: number; seconds: number } | null>(null)
const focusRow = ref<number | null>(null)

async function onSubmit(payload: { name: string; size: number; sheets: SheetRow[] }) {
  submitting.value = true
  submitted.value = null
  try {
    const env = await submitBatch({ name: payload.name, size: payload.size }, payload.sheets)
    const data = env.data
    submitted.value = { jobId: data.job_id, rows: data.rows_total, seconds: data.estimated_seconds }
    // 先把本地任务挂进列表（还没轮询过，evidence 为空），再切过去开始轮询
    if (statusContract.value) {
      const placeholder: BatchJob = {
        job_id: data.job_id,
        status: 'processing',
        created_at: data.accepted_at,
        updated_at: data.accepted_at,
        as_of: data.as_of,
        filename: data.filename,
        rows_total: data.rows_total,
        summary: {
          rows_total: data.rows_total,
          rows_done: 0,
          rows_ok: 0,
          rows_failed: 0,
          success_rate: 0,
          score_mean: null,
          score_min: null,
          score_max: null,
          anomaly_count: 0,
          band_counts: {},
          error_breakdown: [],
          final: false,
        },
        progress_note: '已受理，正在逐行打分',
        live_note: null,
        export_ready: false,
        error: null,
        evidence: [],
      }
      const rest = statusContract.value.jobs.filter((j) => j.job_id !== data.job_id)
      statusContract.value = { ...statusContract.value, jobs: [placeholder, ...rest] }
    }
    activeId.value = data.job_id
    schedulePoll(data.job_id)
  } finally {
    submitting.value = false
  }
}

/* ------------------------------ 对比 ------------------------------ */

const compareOpen = ref(false)
const compareBaseId = ref<string | null>(null)

const compareBase = computed<BatchJob | null>(
  () => jobs.value.find((j) => j.job_id === compareBaseId.value) ?? null,
)

/** 可选的历史批次：已完成、且不是当前任务 */
const compareCandidates = computed(() =>
  jobs.value.filter((j) => j.status === 'done' && j.job_id !== activeJob.value?.job_id),
)

function openCompare() {
  compareOpen.value = true
  // 默认选最近一个历史批次（列表已按 created_at 倒序取第一个"不是当前任务"的）
  compareBaseId.value = compareCandidates.value[0]?.job_id ?? null
}

/* ------------------------------ 导出 ------------------------------ */

const exportSection = ref<InstanceType<typeof BatchExport> | null>(null)
const lastExport = ref<string | null>(null)
/** 从结果表点「导出 CSV」时滚到导出区块 —— 导出列与「失败行怎么处理」在那边写清楚了，
 *  直接下载会让人不知道导出的到底是哪几列 */
function openExport() {
  exportSection.value?.$el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
}

/* ------------------------------ 呈现 ------------------------------ */

const dataset = computed<DatasetKind>(() => (statusMeta.value.dataset as DatasetKind) ?? 'real_panel')
const source = computed(() => String(statusMeta.value.source ?? 'contract_spec'))

const activeRows = computed(() => activeJob.value?.evidence ?? [])
const isLive = computed(() => activeJob.value?.status === 'processing')
const canExport = computed(() => Boolean(activeJob.value?.export_ready && activeRows.value.length))

const pageLoading = computed(() => listLoading.value)
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row">
        <h1 class="page__title">P8 批量评估</h1>
        <span class="page__sub">
          3 个接口 · POST /api/batch/upload · GET /api/batch/{job_id} · GET /api/batch/{job_id}/export
        </span>
        <span v-if="activeJob" class="page__meta">当前 <code>{{ activeJob.job_id }}</code></span>
      </div>
      <p class="page__lede">
        这一页回答「一百家企业的名单能不能一次跑完」。答案不是"能"，而是
        <b>「91 家出分、9 家被拦下、每一家都能追到面板里的哪条记录」</b>——
        名单导入 → 逐行打分 → 排序筛选 → 导出 CSV 是一条闭环，任何一步少了可追溯性，
        这一页就退化成一张漂亮的总表。
      </p>
    </header>

    <RedLineBanner scope="P8 批量评估" :affected="['A21']" />

    <StatePanel
      v-if="statusError && !statusContract"
      variant="error"
      title="批量任务列表未能加载"
      :reason="statusError"
      next-step="确认后端 /api/batch 可用；演示模式请把 VITE_USE_MOCK 设为 mock 或 auto。"
      :retry="() => void loadStatus()"
    />

    <PageSkeleton v-else-if="pageLoading" :kpi-count="5" :charts="[]" />

    <template v-else>
      <!-- 一 · 名单导入 -->
      <div class="section-stack">
        <header class="section-badge-row">
          <SectionBadge :dataset="dataset" :source="source" endpoint="POST /api/batch/upload" />
          <span class="section-badge-row__job">
            <code>{{ activeJob?.job_id }}</code>
            已处理 {{ activeJob?.summary.rows_done ?? 0 }} / {{ activeJob?.rows_total ?? 0 }} 行
          </span>
        </header>
        <BatchImport :busy="submitting" @submit="onSubmit" />
        <p v-if="submitted" class="receipt" data-testid="p8-receipt">
          已受理 <b>{{ submitted.rows }}</b> 行，任务 <code>{{ submitted.jobId }}</code> 已进入队列，
          预计 {{ submitted.seconds }} 秒。下面是它的实时进度。
        </p>
        <p v-if="submitting" class="receipt receipt--dim">正在提交…</p>
      </div>

      <template v-if="activeJob">
        <!-- 二 · 进度 -->
        <BatchProgress :job="activeJob" @open-row="(n) => (focusRow = n)" />

        <!-- 三 · 结果 -->
        <BatchResultTable
          :rows="activeRows"
          :as-of="activeJob.as_of"
          :focus-row="focusRow"
          :live="isLive"
          @export="openExport"
          @compare="openCompare"
        />

        <!-- 四 · 导出（未完成时不出现：导出一份跑了一半的 CSV 比不给更糟） -->
        <BatchExport
          v-if="canExport"
          ref="exportSection"
          :job="activeJob"
          :rows="activeRows.length"
          @done="(f) => (lastExport = f)"
        />
        <StatePanel
          v-else
          variant="pending"
          compact
          title="导出暂不可用"
          :reason="
            activeJob.status === 'failed'
              ? '任务在解析阶段就失败了，没有任何结果行。'
              : isLive
                ? '任务仍在处理中（还未跑完），导出一份未完成的 CSV 会被误当成最终结果。'
                : '该任务没有可导出的结果行（空名单）。'
          "
          next-step="等任务状态变成「已完成」后，本区块自动出现导出按钮。"
        />

        <p v-if="lastExport && lastExport !== 'scroll'" class="receipt receipt--ok" data-testid="p8-export-receipt">
          已生成导出文件：<code>{{ lastExport }}</code>
        </p>

        <!-- 五 · 跨批次对比 -->
        <BatchCompare
          v-if="compareOpen"
          :current="activeJob"
          :previous="compareBase"
          :candidates="compareCandidates"
          @close="compareOpen = false"
          @pick="(id) => (compareBaseId = id)"
        />

        <!-- 六 · 任务列表 -->
        <BatchJobList :jobs="jobs" :active-id="activeJob.job_id" @select="(id) => (activeId = id)" />
      </template>

    </template>

    <!--
      读图问答放在 `<template v-else>` **外面**：接口不可用时，用户最需要看到的
      恰恰是「这一页能证明什么、不能证明什么」—— 把它和降级面板一起藏掉，
      只会留下一个「加载失败」和一句「下一步」，读者连这页本来算什么都不知道。
    -->
    <section class="page__block" aria-labelledby="p8-qa">
      <header class="page__block-head">
        <span class="page__code">Q</span>
        <h2 id="p8-qa" class="page__block-title">这一页能证明什么、不能证明什么</h2>
      </header>
      <div class="qa">
        <div v-for="n in notes" :key="n.q" class="qa__item" :data-level="n.level">
          <p class="qa__q">{{ n.q }}</p>
          <p class="qa__a">{{ n.a }}</p>
        </div>
      </div>
    </section>

    <footer class="page__foot">
      <span>导出列：</span>
      <code v-for="c in exportColumns" :key="c" class="page__col">{{ c }}</code>
      <span class="page__sep">·</span>
      <RouterLink :to="{ name: 'enterprise' }" class="page__link">P7 单企业深度诊断</RouterLink>
      <span class="page__sep">·</span>
      <span>分数与 P7 同源，不另训模型。</span>
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
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
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

.page__lede {
  max-width: 980px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.section-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.section-badge-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-left: 2px;
}

/* 导入区抬头带一眼实时的任务号与进度：用户刚点完提交，视线还在上面 */
.section-badge-row__job {
  margin-left: auto;
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.section-badge-row__job code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 0 4px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}

.receipt {
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  font-size: 12.5px;
  color: var(--color-text);
}

.receipt code {
  font-family: var(--font-mono);
  font-size: 11.5px;
}

.receipt--dim {
  border-left-color: var(--color-border-strong);
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
}

.receipt--ok {
  border-left-color: var(--risk-excellent);
  background: var(--risk-excellent-bg);
  color: var(--risk-excellent);
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

.page__block-title {
  font-size: 16px;
  font-weight: 600;
}

.page__code {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 11px;
}

.qa {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--space-4);
}

.qa__item {
  padding-left: var(--space-3);
  border-left: 2px solid var(--color-border);
}

.qa__item[data-level='redline'] {
  border-left-color: var(--risk-high-risk);
}

.qa__item[data-level='verified'] {
  border-left-color: var(--risk-excellent);
}

.qa__q {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 2px;
}

.qa__a {
  font-size: 12.5px;
  line-height: 1.85;
  color: var(--color-text-muted);
}

.page__foot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__col {
  padding: 0 4px;
  border-radius: 2px;
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 10.5px;
}

.page__link {
  color: var(--color-accent);
}

.page__sep {
  color: var(--color-text-subtle);
}
</style>
