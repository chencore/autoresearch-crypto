## 新增需求

### 需求:健康检查接口

后端必须提供 `GET /health` 接口，用于探活与部署验证。响应体必须为 JSON，包含 `status` 字段（值为 `"ok"`）与 `version` 字段（值为 `"0.1.0"`）。该接口禁止要求鉴权。

#### 场景:服务启动后探活
- **当** 服务启动完成，客户端发起 `GET /health`
- **那么** 返回 HTTP 200，响应体为 `{"status": "ok", "version": "0.1.0"}`

#### 场景:服务未启动
- **当** 服务未运行，客户端发起 `GET /health`
- **那么** 连接被拒绝（无法建立 TCP 连接）

### 需求:配置从环境变量加载

后端必须通过 `pydantic-settings` 从 `backend/.env` 文件加载配置，包括数据库路径、CORS 允许来源、交易所 API Key 占位字段。缺失 `.env` 文件时，必须使用合理默认值启动（开发态默认值），禁止崩溃。

#### 场景:存在 .env 文件
- **当** `backend/.env` 存在且定义了 `DATABASE_URL=sqlite:///./dev.db`
- **那么** 后端启动时使用该 URL 创建 SQLAlchemy engine

#### 场景:缺失 .env 文件
- **当** `backend/.env` 不存在
- **那么** 后端使用默认配置启动（SQLite 路径默认 `sqlite:///./dev.db`，CORS 默认允许 `http://localhost:5173`），不报错

### 需求:SQLite 数据库连接初始化

后端必须通过 SQLAlchemy 创建指向 SQLite 的 engine 与 session 工厂。启动时不强制建表（表由后续 task 创建），但必须能在首次访问时正常打开连接。

#### 场景:首次启动
- **当** 后端首次启动，SQLite 文件不存在
- **那么** SQLAlchemy engine 创建成功；调用 `session_factory()` 不抛异常

#### 场景:重复启动
- **当** SQLite 文件已存在，后端再次启动
- **那么** 复用已有文件，不清空数据

### 需求:CORS 中间件配置

后端必须启用 CORS 中间件，开发态允许来自 `http://localhost:5173`（Vite 默认）的请求。允许的来源必须可由配置项覆盖。

#### 场景:前端开发服务器请求
- **当** 浏览器从 `http://localhost:5173` 发起跨域请求到后端
- **那么** 响应头包含 `Access-Control-Allow-Origin: http://localhost:5173`

#### 场景:未授权来源
- **当** 浏览器从 `http://evil.com` 发起跨域请求
- **那么** 响应不包含该来源的 CORS 头，浏览器拒绝响应

### 需求:统一错误响应格式

所有未捕获异常必须由全局异常处理器转换为统一 JSON 格式：`{"error": {"code": "<string>", "message": "<string>"}}`。禁止向前端暴露堆栈信息或内部实现细节。

#### 场景:未捕获异常
- **当** 某个请求处理过程中抛出未被业务层捕获的异常
- **那么** 返回 HTTP 500，响应体为 `{"error": {"code": "internal_error", "message": "内部错误"}}`

#### 场景:HTTPException
- **当** 业务层抛出 `fastapi.HTTPException(status_code=404, detail="not found")`
- **那么** 返回 HTTP 404，响应体为 `{"error": {"code": "not_found", "message": "not found"}}`

### 需求:业务路由占位

后端必须注册五个业务 router：`strategy`、`backtest`、`live`、`evolve`、`ws`，统一前缀 `/api/v1`。每个 router 必须提供一个 `GET /` 占位端点，返回 `{"module": "<name>", "status": "todo"}`。本 task 禁止实现任何业务逻辑。

#### 场景:访问占位端点
- **当** 客户端发起 `GET /api/v1/strategy`
- **那么** 返回 HTTP 200，响应体为 `{"module": "strategy", "status": "todo"}`

#### 场景:WebSocket 路由占位
- **当** 客户端连接 `/api/v1/ws/test`
- **那么** 连接建立后服务端立即发送一条 `{"status": "todo"}` 消息并关闭连接

### 需求:依赖与项目隔离

后端必须使用独立的 `backend/pyproject.toml` 管理依赖，与根目录 `pyproject.toml`（Python 交易核心）隔离。后端必须能通过 `uv sync` 在 `backend/` 目录内独立安装依赖。

#### 场景:独立安装后端依赖
- **当** 在 `backend/` 目录执行 `uv sync`
- **那么** fastapi、uvicorn、sqlalchemy、pydantic-settings 等依赖被安装到独立的虚拟环境

#### 场景:导入交易核心路径可达
- **当** 后端代码尝试 `from dex.config import PROJECT_DIR`（不触发 numpy/pandas/torch 等交易核心重依赖）
- **那么** 必须能成功导入，返回的 `PROJECT_DIR` 指向项目根目录

> 注：完整 `from dex.strategies.base import BaseStrategy` 会触发 numpy/pandas/torch 等交易核心依赖加载，留给后续业务 task（在 backend 环境补装交易核心依赖）时验证。本 task 只验证 path 配置正确。
