/**
 * 蜂群图（beeswarm）纵向避让（FE-14）
 *
 * ECharts 没有原生 beeswarm。最常见的替代是「y 轴加随机抖动」，但随机抖动在
 * 密集区会把点糊成一团、在稀疏区又显得空，而且**每次重绘位置都不一样**——
 * 截图归档时同一张图两次结果不同，评审会以为数据变了。
 *
 * 这里用确定性的分箱避让（bin + symmetric dodge）：
 *   ① 沿 x 轴等宽分箱（默认 34 箱），点落在哪箱只看数值，不看随机数；
 *   ② 箱内按「离箱心由近及远」排，依次取 0、+d、−d、+2d、−2d … 的偏移；
 *   ③ 偏移上限固定为半个行高（0.34），所以密集箱里的点会更挤，而不是溢出到邻行。
 *
 * 结果：同一份数据永远得到同一张图，且密度可读 —— 这就是 beeswarm 想表达的
 * 「这个特征的贡献是集中在一小撮样本，还是均匀铺开」。
 */
import type { BeeswarmPoint } from '@/types/contracts'

export interface SwarmPoint {
  /** SHAP 值（x 轴） */
  x: number
  /** 行号 + 避让偏移（y 轴，单位是 category 索引） */
  y: number
  /** 特征值 Z-score（着色用） */
  value: number
  /** 原始点在数组中的下标（tooltip 追溯用） */
  index: number
}

export interface SwarmOptions {
  /** 分箱数量，默认 34：太少会把长尾糊成一条，太多则每箱只有 1–2 个点、看不出形状 */
  bins?: number
  /** 单侧最大偏移（单位：行高），默认 0.34 */
  maxOffset?: number
}

/** 所有行共用的 x 轴范围——逐行各算各的会让「同一段 SHAP 值」在不同行宽度不同，不可比 */
export function swarmRange(rows: BeeswarmPoint[][]): [number, number] {
  let lo = Infinity
  let hi = -Infinity
  for (const pts of rows) {
    for (const [x] of pts) {
      if (x < lo) lo = x
      if (x > hi) hi = x
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [-1, 1]
  if (lo === hi) return [lo - 0.5, hi + 0.5]
  return [lo, hi]
}

/**
 * 单行避让。
 *
 * @param points 该行的点（[shap, z]）
 * @param row 行号（ECharts category 轴索引）
 * @param range 全图共用的 x 范围
 */
export function layoutSwarm(
  points: BeeswarmPoint[],
  row: number,
  range: [number, number],
  options: SwarmOptions = {},
): SwarmPoint[] {
  const bins = options.bins ?? 34
  const maxOffset = options.maxOffset ?? 0.34
  const [lo, hi] = range
  const width = hi - lo || 1
  const step = width / bins

  const buckets: number[][] = Array.from({ length: bins }, () => [])
  points.forEach(([x], i) => {
    const raw = Math.floor((x - lo) / step)
    buckets[Math.min(bins - 1, Math.max(0, raw))].push(i)
  })

  const out: SwarmPoint[] = []
  buckets.forEach((idxList, b) => {
    if (idxList.length === 0) return
    const center = lo + step * (b + 0.5)
    // 离箱心近的先放中间，远的往外推 —— 形状才像蜂群而不是整齐的竖条
    const ordered = [...idxList].sort((a, c) => {
      const da = Math.abs(points[a][0] - center)
      const dc = Math.abs(points[c][0] - center)
      return da - dc || a - c
    })
    const slots = ordered.length
    // 偏移步长：让最外层的点正好落在 maxOffset；只有 1 个点时居中不偏移
    const d = slots === 1 ? 0 : maxOffset / Math.ceil(slots / 2)
    ordered.forEach((idx, k) => {
      const layer = Math.ceil(k / 2)
      const sign = k === 0 ? 0 : k % 2 === 1 ? 1 : -1
      const [x, value] = points[idx]
      out.push({ x, y: row + sign * layer * d, value, index: idx })
    })
  })

  return out
}
