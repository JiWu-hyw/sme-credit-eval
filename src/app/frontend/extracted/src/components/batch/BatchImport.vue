<script setup lang="ts">
/**
 * 名单导入（P8 区块一，FE-20）
 *
 * 「导入」这一步决定了后面所有数字可不可信，所以这里做四件事，缺一件都不算完成：
 *
 *   ① **模板不是一张图，是能下载的**：`public/fixtures/batch_demo_100.csv` 是真实可解析的
 *      100 行演示名单（含 6 行故意做坏的），下载下来直接能提交；另给「空白模板」只含表头。
 *   ② **文件级预校验在提交前跑**：类型 / 大小 / 必需 6 列 / 证书编号正则 / 序号连续，
 *      每条都给结论与原因（不是一句「文件不合法」）。
 *   ③ **预览到行**：CSV 直接在前端解析，展示前 8 行 + 每行字段级问题；
 *      xlsx 是二进制，前端不解（真解析要 3 个依赖），明确写「由后端解析」而不是装作能看到。
 *   ④ **两种提交入口**：拖拽/选择文件，或「用内置演示名单」—— 后者不是作弊，
 *      是让评审在没有名单文件时也能把流程走完，且提交的是**同一份真实可解析的 CSV**。
 */
import { computed, ref } from 'vue'

import { validateFile, validateRows, parseRosterCsv, TEMPLATE_COLUMNS, SIZE_LIMIT_MB } from '@/utils/batch'
import type { FileCheck } from '@/utils/batch'
import type { SheetRow } from '@/api/mock/batch'

const emit = defineEmits<{ submit: [payload: { name: string; size: number; sheets: SheetRow[] }] }>()

/** 父组件提交中：按钮置灰并显示「正在提交…」，避免连点出两个任务 */
defineProps<{ busy?: boolean }>()

const DEMO_URL = `${import.meta.env?.BASE_URL ?? '/'}fixtures/batch_demo_100.csv`

interface Loaded {
  name: string
  size: number
  text: string | null
  check: FileCheck
  sheets: SheetRow[]
  issues: string[]
}

const loaded = ref<Loaded | null>(null)
const dragging = ref(false)
/** 读取本地文件时的忙碌态（与父组件的提交忙碌态分开：一个是「在读」，一个是「在交」） */
const reading = ref(false)
const inputRef = ref<HTMLInputElement | null>(null)

/** 预览只展示前 8 行：用户要确认「列对不对、值长什么样」，不需要在导入区翻 100 行 */
const preview = computed(() => loaded.value?.sheets.slice(0, 8) ?? [])

const rowIssues = computed(() => {
  const sheets = loaded.value?.sheets ?? []
  return validateRows(sheets.map((s) => ({ row_no: s.row_no, cells: s.cells })))
})

const issueByRow = computed(() => {
  const m = new Map<number, { field: string; message: string }>()
  for (const i of rowIssues.value) m.set(i.row_no, { field: i.field, message: i.message })
  return m
})

const canSubmit = computed(() => Boolean(loaded.value?.check.ok && loaded.value.sheets.length > 0))

function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(new Error('文件读取失败（可能是编码或权限问题）'))
    reader.readAsText(file, 'utf-8')
  })
}

async function handleFile(file: File) {
  reading.value = true
  try {
    const isCsv = file.name.toLowerCase().endsWith('.csv')
    // xlsx 是二进制：前端不解包，只做类型/大小校验，把内容交给后端
    const text = isCsv ? await readAsText(file) : null
    const check = validateFile({ name: file.name, size: file.size, text })

    let sheets: SheetRow[] = []
    let issues: string[] = []
    if (isCsv && text !== null) {
      const parsed = parseRosterCsv(text)
      issues = parsed.issues
      sheets = parsed.rows.map((r) => ({ row_no: Number(r.cells[0]) || r.row_no, cells: r.cells }))
    }

    loaded.value = { name: file.name, size: file.size, text, check, sheets, issues }
  } finally {
    reading.value = false
  }
}

function onDrop(e: DragEvent) {
  dragging.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) void handleFile(file)
}

function onPick(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) void handleFile(file)
}

/** 用内置演示名单：fetch 同一份 public 下的 CSV，走与用户上传完全相同的解析路径 */
async function useDemo() {
  reading.value = true
  try {
    const res = await fetch(DEMO_URL)
    const text = await res.text()
    const parsed = parseRosterCsv(text)
    const sheets = parsed.rows.map((r) => ({ row_no: Number(r.cells[0]) || r.row_no, cells: r.cells }))
    loaded.value = {
      name: 'batch_demo_100.csv（内置演示名单）',
      size: new Blob([text]).size,
      text,
      check: validateFile({ name: 'batch_demo_100.csv', size: new Blob([text]).size, text }),
      sheets,
      issues: parsed.issues,
    }
  } catch {
    loaded.value = null
  } finally {
    reading.value = false
  }
}

function submit() {
  if (!loaded.value || !canSubmit.value) return
  emit('submit', {
    name: loaded.value.name.replace('（内置演示名单）', ''),
    size: loaded.value.size,
    sheets: loaded.value.sheets,
  })
}

function reset() {
  loaded.value = null
  if (inputRef.value) inputRef.value.value = ''
}
</script>

<template>
  <section class="page__block" aria-labelledby="p8-import" data-testid="p8-section-import">
    <header class="page__block-head">
      <span class="page__code">P8-①</span>
      <h2 id="p8-import" class="page__block-title">名单导入</h2>
      <span class="page__block-desc">Q3 冻结模板 6 列 · CSV / xlsx · 上限 {{ SIZE_LIMIT_MB }} MB</span>
    </header>

    <div class="import">
      <!-- 左：拖拽区 -->
      <div
        class="drop"
        :class="{ 'drop--active': dragging, 'drop--filled': loaded }"
        data-testid="p8-dropzone"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input
          ref="inputRef"
          type="file"
          accept=".csv,.xlsx"
          class="drop__input"
          data-testid="p8-file-input"
          @change="onPick"
        />
        <template v-if="!loaded">
          <p class="drop__title">把名单文件拖到这里，或点击选择</p>
          <p class="drop__hint">
            支持 .csv（前端直接解析预览）与 .xlsx（二进制，交给后端解析）；不接受 .xls 旧格式
          </p>
          <div class="drop__actions">
            <button type="button" class="btn btn--primary" @click="inputRef?.click()">选择文件</button>
            <button type="button" class="btn" data-testid="p8-use-demo" @click="useDemo()">用内置演示名单（100 行）</button>
          </div>
        </template>
        <template v-else>
          <p class="drop__title drop__title--file" data-testid="p8-loaded-name">{{ loaded.name }}</p>
          <p class="drop__hint">
            {{ (loaded.size / 1024).toFixed(1) }} KB ·
            {{ loaded.sheets.length }} 行数据 ·
            {{ loaded.text === null ? '内容由后端解析' : '前端已解析预览' }}
          </p>
          <div class="drop__actions">
            <button type="button" class="btn" @click="inputRef?.click()">换一个文件</button>
            <button type="button" class="btn" @click="reset()">清空</button>
          </div>
        </template>
      </div>

      <!-- 右：模板与规则 -->
      <aside class="rules">
        <p class="rules__title">模板与校验规则</p>
        <ul class="rules__list">
          <li><b>必需 6 列</b>：{{ TEMPLATE_COLUMNS.join(' / ') }}</li>
          <li><b>证书编号</b>：<code>^GR\d{12}$</code>（GR + 12 位数字）</li>
          <li><b>来源年份</b>：2023 / 2024 / 2025</li>
          <li><b>批次</b>：第一批 / 第二批 / 第三批</li>
          <li><b>发证日期</b>：YYYY-MM-DD，且日期真实存在</li>
          <li><b>编码</b>：UTF-8（带 BOM 亦可，Excel「CSV UTF-8」即符合）</li>
        </ul>
        <div class="rules__dl">
          <a class="rules__link" :href="DEMO_URL" download data-testid="p8-template-download">
            下载演示名单（100 行，含 6 行故意做坏）
          </a>
          <span class="rules__dim">同一文件可直接提交，用于演示「失败行被逐条拦下」</span>
        </div>
      </aside>
    </div>

    <!-- 文件级校验：每条都给结论 -->
    <div v-if="loaded" class="checks" data-testid="p8-file-checks">
      <p class="checks__title" :class="loaded.check.ok ? 'is-ok' : 'is-bad'">
        {{ loaded.check.ok ? '文件级校验通过' : '文件级校验未通过' }} — {{ loaded.check.message }}
      </p>
      <ul class="checks__list">
        <li v-for="c in loaded.check.checks" :key="c.label" :class="c.ok ? 'is-ok' : 'is-bad'">
          <span class="checks__mark">{{ c.ok ? '✓' : '✕' }}</span>
          <span class="checks__label">{{ c.label }}</span>
          <span class="checks__detail">{{ c.detail }}</span>
        </li>
      </ul>
      <p v-if="loaded.issues.length" class="checks__issues">
        解析提示：{{ loaded.issues.slice(0, 3).join('；') }}
      </p>
    </div>

    <!-- 行预览 -->
    <div v-if="preview.length" class="preview" data-testid="p8-preview">
      <p class="preview__title">
        解析预览（前 {{ preview.length }} 行，共 {{ loaded!.sheets.length }} 行）
        <span v-if="rowIssues.length" class="preview__warn">
          其中 {{ rowIssues.length }} 行有字段级问题：提交后仍会进入任务，但会被后端逐行拦下并给出原因
        </span>
        <span v-else class="preview__ok">字段级校验全部通过</span>
      </p>
      <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th v-for="c in TEMPLATE_COLUMNS" :key="c">{{ c }}</th>
            <th>校验</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in preview" :key="r.row_no" :class="{ 'row--bad': issueByRow.has(r.row_no) }">
            <td v-for="(cell, i) in r.cells" :key="i" :class="{ 'cell--mono': i !== 1 }">{{ cell || '—' }}</td>
            <td>
              <span v-if="issueByRow.has(r.row_no)" class="tag tag--bad">
                {{ issueByRow.get(r.row_no)!.field }}
              </span>
              <span v-else class="tag tag--ok">通过</span>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <footer class="submit">
      <button
        type="button"
        class="btn btn--primary btn--lg"
        :disabled="!canSubmit || reading || busy"
        data-testid="p8-submit"
        @click="submit()"
      >
        {{ busy ? '提交中…' : reading ? '正在读取…' : `提交评估（${loaded?.sheets.length ?? 0} 行）` }}
      </button>
      <span class="submit__hint">
        提交后进入任务队列，逐行打分；字段级不合法的行**不出分**，在结果表里给出字段与原因。
      </span>
    </footer>
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

.import {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 1fr);
  gap: var(--space-4);
}

@media (max-width: 1023px) {
  .import {
    grid-template-columns: 1fr;
  }
}

.drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  min-height: 196px;
  padding: var(--space-5);
  border: 1.5px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface-muted);
  text-align: center;
}

.drop--active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.drop--filled {
  align-items: flex-start;
  text-align: left;
  background: var(--color-surface);
  border-style: solid;
}

.drop__input {
  position: absolute;
  inset: 0;
  opacity: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
}

.drop--filled .drop__input {
  pointer-events: none;
}

.drop__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}

.drop__title--file {
  font-family: var(--font-mono);
  font-size: 13px;
}

.drop__hint {
  max-width: 460px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.drop__actions {
  position: relative;
  z-index: 1;
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.rules {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.rules__title {
  font-size: 13px;
  font-weight: 600;
}

.rules__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.rules__list code {
  font-family: var(--font-mono);
  font-size: 11.5px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}

.rules__dl {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: auto;
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}

.rules__link {
  font-size: 12.5px;
  color: var(--color-accent);
  font-weight: 600;
}

.rules__dim {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.checks {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-muted);
}

.checks__title {
  font-size: 12.5px;
  font-weight: 600;
}

.checks__list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 4px var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
}

.checks__list li {
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.checks__mark {
  font-weight: 700;
}

.checks__label {
  color: var(--color-text);
  min-width: 76px;
}

.checks__detail {
  color: var(--color-text-muted);
}

.checks__issues {
  font-size: 11.5px;
  color: var(--risk-watch);
}

.is-ok {
  color: var(--risk-excellent);
}

.is-bad {
  color: var(--risk-high-risk);
}

.preview {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.preview__title {
  font-size: 12.5px;
  color: var(--color-text-muted);
}

.preview__warn {
  display: block;
  margin-top: 2px;
  color: var(--risk-watch);
}

.preview__ok {
  margin-left: var(--space-2);
  color: var(--risk-excellent);
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.table th,
.table td {
  padding: 6px var(--space-2);
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  white-space: nowrap;
}

.table th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.cell--mono {
  font-family: var(--font-mono);
  font-size: 11.5px;
}

.row--bad {
  background: color-mix(in srgb, var(--risk-high-risk-bg) 60%, transparent);
}

.tag {
  padding: 1px 6px;
  border-radius: 9px;
  font-size: 11px;
}

.tag--ok {
  color: var(--risk-excellent);
  background: var(--risk-excellent-bg);
}

.tag--bad {
  color: var(--risk-high-risk);
  background: var(--risk-high-risk-bg);
}

.submit {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border);
}

.submit__hint {
  font-size: 11.5px;
  color: var(--color-text-subtle);
}
</style>
