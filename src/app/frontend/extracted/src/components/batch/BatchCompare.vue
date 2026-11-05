<script setup lang="ts">
/**
 * 跨批次对比（P8 补充区块，FE-20）
 *
 * 为什么批量页必须有这一块：真实使用节奏是**每季度跑一次同一批企业**，
 * 第二次上传之后，所有人问的第一个问题都是「谁变差了」。如果页面不给这个功能，
 * 用户就会自己把两份 CSV 拖进 Excel 做减法 —— 那时没人会记得两个批次用了不同版本的模型。
 *
 * 所以这里的第一件事不是画图，是**拦一次**：
 *   `diffJobs()` 第一返回是 `comparable`，两批次 `as_of` 不同就为 false，
 *   页面顶部先把这件事说清楚（差 12.5 分来自模型快照，不是企业经营改善），
 *   用户坚持要看时必须点「我已知晓，仍要对比」，之后每条结论都带「不可比」标。
 *
 * 第二件事：对比结果里 `band_changed` 的行单列——档位跳一级比分数涨 3 分重要得多，
 * 因为档位直接对应「正常类 / 关注类 / 次级类」这套监管口径。
 */
import { computed, ref } from 'vue'

import { diffJobs, bandLabel, bandColor } from '@/utils/batch'
import type { BatchJob } from '@/types/contracts'

const props = defineProps<{
  current: BatchJob
  previous: BatchJob | null
  candidates: BatchJob[]
}>()

const emit = defineEmits<{ close: []; pick: [jobId: string] }>()

const override = ref(false)

const prev = computed(() => props.previous)

const diff = computed(() => {
  if (!prev.value) return null
  return diffJobs(
    { as_of: prev.value.as_of, evidence: prev.value.evidence },
    { as_of: props.current.as_of, evidence: props.current.evidence },
  )
})

/** 只看档位跳变的行（默认），可切回全部行 */
const onlyMoved = ref(true)

const shown = computed(() => {
  const rows = diff.value?.rows ?? []
  return onlyMoved.value ? rows.filter((r) => r.band_changed) : rows
})
</script>

<template>
  <section class="page__block" aria-labelledby="p8-compare" data-testid="p8-section-compare">
    <header class="page__block-head">
      <span class="page__code">P8-⑤</span>
      <h2 id="p8-compare" class="page__block-title">跨批次对比</h2>
      <span class="page__block-desc">同一份名单、两个评估快照</span>
      <button type="button" class="btn btn--sm" data-testid="p8-compare-close" @click="emit('close')">收起</button>
    </header>

    <!-- 选一个历史批次 -->
    <div v-if="!prev" class="pick" data-testid="p8-compare-pick">
      <p>选择一个历史批次与当前任务（{{ current.as_of }}）对比：</p>
      <ul class="pick__list">
        <li v-for="j in candidates" :key="j.job_id">
          <button type="button" class="btn btn--sm" @click="emit('pick', j.job_id)">
            {{ j.as_of }} · {{ j.job_id }} · {{ j.summary.rows_ok }} 行出分
          </button>
        </li>
        <li v-if="!candidates.length" class="pick__none">任务列表里没有可对比的历史批次</li>
      </ul>
    </div>

    <template v-else-if="diff">
      <!-- 不可比：先把话说死 -->
      <div v-if="!diff.comparable" class="block" data-testid="p8-compare-warning">
        <p class="block__title">{{ diff.reason }}</p>
        <p class="block__body">
          两次之间模型快照升级过一版，同一家企业分数整体变化
          <b>{{ diff.mean_delta !== null ? (diff.mean_delta > 0 ? '+' : '') + diff.mean_delta : '—' }}</b>
          分，其中 {{ diff.band_moves }} 家企业的风险分级发生了跳变。
          这组数字说明的是<b>模型版本差异</b>，不是企业经营状况变化，不得作为贷后监控依据。
        </p>
        <button
          v-if="!override"
          type="button"
          class="btn btn--sm"
          data-testid="p8-compare-override"
          @click="override = true"
        >
          我已知晓，仍要查看逐行差异
        </button>
      </div>

      <div v-if="diff.comparable || override" class="result" data-testid="p8-compare-result">
        <div class="head">
          <span class="head__pair">
            <b>{{ prev.as_of }}</b>
            <span class="head__arrow">→</span>
            <b>{{ current.as_of }}</b>
          </span>
          <span class="head__stat">逐行差异 {{ diff.rows.length }} 行 · 档位跳变 {{ diff.band_moves }} 家</span>
          <label class="head__toggle">
            <input v-model="onlyMoved" type="checkbox" />
            只看档位跳变
          </label>
        </div>

        <div class="table-wrap">
        <table class="table" data-testid="p8-compare-table">
          <thead>
            <tr>
              <th>行号</th>
              <th>企业名称</th>
              <th class="num">前批次</th>
              <th class="num">本批次</th>
              <th class="num">差值</th>
              <th>档位变化</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in shown.slice(0, 30)" :key="r.cert_no">
              <td class="mono">{{ r.row_no }}</td>
              <td>{{ r.name }}</td>
              <td class="num mono">{{ r.from_score ?? '—' }}</td>
              <td class="num mono">{{ r.to_score ?? '—' }}</td>
              <td class="num mono">{{ r.delta === null ? '—' : (r.delta > 0 ? '+' : '') + r.delta }}</td>
              <td>
                <template v-if="r.band_changed">
                  <span class="chip" :style="{ '--c': bandColor(r.from_band ?? '') }">{{ bandLabel(r.from_band ?? '') }}</span>
                  <span class="arrow">→</span>
                  <span class="chip" :style="{ '--c': bandColor(r.to_band ?? '') }">{{ bandLabel(r.to_band ?? '') }}</span>
                </template>
                <span v-else class="dim">不变</span>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
        <p v-if="shown.length > 30" class="more">共 {{ shown.length }} 行，此处只展示前 30 行</p>
        <p v-if="!shown.length" class="more">没有档位跳变的行（分数有变化但未跨档）</p>
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

.page__block-head .btn {
  margin-left: auto;
}

.pick {
  font-size: 12.5px;
  color: var(--color-text-muted);
}

.pick__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
}

.pick__none {
  color: var(--color-text-subtle);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: flex-start;
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--risk-warning);
  border-radius: var(--radius-sm);
  background: var(--risk-warning-bg);
}

.block__title {
  font-size: 13px;
  font-weight: 700;
  color: var(--risk-warning);
}

.block__body {
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--color-text);
}

.result {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-3);
  font-size: 12.5px;
  color: var(--color-text-muted);
}

.head__pair {
  font-family: var(--font-mono);
  color: var(--color-text);
}

.head__arrow {
  margin: 0 6px;
  color: var(--color-text-subtle);
}

.head__toggle {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 5px;
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.table th,
.table td {
  padding: 6px var(--space-2);
  border-bottom: 1px solid var(--color-border);
  text-align: left;
}

.table th {
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-weight: 600;
}

.num {
  text-align: right;
}

.mono {
  font-family: var(--font-mono);
  font-size: 11.5px;
}

.chip {
  padding: 0 7px;
  border-radius: 9px;
  border: 1px solid var(--c);
  color: var(--c);
  font-size: 11px;
}

.arrow {
  margin: 0 4px;
  color: var(--color-text-subtle);
}

.dim {
  color: var(--color-text-subtle);
}

.more {
  font-size: 11.5px;
  color: var(--color-text-subtle);
}
</style>
