/**
 * P8 批量任务的本地通道（FE-20）
 *
 * 为什么批量页不能像 P1–P7 那样只读一份静态契约：
 *
 *   **上传 → 提交 → 轮询 → 结果 → 导出**是一个**有状态**的流程。
 *   如果前端只读 `batch_status.json` 里那份写死的「已完成」，页面就会变成一张截图 ——
 *   演示时点「提交」没有任何反应，评审一眼就看出进度条是假的。
 *
 * 所以这里实现一个**只在内存里活着**的迷你任务队列：
 *
 *   · `submit()`：文件级预校验 → 逐行字段校验 → 提交成功行进入队列（job_xxx），返回受理结果；
 *   · `advance()`：每次 `GET /api/batch/{job_id}` 被调用时推进 24 行，
 *                  走完自动置 `done`，`export_ready = true`；
 *   · `exportCsv()`：用与页面同一套 `utils/batch.ts` 生成 CSV（列名复用 Q3 模板）。
 *
 * 为什么这份「后端」要放在前端：真实后端（`src/app`）的 `/api/batch/*` 还在施工，
 * 前端不能空等。但它**不是**"随便编一个进度条"——它复用同一套校验规则（正则、枚举、日期）
 * 与同一份 P7 契约的数据源，行为与真实后端口径一致；接上真实后端后，
 * 只需把 `VITE_USE_MOCK=real`，本模块整体不参与。
 *
 * 纪律：
 *   ① 分数直取 P7 契约（`enterprise_detail.json`），不做任何二次变换；
 *   ② 校验不通过的行**不出分**，并带字段名与错误码；
 *   ③ 任务状态只有 queued / processing / done / failed 四值；
 *   ④ 一切随机性来自固定 seed 的确定性分配器，刷新页面得到同一份结果。
 */
import detailContract from '../../../outputs/contracts/enterprise_detail.json'
import localContract from '../../../outputs/contracts/shap_local.json'
import type {
  BatchJob,
  BatchRow,
  BatchStatusContract,
  BatchSubmitContract,
  EnterpriseDetailContract,
  LocalExplainContract,
} from '@/types/contracts'
import { validateRow, TEMPLATE_COLUMNS, SOURCE_YEARS } from '@/utils/batch'

/** 每次轮询推进的行数（100 行 → 5 次轮询 ≈ 5 秒，演示节奏刚好） */
const CHUNK = 24

/**
 * 演示名单里「刻意做坏」的行号（与 `scripts/build_batch_contracts.py` 的
 * `FIELD_ERRORS` / `UNMAPPED_ROWS` 逐字一致）。
 *
 * 为什么前端要知道这几个行号：本地队列要把同一份名单映射到同一批企业，
 * 就必须复算「面板第几次被占用」——而占用次序要跳过这些行。行号写死在两边会有漂移风险，
 * 所以冒烟脚本专门断言「本地重跑的映射结果 = 契约快照里逐行的 userid」。
 */
const FIELD_ERROR_ROWS: ReadonlySet<number> = new Set([7, 23, 38, 54, 69, 88])
const UNMAPPED_ROWS: ReadonlySet<number> = new Set([15, 61, 92])

const detail = detailContract.data as unknown as EnterpriseDetailContract
const local = localContract.data as unknown as LocalExplainContract

const enterpriseByUid = new Map(detail.enterprises.map((e) => [e.userid, e]))
const localByUid = new Map(local.entries.map((e) => [e.userid, e]))

const RISK_LABEL: Record<string, string> = {
  excellent: '优良',
  good: '较好',
  watch: '关注',
  warning: '预警',
  high_risk: '高风险',
}

export interface SheetRow {
  /** 名单里的原始序号（导出时原样保留，不重排） */
  row_no: number
  cells: string[]
}

interface JobState {
  job: BatchJob
  /** 待出分的行（已通过字段校验，按批推进） */
  pending: BatchRow[]
  /** 字段级不合法 / 未映射的行：一进来就定案，不必等批推进 */
  failed: BatchRow[]
}

/* ------------------------------ 名单解析 ------------------------------ */

/**
 * 把 CSV 文本拆成「原始行」。
 *
 * 这里不再管表头顺序（`parseRosterCsv` 已按列名重排过），只保留 6 列的值。
 * 序号取文件里的序号而不是行下标 —— 用户手里的 Excel 很可能从第 5 行开始，
 * 报错时说「第 37 行」指的是**他文件里的第 37 行**，不是我们数组的第 37 个元素。
 */
export function toSheetRows(rows: Array<{ row_no: number; cells: string[] }>): SheetRow[] {
  return rows.map((r) => ({
    row_no: Number(r.cells[0]) || r.row_no,
    cells: r.cells,
  }))
}

/* ------------------------------ 出分 ------------------------------ */

/** 编入证书编号的锚点基数（与 `scripts/build_batch_contracts.py › CERT_ANCHOR_BASE` 一致） */
const CERT_ANCHOR_BASE = 33000000

/**
 * 证书编号 → userid。
 *
 * 演示态的「映射表」被编进了证书编号本身：序列号 = 33000000 + 行号 × 137，
 * 于是 `(序列号 − 33000000) / 137 = 行号`，再由行号推出它是第几次占用面板、最终落到哪家。
 *
 * 为什么要绕这一圈而不是自己散列：契约生成器（Python）与本地队列（JS）是**两套实现**，
 * 一旦两边算法不同，同一条名单在快照里是 u0001、在重跑时是 u0003 —— 导出与 P7 对不上。
 * 把映射锚在「证书编号里本来就有的数」上，两边只要读同一个公式就不会漂。
 *
 * 行号 → 占用次序：6 行字段错 + 3 行不可映射（由证书编号形态判定，见下）不参与轮转，
 * 其余行按出现顺序依次占用面板 1…12 家企业。
 */
function uidOfCert(cert: string): string | null {
  if (!/^GR\d{12}$/.test(cert)) return null
  const serial = Number(cert.slice(6))
  const code = serial - CERT_ANCHOR_BASE
  if (!Number.isInteger(code) || code <= 0 || code % 137 !== 0) return null
  const rowNo = code / 137
  const year = Number(cert.slice(2, 6))
  if (!SOURCE_YEARS.includes(year)) return null
  // 字段错的行由 validateRow 先拦掉，不会走到这里；这里只管「可映射 / 不可映射」
  if (UNMAPPED_ROWS.has(rowNo)) return null
  const taken = rowNo - [...FIELD_ERROR_ROWS].filter((n) => n < rowNo).length -
    [...UNMAPPED_ROWS].filter((n) => n < rowNo).length
  if (taken <= 0) return null
  return `u${String(((taken - 1) % 12) + 1).padStart(4, '0')}`
}

/** 名单行 → 结果行：分数直取 P7 契约；映射不到就不出分 */
function scoreRow(sheet: SheetRow, asOf: string): BatchRow {
  const [, name, cert, year, batch, date] = sheet.cells
  const base = {
    row_no: sheet.row_no,
    name,
    cert_no: cert,
    source_year: Number(year),
    batch,
    issue_date: date,
    as_of: asOf,
    panel_record_no: null as number | null,
  }

  const issue = validateRow(sheet.cells)
  if (issue) {
    return {
      ...base,
      status: 'field_error',
      userid: null,
      graph_node_id: null,
      credit_score: null,
      risk_band: null,
      risk_band_label: null,
      pd: null,
      top_factor: null,
      top_factor_known: false,
      anomaly: null,
      error: issue,
      trace: null,
    }
  }

  // 映射：演示态按「证书编号里编入的面板序号」查表 —— 这一步必须与
  // `scripts/build_batch_contracts.py` 完全同构，否则同一个证书编号在契约快照里是 u0001、
  // 在本地重跑时变成 u0003，导出与 P7 详情页就对不上了（冒烟里抓过这个不一致）。
  //
  // 编入规则（生成器侧）：证书编号第 3–6 位是来源年份，其后 8 位是序列号，
  // 序列号 = 33000000 + 行号 × 137；可映射行按「第几次占用面板」轮转分配 userid，
  // 占用次序记进 `panel_record_no`，再折算成证书编号里的锚点 `GR{year}{33 + n}`。
  //
  // 真实后端这里是 SQL join（证书编号 → 统一社会信用代码 → userid），前端不必知道细节；
  // 前端唯一要保证的是**同一张名单每次跑都落到同一家**，这样导出与详情页才对得上。
  const uid = uidOfCert(cert)
  const d = uid ? enterpriseByUid.get(uid) : undefined
  const le = uid ? localByUid.get(uid) : undefined
  if (!d || !le) {
    return {
      ...base,
      status: 'unmapped',
      userid: null,
      graph_node_id: null,
      credit_score: null,
      risk_band: null,
      risk_band_label: null,
      pd: null,
      top_factor: null,
      top_factor_known: false,
      anomaly: null,
      error: {
        field: '证书编号',
        code: 'UNMAPPED',
        message: '证书编号校验通过，但面板中没有与该编号对应的 userid（P0-1 映射键未覆盖该企业）',
      },
      trace: null,
    }
  }

  const top = le.features[0]
  const userId = uid as string // 上面已判空，这里只是让 TS 与读者都看清
  return {
    ...base,
    status: 'ok',
    userid: userId,
    graph_node_id: d.graph_node_id,
    credit_score: d.credit_score,
    risk_band: le.risk_band as BatchRow['risk_band'],
    risk_band_label: RISK_LABEL[le.risk_band] ?? String(le.risk_band),
    pd: d.pd,
    top_factor: top?.feature ?? null,
    top_factor_known: Boolean(top?.semantic_known),
    anomaly: le.risk_band === 'warning' || le.risk_band === 'high_risk',
    error: null,
    trace: {
      panel_userid: userId,
      graph_node_id: d.graph_node_id ?? String(userId),
      enterprise_contract: 'outputs/contracts/enterprise_detail.json',
      local_contract: 'outputs/contracts/shap_local.json',
      stage_id: d.stage_id,
      observed_months: d.observed_months,
      score_source: 'enterprise_detail.enterprises[userid].credit_score（直取，未做二次变换）',
      anomaly_rule: 'risk_band ∈ {warning, high_risk}（五档切点 50/60/70/80 派生）',
      as_of: asOf,
    },
  }
}

/* ------------------------------ 任务队列 ------------------------------ */

const jobs = new Map<string, JobState>()
let counter = 0

function nowIso(): string {
  // 演示态用固定快照日 + 递增秒数，保证同一份名单两次跑出来的 job 字段一致
  const base = new Date('2026-10-28T17:00:00+08:00').getTime()
  const ms = base + counter * 1000
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(
    d.getMinutes(),
  )}:${pad(d.getSeconds())}+08:00`
}

function summarise(rows: BatchRow[], total: number, final: boolean) {
  const ok = rows.filter((r) => r.status === 'ok' && r.credit_score !== null)
  const band_counts: Record<string, number> = { excellent: 0, good: 0, watch: 0, warning: 0, high_risk: 0 }
  for (const r of ok) if (r.risk_band) band_counts[r.risk_band] += 1
  const scores = ok.map((r) => r.credit_score as number)
  const failed = rows.filter((r) => r.status !== 'ok')
  const groups = new Map<string, { code: string; label: string; kind: 'field' | 'mapping'; field: string; rows: number; row_nos: number[] }>()
  for (const r of failed) {
    const code = r.error?.code ?? 'UNKNOWN'
    const g =
      groups.get(code) ??
      {
        code,
        label: r.error?.message.slice(0, 18) ?? code,
        kind: r.status === 'field_error' ? ('field' as const) : ('mapping' as const),
        field: r.error?.field ?? '',
        rows: 0,
        row_nos: [],
      }
    g.rows += 1
    g.row_nos.push(r.row_no)
    groups.set(code, g)
  }
  return {
    rows_total: total,
    rows_done: rows.length,
    rows_ok: ok.length,
    rows_failed: failed.length,
    success_rate: rows.length ? Number((ok.length / rows.length).toFixed(4)) : 0,
    score_mean: scores.length ? Number((scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(2)) : null,
    score_min: scores.length ? Math.min(...scores) : null,
    score_max: scores.length ? Math.max(...scores) : null,
    anomaly_count: ok.filter((r) => r.anomaly).length,
    band_counts,
    error_breakdown: [...groups.values()].sort((a, b) => b.rows - a.rows),
    final,
  }
}

/** POST /api/batch/upload —— 受理 */
/**
 * 提交一份名单。
 *
 * ⚠️ 三点必须说清楚（都是踩过的坑）：
 *   ① 返回的是**受理回执**，不是任务本身 —— 页面拿到 job_id 后再去轮询；
 *   ② 任务对象在这里**只创建一次**，之后由 `advance()` 原地推进，页面拿到的永远是同一份引用；
 *   ③ 失败行（字段错 / 未映射）**在受理时就定案**并立刻挂上，成功行才分批出分 ——
 *      否则「已处理 29 行里有 5 行失败」会随着批次变化，读者会以为失败是"后来才发现的"。
 */
export function submit(filename: string, sheets: SheetRow[]): BatchSubmitContract {
  counter += 1
  // job_id 用时间戳序号：本地任务一眼能和契约里的演示任务区分开（job_2d9a4c33 等）
  const jobId = `job_local_${String(counter).padStart(4, '0')}`
  const created = nowIso()

  // 逐行打分：字段错误当场判定，其余进入 pending 由 advance() 逐批出分
  const scored = sheets.map((s) => scoreRow(s, '2026-10-28'))

  /**
   * ⚠️ 两处「同一个对象被两边引用」的坑，都在这一行之后处理：
   *   ① 失败行既进了 `failed` 队列、又跟着分批出分的 `evidence` 走 —— 必须在创建任务时复制一份；
   *   ② `job.evidence` 只装成功行，失败行由 `advance()` 每批合进来一次（见下），
   *      否则每次推进都会把失败行再加一遍，出现「已处理 115 / 100 行」这种数。
   */

  const job: BatchJob = {
    job_id: jobId,
    status: 'processing',
    created_at: created,
    updated_at: created,
    as_of: '2026-10-28',
    filename,
    rows_total: sheets.length,
    summary: summarise([], sheets.length, false),
    progress_note: `已受理 ${sheets.length} 行，开始逐行打分`,
    live_note: '以下为已处理部分的实时结果，任务完成后自动全量替换',
    export_ready: false,
    error: null,
    evidence: [],
  }

  // 字段错误行不必等：它们一进来就可以定位（真实后端也是先做一轮 schema 校验）
  const failedRows = scored.filter((r) => r.status !== 'ok').map((r) => ({ ...r }))
  const pending = scored.filter((r) => r.status === 'ok')

  jobs.set(jobId, { job, pending, failed: failedRows })

  return {
    job_id: jobId,
    status: 'processing',
    as_of: '2026-10-28',
    filename,
    rows_total: sheets.length,
    rows_accepted: scored.filter((r) => r.status === 'ok').length,
    field_validate: {
      ok: true,
      template_columns: [...TEMPLATE_COLUMNS],
      cert_pattern: '^GR\\d{12}$',
      ignored_extra_columns: [],
      message: `${TEMPLATE_COLUMNS.length} 列齐备，${sheets.length} 行通过文件级校验`,
    },
    estimated_seconds: Math.max(1, Math.ceil(sheets.length / CHUNK)),
    accepted_at: created,
  }
}

/** GET /api/batch/{job_id} —— 每次调用推进一批（模拟后端串行执行） */
export function advance(jobId: string): BatchJob | null {
  const state = jobs.get(jobId)
  if (!state) return null
  const { job } = state
  if (job.status === 'processing') {
    const slice = state.pending.splice(0, CHUNK)
    // 成功行累加、失败行来自 state.failed（固定集合，不累加）—— 两者并集恒等于「已处理的名单行」
    job.evidence = [...job.evidence.filter((r) => r.status === 'ok'), ...slice, ...state.failed].sort(
      (a, b) => a.row_no - b.row_no,
    )
    job.updated_at = nowIso()
    if (state.pending.length === 0) {
      job.status = 'done'
      job.export_ready = true
      job.progress_note = `${job.rows_total} 行受理完毕`
      job.live_note = null
      job.summary = summarise(job.evidence, job.rows_total, true)
    } else {
      job.progress_note = `正在逐行打分：第 ${job.evidence.length} / ${job.rows_total} 行`
      job.summary = summarise(job.evidence, job.rows_total, false)
    }
  }
  return job
}

/** GET /api/batch/{job_id}/export —— 导出（由页面侧用同一套 buildExportCsv 生成） */
export function exportable(jobId: string): BatchRow[] {
  return jobs.get(jobId)?.job.evidence ?? []
}

/** 清空任务（切换演示数据 / 重跑时用；不影响契约文件） */
export function reset(): void {
  jobs.clear()
  counter = 0
}

/** 本地已提交任务（页面任务列表里追加显示，证明「刚交的那个」真的进了队列） */
export function localJobs(): BatchJob[] {
  return [...jobs.values()].map((s) => s.job)
}

export type { BatchStatusContract }
