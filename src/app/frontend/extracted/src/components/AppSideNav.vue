<script setup lang="ts">
/**
 * 侧边抽屉导航（FE-04）——仅 `<1024px` 区间使用
 *
 * 为什么不是「把顶部导航塞进侧栏」：
 *   窄屏下 P1–P7 的文字标签比编号重要，抽屉里改为「编号 + 标题 + 一句话职责」，
 *   这样评审在小屏上也能知道每一页负责什么，而不是面对七个 P 编号猜。
 *
 * 可访问性（契约 §7.5）：
 *   - role="dialog" + aria-modal，Esc 关闭；
 *   - 打开时焦点移入第一个导航项，关闭时由 App.vue 还原到汉堡按钮；
 *   - 打开时锁 body 滚动，避免背景仍在滚；
 *   - 遮罩可点击关闭。
 *
 * SSR 安全：所有 window / document 访问都做了 `typeof` 守卫，
 * 因为 `npm run test:routes` 走的是服务端渲染断言，没有 document。
 */
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import type { NavItem } from '@/router'

const props = withDefaults(
  defineProps<{
    items: NavItem[]
    activeName?: string
    open?: boolean
  }>(),
  { activeName: undefined, open: false },
)

const emit = defineEmits<{ close: [] }>()

const panelRef = ref<HTMLElement | null>(null)
let restoreOverflow = ''

function lockScroll() {
  if (typeof document === 'undefined') return
  restoreOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
}

function unlockScroll() {
  if (typeof document === 'undefined') return
  document.body.style.overflow = restoreOverflow
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}

watch(
  () => props.open,
  async (isOpen) => {
    if (typeof window === 'undefined') return
    if (isOpen) {
      lockScroll()
      window.addEventListener('keydown', onKeydown)
      await nextTick()
      // 焦点落到抽屉内的第一个可聚焦元素
      const first = panelRef.value?.querySelector<HTMLElement>('a, button')
      first?.focus()
    } else {
      unlockScroll()
      window.removeEventListener('keydown', onKeydown)
    }
  },
)

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
  unlockScroll()
})
</script>

<template>
  <div v-if="open" class="drawer">
    <div class="drawer__backdrop" @click="emit('close')" />

    <aside
      id="app-drawer"
      ref="panelRef"
      class="drawer__panel"
      role="dialog"
      aria-modal="true"
      aria-label="页面导航"
      data-testid="app-drawer"
    >
      <div class="drawer__head">
        <p class="drawer__title">页面导航</p>
        <button type="button" class="drawer__close" aria-label="关闭导航" @click="emit('close')">
          ×
        </button>
      </div>

      <nav class="drawer__nav">
        <RouterLink
          v-for="item in items"
          :key="item.name"
          class="drawer__link"
          :class="{ 'drawer__link--active': activeName === item.name }"
          :to="{ name: item.name }"
          :aria-current="activeName === item.name ? 'page' : undefined"
          @click="emit('close')"
        >
          <span class="drawer__code">{{ item.meta.code }}</span>
          <span class="drawer__body">
            <span class="drawer__label">{{ item.meta.title }}</span>
            <span class="drawer__desc">{{ item.meta.description }}</span>
          </span>
        </RouterLink>
      </nav>
    </aside>
  </div>
</template>

<style scoped>
.drawer {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
}

.drawer__backdrop {
  position: absolute;
  inset: 0;
  /* 遮罩由 --color-bg 混出，不写死黑色（README 硬约束 5） */
  background: color-mix(in srgb, var(--color-bg) 68%, transparent);
}

.drawer__panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 288px;
  max-width: 86vw;
  height: 100%;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  box-shadow: var(--shadow-md);
  overflow-y: auto;
}

.drawer__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.drawer__title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-muted);
}

.drawer__close {
  width: var(--hit-target-min);
  height: var(--hit-target-min);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}

.drawer__nav {
  display: flex;
  flex-direction: column;
  padding: var(--space-2);
  gap: 2px;
}

.drawer__link {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  min-height: var(--hit-target-min);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  color: var(--color-text-muted);
  text-decoration: none;
}

.drawer__link:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}

.drawer__link--active {
  border-color: var(--color-border-strong);
  background: var(--color-surface-muted);
  color: var(--color-text);
}

.drawer__link:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.drawer__code {
  flex: 0 0 auto;
  padding-top: 2px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-text-subtle);
}

.drawer__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.drawer__label {
  font-size: 13px;
  font-weight: 600;
}

.drawer__desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-subtle);
}
</style>
