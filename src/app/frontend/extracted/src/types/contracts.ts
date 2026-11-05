/**
 * 契约类型镜像（FE-02 / FE-11 扩充）。
 *
 * 纪律（fe-spec §6）：字段名逐字对齐 outputs/contracts/*.json，**不做 camelCase 转换**、
 * **不简化结构**。契约文件是单一事实源，这里只是镜像；真实契约落地后如有出入，
 * 以 outputs/contracts/*.json 为准回改本文件。
 *
 * FE-11 补齐 P2「数据与特征」四区块：
 *   DataPipeline（A01）/ DataQuality（A02）/ PanelDepth（A03）/ FeatureTree（A04）
 */

import type { ContractMeta, DatasetKind, EntityKindKey, RiskBandKey } from './index'

export type { ContractMeta, DatasetKind, EntityKindKey, RiskBandKey }

/** 统一响应包 */
export interface ApiEnvelope<T = unknown> {
  code: string | number
  message: string
  data: T
  meta?: ContractMeta
}

/* ---------------------------- A01 数据接入链路 ---------------------------- */

export type PipelineNodeKind = 'source' | 'process' | 'dataset'

/** 节点状态：ready 已就绪 / partial 部分 / pending 待接入 / blocked 阻塞 / synthetic_only 仅合成 */
export type PipelineStatus = 'ready' | 'partial' | 'pending' | 'blocked' | 'synthetic_only'

export interface PipelineNode {
  id: string
  name: string
  kind: PipelineNodeKind
  status?: PipelineStatus
  description?: string
}

export interface PipelineEdge {
  from: string
  to: string
  label?: string
  status?: PipelineStatus
}

export interface PipelineStep {
  order: number
  name: string
  impl: string
  detail: string
  status: PipelineStatus
}

export interface DataPipeline {
  nodes: PipelineNode[]
  edges: PipelineEdge[]
  steps?: PipelineStep[]
}

/* ----------------------------- A02 数据质量 ------------------------------ */

export interface QualityField {
  field: string
  semantic_known: boolean
  label_ref?: string
  missing_rate: number
  winsorized: boolean
  dtype?: string
  n_non_null?: number
  skew_after?: number
  outlier_rate?: number
}

export interface MissingCell {
  field: string
  semantic_known: boolean
  year: string
  missing_rate: number
}

/** 缩尾前后箱线图数据（q05/q25/q50/q75/q95） */
export interface WinsorBox {
  field: string
  semantic_known: boolean
  stage: 'before' | 'after'
  q05: number
  q25?: number
  q50: number
  q75?: number
  q95: number
  n_trimmed?: number
}

export interface QualitySummary {
  n_fields: number
  n_fields_missing_gt_30pct: number
  mean_missing_rate: number
  semantic_known_ratio: number
  rows_total: number
  enterprises: number
  winsor_bounds?: { lower_pct: number; upper_pct: number; fit_on: string }
  split_rule?: string
}

export interface DataQuality {
  summary?: QualitySummary
  fields: QualityField[]
  missing_matrix: MissingCell[]
  winsor: WinsorBox[]
}

/* ---------------------------- A03 面板观测纵深 ---------------------------- */

export interface DepthBin {
  range: string
  lo?: number
  hi?: number
  enterprises: number
  observations: number
}

export interface DepthCoverage {
  months_total: number
  enterprises_with_panel: number
  median_months: number
  mean_months?: number
  p90_months?: number
  observations_total?: number
  long_sequence_feasible?: boolean
  temporal_unit?: string
}

export interface PanelDepth {
  bins: DepthBin[]
  coverage: DepthCoverage
}

/* ----------------------------- A04 特征体系树 ----------------------------- */

export interface FeatureLeafDetail {
  dtype: string
  missing_rate: number
  winsorized: boolean
  standardized: boolean
  fit_scope: string
  in_graph: boolean
  shap_rank: number | null
  semantic_status: string
  semantic_note: string
}

export interface FeatureLeaf {
  kind: 'leaf'
  field: string
  semantic_known: boolean
  label_ref: string
  type: string
  missing_rate: number
  used_in_model: boolean
  note?: string
  detail?: FeatureLeafDetail
}

export interface FeatureBranch {
  kind: 'branch'
  name: string
  code?: string
  level?: number
  hint?: string
  /** true / false / 'partial'（部分就绪） */
  data_ready?: boolean | 'partial'
  children: FeatureNode[]
}

export type FeatureNode = FeatureBranch | FeatureLeaf

export interface FeatureFlatRow {
  field: string
  semantic_known: boolean
  path: string
  type: string
  missing_rate: number
  used_in_model: boolean
}

export interface FeatureTreeSummary {
  level1: number
  level2: number
  leaves: number
  used_in_model: number
  semantic_known_leaves: number
  data_ready_branches: number
  blocked_by: string[]
}

export interface FeatureTree {
  roots: FeatureNode[]
  flat?: FeatureFlatRow[]
  summary?: FeatureTreeSummary
}

/* ------------------------- P4 模型与对比（实测基准） ------------------------- */

/** 一套指标（holdout 用全六项；CV 只产出 auc/ks/accuracy/recall/f1） */
export interface MetricSet {
  auc: number
  ks: number
  accuracy: number
  precision?: number
  recall: number
  f1: number
}

export interface BenchmarkModelCv {
  model_id: string
  model_label: string
  metrics_mean: MetricSet
  metrics_std: MetricSet
}

export interface BenchmarkLevel {
  level_id: string
  level_label: string
  n_samples: number
  n_groups: number
  n_splits: number
  k_features: number
  is_permutation_control: boolean
  models: BenchmarkModelCv[]
}

export interface BenchmarkModelHoldout {
  model_id: string
  model_label: string
  metrics: MetricSet
  threshold: number
  best_f1: number
  best_f1_threshold: number
}

export interface Benchmark {
  levels: BenchmarkLevel[]
  holdout: {
    n_train: number
    n_test: number
    n_pos: number
    selected_features: string[]
    models: BenchmarkModelHoldout[]
  }
  noise_floor: {
    value: number
    label: string
    source: string
    raw?: Record<string, number>
    note?: string
  }
  notes: string[]
  /* ---- FE-13 扩展（无损追加，旧字段语义不变） ---- */
  model_ids?: string[]
  grouped?: BenchmarkGrouped
  radar?: BenchmarkRadar
  ablation?: AblationBlock
  matrix?: BenchmarkMatrix
  conclusion?: BenchmarkConclusion
}

/* --------------------------- P5 全局 SHAP 解释 --------------------------- */

export interface ShapFeatureRow {
  rank: number
  feature: string
  semantic_known: boolean
  mean_abs_shap: number
  share: number
  /** +1 风险因子（值越大越推高违约概率）/ −1 保护因子 */
  direction: number
  hint: string
}

/**
 * 蜂群图的单点：`[SHAP 值, 特征值 Z-score]`。
 *
 * 用二元组而不是对象：一行 200 个点、15 行 × 2 个模型 = 6000 个点，
 * 写成 `{shap:…, value:…}` 会让契约多出 3 倍体积，而这两个数没有任何
 * 需要按名访问的场合。索引含义由 `BeeswarmMeta.value_axis` 说明，不靠猜。
 */
export type BeeswarmPoint = [number, number]

/** 单个特征的蜂群数据（FE-14） */
export interface ShapBeeswarmFeature {
  rank: number
  feature: string
  semantic_known: boolean
  /** 实测 mean|SHAP|（契约锚点） */
  mean_abs_shap: number
  /** 实测方向：+1 风险因子 / −1 保护因子 */
  direction: number
  share: number
  /** 特征值与 SHAP 值的秩相关（重建约束的校验值，符号须等于 direction） */
  rho_feature_shap: number
  points: BeeswarmPoint[]
}

/**
 * 蜂群图口径（meta.beeswarm_source 决定角标语气）。
 *
 * ⚠️ 逐样本 SHAP 值未随契约落盘，点位是 `reconstructed_from_anchors`：
 * 均值与方向是实测的，分布形状是重建的。页面必须同屏标注，不得只说"实测"。
 */
export interface BeeswarmMeta {
  n_total: number
  n_plotted: number
  n_features: number
  sampling: string
  value_axis: string
  value_axis_note: string
  derived: boolean
  source: string
  derivation: string
  max_mean_deviation: number
}

export interface ShapModelBlock {
  model_id: string
  model_label: string
  top: ShapFeatureRow[]
  top20_share: number
  unknown_field_share: number
  n_features: number
  /** FE-14 追加：Top-15 特征 × n_plotted 点 */
  beeswarm?: ShapBeeswarmFeature[]
  beeswarm_meta?: BeeswarmMeta
}

export interface ShapGlobal {
  explain_set: string
  n_features_used: number
  models: ShapModelBlock[]
  conclusion: string
  /** FE-14 追加：蜂群图覆盖范围的一句话描述 */
  beeswarm_scope?: string
}

/* --------------------- P5 阶段-因子-权重（A15，mock 契约） --------------------- */

/**
 * A15 的真实契约要等 P0-2（字段字典）+ P0-5（融资事件）。
 * 现阶段 `meta.contract_status === 'mock'`：阶段划分与权重是占位，
 * 字段排序与 mean|SHAP| 取自实测 shap_global.json。页面必须把这两类数字分开标注。
 */
export interface StageFactorStage {
  stage_id: string
  stage_label: string
  age_range: string
  funding_marker: string
  /** 企业分布占比（占位） */
  company_share: number
  definition_source: string
}

export interface StageFactorFactor {
  factor_id: string
  factor_label: string
  /** dimension = 本项目自建三维护口径，不是字段业务名 */
  label_kind: string
  semantic_known: boolean
  label_ref: string
  definition: string
  note: string
}

/** 单条权重（阶段 × 维度） */
export interface StageFactorWeightRow {
  stage_id: string
  factor_id: string
  weight: number
}

export interface StageFactorField {
  rank: number
  feature: string
  semantic_known: boolean
  /** 实测 */
  mean_abs_shap: number
  /** 实测 */
  direction: number
  /** 占位：该字段在该阶段的解释权重 */
  stage_weight: number
}

export interface StageFactorStageFields {
  stage_id: string
  fields: StageFactorField[]
}

export interface StageFactorNote {
  stage_id: string
  headline: string
  text: string
}

export interface StageFactorUnlock {
  blocked_by: string[]
  requires: string[]
  after: string
  owner: string
}

export interface StageFactorWeightContract {
  status: 'mock' | 'ready'
  status_note: string
  chart: string
  n_features_used: number
  stage_axis: { label: string; source: string; definition: string }
  stages: StageFactorStage[]
  factors: StageFactorFactor[]
  weights: StageFactorWeightRow[]
  stage_fields: StageFactorStageFields[]
  decision_notes: StageFactorNote[]
  unlock: StageFactorUnlock
  derived: boolean
  derivation: string
}

/* ------------------------- P3 时空图谱（合成六类实体图） ------------------------- */

/**
 * 三份契约共用同一个口径：`dataset = synthetic_graph`（红线三：合成数据警示基调，
 * 不得与真实面板同图）。数据缺口 P0-1 / P0-4 / P0-5 见 meta.blocked_by。
 */

/** 图谱节点：六类实体 + 画图用的结构字段（degree 由后端算好，前端不重算） */
export interface GraphNode {
  id: string
  /** 六类实体键，逐字镜像 viz_theme.yaml › entity_category */
  kind: EntityKindKey
  label: string
  /** 节点度（契约给定，避免前后端各算一套） */
  degree?: number
  /** 节点权重（气泡大小），后端给的相对值 */
  size_hint?: number
  /** 进入图的季度（切片联动用） */
  entered_at?: string
  /** 企业专属：城市 / 阶段 / 风险标签（合成数据，不得当实证） */
  city?: string
  stage?: string
  risk_label?: string
  alias?: string
  /** 专利专属 */
  granted?: boolean
  ipc_prefix?: string
  citations?: number
  /** 人员专属 */
  role?: string
  /** 投资机构专属：机构梯队 */
  tier?: string
  /** 其余附加字段原样保留（后端扩展不破坏前端） */
  [key: string]: unknown
}

/** 语义边类型：申请 / 授权 / 投资 / 任职 / 归属技术领域 / 产出产品 */
export type GraphEdgeKind = 'apply' | 'grant' | 'invest' | 'employ' | 'field_of' | 'produce'

export interface GraphEdge {
  id: string
  source: string
  target: string
  kind: GraphEdgeKind
  /** 该边首次出现的季度 */
  quarter: string
  label?: string
}

export interface GraphStats {
  node_kinds: Record<string, number>
  edge_kinds: Record<string, number>
  n_nodes: number
  n_edges: number
  n_nodes_shown: number
  n_edges_shown: number
  density: number
  avg_degree: number
  isolated: number
  dropped_isolated?: number
}

export interface GraphStructure {
  nodes: GraphNode[]
  edges: GraphEdge[]
  quarters: string[]
  stats: GraphStats
  entity_kinds: EntityKindKey[]
  edge_kinds: GraphEdgeKind[]
}

/** 单个季度切片：新增 / 累计 + 该季度可见节点集合 */
export interface GraphSlice {
  quarter: string
  new_nodes: number
  new_edges: number
  cum_nodes: number
  cum_edges: number
  new_by_kind: Record<string, number>
  visible_node_ids: string[]
}

export interface GraphSlices {
  quarters: string[]
  slices: GraphSlice[]
  summary: {
    n_slices: number
    n_nodes_total: number
    n_edges_total: number
    peak_new_edges_quarter: string
    growth: number
  }
}

/** 四类中心性（契约 §A06） */
export type CentralityMetric = 'degree' | 'betweenness' | 'closeness' | 'eigenvector'

export interface CentralityRow {
  id: string
  label: string
  kind: EntityKindKey
  degree: number
  betweenness: number
  closeness: number
  eigenvector: number
  degree_pct: number
  betweenness_pct: number
  closeness_pct: number
  eigenvector_pct: number
}

export interface GraphCentrality {
  degree_distribution: {
    bins: Array<{ degree_range: string; lo: number; hi: number; nodes: number; isolated?: boolean }>
    max_degree: number
    mean_degree: number
    median_degree: number
    /** 枢纽（度 ≥ max 的一半）承载的边占比 */
    hub_share: number
  }
  metrics: CentralityMetric[]
  metric_labels: Record<CentralityMetric, string>
  interpretation: Record<string, string>
  by_kind_top: Record<string, CentralityRow[]>
  overall_top: CentralityRow[]
  /** 逐节点索引（百分比），保证任意节点点开都能看到四维中心性，不限于 Top 榜 */
  node_index?: Record<
    string,
    {
      kind: EntityKindKey
      label: string
      degree: number
      degree_pct: number
      betweenness_pct: number
      closeness_pct: number
      eigenvector_pct: number
    }
  >
  /** 四类中心性的 Spearman 秩相关矩阵（判断榜单稳不稳） */
  rank_correlation: Record<string, Record<string, number>>
}

/* --------------------- P4 模型与对比（FE-13 四区块扩充） --------------------- */

/*
 * 口径（与 FE-11 / FE-12 一致）：
 *   - 所有字段 snake_case，逐字镜像 outputs/contracts/*.json，不做驼峰转换；
 *   - `source` 决定页面怎么说话：measured = 实测结论；reconstructed_from_anchors /
 *     contract_spec = 由锚点约束生成的曲线，**不得**说成「实测结果」；
 *   - `derived: true` 的对象必须带 `derivation`，页面要把推导口径写出来。
 */

/** 训练史中的单个模型（A09） */
export interface TrainCurveSeries {
  model_id: string
  model_label: string
  epochs: number[]
  loss: number[]
  val_loss: number[]
  train_auc: number[]
  val_auc: number[]
  best_epoch: number
  best_val_auc: number
  final_epoch_auc?: number
  stop_epoch: number
  patience: number
  monitor?: string
  stopped_early: boolean
  final_val_auc: number
  lr_schedule?: string
  derived: boolean
  derivation?: string
}

export interface ArchitectureNode {
  id: string
  label: string
  kind: 'input' | 'layer' | 'output'
  detail?: string
}

export interface ArchitectureEdge {
  from: string
  to: string
  label?: string
}

export interface ModelArchitecture {
  nodes: ArchitectureNode[]
  edges: ArchitectureEdge[]
  loss?: string
  optimizer?: string
  notes?: string[]
}

export interface TrainCurve {
  architecture: ModelArchitecture
  curves: TrainCurveSeries[]
  x_axis: { name: string; label: string }
  y_axis_loss: { name: string; label: string }
  y_axis_auc: { name: string; label: string }
  highlight: { noise_floor: number; coin_flip: number; note: string }
  /** 合成图架构验证 AUC（0.9149）—— 只在本契约语境出现 */
  synthetic_anchor: number
}

/** 分组柱（A10）：同一指标下多模型并排 */
export interface BenchmarkGrouped {
  metrics: string[]
  metric_labels: Record<string, string>
  models: Array<{ model_id: string; model_label: string; values: Record<string, number> }>
}

/** 雷达（A10）：六轴量程各不相同，只做「同模型偏科」判断 */
export interface BenchmarkRadar {
  indicators: Array<{ key: string; label: string; min: number; max: number }>
  series: Array<{ model_id: string; model_label: string; values: number[] }>
  note: string
}

export interface AblationBar {
  ablation_id: string
  label: string
  description: string
  auc: number
  delta_auc: number
  counterfactual: boolean
  derived: boolean
  derivation?: string
}

export interface AblationBlock {
  bars: AblationBar[]
  full_model_id: string
  base_auc: number
  note: string
}

export interface BenchmarkMatrixCell {
  model_id: string | null
  auc: number | null
  status: 'measured' | 'blocked' | 'pending'
  blocked_by?: string[]
}

export interface BenchmarkMatrix {
  rows: string[]
  cols: string[]
  cells: BenchmarkMatrixCell[][]
  note: string
  incomplete: boolean
}

export interface BenchmarkConclusion {
  headline: string
  points: string[]
  dataset_guard: string
}

/** A11 单模型曲线族 */
export interface CurveSeries {
  model_id: string
  model_label: string
  auc: number
  ap: number
  roc: { fpr: number[]; tpr: number[]; threshold?: number[] }
  pr: { recall: number[]; precision: number[] }
  ks: {
    value: number
    /** 实测 KS（T1 表口径），与合成曲线对账用 */
    measured: number
    threshold: number
    index: number
    curve: { threshold: number[]; fpr: number[]; tpr: number[]; gap: number[] }
  }
  derived: boolean
  derivation?: string
}

export interface RocPrKs {
  prevalence: number
  n_test: number
  n_pos: number
  series: CurveSeries[]
  notes: string[]
}

/** A12 噪声地板 */
export interface NoiseFloorBar {
  bar_id: string
  label: string
  group: 'observed' | 'control'
  auc: number
  std?: number | null
  dataset: DatasetKind
  derived: boolean
  derivation?: string
}

export interface NoiseFloorStrategy {
  strategy_id: string
  label: string
  description: string
  role: 'control' | 'diagnostic'
  derived: boolean
}

export interface NoiseFloorMargin {
  model_id: string
  observed_label: string
  observed: number
  floor: number
  delta: number
  delta_pct: number
}

export interface NoiseFloorContract {
  noise_floor: number
  coin_flip: number
  computed_floor: number
  floor_label: string
  floor_note: string
  bars: NoiseFloorBar[]
  strategies: NoiseFloorStrategy[]
  margin: NoiseFloorMargin[]
  verdict: string
  notes: string[]
}

/* --------------------- P6 前瞻预测（FE-15：A16 / A17） --------------------- */

/*
 * 口径（与 FE-13 / FE-14 一致，但这两份契约更特殊，必须多看一行）：
 *   - `meta.contract_status = 'mock'` + `source = 'demo_placeholder'`：
 *     队列是确定性合成的（P0-3 label 口径未定、P0-5 融资事件未到位）；
 *   - 但 KM / Greenwood / log-rank / Cox / Wilson / Brier 都是**现场计算**的真统计量，
 *     所以页面既不能把它当实证结论，也不该把它当假数字 —— 两句话都要说。
 *   - `meta.label_status = 'LABEL_PENDING'` 是这一页最重要的一个字段：
 *     「事件」到底指违约还是经营异常都还没定，任何生存率都只能算演示。
 */

/** KM 曲线单点：`[t, S(t), lo, hi, n_risk, cum_events]`（用元组省体积，含义见 survival.json › axis） */
export type SurvivalPoint = [number, number, number, number, number, number]

export interface SurvivalStratum {
  stratum_id: string
  stratum_label: string
  /** 契约给定色值（与 viz_theme 同源，前端不另生成） */
  color: string
  definition: string
  n: number
  events: number
  /** 中位生存月数；S(t) 始终未跌到 0.5 时为 null（不硬编一个数） */
  median_months: number | null
  s12: number
  s12_ci: [number, number]
  s24: number
  s24_ci: [number, number]
}

export interface SurvivalCurve {
  stratum_id: string
  points: SurvivalPoint[]
}

export interface SurvivalLogRank {
  chi2: number
  df: number
  /** 可能小到 1e-14；页面按 `< 0.001` 渲染，不要 toFixed(3) 打印成 0.000 */
  p: number
  note: string
}

export interface CoxCovariate {
  feature: string
  semantic_known: boolean
  label_ref: string
  coef: number
  se: number
  hr: number
  hr_lo: number
  hr_hi: number
  z: number
  p: number
  direction: number
}

export interface SurvivalCox {
  n: number
  events: number
  ties: string
  covariates: CoxCovariate[]
  concordance: number
  max_recovery_deviation: number
  note: string
}

export interface ForecastUnlock {
  blocked_by: string[]
  requires: string[]
  after: string
  owner: string
}

export interface SurvivalEventDefinition {
  label: string
  status: string
  note: string
}

export interface SurvivalCohort {
  n: number
  events: number
  observed_event_rate: number
  target_event_rate: number
  horizon_months: number
  censoring: string
  baseline_hazard: string
  anchor_note: string
}

export interface SurvivalContract {
  chart: string
  status: string
  status_note: string
  event_definition: SurvivalEventDefinition
  cohort: SurvivalCohort
  axis: {
    x: { name: string; label: string }
    y: { name: string; label: string }
  }
  strata: SurvivalStratum[]
  curves: SurvivalCurve[]
  logrank: SurvivalLogRank
  cox: SurvivalCox
  unlock: ForecastUnlock
  derived: boolean
  derivation: string
}

/** 校准分箱：预测概率均值 vs 观测发生率，区间为 Wilson 95% */
export interface CalibrationBin {
  bin: number
  n: number
  p_mean: number
  obs: number
  obs_lo: number
  obs_hi: number
}

export interface CalibrationBlock {
  n: number
  events: number
  prevalence: number
  bins: CalibrationBin[]
  ci_method: string
  brier: number
  brier_baseline: number
  ece: number
  reliability_slope: number
  reliability_intercept: number
  auc: number
  note: string
}

export interface ValuationBar {
  entity: string
  entity_note: string
  stage: string
  stage_label: string
  p_next: number
  p10: number
  p50: number
  p90: number
  width_ratio: number
}

export interface ValuationBlock {
  unit: string
  horizon: string
  target: string
  bars: ValuationBar[]
  method: string
  summary: { n: number; p50_median: number; width_ratio_median: number }
}

export interface FinancingContract {
  chart: string
  status: string
  status_note: string
  calibration: CalibrationBlock
  valuation: ValuationBlock
  unlock: ForecastUnlock
  derived: boolean
  derivation: string
}

/** 兼容旧别名（P7 尚未实现时仍有地方按名字引用） */
export type Survival = SurvivalContract
export type Financing = FinancingContract

/* ------------------------- P7 单企业深度诊断（FE-16） ------------------------- */

/** 匿名企业档案行（`GET /api/enterprise/{userid}`） */
export interface EnterpriseRow {
  userid: string
  /** 匿名态为「匿名企业 u0001」；P0-1 映射键到位后为真实企业名 */
  display_name: string
  /** 是否仍处于匿名态（true 时页面禁用「企业名」类文案） */
  anonymous: boolean
  name_note: string
  industry: string | null
  industry_note: string
  founded_year: number | null
  stage_id: string
  stage_label?: string
  observed_months: number
  observations: number
  peer_scope: { label: string; n: number; source: string }
  credit_score: number
  pd: number
  graph_node_id: string | null
}

/** 雷达图的一个指标；`percentile` 为 null 表示**该维缺数据**（不是 0） */
export interface EnterpriseRadarItem {
  item_id: string
  field: string | null
  label: string
  label_kind: string
  percentile: number | null
  peer_median: number | null
  has_data: boolean
}

export interface EnterpriseRadarDim {
  factor_id: string
  factor_label: string
  label_ref: string
  semantic_known: boolean
  items: EnterpriseRadarItem[]
  note: string
}

export interface EnterpriseDetailContract {
  chart: string
  status: string
  status_note: string
  mode: 'anonymous' | 'named' | 'partial' | 'label_pending'
  enterprises: EnterpriseRow[]
  radar: EnterpriseRadarDim[]
  risk_bands: { cuts: number[]; note?: string }
  prior: {
    base_rate: number
    base_rate_note: string
    noise_floor: number
    row_auc: number
    auc_note: string
  } | null
  unlock: { blocked_by: string[]; requires: string[]; after: string }
  derived: boolean
  derivation: string
  anchors?: Record<string, unknown>
}

/** 局部归因里的单个特征（`GET /api/explain/local/{userid}`） */
export interface LocalFeatureContribution {
  rank: number
  feature: string
  semantic_known: boolean
  label_ref: string
  value_label: string
  percentile: number
  /** 本企业该特征的贡献（logit 尺度）；正=推高违约概率 */
  phi: number
  /** 实测全局 mean|SHAP|（锚点） */
  mean_abs_shap: number
  /** 实测全局方向：+1 风险因子 / −1 保护因子 */
  global_direction: number
  /** 本企业方向（由 φ 符号给出） */
  direction: number
  consistent_with_global: boolean
  /** 本企业在该特征上的偏离画像：along=沿主方向分量，level=企业整体水平分量 */
  v?: number
  components?: { along: number; level: number }
}

export interface LocalSeries {
  feature: string
  total: number
  /** 逐月分摊比例，之和为 1 */
  by_month: number[]
  note: string
}

export interface LocalNeighbour {
  node_id: string
  label: string
  kind: string
  edge: string
  quarter?: string
}

export interface LocalExplainEntry {
  userid: string
  credit_score: number
  risk_band: string
  pd: number
  base_value: number
  score_logit: number
  features: LocalFeatureContribution[]
  rest: { n_features: number; phi: number; note: string }
  series: LocalSeries[]
  neighbours: LocalNeighbour[]
  neighbour_note: string
  level?: number
  consistency: { global_rho: number | null; sign_agree: number }
}

export interface LocalExplainContract {
  chart: string
  status: string
  status_note: string
  top_k: number
  entries: LocalExplainEntry[]
  method: Record<string, unknown>
  unlock: { blocked_by: string[]; requires: string[]; after: string }
  derived: boolean
  derivation: string
}

/** 兼容旧别名（FE-02 曾把这两个端点写成 Record<string, any>） */
export type EnterpriseDetail = EnterpriseDetailContract
export type LocalExplain = LocalExplainContract

/* ----------------------------- 其余端点（占位） ----------------------------- */

/* eslint-disable @typescript-eslint/no-explicit-any */
export type OverviewSummary = Record<string, any>
export type PermutationControl = Record<string, any>
export type OutputsManifest = Record<string, any>

/* ------------------------- P8 批量评估（FE-20 / A21） ------------------------- */

/**
 * 批量任务状态：**四值**，不设 running 之外的中间态。
 *
 * 为什么只留四个：真实后端是「受理 → 排队 → 逐行跑 → 落库」的串行队列，
 * 页面上能区分的也只有这四件事。多造一个 `finalizing` 这种状态，页面就得为它写一套
 * 没人验证过的 UI 分支 —— 状态机越简单，越不容易出现"永远转圈"。
 */
export type BatchJobStatus = 'queued' | 'processing' | 'done' | 'failed'

/** 失败原因归并项（按错误码分组，不是按「失败」两个字分组） */
export interface BatchErrorGroup {
  code: string
  label: string
  /** field = 数据错（改文件）；mapping = 映射缺（等 P0-1，改不了） */
  kind: 'field' | 'mapping'
  field: string
  rows: number
  row_nos: number[]
}

export interface BatchSummary {
  rows_total: number
  rows_done: number
  rows_ok: number
  rows_failed: number
  success_rate: number
  score_mean: number | null
  score_min: number | null
  score_max: number | null
  anomaly_count: number
  band_counts: Record<string, number>
  error_breakdown: BatchErrorGroup[]
  final: boolean
}

/** 逐行结果（也是导出的数据源：导出 CSV 逐列取自这里） */
export interface BatchRow {
  row_no: number
  name: string
  cert_no: string
  source_year: number
  batch: string
  issue_date: string
  status: 'ok' | 'field_error' | 'unmapped' | 'mapped_exhausted'
  as_of: string
  panel_record_no: number | null
  userid: string | null
  graph_node_id: string | null
  credit_score: number | null
  risk_band: RiskBandKey | null
  risk_band_label: string | null
  pd: number | null
  top_factor: string | null
  top_factor_known: boolean
  anomaly: boolean | null
  error: { field: string; code: string; message: string } | null
  trace: {
    panel_userid: string
    graph_node_id: string
    enterprise_contract: string
    local_contract: string
    stage_id: string
    observed_months: number
    score_source: string
    anomaly_rule: string
    as_of: string
  } | null
}

export interface BatchJob {
  job_id: string
  status: BatchJobStatus
  created_at: string
  updated_at: string
  /** 评估快照日：模型 / 契约版本。跨 as_of 的分数不可直接比 */
  as_of: string
  filename: string
  rows_total: number
  summary: BatchSummary
  progress_note: string
  live_note: string | null
  export_ready: boolean
  error: { code: string; message: string; next_step: string } | null
  evidence: BatchRow[]
}

export interface BatchStatusContract {
  chart: string
  jobs: BatchJob[]
  default_job_id: string
  as_of: string
  field_validate: {
    template_columns: string[]
    cert_pattern: string
    source_years: number[]
    batches: string[]
    encoding: string
    size_limit_mb: number
    row_limit: number
  }
  export_columns: string[]
  notes: BatchNote[]
}

/**
 * 「这一页能证明什么 / 不能证明什么」的一条问答。
 *
 * `level` 是读者该带走的态度：`verified`（有数据撑着的）/
 * `redline`（红线，越界就错）/ `caveat`（口径限制）。页面按它给左边框上色，
 * 不是纯装饰 —— 「红线」和「已验证」在视觉上必须能分开。
 */
export interface BatchNote {
  q: string
  a: string
  level: string
}

export interface BatchSubmitContract {
  job_id: string
  status: BatchJobStatus
  as_of: string
  filename: string
  rows_total: number
  rows_accepted: number
  field_validate: {
    ok: boolean
    template_columns: string[]
    cert_pattern: string
    ignored_extra_columns: string[]
    message: string
  }
  estimated_seconds: number
  accepted_at: string
}

export interface BatchEvidenceContract {
  job_id: string
  as_of: string
  export_columns: string[]
  rows: BatchRow[]
  summary: BatchSummary
}

/** 兼容 FE-02 的旧别名（保留类型名，避免既有引用编译失败；结构已收窄为真实契约） */
export type BatchUploadResult = BatchSubmitContract
export type BatchStatus = BatchStatusContract
