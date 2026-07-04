## 1. 项目初始化

- [x] 1.1 创建 `backend/pyproject.toml`：声明项目名 `autoresearch-crypto-backend`、Python `>=3.10`、依赖（fastapi、uvicorn[standard]、sqlalchemy、pydantic-settings）、dev 依赖（ruff）、`[tool.uv.sources]` 配置对根 `dex` 包的 path 依赖
- [x] 1.2 删除 `backend/.gitkeep`，创建 `backend/app/` 包目录与 `backend/app/__init__.py`
- [x] 1.3 在 `backend/` 目录运行 `uv sync`，验证依赖安装成功
- [x] 1.4 创建 `backend/.env.example`，包含 `DATABASE_URL`、`CORS_ORIGINS`、`BINANCE_API_KEY`、`BINANCE_API_SECRET`、`OKX_API_KEY`、`OKX_API_SECRET`、`OKX_PASSPHRASE`、`NADO_PRIVATE_KEY` 占位
- [x] 1.5 创建 `backend/.gitignore`，忽略 `.env`、`__pycache__/`、`*.db`、`.venv/`

## 2. 配置与数据库

- [x] 2.1 创建 `backend/app/core/__init__.py` 与 `backend/app/core/config.py`：`Settings(BaseSettings)` 类，字段 `database_url`、`cors_origins`（list[str]）、`binance_api_key/secret`、`okx_api_key/secret/passphrase`、`nado_private_key`；`model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")`；默认值：`database_url="sqlite:///./dev.db"`、`cors_origins=["http://localhost:5173"]`
- [x] 2.2 创建 `backend/app/core/database.py`：`engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})`、`SessionLocal = sessionmaker(bind=engine)`、`Base = DeclarativeBase()` 子类；提供 `get_db()` 依赖注入生成器
- [x] 2.3 创建 `backend/app/core/exceptions.py`：定义 `error_response(code, message, status_code)` 工具函数，返回 `JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)`

## 3. FastAPI app 与中间件

- [x] 3.1 创建 `backend/app/main.py`：实例化 `FastAPI(title="autoresearch-crypto-backend", version="0.1.0")`
- [x] 3.2 添加 CORS 中间件：`allow_origins=settings.cors_origins`、`allow_methods=["*"]`、`allow_headers=["*"]`、`allow_credentials=True`
- [x] 3.3 注册全局异常处理器：`@app.exception_handler(HTTPException)` 把 detail 转成 `{"error": {"code": <status_phrase>, "message": <detail>}}`；`@app.exception_handler(Exception)` 兜底返回 500 `{"error": {"code": "internal_error", "message": "内部错误"}}`，同时 `logging.exception` 记录堆栈
- [x] 3.4 注册 `GET /health` 路由，返回 `{"status": "ok", "version": "0.1.0"}`
- [x] 3.5 注册聚合路由 `app.include_router(api_router, prefix="/api/v1")`，其中 `api_router` 来自 `backend/app/api/v1/router.py`

## 4. 路由占位

- [x] 4.1 创建 `backend/app/api/__init__.py` 与 `backend/app/api/v1/__init__.py`
- [x] 4.2 创建 `backend/app/api/v1/strategy.py`：`router = APIRouter()`；`@router.get("/")` 返回 `{"module": "strategy", "status": "todo"}`
- [x] 4.3 创建 `backend/app/api/v1/backtest.py`：同上，返回 `{"module": "backtest", "status": "todo"}`
- [x] 4.4 创建 `backend/app/api/v1/live.py`：同上，返回 `{"module": "live", "status": "todo"}`
- [x] 4.5 创建 `backend/app/api/v1/evolve.py`：同上，返回 `{"module": "evolve", "status": "todo"}`
- [x] 4.6 创建 `backend/app/api/v1/ws.py`：`router = APIRouter()`；`@router.websocket("/{topic}")` 接受连接 → 发送 `{"status": "todo"}` → `close()`
- [x] 4.7 创建 `backend/app/api/v1/router.py`：`api_router = APIRouter()`；`include_router` 五个子 router，每个用对应前缀（`/strategy`、`/backtest`、`/live`、`/evolve`、`/ws`）

## 5. 模型 base 与启动验证

- [x] 5.1 创建 `backend/app/models/__init__.py` 与 `backend/app/models/base.py`：从 `app.core.database` 重导出 `Base`，便于后续 task 在 `models/` 下添加模型
- [x] 5.2 在 `backend/` 目录运行 `uv run uvicorn app.main:app --reload`，验证服务启动无异常
- [x] 5.3 `curl http://127.0.0.1:8000/health` 验证返回 `{"status": "ok", "version": "0.1.0"}`
- [x] 5.4 `curl http://127.0.0.1:8000/api/v1/strategy` 验证返回 `{"module": "strategy", "status": "todo"}`
- [x] 5.5 用 `websocat` 或浏览器调试工具连接 `ws://127.0.0.1:8000/api/v1/ws/test`，验证收到 `{"status": "todo"}` 后连接关闭
- [x] 5.6 `curl -H "Origin: http://localhost:5173" -I http://127.0.0.1:8000/api/v1/strategy`，验证响应头含 `access-control-allow-origin: http://localhost:5173`
- [x] 5.7 在 `backend/` 目录运行 `python -c "from dex.config import PROJECT_DIR; print(PROJECT_DIR)"`，验证后端环境能引用根 `dex/` 包路径（sys.path 配置正确；完整 `from dex.strategies.base import` 留给后续业务 task 补装交易核心依赖后验证）
