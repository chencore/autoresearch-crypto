## 新增需求

### 需求:交易对列表接口

后端必须提供 `GET /api/v1/backtest/symbols` 接口，扫描 `data/crypto/` 目录下的 parquet 文件，从文件名 `{SYMBOL}_{INTERVAL}_{DAYS}d.parquet` 模式解析出可用交易对。响应必须返回 `symbols` 数组与 `total` 字段，每个元素含 `symbol`、`interval`、`days`、`file` 字段。`data/crypto/` 目录不存在或为空时必须返回空数组与 `total: 0`，禁止抛错。列表必须按 `symbol` 字母序排序。

#### 场景:目录不存在
- **当** `data/crypto/` 目录不存在
- **那么** 返回 `{"symbols": [], "total": 0}`

#### 场景:目录含 ETHUSDT_5m_60d.parquet
- **当** `data/crypto/` 含 `ETHUSDT_5m_60d.parquet` 与 `BTCUSDT_5m_60d.parquet`
- **那么** 返回 `{"symbols": [{"symbol": "BTCUSDT", "interval": "5m", "days": 60, "file": "BTCUSDT_5m_60d.parquet"}, {"symbol": "ETHUSDT", "interval": "5m", "days": 60, "file": "ETHUSDT_5m_60d.parquet"}], "total": 2}`

#### 场景:非 parquet 文件被忽略
- **当** `data/crypto/` 含 `README.md` 与 `ETHUSDT_5m_60d.parquet`
- **那么** 只返回 ETHUSDT 一条，非 parquet 文件被忽略

#### 场景:文件名不匹配模式
- **当** `data/crypto/` 含 `random.parquet`（不匹配 `{SYMBOL}_{INTERVAL}_{DAYS}d.parquet`）
- **那么** 该文件被跳过，不出现在响应里

### 需求:回测执行接口

后端必须提供 `POST /api/v1/backtest/run` 接口，请求体含 `symbol`、`interval`、`days`、`strategy`（策略类名）四个必填字段，`start` 与 `end`（ISO 8601 字符串）两个可选字段用于时间段过滤。响应必须返回 `equity_curve`（数组）、`trades`（数组）、`metrics`（对象）、`meta`（含策略名、交易对、interval、bar 数）。回测必须按「加载数据 → 时间过滤 → 实例化策略 → `generate_signals(df)` → `StrategyEvaluator().simulate(signals, prices, df)` → `compute_metrics(equity, trades)`」流程执行。

#### 场景:正常回测
- **当** 客户端 POST `{"symbol": "ETHUSDT", "interval": "5m", "days": 60, "strategy": "TrendFollowStrategy"}`
- **那么** 返回 200，响应含 `equity_curve` 数组（每点 `{step, timestamp, equity}`）、`trades` 数组（每点 `{type, step, pnl?, timestamp?, price?}`）、`metrics` 对象（含 `total_return`/`annualized_return`/`annualized_vol`/`sharpe_ratio`/`max_drawdown`/`win_rate`）、`meta` 对象

#### 场景:数据文件不存在
- **当** 客户端 POST `{"symbol": "DOGEUSDT", "interval": "5m", "days": 60, "strategy": "TrendFollowStrategy"}` 但 `data/crypto/DOGEUSDT_5m_60d.parquet` 不存在
- **那么** 返回 404，响应体为 `{"error": {"code": "data_not_found", "message": "data file not found: DOGEUSDT_5m_60d.parquet"}}`

#### 场景:策略不存在
- **当** 客户端 POST `{"symbol": "ETHUSDT", "interval": "5m", "days": 60, "strategy": "NonExistent"}`
- **那么** 返回 404，响应体为 `{"error": {"code": "not_found", "message": "strategy not found: NonExistent"}}`

#### 场景:时间段过滤
- **当** 客户端 POST 含 `start` 与 `end` 字段（ISO 8601）
- **那么** 后端必须按 `df['datetime']` 列过滤后再跑回测，`meta.bars` 反映过滤后的 bar 数

### 需求:回测响应数据结构

后端返回的回测响应必须遵循固定结构。`equity_curve` 每个点必须含 `step`（int，从 0 起）、`timestamp`（int，毫秒）、`equity`（float）。`trades` 每个点必须含 `type`（"buy"/"sell"/"sell_short"/"buy_cover"/"sell_final"）、`step`（int），`pnl` 字段可选（float，平仓时存在），`timestamp` 必须从 `step` 反查后填充，`price` 必须从 `df['close'].iloc[step]` 反查后填充。`metrics` 必须含 6 个 float 字段：`total_return`、`annualized_return`、`annualized_vol`、`sharpe_ratio`、`max_drawdown`、`win_rate`。所有 numpy 类型必须转原生 Python 类型，禁止返回 `numpy.float64` 等不可 JSON 序列化的类型。

#### 场景:权益曲线点结构
- **当** 回测完成返回 `equity_curve`
- **那么** 每个点形如 `{"step": 0, "timestamp": 1700000000000, "equity": 10000.0}`，可直接 `json.dumps`

#### 场景:交易记录反查时间戳与价格
- **当** trades 含 `{"type": "buy", "step": 100}`
- **那么** 后端必须填充 `timestamp`（df.datetime.iloc[100] 转 int 毫秒）与 `price`（df.close.iloc[100] 转 float），响应形如 `{"type": "buy", "step": 100, "timestamp": ..., "price": 2050.32}`

#### 场景:metrics 全字段
- **当** 回测完成返回 metrics
- **那么** 必须含 `{"total_return": 0.15, "annualized_return": 0.32, "annualized_vol": 0.45, "sharpe_ratio": 0.71, "max_drawdown": -0.08, "win_rate": 0.55}`，全 float

### 需求:回测性能

后端执行回测必须在 30 秒内完成（含数据加载 + 信号生成 + simulate + 指标计算）。数据加载必须只读 parquet 文件一次，禁止重复 IO。`generate_signals` 与 `simulate` 必须串行执行，禁止并发（避免 numpy GIL 争用）。响应序列化前必须把 numpy 数组转 list，禁止在 JSON 序列化阶段才转（避免大数组耗时）。

#### 场景:60 天 5m 数据回测耗时
- **当** 用 ETHUSDT 5m 60 天数据（约 17280 bar）跑 TrendFollowStrategy
- **那么** 端到端响应时间 < 30 秒

#### 场景:numpy 类型转原生
- **当** `compute_metrics` 返回 `{"sharpe_ratio": np.float64(0.71)}`
- **那么** 响应里必须是 `{"sharpe_ratio": 0.71}`（原生 float），可被 `json.dumps` 序列化

### 需求:策略实例化与运行时参数

后端必须用策略类的默认参数实例化策略（v0.1 不支持前端改参数）。若策略 `generate_signals` 签名含 `enable_short` 等运行时参数，必须按默认行为调用（不传额外参数，或传与默认一致的值）。`StrategyEvaluator` 必须用 `dex.config` 的默认 `INITIAL_CAPITAL` / `COMMISSION` / `SLIPPAGE` 实例化，v0.1 不暴露这些参数给前端。

#### 场景:默认参数实例化
- **当** 客户端 POST `{"strategy": "TrendFollowStrategy", ...}`
- **那么** 后端 `TrendFollowStrategy()` 无参构造，用策略 `__init__` 的所有默认值

#### 场景:evaluator 默认配置
- **当** 回测执行
- **那么** `StrategyEvaluator()` 用 `dex.config.INITIAL_CAPITAL=10000.0` / `COMMISSION=0.0002` / `SLIPPAGE=0.0002`
