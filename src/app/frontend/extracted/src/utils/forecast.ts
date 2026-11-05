/**
 * 前瞻预测的纯函数（FE-15）
 *
 * 为什么单拎出来：KM 的「按时间取阶梯值」、置信带的「上下界拼多边形」、
 * 校准分箱的「预测-观测配对」都是**能算错但画得出来**的东西 ——
 * 画歪了没人报错，图照样好看。抽成纯函数后，冒烟脚本可以 ssrLoadModule 直接
 * 拿契约数字对账（12 月生存率必须等于契约 `strata[].s12`），不用起浏览器。
 */

import type { CalibrationBin, SurvivalPoint } from "@/types/contracts";

/** 取 t 时刻的阶梯生存率（左连续：S(t) = 最后一个事件时间 ≤ t 的取值） */
export function survivalAt(
  points: SurvivalPoint[],
  t: number,
): { s: number; lo: number; hi: number; n_risk: number } | null {
  if (!points.length) return null;
  let hit = points[0];
  for (const p of points) {
    if (p[0] <= t + 1e-9) hit = p;
    else break;
  }
  return { s: hit[1], lo: hit[2], hi: hit[3], n_risk: hit[4] };
}

/** 风险集人数：报告里那行 at-risk 表（0/6/12/18/24 月）就靠它 */
export function atRiskAt(points: SurvivalPoint[], t: number): number {
  return survivalAt(points, t)?.n_risk ?? 0;
}

/**
 * 置信带多边形：沿下界正向走一遍，再沿上界反向走回来。
 *
 * 用 polygon 而不是「两条线 + stack」的常见写法，是为了让带子只有**一个** series：
 * stack 方案会往 tooltip 里塞两条假 series，读者悬停时会看到「低风险层 下界」这种
 * 根本不存在的图例项，还得在 formatter 里手动过滤 —— 不如一次画对。
 */
export function bandPolygon(points: SurvivalPoint[]): Array<[number, number]> {
  const lower: Array<[number, number]> = points.map((p) => [p[0], p[2]]);
  const upper: Array<[number, number]> = points
    .map((p): [number, number] => [p[0], p[3]])
    .reverse();
  return [...lower, ...upper];
}

/** 校准配对：`[预测均值, 观测值, 下界, 上界, 样本数]` */
export type CalibrationPoint = [number, number, number, number, number];

export function calibrationPoints(bins: CalibrationBin[]): CalibrationPoint[] {
  return bins.map((b) => [b.p_mean, b.obs, b.obs_lo, b.obs_hi, b.n]);
}

/**
 * p 值渲染口径。
 *
 * 契约里的 log-rank p 是 1.38e-14，`toFixed(3)` 会打印成 `0.000` ——
 * 「p = 0.000」在统计上是不可能出现的值，读者只会认为算错了。
 * 所以极小值一律写成 `< 0.001`，其余按三位小数。
 */
export function formatP(p: number | null | undefined): string {
  if (p === null || p === undefined || Number.isNaN(p)) return "—";
  if (p < 0.001) return "p < 0.001";
  return `p = ${p.toFixed(3)}`;
}

/** HR 的对数位置：森林图按 log 尺度铺，1.0 才是中点 */
export function logSpan(lo: number, hi: number): { min: number; max: number } {
  const a = Math.log(Math.max(lo, 1e-6));
  const b = Math.log(Math.max(hi, 1e-6));
  const pad = Math.max((b - a) * 0.08, 0.02);
  return { min: a - pad, max: b + pad };
}

/** 把 HR 映射到 0–100 的横向百分比（1.0 = 参考线位置） */
export function hrPercent(
  hr: number,
  span: { min: number; max: number },
): number {
  const v = Math.log(Math.max(hr, 1e-6));
  return ((v - span.min) / (span.max - span.min)) * 100;
}

/** 估值区间宽度倍数（P90/P10）：越小说明模型越"敢下结论" */
export function intervalWidth(b: { p10: number; p90: number }): number {
  return b.p10 > 0 ? b.p90 / b.p10 : 0;
}
