<script setup lang="ts">
/**
 * 进度与已处理条数（P8 区块二，FE-20）
 *
 * 这一块只有一句真话可说：**已处理 x / 共 y 行**。所以页面不显示百分比圆环、
 * 不显示「正在智能分析…」，也不估「预计还需 3 分钟」—— 那三个都是可以随便编的东西。
 *
 * 能显示的：
 *   · 进度条（按 rows_done / rows_total 算，宽度与服务端给的数严格一致）；
 *   · 任务状态徽标（queued / processing / done / failed，四值）；
 *   · 已处理部分的实时统计（成功 / 失败 / 均值），并明确标「非终局」。；
 *   · 失败原因的分组（字段错 vs 映射缺），可以点回原行。
 *
 * 处理中的任务**不出导出按钮**（`export_ready=false`）—— 导出一份跑了一半的 CSV，
 * 比不给导出更糟：用户会拿着它去开会。
 */
import { computed } from 'vue'

import BandBar from './BandBar.vue'
import { progressPct } from '@/utils/batch'
import type { BatchJob } from '@/types/contracts'

const props = defineProps<{ job: BatchJob }>()


const emit = defineEmits<{ openRow: [rowNo: number] }>()

const STATUS_META: Record<string, { label: string; tone: 'neutral' | 'run' | 'ok' | 'bad' }> = {
  queued: { label: '排队中', tone: 'neutral' },
  processing: { label: '处理中', tone: 'run' },
  done: { label: '已完成', tone: 'ok' },
  failed: { label: '失败', tone: 'bad' },
}

const meta = computed(() => STATUS_META[props.job.status] ?? { label: props.job.status, tone: 'neutral' as const })
const pct = computed(() => progressPct(props.job.summary.rows_done, props.job.rows_total))
const final = computed(() => props.job.summary.final === true)
</script>

<template>
  <section class="page__block" aria-labelledby="p8-progress" data-testid="p8-section-progress">
    <header class="page__block-head">
      <span class="page__code">P8-②</span>
      <h2 id="p8-progress" class="page__block-title">任务进度</h2>
      <span class="page__block-desc">GET /api/batch/{{ '{job_id}' }} · 轮询</span>
      <span class="status" :data-tone="meta.tone" data-testid="p8-job-status">{{ meta.label }}</span>
      <code class="page__jobid">{{ job.job_id }}</code>
    </header>

    <!-- 解析失败：整个任务没有行，直接给原因与下一步 -->
    <div v-if="job.status === 'failed' && job.error" class="fail" data-testid="p8-job-failed">
      <p class="fail__title">{{ job.error.message }}</p>
      <p class="fail__next">下一步：{{ job.error.next_step }}</p>
      <code class="fail__code">{{ job.error.code }}</code>
    </div>

    <template v-else>
      <div class="bar-row">
        <div
          class="bar"
          role="progressbar"
          :aria-valuenow="pct"
          aria-valuemin="0"
          aria-valuemax="100"
          data-testid="p8-progress-bar"
          :data-pct="pct"
        >
          <div class="bar__fill" :class="{ 'bar__fill--pulse': job.status === 'processing' }" :style="{ width: `${pct}%` }" />
        </div>
        <span class="bar-row__text" data-testid="p8-progress-text">
          已处理 <b>{{ job.summary.rows_done }}</b> / {{ job.rows_total }} 行
          <span class="bar-row__pct">{{ pct }}%</span>
        </span>
      </div>

      <p class="note" data-testid="p8-progress-note">{{ job.progress_note }}</p>
      <p v-if="job.live_note && !final" class="note note--live" data-testid="p8-live-note">
        {{ job.live_note }}
      </p>

      <!-- 已处理部分的统计：处理中时明确标「非终局」 -->
      <dl class="kpis" :class="{ 'kpis--provisional': !final }">
        <div class="kpi">
          <dt>成功出分</dt>
          <dd data-testid="p8-kpi-ok">
            {{ job.summary.rows_ok }}
            <span class="kpi__sub">/ {{ job.summary.rows_done }}</span>
          </dd>
        </div>
        <div class="kpi">
          <dt>被拦下</dt>
          <dd data-testid="p8-kpi-failed">{{ job.summary.rows_failed }}</dd>
        </div>
        <div class="kpi">
          <dt>成功率</dt>
          <dd data-testid="p8-kpi-rate">{{ (job.summary.success_rate * 100).toFixed(1) }}%</dd>
        </div>
        <div class="kpi">
          <dt>分数均值</dt>
          <dd>
            {{ job.summary.score_mean ?? '—' }}
            <span v-if="job.summary.score_min !== null" class="kpi__sub">
              {{ job.summary.score_min }} – {{ job.summary.score_max }}
            </span>
          </dd>
        </div>
        <div class="kpi">
          <dt>异常企业</dt>
          <dd>{{ job.summary.anomaly_count }}</dd>
        </div>
      </dl>

      <div v-if="job.summary.rows_done > 0" class="bands">
        <p class="bands__title">五档分布（仅统计已出分的行）</p>
        <BandBar :counts="job.summary.band_counts" compact k="progress" />
      </div>

      <!-- 失败原因：按错误码分组，点行号回到结果表 -->
      <div v-if="job.summary.error_breakdown.length" class="errors" data-testid="p8-error-breakdown">
        <p class="errors__title">被拦下的 {{ job.summary.rows_failed }} 行，按原因分组</p>
        <ul class="errors__list">
          <li v-for="g in job.summary.error_breakdown" :key="g.code" class="errors__item">
            <span class="errors__kind" :data-kind="g.kind">{{ g.kind === 'field' ? '数据错' : '映射缺' }}</span>
            <span class="errors__label">{{ g.label }}</span>
            <span class="errors__field">字段：{{ g.field }}</span>
            <span class="errors__rows">
              {{ g.rows }} 行 ·
              <button
                v-for="n in g.row_nos.slice(0, 6)"
                :key="n"
                type="button"
                class="errors__row"
                @click="emit('openRow', n)"
              >
                #{{ n }}
              </button>
              <span v-if="g.row_nos.length > 6"> …</span>
            </span>
          </li>
        </ul>
        <p class="errors__hint">
          「数据错」改文件重传即可；「映射缺」是名单企业在面板里没有 userid（P0-1 映射键），改文件没用，得等映射表补齐。
        </p>
      </div>
    </template>
  </section>
</template>

<style scoped>
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

.page__block-desc {
  font-size: 11.5px;
  color: var(--color-text-subtle);
}

.page__code {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 11px;
}

.page__jobid {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-subtle);
}

.status {
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11.5px;
  border: 1px solid var(--color-border-strong);
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.status[data-tone='run'] {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-soft);
}

.status[data-tone='ok'] {
  border-color: var(--risk-excellent);
  color: var(--risk-excellent);
  background: var(--risk-excellent-bg);
}

.status[data-tone='bad'] {
  border-color: var(--risk-high-risk);
  color: var(--risk-high-risk);
  background: var(--risk-high-risk-bg);
}

.bar-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.bar {
  flex: 1;
  height: 10px;
  border-radius: 5px;
  background: var(--color-surface-muted);
  overflow: hidden;
}

.bar__fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.28s ease;
}

.bar__fill--pulse {
  background-image: linear-gradient(
    100deg,
    transparent 0 35%,
    color-mix(in srgb, #fff 34%, transparent) 50%,
    transparent 65% 100%
  );
  background-size: 220% 100%;
  animation: sweep 1.5s linear infinite;
}

@keyframes sweep {
  from {
    background-position: 120% 0;
  }
  to {
    background-position: -120% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .bar__fill--pulse {
    animation: none;
  }
}

.bar-row__text {
  font-size: 12.5px;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.bar-row__text b {
  color: var(--color-text);
  font-family: var(--font-mono);
}

.bar-row__pct {
  margin-left: 6px;
  font-family: var(--font-mono);
  color: var(--color-accent);
}

.note {
  font-size: 12px;
  color: var(--color-text-muted);
}

.note--live {
  color: var(--risk-watch);
}

.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.kpis--provisional {
  opacity: 0.86;
}

.kpi {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
}

.kpi dt {
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.kpi dd {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--color-text);
}

.kpi__sub {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-subtle);
}

.bands {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bands__title {
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.errors {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border-watch, var(--color-border));
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
}

.errors__title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--risk-watch);
}

.errors__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.errors__item {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: 12px;
}

.errors__kind {
  padding: 0 6px;
  border-radius: 9px;
  font-size: 10.5px;
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  color: var(--color-text-muted);
}

.errors__kind[data-kind='mapping'] {
  border-color: var(--risk-warning);
  color: var(--risk-warning);
}

.errors__label {
  font-weight: 600;
  color: var(--color-text);
}

.errors__field,
.errors__rows {
  color: var(--color-text-muted);
}

.errors__row {
  min-height: 0;
  margin-right: 2px;
  padding: 0 4px;
  border: none;
  border-radius: 3px;
  background: var(--color-surface);
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
}

.errors__hint {
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.fail {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--risk-high-risk);
  border-radius: var(--radius-sm);
  background: var(--risk-high-risk-bg);
}

.fail__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--risk-high-risk);
}

.fail__next {
  font-size: 12px;
  color: var(--color-text-muted);
}

.fail__code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-subtle);
}
</style>
