/**
 * 主题派生（FE-01 / FE-11 子集实现）
 *
 * 单一事实源是 configs/viz_theme.yaml（v1.2），生产环境由
 * scripts/build_viz_theme.py 生成 theme.css / theme.ts。
 * 本仓库为 FE-11 可独立运行的最小实现，按 yaml 的**同一套键名与档位**手工镜像一份常量，
 * 供 TS 逻辑（风险档位、实体色、涨跌色）引用；CSS 侧变量见 styles/theme.css。
 *
 * ⚠️ 改色值必须改 yaml 后重跑生成器，不要只改本文件（否则与后端绘图脚本不一致）。
 */

export interface RiskBand {
  code: 'excellent' | 'good' | 'watch' | 'warning' | 'high_risk'
  band: 1 | 2 | 3 | 4 | 5
  label: string
  reg_class: string
  color: string
  color_bg: string
}

/** 风险五档（viz_theme.yaml › risk_band.items，键名逐字一致） */
export const RISK_BANDS: RiskBand[] = [
  { code: 'excellent', band: 1, label: '优良', reg_class: '正常类', color: '#1f7a4d', color_bg: '#e8f4ee' },
  { code: 'good', band: 2, label: '较好', reg_class: '正常类', color: '#4c9a5c', color_bg: '#eef7f0' },
  { code: 'watch', band: 3, label: '关注', reg_class: '关注类', color: '#c98a12', color_bg: '#fdf5e2' },
  { code: 'warning', band: 4, label: '预警', reg_class: '次级类', color: '#d0622a', color_bg: '#fdeee6' },
  { code: 'high_risk', band: 5, label: '高风险', reg_class: '损失类', color: '#b3312c', color_bg: '#fbe9e8' },
]

export type RiskBandKey = RiskBand['code']

export const RISK_COLOR_BY_CODE: Record<string, string> = Object.fromEntries(
  RISK_BANDS.map((b) => [b.code, b.color]),
)

export const RISK_COLOR_BY_BAND: Record<number, string> = Object.fromEntries(
  RISK_BANDS.map((b) => [b.band, b.color]),
)

/** 六类实体（viz_theme.yaml › entity_category，键名逐字一致：技术领域是 field） */
export const ENTITY_COLORS: Record<string, string> = {
  company: '#2f5d8a',
  patent: '#7a5fa8',
  investor: '#c08a1e',
  person: '#3f8f7a',
  field: '#8a6d3b',
  product: '#a0506e',
}

/** 涨跌色（A 股惯例：涨红跌绿） */
export const MARKET_COLORS = { up: '#b3312c', down: '#1f7a4d', flat: '#7a7a7a' }

/** 合规免责声明（全站页脚/角标复用） */
export const COMPLIANCE_DISCLAIMER =
  '本页数值仅用于原型系统演示与架构验证，不构成任何信贷决策依据。'
