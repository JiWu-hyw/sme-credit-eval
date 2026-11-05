<script setup lang="ts">
/**
 * 应用外壳（FE-04）
 *
 * 契约 §7.1 的落地清单：
 *   1. 顶部导航横向切换 P1–P7；<1024 折叠为侧边抽屉；
 *   2. 12 列响应式栅格：≥1024 十二列、<1024 六列、<720 单列；正文最宽 1440；
 *   3. 骨架屏由 `useAsyncSection` 的 300ms 阈值统一控制（见 PageSkeleton）；
 *   4. 空态 / 错误态统一走 `StatePanel`（原因 + 下一步，错误态带重试）；
 *   5. 断网与懒加载 chunk 失败**不整页白屏**——这层是本文件的兜底职责。
 *
 * 兜底为什么要放在 App 而不是各页面：
 *   页面级 try/catch 只管得住自己，管不住「路由懒加载 chunk 取不到」
 *   和「某个页面的 setup 直接抛错」。这两类事故的表现都是白屏，
 *   必须由外壳接住并给出可读原因，否则评审现场断网就是一片空白。
 */
import { computed, onErrorCaptured, ref, shallowRef, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import AppSideNav from '@/components/AppSideNav.vue'
import AppTopNav from '@/components/AppTopNav.vue'
import StatePanel from '@/components/StatePanel.vue'
import { useOnlineStatus } from '@/composables/useAsyncSection'
import { NAV_ROUTES } from '@/router'
import { toReadableError } from '@/utils/errors'

const route = useRoute()
const router = useRouter()

/** 窄屏抽屉开关（≥1024 时无意义，抽屉组件本身也不会被唤出） */
const drawerOpen = ref(false)

/** 渲染期异常 / 懒加载失败的兜底信息；null 表示正常 */
const fatal = shallowRef<{ reason: string; nextStep: string; detail: string } | null>(null)
/** 递增即强制重建 RouterView（错误态「重试」用） */
const viewKey = ref(0)

const { online } = useOnlineStatus()

const activeName = computed(() => (typeof route.name === 'string' ? route.name : undefined))

/** 当前页说明，给页脚用；404 时没有 PageMeta，降级为标题 */
const currentMeta = computed(() => {
  const meta = route.meta as { code?: string; title?: string }
  return {
    code: meta.code ?? '—',
    title: meta.title ?? '页面不存在',
  }
})

function isChunkError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  return /dynamically imported module|Importing a module script failed|Loading chunk/i.test(msg)
}

function captureError(err: unknown) {
  // 错误态页面只给「原因 + 下一步」，原始异常仍然要留在控制台，便于现场排查。
  console.error('[app-captured]', err)
  const readable = toReadableError(err)
  fatal.value = isChunkError(err)
    ? {
        reason: '页面代码没有加载成功（网络中断或发版后旧 chunk 已失效）。',
        nextStep: '点「重新加载」整页刷新；若刚发版，刷新即可取到新版本。',
        detail: readable.detail,
      }
    : readable
}

// 渲染期异常：子组件 setup / render 抛错
onErrorCaptured((err) => {
  captureError(err)
  return false // 已在外壳处理，不再向上冒泡到控制台中断
})

// 路由懒加载失败：只靠 onErrorCaptured 接不住，必须挂 router.onError
router.onError((err) => {
  captureError(err)
})

function retryFatal() {
  const chunkFailure = fatal.value?.reason.includes('页面代码没有加载成功') ?? false
  if (chunkFailure && typeof window !== 'undefined') {
    window.location.reload()
    return
  }
  fatal.value = null
  viewKey.value += 1
}

/** 切页即收抽屉，避免跳转后遮罩仍压在新页面上 */
watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false
  },
)

/** 文档标题随页面走（评审截图归档时靠它认页） */
watch(
  () => currentMeta.value,
  (meta) => {
    if (typeof document !== 'undefined') {
      document.title = `${meta.code} ${meta.title} · 中小科技企业信用智能评估系统`
    }
  },
  { immediate: true },
)

function closeDrawer() {
  drawerOpen.value = false
  // 焦点还原到汉堡按钮，键盘用户不会掉回文档开头
  if (typeof document !== 'undefined') {
    document.querySelector<HTMLElement>('.app-header__menu')?.focus()
  }
}
</script>

<template>
  <div class="app-shell">
    <a class="app-shell__skip" href="#main">跳到主内容</a>

    <AppTopNav
      :items="NAV_ROUTES"
      :active-name="activeName"
      :drawer-open="drawerOpen"
      @toggle-drawer="drawerOpen = !drawerOpen"
    >
      <template #extra>
        <span v-if="!online" class="app-shell__offline" role="status">离线</span>
      </template>
    </AppTopNav>

    <AppSideNav
      :items="NAV_ROUTES"
      :active-name="activeName"
      :open="drawerOpen"
      @close="closeDrawer"
    />

    <!-- 断网提示：不阻断浏览（mock 数据仍可看），只说明当前处于离线态 -->
    <p v-if="!online" class="app-shell__banner" role="status">
      当前处于离线状态：已加载的页面仍可浏览，新数据请求会失败。
    </p>

    <main id="main" class="app-main" tabindex="-1">
      <!-- 兜底错误态：整页替换，但保留导航，用户能直接换页而不是卡死 -->
      <StatePanel
        v-if="fatal"
        variant="error"
        title="当前页面无法显示"
        :reason="fatal.reason"
        :next-step="fatal.nextStep"
        :detail="fatal.detail"
        :retry="retryFatal"
        retry-text="重新加载"
      />

      <!--
        ⚠️ key 用 `route.path` 而不是 `route.fullPath`：后者把 query 也算进去，
        P8 每轮询一次就 `?job=xxx&t=...` 换个 query，整页组件会被重新挂载 ——
        表现是「提交完 100 行名单，页面闪一下回到空导入区」，进度条永远停在第一帧。
        路径变了（换页）才需要重建实例，query 变化交给页面自己响应。
      -->
      <RouterView v-else v-slot="{ Component }">
        <component :is="Component" :key="`${viewKey}-${route.path}`" />
      </RouterView>
    </main>

    <footer class="app-footer">
      <span class="app-footer__meta">原型系统 v0.1 · {{ currentMeta.code }} {{ currentMeta.title }}</span>
      <span class="app-footer__rule">数据口径红线：真实信贷面板结果与合成异质图结果不得同图混排</span>
    </footer>
  </div>
</template>

<style>
/* ==========================================================================
   全局：重置 + 12 列响应式栅格（FE-04）
   放在 App.vue 的**非 scoped** 样式块里，是为了让子页面直接用类名铺版，
   而不必每页各写一套 grid；同时避免改动 styles/index.css 的既有 import 链。
   ========================================================================== */

html,
body {
  margin: 0;
  padding: 0;
  background: var(--color-bg);
}

body {
  font-family: var(--font-sans);
  color: var(--color-text);
  -webkit-font-smoothing: antialiased;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}

:where(a, button, [tabindex]):focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

/* ---------- 12 列栅格 ---------- */
.grid-12 {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: var(--space-4);
}

.col-1,
.col-2,
.col-3,
.col-4,
.col-5,
.col-6,
.col-7,
.col-8,
.col-9,
.col-10,
.col-11,
.col-12 {
  min-width: 0;
}

.col-1 {
  grid-column: span 1;
}
.col-2 {
  grid-column: span 2;
}
.col-3 {
  grid-column: span 3;
}
.col-4 {
  grid-column: span 4;
}
.col-5 {
  grid-column: span 5;
}
.col-6 {
  grid-column: span 6;
}
.col-7 {
  grid-column: span 7;
}
.col-8 {
  grid-column: span 8;
}
.col-9 {
  grid-column: span 9;
}
.col-10 {
  grid-column: span 10;
}
.col-11 {
  grid-column: span 11;
}
.col-12 {
  grid-column: span 12;
}

/* <1024：十二列压成六列，原来占满宽度的块仍然占满 */
@media (max-width: 1023px) {
  .grid-12 {
    grid-template-columns: repeat(6, minmax(0, 1fr));
  }

  .col-7,
  .col-8,
  .col-9,
  .col-10,
  .col-11,
  .col-12 {
    grid-column: span 6;
  }

  .col-5,
  .col-6 {
    grid-column: span 3;
  }

  .col-4 {
    grid-column: span 3;
  }

  .col-3 {
    grid-column: span 3;
  }

  .col-2,
  .col-1 {
    grid-column: span 2;
  }
}

/* <720：单列堆叠，卡片不再并排 */
@media (max-width: 719px) {
  .grid-12 {
    grid-template-columns: minmax(0, 1fr);
  }

  .grid-12 > * {
    grid-column: 1 / -1;
  }
}

/* ---------- 通用排版工具 ---------- */
.stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.cluster {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

/* 宽表格的唯一允许滚动容器：整页不横向滚动，局部可滚 */
.table-scroll {
  width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
</style>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background: var(--color-bg);
  color: var(--color-text);
}

.app-shell__skip {
  position: absolute;
  left: -9999px;
  top: 0;
  z-index: 50;
}

.app-shell__skip:focus {
  left: var(--space-4);
  top: var(--space-4);
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  text-decoration: none;
}

.app-shell__offline {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  font-size: 11px;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.app-shell__banner {
  margin: 0;
  padding: var(--space-2) var(--space-6);
  background: var(--color-surface-muted);
  border-bottom: 1px solid var(--color-border);
  font-size: 12px;
  color: var(--color-text-muted);
}

/* 正文：最宽 1440，左右留白；窄屏收窄内边距 */
.app-main {
  flex: 1 1 auto;
  width: 100%;
  max-width: var(--layout-max-width);
  margin: 0 auto;
  padding: var(--space-6);
  outline: none;
}

.app-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-6);
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
  font-size: 12px;
  color: var(--color-text-subtle);
}

.app-footer__rule {
  color: var(--color-text-muted);
}

@media (max-width: 1023px) {
  .app-main {
    padding: var(--space-4);
  }

  .app-shell__banner {
    padding: var(--space-2) var(--space-4);
  }

  .app-footer {
    padding: var(--space-3) var(--space-4);
  }
}
</style>
