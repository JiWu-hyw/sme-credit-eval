/**
 * P7 单企业深度诊断的纯函数层（FE-16）
 *
 * 抽出来的理由和 FE-14 的 beeswarm 一样：这一页最容易错的地方全在**数字怎么算**，
 * 而且这些错在截图上看不出来（瀑布图少一段、雷达轴画歪，肉眼都像对的）。
 * 放进 `utils/` 之后，冒烟脚本可以 `ssrLoadModule` 直接跑断言，不必起浏览器。
 *
 * 这里的四个函数分别对应四件容易出错的事：
 *   waterfallSegments —— 瀑布图的分段累加（最后一段必须精确落在总分上）
 *   gradientSort      —— 瀑布图的常规排序（SHAP 原生排序 = 按贡献方向分组）
 *   radarValues       —— 雷达三轴（缺数据的维度给 null，不是 0）
 *   gaugeOption       —— 信用分仪表盘（五档色带 + PD 指针）
 */

import { RISK_BANDS, type RiskBandKey } from '@/styles/theme'

/* ------------------------------- 五档 ------------------------------- */

export interface BandCut {
  /** 分数下限（含） */
  lo: number
  /** 分数上限（不含） */
  hi: number
  code: RiskBandKey
  label: string
  color: string
}

/**
 * 契约 cuts = [50, 60, 70, 80] → 五段，**分数越高越好**。
 *
 * ⚠️ `RISK_BANDS` 在 theme.ts 里是按「优良 → 高风险」排的（band 1 = 优良），
 * 而分数是升序的（0 分最差、100 分最好）——两者的顺序**相反**。
 * 这里必须 reverse 一次，否则 0–50 会被涂成「优良」的绿色、80–100 被涂成「高风险」的红色，
 * 仪表盘会对一家 95 分的企业显示「高风险」，档位序数也跟着全错。
 * 这个 bug 是 p7-smoke 的「risk_band 与五档切点自洽」那条断言抓出来的 ——
 * 图能画、色带也有五段，只有把契约里的 risk_band 字段拿来对数才看得见。
 */
export function bandCuts(cuts: number[]): BandCut[] {
  const edges = [0, ...cuts, 100]
  return [...RISK_BANDS].reverse().map((b, i) => ({
    lo: edges[i],
    hi: edges[i + 1],
    code: b.code,
    label: b.label,
    color: b.color,
  }))
}

export function bandOf(score: number, cuts: number[]): BandCut {
  const bands = bandCuts(cuts)
  return bands.find((b) => score >= b.lo && score < b.hi) ?? bands[bands.length - 1]
}

/* ------------------------------ 瀑布图 ------------------------------ */

export interface WaterfallInput {
  feature: string
  phi: number
  semantic_known?: boolean
}

export interface WaterfallSegment {
  /** 段名（字段代号或 E[f(x)] / f(x)） */
  key: string
  kind: 'feature' | 'base' | 'total'
  /** 起点（含） */
  from: number
  /** 终点 */
  to: number
  /** 本段增量（base / total 段为 null） */
  delta: number | null
  /** 绘制用：柱子的下沿 */
  low: number
  /** 绘制用：柱子的上沿 */
  high: number
}

/**
 * 把「基线 → 逐特征贡献 → 总分」拆成可累加的分段。
 *
 * 三条纪律：
 *   ① 最后一段的终点**必须**等于 `scoreLogit`（差 1e-6 就直接抛错，不画图）；
 *      瀑布图整张图的说服力全在「从基线出发，一段一段加过去正好到总分」，
 *      任何一处配平不上，读者核对一次就再也不会信这张图。
 *   ② base 段与 total 段是「从 0 长起来」的整柱，不是增量柱 —— 它们不是某个特征的贡献，
 *      用增量柱画出来会让人以为「基线也是被加上去的」。
 *   ③ 分段顺序按传入顺序（调用方一般先按 |φ| 降序），**不在这里重排**：
 *      排序规则属于交互（见 `gradientSort` 与页面上的排序开关），不属于计算。
 */
export function waterfallSegments(
  baseValue: number,
  features: WaterfallInput[],
  scoreLogit: number,
): WaterfallSegment[] {
  const out: WaterfallSegment[] = [
    { key: 'E[f(x)]', kind: 'base', from: 0, to: baseValue, delta: null, low: Math.min(0, baseValue), high: Math.max(0, baseValue) },
  ]
  let cursor = baseValue
  for (const f of features) {
    const from = cursor
    const to = cursor + f.phi
    out.push({
      key: f.feature,
      kind: 'feature',
      from,
      to,
      delta: f.phi,
      low: Math.min(from, to),
      high: Math.max(from, to),
    })
    cursor = to
  }
  // 显式检查累计误差：容忍 1e-6（浮点），超出即视为契约内部不自洽
  if (Math.abs(cursor - scoreLogit) > 1e-6) {
    throw new Error(
      `瀑布图配平失败：Σφ + E[f(x)] = ${cursor.toFixed(6)}，但 f(x) = ${scoreLogit.toFixed(6)}（差 ${(cursor - scoreLogit).toFixed(6)}）`,
    )
  }
  out.push({
    key: 'f(x)',
    kind: 'total',
    from: 0,
    to: scoreLogit,
    delta: null,
    low: Math.min(0, scoreLogit),
    high: Math.max(0, scoreLogit),
  })
  return out
}

/**
 * 常规排序（SHAP 论文默认）：风险因子在前、保护因子在后，组内按 |φ| 降序。
 *
 * 为什么要留这一档：按 |φ| 排序会把正负交替着排，读者看不出「这家企业总体是被推高还是被压低」；
 * 按方向分组后，前几段全是同向的推力，一眼就能看出结论。
 */
export function gradientSort<T extends { phi: number }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => {
    const sa = a.phi >= 0 ? 1 : 0
    const sb = b.phi >= 0 ? 1 : 0
    if (sa !== sb) return sb - sa
    return Math.abs(b.phi) - Math.abs(a.phi)
  })
}

/** 按 |φ| 降序（默认视图：谁影响最大排最前） */
export function magnitudeSort<T extends { phi: number }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => Math.abs(b.phi) - Math.abs(a.phi))
}

/* ------------------------------- 雷达图 ------------------------------- */

export interface RadarDim {
  factor_id: string
  factor_label: string
  items: Array<{ item_id: string; label: string; percentile: number | null; peer_median: number | null; has_data: boolean }>
}

export interface RadarAxis {
  /** 轴名（指标名） */
  name: string
  /** 本企业分位；null = 该维度缺数据 */
  self: number | null
  /** 同行中位数；null = 缺数据 */
  peer: number | null
  /** 所属维度（图例分组用） */
  dim: string
  /** 该轴是否真的有数据 */
  hasData: boolean
}

/**
 * 三维 × 四指标 → 雷达轴。
 *
 * **缺数据必须给 null，不能给 0。**
 * 画成 0 等于告诉读者「这家企业在技术要素上一无所有」，而事实是「这个维度还没有数据」
 * （P0-2 字段字典没到，面板里没有任何字段能归到该维度）。这是本页最不能含糊的一处：
 * 0 是一个判断，null 是一个事实。
 */
export function radarValues(radar: RadarDim[]): RadarAxis[] {
  const out: RadarAxis[] = []
  for (const dim of radar) {
    for (const it of dim.items) {
      const hasData = it.has_data && typeof it.percentile === 'number'
      out.push({
        name: it.label,
        self: hasData ? (it.percentile as number) : null,
        peer: hasData && typeof it.peer_median === 'number' ? it.peer_median : null,
        dim: dim.factor_label,
        hasData,
      })
    }
  }
  return out
}

/** 本页雷达是否至少有一根轴有数据（全空时整块降级，而不是画一个空心多边形） */
export function radarHasAny(axes: RadarAxis[]): boolean {
  return axes.some((a) => a.hasData && typeof a.self === 'number')
}

/** 缺数据维度占全部维度的比例（用于「数据覆盖度」卡片） */
export function dimCoverage(radar: RadarDim[]): Array<{ dim: string; withData: number; total: number }> {
  return radar.map((d) => ({
    dim: d.factor_label,
    withData: d.items.filter((i) => i.has_data && typeof i.percentile === 'number').length,
    total: d.items.length,
  }))
}

/* ------------------------------ 仪表盘 ------------------------------ */

/**
 * 信用分仪表盘（0–100 + 五档色带 + PD 指针）。
 *
 * 一个容易忽略的点：**色带的顺序必须与分数方向一致**。
 * 高风险（band=1 红色）在刻度最左边（0 分那一端），优良在最右。画反了整张图就在说反话。
 */
export function gaugeOption(score: number, cuts: number[], pdValue: number, bandLabel: string) {
  const bands = bandCuts(cuts)
  // 色带按分数从 0 → 100 依次着色：0–50 高风险（红）→ 80–100 优良（绿）。
  // 与 `bandCuts()` 同源推导，不在这里另写一份切点，避免「改了 cuts、图没跟着改」。
  const stops = bandCuts(cuts)
    .slice()
    .sort((a, b) => a.lo - b.lo)
    .map((b) => [b.hi / 100, b.color] as [number, string])

  return {
    animation: false,
    series: [
      {
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: 100,
        radius: '88%',
        center: ['50%', '62%'],
        splitNumber: 5,
        axisLine: {
          lineStyle: { width: 18, color: stops },
        },
        pointer: {
          width: 5,
          length: '62%',
          itemStyle: { color: '#1f2933' },
        },
        axisTick: { distance: -18, length: 5, lineStyle: { color: '#fff', width: 1 } },
        splitLine: { distance: -18, length: 18, lineStyle: { color: '#fff', width: 2 } },
        axisLabel: { distance: 22, fontSize: 10, color: '#5b6673' },
        detail: {
          valueAnimation: false,
          offsetCenter: [0, '38%'],
          fontSize: 30,
          fontWeight: 700,
          color: '#1f2933',
          formatter: (v: number) => v.toFixed(1),
        },
        title: { offsetCenter: [0, '70%'], fontSize: 12, color: '#5b6673' },
        data: [{ value: score, name: `${bandLabel} · PD ${(pdValue * 100).toFixed(2)}%` }],
      },
    ],
    // 供冒烟断言：色带切点是否与契约 cuts 一致
    __bands: bands.map((b) => [b.lo, b.hi, b.label]),
  }
}

/* ------------------------------ 时序热力 ------------------------------ */

export interface SeriesFeature {
  feature: string
  total: number
  by_month: number[]
}

export interface HeatCell {
  /** 列（月份下标） */
  x: number
  /** 行（特征下标） */
  y: number
  /** 该月该特征的贡献 = 总贡献 × 当月分摊比例 */
  value: number
}

/**
 * 逐月贡献热力图：每格 = 总贡献 × 当月分摊比例。
 *
 * 两个断言式约束（写在这里而不是注释里）：
 *   ① 每行分摊比例之和 = 1（差 1e-3 即抛错）—— 否则「逐月热点」加起来对不上总贡献；
 *   ② 行内格值之和 = 该特征总贡献（同上）。
 * 这两条一旦破掉，热力图就会在「月度」这一维上给出与瀑布图相反的故事。
 */
export function heatCells(series: SeriesFeature[]): HeatCell[] {
  const out: HeatCell[] = []
  series.forEach((f, y) => {
    const s = f.by_month.reduce((a, b) => a + b, 0)
    if (Math.abs(s - 1) > 1e-3) {
      throw new Error(`时序分摊比例之和不为 1：${f.feature} = ${s.toFixed(4)}`)
    }
    f.by_month.forEach((share, x) => {
      out.push({ x, y, value: Number((f.total * share).toFixed(4)) })
    })
  })
  return out
}

/* ------------------------------ 汇总 ------------------------------ */

/** 一句话结论：把「谁在推、谁在拉」讲成一句人话（不引入任何业务名） */
export function localVerdict(features: Array<{ feature: string; phi: number }>): {
  push: number
  pull: number
  topUp: string | null
  topDown: string | null
  net: number
} {
  const push = features.filter((f) => f.phi > 0).reduce((a, b) => a + b.phi, 0)
  const pull = features.filter((f) => f.phi < 0).reduce((a, b) => a + b.phi, 0)
  const up = magnitudeSort(features.filter((f) => f.phi > 0))[0]
  const down = magnitudeSort(features.filter((f) => f.phi < 0))[0]
  return {
    push: Number(push.toFixed(4)),
    pull: Number(pull.toFixed(4)),
    topUp: up ? up.feature : null,
    topDown: down ? down.feature : null,
    net: Number((push + pull).toFixed(4)),
  }
}
