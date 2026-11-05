<script setup lang="ts">
/**
 * P1 概览
 *
 * 数据来源：GET /api/overview/summary（outputs/contracts/overview.json）
 *   source = aggregated —— 本页是 P2/P4/P5 已交付契约的**聚合视图**，不引入新数据。
 *
 * 这页要回答的问题是「现在到哪一步了、还差什么」，所以顺序是：
 *   结论 → 四个数 → 方法链路 → 数据缺口 → 交付进度
 * 而不是先摆一堆漂亮图表再让人自己总结。
 *
 * 一个刻意的取舍：**不在这里重复画 P2/P4/P5 的图**。
 *   AUC 柱状图在 P4、SHAP 在 P5、箱线图在 P2，全部搬过来只会让概览变成缩略图集，
 *   且一旦两页数据不同步就自相矛盾。这里只放数字和链接，图留在它自己的页面上。
 */
import { computed } from 'vue'

import PageSkeleton from '@/components/PageSkeleton.vue'
import RedLineBanner from '@/components/RedLineBanner.vue'
import StatePanel from '@/components/StatePanel.vue'
import { fetchOverviewSummary } from '@/api'
import { useAsyncSection } from '@/composables/useAsyncSection'
import type { ContractMeta, DatasetKind } from '@/types'

/* ------------------------------ 契约类型（本页专用） ------------------------------ */

interface OverviewKpi {
  id: string
  label: string
  value: number
  decimals: number
  unit: string
  tone: 'accent' | 'risk' | 'warn' | 'neutral'
  hint: string
  detail: string
  source_page: string
}

interface OverviewStep {
  order: number
  name: string
  impl: string
  detail: string
  status: string
}

interface OverviewGap {
  id: string
  severity: 'blocked' | 'pending' | 'partial'
  title: string
  impact: string
  consequence: string
  affects: string[]
  evidence: string
}

interface OverviewStage {
  code: string
  label: string
  path: string
  state: 'delivered' | 'pending'
  desc: string
}

interface OverviewSummary {
  headline: { text: string; subtext: string }
  kpis: OverviewKpi[]
  method_chain: { title: string; note: string; steps: OverviewStep[] }
  gaps: { title: string; note: string; items: OverviewGap[] }
  stage_progress: { title: string; note: string; items: OverviewStage[]; summary: { delivered: number; total: number } }
}

/* ---------------------------------- 状态机 ---------------------------------- */

const section = useAsyncSection(fetchOverviewSummary)

const payload = computed<OverviewSummary | null>(() => {
  const env = section.data.value as { data?: OverviewSummary } | null
  const raw = env?.data
  // 形状守卫：一个坏 payload 只让本块进空态，不把整页顶掉
  return raw && Array.isArray(raw.kpis) && Array.isArray(raw.gaps?.items) ? raw : null
})
const meta = computed<ContractMeta>(() => {
  const env = section.data.value as { meta?: ContractMeta } | null
  return env?.meta ?? {}
})
const dataset = computed<DatasetKind>(() => (meta.value.dataset as DatasetKind) ?? 'real_panel')
const status = computed(() => section.status.value)

/* --------------------------------- 展示辅助 --------------------------------- */

const fmtKpi = (k: OverviewKpi) => {
  if (k.unit === '%') return (k.value * 100).toFixed(k.decimals)
  return k.value.toFixed(k.decimals)
}

/** 状态 → 中文标签与色调，避免模板里写一堆三元表达式 */
const STEP_STATE: Record<string, { label: string; tone: string }> = {
  ready: { label: '已就绪', tone: 'ok' },
  partial: { label: '部分就绪', tone: 'warn' },
  pending: { label: '未接入', tone: 'idle' },
  blocked: { label: '被阻断', tone: 'risk' },
  synthetic_only: { label: '仅合成', tone: 'warn' },
}

const stepState = (s: string) => STEP_STATE[s] ?? { label: s, tone: 'idle' }

const SEVERITY: Record<string, { label: string; tone: string }> = {
  blocked: { label: '阻断', tone: 'risk' },
  pending: { label: '未接入', tone: 'warn' },
  partial: { label: '部分', tone: 'warn' },
}

const severity = (s: string) => SEVERITY[s] ?? { label: s, tone: 'idle' }

/** 链路里「已就绪」的步数，用来给一句进度判断 */
const readySteps = computed(
  () => payload.value?.method_chain.steps.filter((s) => s.status === 'ready').length ?? 0,
)
const totalSteps = computed(() => payload.value?.method_chain.steps.length ?? 0)
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div class="page__title-row cluster">
        <h1 class="page__title">P1 概览</h1>
        <span class="page__sub">1 个接口 · GET /api/overview/summary</span>
        <span class="page__meta">
          数据来源 <code>{{ dataset }}</code>
          <span class="page__badge" data-aggregated="true">聚合视图</span>
        </span>
      </div>
      <p class="page__lede">
        全站的一句话状态：哪些结论已经成立、哪些还缺数据、下一步该补哪一块。
        本页不重复画 P2 / P4 / P5 的图——图留在各自页面上，这里只给数字和入口。
      </p>
    </header>

    <RedLineBanner scope="P1 概览" />

    <PageSkeleton v-if="status === 'loading'" :kpi-count="4" :charts="[]" />
    <StatePanel
      v-else-if="status === 'error'"
      variant="error"
      title="概览数据未能加载"
      :reason="section.error.value?.reason ?? ''"
      :next-step="section.error.value?.nextStep ?? ''"
      :retry="() => void section.retry()"
    />
    <template v-else-if="payload">
      <!-- 结论先行 -->
      <section class="page__verdict" aria-labelledby="verdict">
        <h2 id="verdict" class="page__verdict-title">一句话结论</h2>
        <p class="page__verdict-text">{{ payload.headline.text }}</p>
        <p class="page__verdict-sub">{{ payload.headline.subtext }}</p>
      </section>

      <!-- 四个数 -->
      <div class="grid-12">
        <div v-for="k in payload.kpis" :key="k.id" class="col-3 card kpi" :data-tone="k.tone">
          <span class="card__label">{{ k.label }}</span>
          <strong class="card__value">
            {{ fmtKpi(k) }}<span v-if="k.unit" class="kpi__unit">{{ k.unit }}</span>
          </strong>
          <span class="card__hint">{{ k.hint }}</span>
          <p class="kpi__detail">{{ k.detail }}</p>
          <span class="kpi__source">{{ k.source_page }}</span>
        </div>
      </div>

      <!-- 方法链路 -->
      <section class="page__block" aria-labelledby="chain">
        <header class="page__block-head">
          <span class="page__code">T1</span>
          <h2 id="chain" class="page__block-title">{{ payload.method_chain.title }}</h2>
          <p class="page__block-desc">
            {{ readySteps }} / {{ totalSteps }} 步已就绪
          </p>
        </header>

        <ol class="chain">
          <li v-for="s in payload.method_chain.steps" :key="s.order" class="chain__item" :data-tone="stepState(s.status).tone">
            <span class="chain__order">{{ s.order }}</span>
            <div class="chain__body">
              <div class="chain__head">
                <strong class="chain__name">{{ s.name }}</strong>
                <span class="chain__state">{{ stepState(s.status).label }}</span>
              </div>
              <p class="chain__detail">{{ s.detail }}</p>
              <code class="chain__impl">{{ s.impl }}</code>
            </div>
          </li>
        </ol>
        <p class="page__note">{{ payload.method_chain.note }}</p>
      </section>

      <!-- 数据缺口 -->
      <section class="page__block" aria-labelledby="gaps">
        <header class="page__block-head">
          <span class="page__code">T2</span>
          <h2 id="gaps" class="page__block-title">{{ payload.gaps.title }}</h2>
          <p class="page__block-desc">{{ payload.gaps.items.length }} 条</p>
        </header>

        <div class="gaps">
          <article v-for="g in payload.gaps.items" :key="g.id" class="gap" :data-tone="severity(g.severity).tone">
            <header class="gap__head">
              <span class="gap__id">{{ g.id }}</span>
              <h3 class="gap__title">{{ g.title }}</h3>
              <span class="gap__sev">{{ severity(g.severity).label }}</span>
            </header>
            <dl class="gap__dl">
              <dt>影响</dt>
              <dd>{{ g.impact }}</dd>
              <dt>后果</dt>
              <dd>{{ g.consequence }}</dd>
              <dt>依据</dt>
              <dd class="gap__evidence">{{ g.evidence }}</dd>
            </dl>
            <footer class="gap__foot">
              <span class="gap__foot-label">波及</span>
              <span v-for="a in g.affects" :key="a" class="gap__chip">{{ a }}</span>
            </footer>
          </article>
        </div>
        <p class="page__note">{{ payload.gaps.note }}</p>
      </section>

      <!-- 交付进度 -->
      <section class="page__block" aria-labelledby="progress">
        <header class="page__block-head">
          <span class="page__code">T3</span>
          <h2 id="progress" class="page__block-title">{{ payload.stage_progress.title }}</h2>
          <p class="page__block-desc">
            已交付 {{ payload.stage_progress.summary.delivered }} / {{ payload.stage_progress.summary.total }}
          </p>
        </header>

        <ul class="stages">
          <li
            v-for="p in payload.stage_progress.items"
            :key="p.code"
            class="stage"
            :data-state="p.state"
          >
            <RouterLink v-if="p.state === 'delivered'" :to="p.path" class="stage__link">
              <span class="stage__code">{{ p.code }}</span>
              <span class="stage__label">{{ p.label }}</span>
              <span class="stage__desc">{{ p.desc }}</span>
            </RouterLink>
            <div v-else class="stage__static">
              <span class="stage__code">{{ p.code }}</span>
              <span class="stage__label">{{ p.label }}</span>
              <span class="stage__desc">{{ p.desc }}</span>
            </div>
          </li>
        </ul>
        <p class="page__note">{{ payload.stage_progress.note }}</p>
      </section>
    </template>

    <footer class="page__foot cluster">
      <span>下一步：</span>
      <RouterLink :to="{ name: 'data' }" class="page__link">P2 数据与特征</RouterLink>
      <span class="page__foot-sep">·</span>
      <RouterLink :to="{ name: 'model' }" class="page__link">P4 模型与对比</RouterLink>
      <span class="page__foot-sep">·</span>
      <RouterLink :to="{ name: 'explain' }" class="page__link">P5 可解释性</RouterLink>
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
  align-items: baseline;
}

.page__title {
  font-size: 24px;
  font-weight: 700;
}

.page__sub,
.page__meta {
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__badge {
  margin-left: 6px;
  padding: 1px 6px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  font-size: 11px;
}

.page__lede {
  max-width: 76ch;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.page__verdict {
  padding: var(--space-4) var(--space-5);
  border-left: 3px solid var(--color-accent);
  border-radius: var(--radius-md);
  background: var(--color-accent-soft);
}

.page__verdict-title {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--color-accent);
}

.page__verdict-text {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  line-height: 1.6;
}

.page__verdict-sub {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

/* ---------------------------------- KPI ---------------------------------- */

.kpi {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kpi .card__value {
  font-variant-numeric: tabular-nums;
}

.kpi__unit {
  margin-left: 2px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-muted);
}

.kpi__detail {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-muted);
}

.kpi__source {
  margin-top: auto;
  padding-top: 6px;
  font-size: 11px;
  color: var(--color-accent);
}

.kpi[data-tone='risk'] .card__value {
  color: var(--risk-high-risk);
}

.kpi[data-tone='warn'] .card__value {
  color: #b26a00;
}

/* --------------------------------- 方法链路 --------------------------------- */

@media (max-width: 1100px) {
  .chain {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .chain {
    grid-template-columns: minmax(0, 1fr);
  }
}

.chain {
  display: grid;
  /* 六步刻意排成 3×2，而不是 auto-fit 挤成 5+1：
     链路是有顺序的，5+1 的断行会把「多源融合」孤立到第二行，
     读起来像两段不同的流程。所以这里写死 3 列（窄屏由媒体查询降到 2 / 1 列）。 */
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: none;
}

.chain__item {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.chain__item[data-tone='ok'] {
  border-left-color: #2f7a4d;
}

.chain__item[data-tone='warn'] {
  border-left-color: #b26a00;
}

.chain__item[data-tone='risk'] {
  border-left-color: var(--risk-high-risk);
}

.chain__item[data-tone='idle'] {
  border-left-color: var(--color-border-strong);
  background: var(--color-surface-muted);
}

.chain__order {
  flex: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 20px;
  text-align: center;
  color: var(--color-text-muted);
}

.chain__body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.chain__head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.chain__name {
  font-size: 13px;
}

.chain__state {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-size: 10px;
  color: var(--color-text-muted);
}

.chain__detail {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.chain__impl {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-text-muted);
  word-break: break-all;
}

/* --------------------------------- 数据缺口 --------------------------------- */

.gaps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
}

.gap {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-top: 3px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.gap[data-tone='risk'] {
  border-top-color: var(--risk-high-risk);
}

.gap[data-tone='warn'] {
  border-top-color: #b26a00;
}

.gap__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.gap__id {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-secondary);
}

.gap__title {
  flex: 1 1 auto;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.gap__sev {
  padding: 0 6px;
  border: 1px solid currentColor;
  border-radius: 8px;
  font-size: 10px;
  color: var(--color-text-muted);
}

.gap[data-tone='risk'] .gap__sev {
  color: var(--risk-high-risk);
}

.gap[data-tone='warn'] .gap__sev {
  color: #b26a00;
}

.gap__dl {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 4px var(--space-2);
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}

.gap__dl dt {
  color: var(--color-text-muted);
  font-size: 11px;
}

.gap__dl dd {
  margin: 0;
  color: var(--color-text-secondary);
}

.gap__evidence {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.gap__foot {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: auto;
  padding-top: 6px;
  border-top: 1px dashed var(--color-border);
}

.gap__foot-label {
  font-size: 11px;
  color: var(--color-text-muted);
}

.gap__chip {
  padding: 1px 6px;
  border-radius: 8px;
  background: var(--color-surface-muted);
  font-size: 11px;
  color: var(--color-text-secondary);
}

/* --------------------------------- 交付进度 --------------------------------- */

.stages {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.stage__link,
.stage__static {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-decoration: none;
  color: inherit;
}

.stage[data-state='delivered'] .stage__link {
  border-color: #2f7a4d;
}

.stage[data-state='delivered'] .stage__link:hover,
.stage[data-state='delivered'] .stage__link:focus-visible {
  background: var(--color-accent-soft);
}

.stage[data-state='pending'] .stage__static {
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
}

.stage__code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.stage__label {
  font-size: 13px;
  font-weight: 600;
}

.stage__desc {
  font-size: 11px;
  color: var(--color-text-muted);
}

/* ---------------------------------- 通用 ---------------------------------- */

.page__block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.page__block-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.page__code {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.page__block-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.page__block-desc {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__note {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid var(--color-border-strong);
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-secondary);
}

.page__foot {
  align-items: baseline;
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
}

.page__foot-sep {
  color: var(--color-border-strong);
}

.page__link {
  color: var(--color-accent);
  text-decoration: none;
}

.page__link:hover,
.page__link:focus-visible {
  text-decoration: underline;
}
</style>
