## 新增需求

### 需求:数据下载页路由与导航

`/data-download` 路由必须渲染 `DataDownload.vue` 页面。侧边栏菜单必须在「参数调优」下方新增「数据下载」项,key 为 `/data-download`。未登录直接访问 `/data-download` 必须正常渲染(v0.1 无鉴权)。

#### 场景:从侧边栏进入
- **当** 用户在侧边栏点击「数据下载」
- **那么** 路由切换到 `/data-download`,渲染数据下载页

#### 场景:直接访问 URL
- **当** 用户浏览器直接打开 `http://localhost:5173/data-download`
- **那么** 直接渲染数据下载页(无重定向)

### 需求:下载表单

数据下载页必须展示表单,含 5 个字段:
- symbol(NInput,必填,placeholder "如 ETHUSDT")
- interval(NSelect,选项 1m/5m/15m/1h/4h/1d,默认 5m)
- days(NInputNumber,1~365,默认 60)
- proxy_url(NInput,可选,placeholder "http://127.0.0.1:7890 或 socks5://...")
- force(NCheckbox,默认不勾)

表单下方必须有「启动下载」按钮(主色)和「停止」按钮(错误色,仅运行中可点)。启动按钮在表单未填 symbol 或任务运行中时禁用。点击「启动下载」必须调 `POST /api/v1/data-download/start`,请求体 `{symbol, interval, days, proxy_url?, force}`。

#### 场景:正常启动
- **当** 用户填 symbol=ETHUSDT / interval=5m / days=7,点击「启动下载」
- **那么** 按钮 loading,POST /start,成功后开 WS 连接,展示「运行状态」卡片

#### 场景:参数校验失败
- **当** 用户不填 symbol,点击「启动下载」
- **那么** 按钮禁用,无法点击

#### 场景:已有任务在跑
- **当** 已有任务运行中,用户再点「启动下载」
- **那么** 后端返 409 already_running,前端 message.error 提示「已有下载任务在跑」

### 需求:WebSocket 进度展示

启动成功后必须开 WebSocket 连接 `ws://<host>/api/v1/ws/data-download/{task_id}`,接收 5 类事件:
- `started`:展示「运行状态」卡片,status=运行中
- `progress`:把 `line` 追加到「事件日志」区域,行号递增
- `completed`:status=已完成,message.success 提示,关闭 WS,刷新文件列表
- `error`:status=失败,message.error 提示,关闭 WS
- `stopped`:status=已停止,关闭 WS

WS 连接断开时若 status 仍为运行中,必须提示「WebSocket 连接断开」。事件日志区域必须自动滚动到底部。

#### 场景:实时进度
- **当** 任务运行中,WS 收到 `{type: "progress", line: "    +100 (累计 100)", line_number: 5}`
- **那么** 「事件日志」追加 `[5]    +100 (累计 100)`,自动滚到底

#### 场景:下载完成
- **当** WS 收到 `{type: "completed", file_path: ".../ETHUSDT_5m_7d.parquet"}`
- **那么** status=已完成,「最终结果」卡片展示 file_path,message.success 通知,「已下载文件」列表刷新

#### 场景:late subscriber
- **当** 用户刷新页面后重连 WS,任务已输出 10 行 progress
- **那么** WS 先发 10 条历史 progress,前端展示全部

### 需求:已下载文件列表

页面下方必须有「已下载文件」卡片,页面挂载时调 `GET /api/v1/data-download/files` 获取列表,展示表格:filename / symbol / interval / days / size_bytes(人类可读) / mtime(本地时间)。下载完成或停止后必须刷新列表。

#### 场景:列表展示
- **当** data/crypto/ 下有 ETHUSDT_5m_7d.parquet
- **那么** 表格展示一行:ETHUSDT_5m_7d.parquet / ETHUSDT / 5m / 7 / 126KB / 2026-07-04 08:35:37

#### 场景:空目录
- **当** data/crypto/ 无 parquet 文件
- **那么** 表格展示 NEmpty「暂无文件」

### 需求:停止下载

运行中必须可点「停止」按钮,点击后通过 WS 发送 `{action: "stop"}` 消息(等效 POST /stop)。停止后 status=已停止,WS 关闭,刷新文件列表。

#### 场景:正常停止
- **当** 任务运行中,用户点击「停止」
- **那么** WS 发 {action: "stop"},后端 SIGTERM 子进程,WS 推 {type: "stopped"},前端 status=已停止
