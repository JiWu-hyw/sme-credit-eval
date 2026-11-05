# 点文件（dotfiles）内容存档

> 本文件是 FE-00 前端工程三个点文件的**内容存档**。
> 项目网盘（tdrive）不允许文件名以 `.` 开头，故这三个文件无法以原名上传。
> 需要重建仓库时，请按下列文件名和内容在 `frontend/` 根目录原样还原。
>
> - 生成日期：2026-10-15
> - 对应仓库：`sme-credit-eval`（GitHub Private）
> - 归属任务：FE-00 【前端·基础】前端工程初始化与协作规范

---

## 1. `.gitignore`

还原路径：`frontend/.gitignore`

```gitignore
node_modules
dist
dist-ssr
coverage
*.local

# 编辑器
.vscode/*
!.vscode/extensions.json
.idea
*.suo
*.ntvs*
*.njsproj
*.sln
*.sw?

# 日志
logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*

# 环境变量（本地覆盖，不入库）
.env.local
.env.*.local
```

---

## 2. `.prettierignore`

还原路径：`frontend/.prettierignore`

```gitignore
dist
dist-ssr
coverage
node_modules
*.local
package-lock.json

# 生成产物（scripts/build_viz_theme.py 自动生成，禁止手工编辑；改动即破坏 --check 幂等）
src/styles/theme.css
src/styles/theme.ts
```

---

## 3. `.prettierrc.json`

还原路径：`frontend/.prettierrc.json`

```json
{
  "$schema": "https://json.schemastore.org/prettierrc",
  "semi": false,
  "singleQuote": true,
  "printWidth": 100,
  "tabWidth": 2,
  "trailingComma": "all",
  "arrowParens": "always",
  "endOfLine": "lf",
  "vueIndentScriptAndStyle": false
}
```

---

## 说明

- `.gitignore` 与《Git 仓库搭建与协作规范》（T00 v1.0）配套，用于挡住 `node_modules/`、`dist/`、真实数据与密钥。
- `.prettierignore` 中排除 `package-lock.json`，避免 formatter 反复改写锁文件造成无意义 diff。
- `.prettierrc.json` 与 `eslint.config.js` 的类型/风格规则保持一致（无分号、单引号、printWidth 100）。
