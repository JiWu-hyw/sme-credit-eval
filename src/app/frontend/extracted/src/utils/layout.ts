/**
 * 布局常量与断点（FE-04，契约 §7.1）
 *
 * 为什么这些数字要单独成文件而不是散落在各组件里：
 *   「最宽 1440 / 最窄 1024 / 1024 以下折叠侧边导航」是**验收项**，
 *   散落写死后无法被测试与文档同时引用。这里定义一次，
 *   CSS 令牌（styles/tokens.css › --layout-*）与脚本（scripts/layout-smoke.mjs）共用。
 *
 * 注意：CSS 侧的真值仍是 tokens.css 里的自定义属性；
 * 本文件只镜像数值，供 TS 逻辑（如抽屉开关的 matchMedia 判断）使用。
 * 若改断点，**两处必须同时改**，否则会出现「CSS 已折、JS 还以为没折」的错位。
 */

/** 栅格列数：宽屏 12 列（契约 §7.1） */
export const GRID_COLUMNS = 12

/** 断点（单位 px，均为 max-width 语义） */
export const BREAKPOINTS = {
  /** < 1024：顶部导航收为侧边抽屉，正文可低于 1024 不再横向撑开 */
  compact: 1024,
  /** < 720：12 列塌成单列，卡片纵向堆叠 */
  stack: 720,
} as const

/** 布局尺寸（与 tokens.css › --layout-* 一一对应） */
export const LAYOUT = {
  maxWidth: 1440,
  minWidth: 1024,
  /** 顶部导航高度；与 App.vue › .app-header 的 height 保持一致 */
  headerHeight: 64,
  /** 侧边抽屉宽度 */
  drawerWidth: 288,
} as const

/** matchMedia 查询串，避免各组件重复手写 */
export const MEDIA = {
  compact: `(max-width: ${BREAKPOINTS.compact - 1}px)`,
  stack: `(max-width: ${BREAKPOINTS.stack - 1}px)`,
} as const

/** 视口是否已进入「折叠侧边导航」区间 */
export function isCompactViewport(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia(MEDIA.compact).matches
}

/**
 * 栅格跨列校验：超界直接报错而不是静默截断。
 * 页面里写 `:span="13"` 属于笔误，静默截成 12 会让缺陷藏到上线后。
 */
export function assertSpan(span: number): number {
  if (!Number.isInteger(span) || span < 1 || span > GRID_COLUMNS) {
    throw new RangeError(`栅格跨列必须为 1–${GRID_COLUMNS} 的整数，收到 ${span}`)
  }
  return span
}
