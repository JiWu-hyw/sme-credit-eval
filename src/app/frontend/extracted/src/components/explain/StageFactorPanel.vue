<script setup lang="ts">
/**
 * A15 阶段-因子-权重动态决策图谱 —— FE-14
 *
 * 这是申报书承诺的核心卖点：「同一个模型，在企业不同生命阶段看不同的东西」。
 * 表现形式选 100% 堆叠面积图（契约允许山脊图或堆叠面积，这里选后者）：
 *   - x 轴是阶段，面积高度是**该阶段内各维度权重之和为 100%**，所以横向可直接比结构；
 *   - 阶段之间用直线连（不 smooth）：没有证据支持"权重连续漂移"，平滑曲线会把
 *     4 个离散口径画成一条光滑规律，是过度解读；
 *   - 点/区可点选，右侧明细跟着换 —— 这就是"动态"的部分。
 *
 * ⚠️ 本区块的数据是 mock（meta.contract_status = 'mock'）：
 *   阶段划分、维度权重、阶段内字段权重都是占位；字段排序与 mean|SHAP| 取自实测。
 *   所以每个数字旁边都挂了「实测 / 占位」小标 —— 让读者一眼看出哪些能引用。
 */
import { computed, ref } from 'vue'

import ChartPanel from '@/components/ChartPanel.vue'
import UnknownFieldTag from '@/components/UnknownFieldTag.vue'
import type { StageFactorWeightContract } from '@/types/contracts'
import type { DatasetKind } from '@/types'

const props = defineProps<{
  data: StageFactorWeightContract
  dataset: DatasetKind
  /** meta.source，角标由父级渲染，这里只用于文案判断 */
  source?: string
}>()

/* 三个维度的颜色（数据编码色，取自 theme.css 的实体色，不在正文硬编码业务含义） */
const FACTOR_COLOR: Record<string, string> = {
  tech: '#2f5d8a', // --entity-company
  growth: '#c08a1e', // --entity-investor
  fusion: '#7a5fa8', // --entity-patent
}

const stages = computed(() => props.data.stages)
const factors = computed(() => props.data.factors)
const activeStageId = ref(stages.value[0]?.stage_id ?? '')

const activeStage = computed(
  () => stages.value.find((s) => s.stage_id === activeStageId.value) ?? stages.value[0] ?? null,
)

/** 阶段 → 因子权重，缺项按 0 补，保证堆叠面积每列高度都是 100% */
const weightOf = (stageId: string, factorId: string) =>
  props.data.weights.find((w) => w.stage_id === stageId && w.factor_id === factorId)?.weight ?? 0

const stageTotal = (stageId: string) =>
  factors.value.reduce((sum, f) => sum + weightOf(stageId, f.factor_id), 0)

const activeWeights = computed(() => {
  if (!activeStage.value) return []
  const sid = activeStage.value.stage_id
  return [...factors.value]
    .map((f) => ({ ...f, weight: weightOf(sid, f.factor_id) }))
    .sort((a, b) => b.weight - a.weight)
})

const activeFields = computed(() => {
  if (!activeStage.value) return []
  return (
    props.data.stage_fields.find((s) => s.stage_id === activeStage.value?.stage_id)?.fields ?? []
  )
})

const activeNote = computed(
  () => props.data.decision_notes.find((n) => n.stage_id === activeStage.value?.stage_id) ?? null,
)

const pct = (v: number) => `${(v * 100).toFixed(1)}%`

const option = computed(() => ({
  animation: false,
  grid: { left: 58, right: 26, top: 44, bottom: 58 },
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'line', lineStyle: { color: '#b9c0c9' } },
    formatter: (items: Array<{ dataIndex: number; seriesName: string; value: number }>) => {
      const idx = items[0]?.dataIndex ?? 0
      const stage = stages.value[idx]
      if (!stage) return ''
      const lines = factors.value.map((f) => {
        const w = weightOf(stage.stage_id, f.factor_id)
        return `${f.factor_label} ${(w * 100).toFixed(0)}%`
      })
      return [`<b>${stage.stage_label}</b>（占位口径）`, ...lines].join('<br/>')
    },
  },
  legend: {
    bottom: 4,
    itemHeight: 8,
    itemWidth: 12,
    textStyle: { fontSize: 11, color: '#5b6673' },
  },
  xAxis: {
    type: 'category',
    // 留白（true）：首末阶段不贴边，当前阶段那条虚线才看得出是"选中"，而不是坐标轴
    boundaryGap: true,
    data: stages.value.map((s) => s.stage_label),
    axisLabel: { fontSize: 11, color: '#1f2933' },
    axisTick: { show: false },
  },
  yAxis: {
    type: 'value',
    min: 0,
    max: 100,
    name: '权重占比 %',
    nameLocation: 'end',
    nameGap: 14,
    nameTextStyle: { fontSize: 10, color: '#5b6673', align: 'left' },
    axisLabel: { fontSize: 10, color: '#5b6673', formatter: '{value}%' },
    splitLine: { lineStyle: { color: '#eef1f5' } },
  },
  series: factors.value.map((f) => ({
    name: f.factor_label,
    type: 'line',
    stack: 'weight',
    // 不 smooth：4 个离散阶段之间没有"连续漂移"的证据，平滑曲线会造出不存在的规律
    smooth: false,
    symbol: 'circle',
    symbolSize: 7,
    lineStyle: { width: 1.6, color: FACTOR_COLOR[f.factor_id] ?? '#5b6673' },
    itemStyle: { color: FACTOR_COLOR[f.factor_id] ?? '#5b6673' },
    areaStyle: { color: FACTOR_COLOR[f.factor_id] ?? '#5b6673', opacity: 0.22 },
    emphasis: { focus: 'series' },
    // 数据项带上 stage_id：面积图点一下就能切阶段（ChartPanel 把 click 透传上来）
    data: stages.value.map((s) => ({
      value: Number((weightOf(s.stage_id, f.factor_id) * 100).toFixed(1)),
      stage_id: s.stage_id,
    })),
    // 当前阶段竖线：告诉读者右侧明细讲的是哪一列
    markLine: {
      silent: true,
      symbol: 'none',
      lineStyle: { color: '#1f2933', width: 1, type: 'dashed', opacity: 0.45 },
      label: { show: false },
      data: activeStage.value ? [{ xAxis: activeStage.value.stage_label }] : [],
    },
  })),
}))

function onChartClick(payload: { dataType?: string; data?: Record<string, unknown> }) {
  const d = payload.data as { stage_id?: string } | undefined
  if (d?.stage_id) activeStageId.value = d.stage_id
}
</script>

<template>
  <div class="stage-factor">
    <!-- 阶段切换：4 个阶段既是口径说明，也是交互入口 -->
    <div class="stage-chips" role="group" aria-label="选择生命周期阶段" data-testid="stage-chips">
      <button
        v-for="s in stages"
        :key="s.stage_id"
        type="button"
        class="stage-chip"
        :class="{ 'is-active': s.stage_id === activeStageId }"
        :aria-pressed="s.stage_id === activeStageId"
        :data-testid="`stage-chip-${s.stage_id}`"
        @click="activeStageId = s.stage_id"
      >
        <span class="stage-chip__label">{{ s.stage_label }}</span>
        <span class="stage-chip__meta">{{ s.age_range }}</span>
        <span class="stage-chip__share">占 {{ (s.company_share * 100).toFixed(0) }}%</span>
      </button>
      <span class="stage-chips__hint">点面积或按钮切换阶段 · 权重口径为占位</span>
    </div>

    <div class="grid-12">
      <div id="a15-chart" class="col-7">
        <ChartPanel
          title="A15 阶段 × 因子权重（100% 堆叠面积，4 阶段 × 3 维度）"
          :dataset="dataset"
          :option="option"
          :height="340"
          @chart-click="onChartClick"
        >
          <p class="note">
            <b>怎么读：</b>每一列高度恒为 100%，比较的是<b>结构</b>而不是总量；虚线是当前选中阶段。
            阶段间用直线连接——4 个离散口径之间没有「连续漂移」的证据，画成平滑曲线就是过度解读。
          </p>
        </ChartPanel>
      </div>

      <div id="a15-weights" class="col-5">
        <section class="stage-detail" aria-labelledby="stage-weights" data-testid="stage-weights">
          <header class="stage-detail__head">
            <h3 id="stage-weights" class="stage-detail__title">
              {{ activeStage?.stage_label ?? '—' }} · 因子权重
            </h3>
            <span class="tag tag--mock">占位</span>
          </header>
          <p class="stage-detail__def">
            {{ activeStage?.age_range }} · {{ activeStage?.funding_marker }} · 阶段内合计
            {{ pct(stageTotal(activeStageId)) }}
          </p>

          <ul class="factor-list">
            <li v-for="f in activeWeights" :key="f.factor_id" class="factor">
              <div class="factor__row">
                <span class="factor__dot" :style="{ background: FACTOR_COLOR[f.factor_id] }" />
                <span class="factor__label">{{ f.factor_label }}</span>
                <span class="factor__value">{{ pct(f.weight) }}</span>
              </div>
              <div class="factor__track">
                <i
                  class="factor__fill"
                  :style="{ width: `${f.weight * 100}%`, background: FACTOR_COLOR[f.factor_id] }"
                />
              </div>
              <p class="factor__def">
                {{ f.definition }}
                <span class="factor__warn">{{ f.note }}</span>
              </p>
            </li>
          </ul>
        </section>
      </div>
    </div>

    <!-- 阶段内字段明细：实测 mean|SHAP| 与占位阶段权重并列，逐列标口径 -->
    <section id="a15-fields" class="stage-fields" aria-labelledby="stage-fields-title">
      <header class="stage-fields__head">
        <h3 id="stage-fields-title" class="stage-fields__title">
          {{ activeStage?.stage_label }} · 字段层明细（前 {{ activeFields.length }} 位）
        </h3>
        <span class="stage-fields__hint">
          排序与 mean|SHAP| <span class="tag tag--real">实测</span> ·
          阶段权重 <span class="tag tag--mock">占位</span>
        </span>
      </header>
      <div class="table-scroll">
        <table class="tbl">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">字段</th>
              <th scope="col">mean|SHAP|</th>
              <th scope="col">方向</th>
              <th scope="col">阶段权重</th>
              <th scope="col" class="tbl__wide">阶段权重分布</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in activeFields" :key="f.feature">
              <td class="mono">{{ f.rank }}</td>
              <th scope="row">
                <UnknownFieldTag :field="f.feature" :semantic-known="f.semantic_known" />
              </th>
              <td class="mono">{{ f.mean_abs_shap.toFixed(4) }}</td>
              <td>
                <span class="dir" :data-dir="f.direction">
                  {{ f.direction > 0 ? '风险 +' : '保护 −' }}
                </span>
              </td>
              <td class="mono">{{ pct(f.stage_weight) }}</td>
              <td>
                <span class="mini-track">
                  <i class="mini-fill" :style="{ width: `${Math.min(100, f.stage_weight * 300)}%` }" />
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="activeNote" class="stage-note" data-testid="stage-note">
        <b>{{ activeNote.headline }}。</b>{{ activeNote.text }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.stage-factor {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.stage-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.stage-chip {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-height: 44px;
  padding: 5px var(--space-3);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: left;
  cursor: pointer;
}

.stage-chip:hover {
  border-color: var(--color-accent);
}

.stage-chip.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  box-shadow: inset 0 0 0 1px var(--color-accent);
}

.stage-chip__label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--color-text);
}

.stage-chip__meta,
.stage-chip__share {
  font-size: 10.5px;
  color: var(--color-text-subtle);
}

.stage-chips__hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.stage-detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  height: 100%;
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.stage-detail__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.stage-detail__title {
  font-size: 14px;
  font-weight: 600;
}

.stage-detail__def {
  font-size: 11.5px;
  color: var(--color-text-subtle);
}

.factor-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.factor__row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.factor__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.factor__label {
  font-size: 12.5px;
  color: var(--color-text);
}

.factor__value {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 12.5px;
  font-weight: 600;
}

.factor__track {
  height: 6px;
  margin-top: 4px;
  border-radius: 3px;
  background: var(--color-surface-muted);
  overflow: hidden;
}

.factor__fill {
  display: block;
  height: 100%;
  border-radius: 3px;
}

.factor__def {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--color-text-muted);
}

.factor__warn {
  display: block;
  color: var(--risk-watch);
}

.tag {
  padding: 0 5px;
  border-radius: 8px;
  border: 1px solid currentColor;
  font-size: 10px;
  line-height: 15px;
  white-space: nowrap;
}

.tag--mock {
  color: var(--risk-watch);
}

.tag--real {
  color: var(--risk-good);
}

.note {
  margin-top: var(--space-2);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--color-text-subtle);
}

.stage-fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.stage-fields__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.stage-fields__title {
  font-size: 14px;
  font-weight: 600;
}

.stage-fields__hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.table-scroll {
  overflow: auto;
}

.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.tbl th,
.tbl td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border);
  text-align: right;
  white-space: nowrap;
}

.tbl thead th {
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-surface-muted);
}

.tbl th[scope='col']:nth-child(2),
.tbl th[scope='row'],
.tbl__wide {
  text-align: left;
}

.mono {
  font-family: var(--font-mono);
}

.dir {
  font-size: 11px;
  padding: 0 5px;
  border-radius: 8px;
  border: 1px solid currentColor;
}

.dir[data-dir='1'] {
  color: var(--risk-high-risk);
}

.dir[data-dir='-1'] {
  color: var(--color-accent);
}

.mini-track {
  display: block;
  width: 120px;
  height: 6px;
  border-radius: 3px;
  background: var(--color-surface-muted);
  overflow: hidden;
}

.mini-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--color-accent);
  opacity: 0.65;
}

.stage-note {
  padding: var(--space-3);
  border-left: 3px solid var(--risk-watch);
  border-radius: var(--radius-sm);
  background: var(--risk-watch-bg);
  font-size: 12px;
  line-height: 1.75;
  color: var(--color-text-muted);
}
</style>
