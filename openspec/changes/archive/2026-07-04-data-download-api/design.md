## 上下文

v0.1 已交付回测可视化（R-v0.1-ck-4），但用户跑回测前需手动命令行执行 `uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60` 下载数据，体验割裂。中国大陆等地区访问 Binance API 需 VPN 代理，而 `prepare_crypto.py` 当前全局 `PROXY = {}` 不从环境变量读，无法走代理。

后端已有两套子进程管理模式可借鉴：
- `evolution_manager.py`：subprocess + 线程读 stdout + `event_buffer`（cap 500）+ `subscribers` 列表 + `asyncio.run_coroutine_threadsafe` 跨 loop 推 WS + `cancel_event` 协作式停止
- `live_manager.py`：subprocess.Popen + cwd=PROJECT_DIR + 死活检测

本变更合并两者模式：复用 evolution 的「单任务串行 + event_buffer + WS 广播」骨架，复用 live 的「subprocess.Popen + cwd + SIGTERM」骨架。

## 目标 / 非目标

**目标：**
- 后端通过 `subprocess.Popen` 拉起根目录 `prepare_crypto.py`，传 `--symbol --interval --limit --force`
- 单任务串行：同时只允许一个下载任务在跑，第二个请求返回 409 `already_running`
- 子进程 stdout 行实时推 WebSocket（5 类事件：started/progress/completed/error/stopped）
- late subscriber 通过 `event_buffer` 追历史进度
- 前端传 `proxy_url` → 后端设 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量 → 小改 `prepare_crypto.py` 让 `PROXY` 从环境变量读（不设时行为不变，向后兼容）
- 提供 4 个 REST 接口（start/status/stop/files）+ 1 个 WS 端点

**非目标：**
- 多任务并行下载（v0.1 单任务串行足够）
- 下载历史持久化（v0.1 仅内存，进程重启丢历史；文件列表从磁盘扫描）
- 断点续传（prepare_crypto.py 本身不支持）
- 代理认证（用户名/密码）—— v0.1 仅支持 `http://host:port` / `socks5://host:port`
- 前端 UI（由下一个 task `data-download-ui` 处理）

## 决策

### D1：复用 EvolutionManager 模式而非重新设计

**选择**：DataDownloadManager 单例，结构与 EvolutionManager 几乎一致（`DownloadTask` dataclass + `event_buffer` + `subscribers` + `_loop` + `cancel_event` + threading.Lock）。

**理由**：evolution 已验证此模式可行（单任务串行 + WS 广播 + 协作式停止），复用降低实现风险与维护成本。

**替代方案**：基于 asyncio.Task + asyncio.Queue 重写。拒绝：evolution 用线程读 subprocess stdout（同步 IO），改 asyncio 需要 asyncio.create_subprocess_exec，但 evolution 已有线程模式工作良好，统一风格更好维护。

### D2：单任务串行用「全局 active_task_id」而非队列

**选择**：`DataDownloadManager` 维护 `self._active_task_id: str | None`。start 时若已非 None，直接返回 409。不排队。

**理由**：v0.1 单机本地，用户手动触发，排队会让用户困惑（以为成功了但实际等很久）。直接拒绝更明确。

**替代方案**：FIFO 队列。拒绝：v0.1 不需要，增加复杂度。

### D3：proxy_url 在后端转环境变量传给子进程

**选择**：后端 start 接口接收 `proxy_url`，在 `subprocess.Popen(env=...)` 中设 `HTTPS_PROXY` / `HTTP_PROXY` 均为 `proxy_url`。prepare_crypto.py 改 3 行从环境变量读 `PROXY`。

**理由**：最小改动。prepare_crypto.py 已用 `requests.get(..., proxies=PROXY)`，只要 `PROXY` 从环境变量读即可，不改其余逻辑。

**替代方案 1**：给 prepare_crypto.py 加 `--proxy` CLI 参数。拒绝：需改 argparse + 透传，比环境变量多 3-5 行，且环境变量是 Python requests 社区惯例（curl 也用）。

**替代方案 2**：后端不动 prepare_crypto.py，自己用 requests 重新实现下载。拒绝：prepare_crypto.py 含技术指标计算、parquet 落盘、force 重下逻辑等，重写易出 bug。

### D4：PROXY 优先级 HTTPS_PROXY > HTTP_PROXY > ALL_PROXY

**选择**：
```python
proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
PROXY = {"http": proxy_url, "https": proxy_url} if proxy_url else {}
```

**理由**：HTTPS_PROXY 是访问 Binance（HTTPS）最相关的变量；ALL_PROXY 是 socks 类代理的兜底。优先级与 curl/requests 社区惯例一致。

**替代方案**：仅读 HTTPS_PROXY。拒绝：用户可能只设了 ALL_PROXY（socks5 代理），漏读会无法走代理。

### D5：WS 端点路径 `/ws/data-download/{task_id}`

**选择**：与 `/ws/evolve/{run_id}` 同级，挂在 `ws.py` 中。

**理由**：路由聚合，前端统一 `VITE_WS_BASE_URL`。

**替代方案**：新建 `ws_data_download.py`。拒绝：当前 ws.py 仅 1 个端点，加 1 个仍可控，过早拆分。

### D6：files 接口从磁盘扫描而非维护内存索引

**选择**：`GET /files` 实时扫 `data/crypto/*.parquet`，用正则 `^(\w+)_(1m|5m|15m|1h|4h|1d)_(\d+)d\.parquet$` 解析文件名得到 symbol/interval/days。

**理由**：用户可能手动删文件或外部下载新文件，磁盘扫描保证一致性。文件数预期 <100，扫描成本可忽略。

**替代方案**：维护内存 `downloaded_files: list`。拒绝：与磁盘可能不同步，且 prepare_crypto.py 落盘后需手动通知 manager 刷新，耦合更紧。

### D7：stop 用 SIGTERM 而非 SIGKILL

**选择**：`subprocess.Popen.terminate()`（SIGTERM）→ 等 5s → 若仍存活 `kill()`（SIGKILL）。

**理由**：给 prepare_crypto.py 一个干净退出机会（刷新 stdout、关 file handle）。SIGKILL 直接终止可能留下半写 parquet。

**替代方案**：直接 SIGKILL。拒绝：parquet 半写风险。

### D8：tasks 状态机 running → completed/failed/stopped

**选择**：
- `running`：子进程存活
- `completed`：子进程 returncode == 0
- `failed`：子进程 returncode != 0
- `stopping`：收到 stop 请求，已发 SIGTERM，等子进程退出
- `stopped`：子进程退出后最终状态

**理由**：覆盖所有终态。`stopping` 中间态让前端可显示「停止中」。

## 风险 / 权衡

- **[风险] subprocess stdout 行缓冲可能延迟** → prepare_crypto.py 已用 `print(..., flush=True)`；后端读 stdout 用 `for line in iter(proc.stdout.readline, "")`，行级实时。
- **[风险] event_buffer 无上限可能内存爆炸** → cap 500（同 evolution），超过丢弃最早的。下载任务预期 <200 行 progress，不会触上限。
- **[风险] WS 连接断开后子进程仍在跑** → 不影响下载，仅丢后续 progress；用户重连可看 event_buffer 追进度。
- **[风险] SIGTERM 后 prepare_crypto.py 不退出** → 5s 后 SIGKILL 兜底；标记 `stopped` 而非 `failed`（用户主动停的）。
- **[风险] 用户传了非法 proxy_url（如 `ftp://`）导致 requests 报错** → 后端用 Pydantic 校验 `http://` 或 `socks5://` 前缀，否则 422；不合法的代理在 prepare_crypto.py 内部 requests 报错时由后端读 stderr 转 `error` 事件推 WS。
- **[权衡] 单任务串行可能让用户觉得受限** → v0.1 接受；多任务并行留 v0.2。
- **[权衡] 不持久化任务历史** → 进程重启丢正在跑的任务（子进程也跟着死）；已下载文件不受影响（落盘了）。v0.1 接受。
