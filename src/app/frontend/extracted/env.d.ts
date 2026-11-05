/// <reference types="vite/client" />

/** Vite 环境变量（fe-spec：mock 开关收口在接口层，页面代码不感知） */
interface ImportMetaEnv {
  readonly VITE_USE_MOCK?: 'mock' | 'real' | 'auto'
  readonly VITE_API_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
