## 新增需求

### 需求:prepare_crypto.py 代理环境变量支持

`prepare_crypto.py` 全局 `PROXY` 必须从 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 环境变量读取(优先级 `HTTPS_PROXY` > `HTTP_PROXY` > `ALL_PROXY`),让 requests 调用走代理。未设任何环境变量时 `PROXY` 必须为空 dict,行为与改动前完全一致(向后兼容)。

#### 场景:设了 HTTPS_PROXY
- **当** 后端启动子进程时设 `HTTPS_PROXY=http://127.0.0.1:7890`,prepare_crypto.py 运行 `requests.get(..., proxies=PROXY)`
- **那么** `PROXY = {"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"}`,requests 通过代理访问 Binance API

#### 场景:未设环境变量
- **当** 后端不设代理环境变量,prepare_crypto.py 运行
- **那么** `PROXY = {}`,requests 直连 Binance API,行为与改动前一致

### 需求:下载任务启动接口

`POST /api/v1/data-download/start` 必须接收 `{symbol, interval, days, proxy_url?, force?}` 请求体,启动 `prepare_crypto.py` 子进程下载 K 线数据。symbol 必须是字符串(非空),interval 必须是 `1m` / `5m` / `15m` / `1h` / `4h` / `1d` 之一,days 必须 1~365 整数,proxy_url 可选(若提供必须是合法 URL 格式 `http://host:port` 或 `socks5://host:port`),force 可选默认 false。

启动前必须检查:若已有下载任务处于 `running` 状态,必须返回 409 错误 `{code: "already_running", message: "已有下载任务在跑,task_id: xxx"}`(单任务串行)。启动成功必须返回 `{task_id, status: "running"}`,并立即开线程读子进程 stdout 推 WebSocket 事件。

子进程命令必须是 `uv run python prepare_crypto.py --symbol {symbol} --interval {interval} --limit {days} --force`(force 为 true 时加 `--force`),`cwd` 必须是项目根目录(`PROJECT_DIR`)。若 `proxy_url` 非空,子进程环境变量必须包含 `HTTPS_PROXY` / `HTTP_PROXY` 均设为 `proxy_url`。

#### 场景:正常启动
- **当** POST `/start {symbol: "ETHUSDT", interval: "5m", days: 7}` 且无活动任务
- **那么** 启动子进程,返回 `{task_id: "uuid", status: "running"}`,子进程开始下载,stdout 行实时推 WS

#### 场景:单任务串行
- **当** 已有任务 task_id=aaa 在跑,POST `/start {symbol: "BTCUSDT", ...}`
- **那么** 返回 409 `{error: {code: "already_running", message: "已有下载任务在跑,task_id: aaa"}}`,不启动新子进程

#### 场景:参数校验
- **当** POST `/start {symbol: "", interval: "5m", days: 7}`(symbol 为空)
- **那么** 返回 422(Pydantic 字段校验失败),不启动子进程

#### 场景:带代理 + force
- **当** POST `/start {symbol: "ETHUSDT", interval: "5m", days: 60, proxy_url: "http://127.0.0.1:7890", force: true}` 且无活动任务
- **那么** 启动子进程 `uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --limit 60 --force`,子进程环境变量 `HTTPS_PROXY=http://127.0.0.1:7890` `HTTP_PROXY=http://127.0.0.1:7890`,prepare_crypto.py 通过代理下载

### 需求:下载任务状态查询接口

`GET /api/v1/data-download/status/{task_id}` 必须返回任务详情:`{task_id, symbol, interval, days, status, started_at, completed_at, error, line_count, last_line}`。status 必须是 `running` / `completed` / `failed` / `stopped` 之一。task_id 不存在时返回 404 `{code: "not_found"}`。

#### 场景:查询活动任务
- **当** task_id=aaa 正在下载,GET `/status/aaa`
- **那么** 返回 `{task_id: "aaa", symbol: "ETHUSDT", status: "running", line_count: 15, last_line: "    +100 (累计 100)", ...}`

#### 场景:查询已完成任务
- **当** task_id=bbb 已完成,GET `/status/bbb`
- **那么** 返回 `{task_id: "bbb", status: "completed", completed_at: "...", line_count: 50, last_line: "  保存至 .../ETHUSDT_5m_7d.parquet", ...}`

#### 场景:任务不存在
- **当** GET `/status/nonexistent`
- **那么** 返回 404 `{error: {code: "not_found", message: "task not found: nonexistent"}}`

### 需求:下载任务停止接口

`POST /api/v1/data-download/stop` 必须接收 `{task_id}` 请求体,对运行中的任务发 SIGTERM 终止子进程,标记 status 为 `stopping` → `stopped`。task_id 不存在返回 404,任务已完成返回 409 `{code: "already_finished"}`。

#### 场景:正常停止
- **当** task_id=aaa 正在下载,POST `/stop {task_id: "aaa"}`
- **那么** 子进程收到 SIGTERM 退出,任务 status 变 `stopped`,WS 推 `{type: "stopped", task_id: "aaa"}`,返回 `{task_id: "aaa", status: "stopping"}`

#### 场景:任务已完成
- **当** task_id=bbb 已完成,POST `/stop {task_id: "bbb"}`
- **那么** 返回 409 `{error: {code: "already_finished", message: "task bbb is already completed"}}`

### 需求:已下载文件列表接口

`GET /api/v1/data-download/files` 必须扫描 `data/crypto/` 目录,返回所有 `*.parquet` 文件列表,每项含 `{filename, symbol, interval, days, size_bytes, mtime}`。文件名不符合 `{SYMBOL}_{INTERVAL}_{DAYS}d.parquet` 格式的跳过。按 mtime 降序排列(最新在前)。

#### 场景:列表
- **当** `data/crypto/` 下有 `ETHUSDT_5m_7d.parquet` + `BTCUSDT_15m_30d.parquet`,GET `/files`
- **那么** 返回 `{files: [{filename: "ETHUSDT_5m_7d.parquet", symbol: "ETHUSDT", interval: "5m", days: 7, size_bytes: 12345, mtime: "2026-07-04T..."}, ...], total: 2}`

#### 场景:空目录
- **当** `data/crypto/` 不存在或无 parquet 文件,GET `/files`
- **那么** 返回 `{files: [], total: 0}`

### 需求:WebSocket 进度推送

`WS /api/v1/ws/data-download/{task_id}` 必须推送 5 类事件:`started` / `progress` / `completed` / `error` / `stopped`。事件格式:
- `started`: `{type, task_id, symbol, interval, days, total_lines?: null}`
- `progress`: `{type, task_id, line, line_number, timestamp}`(line 为 prepare_crypto.py stdout 一行原文)
- `completed`: `{type, task_id, line_count, file_path}`
- `error`: `{type, task_id, code, message}`
- `stopped`: `{type, task_id}`

WS 连接时必须先发 event_buffer 中的历史事件(让 late subscriber 追进度),再订阅新事件。task_id 不存在时立即发 `{type: "error", code: "not_found"}` 并关闭。WS 必须接收 `{action: "stop"}` 消息触发停止(等效 POST /stop)。任务进入终态(completed/failed/stopped)后 WS 必须主动关闭。

#### 场景:实时进度
- **当** task_id=aaa 正在下载,WS 连接 `/ws/data-download/aaa`,子进程输出 `    +100 (累计 100)`
- **那么** WS 推 `{type: "progress", task_id: "aaa", line: "    +100 (累计 100)", line_number: 5, timestamp: "..."}`

#### 场景:late subscriber
- **当** task_id=aaa 已输出 10 行 progress,WS 才连接
- **那么** WS 先发 10 条历史 progress 事件(event_buffer),再订阅后续新事件

#### 场景:WS 触发停止
- **当** task_id=aaa 运行中,WS 发送 `{action: "stop"}`
- **那么** 后端调 request_stop,子进程退出,WS 推 `{type: "stopped", task_id: "aaa"}` 后关闭连接

#### 场景:任务不存在
- **当** WS 连接 `/ws/data-download/nonexistent`
- **那么** 立即发 `{type: "error", code: "not_found", message: "task not found: nonexistent"}` 并关闭
