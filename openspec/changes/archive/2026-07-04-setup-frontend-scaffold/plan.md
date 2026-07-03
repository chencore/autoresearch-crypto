# Plan: setup-frontend-scaffold

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **6 个阶段**，每个阶段产出可独立验证的增量。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | 项目初始化与依赖 | `package.json` + `.env.example` + `.gitignore` + `index.html` + 依赖可 install | 0.25d |
| S2 | Vite 与 TS 配置 | `vite.config.ts` + `tsconfig.json` + `vite-env.d.ts` | 0.25d |
| S3 | app 实例与全局布局 | `main.ts` + `App.vue` + `router/index.ts` | 0.5d |
| S4 | axios 客户端 | `api/client.ts` + `api/health.ts` | 0.25d |
| S5 | 占位页面 | 四个 `.vue` 页面 | 0.25d |
| S6 | 启动验证 | 浏览器 + curl + ws + typecheck 全绿 | 0.25d |

**总估时**：约 1.75 人日。

---

## S1. 项目初始化与依赖

### 目标
建立 `frontend/` 独立 pnpm 项目，依赖可独立安装。

### 实施步骤

1. 删除 `frontend/.gitkeep`
2. 创建 `frontend/package.json`：
   - `name`: `autoresearch-crypto-frontend`，`version`: `0.1.0`，`private: true`，`type: module`
   - `dependencies`: `vue@^3.4`、`vue-router@^4.3`、`pinia@^2.1`、`naive-ui@^2.38`、`axios@^1.7`
   - `devDependencies`: `typescript@^5.4`、`vite@^5.2`、`@vitejs/plugin-vue@^5.0`、`vue-tsc@^2.0`
   - `scripts`: `dev: vite`、`build: vue-tsc --noEmit && vite build`、`preview: vite preview`、`typecheck: vue-tsc --noEmit`
3. 创建 `frontend/.env.example`：`VITE_API_BASE_URL=/api/v1`
4. 创建 `frontend/.gitignore`：`node_modules/`、`dist/`、`.env`、`*.log`、`.vite/`
5. 创建 `frontend/index.html`：
   - `<!DOCTYPE html>` + `<html lang="zh-CN">` + `<head>` meta + `<title>autoresearch-crypto</title>`
   - `<body><div id="app"></div><script type="module" src="/src/main.ts"></script></body>`
6. 在 `frontend/` 目录运行 `pnpm install`，验证依赖安装成功

### ✅ 完成验证
- [ ] `frontend/node_modules/` 存在
- [ ] `pnpm list vue vue-router pinia naive-ui axios vite typescript` 全部显示已安装
- [ ] `frontend/.env.example`、`frontend/.gitignore`、`frontend/index.html` 存在

---

## S2. Vite 与 TypeScript 配置

### 目标
Vite 开发服务器可启动，TS 严格模式就绪，proxy 配置就绪。

### 实施步骤

1. 创建 `frontend/vite.config.ts`：
   - `import { defineConfig } from 'vite'` + `import vue from '@vitejs/plugin-vue'`
   - `plugins: [vue()]`
   - `server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true }, '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true } } }`
2. 创建 `frontend/tsconfig.json`：
   - `compilerOptions`: `strict: true`、`target: ES2020`、`module: ESNext`、`moduleResolution: bundler`、`jsx: preserve`、`types: ["vite/client"]`、`skipLibCheck: true`、`esModuleInterop: true`
   - `include`: `["src/**/*.ts", "src/**/*.vue"]`
   - `references`: `[{ path: "./tsconfig.node.json" }]`
3. 创建 `frontend/tsconfig.node.json`：
   - `compilerOptions`: `composite: true`、`module: ESNext`、`moduleResolution: bundler`、`types: ["node"]`
   - `include`: `["vite.config.ts"]`
4. 创建 `frontend/src/vite-env.d.ts`：
   - `/// <reference types="vite/client" />`
   - `declare module '*.vue' { import type { DefineComponent } from 'vue'; const component: DefineComponent<{}, {}, any>; export default component; }`

### ✅ 完成验证
- [ ] `pnpm dev` 能启动（即使没有源文件，Vite 也能启动，访问 5173 报 404 即可）
- [ ] `pnpm typecheck` 无错误（即使没源文件）

---

## S3. app 实例与全局布局

### 目标
Vue app 可挂载，路由可切换，全局布局渲染。

### 实施步骤

1. 创建 `frontend/src/main.ts`：
   - `import { createApp } from 'vue'`、`import { createPinia } from 'pinia'`、`import naive from 'naive-ui'`、`import App from './App.vue'`、`import router from './router'`
   - `const app = createApp(App); app.use(router); app.use(createPinia()); app.use(naive); app.mount('#app')`
2. 创建 `frontend/src/App.vue`：
   - `<template>`：`<n-layout has-sider>` → `<n-layout-sider bordered>` 含 `<n-menu :options="menuOptions" :value="activeKey" @update:value="onMenuSelect" />` → `<n-layout-content><router-view /></n-layout-content>`
   - `<script setup lang="ts">`：menuOptions 四项（策略管理 strategies / 回测可视化 backtest / 实盘监控 live / 参数调优 evolve）；`const route = useRoute()`；`activeKey = computed(() => route.path)`；`onMenuSelect(key) = router.push(key)`
3. 创建 `frontend/src/router/index.ts`：
   - `import { createRouter, createWebHistory } from 'vue-router'`
   - `routes`: `{ path: '/', redirect: '/strategies' }`、`{ path: '/strategies', component: () => import('@/pages/Strategies.vue') }`、`/backtest`、`/live`、`/evolve`、`{ path: '/:pathMatch(.*)*', redirect: '/strategies' }`
   - `export default createRouter({ history: createWebHistory(), routes })`
4. 创建 `frontend/tsconfig.json` 补 `paths`：`"@/*": ["src/*"]`，并在 `vite.config.ts` 加 `resolve.alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }`

### ✅ 完成验证
- [ ] `pnpm dev` 启动，浏览器打开 5173 自动重定向到 `/strategies`
- [ ] 侧边栏四项可点击切换，当前路由对应项高亮
- [ ] 主内容区显示对应页面（占位即可）

---

## S4. axios 客户端

### 目标
统一 axios 实例与错误处理，health 联通函数就绪。

### 实施步骤

1. 创建 `frontend/src/api/client.ts`：
   - `import axios from 'axios'`
   - `const client = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1' })`
   - 响应拦截器：`client.interceptors.response.use(r => r.data, err => { const e = err.response?.data?.error; return Promise.reject(e ? { code: e.code, message: e.message } : { code: 'network', message: err.message }); })`
   - `export default client`
2. 创建 `frontend/src/api/health.ts`：
   - `export async function checkHealth(): Promise<{ status: string; version: string }> { const r = await fetch('/health'); return r.json(); }`

### ✅ 完成验证
- [ ] `pnpm typecheck` 无错误
- [ ] 浏览器 console 手动调 `client.get('/strategy')` 能拿到后端响应（需 S5 页面或临时脚本）

---

## S5. 占位页面

### 目标
四个业务页面渲染占位内容，调通后端占位接口。

### 实施步骤

1. 创建 `frontend/src/pages/Strategies.vue`：
   - `<template>`：`<n-card title="策略管理"><n-space><n-tag type="info">todo</n-tag></n-space><pre>{{ resp }}</pre></n-card>`
   - `<script setup lang="ts">`：`import { ref, onMounted } from 'vue'`、`import client from '@/api/client'`；`const resp = ref('')`；`onMounted(async () => { resp.value = JSON.stringify(await client.get('/strategy')); })`
2. 创建 `frontend/src/pages/Backtest.vue`：同上，标题 "回测可视化"，调 `/backtest`
3. 创建 `frontend/src/pages/Live.vue`：同上，标题 "实盘监控"，调 `/live`
4. 创建 `frontend/src/pages/Evolve.vue`：同上，标题 "参数调优"，调 `/evolve`

### ✅ 完成验证
- [ ] 四个页面都能渲染，显示后端返回的 `{"module": "...", "status": "todo"}`

---

## S6. 启动验证

### 目标
前后端联通全链路冒烟。

### 实施步骤

1. 在 `backend/` 目录运行 `uv run uvicorn app.main:app --port 8000`，保持运行
2. 在 `frontend/` 目录运行 `pnpm dev`，保持运行
3. 浏览器打开 `http://localhost:5173/`，执行验证清单
4. 验证 `pnpm typecheck` 无错误

### ✅ 完成验证
- [ ] 浏览器打开 `http://localhost:5173/` 自动重定向到 `/strategies`
- [ ] Strategies 页面显示后端返回 `{"module":"strategy","status":"todo"}`
- [ ] Backtest 页面显示 `{"module":"backtest","status":"todo"}`
- [ ] Live 页面显示 `{"module":"live","status":"todo"}`
- [ ] Evolve 页面显示 `{"module":"evolve","status":"todo"}`
- [ ] 侧边栏四项切换，当前路由高亮
- [ ] 浏览器 console 执行 `new WebSocket('ws://localhost:5173/api/v1/ws/test')` 后能收到 `{"status":"todo"}` 消息并关闭
- [ ] 浏览器 console 执行 `fetch('/health').then(r=>r.json()).then(console.log)` 输出 `{status:"ok",version:"0.1.0"}`
- [ ] `pnpm typecheck` 退出码 0

---

## 风险与回滚

- **风险**：Vite proxy WebSocket 不生效 → 缓解：`vite.config.ts` 显式 `ws: true`，S6 用浏览器 console 验证
- **风险**：vue-tsc 严格模式报未定义类型 → 缓解：占位组件类型简单；复杂类型留给业务 task
- **风险**：`@/` alias 配置缺失导致 import 失败 → 缓解：S3 同时配 tsconfig paths 与 vite resolve.alias
- **回滚**：所有改动集中在 `frontend/` 新增文件，`git checkout` 删除即可
