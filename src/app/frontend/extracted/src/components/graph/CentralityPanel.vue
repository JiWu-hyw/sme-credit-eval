<script setup lang="ts">
/**
 * E2 中心性分析（FE-12 区块三）
 *
 * 组件回答三个问题，顺序就是版式顺序：
 *   1. 度分布长什么样？（图是枢纽型还是均匀型）
 *   2. 谁最重要？—— 而且**换成别的中心性，答案还一样吗？**
 *   3. 四个指标之间到底一不一致？（Spearman 秩相关矩阵）
 *
 * 为什么非要把 2 和 3 一起做：
 *   只给一张「Top-15 度中心性」的横条图，读者会默认「度最高=最重要」。
 *   实际契约里特征向量中心性与度中心性的秩相关是**负数**（二分图结构导致），
 *   不把这个矛盾摆出来，「中心性分析」就变成了「度排名」的包装。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { GraphCentrality, CentralityMetric, CentralityRow } from '@/types/contracts'
import type { DatasetKind, EntityKindKey } from '@/types'
import { ENTITY_KIND_LABELS } from '@/types'
import { ENTITY_COLORS } from '@/styles/theme'
import { CHART_HEIGHT } from '@/charts/sizes'
import { fixed, num, pct } from '@/utils/format'
import { assertSingleDataset } from '@/utils/redlines'

const props = defineProps<{
  centrality: GraphCentrality
  dataset: DatasetKind
  nSamples?: number
}>()

const dataset = computed(() => assertSingleDataset([props.dataset]))

const METRICS: CentralityMetric[] = ['degree', 'betweenness', 'closeness', 'eigenvector']
const metricLabel = (m: string) => props.centrality.metric_labels?.[m as CentralityMetric] ?? m

/* ------------------------------ 度分布 ------------------------------ */

const dist = computed(() => props.centrality.degree_distribution)
const distOption = computed(() => ({
  animation: false,
  grid: { left: 56, right: 24, top: 28, bottom: 44 },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (ps: Array<{ dataIndex: number }>) => {
      const b = dist.value.bins[ps[0]?.dataIndex ?? 0]
      return b ? `度 ${b.degree_range}<br/>节点 ${num(b.nodes)} 个（${pct(b.nodes / totalNodes.value)}）` : ''
    },
  },
  xAxis: {
    type: 'category',
    data: dist.value.bins.map((b) => b.degree_range),
    axisTick: { show: false },
    axisLabel: { fontSize: 10, rotate: 0, interval: 0 },
    name: '节点度',
    nameLocation: 'middle',
    nameGap: 28,
    nameTextStyle: { fontSize: 11 },
  },
  yAxis: {
    type: 'value',
    name: '节点数',
    nameTextStyle: { fontSize: 11 },
    axisLabel: { fontSize: 11 },
    splitLine: { lineStyle: { color: '#e6e9ee' } },
  },
  series: [
    {
      type: 'bar',
      data: dist.value.bins.map((b, i) => ({
        value: b.nodes,
        // 孤立档单独染灰：它不是"度很小的节点"，而是"完全没连边的节点"，
        // 两者混在一起读会把 10 个断点当成 10 个弱节点。
        itemStyle:
          i === 0 && dist.value.bins[0]?.degree_range?.startsWith('0')
            ? { color: '#9aa4b2', borderColor: '#6b7280', borderWidth: 1 }
            : { color: '#2f5d8a' },
      })),
      barMaxWidth: 34,
      itemStyle: { borderRadius: [2, 2, 0, 0] },
    },
  ],
}))

const totalNodes = computed(() => dist.value.bins.reduce((s, b) => s + b.nodes, 0))
/** 最大那一档（通常是度为 2 的专利）——结论里点名，让读者知道"大头"是谁 */
const peakBin = computed(() => {
  const bins = dist.value.bins ?? []
  return bins.reduce((a, b) => (b.nodes > a.nodes ? b : a), { degree_range: '—', nodes: 0 })
})
const peakNote = computed(() =>
  peakBin.value.nodes
    ? `度为 ${peakBin.value.degree_range} 的节点有 ${peakBin.value.nodes} 个（专利是「企业—领域」两端的叶节点）`
    : '',
)
const hubCount = computed(() => {
  const bins = dist.value.bins ?? []
  return bins.filter((b) => b.lo >= dist.value.max_degree / 2).reduce((s, b) => s + b.nodes, 0)
})
/** 度 0 的孤立节点数：契约里单列一档（`isolated: true`），有才在结论里提 */
const isolatedCount = computed(() => (dist.value.bins ?? []).find((b) => b.isolated)?.nodes ?? 0)

/* --------------------------- Top 榜（分类） --------------------------- */

/**
 * 默认展示「技术领域」而不是「企业」。
 *
 * 企业节点几乎都是度 8（专利 2–5 + 人员 1–2 + 产品 1 + 投资 0–1），条状图和雷达完全重合，
 * 看上去像"图坏了"；而 6 个技术领域才是真正有差异的枢纽，第一屏就能看出四类中心性的分歧。
 * 企业榜一键可切，说明文字也写清了"并列说明同类没有强弱"。
 */
const activeKind = ref<EntityKindKey>('field')
const activeMetric = ref<CentralityMetric>('degree')

/** 类型顺序：枢纽类（领域 / 机构 / 产品）在前，规模大的叶节点类在后 */
const KIND_ORDER: EntityKindKey[] = ['field', 'investor', 'product', 'company', 'patent', 'person']
const kindTabs = computed(() =>
  (KIND_ORDER.filter((k) => props.centrality.by_kind_top?.[k]) as EntityKindKey[]).map((k) => ({
    kind: k,
    label: ENTITY_KIND_LABELS[k] ?? k,
    count: (props.centrality.by_kind_top[k] ?? []).length,
  })),
)

const rows = computed<CentralityRow[]>(() => props.centrality.by_kind_top?.[activeKind.value] ?? [])

/** 排序键：度用 degree，其余用对应 pct（原始量纲不同，百分位才可横向比） */
function sortValue(r: CentralityRow, m: CentralityMetric): number {
  return m === 'degree' ? r.degree : (r[`${m}_pct`] as number)
}

/**
 * 按「当前主指标」排序，不是永远按度排。
 *
 * 第一版固定用度排，结果企业榜前 10 全是度 = 8 的平手 —— 换个指标看，条长和排序纹丝不动，
 * 用户会以为「切指标没生效」。现在主指标决定排序键，切到介数中心性时榜单真的会重排。
 */
const sortedRows = computed(() =>
  [...rows.value]
    .sort((a, b) => sortValue(b, activeMetric.value) - sortValue(a, activeMetric.value) || a.id.localeCompare(b.id))
    .slice(0, 15),
)

/** 四维雷达：把 Top-6 的四种百分位画出来，一眼看出「谁只在某个维度强」 */
const radarOption = computed(() => {
  const list = sortedRows.value.slice(0, 6)
  const avg = METRICS.map(
    (m) => rows.value.reduce((s, r) => s + (r[`${m}_pct`] as number), 0) / Math.max(1, rows.value.length),
  )
  return {
    animation: false,
    legend: { bottom: 0, itemWidth: 10, itemHeight: 8, textStyle: { fontSize: 10 }, type: 'scroll' },
    tooltip: { trigger: 'item' },
    radar: {
      center: ['50%', '46%'],
      radius: '64%',
      indicator: METRICS.map((m) => ({ name: metricLabel(m), max: 1 })),
      axisName: { fontSize: 10, color: '#5a6270' },
      splitArea: { areaStyle: { color: ['#fff', '#f7f8fa'] } },
      splitLine: { lineStyle: { color: '#e6e9ee' } },
      axisLine: { lineStyle: { color: '#e6e9ee' } },
    },
    series: [
      {
        type: 'radar',
        symbolSize: 3,
        data: [
          ...list.map((r) => ({
            name: r.label,
            value: METRICS.map((m) => Number((r[`${m}_pct`] as number).toFixed(3))),
            lineStyle: { width: 1.4 },
            areaStyle: { opacity: 0.06 },
          })),
          {
            name: `${ENTITY_KIND_LABELS[activeKind.value] ?? activeKind.value}均值`,
            value: avg.map((v) => Number(v.toFixed(3))),
            lineStyle: { width: 2, type: 'dashed', color: '#1b1f24' },
            itemStyle: { color: '#1b1f24' },
          },
        ],
      },
    ],
  }
})

const topOption = computed(() => {
  const list = [...sortedRows.value].reverse()
  return {
    animation: false,
    grid: { left: 118, right: 48, top: 12, bottom: 28 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: Array<{ dataIndex: number }>) => {
        const r = list[ps[0]?.dataIndex ?? 0]
        if (!r) return ''
        return [
          `<b>${r.label}</b>（${r.id}）`,
          `度 ${r.degree}`,
          `介数 ${fixed(r.betweenness, 4)}`,
          `接近 ${fixed(r.closeness, 4)}`,
          `特征向量 ${fixed(r.eigenvector, 4)}`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      // 度：按真实度数出轴（上限取该榜单最大值 +1），不做「度/最大度」归一 ——
      // 归一后企业度 8 对领域度 118，条长只有 7%，肉眼看就是一条线，等于没画。
      max: activeMetric.value === 'degree' ? (list[0]?.degree ?? 1) : 1,
      axisLabel: {
        fontSize: 10,
        formatter: (v: number) => (activeMetric.value === 'degree' ? `${v}` : `${Math.round(v * 100)}%`),
      },
      splitLine: { lineStyle: { color: '#e6e9ee' } },
    },
    yAxis: {
      type: 'category',
      data: list.map((r) => r.label),
      axisLabel: { fontSize: 11 },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        data: list.map((r) => ({
          value: activeMetric.value === 'degree' ? r.degree : r[`${activeMetric.value}_pct`],
          itemStyle: { color: ENTITY_COLORS[r.kind] ?? '#7a7a7a', borderRadius: [0, 2, 2, 0] },
        })),
        barMaxWidth: 16,
        label: {
          show: true,
          position: 'right',
          fontSize: 10,
          formatter: (p: { dataIndex: number }) => {
            const r = list[p.dataIndex]
            if (!r) return ''
            return activeMetric.value === 'degree' ? `${r.degree}` : `${Math.round((r[`${activeMetric.value}_pct`] as number) * 100)}%`
          },
        },
      },
    ],
  }
})

/* --------------------------- 秩相关矩阵 --------------------------- */

const corrMatrix = computed(() => {
  const c = props.centrality.rank_correlation ?? {}
  return METRICS.map((a) => METRICS.map((b) => Number(c[a]?.[b] ?? 0)))
})

const corrOption = computed(() => {
  const data: Array<[number, number, number]> = []
  // ECharts heatmap 的 data 是 [xIndex, yIndex, value]。
  // 轴配置里 x = 列（第二个指标）、y = 行（第一个指标），所以坐标必须是 [j, i] 不能是 [i, j]；
  // 第一版写反后矩阵只剩上三角有数、下三角全空，看起来像「对称性丢了」。
  corrMatrix.value.forEach((row, i) => row.forEach((v, j) => data.push([j, i, v])))
  return {
    animation: false,
    grid: { left: 96, right: 76, top: 12, bottom: 56 },
    tooltip: {
      formatter: (p: { data: [number, number, number] }) =>
        `${metricLabel(METRICS[p.data[1]])} × ${metricLabel(METRICS[p.data[0]])}<br/>Spearman ρ = ${fixed(p.data[2], 3)}`,
    },
    xAxis: {
      type: 'category',
      data: METRICS.map(metricLabel),
      axisLabel: { fontSize: 10, interval: 0 },
      splitArea: { show: true },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'category',
      data: METRICS.map(metricLabel),
      axisLabel: { fontSize: 10, interval: 0 },
      splitArea: { show: true },
      axisTick: { show: false },
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: false,
      orient: 'vertical',
      right: 0,
      top: 'middle',
      itemWidth: 10,
      itemHeight: 90,
      textStyle: { fontSize: 10 },
      inRange: { color: ['#b3312c', '#f7f7f7', '#2f5d8a'] },
    },
    series: [
      {
        type: 'heatmap',
        data,
        label: {
          show: true,
          fontSize: 10,
          formatter: (p: { data: [number, number, number] }) => fixed(p.data[2], 2),
          // 深色格子上白字，浅色格子上黑字：-0.29 印在深红底上看不清
          color: '#1b1f24',
        },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
      },
    ],
  }
})

/** 最刺眼的那一对（绝对值最小的非对角项）——结论就写它 */
const weakestPair = computed(() => {
  let worst = { a: '', b: '', v: 0 }
  METRICS.forEach((a) => METRICS.forEach((b) => {
    if (a === b) return
    const v = Number(props.centrality.rank_correlation?.[a]?.[b] ?? 0)
    if (!worst.a || Math.abs(v) < Math.abs(worst.v)) worst = { a, b, v }
  }))
  return worst
})

const negativePairs = computed(() => {
  const out: Array<{ a: string; b: string; v: number }> = []
  METRICS.forEach((a, i) => METRICS.forEach((b, j) => {
    if (j <= i) return
    const v = Number(props.centrality.rank_correlation?.[a]?.[b] ?? 0)
    if (v < 0) out.push({ a, b, v })
  }))
  return out
})
</script>

<template>
  <div class="cent">
    <div class="cent__top">
      <ChartPanel
        title="度分布（按度值分档）"
        :dataset="dataset"
        :n-samples="nSamples"
        :option="distOption"
        :height="CHART_HEIGHT.bar"
      >
        <p class="cent__note" data-testid="degree-note">
          最大度 <b>{{ dist.max_degree }}</b> · 均值 <b>{{ fixed(dist.mean_degree, 2) }}</b> · 中位
          <b>{{ dist.median_degree }}</b>；度 ≥ 最大值一半的枢纽节点（{{ hubCount }} 个）承载了
          <b>{{ pct(dist.hub_share) }}</b> 的边。横轴按度值分档而不是等距分箱——度集中在 2，
          等距分箱会把 800+ 个节点挤进一根柱子，看不出形状。
          形态是典型的<b>双峰长尾</b>：{{ peakNote }}，然后 6–8 度的企业连成第二团，
          最后 6 个 ≥65 度的技术领域 / 机构独自撑起连通性。
          <template v-if="isolatedCount">
            灰色第一档是<b>度 0 的孤立节点（{{ isolatedCount }} 个）</b>——它们是本轮抽样跳过、
            没有任何边的企业，在图上等于隐身；这类断点不补齐，图谱就永远接不上远端机构。
          </template>
        </p>
      </ChartPanel>

      <ChartPanel
        title="四类中心性秩相关（Spearman ρ）"
        :dataset="dataset"
        :n-samples="nSamples"
        :option="corrOption"
        :height="CHART_HEIGHT.bar"
      >
        <p class="cent__note" data-testid="corr-note">
          最弱的一对是 <b>{{ metricLabel(weakestPair.a) }} × {{ metricLabel(weakestPair.b) }}</b>：
          ρ = <b>{{ fixed(weakestPair.v, 2) }}</b>。
          <template v-if="negativePairs.length">
            其中 {{ negativePairs.map((p) => `${metricLabel(p.a)}与${metricLabel(p.b)}（${fixed(p.v, 2)}）`).join('、') }}
            为<b>负相关</b>——这不是算错：二分图里「邻居多」和「邻居重要」是两回事。
          </template>
          所以下面的榜单必须写明用的是哪个指标。
        </p>
      </ChartPanel>
    </div>

    <div class="cent__bottom">
      <div class="cent__tabs" role="tablist" aria-label="实体类型">
        <button
          v-for="t in kindTabs"
          :key="t.kind"
          type="button"
          role="tab"
          class="cent__tab"
          :aria-selected="activeKind === t.kind"
          :data-kind="t.kind"
          @click="activeKind = t.kind"
        >
          {{ t.label }} <span class="cent__tab-n">{{ t.count }}</span>
        </button>
      </div>

      <div class="cent__metrics" role="group" aria-label="中心性指标">
        <button
          v-for="m in METRICS"
          :key="m"
          type="button"
          class="cent__metric"
          :aria-pressed="activeMetric === m"
          :data-metric="m"
          @click="activeMetric = m"
        >
          {{ metricLabel(m) }}
        </button>
      </div>

      <div class="cent__pair">
        <ChartPanel
          :title="`${ENTITY_KIND_LABELS[activeKind] ?? activeKind} · 按${metricLabel(activeMetric)}排序（Top-${sortedRows.length}）`"
          :dataset="dataset"
          :n-samples="nSamples"
          :option="topOption"
          :height="CHART_HEIGHT.bar"
        >
          <p class="cent__note">
            <template v-if="activeMetric === 'degree'">
              条长是<b>真实度数</b>（未归一，横轴上限取本榜最大值）。
              <template v-if="activeKind === 'company' || activeKind === 'patent' || activeKind === 'person'">
                这类实体的度数大量并列（企业几乎都是 8），并列说明「同类里没有强弱」，
                要看右侧雷达里其他维度的差别。
              </template>
            </template>
            <template v-else>条长为百分位：该节点在全部节点中的排名位置（0–100%），四类指标只有百分位可横向比。</template>
          </p>
        </ChartPanel>

        <ChartPanel
          title="四维中心性对照（Top-6 vs 同类均值）"
          :dataset="dataset"
          :n-samples="nSamples"
          :option="radarOption"
          :height="CHART_HEIGHT.bar"
          :merged-components="['radar']"
        >
          <p class="cent__note">
            黑虚线是该类实体的平均值。四条轴都归一到 0–1 百分位，所以形状表达的是<b>相对位置</b>，
            不是绝对强度；某条轴凹进去，说明这个实体在那一类结构意义上并不突出。
          </p>
        </ChartPanel>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cent {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.cent__top,
.cent__bottom {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
}

.cent__bottom {
  grid-template-columns: 1fr;
}

@media (max-width: 1080px) {
  .cent__top {
    grid-template-columns: 1fr;
  }
}

.cent__pair {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
  gap: var(--space-4);
}

@media (max-width: 1080px) {
  .cent__pair {
    grid-template-columns: 1fr;
  }
}

.cent__tabs,
.cent__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.cent__tab,
.cent__metric {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-surface);
  font-size: 12px;
  cursor: pointer;
}

.cent__tab[aria-selected='true'],
.cent__metric[aria-pressed='true'] {
  border-color: #2f5d8a;
  background: #e8eef6;
  font-weight: 600;
}

.cent__tab-n {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.cent__note {
  margin: var(--space-2) 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}
</style>
