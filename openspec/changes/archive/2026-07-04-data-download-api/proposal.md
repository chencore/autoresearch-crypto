## 为什么

v0.1 已交付回测可视化(R-v0.1-ck-4),但用户跑回测前必须手动在命令行执行 `uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60` 下载数据,体验割裂。在中国大陆等地区访问 Binance API 需要 VPN 代理,`prepare_crypto.py` 当前全局 `PROXY = {}` 不从环境变量读,无法走代理。本变更新增后端「数据下载」能力:复用 `prepare_crypto.py` 子进程,WebSocket 推 stdout 行进度,前端表单传 proxy_url → 后端设 HTTPS_PROXY 环境变量 → 小改 prepare_crypto.py 让代理生效。

## 变更内容

- **修改** 根目录 `prepare_crypto.py`:把全局 `PROXY = {}` 改为从 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 环境变量读(不设环境变量时行为不变,向后兼容)
- **新增** `backend/app/schemas/data_download.py`:请求/响应 Pydantic 模型(DownloadStartRequest / DownloadStartResponse / DownloadTaskStatus / DownloadFile / DownloadFileListResponse)
- **新增** `backend/app/services/data_download_manager.py`:DataDownloadManager 单例,管理下载任务(单任务串行 + subprocess.Popen + 线程读 stdout + 跨 loop WS 推事件 + cancel_event 协作式停止)
- **新增** `backend/app/api/v1/data_download.py`:3 个 REST 接口(POST /start / GET /status/{task_id} / POST /stop + GET /files 列已下载文件)
- **修改** `backend/app/api/v1/ws.py`:新增 `/ws/data-download/{task_id}` WebSocket 端点(复用 evolve ws 模式:event_buffer + subscribe + 收 stop 消息)
- **修改** `backend/app/api/v1/router.py`:注册 data_download router

## 功能 (Capabilities)

### 新增功能

- `data-download-api`: 后端复用 `prepare_crypto.py` 子进程下载 Binance K 线,WebSocket 推 stdout 行进度,支持 VPN 代理(HTTPS_PROXY 环境变量),单任务串行

### 修改功能

无。

## 影响

- 受影响代码:`prepare_crypto.py`(根目录,小改 3 行)、`backend/app/` 新增 3 个文件 + 修改 2 个文件(ws.py / router.py)
- 受影响依赖:无新增(prepare_crypto.py 已用 requests + pyarrow;后端用 subprocess + threading,均为标准库)
- 受影响 API:新增 4 个 REST 接口 + 1 个 WebSocket 端点
- 受影响部署:无
- 受影响文档:无(README 在后续 data-download-ui task 中更新)
