<script setup lang="ts">
/**
 * 占位页（P1 / P3 / P6 / P7 尚未实现时复用）
 *
 * 不做「正在建设中」这种空话——按契约写清三件事：这页负责什么、
 * 依赖哪个接口、为什么现在还没有。评审点进来也能看懂进度。
 *
 * 已交付的页面列在 DELIVERED 里，占位页会把它们作为「先去哪儿看」的链接，
 * 免得评审点进一个空页之后不知道哪里才有真东西。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import StatePanel from '@/components/StatePanel.vue'

/** 已交付的真实页面（占位页给导航用） */
const DELIVERED = [
  { name: 'overview', code: 'P1', label: '概览' },
  { name: 'data', code: 'P2', label: '数据与特征' },
  { name: 'graph', code: 'P3', label: '时空图谱' },
  { name: 'model', code: 'P4', label: '模型与对比' },
  { name: 'explain', code: 'P5', label: '可解释性' },
  { name: 'forecast', code: 'P6', label: '前瞻预测' },
  { name: 'enterprise', code: 'P7', label: '单企业深度诊断' },
  { name: 'batch', code: 'P8', label: '批量评估' },
] as const

const route = useRoute()
const code = computed(() => String(route.meta.code ?? '—'))
const title = computed(() => String(route.meta.title ?? '页面不存在'))
const description = computed(() => String(route.meta.description ?? ''))
const is404 = computed(() => code.value === '404')
</script>

<template>
  <div class="placeholder stack">
    <header class="placeholder__head">
      <span class="placeholder__code">{{ code }}</span>
      <h1 class="placeholder__title">{{ title }}</h1>
    </header>

    <StatePanel
      v-if="is404"
      variant="empty"
      title="没有这个页面"
      reason="地址拼写有误，或该页面已迁移。"
      next-step="从顶部导航重新进入；P1 概览的地址是 /，不是 /overview。"
    />
    <template v-else>
      <StatePanel
        variant="pending"
        title="该页面尚未实现"
        :reason="`${code} ${title}：${description}。本页由对应 FE 任务交付，当前已交付的是 P2 / P4 / P5 三页。`"
        next-step="先从下面三个已交付页面看起；本页按 FE-10 ~ FE-16 顺序补齐。"
      />
      <nav class="placeholder__delivered" aria-label="已交付页面">
        <span class="placeholder__delivered-label">已交付：</span>
        <RouterLink v-for="d in DELIVERED" :key="d.name" :to="{ name: d.name }" class="placeholder__link">
          <span class="placeholder__link-code">{{ d.code }}</span>
          {{ d.label }}
        </RouterLink>
      </nav>
    </template>
  </div>
</template>

<style scoped>
.placeholder__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}

.placeholder__code {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 12px;
}

.placeholder__title {
  font-size: 22px;
  font-weight: 700;
}

.placeholder__delivered {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-muted);
}

.placeholder__delivered-label {
  font-size: 12px;
  color: var(--color-text-muted);
}

.placeholder__link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--color-accent);
  text-decoration: none;
}

.placeholder__link:hover,
.placeholder__link:focus-visible {
  text-decoration: underline;
}

.placeholder__link-code {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  font-family: var(--font-mono);
  font-size: 11px;
}
</style>
