/**
 * P8 批量评估的纯函数层（FE-20）
 *
 * 和 FE-16 的 `utils/enterprise.ts` 同一个理由：批量页最容易错的地方全在**数字与行数怎么算**，
 * 而这些错在截图上看不出来 —— 表头写「成功率 91%」、表里却是 100 行 100 个分，肉眼一样"很专业"。
 * 把判定逻辑抽到纯函数里，冒烟脚本可以 `ssrLoadModule` 直接跑断言，不必起浏览器。
 *
 * 这里的八个函数分别守住八件容易出错的事：
 *   validateFile     —— 上传前的文件级预校验（类型 / 大小 / 必需 6 列 / 证书编号正则 / 序号连续）
 *   parseRosterCsv   —— 前端解析 CSV 预览（后端仍会重算，前端只管"能不能提交"）
 *   validateRows     —— 逐行字段级校验，**错误行带着行号与字段名**，不是一句"格式不对"
 *   bandOf           —— 五档判定（与 P7 共用切点，不允许批量页另定一套档）
 *   sortRows         —— 结果表排序（分数为 null 的行永远沉底，不参与排序）
 *   filterRows       —— 按风险分级 / 状态 / 关键词筛选
 *   summariseRows    —— 汇总统计（全部由行重算，与契约里的 summary 对齐）
 *   buildExportCsv   —— 导出 CSV（列名复用 Q3 模板 6 列 + 评估结果 5 列）
 *   diffJobs         —— 跨批次对比（as_of 不同即不可混比，必须先拦一次）
 */

import { RISK_BANDS, type RiskBandKey } from '@/styles/theme'

/* ------------------------------- 模板与规则 ------------------------------- */

/** Q3 冻结模板：6 列，顺序固定（后端 pydantic 与前端共用同一份口径） */
export const TEMPLATE_COLUMNS = ['序号', '企业名称', '证书编号', '来源年份', '批次', '发证日期'] as const

/** 证书编号正则（与 scripts/build_batch_contracts.py › CERT_PATTERN 逐字一致） */
export const CERT_PATTERN = /^GR\d{12}$/

/** 允许的来源年份 / 批次 */
export const SOURCE_YEARS = [2023, 2024, 2025]
export const BATCHES = ['第一批', '第二批', '第三批']

/** 文件级限制 */
export const SIZE_LIMIT_MB = 10
export const ROW_LIMIT = 5000

/** 五档切点（P7 契约 risk_bands.cuts 的同一组数；批量页不另立标准） */
export const BAND_CUTS = [50, 60, 70, 80]

export const STATUS_LABEL: Record<string, string> = {
  ok: '成功',
  field_error: '字段不合法',
  unmapped: '未映射到面板',
  mapped_exhausted: '映射额度已用尽',
}

/* ------------------------------- 文件预校验 ------------------------------- */

export interface FileCheck {
  ok: boolean
  /** 一句话结论（给用户看的） */
  message: string
  /** 逐条检查结果（无论通过与否都返回，页面按行展示「检查了什么」） */
  checks: Array<{ label: string; ok: boolean; detail: string }>
}

export interface ParsedRoster {
  header: string[]
  rows: Array<{ row_no: number; cells: string[] }>
  /** 解析层问题（列数不齐 / 空行 / 编码异常），不是字段校验 */
  issues: string[]
}

/**
 * 文件级预校验：**在上传之前**就拦掉九成的手工错误。
 *
 * 为什么前端也要校验一遍（后端反正会验）：现场演示时用户拖进来的往往是
 * 「导出没另存」「Excel 默认 GBK」「表头加了两个空格」这类问题，
 * 等后端起一个任务、跑完 100 行再报「0 行成功」是最差体验。
 * 前端这一关只保证「结构对了、能提交」，字段级语义仍以后端为准（任务卡明确要求）。
 */
export function validateFile(file: {
  name: string
  size: number
  text?: string | null
}): FileCheck {
  const checks: FileCheck['checks'] = []
  const lower = file.name.toLowerCase()
  const isCsv = lower.endsWith('.csv')
  const isXlsx = lower.endsWith('.xlsx')

  checks.push({
    label: '文件类型',
    ok: isCsv || isXlsx,
    detail: isCsv
      ? '.csv（前端直接解析预览）'
      : isXlsx
        ? '.xlsx（前端不透传内容，由后端解析）'
        : `${file.name} 不是 .csv / .xlsx；.xls 旧格式请先另存为 .xlsx`,
  })

  const sizeMb = file.size / (1024 * 1024)
  checks.push({
    label: '文件大小',
    ok: sizeMb <= SIZE_LIMIT_MB,
    detail: `${sizeMb.toFixed(2)} MB（上限 ${SIZE_LIMIT_MB} MB）`,
  })

  // 文本内容只在 csv 时可得；xlsx 是二进制，前端不解
  if (isCsv && typeof file.text === 'string') {
    const text = file.text.replace(/^\uFEFF/, '')
    const lines = text.split(/\r\n|\n|\r/).filter((l) => l.trim() !== '')
    const header = lines[0] ? lines[0].split(',').map((s) => s.trim()) : []
    const missing = TEMPLATE_COLUMNS.filter((c) => !header.includes(c))
    checks.push({
      label: '必需 6 列',
      ok: missing.length === 0,
      detail: missing.length === 0 ? TEMPLATE_COLUMNS.join(' / ') : `缺少：${missing.join('、')}`,
    })

    const dataRows = lines.slice(1)
    const badCert = dataRows.filter((l) => {
      const cert = (l.split(',')[2] ?? '').trim()
      return cert !== '' && !CERT_PATTERN.test(cert)
    }).length
    checks.push({
      label: '证书编号格式',
      ok: badCert === 0,
      detail:
        badCert === 0
          ? `全部符合 ^GR\\d{12}$（${dataRows.length} 行）`
          : `${badCert} 行不符合 ^GR\\d{12}$（提交后会被后端逐行拦下，仍可继续）`,
    })

    const seqs = dataRows.map((l) => Number((l.split(',')[0] ?? '').trim()))
    const seqOk = seqs.every((n, i) => Number.isInteger(n) && n === i + 1)
    checks.push({
      label: '序号连续',
      ok: seqOk,
      detail: seqOk ? `1 – ${dataRows.length} 连续` : '序号断号或非数字（后端会按行号定位，不影响出分）',
    })

    checks.push({
      label: '数据行数',
      ok: dataRows.length > 0 && dataRows.length <= ROW_LIMIT,
      detail:
        dataRows.length === 0
          ? '只有表头，没有数据行'
          : `${dataRows.length} 行（上限 ${ROW_LIMIT}）`,
    })
  }

  // 只有「硬条件」不过才算不通过：证书编号不合法属于字段级，允许提交后由后端逐行拦
  const hardFail = checks
    .filter((c) => ['文件类型', '文件大小', '必需 6 列', '数据行数'].includes(c.label))
    .some((c) => !c.ok)

  return {
    ok: !hardFail,
    message: hardFail
      ? checks.find((c) => !c.ok && ['文件类型', '文件大小', '必需 6 列', '数据行数'].includes(c.label))!
          .detail
      : '文件级校验通过，可以提交',
    checks,
  }
}

/* ------------------------------- CSV 解析 ------------------------------- */

/**
 * 极简 CSV 解析（支持双引号包裹、换行在内的字段）。
 *
 * 刻意不引入 papaparse：模板只有 6 列、值里不含逗号（企业名有中文括号但不含英文逗号），
 * 而多一个依赖就要多一份版本锁定。这里实现的是 RFC4180 的最小可用子集，
 * 遇到不闭合的引号直接报 issue 而不是静默拼错。
 */
export function parseCsv(text: string): { header: string[]; rows: string[][]; issues: string[] } {
  const src = text.replace(/^\uFEFF/, '')
  const rows: string[][] = []
  const issues: string[] = []
  let cell = ''
  let row: string[] = []
  let inQuotes = false

  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i]
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          cell += '"'
          i += 1
        } else {
          inQuotes = false
        }
      } else {
        cell += ch
      }
      continue
    }
    if (ch === '"') {
      inQuotes = true
    } else if (ch === ',') {
      row.push(cell)
      cell = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && src[i + 1] === '\n') i += 1
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else {
      cell += ch
    }
  }
  if (inQuotes) issues.push('第 ' + (rows.length + 1) + ' 行引号未闭合')
  if (cell !== '' || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }

  const nonEmpty = rows.filter((r) => r.some((c) => c.trim() !== ''))
  const header = (nonEmpty[0] ?? []).map((c) => c.trim())
  return { header, rows: nonEmpty.slice(1), issues }
}

export function parseRosterCsv(text: string): ParsedRoster {
  const { header, rows, issues } = parseCsv(text)
  const missing = TEMPLATE_COLUMNS.filter((c) => !header.includes(c))
  if (missing.length) issues.push(`缺少必需列：${missing.join('、')}`)

  // 按列名取下标，而不是按位置 —— 用户很可能把「批次」和「来源年份」调了个顺序
  const idx = (name: string) => header.indexOf(name)
  const order = TEMPLATE_COLUMNS.map((c) => idx(c))

  const parsed = rows.map((cells, i) => ({
    row_no: i + 1,
    cells: order.map((ci) => (ci >= 0 ? (cells[ci] ?? '').trim() : '')),
  }))

  for (const r of parsed) {
    if (r.cells.length !== TEMPLATE_COLUMNS.length) issues.push(`第 ${r.row_no} 行列数不足`)
  }
  return { header, rows: parsed, issues }
}

/* ------------------------------- 行级校验 ------------------------------- */

export interface RowIssue {
  row_no: number
  field: string
  code: string
  message: string
}

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/

/** 单行字段级校验：返回 null 表示这一行可提交 */
export function validateRow(cells: string[]): Omit<RowIssue, 'row_no'> | null {
  const [seq, name, cert, year, batch, date] = cells

  if (!seq || !/^\d+$/.test(seq)) {
    return { field: '序号', code: 'SEQ_INVALID', message: `序号「${seq}」不是正整数` }
  }
  if (!name) {
    return { field: '企业名称', code: 'REQUIRED_MISSING', message: '企业名称为空，无法与面板记录对齐' }
  }
  if (!cert) {
    return { field: '证书编号', code: 'REQUIRED_MISSING', message: '证书编号为空' }
  }
  if (!CERT_PATTERN.test(cert)) {
    return {
      field: '证书编号',
      code: 'CERT_FORMAT',
      message: `证书编号「${cert}」不符合 ^GR\\d{12}$（GR + 12 位数字）`,
    }
  }
  const y = Number(year)
  if (!SOURCE_YEARS.includes(y)) {
    return {
      field: '来源年份',
      code: 'YEAR_ENUM',
      message: `来源年份 ${year || '（空）'} 不在允许范围（${SOURCE_YEARS.join(' / ')}）`,
    }
  }
  if (!BATCHES.includes(batch)) {
    return {
      field: '批次',
      code: 'BATCH_ENUM',
      message: `批次「${batch || '（空）'}」不在 Q3 模板允许值内（${BATCHES.join(' / ')}）`,
    }
  }
  const m = DATE_RE.exec(date)
  const valid = (() => {
    if (!m) return false
    const [yy, mm, dd] = [Number(m[1]), Number(m[2]), Number(m[3])]
    const d = new Date(Date.UTC(yy, mm - 1, dd))
    return d.getUTCFullYear() === yy && d.getUTCMonth() === mm - 1 && d.getUTCDate() === dd
  })()
  if (!valid) {
    return {
      field: '发证日期',
      code: 'DATE_FORMAT',
      message: `发证日期「${date || '（空）'}」无法解析（应为 YYYY-MM-DD 且日期真实存在）`,
    }
  }
  return null
}

export function validateRows(rows: Array<{ row_no: number; cells: string[] }>): RowIssue[] {
  const out: RowIssue[] = []
  for (const r of rows) {
    const issue = validateRow(r.cells)
    if (issue) out.push({ row_no: r.row_no, ...issue })
  }
  return out
}

/* ------------------------------- 五档判定 ------------------------------- */

/**
 * 分数 → 五档（与 `utils/enterprise.ts › bandOf` 同一套切点，左闭右开）。
 *
 * 这里不 import P7 的 `bandOf` 是为了让批量页能独立 `ssrLoadModule`（少一条依赖链），
 * 但两个实现的切点都读同一个常量 `BAND_CUTS`，冒烟脚本会逐档对数。
 */
export function bandOf(score: number, cuts: number[] = BAND_CUTS): RiskBandKey {
  const codes: RiskBandKey[] = ['high_risk', 'warning', 'watch', 'good', 'excellent']
  const idx = cuts.reduce((acc, c) => acc + (score >= c ? 1 : 0), 0)
  return codes[Math.min(idx, codes.length - 1)]
}

export function bandLabel(code: string): string {
  return RISK_BANDS.find((b) => b.code === code)?.label ?? code
}

export function bandColor(code: string): string {
  return RISK_BANDS.find((b) => b.code === code)?.color ?? 'var(--color-text-subtle)'
}

/* ------------------------------- 排序 / 筛选 ------------------------------- */

export type SortKey = 'row_no' | 'credit_score' | 'pd' | 'name' | 'risk_band'

export interface SortSpec {
  key: SortKey
  dir: 'asc' | 'desc'
}

/**
 * 排序：**没出分的行永远沉底**。
 *
 * 不这么做的话，按分数升序时那些 `credit_score === null` 的失败行会排到最前面，
 * 读者第一眼看到的是「9 行失败」而不是「最低的 10 家」—— 表格的重心被数据质量问题劫持了。
 * 失败行的位置由「按状态筛选」去管，不该由排序管。
 */
export function sortRows<T extends { row_no: number; credit_score: number | null; pd: number | null; name: string; risk_band: string | null }>(
  rows: T[],
  spec: SortSpec,
): T[] {
  const sign = spec.dir === 'asc' ? 1 : -1
  const bandOrder = ['high_risk', 'warning', 'watch', 'good', 'excellent']
  return [...rows].sort((a, b) => {
    const an = a.credit_score === null
    const bn = b.credit_score === null
    if (an !== bn) return an ? 1 : -1 // 失败行沉底（与排序方向无关）
    if (an && bn) return a.row_no - b.row_no

    switch (spec.key) {
      case 'credit_score':
        return sign * ((a.credit_score! - b.credit_score!) || (a.row_no - b.row_no))
      case 'pd':
        return sign * (((a.pd ?? 0) - (b.pd ?? 0)) || (a.row_no - b.row_no))
      case 'name':
        return sign * (a.name.localeCompare(b.name, 'zh-Hans-CN') || (a.row_no - b.row_no))
      case 'risk_band':
        return (
          sign *
          ((bandOrder.indexOf(a.risk_band ?? '') - bandOrder.indexOf(b.risk_band ?? '')) ||
            a.row_no - b.row_no)
        )
      case 'row_no':
      default:
        return sign * (a.row_no - b.row_no)
    }
  })
}

export interface FilterSpec {
  /** 风险分级筛选：空数组 = 不限 */
  bands: string[]
  /** 状态筛选：ok / failed / all */
  status: 'all' | 'ok' | 'failed'
  /** 关键词：企业名 / 证书编号 / userid 模糊匹配 */
  keyword: string
  /** 只看异常行 */
  anomalyOnly?: boolean
}

export function filterRows<
  T extends {
    row_no: number
    name: string
    cert_no: string
    userid: string | null
    status: string
    risk_band: string | null
    anomaly: boolean | null
  },
>(rows: T[], spec: FilterSpec): T[] {
  const kw = spec.keyword.trim().toLowerCase()
  return rows.filter((r) => {
    if (spec.status === 'ok' && r.status !== 'ok') return false
    if (spec.status === 'failed' && r.status === 'ok') return false
    if (spec.bands.length && (!r.risk_band || !spec.bands.includes(r.risk_band))) return false
    if (spec.anomalyOnly && r.anomaly !== true) return false
    if (kw) {
      const hay = `${r.name} ${r.cert_no} ${r.userid ?? ''} ${r.row_no}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })
}

/* ------------------------------- 汇总 ------------------------------- */

export interface RowLike {
  row_no: number
  status: string
  credit_score: number | null
  risk_band: string | null
  anomaly: boolean | null
  error: { code: string } | null
}

export interface Summary {
  rows_done: number
  rows_ok: number
  rows_failed: number
  success_rate: number
  score_mean: number | null
  score_min: number | null
  score_max: number | null
  anomaly_count: number
  band_counts: Record<string, number>
}

/** 汇总**只从行算**：页面上的每个 KPI 都能被人拿导出 CSV 重算一遍，没有第二份数 */
export function summariseRows(rows: RowLike[]): Summary {
  const ok = rows.filter((r) => r.status === 'ok' && r.credit_score !== null)
  const scores = ok.map((r) => r.credit_score as number)
  const band_counts: Record<string, number> = {
    excellent: 0,
    good: 0,
    watch: 0,
    warning: 0,
    high_risk: 0,
  }
  for (const r of ok) if (r.risk_band) band_counts[r.risk_band] = (band_counts[r.risk_band] ?? 0) + 1

  return {
    rows_done: rows.length,
    rows_ok: ok.length,
    rows_failed: rows.length - ok.length,
    success_rate: rows.length ? Number((ok.length / rows.length).toFixed(4)) : 0,
    score_mean: scores.length ? Number((scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(2)) : null,
    score_min: scores.length ? Math.min(...scores) : null,
    score_max: scores.length ? Math.max(...scores) : null,
    anomaly_count: ok.filter((r) => r.anomaly === true).length,
    band_counts,
  }
}

/* ------------------------------- 导出 ------------------------------- */

export interface ExportRow {
  row_no: number
  name: string
  cert_no: string
  source_year: number
  batch: string
  issue_date: string
  credit_score: number | null
  risk_band_label: string | null
  top_factor: string | null
  anomaly: boolean | null
  status: string
}

export const EXPORT_COLUMNS = [...TEMPLATE_COLUMNS, '信用分', '风险分级', '主要贡献因子', '是否异常', '评估状态']

const csvCell = (v: unknown): string => {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/**
 * 导出 CSV：**列名复用 Q3 模板**，评估结果追加在后面。
 *
 * 三条硬规矩：
 *   ① 前 6 列顺序与 Q3 模板逐字一致（客户拿到文件能原样回填系统）；
 *   ② 失败行的分数列留空，不写 0 / 不写「-」——写 0 会被下游当成"零分企业"；
 *   ③ 带 UTF-8 BOM，Excel 双击不乱码（这也是原模板的要求）。
 */
export function buildExportCsv(rows: ExportRow[]): string {
  const lines = [EXPORT_COLUMNS.join(',')]
  for (const r of rows) {
    lines.push(
      [
        r.row_no,
        r.name,
        r.cert_no,
        r.source_year,
        r.batch,
        r.issue_date,
        r.credit_score === null ? '' : r.credit_score.toFixed(1),
        r.risk_band_label ?? '',
        r.top_factor ?? '',
        r.anomaly === null ? '' : r.anomaly ? '是' : '否',
        STATUS_LABEL[r.status] ?? r.status,
      ]
        .map(csvCell)
        .join(','),
    )
  }
  return '\uFEFF' + lines.join('\r\n') + '\r\n'
}

/** 导出文件名：批次名 + 快照日 + 行数，避免下载目录里五个「导出.csv」 */
export function exportFilename(job: { filename: string; as_of: string }, rows: number): string {
  const base = job.filename.replace(/\.(xlsx|csv)$/i, '')
  return `${base}_信用评估结果_${job.as_of}_${rows}行.csv`
}

/* ------------------------------- 跨批次对比 ------------------------------- */

export interface JobDiffRow {
  row_no: number
  name: string
  cert_no: string
  from_score: number | null
  to_score: number | null
  delta: number | null
  from_band: string | null
  to_band: string | null
  band_changed: boolean
}

export interface JobDiff {
  comparable: boolean
  reason: string
  rows: JobDiffRow[]
  band_moves: number
  mean_delta: number | null
}

/**
 * 跨批次对比：**as_of 不同就先拦一次**。
 *
 * 真实场景：同一份 500 家名单每季度跑一次，两次之间模型快照升级过一版。
 * 如果把两个批次并排放，"分数涨了 12.5 分"会被读成"企业经营改善"，
 * 实际上是模型换了。所以这里的第一返回是 ``comparable`` 而不是 ``rows`` ——
 * 调用方必须先看这个布尔值，再决定要不要展示。
 */
export function diffJobs(
  from: { as_of: string; evidence: Array<{ row_no: number; name: string; cert_no: string; credit_score: number | null; risk_band: string | null }> },
  to: { as_of: string; evidence: Array<{ row_no: number; name: string; cert_no: string; credit_score: number | null; risk_band: string | null }> },
): JobDiff {
  const comparable = from.as_of === to.as_of
  const mapFrom = new Map(from.evidence.map((r) => [r.cert_no, r]))
  const rows: JobDiffRow[] = []

  for (const b of to.evidence) {
    const a = mapFrom.get(b.cert_no)
    if (!a) continue
    const delta =
      a.credit_score !== null && b.credit_score !== null
        ? Number((b.credit_score - a.credit_score).toFixed(2))
        : null
    rows.push({
      row_no: b.row_no,
      name: b.name,
      cert_no: b.cert_no,
      from_score: a.credit_score,
      to_score: b.credit_score,
      delta,
      from_band: a.risk_band,
      to_band: b.risk_band,
      band_changed: a.risk_band !== b.risk_band,
    })
  }

  const deltas = rows.map((r) => r.delta).filter((d): d is number => d !== null)
  return {
    comparable,
    reason: comparable
      ? '两批次评估快照日相同（as_of 一致），可以逐行比较'
      : `两批次评估快照日不同（${from.as_of} → ${to.as_of}），分数差异含模型版本效应，不可直接当作企业经营变化`,
    rows,
    band_moves: rows.filter((r) => r.band_changed).length,
    mean_delta: deltas.length ? Number((deltas.reduce((x, y) => x + y, 0) / deltas.length).toFixed(2)) : null,
  }
}

/* ------------------------------- 轮询节奏 ------------------------------- */

/**
 * 轮询间隔退避：前 10 次 1s（让用户看到动），之后 2s，60s 后 5s。
 *
 * 为什么不固定 1s：真实后端处理 500 行要 20 秒以上，固定 1s 会打出 20+ 次请求，
 * 单实例后端还得分一个 worker 去应答轮询。退避让「看着有进度」和「别把后端问死」两件事同时成立。
 */
export function pollDelay(elapsedMs: number): number {
  if (elapsedMs < 10_000) return 1000
  if (elapsedMs < 60_000) return 2000
  return 5000
}

/** 进度百分比：用 `rows_done / rows_total`，不编造「第几步」 */
export function progressPct(rowsDone: number, rowsTotal: number): number {
  if (!rowsTotal) return 0
  return Math.max(0, Math.min(100, Math.round((rowsDone / rowsTotal) * 100)))
}
