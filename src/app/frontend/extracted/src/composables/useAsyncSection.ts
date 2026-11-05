/**
 * 异步区块状态机（FE-04）
 *
 * 解决的问题：验收要求「统一骨架屏（>300ms）」——意思是
 *   **只有真正等了超过 300ms 才显示骨架**，否则本地 mock 秒回时
 *   骨架一闪而过，比没有骨架更晃眼。
 *
 * 状态流转：
 *   idle → loading ─┬→ ready（有数据）
 *                   ├→ empty（loader 返回空数组/null）
 *                   └→ error（可读原因 + 重试）
 *
 * 另外两个细节，都是踩过坑才加的：
 *   - `requestId` 竞态守卫：快速切页面时旧请求晚到会覆盖新数据，这里用递增 id 丢弃过期响应；
 *   - `retry()` 复用同一个 loader，不需要调用方重写逻辑。
 */
import { computed, onScopeDispose, ref, type Ref } from 'vue'

import { toReadableError, type ReadableError } from '@/utils/errors'

export type SectionStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'error'

export interface UseAsyncSectionOptions<T> {
  /** 骨架屏延迟阈值，默认 300ms（契约 §7.1） */
  skeletonDelay?: number
  /** 判定为空的条件，默认「null / undefined / 空数组」 */
  isEmpty?: (data: T) => boolean
  /** 首次挂载即加载，默认 true */
  immediate?: boolean
}

const defaultIsEmpty = (data: unknown): boolean =>
  data === null ||
  data === undefined ||
  (Array.isArray(data) && data.length === 0) ||
  (typeof data === 'object' &&
    data !== null &&
    !Array.isArray(data) &&
    Object.keys(data as Record<string, unknown>).length === 0)

export function useAsyncSection<T>(
  loader: () => Promise<T>,
  options: UseAsyncSectionOptions<T> = {},
) {
  const { skeletonDelay = 300, isEmpty = defaultIsEmpty, immediate = true } = options

  const status = ref<SectionStatus>('idle')
  // ⚠️ 必须是 ref 不是 shallowRef：`data` 承载的是接口信封（{code,message,data,meta}），
  // 而页面普遍写成 `section.data.value.data`；shallowRef 只追 .value 的替换，
  // 首次赋值时模板里 .data 还读不到（表现为"永远停在未就绪"）。
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<ReadableError | null>(null) as Ref<ReadableError | null>
  const showSkeleton = ref(false)

  let timer: ReturnType<typeof setTimeout> | undefined
  let requestId = 0

  function clearTimer() {
    if (timer !== undefined) {
      clearTimeout(timer)
      timer = undefined
    }
  }

  /** 等到超过 skeletonDelay 仍在 loading，才把骨架打开 */
  function armSkeleton() {
    clearTimer()
    showSkeleton.value = false
    if (skeletonDelay <= 0) {
      showSkeleton.value = true
      return
    }
    timer = setTimeout(() => {
      if (status.value === 'loading') showSkeleton.value = true
    }, skeletonDelay)
  }

  async function load(): Promise<void> {
    const id = ++requestId
    status.value = 'loading'
    error.value = null
    armSkeleton()

    try {
      const result = await loader()
      if (id !== requestId) return // 过期响应，丢弃
      data.value = result
      status.value = isEmpty(result) ? 'empty' : 'ready'
    } catch (err) {
      if (id !== requestId) return
      error.value = toReadableError(err)
      status.value = 'error'
    } finally {
      if (id === requestId) {
        clearTimer()
        showSkeleton.value = false
      }
    }
  }

  function retry(): Promise<void> {
    return load()
  }

  /** 局部刷新：保留旧数据直到新数据到达，不闪骨架 */
  async function refresh(): Promise<void> {
    const id = ++requestId
    error.value = null
    try {
      const result = await loader()
      if (id !== requestId) return
      data.value = result
      status.value = isEmpty(result) ? 'empty' : 'ready'
    } catch (err) {
      if (id !== requestId) return
      error.value = toReadableError(err)
      status.value = 'error'
    }
  }

  function reset() {
    requestId++
    clearTimer()
    status.value = 'idle'
    data.value = null
    error.value = null
    showSkeleton.value = false
  }

  onScopeDispose(() => {
    // 注意：**不要**在这里 `requestId++`。
    // 首次挂载时「在途请求」正是我们自己发出的那个，一旦组件因为任何原因
    // 被重新创建/卸载一次，这条自增就会把唯一一次请求判成过期响应，
    // 状态永远停在 loading（dev 实测踩到过）。竞态的正确判据是「有没有更新的请求」，
    // 那由下一次 load() 自己的 `++requestId` 负责；这里只需停掉计时器。
    clearTimer()
  })

  if (immediate) void load()

  return {
    status,
    data,
    error,
    showSkeleton,
    isLoading: computed(() => status.value === 'loading'),
    isReady: computed(() => status.value === 'ready'),
    isEmpty: computed(() => status.value === 'empty'),
    isError: computed(() => status.value === 'error'),
    load,
    retry,
    refresh,
    reset,
  }
}

/**
 * 断网监听（FE-04 验收「断网不白屏」）。
 *
 * 断网时不只靠请求失败来体现：顶部会给一条持久提示条，
 * 让评审在没点任何按钮时也能看出「现在是离线演示态」。
 */
export function useOnlineStatus() {
  const online = ref(typeof navigator === 'undefined' ? true : navigator.onLine)

  if (typeof window !== 'undefined') {
    const on = () => (online.value = true)
    const off = () => (online.value = false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    onScopeDispose(() => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    })
  }

  return { online }
}
