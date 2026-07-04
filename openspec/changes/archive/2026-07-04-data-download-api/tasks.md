## 1. prepare_crypto.py 代理支持

- [x] 1.1 修改 `prepare_crypto.py` 全局 `PROXY`：从 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 环境变量读（优先级 HTTPS_PROXY > HTTP_PROXY > ALL_PROXY），未设时 `PROXY = {}`（向后兼容）
- [x] 1.2 验证：不设环境变量时 `PROXY == {}`，行为不变；设 `HTTPS_PROXY=http://127.0.0.1:7890` 时 `PROXY == {"http": "...", "https": "..."}`

## 2. 后端 schemas

- [x] 2.1 新建 `backend/app/schemas/data_download.py`：`DownloadStartRequest`（symbol/interval/days/proxy_url?/force?）、`DownloadStartResponse`（task_id/status）、`DownloadTaskStatus`（task_id/symbol/interval/days/status/started_at/completed_at/error/line_count/last_line）、`DownloadFile`（filename/symbol/interval/days/size_bytes/mtime）、`DownloadFileListResponse`（files/total）
- [x] 2.2 interval 用 `Literal["1m","5m","15m","1h","4h","1d"]`，days 用 `conint(ge=1, le=365)`，proxy_url 用 Pydantic HttpUrl 或自定义校验器限制 `http://` / `socks5://` 前缀

## 3. 后端 service

- [x] 3.1 新建 `backend/app/services/data_download_manager.py`：`DataDownloadManager` 单例 + `DownloadTask` dataclass（task_id/symbol/interval/days/proxy_url/force/status/started_at/completed_at/error/line_count/last_line/event_buffer/subscribers/cancel_event/proc）
- [x] 3.2 实现 `start(symbol, interval, days, proxy_url, force)`：单任务串行校验 → 生成 task_id → subprocess.Popen 拉起 `uv run python prepare_crypto.py --symbol X --interval Y --limit Z --force`，cwd=PROJECT_DIR，env 注入 HTTPS_PROXY/HTTP_PROXY（若 proxy_url 非空）→ 启线程读 stdout → 返回 task_id
- [x] 3.3 实现读 stdout 线程：`for line in iter(proc.stdout.readline, "")` → 更新 task.line_count/last_line → 推 `progress` 事件到 event_buffer + 广播；子进程退出时根据 returncode 推 `completed` 或 `error` 事件
- [x] 3.4 实现 `request_stop(task_id)`：设 cancel_event → `proc.terminate()`（SIGTERM）→ 等 5s → 仍存活 `proc.kill()`；标 status `stopping` → `stopped`，推 `stopped` 事件
- [x] 3.5 实现 `get_status(task_id)` / `get_active_task_id()` / `list_files()`（扫 data/crypto/*.parquet，正则解析文件名）

## 4. 后端 API

- [x] 4.1 新建 `backend/app/api/v1/data_download.py`：`POST /start`（单任务校验失败 409 already_running，参数校验失败 422）→ 启动子进程并立即开线程读 stdout → 返回 `{task_id, status: "running"}`
- [x] 4.2 `GET /status/{task_id}`：返回 `DownloadTaskStatus`；task_id 不存在 404 not_found
- [x] 4.3 `POST /stop`：接收 `{task_id}`，task_id 不存在 404，任务已完成 409 already_finished，运行中调 `request_stop` 返回 `{task_id, status: "stopping"}`
- [x] 4.4 `GET /files`：扫 `data/crypto/`，返回 `{files: [...], total}`，按 mtime 降序
- [x] 4.5 在 `backend/app/api/v1/router.py` 注册 data_download router

## 5. WebSocket 端点

- [x] 5.1 在 `backend/app/api/v1/ws.py` 新增 `WS /ws/data-download/{task_id}`：连接时先发 event_buffer 历史事件 → 订阅新事件 → 接收 `{action: "stop"}` 触发 `request_stop`；task_id 不存在立即发 `error not_found` 并关闭；任务进入终态后主动关闭

## 6. 验证

- [x] 6.1 启动 dev.sh，确认后端无报错启动
- [x] 6.2 curl 测 4 个 REST 接口（start/status/stop/files）+ 单任务串行 409 + 参数校验 422 + 404
- [x] 6.3 用 websocat 或 wscat 测 WS 端点：实时 progress + late subscriber 追历史 + stop 消息触发停止 + task 不存在 error
- [x] 6.4 实测带 proxy_url 启动子进程（即使代理不可用，确认环境变量已注入；prepare_crypto.py PROXY 已读到）
- [x] 6.5 跑 typecheck（`cd backend && uv run pyright app/`）

## 7. 归档

- [x] 7.1 提交代码（feat: add data-download-api）
- [ ] 7.2 `/opsx:archive data-download-api` 归档变更
- [ ] 7.3 合并回父分支 `version/v0.1`，追加 devlog
