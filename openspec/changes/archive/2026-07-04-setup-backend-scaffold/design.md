## 上下文

v0.1 在现有 `dex/` Python 交易核心之上加 Web 后端 + 前端。后端选 FastAPI（见 `spec/design.md` 的技术栈决策），本 task 负责把后端骨架立起来。现有项目根目录已有 `pyproject.toml`（uv 管理，含 torch / ccxt / pandas 等交易核心依赖），后端不能污染根环境，需独立管理依赖。`backend/` 目录目前为空（只有 `.gitkeep`）。

## 目标 / 非目标

**目标：**
- 建立 `backend/` 目录结构与独立 Python 项目（独立 `pyproject.toml` + uv 虚拟环境）
- FastAPI app 实例可启动，`GET /health` 返回 200
- 配置从 `.env` 加载（pydantic-settings）
- SQLite + SQLAlchemy engine / session 工厂就绪
- CORS 中间件（开发态允许 `localhost:5173`）
- 统一错误响应格式 `{"error": {"code", "message"}}`
- 五个业务 router 占位（strategy / backtest / live / evolve / ws），统一前缀 `/api/v1`
- 后端能 import 根目录 `dex/` 包（用于后续 task 调用交易核心）

**非目标：**
- 不实现任何业务逻辑（策略管理 / 回测 / 实盘 / 进化都留给后续 task）
- 不建具体数据表（EvolveTask / LiveProcess 等模型留给后续 task）
- 不做用户鉴权（v0.1 单机无鉴权，见 `spec/requirements.md` R-v0.1-ck-8）
- 不做生产部署配置（gunicorn / 反向代理 / HTTPS 留给 v0.2+）
- 不做前后端联调启动脚本（留给 `integration-launch-script` task）

## 决策

### 决策 1：后端独立 pyproject.toml，而非塞进根 pyproject.toml
- **选择**：`backend/pyproject.toml` 独立管理 fastapi / uvicorn / sqlalchemy 等依赖
- **替代方案**：把后端依赖加到根 `pyproject.toml`
- **理由**：根环境是交易核心（torch + ccxt + pandas），重且与 Web 无关；后端依赖轻量，独立管理避免污染交易核心环境、便于后续单独部署

### 决策 2：用 pydantic-settings 而非 python-dotenv 直接读
- **选择**：`pydantic_settings.BaseSettings` + `model_config = SettingsConfigDict(env_file=".env")`
- **替代方案**：`python-dotenv.load_dotenv()` + `os.getenv`
- **理由**：pydantic-settings 自带类型校验、默认值、嵌套配置；FastAPI 生态推荐；与 Pydantic v2 一致

### 决策 3：SQLAlchemy 用声明式 base + session 工厂，不在启动时建表
- **选择**：`DeclarativeBase` + `sessionmaker`，启动时不调 `Base.metadata.create_all`
- **替代方案**：启动时 `create_all` 自动建表
- **理由**：本 task 不建任何模型表；后续 task 添加模型时自行决定是否 migrate。提前 `create_all` 没有表也无意义

### 决策 4：后端引用 dex/ 包用 path 依赖，而非 pip install -e
- **选择**：`backend/pyproject.toml` 中用 `[tool.uv.sources]` + `project.dependencies` 指向根目录：`dex = { path = ".." }`（前提是根 pyproject.toml 的 project name 是 `dex` 或类似）
- **替代方案 A**：把 `backend/` 提升为根项目子包，共享一个 pyproject.toml
- **替代方案 B**：后端代码用 `sys.path.insert` hack 引用根目录
- **理由**：path 依赖是 uv 官方支持的方式；保持两个环境隔离但代码可 import；避免 sys.path hack

- **风险**：如果根 `pyproject.toml` 的 `name` 不是合法包名（含 `-`），需要确认实际 import 名（如 `autoresearch-crypto` → 包名可能是 `dex`，因为代码在 `dex/` 目录）
- **缓解**：本 task 实施时先 `cat pyproject.toml` 确认根项目 `name` 与包结构，若根项目未声明 `dex` 为包，则在 `backend/pyproject.toml` 里用 `[tool.uv.sources]` 指向 `..` 并在代码里直接 `from dex.strategies...` 导入（根目录已能 import）

### 决策 5：统一错误处理用 FastAPI exception_handler，而非中间件
- **选择**：注册 `@app.exception_handler(Exception)` 与 `@app.exception_handler(HTTPException)`
- **替代方案**：自定义中间件捕获
- **理由**：FastAPI 原生支持，注册后所有路由异常自动走处理器；中间件方式需手动区分异常类型

### 决策 6：WebSocket 路由占位用 /api/v1/ws/{topic}
- **选择**：`@router.websocket("/ws/{topic}")`，占位实现：接受连接 → 发 `{"status": "todo"}` → 关闭
- **替代方案**：每个业务一个 ws 端点（`/ws/evolve/{task_id}`、`/ws/live/{exchange}`）
- **理由**：本 task 只占位，统一端点便于后续 task 在同一 router 内扩展多个 ws 路径；具体业务 ws 路径留给后续 task

## 风险 / 权衡

- **[根包引用失败]** → 后端无法 `import dex`。缓解：实施时先验证根目录 `python -c "import dex"` 可用；若根 pyproject.toml 未把 `dex` 声明为包，则在 backend pyproject 里直接用 `tool.uv.sources` 指向 `..` 并依赖 Python 默认的 sys.path（`backend/` 的父目录是项目根，运行 uvicorn 时若以项目根为 CWD 即可 import）
- **[SQLite 并发]** → 单机个人使用，SQLite 默认配置足够；若后续进化任务并发写 EvolveTask 出现锁竞争，再切 WAL 模式或升级 Postgres
- **[CORS 配置错误]** → 前端开发态跨域失败。缓解：默认值 `http://localhost:5173` 对应 Vite 默认端口；若用户改 Vite 端口需在 `.env` 覆盖
- **[依赖版本漂移]** → uv.lock 锁定版本，提交仓库避免漂移

## 迁移计划

- 新增 `backend/` 全部文件，不动现有代码
- 用户在 `backend/` 目录执行 `uv sync` 安装依赖
- 启动验证：`uv run uvicorn app.main:app --reload`（在 `backend/` 目录）或 `uv run uvicorn backend.app.main:app --reload`（在项目根）
- 回滚：`git checkout` 删除 `backend/` 新增文件即可，无副作用

## 待解决问题

- 后端启动命令的最终形态（在 `backend/` 跑还是项目根跑）？倾向于在 `backend/` 目录跑 `uvicorn app.main:app`，配合 `integration-launch-script` task 统一编排
- 五个占位 router 是否需要统一的响应模型（Pydantic schema）？v0.1 先用裸 dict，后续 task 引入 schema 时再统一
