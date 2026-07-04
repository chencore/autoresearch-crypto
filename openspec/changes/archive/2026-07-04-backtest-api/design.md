## 上下文

`spec/requirements.md` R-v0.1-ck-4 要求「调 `BaseStrategy.simulate` 跑回测」——但实际 `BaseStrategy` 只有 `generate_signals`（`dex/strategies/base.py:47`），`simulate` 在 `StrategyEvaluator` 上（`dex/strategies/base.py:91`）。这是项目级 spec 的笔误，本 task 按实际 API 实现，不动 requirements.md（仅人工修改）。

`data/crypto/` 目录被 `.gitignore` 忽略，用户须自行 `uv run python prepare_crypto.py` 下载。文件名格式 `{SYMBOL}_{INTERVAL}_{DAYS}d.parquet`，列含 `timestamp`(int ms)/`datetime`(pd.Timestamp)/`open`/`high`/`low`/`close`/`volume`/技术指标列。当前环境无数据，需在验证阶段生成 synthetic parquet 兜底。

`StrategyEvaluator.simulate(signals, prices, df)` 返回 `(equity_curve, trades)`，`trades` 元素形如 `{"type": "buy"|"sell"|"sell_short"|"buy_cover"|"sell_final", "step": int, "pnl"?: float}`——`step` 是 bar 索引，无 `timestamp`/`price`，需后端反查 `df` 填充。`compute_metrics(equity, trades)` 返回 6 指标 dict，值是 `numpy.float64` 需转原生。

## 目标 / 非目标

**目标：**
- `GET /api/v1/backtest/symbols`：扫描目录列交易对，目录不存在返回空数组
- `POST /api/v1/backtest/run`：执行完整回测流程，返回可 JSON 序列化的结果
- 时间段过滤（`start` / `end` ISO 8601）
- 30 秒内完成 60 天 5m 数据回测
- numpy 类型全部转原生 Python 类型

**非目标：**
- 不支持前端改策略参数（v0.1 只用默认参数，留给 v0.2）
- 不持久化回测结果（v0.1 内存返回，关闭即丢，留给 v0.2）
- 不并发跑多个回测（单机个人用，串行即可）
- 不做回测缓存（每次都重新跑，开发态策略可能改）
- 不实现 WebSocket 进度推送（回测是同步的，无中间进度；WebSocket 留给 evolution-api）
- 不动 `dex/` 代码（用现有 API）
- 不做数据校验（假设 parquet 列结构正确，列缺失时让 dex 抛错被全局异常处理器接）

## 决策

### 决策 1：文件名解析用正则，不读 parquet 元数据
- **选择**：`re.match(r"^(?P<symbol>.+?)_(?P<interval>\d+[mh])_(?P<days>\d+)d\.parquet$", filename)`
- **替代方案 A**：读 parquet 文件的 schema / metadata 推断 symbol/interval
- **替代方案 B**：维护一个 `data/crypto/index.json` 索引文件
- **理由**：文件名已是结构化的（`prepare_crypto.py` 写死格式），正则一行搞定；读 parquet 元数据慢且不一定有；index.json 会失同步。文件名是单一真相源。

### 决策 2：回测流程封装在 `backtest_runner.py`，不散在 router 里
- **选择**：`def run_backtest(req: BacktestRequest) -> BacktestResponse` 一函数包完整流程
- **替代方案**：在 router 里直接写
- **理由**：router 只做 HTTP 适配，业务逻辑在 service 层；后续 evolution-api 复用回测时可直接 import service 不走 HTTP

### 决策 3：trades 反查 timestamp/price 在 service 层做，不动 dex
- **选择**：simulate 返回后，遍历 trades 用 `df['datetime'].iloc[t['step']]` 与 `df['close'].iloc[t['step']]` 填充
- **替代方案 A**：改 `dex/strategies/base.py` 让 simulate 直接返回 timestamp/price
- **替代方案 B**：前端反查
- **理由**：不动 dex 是铁律（v0.1 在 dex 之上加 Web，不污染交易核心）；前端反查需把整个 df 传到前端，浪费带宽。service 层反查最干净。

### 决策 4：equity_curve 返回 `{step, timestamp, equity}` 三字段，不返回裸数组
- **选择**：每个点 `{"step": i, "timestamp": int_ms, "equity": float}`，前端直接用
- **替代方案**：返回三个并行数组 `{"steps": [...], "timestamps": [...], "equity": [...]}`
- **理由**：点对象更符合 JSON 习惯，前端 v-for 直接渲染；并行数组需 zip 才能用，麻烦。17280 点的数组 JSON 体积约 1MB，可接受。

### 决策 5：时间过滤用 `df['datetime']` 列（pd.Timestamp），不用 `timestamp` 列
- **选择**：`mask = (df['datetime'] >= pd.Timestamp(start)) & (df['datetime'] <= pd.Timestamp(end))`
- **替代方案**：用 `df['timestamp']`（int ms）比较
- **理由**：`datetime` 列是 pd.Timestamp，ISO 8601 字符串直接 `pd.Timestamp` 解析；`timestamp` 列是 ms int，前端要传 int 不友好。两者都对，但 ISO 字符串可读性高。

### 决策 6：numpy 类型转换用 `float()` / `int()` 显式转换，不用 `.tolist()` 兜底
- **选择**：`metrics[k] = float(v)`，`equity_curve[i] = {"step": i, "timestamp": int(ts), "equity": float(e)}`
- **替代方案**：`np.array(...).tolist()` 批量转
- **理由**：显式转换可读性高，避免 `tolist()` 把 dict 也转成 list 的坑；6 个 metrics + N 个 equity 点，循环转也不慢

### 决策 7：策略实例化复用 `strategy_registry.discover_strategies()`
- **选择**：从 `discover_strategies()` 找 `cls.__name__ == req.strategy` 的类，无参构造
- **替代方案**：维护 `STRATEGY_MAP = {"TrendFollowStrategy": TrendFollowStrategy, ...}`
- **理由**：`strategy_registry` 已实现扫描，复用避免重复；显式 map 会失同步

### 决策 8：evaluator 用 `dex.config` 默认值实例化
- **选择**：`from dex.config import INITIAL_CAPITAL, COMMISSION, SLIPPAGE` → `StrategyEvaluator(INITIAL_CAPITAL, COMMISSION, SLIPPAGE)`
- **替代方案**：让前端传 initial_capital 等参数
- **理由**：v0.1 不暴露这些（R-v0.1-ck-4 只说选策略 + 时间段），用 dex 默认值最简单

## 风险 / 权衡

- **[data/crypto/ 不存在]** → `GET /symbols` 必须返回空数组不抛错。缓解：`Path.exists()` 检查 + 目录不存在直接返回 `{"symbols": [], "total": 0}`
- **[parquet 文件损坏]** → `pd.read_parquet` 抛错。缓解：在 service 层 try/except，转 500 错误码 `data_load_failed`
- **[策略 generate_signals 抛错]** → 某策略可能因为数据列缺失抛 KeyError。缓解：service 层 try/except，转 500 错误码 `backtest_failed`，message 含原始异常
- **[数据列名不一致]** → `prepare_crypto.py` 计算的列名（`returns`/`rsi`/`macd` 等）可能与某策略期望的不一致。缓解：本 task 不解决，让 dex 抛错；若验证阶段发现 TrendFollowStrategy 跑不通，换 HybridMeanRevMomentumStrategy 或 PureActionStrategy 验证
- **[回测响应体积大]** → 60 天 5m 数据约 17280 点 equity_curve，每点 ~50 字节，~1MB JSON。缓解：v0.1 接受这个体积；v0.2 可加压缩或抽样
- **[numpy float64 不可 JSON 序列化]** → Pydantic 默认不转。缓解：service 层显式 `float()` 转换；Pydantic 模型字段类型用 `float` 不用 `np.floating`
- **[evaluator.simulate 在异常数据上返回空 equity]** → 触发 `compute_metrics` 的 `equity[-1]` IndexError。缓解：service 层检查 `len(equity) == 0`，转 500 错误码 `backtest_failed`

## 迁移计划

- 新增 `backend/app/services/symbol_scanner.py` + `backend/app/services/backtest_runner.py` + `backend/app/schemas/backtest.py`
- 改造 `backend/app/api/v1/backtest.py` 替换占位
- `backend/` `uv add pyarrow`（已装）
- 验证：用 `prepare_crypto.py` 下载 ETHUSDT 5m 7d 数据；若下载失败，生成 synthetic parquet
- curl 验证 5 项：symbols 空目录、symbols 含数据、run 正常、run 数据不存在 404、run 策略不存在 404
- 回滚：`git checkout backend/app/api/v1/backtest.py` 恢复占位 + 删新增文件 + `uv remove pyarrow`

## 待解决问题

- `MultiTFEnsembleStrategy` 等多周期策略可能需要外部数据，本 task 不特殊处理，让默认参数自己跑
- `GridStrategy` 的 `signal_kind=position_target` 与 `StrategyEvaluator.simulate` 的 0/1/2/3 编码不兼容，跑 GridStrategy 会得到全 flat 信号——本 task 不修复，留给后续 task 在 UI 上禁选 Grid 或在 service 层 special-case
