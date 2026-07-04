## 上下文

3 个 `live_*.py` 脚本（binance / okx / nado）是独立 CLI，写两种文件：
- `logs/live_<exchange>_state.json` — 实盘状态（position / strategy_size / entry_price / last_signal / bar_count 等），脚本每次循环更新
- `logs/live_<exchange>_log.txt` — 日志（`[YYYY-MM-DD HH:MM:SS] msg` 行），append 模式（nado 例外，带时间戳每次新文件）

脚本用 `fcntl.flock` 文件锁防止多实例，未提供健康检查端口。后端无法主动知道进程是否健康，只能 `Popen.poll()` 检查是否退出 + 读 state 文件 mtime 判断活跃度。

后端在 `backend/` 目录跑，脚本在项目根跑（写 `logs/` 相对路径），需 `cwd=PROJECT_ROOT`。脚本用根 `uv` 环境（torch / ccxt / okx / nado_protocol），与 backend 环境隔离。

R-v0.1-ck-5 要求「实时日志」，但 v0.1 单机个人用 + 日志 append 模式，HTTP 轮询（2s 间隔）足够「实时」，比 WebSocket 简单。WebSocket 留给 evolution-api（有真正的流式进度）。

## 目标 / 非目标

**目标：**
- 5 个接口：exchanges / start / stop / status / logs
- 进程管理器单例（exchange → Popen 内存映射，线程安全）
- 状态查询读 state JSON 文件
- 日志查询读 log txt 文件末尾 N 行
- 统一错误格式（already_running / not_running / invalid_exchange / not_found）
- 子进程退出后自动清理映射

**非目标：**
- 不实现 WebSocket 推送（轮询够用，留给 v0.2）
- 不持久化进程映射（后端重启孤儿进程留给用户手动清理，v0.1 接受）
- 不实现孤儿进程检测（不扫 lock 文件 / 不扫 ps，v0.1 简单优先）
- 不实现策略参数前端编辑（v0.1 用脚本默认参数）
- 不实现多 symbol 同交易所（binance 只能跑一个 symbol，脚本文件锁限制）
- 不动 `live_*.py` 代码（用现有 CLI）
- 不做认证（单机本地，R-v0.1-ck-7）

## 决策

### 决策 1：进程映射用内存单例，不持久化
- **选择**：`LiveManager` 单例，`_processes: dict[str, Popen]` + `threading.Lock`
- **替代方案 A**：写 `logs/live_manager.json` 持久化 PID，重启后回捞
- **替代方案 B**：用 SQLite 存 PID
- **理由**：v0.1 单机个人用，后端重启极少；持久化回捞需处理「PID 被复用成别的进程」竞态，复杂；用户手动 `pkill` 清理孤儿可接受。简单优先。

### 决策 2：日志用 HTTP 轮询，不用 WebSocket
- **选择**：`GET /logs/{exchange}?tail=200`，前端 2s 轮询
- **替代方案**：WebSocket 推送新行
- **理由**：脚本日志 append 模式，无中间进度概念；轮询实现简单（一个 HTTP 接口），前端 `setInterval` 即可；WebSocket 需管理连接生命周期 + 心跳 + 断线重连。单机低并发，轮询开销可忽略。WebSocket 留给 evolution-api（真正流式进度）。

### 决策 3：state 字段透传，不裁剪
- **选择**：`state` 字段直接返回 live_*.py 写入的 JSON 内容（dict 透传）
- **替代方案**：定义 Pydantic 模型裁剪成 `{position, strategy_size, entry_price, last_signal, bar_count}` 5 字段
- **理由**：3 个脚本 state 字段不一致（binance 含 `pending_open` 等，nado 字段更少）；透传避免后端跟脚本字段变化同步；前端按需读字段，未定义字段忽略。Pydantic 模型用 `dict[str, Any]` 接收。

### 决策 4：nado 日志取最新文件
- **选择**：`logs/live_nado_log_*.txt` glob，按文件名（含时间戳）降序取第一个
- **替代方案**：读所有 nado 日志合并
- **理由**：nado 每次启动新日志文件，最新即当前运行；合并旧日志无意义且体积大。文件名时间戳格式 `YYYYMMDD_HHMMSS` 字典序即时间序。

### 决策 5：start 用 `uv run python`，不直接调 python
- **选择**：`subprocess.Popen(["uv", "run", "python", "live_<exchange>_quant.py", ...], cwd=PROJECT_ROOT)`
- **替代方案**：直接调 `python` / `python3`
- **理由**：脚本依赖根 `uv` 环境（torch / ccxt 等），`uv run` 自动激活环境；直接 python 会缺依赖。根 `pyproject.toml` 已配置好。

### 决策 6：start 不等脚本输出，fire-and-forget
- **选择**：`Popen` 后立即返回 200，不等待脚本启动日志
- **替代方案**：等 1~2 秒看脚本是否立刻退出（import 失败等）
- **理由**：脚本启动到第一行日志可能数秒（import torch 慢）；等待会拖慢 API 响应；让前端通过 `/status` 与 `/logs` 轮询发现启动失败。`Popen` 失败（如 `uv` 命令不存在）会立刻抛 `FileNotFoundError`，转 500。

### 决策 7：stop 用 terminate + 5s 超时 + kill 兜底
- **选择**：`proc.terminate() → proc.wait(timeout=5)`，超时 `proc.kill() → proc.wait()`
- **替代方案 A**：直接 `proc.kill()` 不给优雅退出
- **替代方案 B**：发 SIGINT（Ctrl+C）让脚本 graceful shutdown
- **理由**：terminate 发 SIGTERM，脚本可捕获退出（保存 state）；5 秒足够脚本清理；超时 kill 兜底防止僵尸。SIGINT 需脚本实现 signal handler，现有脚本未必有。

### 决策 8：状态文件不存在时 state=null，不报错
- **选择**：state 文件不存在 → `state: null, updated_at: null`，进程运行时表示「刚启动未写入」
- **替代方案**：返回 404 或 409
- **理由**：脚本启动后可能数分钟才写第一次 state（等第一个 5m bar 完成）；这期间不算错误，前端显示「等待数据」即可。

## 风险 / 权衡

- **[根环境 torch 装不上]** → macOS arm64 上 `torch==2.6.0+cu124` 无 wheel，`uv run python live_*.py` 会失败。缓解：API 设计上 start 返回 200（Popen 成功），子进程立刻退出 → 前端 `/status` 看到 `running: false` + `/logs` 看到 ImportError 报错。用户自备环境（Linux / WSL / 修复 pyproject.toml）。
- **[后端重启孤儿进程]** → 后端重启后映射表空，但子进程仍在跑。缓解：v0.1 接受，devlog 提示用户 `pkill -f live_*_quant.py` 清理；v0.2 加 PID 持久化 + 启动时扫描回捞。
- **[PID 复用]** → 后端重启后旧 PID 被新进程复用，若做持久化回捞会误杀。缓解：v0.1 不持久化，无此风险。
- **[子进程 stdout/stderr 未重定向]** → 若不重定向，stdout 会写到 backend 的 uvicorn 日志，混乱。缓解：`Popen(stdout=DEVNULL, stderr=DEVNULL)` 或重定向到 log 文件；脚本自己写 LOG_FILE，stdout 不重定向也行（脚本 log_message 同时 print + 写文件）。
- **[并发 start 同一交易所]** → 两个请求同时 start binance。缓解：`threading.Lock` 保护 `_processes` 字典，第一个请求加锁启动，第二个请求看到已存在返回 409。
- **[stop 时进程已退出]** → `proc.terminate()` 对已退出进程抛 ProcessLookupError？实际上 `Popen.terminate()` 对已退出进程是 no-op，不抛错。缓解：try/except 兜底，仍返回 200。
- **[log 文件被 rotate]** → nado 每次启动新 log 文件，旧文件保留。缓解：取最新文件即可；v0.2 加日志清理。

## 迁移计划

- 新增 `backend/app/services/live_manager.py` + `backend/app/services/live_state_reader.py` + `backend/app/schemas/live.py`
- 改造 `backend/app/api/v1/live.py` 替换占位
- 启动后端，curl 验证 5 个接口
- 验证 start/stop 用真实 live_*.py：因 macOS torch 装不上，子进程会立刻退出，验证 API 行为正确（start 返 200、status 显示 stopped、logs 含 ImportError）
- 验证 status/logs 读文件：手动写 fake `logs/live_binance_state.json` 与 `logs/live_binance_log.txt` 测试
- 回滚：`git checkout backend/app/api/v1/live.py` 恢复占位 + 删新增文件

## 待解决问题

- 子进程 stderr 是否单独捕获到 `logs/live_<exchange>_stderr.txt`？v0.1 不做，stderr 混在 stdout 里（脚本 log_message 同时 print + 写 LOG_FILE，错误堆栈走 stderr → 默认继承父进程 stderr → uvicorn 日志）
- 是否需要 `POST /restart` 接口？v0.1 不做，前端调 stop + start 即可
