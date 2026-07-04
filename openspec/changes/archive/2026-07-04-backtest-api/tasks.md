## 1. 依赖与数据准备

- [x] 1.1 在 `backend/` 运行 `uv add pyarrow`（调研阶段已装，确认 `pyproject.toml` 含依赖）
- [x] 1.2 验证 `uv run python -c "import pyarrow; import pandas; import numpy; print('ok')"` 成功
- [x] 1.3 准备验证数据：尝试 `uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --limit 7`（若 Binance API 不可达或 torch 环境问题，fallback 用 `pandas + pyarrow` 生成 synthetic `ETHUSDT_5m_7d.parquet`，列含 timestamp/datetime/open/high/low/close/volume）

## 2. Pydantic 模型

- [x] 2.1 创建 `backend/app/schemas/__init__.py`（已存在则跳过）
- [x] 2.2 创建 `backend/app/schemas/backtest.py`：
  - `class SymbolInfo(BaseModel)`: `symbol: str`、`interval: str`、`days: int`、`file: str`
  - `class SymbolListResponse(BaseModel)`: `symbols: list[SymbolInfo]`、`total: int`
  - `class BacktestRequest(BaseModel)`: `symbol: str`、`interval: str`、`days: int`、`strategy: str`、`start: str | None = None`、`end: str | None = None`
  - `class EquityPoint(BaseModel)`: `step: int`、`timestamp: int`、`equity: float`
  - `class Trade(BaseModel)`: `type: str`、`step: int`、`timestamp: int`、`price: float`、`pnl: float | None = None`
  - `class Metrics(BaseModel)`: `total_return: float`、`annualized_return: float`、`annualized_vol: float`、`sharpe_ratio: float`、`max_drawdown: float`、`win_rate: float`
  - `class BacktestMeta(BaseModel)`: `strategy: str`、`symbol: str`、`interval: str`、`bars: int`
  - `class BacktestResponse(BaseModel)`: `equity_curve: list[EquityPoint]`、`trades: list[Trade]`、`metrics: Metrics`、`meta: BacktestMeta`

## 3. 交易对扫描服务

- [x] 3.1 创建 `backend/app/services/symbol_scanner.py`：
  - `import re`、`from pathlib import Path`、`from dex.config import DATA_DIR`
  - `FILENAME_RE = re.compile(r"^(?P<symbol>.+?)_(?P<interval>\d+[mh])_(?P<days>\d+)d\.parquet$")`
  - `def list_symbols() -> list[SymbolInfo]`:
    - 若 `not DATA_DIR.exists()` 返回 `[]`
    - 遍历 `DATA_DIR.glob("*.parquet")`，对每个文件名 match `FILENAME_RE`
    - 匹配成功构造 `SymbolInfo(symbol, interval, int(days), file=name)`
    - 不匹配的文件跳过
    - 按 `symbol` 字母序排序返回

## 4. 回测执行服务

- [x] 4.1 创建 `backend/app/services/backtest_runner.py`：
  - `import pandas as pd`、`import numpy as np`、`from pathlib import Path`
  - `from dex.config import DATA_DIR, INITIAL_CAPITAL, COMMISSION, SLIPPAGE`
  - `from dex.strategies.base import StrategyEvaluator`
  - `from app.services.strategy_registry import discover_strategies`
  - `from app.schemas.backtest import BacktestRequest, BacktestResponse, EquityPoint, Trade, Metrics, BacktestMeta`
  - `class BacktestError(Exception)`: 自定义异常，含 `code` 与 `message`
  - `def _find_strategy_class(name: str)`: 从 `discover_strategies()` 找 `cls.__name__ == name`，找不到抛 `BacktestError("not_found", f"strategy not found: {name}")`
  - `def _load_data(req: BacktestRequest) -> pd.DataFrame`:
    - `filepath = DATA_DIR / f"{req.symbol}_{req.interval}_{req.days}d.parquet"`
    - 若 `not filepath.exists()` 抛 `BacktestError("data_not_found", f"data file not found: {filepath.name}")`
    - `df = pd.read_parquet(filepath)`
    - 若 `req.start` 与 `req.end` 提供且非空，`mask = (df['datetime'] >= pd.Timestamp(req.start)) & (df['datetime'] <= pd.Timestamp(req.end))`，`df = df[mask]`
    - 若 `len(df) == 0` 抛 `BacktestError("no_data_in_range", f"no data in range {req.start} ~ {req.end}")`
    - 返回 df
  - `def run_backtest(req: BacktestRequest) -> BacktestResponse`:
    - `cls = _find_strategy_class(req.strategy)`
    - `df = _load_data(req)`
    - `strategy = cls()`（无参构造，用默认参数）
    - `signals = strategy.generate_signals(df)`（不传 runtime_params，用默认行为）
    - `prices = df['close'].values`
    - `evaluator = StrategyEvaluator(INITIAL_CAPITAL, COMMISSION, SLIPPAGE)`
    - `equity, trades = evaluator.simulate(signals, prices, df)`
    - 若 `len(equity) == 0` 抛 `BacktestError("backtest_failed", "equity curve is empty")`
    - `metrics_dict = evaluator.compute_metrics(equity, trades)`
    - 构造 `equity_curve: list[EquityPoint]`：遍历 `range(len(equity))`，`EquityPoint(step=i, timestamp=int(df['timestamp'].iloc[i]), equity=float(equity[i]))`
    - 构造 `trades_out: list[Trade]`：遍历 `trades`，`Trade(type=t['type'], step=int(t['step']), timestamp=int(df['timestamp'].iloc[t['step']]), price=float(df['close'].iloc[t['step']]), pnl=float(t['pnl']) if 'pnl' in t else None)`
    - 构造 `Metrics(**{k: float(v) for k, v in metrics_dict.items()})`
    - 构造 `BacktestMeta(strategy=req.strategy, symbol=req.symbol, interval=req.interval, bars=int(len(df)))`
    - 返回 `BacktestResponse(equity_curve=equity_curve, trades=trades_out, metrics=metrics, meta=meta)`
    - 整个流程 try/except `BacktestError` 直接 raise，其他异常转 `BacktestError("backtest_failed", str(e))`

## 5. 路由改造

- [x] 5.1 改造 `backend/app/api/v1/backtest.py`：
  - 移除占位 `@router.get("")` 返回 `{"module": "backtest", "status": "todo"}`
  - `@router.get("/symbols", response_model=SymbolListResponse)`：调 `list_symbols()`，返回 `{"symbols": [...], "total": N}`
  - `@router.post("/run", response_model=BacktestResponse)`：接收 `req: BacktestRequest`，try `run_backtest(req)` except `BacktestError as e` 抛 `HTTPException(status_code=_status_for(e.code), detail=f"{e.code}: {e.message}")`，全局异常处理器转 `{"error": {"code": e.code, "message": e.message}}`
  - `_status_for(code)`: `"not_found" → 404`、`"data_not_found" → 404`、`"no_data_in_range" → 400`、其他 → 500

## 6. 启动验证

- [x] 6.1 启动后端 `cd backend && uv run uvicorn app.main:app --port 8000`
- [x] 6.2 `curl -s http://127.0.0.1:8000/api/v1/backtest/symbols | python3 -m json.tool` 验证返回 `symbols` 数组（含 ETHUSDT_5m_7d 若数据已生成）
- [x] 6.3 `curl -s -X POST http://127.0.0.1:8000/api/v1/backtest/run -H "Content-Type: application/json" -d '{"symbol":"ETHUSDT","interval":"5m","days":7,"strategy":"TrendFollowStrategy"}' | python3 -m json.tool` 验证返回 equity_curve / trades / metrics / meta
- [x] 6.4 验证响应里所有数值都是原生类型：`python3 -c "import json,sys; d=json.load(sys.stdin); print(type(d['metrics']['sharpe_ratio']).__name__, type(d['equity_curve'][0]['equity']).__name__, type(d['equity_curve'][0]['timestamp']).__name__)"` 输出 `float float int`
- [x] 6.5 验证数据不存在 404：`curl -s -X POST http://127.0.0.1:8000/api/v1/backtest/run -H "Content-Type: application/json" -d '{"symbol":"DOGEUSDT","interval":"5m","days":7,"strategy":"TrendFollowStrategy"}'` 返回 `{"error":{"code":"data_not_found","message":"data file not found: DOGEUSDT_5m_7d.parquet"}}`
- [x] 6.6 验证策略不存在 404：`curl -s -X POST http://127.0.0.1:8000/api/v1/backtest/run -H "Content-Type: application/json" -d '{"symbol":"ETHUSDT","interval":"5m","days":7,"strategy":"NonExistent"}'` 返回 `{"error":{"code":"not_found","message":"strategy not found: NonExistent"}}`
- [x] 6.7 验证时间过滤：`curl -s -X POST http://127.0.0.1:8000/api/v1/backtest/run -H "Content-Type: application/json" -d '{"symbol":"ETHUSDT","interval":"5m","days":7,"strategy":"TrendFollowStrategy","start":"2026-06-25T00:00:00","end":"2026-06-30T00:00:00"}'` 验证 `meta.bars` 小于无过滤版本
- [x] 6.8 验证回测耗时 < 30s：`curl -s -o /dev/null -w "%{time_total}\n" -X POST http://127.0.0.1:8000/api/v1/backtest/run -H "Content-Type: application/json" -d '{"symbol":"ETHUSDT","interval":"5m","days":7,"strategy":"TrendFollowStrategy"}'` 输出 < 30
- [x] 6.9 验证空目录：临时把 `data/crypto/` 改名 → `curl /api/v1/backtest/symbols` 返回 `{"symbols": [], "total": 0}`，恢复目录
