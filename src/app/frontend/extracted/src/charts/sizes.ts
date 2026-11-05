/**
 * 图表高度规范（FE-04，单一来源）
 *
 * 契约 §7.1：折线 / 柱状 320px，力导向 640px，热力图自适应。
 * 页面里**禁止手写 640 或 320**，一律引本常量——否则七页会各写各的高度。
 */
export const CHART_HEIGHT = {
  line: 320,
  bar: 320,
  force: 640,
  /** 热力图：由容器决定（行数多时纵向铺开，不强制 320） */
  heatmap: 'auto',
} as const

export type ChartHeight = (typeof CHART_HEIGHT)[keyof typeof CHART_HEIGHT]

/** 数字 → px 字符串；'auto' 原样返回 */
export function chartHeight(h: ChartHeight | number): string {
  return typeof h === 'number' ? `${h}px` : h
}
