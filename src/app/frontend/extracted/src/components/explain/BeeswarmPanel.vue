<script setup lang="ts">
/**
 * A13 蜂群图（beeswarm）—— FE-14
 *
 * 这张图回答条形图回答不了的问题：**贡献是集中在一小撮企业身上，还是均匀铺开。**
 * 条形图只给 mean|SHAP|，两根等长的柱子可能一个是"全体都受一点影响"、
 * 另一个是"少数企业被严重影响"，决策含义完全不同。
 *
 * 三条本图特有的纪律：
 *   ① 点位是 `reconstructed_from_anchors`，不是逐样本实测 —— 组件内固定写明重建口径，
 *      不能只挂"实测"角标了事（均值与方向实测，分布形状重建）；
 *   ② 纵向避让是确定性的（utils/beeswarm.ts），同一份数据永远画出同一张图，
 *      截图归档可比对；不用随机抖动；
 *   ③ x 轴范围全行共用：逐行自适应会让"同样大的 SHAP"在不同行看起来宽度不同。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import type { BeeswarmMeta, ShapBeeswarmFeature } from '@/types/contracts'
import type { DatasetKind } from '@/types'
import { layoutSwarm, swarmRange } from '@/utils/beeswarm'

const props = defineProps<{
  features: ShapBeeswarmFeature[]
  meta: BeeswarmMeta
  dataset: DatasetKind
  /** 图标题里的模型名 */
  modelLabel: string
}>()

/**
 * 行顺序：**沿用页面给的顺序**（页面的排序切换要同时作用于条形图与蜂群图，
 * 组件里再按 rank 排一次就会把用户选的排序吃掉）。
 * ECharts category 轴自下而上，所以倒序喂，让第一行显示在最上面。
 */
const rows = computed(() => [...props.features].reverse())

/** 特征值 → 颜色（低蓝 / 中灰 / 高红），与 SHAP 惯例一致；不用 visualMap 以免和主题色打架 */
function zColor(z: number): string {
  const t = Math.max(0, Math.min(1, (z + 2.5) / 5))
  const low = [47, 93, 138] // #2f5d8a
  const mid = [207, 214, 221] // #cfd6dd
  const high = [179, 49, 44] // #b3312c
  const [a, b, k] = t < 0.5 ? [low, mid, t / 0.5] : [mid, high, (t - 0.5) / 0.5]
  const mix = a.map((v, i) => Math.round(v + (b[i] - v) * k))
  return `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`
}

/**
 * x 轴范围：**对称**取 ±max|SHAP|。
 * 不对称（原始 min…max）会让 SHAP=0 落在偏左的位置，读者下意识把"零贡献"当成
 * "偏保护"，是这类图最常见的误读来源。对称 + 1.5% 留白 = 零线永远在正中。
 */
const range = computed<[number, number]>(() => {
  const [lo, hi] = swarmRange(rows.value.map((r) => r.points))
  const raw = Math.max(Math.abs(lo), Math.abs(hi)) * 1.02 || 1
  // 向上取整到 0.05 的倍数：轴标签才会是 -0.35 / -0.3 … 这种读得懂的数，
  // 而不是 "-0.3271"（最大值随数据变，标签就会天天变，截图前后不可比）
  const m = Math.ceil(raw / 0.05) * 0.05
  return [-m, m]
})

interface SwarmDatum {
  value: [number, number]
  itemStyle: { color: string; opacity: number }
  feature: string
  shap: number
  z: number
}

const seriesData = computed<SwarmDatum[]>(() => {
  const out: SwarmDatum[] = []
  rows.value.forEach((row, rowIndex) => {
    for (const p of layoutSwarm(row.points, rowIndex, range.value)) {
      out.push({
        value: [p.x, p.y],
        itemStyle: { color: zColor(p.value), opacity: 0.78 },
        feature: row.feature,
        shap: p.x,
        z: p.value,
      })
    }
  })
  return out
})

const option = computed(() => {
  const [lo, hi] = range.value
  return {
    animation: false,
    grid: { left: 80, right: 26, top: 18, bottom: 46 },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (p: { data: SwarmDatum }) => {
        const d = p.data
        const dir = d.shap > 0 ? '推高违约概率' : '降低违约概率'
        const level = d.z > 0.4 ? '高于解释集均值' : d.z < -0.4 ? '低于解释集均值' : '接近均值'
        return [
          `<b>${d.feature}</b>（语义未知）`,
          `SHAP ${d.shap > 0 ? '+' : ''}${d.shap.toFixed(4)} → ${dir}`,
          `特征值 z=${d.z > 0 ? '+' : ''}${d.z.toFixed(2)}（${level}）`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      min: Number(lo.toFixed(4)),
      max: Number(hi.toFixed(4)),
      name: 'SHAP 值（← 降低违约概率 ｜ 推高违约概率 →）',
      nameLocation: 'middle',
      nameGap: 26,
      nameTextStyle: { fontSize: 10, color: '#5b6673' },
      axisLabel: { fontSize: 10, color: '#5b6673', formatter: (v: number) => v.toFixed(2) },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    yAxis: {
      type: 'category',
      data: rows.value.map((r) => r.feature),
      axisLabel: { fontSize: 10, fontFamily: 'monospace', color: '#1f2933' },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    series: [
      {
        type: 'scatter',
        symbolSize: 3.6,
        data: seriesData.value,
        emphasis: { itemStyle: { opacity: 1, borderColor: '#1f2933', borderWidth: 0.6 } },
        // 零线：SHAP = 0 意味着"这个特征对这条样本没有推动"
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#98a2b3', width: 1, type: 'dashed' },
          label: { show: false },
          data: [{ xAxis: 0 }],
        },
      },
    ],
  }
})
</script>

<template>
  <ChartPanel
    :title="`${modelLabel} · 特征贡献分布（蜂群图 Top-${meta.n_features}）`"
    :dataset="dataset"
    :option="option"
    :height="460"
  >
    <ul class="legend" aria-label="颜色图例">
      <li class="legend__gradient">
        <span>特征值低</span>
        <i class="legend__bar" aria-hidden="true" />
        <span>高</span>
      </li>
      <li><span class="legend__dot legend__dot--zero" />虚线 = SHAP 0，右侧推高违约概率</li>
      <li class="legend__count">每行 {{ meta.n_plotted }} 点 / 解释集 {{ meta.n_total }}</li>
    </ul>
    <p class="note" data-testid="beeswarm-derivation">
      <b>点位口径：</b>逐样本 SHAP 值未随契约落盘，本图按<b>实测 mean|SHAP| 与实测方向</b>重建
      {{ meta.n_plotted }} 个样本点（<code>{{ meta.source }}</code>）——
      <b>均值与方向是实测的，分布形状是重建的</b>。要换成真实点位，只需算法侧把
      <code>shap_values.npz</code> 落盘后由脚本覆盖 <code>points</code>，前端零改动。
    </p>
  </ChartPanel>
</template>

<style scoped>
.legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
  font-size: 11px;
  color: var(--color-text-muted);
}

.legend__gradient {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.legend__bar {
  display: inline-block;
  width: 68px;
  height: 8px;
  border-radius: 4px;
  background: linear-gradient(90deg, #2f5d8a 0%, #cfd6dd 50%, #b3312c 100%);
}

.legend__dot {
  display: inline-block;
  width: 9px;
  height: 2px;
  margin-right: 5px;
  vertical-align: middle;
}

.legend__dot--zero {
  background: #98a2b3;
}

.legend__count {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--color-text-subtle);
}

.note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-subtle);
}

.note code {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 0 3px;
  border-radius: 2px;
  background: var(--color-surface-muted);
}
</style>
