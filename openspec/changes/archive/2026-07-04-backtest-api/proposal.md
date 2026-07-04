## 为什么

`strategy-management-api` 已暴露策略元数据，但前端要看回测结果（R-v0.1-ck-4）需要后端能扫描 `data/crypto/` 列交易对、执行回测、返回收益曲线 / 交易明细 / 指标。当前 `backend/app/api/v1/backtest.py` 是 setup-backend-scaffold 留下的占位端点。本 task 实现真实回测接口，调 dex 的 `StrategyEvaluator.simulate`（注：`spec/requirements.md` R-v0.1-ck-4 写「调 `BaseStrategy.simulate`」是笔误，实际 `simulate` 在 `StrategyEvaluator` 上，`BaseStrategy` 只有 `generate_signals`——本 task 按实际 API 实现，不动项目级 spec）。

## 变更内容

- 新增 `backend/app/services/backtest_runner.py`：封装「加载数据 → 实例化策略 → 生成信号 → 跑 simulate → 算指标」全流程，处理时间过滤、异常、空数据
- 新增 `backend/app/services/symbol_scanner.py`：扫描 `data/crypto/` 目录，从文件名 `{SYMBOL}_{INTERVAL}_{DAYS}d.parquet` 解析出可用交易对列表
- 新增 `backend/app/schemas/backtest.py`：`SymbolInfo` / `BacktestRequest` / `BacktestResponse` / `Trade` / `Metrics` / `EquityPoint` Pydantic 模型
- 改造 `backend/app/api/v1/backtest.py`：
  - `GET /api/v1/backtest/symbols` → 返回 `data/crypto/` 下可用交易对列表
  - `POST /api/v1/backtest/run` → 接 `{symbol, interval, days, strategy, start?, end?}`，返回回测结果
- `backend/` 补装 `pyarrow`（已在调研阶段装好）

## 功能 (Capabilities)

### 新增功能
- `backtest-api`: 后端回测执行接口，扫描 `data/crypto/` 列交易对 + 执行 `StrategyEvaluator.simulate` 回测 + 返回收益曲线 / 交易明细 / 指标

### 修改功能
<!-- 无 -->

## 影响

- 新增文件：`backend/app/services/backtest_runner.py`、`backend/app/services/symbol_scanner.py`、`backend/app/schemas/backtest.py`
- 修改文件：`backend/app/api/v1/backtest.py`（占位重写）、`backend/pyproject.toml`（+pyarrow）
- 复用：`backend/app/services/strategy_registry.py` 的 `discover_strategies` / `get_detail`（按名查策略类）
- 复用：`backend/app/api/error.py` 的统一错误格式
- 数据依赖：`data/crypto/` 目录（gitignored，用户须自行 `uv run python prepare_crypto.py` 下载）；目录不存在或为空时 `GET /symbols` 返回空数组
- 不动：`dex/` 代码、其他 router、前端
