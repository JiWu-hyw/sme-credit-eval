/**
 * 前后端共享契约类型（前端侧镜像）。
 * 字段名与库表 / 接口契约保持一致，**不做 camelCase 转换**。
 *
 * 权威来源：
 * - 《可视化清单与图表数据契约》v1.0 §6.2–6.6（meta 必备字段、dataset 枚举、
 *   噪声地板、字段语义约束、视觉约定）
 * - 《前端页面与交互原型说明》v1.0 §6（21 接口与统一响应包）
 *
 * FE-02 会在本文件基础上补齐 21 个接口的请求 / 响应 data 结构。
 */

import { RISK_BANDS } from '../styles/theme'

/** 数据来源枚举（契约 §6.3，红线一：三者不得同图混排） */
export type DatasetKind = 'real_panel' | 'synthetic_graph' | 'mixed'

/** dataset → 角标中文文案（契约 §6.3；FE-03 DatasetBadge 消费） */
export const DATASET_LABELS: Record<DatasetKind, string> = {
  real_panel: '真实信贷面板',
  synthetic_graph: '合成图数据',
  mixed: '混合（真实 + 合成）',
}

/** 角标基调：真实面板中性呈现；合成 / 混合必须警示（FE-03 强制，不可关闭） */
export type DatasetTone = 'neutral' | 'warning'

/** dataset → 角标基调（契约 §6.3：合成数据用于架构验证，不能与实证结论同观感） */
export const DATASET_TONE: Record<DatasetKind, DatasetTone> = {
  real_panel: 'neutral',
  synthetic_graph: 'warning',
  mixed: 'warning',
}

/**
 * 契约顶层必备 meta（契约 §6.2）。
 * 后端**原样透传**，前端不得推断、补全或缓存旧值（契约 §6「meta.dataset 取值变化时
 * 页面必须同步更新数据来源提示，不得缓存旧提示」）。
 */
export interface ContractMeta {
  /** ISO-8601 时间戳 */
  generated_at?: string
  /** 产出该契约的脚本相对路径 */
  script?: string
  /** 数据来源，决定角标形态 */
  dataset?: DatasetKind
  /** 样本量（企业数或记录数） */
  n_samples?: number
  /** 随机种子，保证可复现 */
  seed?: number
  /** 契约版本，当前 1.0 */
  schema_version?: string

  /** 置换对照噪声地板（契约 §6.4；真实信贷面板当前已知参考值 0.477） */
  noise_floor?: number | null

  /** 字段语义是否已知（契约 §6.5）；false 时只允许显示原始代号（如 x037） */
  semantic_known?: boolean

  /** 业务因子名的间接引用（契约 §6.5）：指向未来的字段字典，禁止前端硬编码业务名 */
  label_ref?: string | null

  /** 预留：契约可透传任意附加元信息 */
  [key: string]: unknown
}

/** 统一响应包（契约 §6 约定） */
export interface ApiEnvelope<T = unknown> {
  code: string | number
  message: string
  data: T
  meta?: ContractMeta
}

/** 契约文件清单（契约 §6.1：统一落在 outputs/contracts/，snake_case.json） */
export type ContractFile =
  | 'data_pipeline.json'
  | 'data_quality.json'
  | 'panel_depth.json'
  | 'feature_tree.json'
  | 'graph_structure.json'
  | 'graph_slices.json'
  | 'graph_centrality.json'
  | 'train_curve.json'
  | 'benchmark.json'
  | 'roc_pr_ks.json'
  | 'permutation_control.json'
  | 'shap_global.json'
  | 'shap_top_features.csv'
  | 'stage_factor_weight.json'
  | 'survival.json'
  | 'financing.json'

/**
 * 五档风险色带的语义键（契约 §6.6）。
 * 色值一律来自 configs/viz_theme.yaml，组件内禁止写死（FE-01 负责生成 CSS 变量）。
 *
 * ⚠️ 键名**必须逐字镜像** viz_theme.yaml › risk_band.items[].code（该文件声明为颜色唯一来源，
 *    且「禁止前端另行定义」）。yaml v1.2 第 5 档 code 为 `high_risk`，不是 `high`。
 *    yaml 同档另带 `band`（1–5）/`reg_class`（正常类…损失类）/`color_bg`，theme.ts 已全量派生。
 */
export type RiskBandKey = 'excellent' | 'good' | 'watch' | 'warning' | 'high_risk'

/**
 * 五档语义标签 —— **从 theme.ts 派生**，不在此重复维护。
 * theme.ts 由 scripts/build_viz_theme.py 从 configs/viz_theme.yaml（v1.2）生成，
 * label / reg_class / 色值全部单一来源；改 yaml 后重跑生成器即全站同步。
 */
export const RISK_BAND_LABELS: Record<RiskBandKey, string> = Object.fromEntries(
  RISK_BANDS.map((b) => [b.code, b.label]),
) as Record<RiskBandKey, string>

/** 五档 → 五级分类（正常类…损失类），同源 theme.ts，供展示与导出用 */
export const RISK_REG_CLASS: Record<RiskBandKey, string> = Object.fromEntries(
  RISK_BANDS.map((b) => [b.code, b.reg_class]),
) as Record<RiskBandKey, string>

/**
 * 六类实体语义键（契约 §6.6）。
 * 颜色映射写入 configs/viz_theme.yaml，与算法侧 Python 绘图脚本共用，禁止前端另行定义。
 *
 * ⚠️ 键名**必须逐字镜像** viz_theme.yaml › entity_category 的键。
 *    当前 yaml v1.0「技术领域」的键是 `field`，不是 `tech_domain`。
 */
export type EntityKindKey = 'company' | 'patent' | 'investor' | 'person' | 'field' | 'product'

/** 六类实体中文名（用于图例文字，颜色不在此定义；键名同 viz_theme.yaml › entity_category） */
export const ENTITY_KIND_LABELS: Record<EntityKindKey, string> = {
  company: '企业',
  patent: '专利',
  investor: '投资机构',
  person: '核心人员',
  field: '技术领域',
  product: '产品',
}
