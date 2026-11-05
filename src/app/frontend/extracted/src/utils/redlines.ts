/**
 * 红线运行期守卫（FE-03）
 *
 * 三条红线的最后一层保险：
 *   1. 同一图表不得混排 real_panel 与 synthetic_graph（契约 §6.3）——
 *      真实面板 AUC≈0.59、合成图≈0.91，量级与含义完全不同，混在一起就是误导；
 *   2. 语义未知字段不得被包装成业务名（契约 §6.5）；
 *   3. 真实面板性能图必须带噪声地板（契约 §6.4）。
 *
 * 这些条件在类型层拦不住（都是运行期数据），所以做成显式断言，
 * 由页面在组装 series 前调用，失败直接抛错而不是静默画出来。
 */
import type { DatasetKind } from '@/types'

/** 真实面板的置换对照噪声地板（契约 §6.4，当前已知值） */
export const NOISE_FLOOR = 0.477

export class RedLineError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'RedLineError'
  }
}

/**
 * 断言同一图表只用一个数据来源。
 * `mixed` 表示「明确标注的混合来源」，它本身不能与其他来源并存。
 */
export function assertSingleDataset(datasets: DatasetKind[]): DatasetKind {
  const unique = Array.from(new Set(datasets))
  if (unique.length > 1) {
    throw new RedLineError(
      `同一图表混排了多个数据来源（${unique.join(' / ')}）：real_panel 与 synthetic_graph 结果不得同图（契约 §6.3）。`,
    )
  }
  return unique[0] ?? 'real_panel'
}

/** 是否形如 x037 的语义未知字段代号 */
export function isUnknownFieldCode(field: string): boolean {
  return /^x[_-]?\d+$/i.test(field.trim())
}

/**
 * 断言字段展示合法：语义未知时只能显示代号。
 * 传入一个「业务名」而不是代号，说明调用方在包装未知字段，直接抛错。
 */
export function assertFieldDisplayable(field: string, semanticKnown: boolean): void {
  if (!semanticKnown && !isUnknownFieldCode(field)) {
    throw new RedLineError(
      `字段「${field}」语义未知，但展示值不是原始代号：语义未确认字段只允许显示代号（契约 §6.5）。`,
    )
  }
}

/** 真实面板性能图是否满足「同屏噪声地板」要求 */
export function hasNoiseFloor(dataset: DatasetKind, floor: number | null | undefined): boolean {
  return dataset !== 'real_panel' || typeof floor === 'number'
}
