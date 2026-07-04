## 1. API 客户端

- [x] 1.1 创建 `frontend/src/api/data-download.ts`,定义事件类型接口(StartedEvent / ProgressEvent / CompletedEvent / ErrorEvent / StoppedEvent)与下载文件 / 状态响应接口
- [x] 1.2 在 `data-download.ts` 实现 `startDownload` / `getDownloadStatus` / `stopDownload` / `listFiles` 四个 REST 调用函数(走 axios client)
- [x] 1.3 在 `data-download.ts` 实现 `buildDataDownloadWsUrl(taskId)`,**必须包含 `/ws/` 前缀**(同 evolve.ts 已修复版本)

## 2. 页面组件

- [x] 2.1 创建 `frontend/src/pages/DataDownload.vue`,搭出骨架:表单卡片 + 运行状态卡片 + 事件日志 + 最终结果 + 已下载文件卡片
- [x] 2.2 实现下载表单(symbol NInput / interval NSelect 6 项 / days NInputNumber 1-365 / proxy_url NInput / force NCheckbox),「启动下载」按钮在 symbol 空或运行中时禁用
- [x] 2.3 实现启动逻辑:点击「启动下载」调 `startDownload`,成功后开 WS 连接,展示运行状态卡片
- [x] 2.4 实现 WS 事件处理:started→运行中 / progress→追加日志行并自动滚到底 / completed→已完成+刷新文件列表+message.success / error→失败+message.error / stopped→已停止,终态关闭 WS
- [x] 2.5 实现 WS 断开处理:断开时 status 仍为运行中则提示「WebSocket 连接断开」
- [x] 2.6 实现停止按钮:运行中可点,点击发 `{action: "stop"}`,停止后刷新文件列表
- [x] 2.7 实现「已下载文件」表格:挂载时调 `listFiles`,展示 filename / symbol / interval / days / size(人类可读) / mtime(本地时间),空目录展示 NEmpty
- [x] 2.8 实现 `formatBytes()` 工具函数,把 size_bytes 转成 "126KB" / "1.2MB" 等可读格式

## 3. 路由与导航

- [x] 3.1 在 `frontend/src/router/index.ts` 新增 `/data-download` 路由,指向 `DataDownload.vue`
- [x] 3.2 在 `frontend/src/App.vue` 侧边栏菜单「参数调优」下方新增「数据下载」项,key 为 `/data-download`

## 4. 验证

- [x] 4.1 启动后端 + 前端 dev server,浏览器访问 `/data-download` 渲染正常
- [x] 4.2 表单触发启动,WS 接 progress,事件日志实时滚动
- [x] 4.3 终态(completed/error/stopped)后 WS 关闭、文件列表自动刷新
- [x] 4.4 停止按钮可中断任务(复用 Evolve.vue 已验证模式,后端 stop 端点已在 data-download-api 任务中验证)
