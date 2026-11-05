<script setup lang="ts">
/**
 * 顶部导航（FE-04 组件，FE-11 依赖）
 *
 * 约定：
 *   - 一律 `{ name }` 跳转：P7 是动态路由，拼 path 会拼错；
 *   - 导航条自身横向滚动，不换行挤压品牌区；
 *   - 窄屏（<1024）右上角汉堡按钮打开侧边抽屉（44×44 命中区）。
 */
import { RouterLink } from 'vue-router'

import type { NavItem } from '@/router'

withDefaults(
  defineProps<{
    items: NavItem[]
    activeName?: string
    drawerOpen?: boolean
  }>(),
  { activeName: undefined, drawerOpen: false },
)

defineEmits<{ 'toggle-drawer': [] }>()
</script>

<template>
  <header class="app-header">
    <div class="app-header__brand">
      <img
        class="app-header__logo"
        src="/brand/logo-mark.svg"
        alt=""
        width="26"
        height="26"
        aria-hidden="true"
      />
      <span class="app-header__title">中小科技企业信用智能评估系统</span>
      <span class="app-header__version">v0.1</span>
    </div>

    <nav class="app-header__nav" aria-label="页面导航">
      <RouterLink
        v-for="item in items"
        :key="item.name"
        class="app-header__item"
        :class="{ 'app-header__item--active': activeName === item.name }"
        :to="{ name: item.name }"
        :aria-current="activeName === item.name ? 'page' : undefined"
        :title="item.meta.description"
        data-testid="nav-item"
      >
        <span class="app-header__code">{{ item.meta.code }}</span>
        <span class="app-header__label">{{ item.meta.title }}</span>
      </RouterLink>
      <slot name="extra" />
    </nav>

    <button
      type="button"
      class="app-header__menu"
      aria-label="打开导航"
      :aria-expanded="drawerOpen"
      @click="$emit('toggle-drawer')"
    >
      ☰
    </button>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: 64px;
  padding: 0 var(--space-6);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  z-index: 20;
}

.app-header__brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 0 0 auto;
  white-space: nowrap;
}

.app-header__logo {
  display: block;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  flex: 0 0 auto;
}

.app-header__title {
  font-size: 15px;
  font-weight: 600;
}

.app-header__version {
  font-size: 11px;
  color: var(--color-text-subtle);
  font-family: var(--font-mono);
}

.app-header__nav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
  overflow-x: auto;
  scrollbar-width: thin;
}

.app-header__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--hit-target-min);
  padding: 0 var(--space-3);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  text-decoration: none;
  white-space: nowrap;
  font-size: 13px;
}

.app-header__item:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}

.app-header__item--active {
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}

.app-header__code {
  font-family: var(--font-mono);
  font-size: 11px;
  opacity: 0.7;
}

.app-header__menu {
  display: none;
  width: var(--hit-target-min);
  height: var(--hit-target-min);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 16px;
  cursor: pointer;
}

@media (max-width: 1023px) {
  .app-header {
    padding: 0 var(--space-4);
  }

  .app-header__nav {
    display: none;
  }

  .app-header__menu {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    margin-left: auto;
  }
}
</style>
