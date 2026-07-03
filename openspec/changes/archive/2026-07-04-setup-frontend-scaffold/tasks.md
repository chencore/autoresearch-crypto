## 1. 项目初始化

- [x] 1.1 删除 `frontend/.gitkeep`，创建 `frontend/package.json`：name=`autoresearch-crypto-frontend`，dependencies（vue@^3.4、vue-router@^4.3、pinia@^2.1、naive-ui@^2.38、axios@^1.7），devDependencies（typescript@^5.4、vite@^5.2、@vitejs/plugin-vue@^5.0、vue-tsc@^2.0），scripts（dev/build/preview/typecheck）
- [x] 1.2 创建 `frontend/.env.example`：`VITE_API_BASE_URL=/api/v1`
- [x] 1.3 创建 `frontend/.gitignore`：`node_modules/`、`dist/`、`.env`、`*.log`、`.vite/`
- [x] 1.4 创建 `frontend/index.html`：入口 HTML，`<div id="app">`，`<script type="module" src="/src/main.ts">`
- [x] 1.5 在 `frontend/` 目录运行 `pnpm install`，验证依赖安装成功

## 2. Vite 与 TypeScript 配置

- [x] 2.1 创建 `frontend/vite.config.ts`：`@vitejs/plugin-vue` 插件；`server.port=5173`；`server.proxy['/api']={ target: 'http://127.0.0.1:8000', changeOrigin: true, ws: true }`；`server.proxy['/health']={ target: 'http://127.0.0.1:8000', changeOrigin: true }`
- [x] 2.2 创建 `frontend/tsconfig.json`：`strict: true`、`target: ES2020`、`module: ESNext`、`moduleResolution: bundler`、`jsx: preserve`、`types: ["vite/client"]`、`include: ["src/**/*.ts", "src/**/*.vue"]`
- [x] 2.3 创建 `frontend/tsconfig.node.json`：用于 vite.config.ts，`include: ["vite.config.ts"]`
- [x] 2.4 创建 `frontend/src/vite-env.d.ts`：`/// <reference types="vite/client" />` 与 `*.vue` 模块声明

## 3. app 实例与全局布局

- [x] 3.1 创建 `frontend/src/main.ts`：`createApp(App).use(router).use(pinia).use(naive).mount('#app')`
- [x] 3.2 创建 `frontend/src/App.vue`：全局布局，`n-layout` 侧边栏（`n-menu` 四项导航）+ `n-layout-content` 主内容区（`<router-view />`）；侧边栏菜单项与路由联动高亮
- [x] 3.3 创建 `frontend/src/router/index.ts`：`createRouter` + `createWebHistory()`；路由表：`/` redirect `/strategies`、`/strategies`、`/backtest`、`/live`、`/evolve`、`/:pathMatch(.*)*` redirect `/strategies`

## 4. axios 客户端

- [x] 4.1 创建 `frontend/src/api/client.ts`：`axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1' })`；响应拦截器把 `error.response.data.error` 转为 `{ code, message }` reject；请求拦截器留空
- [x] 4.2 在 `frontend/src/api/` 下创建 `health.ts`：`fetch('/health').then(r => r.json())` 独立函数，用于联通性检查

## 5. 占位页面

- [x] 5.1 创建 `frontend/src/pages/Strategies.vue`：`<n-card>` 标题 "策略管理" + `<n-tag>todo</n-tag>` + mounted 时调 `client.get('/strategy')` 显示返回
- [x] 5.2 创建 `frontend/src/pages/Backtest.vue`：同上，标题 "回测可视化"，调 `/backtest`
- [x] 5.3 创建 `frontend/src/pages/Live.vue`：同上，标题 "实盘监控"，调 `/live`
- [x] 5.4 创建 `frontend/src/pages/Evolve.vue`：同上，标题 "参数调优"，调 `/evolve`

## 6. 启动验证

- [x] 6.1 在 `backend/` 目录启动后端 `uv run uvicorn app.main:app --port 8000`
- [x] 6.2 在 `frontend/` 目录启动前端 `pnpm dev`，验证 Vite 在 5173 启动
- [x] 6.3 浏览器打开 `http://localhost:5173/`，验证重定向到 `/strategies`
- [x] 6.4 验证四个页面都能调通后端占位接口，显示 `{"module": "...", "status": "todo"}`
- [x] 6.5 验证侧边栏导航切换，高亮当前路由
- [x] 6.6 浏览器 console 测试 WebSocket：`new WebSocket('ws://localhost:5173/api/v1/ws/test')`，验证收到 `{"status":"todo"}` 后关闭
- [x] 6.7 调用 `fetch('/health')` 验证返回 `{"status":"ok","version":"0.1.0"}`
- [x] 6.8 运行 `pnpm typecheck`（vue-tsc --noEmit），验证无类型错误
