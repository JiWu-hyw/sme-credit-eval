<script setup lang="ts">
/**
 * 噪声地板注记（红线三，FE-03）
 *
 * 契约 §6.4：用真实面板给性能结论的图，必须同屏呈现置换对照噪声地板。
 * 真实信贷面板行级 AUC≈0.59，打乱标签后的噪声地板 0.477 ——
 * 不把这条写在图上，读者会把 0.59 当成「模型挺准」，实际上离抛硬币没多远。
 */
const props = withDefaults(
  defineProps<{
    floor?: number | null
    /** 同图参照的真实指标，用于算「高出地板多少」 */
    reference?: number | null
  }>(),
  { floor: 0.477, reference: null },
)

const floorText = () =>
  props.floor === null || props.floor === undefined ? '—' : props.floor.toFixed(3)
/**
 * 本图对照指标（FE-13 修正）
 *
 * 旧实现里 `deltaText()`（判断是否显示"本图指标"）与 `delta()`（真正的差值）各写了一遍
 * 同样的三元表达式，前者返回的是 `reference.toFixed(3)` 字符串、后者返回差值——
 * 于是注记变成"本图指标 0.586，高出地板 0.109"，同一句话里两个"指标"是不同含义，极易误读。
 * 现在统一从 `reference` 推导：本图指标 / 高出地板，各说各的，不再混用。
 */
const referenceText = () =>
  props.reference === null || props.reference === undefined ? null : props.reference.toFixed(3)

const delta = () =>
  props.reference === null || props.reference === undefined || props.floor === null || props.floor === undefined
    ? null
    : (props.reference - props.floor)
</script>

<template>
  <p class="noise-note" data-testid="noise-floor-note">
    <span class="noise-note__tag">噪声地板</span>
    置换对照（打乱标签后重训）AUC = <strong>{{ floorText() }}</strong>，低于此值即与随机无异。
    <span v-if="referenceText() !== null">
      本图指标 {{ referenceText() }}，高出地板
      <strong>{{ delta() !== null ? (delta()! >= 0 ? '+' : '') + delta()!.toFixed(3) : '—' }}</strong>。
    </span>
  </p>
</template>

<style scoped>
.noise-note {
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid var(--risk-watch);
  background: var(--risk-watch-bg);
  color: var(--color-text-muted);
  font-size: 12px;
  line-height: 1.6;
}

.noise-note__tag {
  display: inline-block;
  margin-right: 6px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--risk-watch);
  color: var(--color-surface);
  font-size: 11px;
}

.noise-note strong {
  font-family: var(--font-mono);
  color: var(--color-text);
}
</style>
