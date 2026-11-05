<script setup lang="ts">
/**
 * A12 置换对照噪声地板（FE-13）
 *
 * 这是全项目最重要的一张图，没有之一。
 * 它把「观察到的 AUC」和「打乱标签后还能拿到的 AUC」并排放在同一把尺子上——
 * 如果两者挨着，那 0.59 就不是"模型挺准"，而是"数据里本来就只有这么多可分的"。
 *
 * 设计要点：
 *   - 观察组与控制组用**两种底色分区**，中间一条 0.477 的红色虚线；
 *   - 每根柱子带 5 折标准差误差带（有 std 才画），避免读者拿单次结果比大小；
 *   - 面板底部直接给「高出地板多少」的差额表，把减法替读者做完；
 *   - 图的 title 与图注都写"地板不是及格线"，因为这是最常见的误读。
 */
import { computed } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'

import { CHART_HEIGHT } from '@/charts/sizes'
import type { ContractMeta, DatasetKind } from '@/types'
import type { NoiseFloorContract } from '@/types/contracts'

const props = defineProps<{
  data: NoiseFloorContract
  dataset: DatasetKind
  meta: ContractMeta
}>()

const option = computed(() => {
  const bars = props.data.bars
  return {
    animation: false,
    grid: { left: 132, right: 64, top: 20, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: Array<{ dataIndex: number; value: number }>) => {
        const b = bars[ps[0]?.dataIndex ?? 0]
        if (!b) return ''
        const diff = b.auc - props.data.noise_floor
        return [
          `<b>${b.label}</b>`,
          `AUC ${b.auc.toFixed(4)}${b.std ? ` ± ${b.std.toFixed(3)}` : ''}`,
          `相对地板 ${diff >= 0 ? '+' : ''}${diff.toFixed(4)}`,
          b.derived ? '（合成对照，非实测）' : '',
        ]
          .filter(Boolean)
          .join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      min: 0.4,
      max: 0.66,
      name: 'AUC',
      nameLocation: 'end',
      axisLabel: { fontSize: 10, color: '#5b6673' },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: bars.map((b) => b.label),
      axisLabel: { fontSize: 11, color: '#1f2933' },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#dde1e7' } },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        data: bars.map((b) => ({
          value: b.auc,
          itemStyle: {
            // 观察组深蓝、控制组灰——色块区分比图例更可靠
            color: b.group === 'observed' ? '#2f5d8a' : '#b9c0c9',
            opacity: b.group === 'observed' ? 0.95 : 0.85,
          },
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            color: '#5b6673',
            formatter: `${b.auc.toFixed(3)}${b.std ? ` ±${b.std.toFixed(2)}` : ''}`,
          },
        })),
        markLine: {
          silent: true,
          symbol: 'none',
          data: [
            {
              xAxis: props.data.noise_floor,
              lineStyle: { color: '#b3312c', type: 'dashed', width: 1.5 },
              label: {
                formatter: `噪声地板 ${props.data.noise_floor}`,
                fontSize: 10,
                color: '#b3312c',
                position: 'end',
              },
            },
            {
              xAxis: props.data.coin_flip,
              lineStyle: { color: '#8a939e', type: 'dotted', width: 1 },
              label: { formatter: '抛硬币 0.5', fontSize: 10, color: '#8a939e', position: 'start' },
            },
          ],
        },
      },
    ],
  }
})

const controlCount = computed(() => props.data.bars.filter((b) => b.group === 'control').length)
</script>

<template>
  <div class="a12" data-testid="noise-floor-block">
    <ChartPanel
      title="观察组 vs 置换对照组：AUC 摆在同一把尺子上"
      :dataset="dataset"
      :n-samples="meta.n_samples ?? undefined"
      :noise-floor="data.noise_floor"
      :option="option"
      :height="Math.max(CHART_HEIGHT.bar + 60, data.bars.length * 26 + 90)"
    >
      <p class="a12__verdict" data-testid="noise-floor-verdict">{{ data.verdict }}</p>

      <div class="a12__legend">
        <span class="a12__swatch" data-kind="observed" />观察组（真实训练结果，{{ data.bars.length - controlCount }} 条）
        <span class="a12__swatch" data-kind="control" />对照组（打乱标签 / 随机 / 打乱特征，{{ controlCount }} 条）
      </div>

      <table class="tbl" data-testid="noise-margin-table">
        <caption>高出地板多少（地板 = {{ data.floor_label }}）</caption>
        <thead>
          <tr><th scope="col">口径</th><th scope="col">AUC</th><th scope="col">地板</th><th scope="col">差额 Δ</th><th scope="col">相对</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in data.margin" :key="m.model_id">
            <th scope="row">{{ m.observed_label }}</th>
            <td class="mono">{{ m.observed.toFixed(4) }}</td>
            <td class="mono">{{ m.floor.toFixed(3) }}</td>
            <td class="mono" :data-sign="m.delta >= 0 ? 'up' : 'down'">{{ m.delta >= 0 ? '+' : '' }}{{ m.delta.toFixed(4) }}</td>
            <td class="mono">{{ m.delta_pct >= 0 ? '+' : '' }}{{ m.delta_pct.toFixed(1) }}%</td>
          </tr>
        </tbody>
      </table>

      <p class="a12__note">{{ data.floor_note }}</p>
      <p class="a12__note a12__note--warn">
        <b>地板不是及格线。</b>它是"标签被打乱后模型还能捞到多少"的期望值；
        真正有信号的标准是"显著高于地板"，而不是"高于 0.5"。
      </p>
    </ChartPanel>

    <section class="a12__strategies" aria-labelledby="strategies">
      <header class="a12__strategies-head">
        <h3 id="strategies">本项目用到的五种对照口径</h3>
        <!-- 这一块沿用区块头的角标（A4 已挂），此处不重复挂第二颗：同一区块两颗角标会让人以为是两个数据源 -->
      </header>
      <ul>
        <li v-for="s in data.strategies" :key="s.strategy_id" :data-role="s.role">
          <span class="a12__s-label">{{ s.label }}</span>
          <span class="a12__s-role" :data-role="s.role">{{ s.role === 'control' ? '对照' : '诊断' }}</span>
          <span class="a12__s-desc">{{ s.description }}</span>
          <span v-if="s.derived" class="a12__s-tag">合成</span>
        </li>
      </ul>
      <ul class="a12__sub-notes">
        <li v-for="n in data.notes" :key="n">{{ n }}</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.a12 {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.a12__verdict {
  margin-top: var(--space-2);
  padding: var(--space-3);
  border-left: 3px solid var(--risk-high-risk);
  background: var(--risk-high-risk-bg);
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--color-text);
}

.a12__legend {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
  font-size: 11px;
  color: var(--color-text-muted);
}

.a12__swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.a12__swatch[data-kind='observed'] {
  background: #2f5d8a;
}

.a12__swatch[data-kind='control'] {
  background: #b9c0c9;
}

.tbl {
  width: 100%;
  margin-top: var(--space-3);
  border-collapse: collapse;
  font-size: 12px;
}

.tbl caption {
  caption-side: top;
  padding-bottom: var(--space-2);
  text-align: left;
  font-size: 11.5px;
  color: var(--color-text-muted);
}

.tbl th,
.tbl td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--color-border);
  text-align: right;
  white-space: nowrap;
}

.tbl th[scope='col']:first-child,
.tbl th[scope='row'] {
  text-align: left;
}

.tbl thead th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.tbl td[data-sign='down'] {
  color: var(--risk-high-risk);
}

.a12__note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--color-text-muted);
}

.a12__note--warn {
  color: var(--risk-high-risk);
}

.a12__strategies {
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.a12__strategies-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.a12__strategies h3 {
  font-size: 14px;
  font-weight: 600;
}

/* 对照组说明与底部注释分两栏：说明占了整行会在窄屏下把右侧挤出一列空槽 */
.a12__strategies ul:not(.a12__sub-notes) {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
}

.a12__strategies ul:not(.a12__sub-notes) li {
  display: grid;
  grid-template-columns: 180px auto 1fr auto;
  align-items: baseline;
  gap: var(--space-2);
  padding: 6px 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 12px;
}

/* 底部两条口径说明：不能落进上面那条 grid（180px/auto/1fr/auto），否则正文被挤成竖条 */
.a12__sub-notes {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-3) 0 0;
  padding: var(--space-3) 0 0;
  border-top: 1px solid var(--color-border);
  list-style: none;
}

.a12__sub-notes li {
  position: relative;
  padding-left: 14px;
  font-size: 11.5px;
  line-height: 1.75;
  color: var(--color-text-muted);
}

.a12__sub-notes li::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 8px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-text-subtle);
}

.a12__s-label {
  font-weight: 600;
}

.a12__s-role {
  padding: 0 6px;
  border-radius: 8px;
  font-size: 10px;
  border: 1px solid var(--color-border-strong);
  color: var(--color-text-subtle);
  justify-self: start;
}

.a12__s-role[data-role='control'] {
  border-color: var(--risk-high-risk);
  color: var(--risk-high-risk);
}

.a12__s-desc {
  color: var(--color-text-muted);
  font-size: 11.5px;
}

.a12__s-tag {
  padding: 0 5px;
  border: 1px dashed var(--color-text-subtle);
  border-radius: 8px;
  font-size: 10px;
  color: var(--color-text-subtle);
}

.a12__sub-notes {
  margin: var(--space-3) 0 0;
  padding-left: 1.1em;
  font-size: 11.5px;
  line-height: 1.9;
  color: var(--color-text-muted);
}

.mono {
  font-family: var(--font-mono);
}
</style>
