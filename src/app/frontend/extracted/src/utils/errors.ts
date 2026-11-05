/**
 * 错误可读化（FE-04）
 *
 * 目标：页面**永不**把 `Error: Request failed with status code 500`
 * 或 `AxiosError` 这类东西直接怼到用户脸上，也**永不**整页白屏。
 *
 * 分三层：
 *   1) 传输层（断网 / DNS / 超时）→ 说「网络」并给「检查网络后重试」；
 *   2) 业务层（后端给了 code / message）→ 原样透出 code，message 取后端文案；
 *   3) 未知 → 兜底文案 + 错误摘要，绝不留空白。
 *
 * 刻意不 import `@/api/codes`：阻塞码枚举由 FE-02 维护，
 * 这里只做「有 code 就展示」的通用处理，避免枚举改名时布局层跟着编译失败。
 */

export interface ReadableError {
  /** 一句话原因（可读，不含堆栈） */
  reason: string
  /** 下一步动作（空态/错误态都必须给） */
  nextStep: string
  /** 技术摘要：code / status，供排查与截图归档 */
  detail: string
}

const OFFLINE: ReadableError = {
  reason: '当前设备处于离线状态，请求没有发出去。',
  nextStep: '确认网络恢复后点「重试」；若是会议现场断网，可先切到 mock 模式演示（VITE_USE_MOCK=mock）。',
  detail: 'navigator.onLine = false',
}

const TIMEOUT: ReadableError = {
  reason: '接口响应超时，后端可能正在跑批量评估或模型推理。',
  nextStep: '稍等 10 秒再重试；持续超时请联系后端确认服务是否卡在长任务上。',
  detail: 'ECONNABORTED / timeout',
}

const NETWORK: ReadableError = {
  reason: '连不上后端服务，请求被拒绝或地址不可达。',
  nextStep: '确认后端（FastAPI :8000）已启动、Vite 代理指向正确，再点「重试」。',
  detail: 'Network Error / ERR_CONNECTION_REFUSED',
}

interface ErrorLike {
  code?: unknown
  status?: unknown
  message?: unknown
  response?: { status?: unknown; data?: { code?: unknown; message?: unknown } }
}

function pick(err: unknown): ErrorLike {
  return (typeof err === 'object' && err !== null ? err : {}) as ErrorLike
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v.trim() : undefined
}

/** 把任意抛出的东西翻译成「原因 + 下一步 + 摘要」 */
export function toReadableError(err: unknown): ReadableError {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return OFFLINE

  const e = pick(err)
  const raw = str(e.message) ?? ''
  const code = str(e.code) ?? str(e.response?.data?.code)
  const status = e.response?.status ?? e.status

  if (/timeout|ECONNABORTED/i.test(raw) || code === 'ECONNABORTED') return TIMEOUT
  if (/Network Error|ERR_CONNECTION|Failed to fetch/i.test(raw)) return NETWORK

  const backendMsg = str(e.response?.data?.message)
  const detailBits = [code && `code=${code}`, status && `status=${String(status)}`].filter(Boolean)

  if (backendMsg || code) {
    return {
      reason: backendMsg ?? `后端返回了阻塞码（${code}），本页所需数据暂未就绪。`,
      nextStep: code
        ? `该阻塞码由接口层定义，可对照《接口契约》确认依赖项；补齐后点「重试」。`
        : '点「重试」；若反复出现，截图本页含 code 的提示反馈给后端。',
      detail: detailBits.join(' · ') || 'no detail',
    }
  }

  if (raw) {
    return {
      reason: '页面渲染时出现异常，当前区块无法显示。',
      nextStep: '点「重试」重建该区块；若仍失败，把下方摘要发给前端负责人定位。',
      detail: raw.slice(0, 200),
    }
  }

  return {
    reason: '发生了未预期的错误，当前区块没有拿到可用数据。',
    nextStep: '点「重试」；持续失败请携带浏览器 Console 截图反馈。',
    detail: 'unknown error',
  }
}
