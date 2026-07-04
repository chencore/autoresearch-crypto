## 上下文

`evolution-api` 已完成后端 4 个 REST 接口 + WebSocket 端点:
- `POST /api/v1/evolve/start` → `{run_id, status}`
- `GET /api/v1/evolve/runs` → run 列表
- `GET /api/v1/evolve/runs/{run_id}` → run 详情
- `POST /api/v1/evolve/stop` → `{run_id, status:"stopping"}`
- `WS /api/v1/ws/evolve/{run_id}` → 推送 7 类事件(started / generation / cycle / meta_reflection / completed / stopped / error)

前端 axios 客户端 `client.ts` 已配拦截器:`response.data` 直接返回、错误转 `{code, message}` reject。Backtest.vue 已建立 echarts use 模式(`use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])`)+ `fetchSymbolList` + 表单 / 结果区布局。App.vue 已包 NMessageProvider。

vite proxy 已配 `/api → 127.0.0.1:8000` 含 `ws: true`,前端通过 5173 端口访问 WebSocket。生产构建时前端与后端同源(由后端 serve 静态文件),WebSocket URL 直接拼当前 origin。

进化是短跑(几秒到几分钟),WebSocket 一连接到完成,不需要重连机制。后端 event_buffer 让 late subscriber 追进度,但 v0.1 前端是 start 后立即连 WS,不会 late。

## 目标 / 非目标

**目标:**
- 配置表单(引擎 / symbol / generations / evolution_interval)
- WebSocket 接收 7 类事件,实时更新 UI
- 进度条 + status tag + run_id 展示
- agents 表(NDataTable,4 行)
- 进化曲线(echarts line chart,4 条线随事件 append)
- 事件日志(`<pre>` 滚动,cap 200,自动滚到底)
- 最终结果卡(atlas: best_agent + final_weights;gepa: experiment_count + blind_spots)
- 停止按钮(WS 发 `{action:"stop"}`)
- onUnmounted 关闭 WS

**非目标:**
- 不实现 WS 断线重连(v0.1 短跑,用户重启)
- 不实现 run 历史列表(用户在当前会话只看当前 run,v0.2 加)
- 不实现参数导出 / 应用到实盘(v0.2 加)
- 不实现多 run 并发展示(v0.1 一次只跑一个)
- 不动后端、其他前端页、router

## 决策

### 决策 1:WebSocket URL 派生
- **选择**:从 `import.meta.env.VITE_API_BASE_URL || '/api/v1'` 取基础路径,replace `http://` → `ws://` / `https://` → `wss://`(若为相对路径用当前 `window.location`),拼接 `/evolve/{run_id}`
- **替代方案 A**:硬编码 `ws://localhost:8000/api/v1/ws/evolve/{run_id}`
- **替代方案 B**:用 `socket.io-client` 库
- **理由**:硬编码不适应生产部署(前端由后端 serve);socket.io 引入额外依赖 + 后端需配 socket.io(当前用原生 WebSocket);URL 派生法适配 dev(5173 → vite proxy → 8000)+ 生产(同源)。

### 决策 2:agents 表 + 曲线 + 日志三区布局
- **选择**:上部表单 + 中部进度 + 下部 NGrid `cols=2`(左 agents 表 / 右上曲线 / 右下日志)
- **替代方案 A**:Tab 切换(agents / 曲线 / 日志)
- **替代方案 B**:垂直堆叠(agents / 曲线 / 日志 全宽)
- **理由**:进化是动态过程,用户想同时看 agents 表 + 曲线 + 日志;Tab 切换会错过信息;垂直堆叠在宽屏浪费空间。NGrid 2 列在 1280px 宽屏舒适,响应式 `xs=1`。

### 决策 3:进化曲线用 echarts line chart,4 条线按 agent name 索引
- **选择**:每事件 append 数据点到对应 series,echarts `setOption` 替换 series data
- **替代方案 A**:每次事件全量重绘
- **替代方案 B**:用 NDataTable 展示 score 矩阵(agent × generation)
- **理由**:曲线是进化趋势最直观展示;echarts `setOption` merge 模式只更新数据不重绘轴;矩阵表不直观。4 条线用不同颜色区分,echarts 默认 theme 已配色。

### 决策 4:事件日志用 `<pre>` 不用 NCode / NLog
- **选择**:`<pre class="event-log">` + 等宽字体 + 滚动 + cap 200
- **替代方案 A**:NCode(naive-ui 代码块)
- **替代方案 B**:NLog(naive-ui 日志组件)
- **理由**:NCode 会对每行加行号 + 语法高亮(不必要);NLog 是 v2.x 后新组件,版本兼容性不确定;`<pre>` 简单可控,与 Live.vue 日志区风格一致。

### 决策 5:停止按钮走 WS 不走 REST
- **选择**:WS 发 `{action:"stop"}` 消息(后端 WS 协程已实现接收)
- **替代方案**:POST `/evolve/stop`
- **理由**:WS 已连接,省一次 HTTP 请求;后端 WS 协程收到 stop 立即调 `request_stop`,与 REST 等效;v0.1 简化。

### 决策 6:WS 连接错误不弹 message
- **选择**:`ws.onerror = () => console.warn(...)`,不弹 NMessage
- **替代方案**:每次 onerror 弹 error message
- **理由**:后端通过 `error` 事件通知业务错误(如 data_not_found),WS 层错误(如网络断开)在 onclose 中处理更合适;onerror 弹 message 会与 error 事件重复。

### 决策 7:最终结果卡在 completed 事件后展示
- **选择**:接收 completed 事件 → 设置 `finalResult` ref → 渲染 NCard
- **替代方案 A**:调 `GET /runs/{run_id}` 拉详情
- **替代方案 B**:启动时就预留位置,completed 后填充
- **理由**:completed 事件已含全部最终数据(best_agent + final_weights / experiment_count + blind_spots),无需额外请求;预留位置会在跑的过程中显示空 NCard 体验差。

### 决策 8:进度条用 NProgress 不是 NStatistic
- **选择**:`<NProgress type="line" :percentage="gen/total*100" :indicator-placement="'inside'">`
- **替代方案**:NStatistic 显示 `3/10`
- **理由**:进度条视觉直观,百分比 + 数字 inside 一目了然;NStatistic 只显示数字无视觉进度。NProgress 是 naive-ui 内置组件,无新增依赖。

## 风险 / 权衡

- **[WS URL 在生产部署可能不对]** → 生产构建时 `VITE_API_BASE_URL` 未设,前端用相对路径 `/api/v1`,WS URL 派生为 `ws://host/api/v1/ws/evolve/{run_id}`(基于 `window.location`),需后端 serve 静态文件 + 反代 WebSocket。缓解:v0.1 dev 模式用 vite proxy,生产留给 integration-launch-script task 处理。
- **[曲线数据点过多卡顿]** → 100 代 × 4 agent = 400 点,echarts 完全可承受。缓解:无 v0.1 优化需求。
- **[事件日志 cap 200 丢历史]** → 用户离开页面再回来,日志清空。缓解:v0.1 接受,用户在当前会话看即可;v0.2 加持久化。
- **[WS 异常断开 status 不准]** → 后端 run 实际还在跑,前端显示 disconnected。缓解:v0.1 接受,用户重新启动 run;v0.2 加恢复机制。
- **[gepa agents 表 score 显示 `—`]** → gepa 后端不维护 score_history,前端按 score=0.0 显示 `—`。缓解:文档说明,gepa 关注 score_before/after 在事件日志中展示。
- **[echarts 在 Vue 3.5 + naive-ui 主题冲突]** → echarts 默认主题色与 naive-ui 暗色主题可能不协调。缓解:v0.1 用 echarts 默认主题,文档提示暗色背景下曲线颜色可能不亮;v0.2 加主题适配。

## 迁移计划

- 新增 `frontend/src/api/evolve.ts`:4 个 REST 函数 + WS URL 帮助 + 7 个事件类型 + 4 个 REST 类型
- 重写 `frontend/src/pages/Evolve.vue`:表单 + 进度 + 三区(agents / 曲线 / 日志)+ 最终结果 + WS 生命周期
- typecheck:`cd frontend && pnpm typecheck`
- dev server:`cd frontend && pnpm dev`,浏览器目视验证(手动)
- 联通验证:启动后端 + pnpm dev + 浏览器 `/evolve` → 选 atlas + ETHUSDT 5m 7d + 10 代 → 启动 → 看 WS 推进度 + 曲线 + 日志 + 最终结果
- 回滚:`git checkout frontend/src/pages/Evolve.vue` 恢复占位 + 删 `api/evolve.ts`

## 待解决问题

- 是否需要 run 历史列表?v0.1 不做,用户在当前会话只看当前 run
- 是否需要参数导出?v0.2 加(导出 best_agent.params 为 JSON)
- 是否需要多 run 并发?v0.1 不做,一次一个
- 是否需要 echarts 暗色主题?v0.2 加(根据 naive-ui theme 调整)
