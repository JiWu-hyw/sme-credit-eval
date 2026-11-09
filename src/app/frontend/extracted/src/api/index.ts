/**
 * 接口层（FE-02 收口点）
 *
 * 页面只 import 本文件，**永远不直接 import axios / mock**——切模式零改页面代码。
 * FE-11 只用到 P2 的四个端点，其余端点保持同样的信封结构，由后续任务补齐实现。
 *
 * 模式（VITE_USE_MOCK）：
 *   mock  → 只用契约同构 mock
 *   real  → 只用真实接口，失败如实抛出
 *   auto  → 默认。真实接口优先；命中阻塞码 / 网络错误自动降级 mock
 */
import type { ApiEnvelope } from '@/types/contracts'
import type { ContractMeta } from '@/types'

import { http } from './http'
import { getMock, getMockMode } from './mock'
import type { MockMode } from './mock'
import * as batchStore from './mock/batch'
import type { SheetRow } from './mock/batch'
import batchStatusContract from '../../outputs/contracts/batch_status.json'
import batchSubmitContract from '../../outputs/contracts/batch_submit.json'
import type {
  BatchJob,
  BatchStatusContract,
  BatchSubmitContract,
} from '@/types/contracts'

export { configureMock, getMockMode } from './mock'
export type { MockMode }

/** 端点 key → 契约路径（与 fe-spec §6 表逐行对应，供冒烟与文档核对） */
export const ENDPOINT_PATHS = {
  'overview/summary': 'GET /api/overview/summary',
  'data/pipeline': 'GET /api/data/pipeline',
  'data/quality': 'GET /api/data/quality',
  'data/panel-depth': 'GET /api/data/panel-depth',
  'features/tree': 'GET /api/features/tree',
  'graph/structure': 'GET /api/graph/structure',
  'graph/slices': 'GET /api/graph/slices',
  'graph/centrality': 'GET /api/graph/centrality',
  'model/train-curve': 'GET /api/model/train-curve',
  'model/benchmark': 'GET /api/model/benchmark',
  'model/roc-pr-ks': 'GET /api/model/roc-pr-ks',
  'model/noise-floor': 'GET /api/model/noise-floor',
  'explain/global': 'GET /api/explain/global',
  'explain/stage-factor': 'GET /api/explain/stage-factor',
  'explain/local': 'GET /api/explain/local/{userid}',
  'enterprise/detail': 'GET /api/enterprise/{userid}',
  'enterprise/list': 'GET /api/enterprise/list（前端选择器用，不计入 21）',
  // P8 三端点：**已实现**（不是占位）。有状态，走下方独立实现，不经 call()
  'batch/upload': 'POST /api/batch/upload',
  'batch/status': 'GET /api/batch/{job_id}',
  'batch/export': 'GET /api/batch/{job_id}/export',
  'forecast/survival': 'GET /api/forecast/survival',
  'forecast/financing': 'GET /api/forecast/financing',
  'outputs/manifest': 'GET /api/outputs/manifest（前端回执新增，不计入 21）',
} as const

export type EndpointKey = keyof typeof ENDPOINT_PATHS

/** 阻塞码：契约 / 标签 / 批次运行中（运行中是合法态，不降级） */
const PENDING_CODES = new Set(['CONTRACT_PENDING', 'LABEL_PENDING'])

/**
 * 形状校验：这个响应**到底是不是**约定的信封？
 *
 * 踩坑（2026-10-22 线上白屏）：
 *   静态托管 / 网关对 `/api/*` 未命中接口时会回落 `index.html`，
 *   于是 axios 拿到 200 + 一段 HTML，`res.data` 是字符串。
 *   原来的实现只看 `code`，字符串上没有 `code`，既不命中阻塞码也不报错，
 *   就把 HTML 当信封交给了页面；页面读 `pipeline.nodes` 得到 undefined，
 *   在 `.filter()` 上抛 TypeError，整页被错误态替换。
 *
 * 所以：`code` 必须是数字或字符串、`data` 必须存在且不是字符串（对象 / 数组才算），
 * 否则视为「响应不是约定信封」，在 auto 模式下按可用性降级 mock。
 */
function isEnvelope(body: unknown): body is ApiEnvelope<unknown> {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return false
  const b = body as Record<string, unknown>
  const codeOk = typeof b.code === 'number' || typeof b.code === 'string'
  const dataOk = b.data !== undefined && b.data !== null && typeof b.data !== 'string'
  return codeOk && dataOk
}

/** 请求头：明确要 JSON，别让网关把我们当页面请求处理 */
const JSON_ACCEPT = 'application/json'

async function call<T>(
  key: EndpointKey,
  url: string,
  params?: Record<string, unknown>,
): Promise<ApiEnvelope<T>> {
  const mode = getMockMode()
  if (mode === 'mock') return getMock(key) as ApiEnvelope<T>

  try {
    const res = await http.get<ApiEnvelope<T>>(url, { params, headers: { Accept: JSON_ACCEPT } })
    const body = res.data
    if (mode === 'auto') {
      if (!isEnvelope(body)) {
        // 非约定信封（典型是登录页 / index.html / 网关错误页）→ 按不可用处理
        return getMock(key) as ApiEnvelope<T>
      }
      if (PENDING_CODES.has(String(body.code))) return getMock(key) as ApiEnvelope<T>
    }
    return body
  } catch (error) {
    if (mode === 'auto') return getMock(key) as ApiEnvelope<T>
    throw error
  }
}

/* --------------------------------- P1 概览 --------------------------------- */

/** P1 GET /api/overview/summary —— 全站状态聚合（六份契约的汇总视图） */
export const fetchOverviewSummary = () => call('overview/summary', '/overview/summary')

/* ----------------------------- P2 数据与特征 ----------------------------- */

/** A01 GET /api/data/pipeline —— 数据接入链路 */
export const fetchDataPipeline = () => call('data/pipeline', '/data/pipeline')

/** A02 GET /api/data/quality —— 数据质量诊断 */
export const fetchDataQuality = () => call('data/quality', '/data/quality')

/** A03 GET /api/data/panel-depth —— 面板观测纵深 */
export const fetchPanelDepth = () => call('data/panel-depth', '/data/panel-depth')

/** A04 GET /api/features/tree —— 特征体系三级树 */
export const fetchFeatureTree = () => call('features/tree', '/features/tree')

/* ------------------------- P4 模型与对比 / P5 全局解释 ------------------------- */

/** P4 GET /api/model/benchmark —— 基线 + 图模型 + 无图对照 + 消融 + 2×2 矩阵（实测锚点） */
export const fetchBenchmark = () => call<import('@/types/contracts').Benchmark>('model/benchmark', '/model/benchmark')

/**
 * P4 GET /api/model/train-curve —— 模型架构 + 训练曲线（A08 / A09）
 *
 * ⚠️ 契约 meta.dataset = synthetic_graph、source = reconstructed_from_anchors：
 * 曲线是「端点锚定 + 形状重建」，不是真实训练日志。页面必须单独成区块并标口径，
 * 不得与真实面板的 performance 图共用坐标轴（红线一 / 四）。
 */
export const fetchTrainCurve = () => call<import('@/types/contracts').TrainCurve>('model/train-curve', '/model/train-curve')

/** P4 GET /api/model/roc-pr-ks —— ROC / PR / KS 曲线族（A11） */
export const fetchRocPrKs = () => call<import('@/types/contracts').RocPrKs>('model/roc-pr-ks', '/model/roc-pr-ks')

/** P4 GET /api/model/noise-floor —— 置换对照噪声地板柱状对比（A12） */
export const fetchNoiseFloor = () =>
  call<import('@/types/contracts').NoiseFloorContract>('model/noise-floor', '/model/noise-floor')

/**
 * P5 GET /api/explain/global —— 全局 SHAP 特征贡献（实测）+ 蜂群点位（锚点重建）
 *
 * ⚠️ 方向语义（2026-11-09）：本页契约是 A 套信贷面板 **PD 风险方向** 的前端原型 mock，
 * `+1 = 推高违约概率`（风险因子），`label_ref = null`、`semantic_known = false`。
 * v1.9 企业好坏口径的实测解释是 `outputs/shap_credit_direction.json`（P(好企业)，
 * `+1 = 增信因子`），两者方向相反且数据集不同，**禁止互引数值或合并排序**。
 * 详见 `outputs/shap_legacy_notice.md`；契约 meta 内已注入 `label_direction` 声明。
 */
export const fetchShapGlobal = () =>
  call<import('@/types/contracts').ShapGlobal>('explain/global', '/explain/global')

/**
 * P5 GET /api/explain/stage-factor —— A15 阶段-因子-权重（A15）
 *
 * ⚠️ 契约 meta.contract_status = 'mock'、source = demo_placeholder：
 * A15 真实契约等 P0-2（字段字典）与 P0-5（融资事件）。页面必须把「占位」摆在明面上，
 * 不能因为接口返回 200 就当成实测结论（红线四：不同性质的数据不同观感）。
 */
export const fetchStageFactor = () =>
  call<import('@/types/contracts').StageFactorWeightContract>(
    'explain/stage-factor',
    '/explain/stage-factor',
  )

/* ------------------------------ P6 前瞻预测 ------------------------------ */

/**
 * P6 GET /api/forecast/survival —— A16 生存力预测（KM 曲线分层 + Cox 系数表）
 *
 * ⚠️ 契约 meta.contract_status = 'mock'、source = demo_placeholder：
 * 队列是按 Cox-Weibull 确定性抽样的**合成队列**（P0-3 标签口径 / P0-5 融资事件未到位）。
 * 但 KM 点估计、Greenwood 置信区间、log-rank、Cox 系数**全部是拿到这份队列后现场算的**，
 * 不是抄来的数。页面两件事必须同时说清：估计器是真的，队列是假的。
 */
export const fetchSurvival = () =>
  call<import('@/types/contracts').SurvivalContract>('forecast/survival', '/forecast/survival')

/**
 * P6 GET /api/forecast/financing —— A17 融资力预测（概率校准 + 估值区间条）
 *
 * ⚠️ 同上：契约 meta.contract_status = 'mock'。校准曲线用的是合成队列的真标签，
 * Brier / ECE / 可靠性斜率 / AUC 都是现场算的；估值区间条（P10/P50/P90）是**区间口径演示**，
 * 不是可对外的估值结论（P0-5 融资事件缺失，估值分布没有真实锚点）。
 */
export const fetchFinancing = () =>
  call<import('@/types/contracts').FinancingContract>('forecast/financing', '/forecast/financing')

/* ---------------------------- P7 单企业深度诊断 ---------------------------- */

/**
 * P7 GET /api/enterprise/{userid} —— 企业档案（头部 + 五档 + 同行上下文）
 *
 * ⚠️ 契约 meta.contract_status = 'mock'、source = contract_spec：
 * 企业集合与匿名编号锚定真实面板的 userid 语料，分数 / PD / 雷达分位数是结构同构占位。
 * 页面必须以「匿名态」呈现（P0-1 映射键未到位），不许编企业名。
 */
export const fetchEnterpriseDetail = (userid: string) =>
  call<import('@/types/contracts').EnterpriseDetailContract>(
    'enterprise/detail',
    `/enterprise/${encodeURIComponent(userid)}`,
  )

/**
 * P7 GET /api/explain/local/{userid} —— 局部归因（瀑布 / 时序 / 关键邻居）
 *
 * ⚠️ source = 'reconstructed_from_anchors'：**排序与方向**锚定实测全局 SHAP，
 * 逐企业幅度是重建的，Σφ + E[f] = f(x) 已自校验。SHAP 是贡献度不是判别力，
 * 本区块不挂 AUC 噪声地板（红线三的反面）。
 */
export const fetchLocalExplain = (userid: string) =>
  call<import('@/types/contracts').LocalExplainContract>(
    'explain/local',
    `/explain/local/${encodeURIComponent(userid)}`,
  )

/** 匿名队列的 userid 列表（前端选择器用；真实后端由 /api/enterprise/list 提供） */
export const fetchEnterpriseList = () =>
  call<{ userids: string[] }>('enterprise/list', '/enterprise/list')

/* ------------------------------ P3 时空图谱 ------------------------------ */

/** E1 GET /api/graph/structure —— 六类实体异质图（合成图） */
export const fetchGraphStructure = () =>
  call<import('@/types/contracts').GraphStructure>('graph/structure', '/graph/structure')

/** E3 GET /api/graph/slices —— 季度时间切片（增量 + 累计 + 可见节点集） */
export const fetchGraphSlices = () =>
  call<import('@/types/contracts').GraphSlices>('graph/slices', '/graph/slices')

/** E2 GET /api/graph/centrality —— 度分布 + 四类中心性比较 */
export const fetchGraphCentrality = () =>
  call<import('@/types/contracts').GraphCentrality>('graph/centrality', '/graph/centrality')

/* ------------------------------ P8 批量评估 ------------------------------ */

/**
 * P8 批量评估的三个端点（FE-20）。
 *
 * 与 P1–P7 的差别：**这三个是有状态的**。所以它们不走 `call()`（那条通道只读静态契约），
 * 而是走独立实现：
 *
 *   · real / auto：`POST /api/batch/upload` 传 multipart，`GET /api/batch/{job_id}` 与
 *     `GET /api/batch/{job_id}/export` 直接透传 —— 失败如实抛出，不静默降级
 *     （批量任务的进度条如果"降级"成假数据，比报错危险得多）；
 *   · mock：走 `api/mock/batch.ts` 的内存队列，行为与真实后端同一套校验与口径。
 *
 * 演示任务列表（6 个任务，含排队 / 处理中 / 跨快照）来自 `batch_status.json`；
 * 本地新提交的任务追加在列表顶部，`job_local_*` 一眼可辨。
 */

/**
 * 批量端点的形状校验（比 `isEnvelope` 更严一点）。
 *
 * P1–P7 的守卫只要求「是个信封、data 不是字符串」，因为那些端点的空对象会被各区块
 * 自己的降级面板接住。批量页不一样：`data.jobs` 不是数组时 `jobs.value` 会抛
 * `find is not a function`，整页被错误态替换 —— 用户看到的是白屏，看不出是网关的问题。
 *
 * 所以批量列表这里多要求一条：`data.jobs` 必须是数组。不满足就当「后端还没上线」处理。
 */
function isBatchStatusEnvelope(body: unknown): body is ApiEnvelope<BatchStatusContract> {
  if (!isEnvelope(body)) return false
  const data = (body as ApiEnvelope<unknown>).data as { jobs?: unknown } | null
  return !!data && typeof data === 'object' && Array.isArray((data as { jobs?: unknown }).jobs)
}

/** 演示任务列表（静态契约）+ 本地队列里刚提交的任务 */
export async function fetchBatchStatus(): Promise<ApiEnvelope<BatchStatusContract>> {
  if (getMockMode() === 'mock') {
    const base = batchStatusContract as unknown as ApiEnvelope<BatchStatusContract>
    const locals = structuredClone(batchStore.localJobs())
    if (!locals.length) return base
    return {
      ...base,
      data: { ...base.data, jobs: [...locals, ...base.data.jobs] },
    }
  }
  try {
    const res = await http.get<ApiEnvelope<BatchStatusContract>>('/batch', {
      headers: { Accept: 'application/json' },
    })
    const body = res.data
    // real 模式：形状不对就如实抛错（页面走降级面板），绝不悄悄换成 mock 列表 ——
    // 那会让人以为「后端已经有 6 个任务了」。auto 模式才按可用性降级。
    if (!isBatchStatusEnvelope(body)) {
      throw new Error('GET /api/batch 返回的不是批量任务列表（data.jobs 缺失或不是数组）')
    }
    return body
  } catch (error) {
    if (getMockMode() === 'auto') {
      const base = batchStatusContract as unknown as ApiEnvelope<BatchStatusContract>
      const locals = structuredClone(batchStore.localJobs())
      return { ...base, data: { ...base.data, jobs: [...locals, ...base.data.jobs] } }
    }
    throw error
  }
}

/** 提交一份名单：mock 走本地队列，real 走 multipart 上传 */
export async function submitBatch(
  file: { name: string; size: number },
  sheets: SheetRow[],
): Promise<ApiEnvelope<BatchSubmitContract>> {
  if (getMockMode() === 'mock') {
    const data = batchStore.submit(file.name, sheets)
    return {
      code: 0,
      message: 'ok',
      data,
      meta: (batchSubmitContract as unknown as ApiEnvelope<BatchSubmitContract>).meta,
    }
  }
  const form = new FormData()
  const blob = new Blob([JSON.stringify({ rows: sheets })], { type: 'application/json' })
  form.append('file', blob, file.name)
  const res = await http.post<ApiEnvelope<BatchSubmitContract>>('/batch/upload', form, {
    headers: { Accept: 'application/json' },
  })
  return res.data
}

/**
 * 轮询单个任务。
 *
 * mock 模式下每次调用都会推进一批（24 行）—— 这样演示时的进度条是真的在动，
 * 而不是靠 `setInterval` 自己数数。real 模式下就是单纯的 GET。
 *
 * ## 为什么返回的是一份**深拷贝**
 *
 * mock 队列里的任务对象会被 `advance()` **原地推进**（引用不变、内容在变）。
 * 如果把它原样交给页面，Vue 的 `shallowRef` + `computed` 会认为"引用没变 → 不用重算"，
 * 结果是：接口已经在推进，页面却一直停在「已处理 0 / 100 行」。
 * 这个 bug 只在"同一个对象被反复修改"时出现，接真实后端（每次都是新 JSON）永远碰不到 ——
 * 所以必须在 mock 边界上就断掉：每次轮询交出的都是一个新对象。
 */
export async function fetchBatchJob(jobId: string): Promise<ApiEnvelope<BatchJob>> {
  if (getMockMode() === 'mock') {
    const base = batchStatusContract as unknown as ApiEnvelope<BatchStatusContract>
    const job = batchStore.advance(jobId)
    if (job) {
      return { code: 0, message: 'ok', data: structuredClone(job), meta: base.meta }
    }
    const found = base.data.jobs.find((j) => j.job_id === jobId)
    if (found) return { ...base, data: found }
    return { code: 'JOB_NOT_FOUND', message: `任务 ${jobId} 不存在`, data: {} as BatchJob, meta: base.meta }
  }
  const res = await http.get<ApiEnvelope<BatchJob>>(`/batch/${encodeURIComponent(jobId)}`, {
    headers: { Accept: 'application/json' },
  })
  return res.data
}

/** 导出所需的行（mock 从本地队列取；real 由页面直接调 export 端点） */
export function exportRowsOf(jobId: string) {
  return batchStore.exportable(jobId)
}

/** 导出端点地址（real 模式直接用浏览器下载，避免把 CSV 读进内存再落盘） */
export function exportUrl(jobId: string): string {
  const base = (import.meta.env?.VITE_API_BASE ?? '/api').replace(/\/$/, '')
  return `${base}/batch/${encodeURIComponent(jobId)}/export`
}

/** 其余只读端点保持同一通道（`call()`），供未写专用封装的页面使用 */
export const fetchAny = <T>(key: Exclude<EndpointKey, `data/${string}` | 'features/tree'>) =>
  call<T>(key, `/${key.split('/').filter((s) => !s.startsWith('{')).join('/')}`)

export type { ContractMeta }
