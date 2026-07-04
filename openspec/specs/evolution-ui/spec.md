## 新增需求

### 需求:进化配置表单

`Evolve.vue` 加载时必须调 `fetchSymbolList()`(复用 `/backtest/symbols`)填充 symbol 下拉,引擎下拉固定为 `[{label:'ATLAS 多策略进化', value:'atlas'}, {label:'GEPA 反思式进化', value:'gepa'}]`。表单字段:引擎(必选,默认 atlas)、symbol(必选,从列表选,值为 `${symbol}|${interval}|${days}` 拼接)、generations(NInputNumber 默认 10 min 1 max 100 step 1)、evolution_interval(NInputNumber 默认 5 min 1 max 20 step 1,仅 engine=atlas 时显示,gepa 时隐藏)。「启动」按钮在引擎 + symbol 未选时 disabled。「停止」按钮在无活动 run 时 disabled。点击「启动」必须调 `POST /evolve/start`,成功后保存 `run_id` + 打开 WebSocket 连接。

#### 场景:首次加载
- **当** 用户进入 `/evolve` 页
- **那么** 调 `fetchSymbolList` 填充 symbol 下拉(如 `ETHUSDT 5m 7d`),引擎默认 atlas,generations 默认 10,evolution_interval 默认 5,「启动」disabled(symbol 未选)

#### 场景:切换引擎
- **当** 用户从 atlas 切到 gepa
- **那么** evolution_interval 字段隐藏(form 中不显示),表单高度自适应

#### 场景:symbol 选中后启动
- **当** 用户选 `ETHUSDT 5m 7d` + 引擎 gepa + generations 15 → 点「启动」
- **那么** POST `/evolve/start {engine:'gepa', symbol:'ETHUSDT', interval:'5m', days:7, generations:15}` → 收到 `{run_id, status:'running'}` → 保存 run_id → 打开 WS `ws://host/api/v1/ws/evolve/<run_id>` → 「启动」disabled,「停止」enabled

### 需求:WebSocket 进度接收

启动成功后必须打开 WebSocket 连接到 `/api/v1/ws/evolve/{run_id}`。URL 派生:从 `import.meta.env.VITE_API_BASE_URL || '/api/v1'` 取基础路径,转 `ws://` 或 `wss://`(根据当前页面 protocol),拼接 `/evolve/{run_id}`。WS 收到消息必须按 `event.type` 分发:
- `started` → 初始化进度条 0%,status tag「running」绿
- `generation`(atlas)→ 更新 agents 表 + 进化曲线追加数据点 + 进度条 `generation/total` + 事件日志 append
- `cycle`(gepa)→ 更新 agents 表(gepa agents 无 score 字段,显示 `score_after` of latest cycle)+ 进化曲线追加数据点(用 cycle 作 x)+ 进度条 `cycle/total` + 事件日志 append
- `meta_reflection`(gepa)→ 事件日志 append + NMessage info「第 N 周期元反思」
- `completed` → status tag「completed」绿 + 进度条 100% + 展示最终结果卡 + 关闭 WS + 「启动」enabled 「停止」disabled
- `stopped` → status tag「stopped」灰 + 关闭 WS + 「启动」enabled 「停止」disabled
- `error` → status tag「failed」红 + NMessage error `${code}: ${message}` + 关闭 WS + 「启动」enabled 「停止」disabled

WS 连接必须 `onUnmounted` 关闭,避免离开页面后泄漏。WS `onerror` 不弹 message(后端会通过 error 事件通知);WS `onclose` 不弹 message(正常完成时后端主动 close)。

#### 场景:atlas 进度推送
- **当** atlas run 启动,WS 收到 `{type:'generation', generation:1, total:10, agents:[...]}`
- **那么** 进度条显示 1/10 = 10%,agents 表更新 4 行,进化曲线追加第 1 代 4 个数据点,事件日志 append「Generation 1/10 completed」

#### 场景:gepa meta_reflection
- **当** gepa run 第 5 周期,WS 收到 `{type:'meta_reflection', cycle:5, summary:'...'}`
- **那么** 事件日志 append「第 5 周期元反思:...」,显示 NMessage info「第 5 周期元反思完成」

#### 场景:运行完成
- **当** atlas run 完成,WS 收到 `{type:'completed', best_agent:{name:'Beta',...}, final_weights:{...}}`
- **那么** status tag「completed」绿,进度条 100%,最终结果卡展示 best_agent + final_weights,WS 关闭,「启动」enabled 「停止」disabled

#### 场景:运行失败
- **当** WS 收到 `{type:'error', code:'data_not_found', message:'...'}`
- **那么** status tag「failed」红,NMessage error `data_not_found: data file not found: ...`,WS 关闭,「启动」enabled 「停止」disabled

### 需求:停止运行

点击「停止」按钮必须通过 WebSocket 发送 `{action:'stop'}` 消息(等效于 POST /stop,但走 WS 更省一次 HTTP 请求)。按钮立即 disabled + 显示 loading。后端收到 stop 后会在下次循环检查时推 `stopped` 事件,前端据此更新 status tag。

#### 场景:正常停止
- **当** atlas run 运行中,用户点「停止」
- **那么** WS 发送 `{action:'stop'}`,按钮 loading,后端推 `{type:'stopped', generation:N}` → status tag「stopped」灰,WS 关闭,「启动」enabled

### 需求:agents 表展示

agents 表必须用 NDataTable,列:名称(name)、风格(style)、评分(score,保留 4 位小数)、权重(weight,百分比 2 位小数)、代数(generation)。表数据从最新的 generation 事件(atlas)或 cycle 事件(gepa)的 `agents` 字段取。无事件时显示 NEmpty「暂无数据,启动进化后显示」。gepa 的 agents 字段 score 固定 0(后端不维护 score_history),评分列显示 `—`。

#### 场景:atlas agents 表
- **当** atlas run 第 3 代完成,agents=[{name:'Alpha', style:'趋势跟踪', score:0.4217, weight:0.0031, generation:3, params:{...}}, ...]
- **那么** 表显示 4 行,Alpha 行 score=0.4217 weight=0.31% generation=3

#### 场景:gepa agents 表
- **当** gepa run 第 2 周期,agents=[{name:'Alpha', score:0.0, weight:0.25, generation:2, params:{...}}, ...]
- **那么** 表显示 4 行,score 列显示 `—`(gepa 不维护 score),generation=2

### 需求:进化曲线展示

进化曲线必须用 echarts line chart,x 轴 = 代 / 周期号,y 轴 = score,每个 agent 一条线(4 条)。数据点从累积的 generation / cycle 事件提取:每个事件的 `agents` 数组按 name 索引,score 追加到对应 series 的 data。atlas 直接用 `agent.score`;gepa 用 cycle 事件的 `score_after`(以该 cycle 的 agent 命名 series)。曲线必须随事件实时更新(append 新点不重绘整个图)。

无数据时显示 NEmpty「暂无数据,启动进化后展示」。gepa 因 score_after 是单 agent 的,曲线展示 4 个 agent 各自的 score_after 随 cycle 变化(其他 agent 在该 cycle 无数据点 → echarts 自动断线)。

#### 场景:atlas 曲线
- **当** atlas run 第 1 / 2 / 3 代完成
- **那么** 曲线显示 4 条线(Alpha/Beta/Gamma/Delta),每条线 3 个数据点,x=1/2/3,y=对应 score

#### 场景:gepa 曲线
- **当** gepa run 第 1 周期 agent=Alpha score_after=0.33,第 2 周期 agent=Beta score_after=0.36
- **那么** 曲线显示 4 条线,Alpha 在 x=1 有点 y=0.33 x=2 无点(echarts 断线),Beta 在 x=2 有点 y=0.36

### 需求:事件日志展示

事件日志必须用 `<pre>` 滚动列表,最新事件在底部,自动滚动到底部(用 `nextTick` + `el.scrollTop = el.scrollHeight`)。每条日志一行,格式:
- started → `[00:00:01] 进化启动 (atlas, 10 代)`
- generation → `[00:00:02] 第 1/10 代: Alpha=0.42 Beta=0.61 Gamma=0.44 Delta=0.55`
- cycle → `[00:00:03] 第 1/10 周期 (Alpha): 0.29 → 0.33 ✓ 接受`
- meta_reflection → `[00:00:04] 元反思: 改进显著...`(截断 summary 前 100 字符)
- completed → `[00:00:05] 进化完成: 最佳=Beta 实验=6`
- stopped → `[00:00:06] 用户停止 (第 38 周期)`
- error → `[00:00:07] 错误 data_not_found: data file not found: ...`

日志列表必须 cap 200 条,超过则丢弃最早的。无事件时显示 NEmpty「暂无事件」。

#### 场景:多事件累积
- **当** atlas run 收到 5 个事件(started + 3 generation + completed)
- **那么** 日志列表显示 5 行,自动滚动到底部(completed 行可见)

### 需求:最终结果展示

收到 `completed` 事件后必须展示最终结果卡(在 agents 表 + 曲线下方):
- atlas:`最佳 Agent: <name> (<style>)` + `评分: <score>` + `权重: <weight>` + `最终权重: Alpha=X% Beta=Y% Gamma=Z% Delta=W%`
- gepa:`实验总数: <experiment_count>` + `元反思次数: <meta_count>` + `盲点发现: <blind_spots>`

结果卡用 NCard + NDescriptions 展示,字段从 completed 事件取。

#### 场景:atlas 完成
- **当** atlas completed 事件含 `best_agent:{name:'Beta', score:0.61, weight:0.84}` + `final_weights:{Alpha:0.003, ...}`
- **那么** 结果卡显示「最佳 Agent: Beta (均值回归) 评分: 0.6100 权重: 83.63%」+ NDescriptions 列最终权重 4 项

#### 场景:gepa 完成
- **当** gepa completed 事件含 `experiment_count:6, meta_count:1, blind_spots:['...']`
- **那么** 结果卡显示「实验总数: 6 元反思次数: 1」+ 盲点列表

### 需求:WebSocket 生命周期

页面 `onUnmounted` 必须关闭 WS 连接(`ws.close()`),避免离开页面后泄漏。WS 连接错误(`onerror`)不弹 message,仅 console.warn。WS 关闭(`onclose`)若非 completed/stopped/error 触发(异常关闭),status tag 显示「disconnected」灰 + NMessage warning「连接断开」。

#### 场景:离开页面清理
- **当** 用户从 `/evolve` 切到 `/backtest`,run 还在跑
- **那么** `onUnmounted` 调 `ws.close()`,后端 WS 协程收到 WebSocketDisconnect 退出,run 在后端继续跑(不取消)

#### 场景:WS 异常断开
- **当** WS onclose 触发且最后事件不是 completed/stopped/error
- **那么** status tag 显示「disconnected」灰 + NMessage warning「WebSocket 连接断开」,「启动」enabled 「停止」disabled
