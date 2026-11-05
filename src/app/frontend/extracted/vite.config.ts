import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

/**
 * 后端框架：FastAPI（后端答复 3.1）。
 * 开发期通过 Vite 代理把 /api 转发到本地后端，前端代码里只写相对路径 `/api/...`，
 * 因此切换「本地后端 / 部署后端 / mock」时不需要改页面代码（FE-02 会在此基础上做接口层）。
 */
const DEFAULT_API_PROXY_TARGET = 'http://127.0.0.1:8000'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || DEFAULT_API_PROXY_TARGET

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: false,
      open: false,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
          ws: false,
          // 后端接口本身即以 /api 为前缀（见后端 API 与数据表 Schema），因此不做 rewrite。
          // 若后端改为无前缀挂载，打开下面一行即可：
          // rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    build: {
      target: 'es2020',
      sourcemap: mode !== 'production',
      chunkSizeWarningLimit: 1200,
      rollupOptions: {
        output: {
          // echarts / zrender 单独成包（力导向图是重依赖）；
          // 用函数式分包，未引入时不会产生空 chunk。
          manualChunks(id) {
            if (id.includes('node_modules/echarts') || id.includes('node_modules/zrender')) {
              return 'echarts'
            }
            if (id.includes('node_modules')) {
              return 'vendor'
            }
            return undefined
          },
        },
      },
    },
  }
})
