## 为什么

v0.1 要在现有 `dex/` 交易核心之上加 Web 后端 + 前端，让策略管理 / 回测 / 实盘监控 / 参数调优具备可视化界面。前端四个模块的所有 HTTP / WebSocket 调用都需要一个统一的 FastAPI 后端来承接，后续四个业务 task（strategy / backtest / live / evolve）都依赖这个骨架。先把后端骨架立起来，后续 task 只需在自己的 router 里填业务逻辑。

## 变更内容

- 新增 `backend/` 顶层目录，承载 FastAPI 服务
- 新增 `backend/pyproject.toml`（用 uv 管理依赖）：fastapi、uvicorn、sqlalchemy、pydantic-settings、python-dotenv 等
- 新增 `backend/app/main.py`：FastAPI app 实例 + CORS 中间件 + 全局异常处理 + 路由注册
- 新增 `backend/app/core/config.py`：基于 pydantic-settings 的配置类，从 `.env` 读取（数据库路径、CORS 来源、交易所 API Key 占位等）
- 新增 `backend/app/core/database.py`：SQLAlchemy engine + session 工厂，初始化 SQLite
- 新增 `backend/app/core/exceptions.py`：统一错误响应格式 `{"error": {"code": "...", "message": "..."}}`
- 新增五个占位 router：`backend/app/api/v1/strategy.py` / `backtest.py` / `live.py` / `evolve.py` / `ws.py`，每个先放一个空 `APIRouter()` 与一个 `GET /` 占位
- 新增 `backend/app/api/v1/router.py`：聚合五个子 router，统一前缀 `/api/v1`
- 新增 `backend/app/models/base.py`：SQLAlchemy declarative base（不建具体表，留给后续 task）
- 新增 `backend/.env.example`：示例环境变量
- 新增 `GET /health` 接口：返回 `{"status": "ok", "version": "0.1.0"}`
- **BREAKING**：无（纯新增，不动现有 `dex/` 代码）

## 功能 (Capabilities)

### 新增功能
- `backend-scaffold`: FastAPI 后端骨架——app 实例、配置加载、数据库连接、CORS、统一错误处理、五个业务 router 占位、健康检查接口。本 task 只搭骨架，不实现任何业务逻辑。

### 修改功能
<!-- 无现有功能被修改 -->

## 影响

- **代码**：新增 `backend/` 目录及其下全部文件；不动 `dex/`、`frontend/`、`spec/`、`openspec/`
- **依赖**：新增 `backend/pyproject.toml`，引入 fastapi / uvicorn / sqlalchemy / pydantic-settings 等后端依赖（与根目录 `pyproject.toml` 的 Python 交易核心依赖隔离）
- **API**：新增 `GET /health` 与五个 `/api/v1/{strategy,backtest,live,evolve,ws}` 占位端点
- **配置**：新增 `backend/.env.example`，运行时从 `backend/.env` 读取
- **启动方式**：新增 `uv run uvicorn backend.app.main:app --reload`（或等价方式）作为后端开发启动命令
