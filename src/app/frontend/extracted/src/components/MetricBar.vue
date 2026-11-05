<script setup lang="ts">
/**
 * 指标条（P4 / P5 共用）
 *
 * 只做一件事：把「数值 + 参照线」画在一条 0–1 的横轨上。
 * AUC 0.59 单独写出来没有体感，旁边摆一条 0.477 的地板、一条 0.5 的抛硬币线，
 * 读者立刻知道它离"没用"有多近——这正是本项目要传达的结论。
 */
const props = withDefaults(
  defineProps<{
    label: string
    /** 0–1 的数值 */
    value: number
    /** ±标准差（可选）：画成浅色误差带 */
    std?: number | null
    /** 参照线（如噪声地板 0.477、抛硬币 0.5） */
    refs?: Array<{ value: number; label: string; tone?: 'danger' | 'neutral' }>
    /** 数值后缀，默认保留 3 位小数 */
    digits?: number
    /** 高亮色：默认主色 */
    emphasize?: boolean
  }>(),
  { std: null, refs: () => [], digits: 3, emphasize: false },
)

const pct = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`
const fmt = (v: number) => v.toFixed(props.digits)

const lo = () => (props.std ? Math.max(0, props.value - props.std) : props.value)
const hi = () => (props.std ? Math.min(1, props.value + props.std) : props.value)
</script>

<template>
  <div class="metric-bar" :class="{ 'metric-bar--emph': emphasize }">
    <span class="metric-bar__label">{{ label }}</span>
    <span class="metric-bar__track" role="img" :aria-label="`${label} ${fmt(value)}`">
      <span
        v-if="std"
        class="metric-bar__band"
        :style="{ left: pct(lo()), width: pct(hi() - lo()) }"
      />
      <span class="metric-bar__fill" :style="{ width: pct(value) }" />
      <span
        v-for="r in refs"
        :key="r.label"
        class="metric-bar__ref"
        :data-tone="r.tone ?? 'neutral'"
        :style="{ left: pct(r.value) }"
        :title="`${r.label} ${r.value}`"
      />
    </span>
    <span class="metric-bar__value">
      {{ fmt(value) }}<template v-if="std"> <small>± {{ fmt(std) }}</small></template>
    </span>
  </div>
</template>

<style scoped>
.metric-bar {
  display: grid;
  grid-template-columns: 92px 1fr 92px;
  align-items: center;
  gap: var(--space-3);
  padding: 3px 0;
}

.metric-bar__label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.metric-bar__track {
  position: relative;
  height: 10px;
  border-radius: 5px;
  background: var(--color-surface-muted);
  border: 1px solid var(--color-border);
  overflow: hidden;
}

.metric-bar__band {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--color-accent-soft);
}

.metric-bar__fill {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  background: var(--color-accent);
  opacity: 0.85;
}

.metric-bar--emph .metric-bar__fill {
  background: var(--risk-high-risk);
}

.metric-bar__ref {
  position: absolute;
  top: -2px;
  bottom: -2px;
  width: 2px;
  background: var(--color-text-subtle);
}

.metric-bar__ref[data-tone='danger'] {
  background: var(--risk-high-risk);
}

.metric-bar__value {
  font-family: var(--font-mono);
  font-size: 12px;
  text-align: right;
  color: var(--color-text);
}

.metric-bar__value small {
  color: var(--color-text-subtle);
  font-size: 10px;
}
</style>
