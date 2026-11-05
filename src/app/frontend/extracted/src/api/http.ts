/**
 * HTTP 客户端（FE-02）
 *
 * 统一拦截器：
 *   - 响应信封原样透传（code / message / data / meta），不在这一层改字段名；
 *   - 错误归一为 ApiError，交给 utils/errors.ts 翻译成「原因 + 下一步」。
 */
import axios, { type AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'

export class ApiError extends Error {
  code?: string
  status?: number
  response?: { status?: number; data?: { code?: string; message?: string } }

  constructor(message: string, opts: { code?: string; status?: number } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = opts.code
    this.status = opts.status
  }
}

export const http: AxiosInstance = axios.create({
  baseURL: import.meta.env?.VITE_API_BASE ?? '/api',
  timeout: 30_000,
})

http.interceptors.response.use(
  (res) => res,
  (error: AxiosError<{ code?: string; message?: string }>) => {
    const status = error.response?.status
    const data = error.response?.data
    throw new ApiError(data?.message ?? error.message ?? '请求失败', {
      code: data?.code,
      status,
    })
  },
)

/**
 * 细节直取（FE-12 新增，P3 用）。
 *
 * 为什么不用 `call()`：`call` 是「auto 模式失败就降级 mock」的封装，适合页面首屏取数；
 * 而「切到全图时带 sample_cap 重新取一份」属于局部刷新，失败必须**如实报错**，
 * 不能偷偷回落到抽样版——否则用户以为自己在看全图。
 */
export async function httpGetData<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const res = await http.get<{ data: T }>(url, {
    params,
    headers: { Accept: 'application/json' },
  } as AxiosRequestConfig)
  const body = res.data as { data?: T }
  return body?.data as T
}
