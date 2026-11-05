import pluginVue from 'eslint-plugin-vue'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{ts,mts,tsx,vue}'],
  },
  {
    name: 'app/files-to-ignore',
    ignores: ['**/dist/**', '**/dist-ssr/**', '**/coverage/**', '**/node_modules/**'],
  },
  pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,
  {
    name: 'app/project-rules',
    rules: {
      // 组件文件统一 PascalCase 多词命名（视图以 `XxxView` 结尾）
      'vue/multi-word-component-names': ['error', { ignores: ['App'] }],
      // 占位页阶段允许 console.warn，其余 console 由 review 把关
      'no-console': ['warn', { allow: ['warn', 'error'] }],
    },
  },
  {
    // Node 侧脚本（路由冒烟测试等）需要往终端打印结果
    name: 'app/node-scripts',
    files: ['scripts/**/*.{js,mjs,cjs}'],
    languageOptions: {
      globals: { console: 'readonly', process: 'readonly' },
    },
    rules: {
      'no-console': 'off',
    },
  },
  skipFormatting,
)
