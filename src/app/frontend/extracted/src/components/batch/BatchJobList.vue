<script setup lang="ts">
/**
 * 任务列表（P8 区块六，FE-20）
 *
 * 六个演示任务覆盖页面上会遇到的每一种状态，一个都不能少 ——
 * 因为「降级不白屏」这条验收，只有真把空名单、解析失败、处理中三种态画出来才算数：
 *
 *   processing  → 进度在动、已处理行可先看、导出按钮不出现
 *   queued      → 进度为 0、统计为 null（不是 0）、说明「前面还有任务」
 *   done        → 完整结果 + 导出
 *   done（历史） → as_of 不同，切过去会先弹跨批次提示
 *   done（空）   → 只有表头，明确说「没有数据行」而不是显示 0 家已评估
 *   failed      → 解析失败，给原因与「另存为 xlsx」的下一步
 */
import { computed } from 'vue'

import type { BatchJob } from '@/types/contracts'

const props = defineProps<{ jobs: BatchJob[]; activeId: string | null }>()
const emit = defineEmits<{ select: [jobId: string] }>()

const STATUS: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'neutral' },
  processing: { label: '处理中', tone: 'run' },
  done: { label: '已完成', tone: 'ok' },
  failed: { label: '失败', tone: 'bad' },
}

const ordered = computed(() =>
  [...props.jobs].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
)

function shortTime(iso: string) {
  return iso.slice(5, 16).replace('T', ' ')
}
</script>

<template>
  <section class="page__block" aria-labelledby="p8-jobs" data-testid="p8-section-jobs">
    <header class="page__block-head">
      <span class="page__code">P8-⑥</span>
      <h2 id="p8-jobs" class="page__block-title">任务列表</h2>
      <span class="page__block-desc">{{ jobs.length }} 个任务 · 点一行切换</span>
    </header>

    <ul class="jobs">
      <li v-for="j in ordered" :key="j.job_id">
        <button
          type="button"
          class="job"
          :class="{ 'job--on': j.job_id === activeId }"
          :data-job="j.job_id"
          :data-status="j.status"
          @click="emit('select', j.job_id)"
        >
          <span class="job__top">
            <span class="job__status" :data-tone="STATUS[j.status]?.tone">{{ STATUS[j.status]?.label ?? j.status }}</span>
            <span class="job__id">{{ j.job_id }}</span>
            <span class="job__time">{{ shortTime(j.created_at) }}</span>
          </span>
          <span class="job__file">{{ j.filename }}</span>
          <span class="job__meta">
            <template v-if="j.rows_total === 0 && j.status === 'failed'">解析失败，未进入打分</template>
            <template v-else-if="j.rows_total === 0">0 行（文件只有表头）</template>
            <template v-else>
              {{ j.summary.rows_ok }} / {{ j.rows_total }} 行出分 · 均值 {{ j.summary.score_mean ?? '—' }}
            </template>
            <span class="job__asof">快照 {{ j.as_of }}</span>
          </span>
          <span class="job__note">{{ j.progress_note }}</span>
        </button>
      </li>
    </ul>
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

.jobs {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.job {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  height: 100%;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  text-align: left;
  cursor: pointer;
}

.job:hover {
  border-color: var(--color-accent);
}

.job:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.job--on {
  border-color: var(--color-accent);
  box-shadow: inset 3px 0 0 var(--color-accent);
  background: var(--color-accent-soft);
}

.job__top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 11px;
}

.job__status {
  padding: 0 6px;
  border-radius: 9px;
  border: 1px solid var(--color-border-strong);
  color: var(--color-text-muted);
}

.job__status[data-tone='run'] {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.job__status[data-tone='ok'] {
  border-color: var(--risk-excellent);
  color: var(--risk-excellent);
}

.job__status[data-tone='bad'] {
  border-color: var(--risk-high-risk);
  color: var(--risk-high-risk);
}

.job__id,
.job__time {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--color-text-subtle);
}

.job__time {
  margin-left: auto;
}

.job__file {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
}

.job__meta {
  display: flex;
  gap: var(--space-2);
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.job__asof {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--color-text-subtle);
}

.job__note {
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-subtle);
}
</style>
