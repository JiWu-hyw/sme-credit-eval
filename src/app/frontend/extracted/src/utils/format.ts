/**
 * 格式化工具（FE-11）
 *
 * P2 页大量展示「率」与「计数」，统一在这里处理，避免每张卡片各写一套 toFixed。
 */

/** 0.1234 → 12.3% */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

/** 千分位整数 */
export function num(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Math.round(v).toLocaleString('zh-CN')
}

/** 保留小数（默认 2 位） */
export function fixed(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

/** 缺失率分档 → 风险色 CSS 变量名（低 / 中 / 高 / 极高） */
export function missingTier(rate: number): 'low' | 'mid' | 'high' | 'severe' {
  if (rate >= 0.5) return 'severe'
  if (rate >= 0.3) return 'high'
  if (rate >= 0.1) return 'mid'
  return 'low'
}

/** 缺失率 → 底色（热力图用；用 color-mix 从风险色派生，不新增硬编码色值） */
export function missingColor(rate: number): string {
  const tier = missingTier(rate)
  const map: Record<string, string> = {
    low: 'color-mix(in srgb, var(--risk-good) 18%, var(--color-surface))',
    mid: 'color-mix(in srgb, var(--risk-watch) 28%, var(--color-surface))',
    high: 'color-mix(in srgb, var(--risk-warning) 45%, var(--color-surface))',
    severe: 'color-mix(in srgb, var(--risk-high-risk) 62%, var(--color-surface))',
  }
  return map[tier]
}

/** 节点状态 → 中文 & 语气 */
export const PIPELINE_STATUS: Record<string, { label: string; tone: 'ok' | 'partial' | 'blocked' | 'pending' | 'warn' }> = {
  ready: { label: '已就绪', tone: 'ok' },
  partial: { label: '部分就绪', tone: 'partial' },
  pending: { label: '待接入', tone: 'pending' },
  blocked: { label: '阻塞', tone: 'blocked' },
  synthetic_only: { label: '仅合成', tone: 'warn' },
}

export function statusMeta(status?: string) {
  return PIPELINE_STATUS[status ?? ''] ?? { label: status ?? '未知', tone: 'pending' as const }
}
