<script setup lang="ts">
/**
 * 结果表（P8 区块三，FE-20）
 *
 * 验收要求「支持排序、按风险分级筛选」，但这一块真正难的不是这两个功能，是**不许骗人**：
 *
 *   ① **失败行不隐藏**：默认状态是「全部」，9 行被拦下的行就在表里，带原因。
 *      只展示成功行是最常见的作假方式 —— 表格漂亮了，但用户永远不知道名单里有 9 家没跑出来。
 *   ② **未出分的行不排序**：按分数排序时，`credit_score === null` 的行永远沉底。
 *      否则升序第一屏全是失败行，读者会把"数据质量问题"误读成"最差的企业"。
 *   ③ **主要贡献因子只显代号**：`x125`，不是"研发投入强度"。字段字典（P0-2）没到就不许编名字。
 *   ④ **每一行能追到源头**：点行展开追溯面板，显示面板 userid / 图节点 / 契约文件 / 分数来源。
 *
 * 虚拟滚动：100 行不需要。真实 500 行时一次渲染 500 个 `<tr>` 在 Chromium 上约 30ms，
 * 加一层虚拟滚动反而让「Ctrl+F 找一个企业」失效。这里选择**分页 25 行 + 表头吸顶**。
 */
import { computed, ref, watch } from 'vue'

import BandBar from './BandBar.vue'
import { filterRows, sortRows, bandLabel, bandColor, STATUS_LABEL } from '@/utils/batch'
import type { FilterSpec, SortKey, SortSpec } from '@/utils/batch'
import type { BatchRow } from '@/types/contracts'

const props = defineProps<{
  rows: BatchRow[]
  /** 评估快照日（跨批次比对时页面要拿它拦一次） */
  asOf: string
  /** 外部要求定位到某一行（从失败分组点行号过来） */
  focusRow?: number | null
  /** 当前是处理中任务：表头要提醒「结果还会变」 */
  live?: boolean
}>()

const emit = defineEmits<{ export: []; compare: [] }>()

const PAGE = 25

const sort = ref<SortSpec>({ key: 'row_no', dir: 'asc' })
const keyword = ref('')
const bands = ref<string[]>([])
const status = ref<FilterSpec['status']>('all')
const anomalyOnly = ref(false)
const page = ref(1)
const expanded = ref<number | null>(null)

const filter = computed<FilterSpec>(() => ({
  bands: bands.value,
  status: status.value,
  keyword: keyword.value,
  anomalyOnly: anomalyOnly.value,
}))

const filtered = computed(() => filterRows(props.rows, filter.value))
const sorted = computed(() => sortRows(filtered.value, sort.value))
const pageCount = computed(() => Math.max(1, Math.ceil(sorted.value.length / PAGE)))
const pageRows = computed(() => sorted.value.slice((page.value - 1) * PAGE, page.value * PAGE))

/** 表头计数：用整份 rows 算，不随筛选变化 —— 否则「关注 38 家」的 38 会随筛选变，反而看不懂 */
const bandCounts = computed(() => {
  const c: Record<string, number> = {}
  for (const r of props.rows) if (r.status === 'ok' && r.risk_band) c[r.risk_band] = (c[r.risk_band] ?? 0) + 1
  return c
})

const counts = computed(() => ({
  all: props.rows.length,
  ok: props.rows.filter((r) => r.status === 'ok').length,
  failed: props.rows.filter((r) => r.status !== 'ok').length,
  anomaly: props.rows.filter((r) => r.anomaly === true).length,
}))

watch([filtered, sort], () => {
  page.value = 1
})

watch(
  () => props.focusRow,
  (n) => {
    if (!n) return
    // 定位：清掉筛选、按行号排序、翻到该行所在页、展开追溯
    bands.value = []
    status.value = 'all'
    anomalyOnly.value = false
    keyword.value = ''
    sort.value = { key: 'row_no', dir: 'asc' }
    const idx = props.rows.findIndex((r) => r.row_no === n)
    if (idx >= 0) {
      page.value = Math.floor(idx / PAGE) + 1
      expanded.value = n
    }
  },
)

function toggleSort(key: SortKey) {
  if (sort.value.key === key) {
    sort.value = { key, dir: sort.value.dir === 'asc' ? 'desc' : 'asc' }
  } else {
    sort.value = { key, dir: key === 'name' || key === 'row_no' ? 'asc' : 'desc' }
  }
}

function toggleBand(code: string) {
  bands.value = bands.value.includes(code) ? bands.value.filter((b) => b !== code) : [...bands.value, code]
}

function arrow(key: SortKey) {
  if (sort.value.key !== key) return ''
  return sort.value.dir === 'asc' ? '↑' : '↓'
}

function fmt(v: number | null, digits = 1) {
  return v === null || v === undefined ? '—' : v.toFixed(digits)
}
</script>

<template>
  <section class="page__block" aria-labelledby="p8-result" data-testid="p8-section-result">
    <header class="page__block-head">
      <span class="page__code">P8-③</span>
      <h2 id="p8-result" class="page__block-title">评估结果</h2>
      <span class="page__block-desc">
        <template v-if="live">任务未完成：以下为已处理部分，完成后自动刷新</template>
        <template v-else>评估快照 {{ asOf }} · 失败行不隐藏</template>
      </span>
      <button type="button" class="btn btn--sm" data-testid="p8-compare" @click="emit('compare')">与上一批次对比</button>
      <button type="button" class="btn btn--primary btn--sm" data-testid="p8-export" @click="emit('export')">
        导出 CSV（{{ sorted.length }} 行）
      </button>
    </header>

    <!-- 红线提示：批量场景最容易把「能出分」当成「能决策」 -->
    <p class="caveat" data-testid="p8-caveat">
      分数只够做名单初筛排序：行级实测 AUC ≈ 0.59，置换对照噪声地板 0.477，仅高出 0.1。
      批量结果<b>不构成授信依据</b>；主要贡献因子为未解析字段代号（P0-2 字段字典未到位）。
    </p>

    <!-- 筛选条 -->
    <div class="filters" data-testid="p8-filters">
      <label class="filters__search">
        <span>搜索</span>
        <input
          v-model="keyword"
          type="search"
          placeholder="企业名 / 证书编号 / userid / 行号"
          data-testid="p8-search"
        />
      </label>

      <div class="seg" role="group" aria-label="按状态筛选">
        <button
          v-for="s in [
            { k: 'all', t: '全部', n: counts.all },
            { k: 'ok', t: '已出分', n: counts.ok },
            { k: 'failed', t: '被拦下', n: counts.failed },
          ]"
          :key="s.k"
          type="button"
          class="seg__btn"
          :class="{ 'seg__btn--on': status === s.k }"
          :data-testid="`p8-status-${s.k}`"
          @click="status = s.k as FilterSpec['status']"
        >
          {{ s.t }}<span class="seg__n">{{ s.n }}</span>
        </button>
      </div>

      <label class="filters__anomaly">
        <input v-model="anomalyOnly" type="checkbox" data-testid="p8-anomaly-only" />
        只看异常（{{ counts.anomaly }}）
      </label>
    </div>

    <div class="bandline">
      <span class="bandline__label">按风险分级筛选（可多选，点两次取消）</span>
      <BandBar :counts="bandCounts" clickable :selected="bands" compact k="filter" @toggle="toggleBand" />
    </div>

    <!-- 结果表 -->
    <div class="table-wrap">
      <table class="table" data-testid="p8-result-table">
        <thead>
          <tr>
            <th class="th-sort" data-testid="p8-sort-row_no" @click="toggleSort('row_no')">行号 {{ arrow('row_no') }}</th>
            <th class="th-sort" data-testid="p8-sort-name" @click="toggleSort('name')">企业名称 {{ arrow('name') }}</th>
            <th class="th-mono">证书编号</th>
            <th class="th-mono">企业标识</th>
            <th class="th-sort th-num" data-testid="p8-sort-credit_score" @click="toggleSort('credit_score')">
              信用分 {{ arrow('credit_score') }}
            </th>
            <th class="th-sort" data-testid="p8-sort-risk_band" @click="toggleSort('risk_band')">
              风险分级 {{ arrow('risk_band') }}
            </th>
            <th class="th-sort th-num" data-testid="p8-sort-pd" @click="toggleSort('pd')">PD {{ arrow('pd') }}</th>
            <th>主要贡献因子</th>
            <th>是否异常</th>
            <th>评估状态</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="r in pageRows" :key="r.row_no">
            <tr
              :class="{ 'row--bad': r.status !== 'ok', 'row--open': expanded === r.row_no, 'row--anomaly': r.anomaly }"
              :data-row="r.row_no"
              :data-status="r.status"
              :data-band="r.risk_band ?? ''"
              @click="expanded = expanded === r.row_no ? null : r.row_no"
            >
              <td class="td-mono">{{ r.row_no }}</td>
              <td class="td-name">
                {{ r.name || '（空）' }}
                <span v-if="r.status !== 'ok'" class="td-name__why">{{ r.error?.message }}</span>
              </td>
              <td class="td-mono">{{ r.cert_no || '—' }}</td>
              <td class="td-mono">
                <template v-if="r.userid">{{ r.userid }}</template>
                <span v-else class="td-dim">未映射</span>
              </td>
              <td class="td-num td-mono">{{ fmt(r.credit_score) }}</td>
              <td>
                <span v-if="r.risk_band" class="chip" :style="{ '--c': bandColor(r.risk_band) }">
                  {{ r.risk_band_label ?? bandLabel(r.risk_band) }}
                </span>
                <span v-else class="td-dim">—</span>
              </td>
              <td class="td-num td-mono">
                <template v-if="r.pd !== null">{{ (r.pd * 100).toFixed(1) }}%</template>
                <span v-else class="td-dim">—</span>
              </td>
              <td class="td-mono">
                <span v-if="r.top_factor" class="code" :title="r.top_factor_known ? '' : '字段语义未知（P0-2）'">
                  {{ r.top_factor }}
                </span>
                <span v-else class="td-dim">—</span>
              </td>
              <td>
                <span v-if="r.anomaly === null" class="td-dim">—</span>
                <span v-else-if="r.anomaly" class="flag flag--bad">异常</span>
                <span v-else class="flag">正常</span>
              </td>
              <td>
                <span class="state" :data-state="r.status">{{ STATUS_LABEL[r.status] ?? r.status }}</span>
              </td>
            </tr>
            <tr v-if="expanded === r.row_no" class="trace">
              <td colspan="10">
                <div class="trace__body" data-testid="p8-trace">
                  <p class="trace__title">第 {{ r.row_no }} 行追溯</p>
                  <dl class="trace__dl">
                    <div><dt>名单字段</dt><dd>{{ r.name }} / {{ r.cert_no }} / {{ r.source_year }} / {{ r.batch }} / {{ r.issue_date }}</dd></div>
                    <div><dt>评估快照</dt><dd>{{ r.as_of }}（跨快照不可比）</dd></div>
                    <template v-if="r.status === 'ok' && r.trace">
                      <div><dt>面板记录</dt><dd><code>{{ r.trace.panel_userid }}</code> · 图节点 <code>{{ r.trace.graph_node_id }}</code> · 阶段 {{ r.trace.stage_id }} · 观测 {{ r.trace.observed_months }} 个月</dd></div>
                      <div><dt>分数来源</dt><dd>{{ r.trace.score_source }}</dd></div>
                      <div><dt>异常判定</dt><dd>{{ r.trace.anomaly_rule }}</dd></div>
                      <div><dt>契约文件</dt><dd><code>{{ r.trace.enterprise_contract }}</code> · <code>{{ r.trace.local_contract }}</code></dd></div>
                    </template>
                    <div v-else>
                      <dt>未出分原因</dt>
                      <dd>
                        <b>{{ r.error?.field }}</b>：{{ r.error?.message }}
                        <code class="trace__code">{{ r.error?.code }}</code>
                      </dd>
                    </div>
                  </dl>
                  <RouterLink
                    v-if="r.userid"
                    class="trace__link"
                    :to="{ name: 'enterprise', params: { userid: r.userid } }"
                  >
                    打开该企业的深度诊断（P7）→
                  </RouterLink>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>

      <p v-if="!pageRows.length" class="empty" data-testid="p8-table-empty">
        {{ rows.length ? '当前筛选条件下没有行（试试清空搜索或档位筛选）' : '这个任务还没有结果行' }}
      </p>
    </div>

    <!-- 分页 -->
    <footer v-if="sorted.length > PAGE" class="pager" data-testid="p8-pager">
      <button type="button" class="btn btn--sm" :disabled="page <= 1" @click="page -= 1">上一页</button>
      <span class="pager__text">
        第 {{ page }} / {{ pageCount }} 页 · {{ (page - 1) * PAGE + 1 }}–{{ Math.min(page * PAGE, sorted.length) }} 行
        （筛选后 {{ sorted.length }} / {{ rows.length }}）
      </span>
      <button type="button" class="btn btn--sm" :disabled="page >= pageCount" @click="page += 1">下一页</button>
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

.page__block-head .btn {
  margin-left: auto;
}

.page__block-head .btn + .btn {
  margin-left: 0;
}

.caveat {
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--risk-watch);
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
  font-size: 12px;
  line-height: 1.75;
  color: var(--color-text);
}

.filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.filters__search {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.filters__search input {
  min-height: 34px;
  width: 260px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  font-size: 12.5px;
  font-family: inherit;
}

.filters__anomaly {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.seg {
  display: inline-flex;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.seg__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  padding: 0 var(--space-3);
  border: none;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 12.5px;
  cursor: pointer;
}

.seg__btn + .seg__btn {
  border-left: 1px solid var(--color-border);
}

.seg__btn--on {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}

.seg__n {
  font-family: var(--font-mono);
  font-size: 10.5px;
  opacity: 0.8;
}

.bandline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
}

.bandline__label {
  flex: 0 0 auto;
  font-size: 11.5px;
  color: var(--color-text-muted);
}

/* 色带在窄屏下换行到下一行并允许自身折行，否则五段等分宽度之和会顶出容器 */
.bandline :deep(.band) {
  flex: 1 1 320px;
  min-width: 0;
  max-width: 620px;
  flex-wrap: wrap;
}

@media (max-width: 719px) {
  .bandline__label {
    flex: 1 1 100%;
  }
}

.table-wrap {
  overflow: auto;
  max-height: 720px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}

.table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px var(--space-2);
  background: var(--color-surface-muted);
  border-bottom: 1px solid var(--color-border-strong);
  text-align: left;
  font-weight: 600;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.th-sort {
  cursor: pointer;
  user-select: none;
}

.th-sort:hover {
  color: var(--color-accent);
}

.th-num,
.td-num {
  text-align: right;
}

.table td {
  padding: 6px var(--space-2);
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
}

.table tbody tr:not(.trace) {
  cursor: pointer;
}

.table tbody tr:hover:not(.trace) {
  background: var(--color-surface-muted);
}

.td-mono {
  font-family: var(--font-mono);
  font-size: 11.5px;
  white-space: nowrap;
}

.td-name {
  min-width: 220px;
}

.td-name__why {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--risk-high-risk);
}

.td-dim {
  color: var(--color-text-subtle);
}

.row--bad {
  background: color-mix(in srgb, var(--risk-high-risk-bg) 55%, transparent);
}

.row--anomaly .td-num {
  font-weight: 700;
}

.row--open {
  background: var(--color-accent-soft);
}

.chip {
  display: inline-block;
  padding: 0 7px;
  border-radius: 9px;
  border: 1px solid var(--c);
  color: var(--c);
  font-size: 11px;
  white-space: nowrap;
}

.code {
  padding: 0 4px;
  border-radius: 3px;
  background: var(--color-surface-muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.flag {
  font-size: 11px;
  color: var(--color-text-muted);
}

.flag--bad {
  padding: 0 6px;
  border-radius: 9px;
  background: var(--risk-high-risk-bg);
  color: var(--risk-high-risk);
  font-weight: 600;
}

.state {
  font-size: 11px;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--risk-excellent-bg);
  color: var(--risk-excellent);
  white-space: nowrap;
}

.state[data-state='field_error'],
.state[data-state='unmapped'] {
  background: var(--risk-high-risk-bg);
  color: var(--risk-high-risk);
}

.trace td {
  padding: 0;
  background: var(--color-accent-soft);
}

.trace__body {
  padding: var(--space-3) var(--space-4);
  cursor: default;
}

.trace__title {
  font-size: 12px;
  font-weight: 700;
  color: var(--color-accent);
  margin-bottom: 6px;
}

.trace__dl {
  display: grid;
  gap: 3px;
  margin: 0;
  font-size: 12px;
}

.trace__dl > div {
  display: flex;
  gap: var(--space-2);
}

.trace__dl dt {
  flex: 0 0 72px;
  color: var(--color-text-muted);
}

.trace__dl dd {
  margin: 0;
  color: var(--color-text);
}

.trace__dl code {
  font-family: var(--font-mono);
  font-size: 11px;
}

.trace__code {
  margin-left: 6px;
  color: var(--color-text-subtle);
}

.trace__link {
  display: inline-block;
  margin-top: var(--space-2);
  font-size: 12px;
  color: var(--color-accent);
  font-weight: 600;
}

.empty {
  padding: var(--space-5);
  text-align: center;
  font-size: 12.5px;
  color: var(--color-text-muted);
}

.pager {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: 12px;
  color: var(--color-text-muted);
}
</style>
