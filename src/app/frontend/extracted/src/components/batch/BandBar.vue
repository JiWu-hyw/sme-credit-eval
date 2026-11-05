<script setup lang="ts">
/**
 * 五档色带（S6 / P8 共用）
 *
 * 为什么抽出来：P7 的仪表盘把它画在 ECharts 里，P8 的批量结果需要一条**可点的过滤器**。
 * 两处若各画一遍，切点一旦改动（50/60/70/80）就会出现「仪表盘说 79 分是较好、列表筛出来却是关注」。
 * 所以色带由 `RISK_BANDS` + `cuts` 派生，两页共用同一份派生结果。
 *
 * 交互（默认关闭，`clickable` 打开）：点击某段切换该档的筛选 ——
 * 这是批量页最常用的动作（"只看高风险的"），没必要再放一个下拉框。
 */
import { computed } from 'vue'

import { RISK_BANDS } from '@/styles/theme'

const props = withDefaults(
  defineProps<{
    /** 切点，默认 P7 契约的 [50, 60, 70, 80] */
    cuts?: number[]
    /** 各档计数（有就显示在段内） */
    counts?: Record<string, number> | null
    /** 是否可点选 */
    clickable?: boolean
    /** 当前选中（空数组 = 不限） */
    selected?: string[]
    /** 紧凑模式：结果表抬头用 */
    compact?: boolean
    /**
     * 组件标识。同一页面上会出现两条色带（进度区的只读分布 + 结果表的可点筛选），
     * testid 不带标识时冒烟脚本会同时选中两条，点到那只读的按钮上（disabled，点不动）。
     */
    k?: string
  }>(),
  { cuts: () => [50, 60, 70, 80], counts: null, clickable: false, selected: () => [], compact: false, k: 'band' },
)

const emit = defineEmits<{ toggle: [code: string] }>()

/**
 * 分数升序的五段：0–49 高风险 → 80–100 优良。
 * `RISK_BANDS` 在 theme.ts 里按「优良 → 高风险」排（band 1 = 优良），与分数升序**相反**，
 * 这里 reverse 一次；与 utils/enterprise.ts › bandCuts 的修法完全一致（那边踩过这个坑）。
 */
const bands = computed(() => {
  const edges = [0, ...props.cuts, 100]
  const asc = [...RISK_BANDS].reverse() // 高风险 … 优良
  return asc.map((b, i) => ({
    ...b,
    lo: edges[i],
    hi: edges[i + 1],
    count: props.counts?.[b.code] ?? null,
  }))
})

function onToggle(code: string) {
  if (!props.clickable) return
  emit('toggle', code)
}
</script>

<template>
  <div class="band" :class="{ 'band--compact': compact }" :data-testid="`risk-band-bar-${k}`">
    <button
      v-for="b in bands"
      :key="b.code"
      type="button"
      class="band__seg"
      :class="{ 'band__seg--on': selected.includes(b.code), 'band__seg--off': selected.length > 0 && !selected.includes(b.code) }"
      :style="{ '--seg': b.color, '--seg-bg': b.color_bg }"
      :disabled="!clickable"
      :aria-pressed="clickable ? selected.includes(b.code) : undefined"
      :data-band="b.code"
      @click="onToggle(b.code)"
    >
      <span class="band__label">{{ b.label }}</span>
      <span class="band__range">{{ b.lo }}–{{ b.hi }}</span>
      <span v-if="b.count !== null" class="band__count">{{ b.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.band {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  width: 100%;
  min-width: 0;
}

.band__seg {
  /* 40px 是「能放下 4 个字 + 计数」的最小实用宽度；再窄就换行而不是把字挤没 */
  flex: 1 1 40px;
  min-width: 0;
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 6px;
  min-height: 34px;
  padding: var(--space-2) var(--space-1);
  border: 1px solid var(--seg);
  border-radius: var(--radius-sm);
  background: var(--seg-bg);
  color: var(--seg);
  font-size: 12px;
  cursor: default;
  transition: opacity 0.12s ease;
}

.band__seg:not(:disabled) {
  cursor: pointer;
}

.band__seg:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.band__seg--on {
  box-shadow: inset 0 0 0 2px var(--seg);
}

.band__seg--off {
  opacity: 0.42;
}

.band__label {
  font-weight: 600;
}

.band__range,
.band__count {
  font-family: var(--font-mono);
  font-size: 10.5px;
  opacity: 0.85;
}

.band__count::before {
  content: '· ';
}

.band--compact .band__seg {
  min-height: 26px;
  padding: 2px var(--space-1);
  font-size: 11px;
}

.band--compact .band__range {
  display: none;
}
</style>
