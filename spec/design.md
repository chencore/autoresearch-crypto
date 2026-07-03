# Design

> **维护规则**：本文件是**项目整体设计与架构决策**，仅在**人工明确要求**时修改。AI 不得擅自更新。

---

## 1. 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 前端 | Vue 3 + Vite + TypeScript + Naive UI | Naive UI 对 TS 友好、主题灵活、适合 dashboard；Vite 启动快 |
| 后端 | Python 3.10+ / FastAPI / Uvicorn | 直接 `import dex/`，零跨语言成本；FastAPI 自带 OpenAPI + WebSocket |
| 数据库 | SQLite + SQLAlchemy | 单机够用，零部署成本；为后续切 Postgres 预留 ORM 抽象 |
| 进度推送 | WebSocket（FastAPI 原生） | 进化引擎每代进度需实时推送；轮询体验差 |
| 实盘集成 | subprocess 拉起 `live_*.py` | 复用现有脚本不动核心；进程隔离便于启停 |
| 包管理 | uv（后端）/ pnpm（前端） | 沿用项目现有 uv；前端用 pnpm 速度快 |

## 2. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│  浏览器（Vue 3 SPA）                                          │
│  ┌────────────┬────────────┬────────────┬────────────┐       │
│  │ 策略管理    │ 回测可视化  │ 实盘监控    │ 参数调优    │       │
│  └─────┬──────┴─────┬──────┴─────┬──────┴─────┬──────┘       │
│        │            │            │            │ WebSocket     │
└────────┼────────────┼────────────┼────────────┼──────────────┘
         │ HTTP       │ HTTP       │ HTTP/WS    │ WS
┌────────▼────────────▼────────────▼────────────▼──────────────┐
│  FastAPI 后端（uvicorn）                                       │
│  ┌─────────┬─────────┬─────────┬─────────┬─────────────┐     │
│  │strategy │backtest │live     │evolve   │ ws manager  │     │
│  │ router  │ router  │ router  │ router  │             │     │
│  └────┬────┴────┬────┴────┬────┴────┬────┴──────┬──────┘     │
│       │         │         │         │           │            │
│       │  ┌──────▼──────┐  │  ┌──────▼──────┐    │            │
│       │  │ dex/ 包      │  │  │ subprocess  │    │ SQLite     │
│       │  │ - strategies │  │  │ live_*.py   │    │ (任务/状态) │
│       │  │ - evolution  │  │  └─────────────┘    │            │
│       │  │ - reflection │  │                     │            │
│       │  └─────────────┘  │                     │            │
│       │                   │                     │            │
│       └──── data/crypto/ parquet ───────────────┘            │
└──────────────────────────────────────────────────────────────┘
```

## 3. 模块划分

**后端 routers**：
- `strategy` — 扫描 `dex/strategies/`，返回策略元数据 + 参数定义 + 当前实盘运行状态
- `backtest` — 扫描 `data/crypto/`，执行 `BaseStrategy.simulate`，返回结果 JSON
- `live` — 子进程管理（启动 / 停止 / 列出），流式读取 `logs/` 状态文件 + 进程 stdout
- `evolve` — 触发 ATLAS / GEPA，通过 WebSocket 推送进度
- `ws` — 统一 WebSocket 连接管理（订阅任务进度）

**前端页面**：
- 策略管理（`/strategies`）— 列表 + 详情抽屉
- 回测（`/backtest`）— 表单 + 结果可视化
- 实盘（`/live`）— 交易所选择 + 启停 + 监控面板
- 调优（`/evolve`）— 引擎配置 + 进度可视化

## 4. 数据模型（核心实体）

> v0.1 仅持久化「进化任务」与「实盘进程」状态，回测结果不落盘。

### EvolveTask
- `id` (str, uuid) — 任务 ID
- `engine` (str) — `atlas` / `gepa`
- `status` (str) — `pending` / `running` / `done` / `failed`
- `config` (json) — 引擎配置
- `started_at` / `ended_at` (datetime)
- `result` (json, nullable) — 最终结果

### LiveProcess
- `exchange` (str, pk) — `binance` / `okx` / `nado`
- `pid` (int, nullable) — 子进程 PID
- `status` (str) — `stopped` / `running` / `crashed`
- `started_at` (datetime, nullable)
- `last_state` (json, nullable) — 最近从 `logs/` 读到的状态快照

## 5. 关键接口约定

- **HTTP 风格**：RESTful，所有路径前缀 `/api/v1`
- **错误格式**：`{"error": {"code": "...", "message": "..."}}`
- **WebSocket**：`/api/v1/ws/evolve/{task_id}` 推送进化进度，消息格式 `{type: "generation", data: {...}}`
- **API Key 安全**：交易所 API Key 仅从 `.env` 读取，**绝不通过任何 HTTP 接口返回前端**
- **CORS**：开发态放行 `localhost:5173`，生产态前后端同源

## 6. 关键决策与权衡

### 决策 1：实盘用 subprocess 而非 asyncio 原生
- **选择**：subprocess 拉起 `live_*.py`
- **放弃的方案**：后端 asyncio 任务直接跑策略循环
- **理由**：复用现有脚本不动核心代码；进程隔离便于启停与崩溃恢复；FastAPI 主循环不被长任务阻塞

### 决策 2：SQLite 而非 Postgres
- **选择**：SQLite
- **放弃的方案**：Postgres
- **理由**：单机零部署成本；v0.1 数据量小；用 SQLAlchemy 留好抽象，未来切 Postgres 改连接串即可

### 决策 3：策略参数只读
- **选择**：前端只展示策略参数，不支持编辑
- **放弃的方案**：可编辑并保存
- **理由**：参数定义散落在各策略类里，没有统一 schema；v0.1 先解决"看见"问题，编辑留给 v0.2 配合参数 schema 化

### 决策 4：回测结果不持久化
- **选择**：仅在内存 / 临时文件
- **放弃的方案**：落 SQLite
- **理由**：回测结果体积大（含完整交易明细），单机个人使用关闭即丢可接受；持久化留给 v0.2 配合历史对比功能

### 决策 5：进化进度走 WebSocket
- **选择**：WebSocket
- **放弃的方案**：HTTP 轮询
- **理由**：进化每代耗时 30s~数分钟，轮询体验差且浪费请求；FastAPI 原生支持 WS，前端 Naive UI 也有进度组件

### 决策 6：后端独立 pyproject.toml，与根交易核心环境隔离
- **选择**：`backend/pyproject.toml` 独立管理 fastapi/uvicorn/sqlalchemy 等依赖，根 `pyproject.toml` 不变
- **放弃的方案**：把后端依赖塞进根 `pyproject.toml`
- **理由**：根环境是交易核心（torch + ccxt + pandas），重且与 Web 无关；后端依赖轻量，独立管理避免污染交易核心环境、便于后续单独部署。后续业务 task 需要 import dex 时，在 backend 环境补装交易核心依赖

### 决策 7：backend 用 sys.path insert 引用根 dex/ 包
- **选择**：在 `backend/app/__init__.py` 顶部 `sys.path.insert(0, <项目根>)`
- **放弃的方案**：`[tool.uv.sources]` path 依赖引用根项目
- **理由**：根 `pyproject.toml` 的 `name=autoresearch-crypto` 含 `-` 不是合法 import 名，且根项目未声明 packages，path 依赖装了也 import 不到 `dex`；sys.path insert 一次性解决，所有 backend 模块均可 `from dex... import`

## 7. 待定项（Open Questions）

- 子进程 stdout 如何切片发给前端？考虑按行缓冲 + 环形缓冲区（限最近 1000 行）
- 多个交易所实盘同时跑时，logs/ 目录的并发写入是否需要加锁？需检查现有 `dex/live/common.py` 的锁机制
- 进化引擎被多个任务并发触发时如何排队？v0.1 先做单任务串行，多任务排队留给 v0.2
