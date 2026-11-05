<script setup lang="ts">
/**
 * A02 数据质量诊断（FE-11 区块二）
 *
 * 两块内容：
 *   ① 缺失率热力图：字段 × 观测年。用 CSS grid 铺格而不是 ECharts heatmap ——
 *      14×3 的格子用 DOM 就能铺完，好处是每格可聚焦、可读屏、SSR 可断言；
 *      ECharts 版本在无头环境里只能导出图片，评审没法用键盘读。
 *   ② 缩尾前后箱线图：用 ECharts 自定义系列（custom）画 q05/q25/q50/q75/q95，
 *      因为 echarts 的 boxplot 需要额外注册，而 custom 的写法更直白。
 *
 * 红线：所有字段只显示代号（x021…），语义未知不得包装（契约 §6.5）。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import type { DataQuality, QualityField } from '@/types/contracts'
import type { ChartHeight } from '@/charts/sizes'
import { missingColor, missingTier, pct } from '@/utils/format'
import { assertFieldDisplayable, assertSingleDataset } from '@/utils/redlines'

const props = defineProps<{
  quality: DataQuality
  dataset: 'real_panel' | 'synthetic_graph' | 'mixed'
  nSamples?: number
}>()

/** 红线段言：本区块只承载单一来源，且字段名必须是代号形态 */
const dataset = computed(() => assertSingleDataset([props.dataset]))
props.quality.fields.forEach((f) => assertFieldDisplayable(f.field, f.semantic_known))

const sortMode = ref<'original' | 'missing'>('original')

const fields = computed<QualityField[]>(() => {
  const list = [...props.quality.fields]
  return sortMode.value === 'missing' ? list.sort((a, b) => b.missing_rate - a.missing_rate) : list
})

const years = computed(() => Array.from(new Set(props.quality.missing_matrix.map((c) => c.year))))

function cellRate(field: string, year: string): number | null {
  const hit = props.quality.missing_matrix.find((c) => c.field === field && c.year === year)
  return hit ? hit.missing_rate : null
}

const TIER_LABEL = { low: '低（<10%）', mid: '中（10–30%）', high: '高（30–50%）', severe: '极高（≥50%）' }

const summary = computed(() => props.quality.summary)

/** 只取前 4 个字段画箱线图，多了横向挤成一团 */
const boxFields = computed(() => props.quality.winsor.map((w) => w.field).filter((f, i, a) => a.indexOf(f) === i).slice(0, 4))

/**
 * 箱线图：每个字段一组（缩尾前 / 缩尾后）**并排**，用 custom 系列画「须—箱—中位线」。
 * 视觉编码：缩尾前用中性灰，缩尾后用风险第五档色，突出「上尾被收掉」这一事实。
 *
 * 踩坑：原实现用 `idx ± 0.18` 做偏移，但 xAxis 是 category 轴，坐标只落在整数刻度上，
 * ±0.18 会被吸附回同一个刻度，两组箱体完全重叠 —— 图上只剩红色，灰色根本看不见。
 * 改为先按 2×N 铺「槽位 / 并排刻度」（槽位 ×2 表示「前」「后」），再在每个槽位内部
 * 用 ±0.16 的类目内偏移，这样两组才真正并排。
 */
const SERIES_LABEL = ['缩尾前', '缩尾后'] as const

const slotIndex = computed(() => new Map(boxFields.value.map((f, i) => [f, i])))

const boxOption = computed(() => {
  const rows = props.quality.winsor
  const data: Array<[number, number, number, number, number, number, number]> = rows.map((r) => {
    const slot = (slotIndex.value.get(r.field) ?? 0) * 2
    const side = r.stage === 'before' ? 0 : 1
    return [
      slot + side,
      r.q05,
      r.q25 ?? r.q50,
      r.q50,
      r.q75 ?? r.q50,
      r.q95,
      side,
    ]
  })

  return {
    animation: false,
    grid: { left: 56, right: 24, top: 36, bottom: 44 },
    tooltip: {
      trigger: 'item',
      formatter: (p: { data: number[] }) => {
        const [slot, q05, q25, q50, q75, q95, stage] = p.data
        const f = boxFields.value[Math.floor(slot / 2)]
        return `${f} · ${SERIES_LABEL[stage]}<br/>q95 ${q95} / q75 ${q75} / 中位 ${q50} / q25 ${q25} / q05 ${q05}`
      },
    },
    xAxis: {
      type: 'category',
      // 2×N 槽位：每字段两个刻度（缩尾前 / 缩尾后），标签只在槽位中心显示一次
      data: boxFields.value.flatMap((f) => [`${f}·前`, `${f}·后`]),
      axisLabel: {
        fontSize: 11,
        fontFamily: 'monospace',
        // 前 / 后两组并排，字段代号标在两个刻度中间；用 CSS 变量会被 canvas 忽略，直接给色值
        interval: 0,
        color: '#5b6673',
        formatter: (v: string) => (v.endsWith('·前') ? v.slice(0, -2) : ''),
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dde1e7' } },
    },
    yAxis: {
      type: 'value',
      name: '取值',
      nameTextStyle: { fontSize: 11 },
      axisLabel: { fontSize: 11 },
      splitLine: { lineStyle: { color: '#e6e9ee' } },
    },
    series: [
      {
        type: 'custom',
        name: '缩尾前后分布',
        encode: { x: 0, y: [1, 5] },
        data,
        renderItem: (
          _params: unknown,
          api: {
            value: (i: number) => number
            coord: (p: number[]) => number[]
            size: (p: number[]) => number[]
            style: () => Record<string, unknown>
          },
        ) => {
          // 每个刻度内部再偏移 ±0.16，让「前」「后」并排而不是叠在同一刻度上
          const side = api.value(6)
          const x = api.coord([api.value(0) + (side === 0 ? -0.16 : 0.16), 0])[0]
          const y = (v: number) => api.coord([0, v])[1]
          // 每个字段占两个刻度，箱体宽度按半个槽位算，留出中缝
          const w = Math.max(10, api.size([0.52, 0])[0])
          const stage = side
          // ECharts canvas 不解析 CSS 变量，必须给十六进制字面量（与 theme.css 的 --risk-high-risk 一致）
          const color = stage === 0 ? '#b9c0c9' : '#b3312c'
          const stroke = stage === 0 ? '#8a939e' : '#b3312c'

          const q05 = y(api.value(1))
          const q25 = y(api.value(2))
          const q50 = y(api.value(3))
          const q75 = y(api.value(4))
          const q95 = y(api.value(5))

          return {
            type: 'group',
            children: [
              // 须
              { type: 'line', shape: { x1: x, y1: q95, x2: x, y2: q05 }, style: { stroke, lineWidth: 1 } },
              { type: 'line', shape: { x1: x - 5, y1: q95, x2: x + 5, y2: q95 }, style: { stroke, lineWidth: 1 } },
              { type: 'line', shape: { x1: x - 5, y1: q05, x2: x + 5, y2: q05 }, style: { stroke, lineWidth: 1 } },
              // 箱
              {
                type: 'rect',
                shape: { x: x - w / 2, y: q75, width: w, height: Math.max(1, q25 - q75), r: 2 },
                style: { fill: color, stroke, lineWidth: 1, opacity: 0.85 },
              },
              // 中位线
              { type: 'line', shape: { x1: x - w / 2, y1: q50, x2: x + w / 2, y2: q50 }, style: { stroke: '#fff', lineWidth: 1.6 } },
            ],
          }
        },
      },
    ],
    legend: { show: false },
  }
})
</script>

<template>
  <div class="quality stack">
    <div class="quality__kpis grid-12">
      <div class="col-3 quality__kpi">
        <span class="quality__kpi-label">字段数</span>
        <strong class="quality__kpi-value">{{ summary?.n_fields ?? fields.length }}</strong>
      </div>
      <div class="col-3 quality__kpi">
        <span class="quality__kpi-label">平均缺失率</span>
        <strong class="quality__kpi-value">{{ pct(summary?.mean_missing_rate) }}</strong>
      </div>
      <div class="col-3 quality__kpi">
        <span class="quality__kpi-label">缺失 &gt; 30% 字段</span>
        <strong class="quality__kpi-value quality__kpi-value--warn">
          {{ summary?.n_fields_missing_gt_30pct ?? '—' }}
        </strong>
      </div>
      <div class="col-3 quality__kpi">
        <span class="quality__kpi-label">语义已知字段占比</span>
        <strong class="quality__kpi-value quality__kpi-value--warn">
          {{ pct(summary?.semantic_known_ratio ?? 0) }}
        </strong>
      </div>
    </div>

    <!-- ① 缺失率热力图 -->
    <section class="quality__block">
      <header class="quality__block-head cluster">
        <h3 class="quality__block-title">缺失率热力图（字段 × 观测年）</h3>
        <div class="quality__sort" role="group" aria-label="排序方式">
          <button
            type="button"
            :aria-pressed="sortMode === 'original'"
            :class="{ 'is-active': sortMode === 'original' }"
            @click="sortMode = 'original'"
          >
            原始顺序
          </button>
          <button
            type="button"
            :aria-pressed="sortMode === 'missing'"
            :class="{ 'is-active': sortMode === 'missing' }"
            @click="sortMode = 'missing'"
          >
            按缺失率降序
          </button>
        </div>
      </header>

      <div class="quality__heat table-scroll">
        <table class="heat">
          <caption class="sr-only">
            各字段在 2021–2023 年的缺失率，颜色越深缺失越严重
          </caption>
          <thead>
            <tr>
              <th scope="col">字段</th>
              <th v-for="y in years" :key="y" scope="col">{{ y }}</th>
              <th scope="col">整体</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in fields" :key="f.field">
              <th scope="row">
                <UnknownFieldTag :field="f.field" :semantic-known="f.semantic_known" />
              </th>
              <td v-for="y in years" :key="y">
                <span
                  class="heat__cell"
                  :style="{ background: missingColor(cellRate(f.field, y) ?? 0) }"
                  :title="`${f.field} · ${y} 缺失率 ${pct(cellRate(f.field, y))}`"
                  :data-tier="missingTier(cellRate(f.field, y) ?? 0)"
                >
                  {{ cellRate(f.field, y) === null ? '—' : pct(cellRate(f.field, y)) }}
                </span>
              </td>
              <td>
                <span
                  class="heat__cell heat__cell--total"
                  :style="{ background: missingColor(f.missing_rate) }"
                  :title="`${f.field} 整体缺失率 ${pct(f.missing_rate)}`"
                  :data-tier="missingTier(f.missing_rate)"
                >
                  {{ pct(f.missing_rate) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <ul class="quality__legend" aria-label="缺失率图例">
        <li v-for="(label, tier) in TIER_LABEL" :key="tier">
          <span class="heat__swatch" :style="{ background: missingColor({ low: 0, mid: 0.2, high: 0.4, severe: 0.7 }[tier] ?? 0) }" />
          {{ label }}
        </li>
      </ul>
    </section>

    <!-- ② 缩尾前后箱线图 -->
    <div class="grid-12">
      <div class="col-7">
        <ChartPanel
          title="缩尾前后分布（q05–q25–中位–q75–q95）"
          :dataset="dataset"
          :n-samples="nSamples"
          :option="boxOption"
          :height="(320 as ChartHeight)"
        >
          <p class="quality__note">
            灰 = 缩尾前，红 = 1%/99% 缩尾后；边界只在训练集拟合，验证/测试集直接套用，不回流。
            上尾被收掉的比例即异常值率，见下方字段明细。
          </p>
        </ChartPanel>
      </div>

      <div class="col-5">
        <div class="quality__table-wrap">
          <table class="fields">
            <caption class="sr-only">字段质量明细</caption>
            <thead>
              <tr>
                <th scope="col">字段</th>
                <th scope="col">缺失率</th>
                <th scope="col">缩尾</th>
                <th scope="col">非空样本</th>
                <th scope="col">偏度</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in fields" :key="f.field">
                <th scope="row"><UnknownFieldTag :field="f.field" :semantic-known="f.semantic_known" /></th>
                <td :data-tier="missingTier(f.missing_rate)">{{ pct(f.missing_rate) }}</td>
                <td>{{ f.winsorized ? '是' : '否' }}</td>
                <td>{{ f.n_non_null?.toLocaleString('zh-CN') ?? '—' }}</td>
                <td>{{ f.skew_after?.toFixed(2) ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.quality__kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.quality__kpi-label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.quality__kpi-value {
  font-family: var(--font-mono);
  font-size: 22px;
  color: var(--color-text);
}

.quality__kpi-value--warn {
  color: var(--risk-warning);
}

.quality__block {
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.quality__block-title {
  font-size: 14px;
  font-weight: 600;
}

.quality__sort {
  display: inline-flex;
  margin-left: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.quality__sort button {
  min-height: 30px;
  padding: 0 var(--space-3);
  border: 0;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 12px;
  cursor: pointer;
}

.quality__sort button.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}

.quality__heat {
  margin-top: var(--space-3);
}

.heat {
  width: 100%;
  border-collapse: separate;
  border-spacing: 2px;
  font-size: 12px;
}

.heat th {
  text-align: left;
  font-weight: 500;
  color: var(--color-text-muted);
  padding: 2px 4px;
  white-space: nowrap;
}

.heat td {
  padding: 0;
}

.heat__cell {
  display: block;
  min-width: 64px;
  padding: 4px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 11px;
  text-align: center;
  color: var(--color-text);
}

.heat__cell[data-tier='high'],
.heat__cell[data-tier='severe'] {
  color: #fff;
}

.heat__cell--total {
  font-weight: 600;
}

.heat__swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
  vertical-align: -2px;
  margin-right: 4px;
}

.quality__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
  color: var(--color-text-muted);
}

.quality__note {
  margin-top: var(--space-2);
  font-size: 12px;
  line-height: 1.6;
  color: var(--color-text-muted);
}

.quality__table-wrap {
  height: 100%;
  max-height: 396px;
  overflow: auto;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.fields {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.fields th,
.fields td {
  padding: var(--space-2) var(--space-3);
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}

.fields thead th {
  position: sticky;
  top: 0;
  background: var(--color-surface-muted);
  font-weight: 500;
  color: var(--color-text-muted);
  z-index: 1;
}

.fields td {
  font-family: var(--font-mono);
  font-size: 11px;
}

.fields td[data-tier='severe'] {
  color: var(--risk-high-risk);
}

.fields td[data-tier='high'] {
  color: var(--risk-warning);
}
</style>
