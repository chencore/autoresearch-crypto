# Plan: setup-backend-scaffold

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **5 个阶段**，每个阶段产出可独立验证的增量。阶段之间以"**✅ 完成验证**"卡点推进，不跳步。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | 项目初始化与依赖 | `backend/pyproject.toml` + `.env.example` + 依赖可 sync | 0.25d |
| S2 | 配置与数据库 | `config.py` + `database.py` + `exceptions.py` | 0.25d |
| S3 | FastAPI app 与中间件 | `main.py`（CORS + 异常处理 + /health + 路由聚合） | 0.25d |
| S4 | 路由占位 | 五个 router + 聚合 router | 0.25d |
| S5 | 启动验证 | curl + ws + 跨域 + dex import 全绿 | 0.25d |

**总估时**：约 1.25 人日。

---

## S1. 项目初始化与依赖

### 目标
建立 `backend/` 独立 Python 项目，依赖可独立安装。

### 实施步骤

1. 先确认根 `pyproject.toml` 的 `name` 字段与 `dex/` 包结构：`cat pyproject.toml | head -20`，确认 `dex` 是否可被 import
2. 创建 `backend/pyproject.toml`：
   - `[project]` name=`autoresearch-crypto-backend`，version=`0.1.0`，requires-python=`>=3.10`
   - `dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.27", "sqlalchemy>=2.0", "pydantic-settings>=2.1"]`
   - `[tool.uv]` dev-dependencies = `["ruff>=0.3"]`
   - `[tool.uv.sources]`：若根项目 `name` 是 `dex` 或包含 `dex` 包，加 `dex = { path = ".." }` 并在 dependencies 里加 `dex`；否则不引入，验证时用 `PYTHONPATH=..` 兜底
3. 删除 `backend/.gitkeep`，创建 `backend/app/__init__.py`（空文件）
4. 创建 `backend/.env.example`，含 `DATABASE_URL`、`CORS_ORIGINS`、各交易所 Key 占位
5. 创建 `backend/.gitignore`：`.env`、`__pycache__/`、`*.db`、`.venv/`
6. 在 `backend/` 目录运行 `uv sync`，验证依赖安装成功

### ✅ 完成验证
- [ ] `backend/.venv/` 存在，`uv pip list` 含 fastapi、uvicorn、sqlalchemy、pydantic-settings
- [ ] `backend/.env.example` 与 `backend/.gitignore` 存在
- [ ] `backend/app/__init__.py` 存在

---

## S2. 配置与数据库

### 目标
配置加载、数据库连接、统一错误响应工具就绪。

### 实施步骤

1. 创建 `backend/app/core/__init__.py`（空）
2. 创建 `backend/app/core/config.py`：
   - `class Settings(BaseSettings)`，字段：`database_url: str = "sqlite:///./dev.db"`、`cors_origins: list[str] = ["http://localhost:5173"]`、`binance_api_key/secret: str = ""`、`okx_api_key/secret/passphrase: str = ""`、`nado_private_key: str = ""`
   - `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")`
   - 模块级单例 `settings = Settings()`
3. 创建 `backend/app/core/database.py`：
   - `engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})`
   - `SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)`
   - `class Base(DeclarativeBase): pass`
   - `def get_db() -> Generator`：yield session 并 finally close
4. 创建 `backend/app/core/exceptions.py`：
   - `def error_response(code: str, message: str, status_code: int) -> JSONResponse`
   - 辅助：`http_exception_handler(request, exc)`、`unhandled_exception_handler(request, exc)`

### ✅ 完成验证
- [ ] `python -c "from app.core.config import settings; print(settings.database_url)"` 输出 `sqlite:///./dev.db`
- [ ] `python -c "from app.core.database import engine, Base, SessionLocal; s=SessionLocal(); s.close(); print('ok')"` 输出 `ok`
- [ ] `python -c "from app.core.exceptions import error_response; print(error_response('x','y',400).body)"` 输出 `b'{"error":{"code":"x","message":"y"}}'`

---

## S3. FastAPI app 与中间件

### 目标
FastAPI app 实例可启动，CORS、异常处理、/health、路由聚合全部就绪。

### 实施步骤

1. 创建 `backend/app/main.py`：
   - `app = FastAPI(title="autoresearch-crypto-backend", version="0.1.0")`
   - `app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=True)`
   - 注册 `@app.exception_handler(HTTPException)` → 转 `error_response`
   - 注册 `@app.exception_handler(Exception)` → 500 `internal_error` + `logging.exception`
   - `@app.get("/health")` → `{"status": "ok", "version": "0.1.0"}`
   - `app.include_router(api_router, prefix="/api/v1")`（从 `app.api.v1.router` 导入）

### ✅ 完成验证
- [ ] `uv run uvicorn app.main:app` 启动无异常
- [ ] `curl http://127.0.0.1:8000/health` 返回 `{"status":"ok","version":"0.1.0"}`（此时路由聚合可能尚未完成，可先注释 include_router 验证 /health，或与 S4 合并验证）

> 注：S3 的 `include_router` 依赖 S4 的 `api_router`，可先写 `main.py` 但暂注释 `include_router` 行，跑完 S4 再启用。

---

## S4. 路由占位

### 目标
五个业务 router + 聚合 router 就绪，全部返回占位响应。

### 实施步骤

1. 创建 `backend/app/api/__init__.py` 与 `backend/app/api/v1/__init__.py`（空）
2. 创建 `backend/app/api/v1/strategy.py`：`router = APIRouter()`；`@router.get("/")` → `{"module": "strategy", "status": "todo"}`
3. 创建 `backend/app/api/v1/backtest.py`：同上，module=`backtest`
4. 创建 `backend/app/api/v1/live.py`：同上，module=`live`
5. 创建 `backend/app/api/v1/evolve.py`：同上，module=`evolve`
6. 创建 `backend/app/api/v1/ws.py`：
   - `router = APIRouter()`
   - `@router.websocket("/{topic}")`：`async def ws_placeholder(websocket, topic)`：`await websocket.accept()` → `await websocket.send_json({"status": "todo"})` → `await websocket.close()`
7. 创建 `backend/app/api/v1/router.py`：
   - `api_router = APIRouter()`
   - `api_router.include_router(strategy.router, prefix="/strategy", tags=["strategy"])`
   - 同上注册 backtest/live/evolve/ws
8. 回到 `main.py` 启用 `app.include_router(api_router, prefix="/api/v1")`

### ✅ 完成验证
- [ ] `curl http://127.0.0.1:8000/api/v1/strategy` → `{"module":"strategy","status":"todo"}`
- [ ] 四个 HTTP 占位端点全部返回对应 module 名
- [ ] `websocat ws://127.0.0.1:8000/api/v1/ws/test`（或浏览器调试）收到 `{"status":"todo"}` 后连接关闭

---

## S5. 启动验证

### 目标
全链路冒烟：健康检查、占位端点、CORS、跨包 import 全绿。

### 实施步骤

1. 在 `backend/` 目录运行 `uv run uvicorn app.main:app --reload`，保持运行
2. 执行验证命令清单（见下方完成验证）
3. 验证失败时定位修复，验证全部通过后停服

### ✅ 完成验证
- [ ] `curl -s http://127.0.0.1:8000/health` → `{"status":"ok","version":"0.1.0"}`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/strategy` → `{"module":"strategy","status":"todo"}`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/backtest` → `{"module":"backtest","status":"todo"}`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/live` → `{"module":"live","status":"todo"}`
- [ ] `curl -s http://127.0.0.1:8000/api/v1/evolve` → `{"module":"evolve","status":"todo"}`
- [ ] `curl -s -H "Origin: http://localhost:5173" -I http://127.0.0.1:8000/api/v1/strategy` 响应头含 `access-control-allow-origin: http://localhost:5173`
- [ ] WebSocket 连接 `/api/v1/ws/test` 收到 `{"status":"todo"}` 后关闭
- [ ] `cd backend && python -c "from dex.config import PROJECT_DIR; print(PROJECT_DIR)"` 输出项目根路径（验证 sys.path 配置正确；完整 `from dex.strategies.base import` 留给后续业务 task 补装交易核心依赖后验证）
- [ ] 手动触发一个未捕获异常（如临时加 `raise RuntimeError("test")` 到某路由），验证返回 `{"error":{"code":"internal_error","message":"内部错误"}}` 且响应不含堆栈

---

## 风险与回滚

- **风险**：根 `pyproject.toml` 的 `name` 不是合法包名，导致 path 依赖失败
  - **缓解**：S1 第 1 步先确认；若失败，用 `PYTHONPATH` 兜底，不阻塞 S2-S5
- **回滚**：所有改动集中在 `backend/` 新增文件，`git checkout` 删除即可
