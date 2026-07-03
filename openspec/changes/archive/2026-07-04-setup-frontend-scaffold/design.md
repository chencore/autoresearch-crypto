## 上下文

v0.1 工作台前端选 Vue 3 + Vite + TypeScript + Naive UI（见 `spec/design.md` 技术栈表）。后端骨架（`setup-backend-scaffold`）已就绪，提供 `/api/v1/{strategy,backtest,live,evolve,ws}` 占位端点与 `/health`。`frontend/` 目录目前为空（只有 `.gitkeep`）。本 task 立起前端骨架，后续四个业务 task 在此之上填页面。

## 目标 / 非目标

**目标：**
- 建立 `frontend/` 目录结构与独立 pnpm 项目
- Vite 开发服务器可启动，`http://localhost:5173` 可访问
- vue-router 配置四个业务路由 + 重定向
- 全局布局（侧边栏 + 主内容区）+ Naive UI 安装
- axios 客户端 + 统一错误拦截
- Vite proxy 转发 `/api` 到后端（HTTP + WebSocket）
- 四个占位页面，能调通后端占位接口

**非目标：**
- 不实现任何业务 UI（策略列表 / 回测表单 / 实盘面板 / 进化曲线留给后续 task）
- 不做用户鉴权 / 登录页（v0.1 单机无鉴权）
- 不做移动端 / H5 适配（v0.1 仅桌面）
- 不做生产构建配置（`pnpm build` 产物部署留给 `integration-launch-script` task）
- 不做状态管理业务逻辑（Pinia store 留给后续 task，本 task 只装 Pinia）
- 不做单元测试 / E2E 测试（留给后续 task）

## 决策

### 决策 1：用 Vite 模板手起而非 create-vue CLI
- **选择**：手动创建 `package.json` + `vite.config.ts` + `tsconfig.json` + 源文件
- **替代方案**：`pnpm create vue@latest` 脚手架
- **理由**：CLI 会生成大量本 task 用不到的文件（ESLint、Prettier、vitest、cypress 等）；手起只放本 task 需要的最小集，与 backend 骨架对称。后续 task 需要时再增量加

### 决策 2：状态管理用 Pinia，但本 task 不建 store
- **选择**：安装 pinia 并在 main.ts 注册，不创建任何 store 文件
- **替代方案**：本 task 不装 Pinia，留给业务 task
- **理由**：Pinia 是 Vue 3 官方推荐，四个业务 task 都会用到；提前装好避免后续 task 重复改 main.ts；不建 store 避免空文件污染

### 决策 3：axios 实例 baseURL 用 `/api/v1`，health 检查独立 fetch
- **选择**：`client = axios.create({ baseURL: '/api/v1' })`；health 联通用 `fetch('/health')` 独立请求
- **替代方案 A**：axios baseURL 用 `/`，每个业务请求写完整路径
- **替代方案 B**：把 `/health` 移到 `/api/v1/health`（改后端）
- **理由**：业务接口都在 `/api/v1` 下，baseURL 统一前缀更简洁；`/health` 是探活接口，独立路径更符合语义；不改后端

### 决策 4：Vite proxy 用 rewrite 保持路径
- **选择**：`proxy['/api'] = { target: 'http://127.0.0.1:8000', changeOrigin: true }`，不 rewrite 路径
- **替代方案**：rewrite `^/api` 到 `/api`
- **理由**：后端接口本身就是 `/api/v1/*`，无需 rewrite；`/health` 也需要 proxy，单独配 `proxy['/health']`

### 决策 5：WebSocket proxy 与 HTTP proxy 分开配
- **选择**：`proxy['/api']` 配置 `ws: true` 启用 WebSocket 转发
- **替代方案**：单独配 `proxy['/api/v1/ws']`
- **理由**：Vite proxy 单条配置同时支持 HTTP + WS（`ws: true`），更简洁；后续业务 task 的 ws 端点都在 `/api/v1/ws/*` 下

### 决策 6：路由模式用 history 而非 hash
- **选择**：`createWebHistory()`
- **替代方案**：`createWebHashHistory()`
- **理由**：开发态 Vite 自动处理 history fallback；URL 更干净（无 `#`）；生产部署时配 nginx try_files 即可

### 决策 7：Naive UI 用全局注册而非按需引入
- **选择**：`app.use(naive)` 全局注册所有组件
- **替代方案**：按需引入（unplugin-naive-ui + tree-shaking）
- **理由**：单机本地应用，bundle 体积不敏感；全局注册开发体验好，后续业务 task 直接用 `<n-xxx>` 标签；按需引入留给生产优化阶段

## 风险 / 权衡

- **[Vite proxy WS 不生效]** → WebSocket 连接失败。缓解：`vite.config.ts` 显式 `ws: true`，S5 验证时用浏览器 console `new WebSocket(...)` 测试
- **[Naive UI 主题与深色模式]** → 本 task 不配主题，用默认。后续业务 task 若需深色模式再统一配
- **[TypeScript 严格模式报错]** → vue-tsc 严格模式可能报未定义类型。缓解：本 task 只写占位组件，类型简单；复杂类型留给业务 task
- **[node_modules 体积]** → pnpm 用 symlink 节省空间；`.gitignore` 已忽略 `node_modules/`

## 迁移计划

- 新增 `frontend/` 全部文件，不动现有代码
- 用户在 `frontend/` 目录执行 `pnpm install` 安装依赖
- 启动验证：`pnpm dev`（前端）+ `uv run uvicorn app.main:app`（后端，在 `backend/` 目录）双开
- 回滚：`git checkout` 删除 `frontend/` 新增文件即可

## 待解决问题

- 前端启动命令是否要统一到根目录脚本？留给 `integration-launch-script` task
- 环境变量 `VITE_API_BASE_URL` 是否需要支持覆盖后端地址？v0.1 用 Vite proxy 即可，留 v0.2
