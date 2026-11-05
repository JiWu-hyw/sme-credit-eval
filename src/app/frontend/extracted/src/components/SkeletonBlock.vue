<script setup lang="ts">
/**
 * 骨架屏基元（FE-04）
 *
 * 只做「一块灰」这件事，布局由调用方（PageSkeleton / 页面）决定。
 * 不写死色值：底色与高光都由 `--color-surface-muted` 与 `--color-text` 混出，
 * 满足 README 硬约束 5（组件内禁止写死色值）。
 *
 * 尊重 `prefers-reduced-motion`：动效只做一次淡入脉动，不影响前庭敏感用户。
 */
withDefaults(
  defineProps<{
    /** 宽度：数字按 px，字符串原样（如 '60%'） */
    width?: number | string
    /** 高度（px） */
    height?: number
    /** 圆角覆盖，默认取 --radius-sm */
    radius?: string
    /** 圆形占位（头像 / 状态点） */
    circle?: boolean
  }>(),
  { width: '100%', height: 16, radius: undefined, circle: false },
)

function toSize(v: number | string): string {
  return typeof v === 'number' ? `${v}px` : v
}
</script>

<template>
  <span
    class="skeleton-block"
    :class="{ 'skeleton-block--circle': circle }"
    :style="{
      width: toSize(width),
      height: circle ? toSize(width) : `${height}px`,
      borderRadius: circle ? '50%' : radius,
    }"
    aria-hidden="true"
  />
</template>

<style scoped>
.skeleton-block {
  display: block;
  border-radius: var(--radius-sm);
  background-color: var(--color-surface-muted);
  background-image: linear-gradient(
    90deg,
    transparent 0%,
    color-mix(in srgb, var(--color-text) 6%, transparent) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: skeleton-sweep 1.4s ease-in-out infinite;
}

@keyframes skeleton-sweep {
  0% {
    background-position: 120% 0;
  }
  100% {
    background-position: -20% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-block {
    animation: none;
    background-image: none;
  }
}
</style>
