## 新增需求

### 需求:交易所列表接口

后端必须提供 `GET /api/v1/live/exchanges` 接口，返回支持的 3 个交易所（binance / okx / nado）的当前运行状态。响应必须返回 `exchanges` 数组与 `total` 字段，每个元素含 `name`（"binance" / "okx" / "nado"）、`status`（"running" / "stopped"）、`pid`（int 或 null）、`script`（脚本文件名）、`state_file`（state 文件名）、`log_file`（log 文件名）字段。`status` 必须实时反映内存中进程映射表的状态（已启动且进程未退出 → "running"，否则 "stopped"）。

#### 场景:首次查询
- **当** 后端启动后从未启动任何实盘进程，调 `GET /api/v1/live/exchanges`
- **那么** 返回 `{"exchanges": [{"name": "binance", "status": "stopped", "pid": null, "script": "live_binance_quant.py", "state_file": "live_binance_state.json", "log_file": "live_binance_log.txt"}, ...], "total": 3}`

#### 场景:某交易所已启动
- **当** 已调 `POST /start` 启动 binance，进程 PID 12345 仍在运行
- **那么** `GET /exchanges` 返回 binance 项 `{"name": "binance", "status": "running", "pid": 12345, ...}`

#### 场景:进程已退出但未 stop
- **当** binance 子进程因异常退出（returncode 非 0），但未调 `POST /stop`
- **那么** `GET /exchanges` 返回 binance 项 `{"name": "binance", "status": "stopped", "pid": null, ...}`（进程管理器在 status 查询时检查 `poll()`，发现已退出则清理映射）

### 需求:启动实盘接口

后端必须提供 `POST /api/v1/live/start` 接口，请求体含 `exchange`（"binance" / "okx" / "nado"）、`symbol`（如 "BTCUSDT"）、`mode`（"demo" / "live"）三个必填字段，`capital`（float，默认 100）、`leverage`（float，默认 1）两个可选字段。后端必须用 `subprocess.Popen` 拉起对应的 `live_*.py` 脚本，参数含 `--symbol` / `--demo|--live` / `--capital` / `--leverage`，cwd 设为项目根目录（让脚本写到 `logs/` 相对路径）。启动成功必须把 `Popen` 对象存入内存映射表，返回 `{"exchange": "binance", "pid": 12345, "status": "running"}`。已启动的交易所重复调 start 必须返回 409 错误 `{"error": {"code": "already_running", "message": "binance is already running, stop it first"}}`。

#### 场景:正常启动 binance demo
- **当** 客户端 POST `{"exchange": "binance", "symbol": "BTCUSDT", "mode": "demo", "capital": 100}`
- **那么** 后端 `subprocess.Popen(["uv", "run", "python", "live_binance_quant.py", "--symbol", "BTCUSDT", "--demo", "--capital", "100"], cwd=PROJECT_ROOT, env=...)`，返回 `{"exchange": "binance", "pid": <PID>, "status": "running"}`

#### 场景:已启动重复调 start
- **当** binance 已在运行，再次 POST `{"exchange": "binance", ...}`
- **那么** 返回 409 + `{"error": {"code": "already_running", "message": "binance is already running, stop it first"}}`

#### 场景:不支持的交易所
- **当** 客户端 POST `{"exchange": "huobi", ...}`
- **那么** 返回 400 + `{"error": {"code": "invalid_exchange", "message": "unsupported exchange: huobi"}}`

### 需求:停止实盘接口

后端必须提供 `POST /api/v1/live/stop` 接口，请求体含 `exchange` 字段。后端必须从映射表取出 `Popen` 对象，调 `terminate()`，等待 5 秒；若 5 秒后进程仍存活，调 `kill()` 强制终止。停止成功必须从映射表移除该 exchange，返回 `{"exchange": "binance", "status": "stopped"}`。停止未运行的交易所必须返回 404 + `{"error": {"code": "not_running", "message": "binance is not running"}}`。

#### 场景:正常停止
- **当** binance 在运行，POST `{"exchange": "binance"}`
- **那么** 后端 `proc.terminate() → wait(5)`，返回 `{"exchange": "binance", "status": "stopped"}`

#### 场景:停止未运行的
- **当** binance 未运行，POST `{"exchange": "binance"}`
- **那么** 返回 404 + `{"error": {"code": "not_running", "message": "binance is not running"}}`

#### 场景:terminate 超时强制 kill
- **当** `proc.terminate()` 后 5 秒进程仍存活
- **那么** 后端 `proc.kill()` 强制终止，仍返回 200 + `{"exchange": "binance", "status": "stopped"}`

### 需求:实盘状态查询接口

后端必须提供 `GET /api/v1/live/status/{exchange}` 接口，读 `logs/live_<exchange>_state.json` 文件返回持仓 / 未实现盈亏 / 最近成交 / 策略状态。响应必须含 `exchange`、`running`（bool，从进程映射表查）、`state`（object 或 null，state 文件不存在则为 null）、`updated_at`（state 文件的 mtime ISO 字符串，文件不存在则 null）。`state` 字段直接透传 live_*.py 写入的 JSON 内容（含 `position` / `strategy_size` / `entry_price` / `last_signal` / `bar_count` 等），后端不做字段裁剪。state 文件不存在但进程在运行时，`state` 为 null（表示进程刚启动尚未写入 state）。

#### 场景:进程运行中且有 state
- **当** binance 在运行，`logs/live_binance_state.json` 存在
- **那么** 返回 `{"exchange": "binance", "running": true, "state": {"position": 1, "strategy_size": 0.5, ...}, "updated_at": "2026-07-04T12:34:56"}`

#### 场景:进程运行中但 state 未就绪
- **当** binance 刚启动，state 文件尚未写入
- **那么** 返回 `{"exchange": "binance", "running": true, "state": null, "updated_at": null}`

#### 场景:进程未运行但有历史 state
- **当** binance 未运行，但 `logs/live_binance_state.json` 存在（上次运行残留）
- **那么** 返回 `{"exchange": "binance", "running": false, "state": {...}, "updated_at": "..."}`

#### 场景:不支持的交易所
- **当** GET `/api/v1/live/status/huobi`
- **那么** 返回 404 + `{"error": {"code": "not_found", "message": "unsupported exchange: huobi"}}`

### 需求:实盘日志查询接口

后端必须提供 `GET /api/v1/live/logs/{exchange}?tail=200` 接口，读 `logs/live_<exchange>_log.txt` 文件末尾 N 行。响应必须含 `exchange`、`lines`（数组，每行一个字符串）、`total_lines`（int，文件总行数）。`tail` 参数默认 200，最大 1000。log 文件不存在时必须返回 `{"exchange": "binance", "lines": [], "total_lines": 0}`，禁止抛错。nado 的 log 文件名带时间戳（`live_nado_log_{YYYYMMDD_HHMMSS}.txt`），后端必须取目录下最新的 `live_nado_log_*.txt` 文件。

#### 场景:正常读取日志
- **当** `logs/live_binance_log.txt` 有 500 行，调 `GET /logs/binance?tail=200`
- **那么** 返回 `{"exchange": "binance", "lines": [...200 行末尾...], "total_lines": 500}`

#### 场景:日志文件不存在
- **当** binance 从未运行过，`logs/live_binance_log.txt` 不存在
- **那么** 返回 `{"exchange": "binance", "lines": [], "total_lines": 0}`

#### 场景:nado 日志取最新
- **当** `logs/` 下含 `live_nado_log_20260704_120000.txt` 与 `live_nado_log_20260704_150000.txt`
- **那么** `GET /logs/nado` 取后者（按文件名时间戳排序最新）

#### 场景:tail 参数超限
- **当** 调 `GET /logs/binance?tail=5000`
- **那么** 后端裁剪到 1000 行，返回末尾 1000 行

### 需求:进程映射表生命周期

后端必须在内存中维护 `exchange → Popen` 映射表（进程管理器单例）。映射表的 `Popen` 对象在 `poll()` 返回非 None 时（进程已退出）必须自动从映射表移除，避免内存泄漏。后端重启时映射表为空（不持久化），意味着后端重启后所有实盘进程变孤儿——v0.1 接受这个限制，用户须手动 `pkill -f live_*_quant.py` 清理。映射表必须线程安全（用 `threading.Lock` 保护），多个并发请求访问时无竞态。

#### 场景:子进程退出后清理映射
- **当** binance 子进程因异常退出，下次调 `GET /exchanges` 或 `GET /status/binance`
- **那么** 后端 `proc.poll()` 返回非 None，从映射表移除 binance，返回 `status: "stopped"`

#### 场景:后端重启后映射表为空
- **当** 后端重启，原本运行的 binance 子进程仍在（孤儿）
- **那么** 后端 `GET /exchanges` 返回 binance `status: "stopped"`（映射表空），但实际孤儿进程仍在跑——v0.1 接受，需用户手动 `pkill` 清理
