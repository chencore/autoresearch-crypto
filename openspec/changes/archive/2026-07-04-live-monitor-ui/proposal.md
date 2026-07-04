## 为什么

R-v0.1-ck-5 要求前端能从面板启停 Binance / OKX / Nado 三个实盘进程,监控持仓 / 未实现盈亏 / 最近成交 / 策略状态 / 实时日志。`live-monitor-api` 已完成后端 5 个接口(`exchanges` / `start` / `stop` / `status` / `logs`),本 task 实现前端 `Live.vue` 占位页重写,提供可视化操作界面。

v0.1 单机本地用,日志用 HTTP 轮询(2s 间隔)足够「实时」,不引入 WebSocket。

## 变更内容

- 新增 `frontend/src/api/live.ts`:封装 5 个接口,导出 TS 类型(ExchangeInfo / ExchangeListResponse / StartRequest / StartResponse / StopResponse / LiveStatus / LogResponse / ApiError)
- 重写 `frontend/src/pages/Live.vue`:
  - 顶部 3 个交易所卡片(NGrid x3):名称 + 状态 tag(running 绿 / stopped 灰)+ PID + 启动/停止按钮
  - 启动按钮弹 NModal:输入 symbol(默认 BTCUSDT)+ mode(NSelect demo/live)+ capital(默认 100)+ leverage(默认 1)
  - 选中某交易所 → 下方展示其 state(NDescriptions,字段透传:position/strategy_size/entry_price/last_signal/bar_count)+ logs(NCode 末尾 200 行,2s 轮询)
  - 错误处理:already_running/not_running/invalid_exchange/not_found → NMessage error
- 不动:后端、`live_*.py` 脚本、其他前端页

## 功能 (Capabilities)

### 新增功能
- `live-monitor-ui`: 前端实盘监控页,3 交易所卡片 + 启停 Modal + state/logs 展示 + 2s 轮询

### 修改功能
<!-- 无 -->

## 影响

- 新增文件:`frontend/src/api/live.ts`
- 修改文件:`frontend/src/pages/Live.vue`(占位重写)
- 复用:`frontend/src/api/client.ts`(axios 实例 + ApiError 拦截)、`frontend/src/App.vue` 的 NMessageProvider
- 数据依赖:后端 `/api/v1/live/*` 5 个接口(由 `live-monitor-api` 提供)
- 环境依赖:naive-ui(NCard/NGrid/NTag/NButton/NModal/NForm/NSelect/NInputNumber/NDescriptions/NCode/NSpin/NEmpty/useMessage)、vue 3.5 composition API
- 不动:后端、其他前端页、router
