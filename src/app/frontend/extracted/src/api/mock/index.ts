/**
 * 契约同构 mock（FE-02 / FE-11）
 *
 * 纪律（fe-spec §6）：
 *   - 字段名逐字对齐 src/types/contracts.ts，禁止简化、禁止 camelCase；
 *   - 语义未知字段（x21–x200）一律 semantic_known: false（红线二）；
 *   - meta 由本文件统一注入（页面只拿信封，不自己拼 meta）。
 *
 * 数值来源分两类，必须分清：
 *   ① **实测锚点**：真实面板行级 AUC≈0.59、企业级 0.47、噪声地板 0.477、
 *      每企业平均 ≈2 个月观测 —— 这些是项目已跑出来的结果，mock 里照抄；
 *   ② **结构同构占位**：真实面板尚未接入 data/processed 时，为了把页面结构和交互跑通，
 *      按契约结构生成点位；一律标 source='demo_placeholder' 并在页面透出免责声明。
 */
import type { ApiEnvelope } from '@/types/contracts'
import type { ContractMeta } from '@/types'
import { NOISE_FLOOR } from '@/utils/redlines'

import dataPipeline from '../../../outputs/contracts/data_pipeline.json'
import dataQuality from '../../../outputs/contracts/data_quality.json'
import panelDepth from '../../../outputs/contracts/panel_depth.json'
import featureTree from '../../../outputs/contracts/feature_tree.json'
// FE-14 / FE-15 的实测契约（非占位：数值来自已跑出的实验结果）
import benchmark from '../../../outputs/contracts/benchmark.json'
import shapGlobal from '../../../outputs/contracts/shap_global.json'
// FE-13 P4 其余三份契约：曲线重建（reconstructed_from_anchors）与实测锚点（measured）
import trainCurve from '../../../outputs/contracts/train_curve.json'
import rocPrKs from '../../../outputs/contracts/roc_pr_ks.json'
import noiseFloor from '../../../outputs/contracts/noise_floor.json'
// FE-14 P5 阶段-因子-权重（A15）：mock 契约，等 P0-2 / P0-5
import stageFactor from '../../../outputs/contracts/stage_factor_weight.json'
// FE-15 P6 前瞻预测（A16 / A17）：合成队列 + 现场实算统计量，等 P0-3 / P0-5
import survival from '../../../outputs/contracts/survival.json'
import financing from '../../../outputs/contracts/financing.json'
// P1 概览：六份契约的聚合视图（source=aggregated，不引入新数据）
import overview from '../../../outputs/contracts/overview.json'
// FE-12 P3 时空图谱：合成六类实体图（source=synthetic_graph，须警示基调）
import graphStructure from '../../../outputs/contracts/graph_structure.json'
import graphSlices from '../../../outputs/contracts/graph_slices.json'
import graphCentrality from '../../../outputs/contracts/graph_centrality.json'
// FE-16 P7 单企业深度诊断（A18–A20）：观测集锚定真实 userid 语料，分项值为重建 / 占位
import enterpriseDetail from '../../../outputs/contracts/enterprise_detail.json'
import shapLocal from '../../../outputs/contracts/shap_local.json'

export type MockMode = 'mock' | 'real' | 'auto'

let mode: MockMode = ((): MockMode => {
  const v = import.meta.env?.VITE_USE_MOCK
  return v === 'mock' || v === 'real' || v === 'auto' ? v : 'auto'
})()

export function configureMock(next: MockMode): void {
  mode = next
}

export function getMockMode(): MockMode {
  return mode
}

/**
 * 信封：code/message/data/meta 四件套，meta 取契约文件自带那份。
 *
 * `noise_floor` 只在 meta **没有这个键**时兜底成 0.477（FE-11 口径）；
 * 一旦契约显式写了 `noise_floor: null`（合成图 / 曲线重建），必须原样透传 null——
 * FE-13 踩过：原来的 `{...meta, noise_floor: NOISE_FLOOR}` 会把合成图的 null 覆盖成 0.477，
 * 页面于是给合成图也画了一条「噪声地板」线，正好踩红线三的反面（地板不适用时乱贴）。
 */
function envelope<T>(data: T, meta: ContractMeta | undefined): ApiEnvelope<T> {
  const m: ContractMeta = { ...(meta ?? {}) }
  if (m.noise_floor === undefined) m.noise_floor = NOISE_FLOOR
  return { code: 0, message: 'ok', data, meta: m }
}

const REGISTRY: Record<string, () => ApiEnvelope<unknown>> = {
  'overview/summary': () =>
    envelope(overview.data, overview.meta as unknown as ContractMeta),
  'data/pipeline': () =>
    envelope(dataPipeline.data, dataPipeline.meta as unknown as ContractMeta),
  'data/quality': () =>
    envelope(dataQuality.data, dataQuality.meta as unknown as ContractMeta),
  'data/panel-depth': () =>
    envelope(panelDepth.data, panelDepth.meta as unknown as ContractMeta),
  'features/tree': () =>
    envelope(featureTree.data, featureTree.meta as unknown as ContractMeta),
  'model/benchmark': () =>
    envelope(benchmark.data, benchmark.meta as unknown as ContractMeta),
  'model/train-curve': () =>
    envelope(trainCurve.data, trainCurve.meta as unknown as ContractMeta),
  'model/roc-pr-ks': () =>
    envelope(rocPrKs.data, rocPrKs.meta as unknown as ContractMeta),
  'model/noise-floor': () =>
    envelope(noiseFloor.data, noiseFloor.meta as unknown as ContractMeta),
  'explain/global': () =>
    envelope(shapGlobal.data, shapGlobal.meta as unknown as ContractMeta),
  'explain/stage-factor': () =>
    envelope(stageFactor.data, stageFactor.meta as unknown as ContractMeta),
  'graph/structure': () =>
    envelope(graphStructure.data, graphStructure.meta as unknown as ContractMeta),
  'graph/slices': () =>
    envelope(graphSlices.data, graphSlices.meta as unknown as ContractMeta),
  'graph/centrality': () =>
    envelope(graphCentrality.data, graphCentrality.meta as unknown as ContractMeta),
  'forecast/survival': () =>
    envelope(survival.data, survival.meta as unknown as ContractMeta),
  'forecast/financing': () =>
    envelope(financing.data, financing.meta as unknown as ContractMeta),
  'enterprise/detail': () =>
    envelope(enterpriseDetail.data, enterpriseDetail.meta as unknown as ContractMeta),
  'explain/local': () =>
    envelope(shapLocal.data, shapLocal.meta as unknown as ContractMeta),
  'enterprise/list': () =>
    envelope(
      { userids: (enterpriseDetail.data.enterprises as Array<{ userid: string }>).map((e) => e.userid) },
      enterpriseDetail.meta as unknown as ContractMeta,
    ),
}

export function getMock<T>(key: string): ApiEnvelope<T> {
  const factory = REGISTRY[key]
  if (!factory) {
    return {
      code: 'CONTRACT_PENDING',
      message: `契约 ${key} 尚未产出`,
      data: {} as T,
      meta: { dataset: 'real_panel', schema_version: '1.0', noise_floor: NOISE_FLOOR },
    }
  }
  return factory() as ApiEnvelope<T>
}
