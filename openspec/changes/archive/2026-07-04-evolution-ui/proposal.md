## 为什么

R-v0.1-ck-6 要求前端能调优策略参数,展示进化曲线 / 当前最佳 / 最终结果。`evolution-api` 已完成后端 ATLAS / GEPA 进化引擎 + WebSocket 推送每代 / 每周期事件(started / generation / cycle / meta_reflection / completed / stopped / error)。本 task 实现前端 `Evolve.vue` 占位页重写,提供可视化操作界面:选引擎 + 配置 → 启动 → WebSocket 接进度 → 展示进化曲线 + 当前最佳 + 事件日志 + 最终结果。

v0.1 单机本地用,进化是短跑(几秒到几分钟),WebSocket 一连接到完成,不需要重连机制。

## 变更内容

- 新增 `frontend/src/api/evolve.ts`:封装 4 个 REST 接口 + WebSocket 帮助函数 + 7 个 TS 类型(StartRequest / StartResponse / RunSummary / RunListResponse / RunDetail / AgentState / StopResponse)+ 7 个事件类型(StartedEvent / GenerationEvent / CycleEvent / MetaReflectionEvent / CompletedEvent / StoppedEvent / ErrorEvent)
- 重写 `frontend/src/pages/Evolve.vue`:
  - 顶部表单:NSelect 引擎(atlas/gepa)+ NSelect symbol(复用 backtest 的 fetchSymbolList)+ NInputNumber generations(默认 10)+ NInputNumber evolution_interval(默认 5,仅 atlas 显示)+ 「启动」「停止」按钮
  - 中部进度卡:NProgress 进度条(current_gen/total)+ NTag 状态(running/completed/failed/stopped)+ 当前 run_id 文本
  - 下部左:agents 表(NDataTable,列 name/style/score/weight/generation)
  - 下部右上:进化曲线(echarts line chart,x=generation/cycle,y=score,每 agent 一条线)
  - 下部右下:事件日志(NCode 或 `<pre>` 滚动列表,最新 200 条,cap 200)
  - 完成时:NCard 展示最终结果(atlas:best_agent + final_weights;gepa:experiment_count + meta_count + blind_spots)
- 不动:后端、其他前端页、router

## 功能 (Capabilities)

### 新增功能
- `evolution-ui`: 前端参数调优页,选引擎 + 配置 → 启动 → WebSocket 接进度 → 进化曲线 + agents 表 + 事件日志 + 最终结果

### 修改功能
<!-- 无 -->

## 影响

- 新增文件:`frontend/src/api/evolve.ts`
- 修改文件:`frontend/src/pages/Evolve.vue`(占位重写)
- 复用:`frontend/src/api/client.ts`(axios + ApiError 拦截)、`frontend/src/api/backtest.ts` 的 `fetchSymbolList` + `SymbolInfo`、`frontend/src/App.vue` 的 NMessageProvider、Backtest.vue 的 echarts use 模式
- 数据依赖:后端 `/api/v1/evolve/*` 4 个 REST 接口 + `/api/v1/ws/evolve/{run_id}` WebSocket(由 `evolution-api` 提供);`/api/v1/backtest/symbols` 列交易对
- 环境依赖:naive-ui(NCard/NForm/NFormItem/NSelect/NInputNumber/NButton/NProgress/NTag/NDataTable/NCode/NEmpty/NSpin/NSpace/NStatistic/useMessage)、echarts(LineChart)、vue 3.5 composition API、原生 WebSocket API
- 不动:后端、其他前端页、router
