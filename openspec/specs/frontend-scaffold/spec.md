## 新增需求

### 需求:前端项目可独立安装与启动

前端必须使用独立的 `frontend/package.json` 管理依赖（pnpm），与后端 Python 依赖完全隔离。前端必须能通过 `pnpm install` 在 `frontend/` 目录内独立安装依赖，通过 `pnpm dev` 启动开发服务器。

#### 场景:独立安装前端依赖
- **当** 在 `frontend/` 目录执行 `pnpm install`
- **那么** vue、vue-router、pinia、naive-ui、axios、vite、typescript 等依赖被安装到 `node_modules/`

#### 场景:启动开发服务器
- **当** 在 `frontend/` 目录执行 `pnpm dev`
- **那么** Vite 开发服务器在 `http://localhost:5173` 启动，浏览器打开能看到前端页面

### 需求:开发代理转发 API 请求

前端开发服务器必须通过 Vite proxy 把 `/api` 请求转发到后端 `http://127.0.0.1:8000`，避免浏览器跨域。代理必须同时支持 HTTP 与 WebSocket。

#### 场景:HTTP 请求转发
- **当** 前端代码发起 `GET /api/v1/strategy`
- **那么** 请求被 Vite proxy 转发到 `http://127.0.0.1:8000/api/v1/strategy`，返回后端的占位响应

#### 场景:WebSocket 请求转发
- **当** 前端代码连接 `ws://localhost:5173/api/v1/ws/test`
- **那么** 连接被 Vite proxy 转发到后端 `ws://127.0.0.1:8000/api/v1/ws/test`，收到 `{"status":"todo"}` 后关闭

### 需求:路由配置与页面占位

前端必须配置 vue-router，提供四个业务路由：`/strategies`、`/backtest`、`/live`、`/evolve`。每个路由对应一个占位页面，显示模块名与 "todo" 状态。根路径 `/` 必须重定向到 `/strategies`。

#### 场景:访问根路径
- **当** 浏览器打开 `http://localhost:5173/`
- **那么** 自动重定向到 `/strategies`

#### 场景:访问业务路由
- **当** 浏览器导航到 `/backtest`
- **那么** 主内容区显示 Backtest 页面，含模块标题与 "todo" 占位

#### 场景:访问未知路由
- **当** 浏览器导航到 `/nonexistent`
- **那么** 显示 404 提示或重定向到默认页

### 需求:全局布局与导航

前端必须提供全局布局：左侧侧边栏导航（四个模块入口）+ 主内容区（路由出口）。导航项高亮当前路由。布局必须适配桌面浏览器（≥ 1280px 宽）。

#### 场景:导航切换
- **当** 用户点击侧边栏 "实盘监控"
- **那么** 路由切换到 `/live`，侧边栏 "实盘监控" 项高亮，主内容区显示 Live 页面

#### 场景:桌面适配
- **当** 浏览器窗口宽度 ≥ 1280px
- **那么** 侧边栏与主内容区正常显示，无横向滚动条

### 需求:axios 客户端与统一错误处理

前端必须提供统一的 axios 实例（`/api/v1` 前缀），所有业务 API 调用通过该实例发起。axios 拦截器必须把后端统一错误格式 `{"error": {"code", "message"}}` 转换为前端可用的错误对象。

#### 场景:成功请求
- **当** 调用 `client.get('/strategy')` 且后端返回 200
- **那么** Promise resolve，返回 `{"module": "strategy", "status": "todo"}`

#### 场景:后端返回错误
- **当** 后端返回 404 `{"error": {"code": "not_found", "message": "Not Found"}}`
- **那么** axios 拦截器 reject，错误对象含 `code` 与 `message` 字段，前端可读取

### 需求:健康检查联通性验证

前端启动后必须能访问后端 `/health` 接口，验证前后端联通。

#### 场景:前后端联通
- **当** 后端已启动，前端发起 `GET /api/v1/../health`（或直接请求后端 health）
- **那么** 返回 `{"status": "ok", "version": "0.1.0"}`，前端控制台打印联通日志

> 注：`/health` 在后端根路径（非 `/api/v1` 前缀），前端 axios 实例 baseURL 为 `/api/v1`，因此 health 检查需用独立请求或调整 baseURL。本 task 用独立 fetch 调用 `/health`（经 Vite proxy 转发）验证联通性。
