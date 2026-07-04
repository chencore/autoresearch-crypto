# Plan: backtest-api

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **6 个阶段**。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | 数据准备 | `data/crypto/ETHUSDT_5m_7d.parquet` 可读 | 0.5d |
| S2 | Pydantic 模型 | `schemas/backtest.py` 8 个模型 | 0.25d |
| S3 | 交易对扫描服务 | `services/symbol_scanner.py` | 0.25d |
| S4 | 回测执行服务 | `services/backtest_runner.py` 完整流程 | 0.75d |
| S5 | 路由改造 | `api/v1/backtest.py` 两个端点 | 0.25d |
| S6 | 启动验证 | 9 项 curl 全绿 | 0.25d |

**总估时**：约 2.25 人日。

---

## S1. 数据准备

### 目标

`data/crypto/ETHUSDT_5m_7d.parquet` 存在且可被 `pd.read_parquet` 读取，列含 `timestamp`(int ms) / `datetime`(pd.Timestamp) / `open` / `high` / `low` / `close` / `volume`。

### 实施步骤

1. 尝试 `uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --limit 7`（在根目录环境跑，可能因 torch 装不上失败）
2. 若失败，用 backend 环境生成 synthetic parquet：
   ```python
   # backend/scripts/gen_synthetic_data.py（临时脚本，验证后删）
   import pandas as pd
   import numpy as np
   from pathlib import Path
   import sys
   sys.path.insert(0, str(Path(__file__).parent.parent.parent))
   from dex.config import DATA_DIR

   DATA_DIR.mkdir(parents=True, exist_ok=True)
   n = 7 * 24 * 12  # 7 天 5m bar
   start_ts = pd.Timestamp("2026-06-25T00:00:00")
   timestamps = pd.date_range(start_ts, periods=n, freq="5min")
   ts_ms = (timestamps.astype("int64") // 10**6).astype("int64")
   np.random.seed(42)
   close = 2000 + np.cumsum(np.random.randn(n) * 5)
   df = pd.DataFrame({
       "timestamp": ts_ms,
       "datetime": timestamps,
       "open": close - np.random.rand(n) * 2,
       "high": close + np.random.rand(n) * 3,
       "low": close - np.random.rand(n) * 3,
       "close": close,
       "volume": np.random.rand(n) * 100,
   })
   df.to_parquet(DATA_DIR / "ETHUSDT_5m_7d.parquet")
   print(f"wrote {len(df)} bars to {DATA_DIR / 'ETHUSDT_5m_7d.parquet'}")
   ```
3. 验证：`uv run python -c "import pandas as pd; df=pd.read_parquet('/Users/chenke/code/autoresearch-crypto/data/crypto/ETHUSDT_5m_7d.parquet'); print(df.columns.tolist()); print(len(df)); print(df.head(2))"`

### ✅ 完成验证
- [ ] `data/crypto/ETHUSDT_5m_7d.parquet` 存在
- [ ] `pd.read_parquet` 能读，列含 7 列，约 2016 行

---

## S2. Pydantic 模型

### 目标

定义请求 / 响应的 Pydantic schema，对齐 design 决策 4/6。

### 实施步骤

1. 创建 `backend/app/schemas/backtest.py`：
   ```python
   from pydantic import BaseModel

   class SymbolInfo(BaseModel):
       symbol: str
       interval: str
       days: int
       file: str

   class SymbolListResponse(BaseModel):
       symbols: list[SymbolInfo]
       total: int

   class BacktestRequest(BaseModel):
       symbol: str
       interval: str
       days: int
       strategy: str
       start: str | None = None
       end: str | None = None

   class EquityPoint(BaseModel):
       step: int
       timestamp: int
       equity: float

   class Trade(BaseModel):
       type: str
       step: int
       timestamp: int
       price: float
       pnl: float | None = None

   class Metrics(BaseModel):
       total_return: float
       annualized_return: float
       annualized_vol: float
       sharpe_ratio: float
       max_drawdown: float
       win_rate: float

   class BacktestMeta(BaseModel):
       strategy: str
       symbol: str
       interval: str
       bars: int

   class BacktestResponse(BaseModel):
       equity_curve: list[EquityPoint]
       trades: list[Trade]
       metrics: Metrics
       meta: BacktestMeta
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.schemas.backtest import BacktestRequest, BacktestResponse; print('ok')"` 输出 `ok`

---

## S3. 交易对扫描服务

### 目标

扫描 `data/crypto/` 列出可用交易对，目录不存在返回空数组。

### 实施步骤

1. 创建 `backend/app/services/symbol_scanner.py`：
   ```python
   import re
   from dex.config import DATA_DIR
   from app.schemas.backtest import SymbolInfo

   FILENAME_RE = re.compile(r"^(?P<symbol>.+?)_(?P<interval>\d+[mh])_(?P<days>\d+)d\.parquet$")

   def list_symbols() -> list[SymbolInfo]:
       if not DATA_DIR.exists():
           return []
       results: list[SymbolInfo] = []
       for path in sorted(DATA_DIR.glob("*.parquet")):
           m = FILENAME_RE.match(path.name)
           if not m:
               continue
           results.append(SymbolInfo(
               symbol=m.group("symbol"),
               interval=m.group("interval"),
               days=int(m.group("days")),
               file=path.name,
           ))
       results.sort(key=lambda s: s.symbol)
       return results
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.services.symbol_scanner import list_symbols; ss=list_symbols(); print(len(ss), [s.symbol for s in ss])"` 输出 `1 ['ETHUSDT']`

---

## S4. 回测执行服务

### 目标

封装完整回测流程，处理异常 + numpy 类型转换。

### 实施步骤

1. 创建 `backend/app/services/backtest_runner.py`：
   ```python
   from __future__ import annotations
   import pandas as pd
   from dex.config import DATA_DIR, INITIAL_CAPITAL, COMMISSION, SLIPPAGE
   from dex.strategies.base import StrategyEvaluator
   from app.services.strategy_registry import discover_strategies
   from app.schemas.backtest import (
       BacktestRequest, BacktestResponse, EquityPoint, Trade, Metrics, BacktestMeta,
   )

   class BacktestError(Exception):
       def __init__(self, code: str, message: str) -> None:
           self.code = code
           self.message = message
           super().__init__(f"{code}: {message}")

   def _find_strategy_class(name: str):
       for cls in discover_strategies():
           if cls.__name__ == name:
               return cls
       raise BacktestError("not_found", f"strategy not found: {name}")

   def _load_data(req: BacktestRequest) -> pd.DataFrame:
       filepath = DATA_DIR / f"{req.symbol}_{req.interval}_{req.days}d.parquet"
       if not filepath.exists():
           raise BacktestError("data_not_found", f"data file not found: {filepath.name}")
       df = pd.read_parquet(filepath)
       if req.start and req.end:
           mask = (df["datetime"] >= pd.Timestamp(req.start)) & (df["datetime"] <= pd.Timestamp(req.end))
           df = df[mask].reset_index(drop=True)
       elif req.start:
           mask = df["datetime"] >= pd.Timestamp(req.start)
           df = df[mask].reset_index(drop=True)
       elif req.end:
           mask = df["datetime"] <= pd.Timestamp(req.end)
           df = df[mask].reset_index(drop=True)
       if len(df) == 0:
           raise BacktestError("no_data_in_range", f"no data in range {req.start} ~ {req.end}")
       return df

   def run_backtest(req: BacktestRequest) -> BacktestResponse:
       try:
           cls = _find_strategy_class(req.strategy)
           df = _load_data(req)
           strategy = cls()
           signals = strategy.generate_signals(df)
           prices = df["close"].values
           evaluator = StrategyEvaluator(INITIAL_CAPITAL, COMMISSION, SLIPPAGE)
           equity, trades = evaluator.simulate(signals, prices, df)
           if len(equity) == 0:
               raise BacktestError("backtest_failed", "equity curve is empty")
           metrics_dict = evaluator.compute_metrics(equity, trades)

           ts_values = df["timestamp"].values
           close_values = df["close"].values
           equity_curve = [
               EquityPoint(step=int(i), timestamp=int(ts_values[i]), equity=float(equity[i]))
               for i in range(len(equity))
           ]
           trades_out = [
               Trade(
                   type=str(t["type"]),
                   step=int(t["step"]),
                   timestamp=int(ts_values[t["step"]]),
                   price=float(close_values[t["step"]]),
                   pnl=float(t["pnl"]) if "pnl" in t else None,
               )
               for t in trades
           ]
           metrics = Metrics(**{k: float(v) for k, v in metrics_dict.items()})
           meta = BacktestMeta(strategy=req.strategy, symbol=req.symbol, interval=req.interval, bars=int(len(df)))
           return BacktestResponse(equity_curve=equity_curve, trades=trades_out, metrics=metrics, meta=meta)
       except BacktestError:
           raise
       except Exception as e:
           raise BacktestError("backtest_failed", str(e)) from e
   ```

### ✅ 完成验证
- [ ] `uv run python -c "from app.services.backtest_runner import run_backtest; from app.schemas.backtest import BacktestRequest; r=run_backtest(BacktestRequest(symbol='ETHUSDT', interval='5m', days=7, strategy='TrendFollowStrategy')); print('bars:', r.meta.bars, 'equity points:', len(r.equity_curve), 'trades:', len(r.trades), 'metrics:', r.metrics.model_dump())"` 输出非空 + 全 float metrics

---

## S5. 路由改造

### 目标

替换 backtest router 占位为两个真实端点。

### 实施步骤

1. 改造 `backend/app/api/v1/backtest.py`：
   ```python
   from fastapi import APIRouter, HTTPException
   from app.schemas.backtest import SymbolListResponse, BacktestRequest, BacktestResponse
   from app.services.symbol_scanner import list_symbols
   from app.services.backtest_runner import run_backtest, BacktestError

   router = APIRouter()

   _STATUS_MAP = {
       "not_found": 404,
       "data_not_found": 404,
       "no_data_in_range": 400,
   }

   @router.get("/symbols", response_model=SymbolListResponse)
   async def list_symbols_endpoint() -> SymbolListResponse:
       symbols = list_symbols()
       return SymbolListResponse(symbols=symbols, total=len(symbols))

   @router.post("/run", response_model=BacktestResponse)
   async def run_backtest_endpoint(req: BacktestRequest) -> BacktestResponse:
       try:
           return run_backtest(req)
       except BacktestError as e:
           status = _STATUS_MAP.get(e.code, 500)
           raise HTTPException(status_code=status, detail=f"{e.code}: {e.message}")
   ```
   - 全局异常处理器已把 `HTTPException` 转成 `{"error": {"code": ..., "message": ...}}`，但当前实现是把 `detail` 整体当 `message`，需要确认 detail 格式
   - 检查 `backend/app/main.py` 或 `backend/app/api/error.py` 的异常处理器，若 detail 是 `"code: message"` 字符串，需改成解析后传 `{code, message}`

2. 若全局异常处理器不支持自定义 code，改用 `JSONResponse` 直接返回：
   ```python
   from fastapi.responses import JSONResponse
   @router.post("/run", response_model=BacktestResponse)
   async def run_backtest_endpoint(req: BacktestRequest) -> BacktestResponse:
       try:
           return run_backtest(req)
       except BacktestError as e:
           status = _STATUS_MAP.get(e.code, 500)
           return JSONResponse(
               status_code=status,
               content={"error": {"code": e.code, "message": e.message}},
           )
   ```
   - 优先用这种，避免依赖全局处理器的实现细节

### ✅ 完成验证
- [ ] `uv run python -c "from app.api.v1.backtest import router; print([(r.path, r.methods) for r in router.routes])"` 显示 `/symbols` GET 与 `/run` POST

---

## S6. 启动验证

### 目标

9 项 curl + typecheck 全绿。

### 实施步骤

1. 启动后端 `cd backend && uv run uvicorn app.main:app --port 8000`
2. 逐项执行 curl 验证

### ✅ 完成验证
- [ ] 6.2 `curl /api/v1/backtest/symbols` 返回含 ETHUSDT_5m_7d
- [ ] 6.3 `curl -X POST /api/v1/backtest/run` 正常返回 equity_curve / trades / metrics / meta
- [ ] 6.4 响应数值全原生类型（float / int）
- [ ] 6.5 数据不存在 → 404 + `{"error":{"code":"data_not_found",...}}`
- [ ] 6.6 策略不存在 → 404 + `{"error":{"code":"not_found",...}}`
- [ ] 6.7 时间过滤后 `meta.bars` 减少
- [ ] 6.8 回测耗时 < 30s
- [ ] 6.9 空目录返回 `{"symbols":[],"total":0}`

---

## 风险与回滚

- **[data/crypto/ 不存在]** → S1 synthetic parquet 兜底；API 层 `Path.exists()` 检查不抛错
- **[TrendFollowStrategy 在 synthetic 数据上跑空 equity]** → 改用 HybridMeanRevMomentumStrategy 或 PureActionStrategy 验证
- **[numpy 类型漏转]** → service 层显式 `float()` / `int()`；curl 验证 6.4 检查
- **[回测 > 30s]** → synthetic 7 天数据 ~2000 bar 应 < 1s；若超时检查是否重复读 parquet
- **[全局异常处理器不识别自定义 code]** → S5.2 用 `JSONResponse` 直接返回，绕过处理器
- **[回滚]**：`git checkout backend/app/api/v1/backtest.py` 恢复占位 + 删新增 schemas/services + `uv remove pyarrow`
