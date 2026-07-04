## 为什么

R-v0.1-ck-5 要求前端能从面板启停 Binance / OKX / Nado DEX 三个实盘进程，监控持仓 / 未实现盈亏 / 最近成交 / 策略状态 / 实时日志。现有 `live_binance_quant.py` / `live_okx_quant.py` / `live_nado_quant.py` 是独立 CLI 脚本，每次启动要敲命令行 + 编辑 `.env` + tail 日志，体验差。本 task 在后端用 `subprocess.Popen` 拉起 / 停止这三个脚本，读 `logs/live_*_state.json` 状态文件 + `logs/live_*_log.txt` 日志文件，通过 HTTP 暴露给前端轮询。

## 变更内容

- 新增 `backend/app/services/live_manager.py`：进程管理器（start / stop / status / logs），单例，维护 `exchange → Popen` 映射，处理子进程退出回收
- 新增 `backend/app/services/live_state_reader.py`：解析 `logs/live_*_state.json` 与 `logs/live_*_log.txt`，返回 Pydantic 模型
- 新增 `backend/app/schemas/live.py`：`ExchangeInfo` / `LiveStatusRequest` 不需要（path param）/ `LiveStatus` / `LogEntry` / `LogResponse` / `StartRequest` / `StopRequest` 模型
- 改造 `backend/app/api/v1/live.py`：
  - `GET /api/v1/live/exchanges` → 列 3 个交易所 + 当前运行状态
  - `POST /api/v1/live/start` → body `{exchange, symbol, mode, capital?, leverage?}` → 启动子进程
  - `POST /api/v1/live/stop` → body `{exchange}` → 终止子进程
  - `GET /api/v1/live/status/{exchange}` → 读 state 文件返回持仓 / 未实现盈亏 / 最近成交 / 策略状态
  - `GET /api/v1/live/logs/{exchange}?tail=200` → 读日志文件末尾 N 行

## 功能 (Capabilities)

### 新增功能
- `live-monitor-api`: 后端实盘进程管理与状态查询，subprocess 拉起 / 停止 `live_*.py`，读 `logs/` 状态文件 + 日志文件

### 修改功能
<!-- 无 -->

## 影响

- 新增文件：`backend/app/services/live_manager.py`、`backend/app/services/live_state_reader.py`、`backend/app/schemas/live.py`
- 修改文件：`backend/app/api/v1/live.py`（占位重写）
- 复用：`backend/app/api/error.py` 的统一错误格式
- 数据依赖：`logs/live_*_state.json` 与 `logs/live_*_log.txt`（由 live_*.py 写入）；项目根 `live_binance_quant.py` / `live_okx_quant.py` / `live_nado_quant.py`
- 环境依赖：根 `uv` 环境（含 torch / ccxt / okx / nado_protocol / dotenv）；本 task 不解决 macOS arm64 torch 装不上的问题（用户自备环境）
- 不动：`live_*.py` 代码、其他 router、前端
