<script setup lang="ts">
/**
 * 导出（P8 区块四，FE-20）
 *
 * 导出看起来是最简单的一步（`a[download]` 而已），但它是**客户唯一会带走的东西**：
 * 当天演示结束，评审电脑上剩下的就是这份 CSV。所以这里写清三件事：
 *
 *   ① **列是什么**：Q3 模板 6 列（原样保留，可回填系统）+ 评估结果 5 列。列顺序逐字列在页面上，
 *      不让用户下载完才发现多了/少了一列；
 *   ② **失败行怎么处理**：分数列留空，状态列写明原因 —— **不填 0**（填 0 会被下游当成"零分企业"）；
 *   ③ **文件名**：带评估快照日与行数（`…_2026-10-28_91行.csv`），避免下载目录里五个「导出.csv」。
 *
 * 未完成的任务不显示导出按钮（由父组件控制 `ready`），因为导出跑了一半的 CSV 比不给更糟。
 */
import { computed } from 'vue'

import { buildExportCsv, exportFilename, EXPORT_COLUMNS, STATUS_LABEL } from '@/utils/batch'
import type { BatchJob } from '@/types/contracts'

const props = defineProps<{ job: BatchJob; rows: number }>()

const emit = defineEmits<{ done: [filename: string] }>()

const filename = computed(() => exportFilename(props.job, props.rows))

const failedCount = computed(() => props.job.evidence.filter((r) => r.status !== 'ok').length)

const statusLegend = computed(() => {
  const seen = new Set(props.job.evidence.map((r) => r.status))
  return [...seen].map((s) => STATUS_LABEL[s] ?? s)
})

function download() {
  // 用页面同一份行数据与同一套 buildExportCsv 生成 —— 导出的内容与屏幕上看到的一字不差
  const csv = buildExportCsv(
    props.job.evidence.map((r) => ({
      row_no: r.row_no,
      name: r.name,
      cert_no: r.cert_no,
      source_year: r.source_year,
      batch: r.batch,
      issue_date: r.issue_date,
      credit_score: r.credit_score,
      risk_band_label: r.risk_band_label,
      top_factor: r.top_factor,
      anomaly: r.anomaly,
      status: r.status,
    })),
  )
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.value
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 立刻 revoke 会让部分浏览器来不及下载，延迟一拍
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  emit('done', filename.value)
}
</script>

<template>
  <section class="page__block" aria-labelledby="p8-export" data-testid="p8-section-export">
    <header class="page__block-head">
      <span class="page__code">P8-④</span>
      <h2 id="p8-export" class="page__block-title">导出</h2>
      <span class="page__block-desc">GET /api/batch/{{ '{job_id}' }}/export · CSV（UTF-8 BOM）</span>
    </header>

    <div class="grid">
      <div class="col">
        <p class="col__title">导出列（{{ EXPORT_COLUMNS.length }} 列，顺序固定）</p>
        <ol class="cols">
          <li v-for="(c, i) in EXPORT_COLUMNS" :key="c" :class="{ 'cols__add': i >= 6 }">
            {{ c }}
            <span v-if="i < 6" class="cols__tag">Q3 模板</span>
            <span v-else class="cols__tag cols__tag--new">评估结果</span>
          </li>
        </ol>
      </div>

      <div class="col">
        <p class="col__title">这次导出包含什么</p>
        <dl class="facts">
          <div><dt>行数</dt><dd>{{ rows }} 行（名单全部 {{ job.rows_total }} 行）</dd></div>
          <div><dt>未出分</dt><dd>{{ failedCount }} 行，分数列留空，状态列写明原因</dd></div>
          <div><dt>状态取值</dt><dd>{{ statusLegend.join(' / ') || '—' }}</dd></div>
          <div><dt>文件名</dt><dd class="facts__file">{{ filename }}</dd></div>
          <div><dt>编码</dt><dd>UTF-8 带 BOM（Excel 双击不乱码）</dd></div>
        </dl>

        <div class="act">
          <button type="button" class="btn btn--primary" data-testid="p8-download" @click="download()">
            下载 CSV
          </button>
          <a class="act__demo" href="/fixtures/batch_export_100.csv" download data-testid="p8-export-sample">
            或先看一份示例导出
          </a>
        </div>
      </div>
    </div>

    <p class="foot-note">
      为什么保留原 6 列而不是只导分数：客户拿到文件还要回填自己的系统，列名一改就得手工对表；
      为什么追加而不是替换：<b>「哪一行没出分」必须一眼看见</b>，否则这份 CSV 会被当成「100 家都评过了」。
    </p>
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

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr);
  gap: var(--space-4);
}

@media (max-width: 1023px) {
  .grid {
    grid-template-columns: 1fr;
  }
}

.col {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.col__title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
}

.cols {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px var(--space-3);
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.cols__tag {
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 8px;
  font-size: 10px;
  background: var(--color-surface-muted);
  color: var(--color-text-subtle);
}

.cols__tag--new {
  background: var(--color-accent-soft);
  color: var(--color-accent);
}

.facts {
  display: grid;
  gap: 3px;
  margin: 0;
  font-size: 12px;
}

.facts > div {
  display: flex;
  gap: var(--space-2);
}

.facts dt {
  flex: 0 0 64px;
  color: var(--color-text-muted);
}

.facts dd {
  margin: 0;
  color: var(--color-text);
}

.facts__file {
  font-family: var(--font-mono);
  font-size: 11px;
  word-break: break-all;
}

.act {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding-top: var(--space-2);
}

.act__demo {
  font-size: 12px;
  color: var(--color-accent);
}

.foot-note {
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border);
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-subtle);
}
</style>
