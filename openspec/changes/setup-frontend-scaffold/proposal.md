## 为什么

v0.1 工作台前端四个模块（策略管理 / 回测 / 实盘 / 调优）需要一个统一的 Vue 3 SPA 承载。后端骨架（`setup-backend-scaffold`）已就绪，提供 `/api/v1/*` 与 `/health`。本 task 立起前端骨架，后续四个业务 task（strategy / backtest / live / evolve UI）在此之上填页面。

## 变更内容

- 新增 `frontend/` 顶层目录，承载 Vue 3 + Vite + TypeScript SPA
- 新增 `frontend/package.json`（pnpm 管理）：vue@3、vue-router@4、pinia、naive-ui、axios、typescript、vite、@vitejs/plugin-vue、vue-tsc
- 新增 `frontend/vite.config.ts`：Vite 配置 + 开发代理 `/api` → `http://127.0.0.1:8000`
- 新增 `frontend/tsconfig.json` 与 `frontend/tsconfig.node.json`：TS 配置，严格模式
- 新增 `frontend/index.html`：入口 HTML
- 新增 `frontend/src/main.ts`：Vue app 实例 + 路由 + Pinia + Naive UI 安装
- 新增 `frontend/src/App.vue`：根组件 + 全局布局（侧边栏导航 + 主内容区）
- 新增 `frontend/src/router/index.ts`：vue-router 配置，四个业务路由占位 + 重定向
- 新增四个占位页面：`frontend/src/pages/Strategies.vue` / `Backtest.vue` / `Live.vue` / `Evolve.vue`，每个先放标题 + "todo" 占位
- 新增 `frontend/src/api/client.ts`：axios 实例，baseURL `/api/v1`，统一错误处理
- 新增 `frontend/.env.example`：`VITE_API_BASE_URL=/api/v1`
- 新增 `frontend/.gitignore`：`node_modules/`、`dist/`、`.env`、`*.log`
- **BREAKING**：无（纯新增，不动现有代码）

## 功能 (Capabilities)

### 新增功能
- `frontend-scaffold`: Vue 3 SPA 骨架——app 实例、路由、Pinia、Naive UI、axios client、全局布局、四个业务页面占位。本 task 只搭骨架，不实现任何业务 UI。

### 修改功能
<!-- 无现有功能被修改 -->

## 影响

- **代码**：新增 `frontend/` 目录及其下全部文件；不动 `dex/`、`backend/`、`spec/`、`openspec/`
- **依赖**：新增 `frontend/package.json`，引入 Vue 3 / vue-router / pinia / naive-ui / axios / vite / typescript 等前端依赖（与 backend Python 依赖完全隔离）
- **API**：消费后端 `/api/v1/*` 占位端点（已由 `setup-backend-scaffold` 提供）
- **配置**：新增 `frontend/.env.example`，开发态通过 Vite proxy 转发 `/api` 到后端
- **启动方式**：新增 `pnpm dev`（在 `frontend/` 目录）作为前端开发启动命令
