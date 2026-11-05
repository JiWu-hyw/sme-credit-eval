<script setup lang="ts">
/**
 * P7 区块三 · 特征雷达（FE-16 / A04 的企业视角）
 *
 * 三类维度（技术要素 / 成长轨迹 / 融合特征）× 每类 4 个指标，两根多边形：
 * 本企业（实线）与同行中位数（虚线）。
 *
 * 这一块的核心不是雷达图画得好不好看，而是**缺数据时不能画成 0**：
 * 技术要素与成长轨迹两个维度在当前面板里一个字段都没有（P0-2 字段字典未补齐，
 * 面板里没有字段能归到这两维），所以它们的 8 根轴必须显示为「无数据」。
 * 画成 0 等于告诉读者「这家企业在这两维上一无所有」，那是判断，不是事实。
 *
 * 实现上：`radarValues()` 把缺数据的轴给 `null`，ECharts 对 null 的处理是**不连线**
 * （留缺口），这正是我们要的样子；同时在图下方逐个维度标出「有几项有数据」，
 * 并把缺数据的维度单独列出来，避免读者把缺口当成「值很小」。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { DatasetKind } from '@/types'
import type { EnterpriseRadarDim } from '@/types/contracts'
import { dimCoverage, radarHasAny, radarValues } from '@/utils/enterprise'
import { CHART_HEIGHT } from '@/charts/sizes'

const props = defineProps<{
  radar: EnterpriseRadarDim[]
  dataset: DatasetKind
  nSamples?: number
}>()

const axes = computed(() => radarValues(props.radar))
const anyData = computed(() => radarHasAny(axes.value))
const coverage = computed(() => dimCoverage(props.radar))
const missingDims = computed(() => coverage.value.filter((c) => c.withData === 0).map((c) => c.dim))

/**
 * 维度色：与 P5 的 A15 阶段-因子图**同一套**（技术要素 #2f5d8a / 成长轨迹 #c08a1e / 融合特征 #7a5fa8），
 * 按 factor_id 取色而不是按中文名 —— 中文名一变，按名取色的图例就会静默退化成灰色。
 */
const DIM_COLORS: Record<string, string> = {
  tech: '#2f5d8a',
  growth: '#c08a1e',
  fusion: '#7a5fa8',
}

const DIM_ID_BY_LABEL = computed(() => {
  const m = new Map<string, string>()
  for (const d of props.radar) m.set(d.factor_label, d.factor_id)
  return m
})

function dimColor(label: string): string {
  const id = DIM_ID_BY_LABEL.value.get(label)
  return (id && DIM_COLORS[id]) || '#8a939e'
}

/**
 * 右侧明细表：雷达图看形状，表格看数值。
 *
 * 一块 1292px 宽的区块里只放一个直径 200px 的雷达图，右边会空出 700px —— 这不是「留白」，
 * 是「信息没有用起来」。所以把 12 项的数值摊成表：形状负责一眼看出偏科，表格负责复核。
 */
const rows = computed(() =>
  props.radar.flatMap((d) =>
    d.items.map((it) => ({
      dim: d.factor_label,
      dimId: d.factor_id,
      label: it.label,
      self: it.has_data ? it.percentile : null,
      peer: it.has_data ? it.peer_median : null,
      gap: it.has_data && it.percentile !== null && it.peer_median !== null ? it.percentile - it.peer_median : null,
    })),
  ),
)

const option = computed(() => {
  const list = axes.value
  return {
    animation: false,
    legend: { top: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: '#5b6673' } },
    tooltip: { trigger: 'item' },
    radar: {
      center: ['50%', '56%'],
      radius: '62%',
      // 每 20% 一档（0/20/40/60/80/100）：分位数天然以 50 为「中位」。
      //
      // ⚠️ 这里必须是 5，不能是 4。ECharts 的 radar 会用第一个 indicator 建一把「对齐刻度」的
      // 虚拟坐标轴，splitNumber=4 时算出的 interval 不是「好看数」（25 → 某次内部取整后变成 25.0000001 之类），
      // 于是 dev 环境下会连刷几十条 `The ticks may be not readable...` 警告 —— 图本身是对的，
      // 但冒烟里「无 ECharts 告警」这一条会被判红，而且很容易被当成脏日志忽略掉。
      splitNumber: 5,
      indicator: list.map((a) => ({ name: a.name, max: 100, min: 0 })),
      axisName: {
        fontSize: 10,
        color: '#5b6673',
        formatter: (name: string) => {
          const ax = list.find((a) => a.name === name)
          return ax && !ax.hasData ? `${name}\n（无数据）` : name
        },
      },
      splitArea: { areaStyle: { color: ['#ffffff', '#fafbfc'] } },
      splitLine: { lineStyle: { color: '#eef1f5' } },
      axisLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        type: 'radar',
        symbolSize: 4,
        data: [
          {
            name: '本企业分位',
            // null 不连线 —— 缺数据的维度必须留缺口，不许补 0
            value: list.map((a) => (a.hasData ? a.self : null)),
            lineStyle: { width: 2, color: '#b3312c' },
            itemStyle: { color: '#b3312c' },
            areaStyle: { opacity: 0.12, color: '#b3312c' },
          },
          {
            name: '同行中位数',
            value: list.map((a) => (a.hasData ? a.peer : null)),
            lineStyle: { width: 1.5, type: 'dashed', color: '#2f5d8a' },
            itemStyle: { color: '#2f5d8a' },
          },
        ],
      },
    ],
  }
})
</script>

<template>
  <div class="radar" data-testid="p7-radar-block">
    <div v-if="anyData" class="radar__main">
      <ChartPanel
        title="三类维度归一化分位（本企业 vs 同行中位数）"
        :dataset="dataset"
        :n-samples="nSamples"
        :noise-floor="null"
        :option="option"
        :height="CHART_HEIGHT.line"
        :merged-components="['radar']"
      >
        <p class="radar__note">
          怎么看：分位数越高越好，50 = 同行中位。图中<b>断开的缺口是「该维度无数据」，不是 0</b>。
        </p>
      </ChartPanel>

      <table class="tbl radar__table" data-testid="p7-radar-table">
        <caption>
          12 项指标的数值（雷达图看形状，这张表看数）
        </caption>
        <thead>
          <tr>
            <th scope="col">维度</th>
            <th scope="col">指标</th>
            <th scope="col" class="num">本企业分位</th>
            <th scope="col" class="num">同行中位</th>
            <th scope="col" class="num">差值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.label" :data-empty="r.self === null">
            <td>
              <span class="radar__dot" :style="{ background: dimColor(r.dim) }" />
              {{ r.dim }}
            </td>
            <td>{{ r.label }}</td>
            <td class="num">{{ r.self === null ? '无数据' : r.self.toFixed(1) }}</td>
            <td class="num">{{ r.peer === null ? '—' : r.peer.toFixed(1) }}</td>
            <td class="num" :class="r.gap === null ? '' : r.gap >= 0 ? 'is-up' : 'is-down'">
              {{ r.gap === null ? '—' : `${r.gap >= 0 ? '+' : ''}${r.gap.toFixed(1)}` }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="radar__empty" data-testid="p7-radar-empty">
      <p class="radar__empty-title">三类维度均无数据，雷达图不绘制</p>
      <p class="radar__empty-text">
        技术要素 / 成长轨迹 / 融合特征的指标需要字段字典（P0-2）才能归属，当前面板里没有字段可归属；
        画一张全 0 的雷达图等于断言「这家企业在三维上一无所有」，那是不成立的结论。
      </p>
    </div>

    <div class="radar__cover" data-testid="p7-radar-coverage">
      <div v-for="c in coverage" :key="c.dim" class="radar__dim" :data-empty="c.withData === 0">
        <span class="radar__dot" :style="{ background: dimColor(c.dim) }" />
        <span class="radar__dim-name">{{ c.dim }}</span>
        <span class="radar__dim-count">{{ c.withData }} / {{ c.total }} 项有数据</span>
      </div>
    </div>

    <p v-if="missingDims.length" class="radar__warn" data-testid="p7-radar-warning">
      缺数据的维度：<b>{{ missingDims.join('、') }}</b> —— 待 P0-2（x21–x200 字段字典）与 P0-4（专利）、
      P0-5（融资 / 工商变更）接入后，这 8 根轴才会出现；届时本组件零改动。
    </p>
  </div>
</template>

<style scoped>
.radar {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.radar__main {
  display: grid;
  /* 雷达图是正方形，宽度给够就够，剩下的全给表 —— 否则 1292px 里空 700px */
  grid-template-columns: minmax(420px, 1fr) minmax(420px, 520px);
  gap: var(--space-4);
  align-items: start;
}

@media (max-width: 1100px) {
  .radar__main {
    grid-template-columns: 1fr;
  }
}

.radar__table {
  font-size: 11.5px;
}

.radar__table caption {
  padding-bottom: var(--space-2);
  font-size: 11.5px;
  color: var(--color-text-muted);
  text-align: left;
}

.radar__table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.radar__table .is-up {
  color: #b3312c;
}

.radar__table .is-down {
  color: #2f5d8a;
}

.radar__table tr[data-empty='true'] td {
  color: var(--color-text-muted);
}

.radar__note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}

.radar__empty {
  padding: var(--space-6);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: center;
}

.radar__empty-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.radar__empty-text {
  max-width: 720px;
  margin: var(--space-2) auto 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.radar__cover {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.radar__dim {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: 12px;
}

.radar__dim[data-empty='true'] {
  border-style: dashed;
  color: var(--color-text-subtle);
}

.radar__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.radar__dim-name {
  font-weight: 600;
}

.radar__dim-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-muted);
}

.radar__warn {
  padding: var(--space-2) var(--space-3);
  border-left: 2px solid var(--risk-watch);
  background: var(--risk-watch-bg);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-muted);
}
</style>
