## 为什么

`spec/tasks.md` 的 `strategy-management-ui` task 需要一个后端接口提供策略列表与详情数据。`dex/strategies/` 下有 11 个策略类，参数定义散落在各 `__init__`，无统一 schema 也无 registry。本 task 用反射 + 目录扫描暴露策略元数据，让前端只读展示策略参数与说明，避免前端硬编码策略清单。

## 变更内容

- 新增 `backend/app/services/strategy_registry.py`：扫描 `dex/strategies/*.py`，发现 `BaseStrategy` 子类；用 `inspect` 提取类名、模块、docstring、`__init__` 参数（名/类型/默认值）。共发现 10 个具体策略类。
- 新增 `backend/app/schemas/strategy.py`：Pydantic 响应模型（`StrategySummary`、`StrategyDetail`、`ParamDef`）
- 改造 `backend/app/api/v1/strategy.py`：
  - `GET /api/v1/strategy` — 返回策略列表摘要
  - `GET /api/v1/strategy/{name}` — 返回某策略详情含完整参数定义
  - 占位 `GET /api/v1/strategy`（根）改为列表接口
- 新增 `backend/app/schemas/__init__.py`
- 新增 `backend/app/services/__init__.py`
- **不动**：`dex/` 包代码（纯反射读取，不修改策略类）
- **BREAKING**：`GET /api/v1/strategy` 响应从 `{"module": "strategy", "status": "todo"}` 改为策略列表 JSON

## 功能 (Capabilities)

### 新增功能
- `strategy-management`: 策略元数据只读接口——扫描 `dex/strategies/` 发现所有 `BaseStrategy` 子类，反射 `__init__` 提取参数定义，提供列表与详情两个 REST 端点。

### 修改功能
<!-- 无现有功能被修改（backend-scaffold 的 strategy router 占位被替换，但那是本 task 的范围） -->

## 影响

- **代码**：新增 `backend/app/services/strategy_registry.py`、`backend/app/schemas/strategy.py`；改造 `backend/app/api/v1/strategy.py`
- **依赖**：复用 `backend` 已装的 fastapi / pydantic；新增对根 `dex` 包的运行时引用（`from dex.strategies.base import BaseStrategy`）—— 需在 backend 环境补装 `numpy` / `pandas`（dex.strategies.base 的依赖）
- **API**：
  - `GET /api/v1/strategy` → 200 `{"strategies": [...], "total": N}`
  - `GET /api/v1/strategy/{name}` → 200 详情 JSON；404 `{"error": {"code": "not_found", "message": "..."}}`
- **数据模型**：无（v0.1 策略元数据纯反射，不落库）
- **运行状态**：本 task 不实现实盘进程状态查询（留给 `live-monitor-api` task）；详情接口返回 `live_status: "unknown"` 占位
