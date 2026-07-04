## 上下文

`data-download-api` 已交付后端能力:4 个 REST 接口(start/status/stop/files)+ 1 个 WS 端点(/ws/data-download/{task_id}),推送 5 类事件(started/progress/completed/error/stopped)。前端需消费这些接口,提供可视化入口。

前端已有 4 个页面(Strategies/Backtest/Live/Evolve),其中 Evolve 页与数据下载页结构最相似:表单 → 启动 → WS 接进度 → 事件日志 + 最终结果。复用 Evolve 页的 WS 模式(event_buffer 重放 + late subscriber + stop 消息)。

## 目标 / 非目标

**目标：**
- 新增 `/data-download` 路由 + 侧边栏菜单项
- 表单(symbol/interval/days/proxy_url/force)触发 POST /start
- WebSocket 接 5 类事件,展示 stdout 行日志 + 运行状态 + 最终结果
- 已下载文件列表(GET /files),下载完成后刷新
- 停止按钮(WS 发 {action: "stop"})

**非目标：**
- 多任务并行(v0.1 后端单任务串行,前端只展示当前任务)
- 下载历史持久化(后端进程重启丢历史,前端不维护历史)
- 文件删除 / 重命名(只读展示)
- 文件预览 / 下载(v0.1 仅列表)
- 自定义 interval(只支持 6 个预设)

## 决策

### D1：复用 Evolve 页的 WS 模式

**选择**：DataDownload.vue 结构与 Evolve.vue 几乎一致——表单卡片 + 运行状态卡片 + 事件日志 + 最终结果 + 文件列表。WS 处理逻辑(handleWsMessage / closeWs / startXxx / stopXxx)直接照搬。

**理由**：Evolve 页已验证此模式可行(WS 连接 + 事件重放 + late subscriber + stop 消息),复用降低实现风险。

**替代方案**：抽公共 WS composable(`useWsProgress`)。拒绝：v0.1 仅 2 个页面用,过早抽象;v0.2 若新增第三个 WS 页面再抽。

### D2：WS URL 构造函数独立写

**选择**：在 `api/data-download.ts` 中写 `buildDataDownloadWsUrl(taskId)`,与 `evolve.ts` 的 `buildEvolveWsUrl` 各自独立。

**理由**：避免跨模块耦合;两个函数都很短(10 行);若后端路由变化,互不影响。

**替代方案**：抽公共 `buildWsUrl(path, id)`。拒绝：同 D1,过早抽象。

### D3：文件大小用人类可读格式

**选择**：size_bytes 用 `formatBytes()` 转 "126KB" / "1.2MB" 显示,mtime 用 `toLocaleString()` 转本地时间。

**理由**：用户看 "129453 bytes" 不直观,"126KB" 更友好。

### D4：事件日志区域用 `<pre>` 等宽字体

**选择**：同 Evolve 页,事件日志用 `<pre class="event-log">` + 等宽字体 + 自动滚到底。

**理由**：prepare_crypto.py 的 stdout 含对齐空格(如 "    +100 (累计 100)"),等宽字体保持对齐。

### D5：下载完成后自动刷新文件列表

**选择**：WS 收到 `completed` 事件后,调 `fetchFiles()` 刷新列表。

**理由**：用户期望下载完能立刻看到新文件;不刷新需手动刷新,体验差。

**替代方案**：定时轮询 /files。拒绝：浪费请求,且下载期间文件不变。

### D6：proxy_url 用 NInput 而非 NSelect

**选择**：proxy_url 用 NInput,placeholder 提示 "http://host:port 或 socks5://host:port"。

**理由**：代理 URL 格式多样(用户名密码 / 不同端口),NInput 最灵活。后端 Pydantic 校验前缀(http:// / https:// / socks5://),失败返 422,前端 message.error 提示。

### D7：force 用 NCheckbox 而非 NSwitch

**选择**：force 用 NCheckbox,label "强制重新下载(忽略已有文件)"。

**理由**：force 是低频操作,Checkbox 比 Switch 更明确表达「勾选才启用」语义。

## 风险 / 权衡

- **[风险] 后端 prepare_crypto.py 在 macOS 上因 torch 问题无法运行** → 前端会收到 error 事件(stderr 输出),message.error 提示 + 事件日志展示错误行;不影响 UI 流程验证
- **[风险] WS 连接失败(如后端未启动)** → onclose 处理:status='disconnected' + message.warning;用户可重试
- **[权衡] 不做文件下载 / 预览** → v0.1 仅列表展示;用户可在文件管理器中手动打开 data/crypto/
- **[权衡] 不做下载历史** → 进程重启丢历史任务;已下载文件从磁盘扫描,不受影响
