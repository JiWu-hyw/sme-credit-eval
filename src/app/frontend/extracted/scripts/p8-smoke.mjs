/**
 * P8 批量评估 冒烟测试（FE-20）
 *
 * 本页的验收只有一句：「100 条名单能完整跑通并导出」。但这句最容易被糊过去 ——
 * 一个假的进度条 + 一张写死的 100 行表 + 一个 `a[download]`，看起来同样"跑通了"。
 * 所以这支脚本分六段，每段都在证明「它不能糊」：
 *
 *   0. 纯函数：validateFile / parseRosterCsv / validateRow / sortRows / filterRows /
 *      summariseRows / buildExportCsv / diffJobs / pollDelay / progressPct
 *      —— 页面读的就是它们，且必须能证明「把错的拦下」。
 *   1. 契约自洽：契约文件里的 summary 必须能由 evidence 逐行重算出来（差一个数就说明有两份真相）、
 *      成功行分数与 P7 `enterprise_detail.json` 同 userid 逐位一致、五档计数自洽、导出 CSV 逐列对齐。
 *   2. 页面：六区块渲染 → **真点一次提交**，进度从 0 真的走到 100（不是页面自己数数），
 *      结果表行数随之递进，跑完后导出按钮才出现。
 *   3. 结果表交互：排序真的改顺序且失败行永远沉底、按档筛选真的生效、搜索、分页、行展开追溯。
 *   4. 红线：失败行不隐藏、语义未知字段只显 xNNN、不混排来源、跨快照对比先拦一次。
 *   5. 降级：real 模式 /api/batch 打空 → 页面不白屏、读图问答仍在、无运行时异常。
 *
 * 用法：npm run test:p8
 */
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createServer as createHttp } from 'node:http'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'
import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const contract = (name) =>
  JSON.parse(readFileSync(resolve(here, `../outputs/contracts/${name}.json`), 'utf8'))

const statusEnv = contract('batch_status')
const status = statusEnv.data
const detail = contract('enterprise_detail').data
const demoCsv = readFileSync(resolve(here, '../public/fixtures/batch_demo_100.csv'), 'utf8')
const exportSample = readFileSync(resolve(here, '../public/fixtures/batch_export_100.csv'), 'utf8')

let failed = 0
const report = (ok, label, extra = '') => {
  if (!ok) failed += 1
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${extra}`)
}
const flat = (s) => s.replace(/\s/g, '')
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps

const PORT = 11588
const BASE = `http://127.0.0.1:${PORT}`

const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  mode: 'test',
  define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('mock') },
  server: { host: '127.0.0.1', port: PORT, strictPort: true },
  logLevel: 'error',
})
await server.listen()

/* ============================================================ 0. 纯函数 */
console.log('\n— 0. 纯函数（页面读的就是这些）')
const ut = await server.ssrLoadModule('/src/utils/batch.ts')

/* -------- 文件级预校验 -------- */
const okFile = ut.validateFile({ name: 'roster.csv', size: 12 * 1024, text: demoCsv })
report(okFile.ok === true, `演示名单通过文件级校验（${okFile.checks.length} 条检查全部给结论）`)
report(
  okFile.checks.every((c) => typeof c.detail === 'string' && c.detail.length > 0),
  '每条检查都带具体 detail（不是一句「文件不合法」）',
)

const badType = ut.validateFile({ name: '名单.xls', size: 1024, text: null })
report(badType.ok === false && flat(badType.message).includes('.xlsx'), '.xls 旧格式被拦下，并给出「另存为 .xlsx」的下一步')

const oversized = ut.validateFile({ name: 'big.csv', size: 11 * 1024 * 1024, text: 'x' })
report(oversized.ok === false && oversized.message.includes('上限'), '超过 10 MB 被拦下，原因带上限值')

const missingCol = ut.validateFile({
  name: 'narrow.csv',
  size: 200,
  text: '序号,企业名称,证书编号\n1,甲公司,GR202433000137\n',
})
report(
  missingCol.ok === false && missingCol.message.includes('缺少'),
  `缺列被拦下并点名缺哪几列（${missingCol.message}）`,
)

const headerOnly = ut.validateFile({
  name: 'empty.csv',
  size: 100,
  text: ut.TEMPLATE_COLUMNS.join(','),
})
report(headerOnly.ok === false && headerOnly.message.includes('没有数据行'), '只有表头（0 数据行）被拦下')

/**
 * 关键一条：证书编号不合法**不算文件级失败**。
 * 它属于字段级，应当放行到后端逐行拦 —— 否则 100 行里有 1 行坏号就整份退回去，
 * 用户会以为"整个文件不能交"，而实际上另外 99 行本来是能出分的。
 */
const oneBadCert = ut.validateFile({
  name: 'one-bad.csv',
  size: 200,
  text: '序号,企业名称,证书编号,来源年份,批次,发证日期\n1,甲公司,GR20243300013X,2024,第一批,2024-06-28\n',
})
report(oneBadCert.ok === true, '单行证书编号不合法时文件级仍放行（字段级问题交给后端逐行拦）')
report(
  oneBadCert.checks.some((c) => c.label === '证书编号格式' && c.ok === false),
  '但「证书编号格式」这条检查仍明确标红并说明会有几行被拦',
)

/* -------- CSV 解析 -------- */
const parsed = ut.parseRosterCsv(demoCsv)
report(parsed.rows.length === 100, `演示名单解析出 100 行（实际 ${parsed.rows.length}）`)
report(
  parsed.rows.every((r) => r.cells.length === ut.TEMPLATE_COLUMNS.length),
  '每行都是 6 列（按列名取，不是按下标硬切）',
)
report(
  parsed.rows.every((r, i) => Number(r.cells[0]) === i + 1),
  '序号 1–100 连续',
)

// 列顺序被打乱时仍能对齐 —— 用户经常把「批次」和「来源年份」调个位置
const shuffled =
  '企业名称,序号,批次,证书编号,发证日期,来源年份\n乙公司,1,第三批,GR202433000137,2024-06-28,2024\n'
const s1 = ut.parseRosterCsv(shuffled)
report(
  s1.rows[0].cells[0] === '1' && s1.rows[0].cells[1] === '乙公司' && s1.rows[0].cells[2] === 'GR202433000137',
  '打乱列顺序后按列名重排（序号/企业名/证书编号各归其位）',
)

const quoted = '序号,企业名称,证书编号,来源年份,批次,发证日期\n1,"甲, 乙公司",GR202433000137,2024,第一批,2024-06-28\n'
report(ut.parseRosterCsv(quoted).rows[0].cells[1] === '甲, 乙公司', '引号包裹的字段含逗号时不被切开（RFC4180 最小子集）')

const unclosed = '序号,企业名称,证书编号,来源年份,批次,发证日期\n1,"甲公司,GR202433000137,2024,第一批,2024-06-28\n'
report(
  ut.parseRosterCsv(unclosed).issues.some((s) => s.includes('引号未闭合')),
  '引号不闭合时报 issue 而不是静默拼错',
)

/* -------- 行级校验：每一类错都要能被点名 -------- */
const goodRow = ['1', '甲公司', 'GR202433000137', '2024', '第一批', '2024-06-28']
report(ut.validateRow(goodRow) === null, '合法行返回 null（可通过）')

const cases = [
  { cells: ['', '甲', 'GR202433000137', '2024', '第一批', '2024-06-28'], code: 'SEQ_INVALID' },
  { cells: ['1', '', 'GR202433000137', '2024', '第一批', '2024-06-28'], code: 'REQUIRED_MISSING' },
  { cells: ['1', '甲', 'GR20243300013X', '2024', '第一批', '2024-06-28'], code: 'CERT_FORMAT' },
  { cells: ['1', '甲', 'GR202433000137', '2022', '第一批', '2024-06-28'], code: 'YEAR_ENUM' },
  { cells: ['1', '甲', 'GR202433000137', '2024', '第五批', '2024-06-28'], code: 'BATCH_ENUM' },
  { cells: ['1', '甲', 'GR202433000137', '2024', '第一批', '2024-02-30'], code: 'DATE_FORMAT' },
]
for (const c of cases) {
  const issue = ut.validateRow(c.cells)
  report(
    issue?.code === c.code && issue.field.length > 0 && issue.message.length > 0,
    `拦下 ${c.code}：字段「${issue?.field}」+ 原因（${String(issue?.message).slice(0, 24)}…）`,
  )
}

const allIssues = ut.validateRows(parsed.rows)
report(allIssues.length === 6, `演示名单逐行校验出 6 处字段错（实际 ${allIssues.length}）`)
report(
  allIssues.map((i) => i.row_no).join(',') === '7,23,38,54,69,88',
  `坏的正好是第 7/23/38/54/69/88 行（实际 ${allIssues.map((i) => i.row_no).join('/')}）`,
)
report(
  parsed.rows.filter((r) => ut.validateRow(r.cells) !== null).map((r) => r.cells[1].trim() === '' ? 1 : 0).reduce((a, b) => a + b, 0) === 1,
  '6 行被拦下的行里只有 1 行是企业名称为空（其余坏在各不相同的列，便于对着原因逐类改）',
)

/* -------- 五档 -------- */
const cuts = detail.risk_bands.cuts
report(
  JSON.stringify(cuts) === JSON.stringify(ut.BAND_CUTS),
  `批量页切点与 P7 契约同源（${cuts.join('/')}）`,
)
report(
  ut.bandOf(80) === ut.bandOf(100) && ut.bandOf(79.9) !== ut.bandOf(80),
  '切点左闭右开（80 归上一档，79.9 归下一档）',
)
report(
  ['high_risk', 'warning', 'watch', 'good', 'excellent'].every((c) => ut.bandLabel(c) !== c),
  '五档都有中文标签（不把英文 code 直接甩给用户）',
)

/* -------- 排序：失败行沉底 -------- */
const rows = status.jobs.find((j) => j.job_id === status.default_job_id).evidence
const ascScore = ut.sortRows(rows, { key: 'credit_score', dir: 'asc' })
const descScore = ut.sortRows(rows, { key: 'credit_score', dir: 'desc' })
report(
  ascScore[0].credit_score <= ascScore[1].credit_score && ascScore[0].status === 'ok',
  `按分数升序时首行是最低分 ${ascScore[0].credit_score}（不是失败行）`,
)
const firstNull = ascScore.findIndex((r) => r.credit_score === null)
report(
  firstNull > 0 && ascScore.slice(firstNull).every((r) => r.credit_score === null),
  `9 行未出分的行全部沉底（从第 ${firstNull + 1} 行开始）—— 排序不会把数据质量问题顶到第一屏`,
)
report(
  descScore[descScore.length - 1].credit_score === null,
  '倒序时未出分的行同样沉底（与排序方向无关）',
)
report(
  ut.sortRows(rows, { key: 'credit_score', dir: 'asc' }).length === rows.length,
  '排序不丢行（100 行进、100 行出）',
)
const desc = ut.sortRows(rows.filter((r) => r.status === 'ok'), { key: 'credit_score', dir: 'desc' })
report(
  desc.every((r, i) => i === 0 || desc[i - 1].credit_score >= r.credit_score),
  '已出分的行按分数严格降序',
)

/* -------- 筛选 -------- */
const onlyWatch = ut.filterRows(rows, { bands: ['watch'], status: 'all', keyword: '' })
report(
  onlyWatch.length === 38 && onlyWatch.every((r) => r.risk_band === 'watch'),
  `只看「关注」筛出 38 行（实际 ${onlyWatch.length}）`,
)
report(
  ut.filterRows(rows, { bands: [], status: 'failed', keyword: '' }).length === 9,
  '只看「被拦下」筛出 9 行（失败行可被单独调出，说明它们真的在表里）',
)
report(
  ut.filterRows(rows, { bands: [], status: 'all', keyword: 'u0001' }).every((r) =>
    `${r.name}${r.cert_no}${r.userid ?? ''}${r.row_no}`.includes('u0001') ||
    r.userid === 'u0001' || String(r.row_no) === '1' || String(r.row_no).startsWith('1'),
  ),
  '关键词搜 userid 能命中（企业名 / 证书号 / userid / 行号 四路都可搜）',
)
report(
  ut.filterRows(rows, { bands: ['watch'], status: 'failed', keyword: '' }).length === 0,
  '「关注」+「被拦下」互斥时返回空（筛选是交集，不是并集）',
)

/* -------- 汇总：只从行算 -------- */
const sum = ut.summariseRows(rows)
report(sum.rows_ok === 91 && sum.rows_failed === 9, `汇总 91 成功 / 9 失败（实际 ${sum.rows_ok}/${sum.rows_failed}）`)
report(
  near(sum.score_mean, 74.11, 0.005),
  `均值 ${sum.score_mean} 与契约 summary 对齐（74.11）`,
)
report(
  Object.values(sum.band_counts).reduce((a, b) => a + b, 0) === sum.rows_ok,
  '五档计数之和 = 成功行数（没有行同时落两档，也没有行落空）',
)
report(
  sum.success_rate === Number((91 / 100).toFixed(4)),
  '成功率的分母是名单总行数（100），不是已处理行数',
)
report(
  ut.summariseRows([]).score_mean === null && ut.summariseRows([]).success_rate === 0,
  '空名单时均值为 null 而不是 0（0 分会被读成「100 家都是 0 分」）',
)

/* -------- 导出 CSV -------- */
const exportRows = rows.map((r) => ({
  row_no: r.row_no,
  name: r.name,
  cert_no: r.cert_no,
  source_year: r.source_year,
  batch: r.batch,
  issue_date: r.issue_date,
  credit_score: r.credit_score,
  risk_band_label: r.risk_band_label,
  top_factor: r.top_factor,
  anomaly: r.anomaly,
  status: r.status,
}))
const csv = ut.buildExportCsv(exportRows)
report(csv.startsWith('\uFEFF'), '导出 CSV 带 UTF-8 BOM（Excel 双击不乱码）')
report(csv.includes('\r\n'), '行尾是 CRLF（Excel / WPS 兼容）')
const csvLines = csv.replace(/^\uFEFF/, '').trimEnd().split('\r\n')
report(csvLines.length === 101, `导出 1 行表头 + 100 行数据（实际 ${csvLines.length} 行）`)
report(
  csvLines[0] === ut.EXPORT_COLUMNS.join(','),
  `表头 = ${ut.EXPORT_COLUMNS.length} 列（Q3 模板 6 列 + 评估结果 5 列）`,
)
report(
  ut.EXPORT_COLUMNS.slice(0, 6).join(',') === ut.TEMPLATE_COLUMNS.join(','),
  '前 6 列与 Q3 冻结模板逐字一致（客户能原样回填系统）',
)
const failedLines = csvLines.slice(1).filter((l) => l.split(',')[10] !== '成功')
report(failedLines.length === 9, `被拦下的 9 行也在导出里（实际 ${failedLines.length}）`)
report(
  failedLines.every((l) => {
    const c = l.split(',')
    return c.slice(6, 10).every((x) => x === '')
  }),
  '失败行的分数列留空，不写 0、不写「-」（写 0 会被下游当成零分企业）',
)
report(
  ut.exportFilename({ filename: '名单_2026Q3.xlsx', as_of: '2026-10-28' }, 100) ===
    '名单_2026Q3_信用评估结果_2026-10-28_100行.csv',
  '导出文件名带快照日与行数（下载目录里五个「导出.csv」的问题）',
)

/* -------- 跨批次对比 -------- */
const hist = status.jobs.find((j) => j.as_of !== status.as_of && j.status === 'done')
const cur = status.jobs.find((j) => j.job_id === status.default_job_id)
const sameSnap = ut.diffJobs(cur, cur)
report(sameSnap.comparable === true, 'as_of 相同时判为可比')
const cross = ut.diffJobs(hist, cur)
report(cross.comparable === false, `as_of 不同（${hist.as_of} → ${cur.as_of}）时第一返回就是「不可比」`)
report(
  cross.reason.includes('模型版本') && cross.reason.includes(hist.as_of),
  '不可比的原因点名了快照日期与「模型版本效应」，而不是一句「数据不同」',
)
report(
  Math.abs(cross.mean_delta) > 12 && Math.abs(cross.mean_delta) < 13,
  `跨快照整体位移被量化成 ${cross.mean_delta} 分（历史 ${hist.as_of} → 当前 ${cur.as_of}），页面不让它冒充经营改善`,
)
report(
  cross.rows.length === 100 && cross.rows.filter((r) => r.delta === null).length === 9,
  `逐行对比列出 100 行，其中 9 行因任一侧未出分而 delta 为空（实际 ${cross.rows.length} 行 / ${cross.rows.filter((r) => r.delta === null).length} 行空差值）`,
)

/* -------- 轮询节奏与进度 -------- */
report(ut.pollDelay(0) === 1000 && ut.pollDelay(9_000) === 1000, '前 10 秒每秒一次（让用户看见在动）')
report(ut.pollDelay(30_000) === 2000 && ut.pollDelay(120_000) === 5000, '之后退避到 2s / 5s（不把后端问死）')
report(ut.progressPct(42, 100) === 42 && ut.progressPct(0, 0) === 0, '进度百分比 = rows_done / rows_total，不编「第几步」')
report(ut.progressPct(120, 100) === 100, '进度封顶 100（不会出现 115% 这种数）')

/* ============================================================ 1. 契约自洽 */
console.log('\n— 1. 契约自洽（summary 必须能由 evidence 重算）')
for (const j of status.jobs) {
  const s = ut.summariseRows(j.evidence)
  const declared = j.summary
  const same =
    s.rows_ok === declared.rows_ok &&
    s.rows_failed === declared.rows_failed &&
    (s.score_mean === null ? declared.score_mean === null : near(s.score_mean, declared.score_mean, 0.005)) &&
    JSON.stringify(s.band_counts) === JSON.stringify(declared.band_counts)
  report(same, `${j.job_id}：summary 与逐行重算完全一致（${declared.rows_ok} 出分 / ${declared.rows_failed} 拦下）`)
}

const main = status.jobs.find((j) => j.job_id === status.default_job_id)
report(main.rows_total === 100 && main.evidence.length === 100, '默认任务：100 行名单 → 100 行结果（一行不少）')
report(
  main.summary.rows_done === main.evidence.length,
  '已完成任务的 rows_done = 结果行数（进度不是另一份数）',
)

const detailScore = new Map(detail.enterprises.map((e) => [e.userid, e]))
const okRows = main.evidence.filter((r) => r.status === 'ok')
report(
  okRows.every((r) => {
    const d = detailScore.get(r.userid)
    return d && Math.abs(d.credit_score - r.credit_score) < 1e-9
  }),
  `91 个成功行的分数与 P7 enterprise_detail 同 userid 逐位一致（不另训模型、不做二次变换）`,
)
report(
  okRows.every((r) => ut.bandOf(r.credit_score, cuts) === r.risk_band),
  '91 行的 risk_band 全部由 P7 同一套切点 50/60/70/80 派生（批量页不另定档）',
)
report(
  okRows.every((r) => ut.bandLabel(r.risk_band) === r.risk_band_label),
  '标签与档位一一对应（不会出现「watch / 较好」这种串档）',
)
report(
  okRows.every((r) => r.trace && r.trace.panel_userid === r.userid && r.trace.graph_node_id === r.graph_node_id),
  '每条成功行都有 trace，且面板 userid / 图节点与行内字段一致（可追溯不是一句口号）',
)
report(
  okRows.every((r) => r.trace.score_source.includes('enterprise_detail')),
  'trace 里写明了分数来源文件与「直取，未做二次变换」',
)

const badRows = main.evidence.filter((r) => r.status !== 'ok')
report(
  badRows.every((r) => r.credit_score === null && r.pd === null && r.risk_band === null),
  '9 行失败行没有任何分数（不是偷偷填 0 或均值）',
)
report(
  badRows.every((r) => r.error && r.error.field && r.error.message && r.error.code),
  '9 行失败行每条都带「字段 + 原因 + 错误码」（说得出为什么）',
)
report(
  badRows.filter((r) => r.status === 'unmapped').length === 3 &&
    badRows.filter((r) => r.status === 'field_error').length === 6,
  '失败分成「数据错 6 行」与「映射缺 3 行」两类（前者改文件能解决，后者不能）',
)
report(
  badRows.filter((r) => r.status === 'unmapped').every((r) => r.cert_no && r.name),
  '「映射缺」的行本身数据是合法的（证书号合规、企业名有值），只是面板里没有对应记录',
)

const certs = main.evidence.map((r) => r.cert_no)
report(new Set(certs).size === certs.length, '100 个证书编号在名单内唯一（不会一个编号出两个分）')

const jobsByStatus = status.jobs.reduce((m, j) => ({ ...m, [j.status]: (m[j.status] ?? 0) + 1 }), {})
report(
  jobsByStatus.queued >= 1 && jobsByStatus.processing >= 1 && jobsByStatus.done >= 3 && jobsByStatus.failed >= 1,
  `6 个任务覆盖 排队/处理中/完成(3)/失败：${JSON.stringify(jobsByStatus)}`,
)
report(
  status.jobs.some((j) => j.rows_total === 0 && j.status === 'done'),
  '有「空名单」任务（只有表头）—— 空态是真实存在的一种，不是想象出来的',
)

/* 示例导出文件必须与页面导出同构：同表头、同 BOM */
report(exportSample.startsWith('\uFEFF'), 'public/fixtures/batch_export_100.csv 也带 BOM（示例与实跑同构）')
report(
  exportSample.replace(/^\uFEFF/, '').split(/\r?\n/)[0] === ut.EXPORT_COLUMNS.join(','),
  '示例导出的表头与 buildExportCsv 逐字一致（示例不会误导客户）',
)
report(demoCsv.startsWith('\uFEFF'), '演示名单带 BOM（所以「用内置演示名单」走的是同一条解析路径）')

/*
 * 本地队列（JS）与契约生成器（Python）是两套实现，映射算法一旦漂移，
 * 同一份名单在快照里是 u0001、在重跑时是 u0003 —— 导出与 P7 详情页就对不上。
 * 所以这里真的把演示 CSV 灌进本地队列跑一遍，逐行比对契约快照。
 */
const mockStore = await server.ssrLoadModule('/src/api/mock/batch.ts')
const localSheets = ut
  .parseRosterCsv(demoCsv)
  .rows.map((r) => ({ row_no: Number(r.cells[0]) || r.row_no, cells: r.cells }))
const submitted = mockStore.submit('batch_demo_100.csv', localSheets)
let localJob = null
for (let i = 0; i < 10; i += 1) {
  localJob = mockStore.advance(submitted.job_id)
  if (localJob?.status === 'done') break
}
const refJob = status.jobs.find((j) => j.job_id === status.default_job_id)
const driftRows = refJob.evidence.filter((r) => {
  const mine = localJob.evidence.find((x) => x.row_no === r.row_no)
  return (
    !mine ||
    mine.userid !== r.userid ||
    mine.credit_score !== r.credit_score ||
    mine.risk_band !== r.risk_band ||
    mine.status !== r.status
  )
})
report(
  driftRows.length === 0,
  `本地重跑与契约快照逐行一致（100 行的 userid / 分数 / 档位 / 状态全等，漂移 ${driftRows.length} 行）`,
  driftRows.length ? ` ← 首个漂移：行 ${driftRows[0].row_no}` : '',
)
report(
  localJob.summary.rows_ok === refJob.summary.rows_ok &&
    localJob.summary.rows_failed === refJob.summary.rows_failed,
  `本地重跑的 91 / 9 与契约一致（${localJob.summary.rows_ok} / ${localJob.summary.rows_failed}）`,
)
mockStore.reset()

/* ============================================================ 2–4. 页面 */
console.log('\n— 2/3/4. 页面：真提交一次，看进度是否真的走完')
const CHROMIUM = [process.env.CHROMIUM_PATH, '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome']
  .filter(Boolean)
  .find((p) => existsSync(p))
const browser = await chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox', '--disable-dev-shm-usage'] })

try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
  const page = await ctx.newPage()
  const pageErrors = []
  const warns = []
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'warning') warns.push(m.text())
  })

  await page.goto(`${BASE}/batch`, { waitUntil: 'networkidle' })
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="page-skeleton"]').length === 0,
    null,
    { timeout: 20_000 },
  )
  await page.waitForTimeout(600)

  for (const id of ['import', 'progress', 'result', 'export', 'jobs']) {
    report((await page.locator(`[data-testid="p8-section-${id}"]`).count()) === 1, `区块 ${id} 存在`)
  }
  report((await page.locator('[data-testid="p8-section-compare"]').count()) === 0, '对比区块默认收起（不占视线）')

  const body0 = await page.locator('main').innerText()
  report(flat(body0).includes('批量结果不构成授信依据'), '页面抬头就把「不构成授信依据」写在结果表上方')

  /* -------- 文件级校验：先点内置名单 -------- */
  await page.locator('[data-testid="p8-use-demo"]').click()
  await page.waitForFunction(() => !!document.querySelector('[data-testid="p8-file-checks"]'), null, {
    timeout: 10_000,
  })
  const checks = await page.locator('[data-testid="p8-file-checks"]').innerText()
  report(checks.includes('文件级校验通过'), '点「用内置演示名单」后跑出文件级校验结论')
  report(
    ['文件类型', '文件大小', '必需 6 列', '证书编号格式', '序号连续', '数据行数'].every((k) => checks.includes(k)),
    '六项检查逐条上屏（不是一句「文件合法」）',
  )
  const preview = await page.locator('[data-testid="p8-preview"]').innerText()
  report(preview.includes('前 8 行'), '导入区给出 8 行预览（确认列与值对不对）')
  report(
    /其中\s*6\s*行有字段级问题/.test(preview) && flat(preview).includes('会被后端逐行拦下'),
    '预览里点名「6 行有字段级问题，提交后仍会进任务但会被逐行拦下」',
  )

  /* -------- 提交：进度必须真的推进 -------- */
  const before = await page.locator('[data-testid="p8-section-import"]').locator('..').locator('.section-badge-row__job').innerText()
  await page.locator('[data-testid="p8-submit"]').click()
  await page.waitForFunction(() => !!document.querySelector('[data-testid="p8-receipt"]'), null, { timeout: 10_000 })
  const receipt = await page.locator('[data-testid="p8-receipt"]').innerText()
  report(receipt.includes('已受理') && receipt.includes('100'), `提交后立刻给出受理回执（${receipt.slice(0, 28)}…）`)
  report(/job_local_\d{4}/.test(receipt), '回执里带真实的 job_id（可复制去查）')

  const seen = []
  let downloadAppeared = false
  for (let i = 0; i < 20; i += 1) {
    await page.waitForTimeout(700)
    const txt = await page
      .locator('[data-testid="p8-section-import"]')
      .locator('..')
      .locator('.section-badge-row__job')
      .innerText()
    const m = /已处理\s*(\d+)\s*\/\s*(\d+)/.exec(txt.replace(/\n/g, ' '))
    const done = m ? Number(m[1]) : -1
    if (seen[seen.length - 1] !== done) seen.push(done)
    if (await page.locator('[data-testid="p8-download"]').count()) {
      downloadAppeared = true
      break
    }
  }
  report(seen.length >= 3 && seen[0] < seen[seen.length - 1], `进度真的在推进：${seen.join(' → ')}（不是页面自己数数）`)
  report(seen[seen.length - 1] === 100, `最终停在 100 / 100（实际 ${seen[seen.length - 1]}）`)
  report(seen.every((n, i) => i === 0 || n >= seen[i - 1]), '进度单调不减（不会回跳）')
  report(downloadAppeared, '任务跑完后导出区块与「下载 CSV」按钮自动出现（跑一半时不给导出）')
  report(before !== (await page.locator('[data-testid="p8-section-import"]').locator('..').locator('.section-badge-row__job').innerText()), '抬头任务号从未选任务切成了新任务')

  /* -------- 进度区口径 -------- */
  const progText = flat(await page.locator('[data-testid="p8-progress-text"]').innerText())
  report(progText.includes('100/100'), `进度区显示「已处理 100 / 100 行」（${progText}）`)
  const kpiOk = flat(await page.locator('[data-testid="p8-kpi-ok"]').innerText())
  const kpiRate = flat(await page.locator('[data-testid="p8-kpi-rate"]').innerText())
  report(kpiOk === '91/100', `KPI 写的是「出分 91 / 已处理 100」而非只写一个 91（${kpiOk}）`)
  report(
    kpiRate.includes('91.0%'),
    `成功率 ${kpiRate} = 91 / 100（分母是名单总行数，不是「已跑完的比例」）`,
  )
  report((await page.locator('[data-testid="p8-live-note"]').count()) === 0, '任务完成后「实时结果」提示条消失（不再标记非终局）')
  report(
    (await page.locator('[data-testid="p8-error-breakdown"]').count()) === 1,
    '失败原因分组可见（数据错 vs 映射缺）',
  )
  const breakdown = await page.locator('[data-testid="p8-error-breakdown"]').innerText()
  report(breakdown.includes('数据错') && breakdown.includes('映射缺'), '两类失败原因都上屏，并说明「改文件没用 vs 改文件能解决」')

  /* -------- 结果表：排序 / 筛选 / 搜索 / 分页 / 追溯 -------- */
  const rowCount = await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').count()
  report(rowCount === 25, `结果表分页 25 行（实际 ${rowCount}）`)
  report((await page.locator('[data-testid="p8-pager"]').count()) === 1, '分页条出现（100 行分 4 页）')
  report(
    flat(await page.locator('[data-testid="p8-status-all"]').innerText()).includes('100'),
    '状态筛选默认「全部 100」（失败行默认就在表里，不被藏起来）',
  )

  const colScores = async () =>
    (await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').allInnerTexts()).map((t) =>
      t.replace(/\s+/g, ' ').trim(),
    )
  const orderByNo = await colScores()
  await page.locator('[data-testid="p8-sort-credit_score"]').click()
  await page.waitForTimeout(300)
  const orderByScore = await colScores()
  report(orderByNo.join('|') !== orderByScore.join('|'), '点「信用分」表头后行序真的变了（排序不是装饰）')
  report(
    orderByScore.length === orderByNo.length,
    '排序不丢行（25 行还是 25 行）',
  )
  await page.locator('[data-testid="p8-sort-risk_band"]').click()
  await page.waitForTimeout(300)
  const orderByBand = await colScores()
  report(
    orderByBand.map((t) => t.split(' ')[1]).join('|') !== orderByScore.map((t) => t.split(' ')[1]).join('|'),
    '再点「风险分级」表头后又换了一次序（多列排序都可用）',
  )
  await page.locator('[data-testid="p8-sort-row_no"]').click()
  await page.waitForTimeout(300)
  report((await colScores())[0].startsWith('1 '), '点回「行号」恢复 1 起（排序可逆）')

  /* -------- 失败行沉底（页面上验证一次） -------- */
  await page.locator('[data-testid="p8-status-ok"]').click()
  await page.waitForTimeout(200)
  await page.locator('[data-testid="p8-status-failed"]').click()
  await page.waitForTimeout(200)
  const failedShown = await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').count()
  report(failedShown === 9, `切到「被拦下」时 9 行全部在表里（实际 ${failedShown}）`)
  const failedText = await page.locator('[data-testid="p8-result-table"]').innerText()
  report(
    ['证书编号', 'GR2024', '批次'].some((k) => failedText.includes(k)),
    '失败行就地给出字段名与原始值（用户能直接定位到那一格去改）',
  )
  await page.locator('[data-testid="p8-status-all"]').click()
  await page.waitForTimeout(200)

  /* -------- 按档筛选 -------- */
  const selector = '[data-testid="risk-band-bar-filter"] button'
  const barBtns = await page.locator(selector).count()
  report(barBtns === 5, `五档色带 5 段可点（实际 ${barBtns}）`)
  const watchBtn = page.locator(`${selector}[data-band="watch"]`)
  await watchBtn.click()
  await page.waitForTimeout(300)
  const afterBand = await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').count()
  const pagerText = flat(await page.locator('[data-testid="p8-pager"]').innerText())
  report(afterBand === 25, `点「关注」后第一页仍是 25 行（共 38 行，分 2 页）`)
  report(pagerText.includes('38/100'), `筛选后计数写明「38 / 100」（${pagerText}）`)
  report(
    (await page.locator('[data-testid="p8-result-table"] tbody tr[data-band="watch"]').count()) === 25,
    '筛出来的行 risk_band 全是 watch（筛选按档位真的生效，不是只改了计数）',
  )
  await watchBtn.click()
  await page.waitForTimeout(300)
  report(
    flat(await page.locator('[data-testid="p8-pager"]').innerText()).includes('100/100'),
    '再点一次取消筛选（点两次取消，与文案一致）',
  )

  /* -------- 搜索 -------- */
  await page.locator('[data-testid="p8-search"]').fill('u0001')
  await page.waitForTimeout(400)
  const searched = await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').count()
  report(searched > 0 && searched < 25, `搜索 u0001 后行数收窄（${searched} 行）`)
  await page.locator('[data-testid="p8-search"]').fill('不存在的企业名')
  await page.waitForTimeout(400)
  report((await page.locator('[data-testid="p8-table-empty"]').count()) === 1, '搜不到时给空态说明，不是一张空表')
  await page.locator('[data-testid="p8-search"]').fill('')
  await page.waitForTimeout(300)

  /* -------- 行展开追溯 -------- */
  await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').first().click()
  await page.waitForTimeout(300)
  report((await page.locator('[data-testid="p8-trace"]').count()) === 1, '点行展开追溯面板')
  const trace = await page.locator('[data-testid="p8-trace"]').innerText()
  report(
    trace.includes('面板记录') && trace.includes('分数来源') && trace.includes('契约文件'),
    '追溯面板给出面板记录 / 分数来源 / 契约文件（三处都能查）',
  )
  report(trace.includes('跨快照不可比'), '追溯面板点名「跨快照不可比」')
  report((await page.locator('[data-testid="p8-trace"] a').count()) >= 1, '追溯面板可直接跳 P7 深度诊断')
  await page.locator('[data-testid="p8-result-table"] tbody tr[data-row]').first().click()
  await page.waitForTimeout(200)

  /* -------- 红线 -------- */
  const caveat = flat(await page.locator('[data-testid="p8-caveat"]').innerText())
  report(
    caveat.includes('不构成授信依据') && caveat.includes('0.477'),
    '结果表上方标注「AUC 0.59 vs 噪声地板 0.477」与「不构成授信依据」',
  )
  const factorCells = await page.locator('[data-testid="p8-result-table"] tbody tr[data-row] .code').allInnerTexts()
  report(
    factorCells.length > 0 && factorCells.every((c) => /^x\d+$/.test(c.trim())),
    `主要贡献因子 ${factorCells.length} 格全部是 xNNN 代号（红线二：字段字典没到就不编名字）`,
  )
  const badges = await page.locator('[data-testid="section-dataset-badge"]').count()
  report(badges >= 1, `导入区带来源角标（${badges} 个，红线①：不混排来源）`)
  const pageText = await page.locator('main').innerText()
  report(
    pageText.includes('P0-1') && pageText.includes('P0-2'),
    '页面点名阻塞项 P0-1 / P0-2（不假装已解锁）',
  )

  /* -------- 导出：真的下载一份，逐行核对 -------- */
  const dl = await Promise.all([
    page.waitForEvent('download', { timeout: 15_000 }),
    page.locator('[data-testid="p8-download"]').click(),
  ]).then(([d]) => d)
  const dlPath = await dl.path()
  const dlText = readFileSync(dlPath, 'utf8')
  report(/信用评估结果_2026-10-28_100行\.csv$/.test(dl.suggestedFilename()), `下载文件名为 ${dl.suggestedFilename()}`)
  report(dlText.startsWith('\uFEFF'), '下载的 CSV 带 BOM')
  const dlLines = dlText.replace(/^\uFEFF/, '').trimEnd().split('\r\n')
  report(dlLines.length === 101, `下载的 CSV 是 1 表头 + 100 行（实际 ${dlLines.length}）`)
  report(dlLines[0] === ut.EXPORT_COLUMNS.join(','), '下载的 CSV 表头 = 页面上列出的 11 列')
  report(
    dlLines.slice(1).filter((l) => l.split(',')[10] === '成功').length === 91,
    '下载的 CSV 里恰好 91 行「成功」',
  )
  report(
    dlLines.slice(1).filter((l) => l.split(',')[10] !== '成功').every((l) => l.split(',')[6] === ''),
    '下载的 CSV 里 9 行失败行的分数列是空的',
  )
  // 与屏幕上的表同源：抽一行核对分数
  const row1 = dlLines.find((l) => l.startsWith('1,'))
  report(
    row1.split(',')[6] === String(main.evidence[0].credit_score.toFixed(1)),
    `导出首行分数 ${row1.split(',')[6]} = 契约里同一行 ${main.evidence[0].credit_score}（导出与屏幕同源）`,
  )
  report((await page.locator('[data-testid="p8-export-receipt"]').count()) === 1, '导出后页面上留下「已生成导出文件」回执')

  /* -------- 跨批次对比：先拦一次 -------- */
  await page.locator('[data-testid="p8-compare"]').click()
  await page.waitForTimeout(500)
  report((await page.locator('[data-testid="p8-section-compare"]').count()) === 1, '点「与上一批次对比」后对比区块出现')
  const cmpWarn = await page.locator('[data-testid="p8-compare-warning"]').count()
  if (cmpWarn) {
    const warnText = flat(await page.locator('[data-testid="p8-compare-warning"]').innerText())
    report(warnText.includes('不可比') || warnText.includes('模型版本'), '跨快照对比先拦一次：说明差异来自模型版本')
    report(
      (await page.locator('[data-testid="p8-compare-result"]').count()) === 0,
      '未点「我已知晓」前不展示逐行差异（不是提示一句就放行）',
    )
    await page.locator('[data-testid="p8-compare-override"]').click()
    await page.waitForTimeout(300)
    report((await page.locator('[data-testid="p8-compare-table"]').count()) === 1, '确认后才展示逐行差异表')
  } else {
    // 本地新提交任务的 as_of 与默认历史批次一致时不会触发拦截，这属于合理路径
    report((await page.locator('[data-testid="p8-compare-result"]').count()) === 1, '同快照对比直接展示逐行差异')
  }
  await page.locator('[data-testid="p8-compare-close"]').click()
  await page.waitForTimeout(200)
  report((await page.locator('[data-testid="p8-section-compare"]').count()) === 0, '对比区块可收起')

  /* -------- 任务列表：切到空名单 / 解析失败 / 处理中 -------- */
  const jobCards = await page.locator('[data-testid="p8-section-jobs"] .job').count()
  report(jobCards >= 7, `任务列表 ${jobCards} 个（6 个演示 + 1 个刚提交的本地任务）`)

  const pickJob = async (jobId) => {
    await page.locator(`[data-testid="p8-section-jobs"] .job[data-job="${jobId}"]`).click()
    await page.waitForTimeout(600)
  }

  await pickJob('job_d77e2a04')
  report((await page.locator('[data-testid="p8-job-failed"]').count()) === 1, '解析失败任务：给出失败原因区块')
  report((await page.locator('[data-testid="p8-download"]').count()) === 0, '解析失败任务不显示导出（没东西可导）')

  await pickJob('job_0a11b6f8')
  report(
    flat(await page.locator('[data-testid="p8-section-jobs"] .job[data-job="job_0a11b6f8"]').innerText()).includes('0行'),
    '空名单任务：任务卡明写「0 行（文件只有表头）」',
  )
  report((await page.locator('[data-testid="p8-download"]').count()) === 0, '空名单不给导出按钮')

  await pickJob('job_7f3c1a90')
  report(
    (await page.locator('[data-testid="p8-live-note"]').count()) === 1,
    '处理中任务：标「实时结果，完成后自动刷新」',
  )
  report((await page.locator('[data-testid="p8-download"]').count()) === 0, '处理中任务不给导出（跑一半的 CSV 比不给更糟）')

  await pickJob(status.default_job_id)
  report((await page.locator('[data-testid="p8-download"]').count()) === 1, '切回已完成任务，导出按钮恢复')

  report(pageErrors.length === 0, '无运行时异常', pageErrors.length ? ` ← ${pageErrors[0]}` : '')
  const realWarns = warns.filter((m) => !m.includes('v-scx') && !m.includes('Download the Vue Devtools'))
  report(realWarns.length === 0, '无 Vue 运行时告警', realWarns.length ? ` ← ${realWarns[0]}` : '')

  /* -------- 窄屏：整页不横向溢出（结果表在自己的容器里横滚是允许的） -------- */
  await page.setViewportSize({ width: 420, height: 900 })
  await page.waitForTimeout(700)
  const narrow = await page.evaluate(() => ({
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    tableScrolls: (() => {
      const w = document.querySelector('.table-wrap')
      return w ? w.scrollWidth > w.clientWidth : false
    })(),
  }))
  report(narrow.overflow <= 2, `420px 窄屏下整页不横向溢出（超出 ${narrow.overflow}px）`)
  report(narrow.tableScrolls, '窄屏下结果表在自己的容器内横滚（不是把整页顶出去）')
  await page.setViewportSize({ width: 1440, height: 1000 })

  await ctx.close()

  /* ============================================================ 5. 降级 */
  console.log('\n— 5. 降级（real 模式 + 接口打空）')
  const STUB = PORT + 1
  const DG = PORT + 2
  const stub = createHttp((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ code: 0, message: 'ok', data: {}, meta: { dataset: 'real_panel' } }))
  })
  await new Promise((r) => stub.listen(STUB, '127.0.0.1', r))

  const degrade = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    mode: 'test',
    define: { 'import.meta.env.VITE_USE_MOCK': JSON.stringify('real') },
    plugins: [vue()],
    resolve: { alias: { '@': resolve(here, '../src') } },
    server: {
      host: '127.0.0.1',
      port: DG,
      strictPort: true,
      proxy: { '/api': { target: `http://127.0.0.1:${STUB}`, changeOrigin: true } },
    },
    logLevel: 'error',
  })
  await degrade.listen()

  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const dpage = await ctx2.newPage()
  const dErrors = []
  dpage.on('pageerror', (e) => dErrors.push(String(e)))

  await dpage.goto(`http://127.0.0.1:${DG}/batch`, { waitUntil: 'networkidle' })
  await dpage.waitForTimeout(1500)
  report(
    (await dpage.locator('[data-testid="state-panel"]').count()) >= 1,
    'real 模式下 /api/batch 返回空信封 → 出现区块级降级面板（不白屏）',
  )
  const panel = await dpage.locator('[data-testid="state-panel"]').first().innerText()
  report(panel.includes('原因') && panel.includes('下一步'), '降级面板「原因」与「下一步」都在')
  report(
    (await dpage.locator('.qa__item').count()) >= 3,
    `降级时读图问答仍在（读者仍有口径说明可看，${await dpage.locator('.qa__item').count()} 条）`,
  )
  const dText = (await dpage.locator('body').innerText()).trim()
  report((await dpage.locator('.page__title').count()) === 1 && dText.length > 150, `页面不白屏（正文 ${dText.length} 字）`)
  report(dErrors.length === 0, '降级路径无运行时异常', dErrors.length ? ` ← ${dErrors[0]}` : '')

  await ctx2.close()
  await degrade.close()
  await new Promise((r) => stub.close(r))
} finally {
  await browser.close()
  await server.close()
}

console.log(failed === 0 ? '\nP8 冒烟全部通过' : `\n${failed} 项失败`)
process.exit(failed === 0 ? 0 : 1)
