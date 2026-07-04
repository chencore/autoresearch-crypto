## 为什么

`backtest-api` 已暴露 `GET /api/v1/backtest/symbols` 与 `POST /api/v1/backtest/run`，但前端 `Backtest.vue` 仍是 setup-frontend-scaffold 留下的占位页（直接 `JSON.stringify` 原始响应）。需要让用户在浏览器里选交易对 + 策略 + 时间段 → 一键跑回测 → 看收益曲线 + 交易明细 + 指标卡片，对应 R-v0.1-ck-4「回测可视化」需求。

## 变更内容

- 重写 `frontend/src/pages/Backtest.vue`：左侧表单（交易对 select / 策略 select / 时间段 date range / 跑回测按钮），右侧结果区（指标卡片 6 个 + 收益曲线图 + 交易明细表）
- 新增 `frontend/src/api/backtest.ts`：封装 symbols 与 run 接口，导出与后端 `app.schemas.backtest` 对齐的 TypeScript 类型
- 引入 `echarts` + `vue-echarts` 做收益曲线图（naive-ui 无图表组件）
- 指标卡片：6 个 NStatistic（total_return / annualized_return / annualized_vol / sharpe_ratio / max_drawdown / win_rate），按值正负染色
- 交易明细表：NDataTable，列含 step / timestamp（ISO）/ type（NTag 染色）/ price / pnl（正绿负红）
- 加载 / 错误状态：跑回测时按钮 loading + 全屏 NSpin；失败弹 NMessage；空结果（如时间过滤无数据）用 NEmpty
- 表单联动：交易对 select 从 `/symbols` 拉取；策略 select 从 `/strategy` 拉取（复用 strategy-management-ui 的 API client）

## 功能 (Capabilities)

### 新增功能
- `backtest-ui`: 前端回测页（表单 + 结果区），对接 `GET /api/v1/backtest/symbols` 与 `POST /api/v1/backtest/run`，展示收益曲线 + 交易明细 + 指标卡片

### 修改功能
<!-- 无 -->

## 影响

- 新增文件：`frontend/src/api/backtest.ts`
- 修改文件：`frontend/src/pages/Backtest.vue`（占位重写）、`frontend/package.json`（+echarts +vue-echarts）
- 复用：`frontend/src/api/client.ts`（axios 实例）、`frontend/src/api/strategy.ts`（fetchStrategyList 复用）
- 依赖新增：`echarts`、`vue-echarts`（tree-shakeable，bundle 增量 ~300KB）
- 不动：后端、路由结构、App.vue、其他三个页面占位
