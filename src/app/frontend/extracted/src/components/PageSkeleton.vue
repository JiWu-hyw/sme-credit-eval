<script setup lang="ts">
/**
 * 页面级骨架屏（FE-04）
 *
 * 只负责「页面大概长什么样」的占位，具体什么时候出现由 `useAsyncSection`
 * 的 300ms 延迟阈值决定——本组件被渲染出来时，就一定已经等了 >300ms。
 *
 * 骨架的行高刻意贴近真实排版（标题 24 / 正文 13 / 图表块按规范高度），
 * 目的在于数据到达时布局不跳动（CLS），而不是做动画表演。
 */
import SkeletonBlock from './SkeletonBlock.vue'
import { CHART_HEIGHT } from '@/charts/sizes'

withDefaults(
  defineProps<{
    /** KPI 卡片个数，默认 4（P1/P4 的首屏） */
    kpiCount?: number
    /** 图表占位块高度，默认按规范给一条折线 + 一个力导向 */
    charts?: Array<number>
  }>(),
  { kpiCount: 4, charts: () => [CHART_HEIGHT.line, CHART_HEIGHT.force] },
)
</script>

<template>
  <div class="page-skeleton" aria-busy="true" aria-live="polite" data-testid="page-skeleton">
    <span class="sr-only">正在加载页面内容</span>

    <div class="page-skeleton__head">
      <SkeletonBlock :width="180" :height="24" />
      <SkeletonBlock :width="420" :height="14" />
    </div>

    <div class="grid-12 page-skeleton__kpis">
      <div v-for="i in kpiCount" :key="i" class="col-3 page-skeleton__kpi">
        <SkeletonBlock :width="88" :height="12" />
        <SkeletonBlock :width="120" :height="28" />
      </div>
    </div>

    <div class="page-skeleton__charts">
      <div
        v-for="(h, i) in charts"
        :key="i"
        class="page-skeleton__chart"
        :style="{ height: typeof h === 'number' ? `${h}px` : '320px' }"
      >
        <SkeletonBlock :width="140" :height="14" />
        <SkeletonBlock width="100%" :height="Math.max(80, (typeof h === 'number' ? h : 320) - 64)" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.page-skeleton__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.page-skeleton__kpi {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.page-skeleton__charts {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.page-skeleton__chart {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-sizing: border-box;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
