## 新增需求

### 需求:回测页布局

前端必须在 `/backtest` 路由展示回测页。页面必须分为左右两区：左侧表单区（width 320px），右侧结果区（flex 1）。表单区含交易对 select、策略 select、时间段 date range（可选）、跑回测按钮。结果区初始显示 NEmpty「尚未跑回测」，跑完后显示指标卡片 + 收益曲线图 + 交易明细表。页面挂载时必须并行拉取 `GET /api/v1/backtest/symbols` 与 `GET /api/v1/strategy` 填充两个 select 的 options。

#### 场景:首次进入回测页
- **当** 用户从侧边栏点「回测可视化」进入 `/backtest`
- **那么** 左侧表单显示空 select，右侧显示 NEmpty「尚未跑回测」
- **那么** 页面挂载后并行拉取 symbols 与 strategies，填到两个 select 的 options

#### 场景:symbols 或 strategies 加载失败
- **当** `GET /symbols` 或 `GET /strategy` 失败
- **那么** 对应 select 显示空 options，顶部弹 NMessage error 提示「<资源>加载失败：<message>」

### 需求:回测表单

表单必须含 4 个控件：交易对 NSelect（options 从 symbols 接口拉，label 显示「`{symbol} {interval} {days}d`」，value 是 `{symbol}|{interval}|{days}` 拼接）、策略 NSelect（options 从 strategy 接口拉，label 是策略类名，value 是策略类名）、时间段 NDatePicker range（可选，留空表示不过滤）、跑回测 NButton。点击「跑回测」时必须校验交易对与策略已选，未选时弹 NMessage warning「请选择交易对与策略」不发起请求。已选时构造 `BacktestRequest` 调 `POST /run`，按钮显示 loading 禁用。

#### 场景:未选交易对或策略
- **当** 用户未选交易对或策略，点「跑回测」
- **那么** 弹 NMessage warning「请选择交易对与策略」，不发起 POST 请求

#### 场景:正常发起回测
- **当** 用户选 ETHUSDT 5m 7d + TrendFollowStrategy，点「跑回测」
- **那么** 按钮 loading 禁用，发起 `POST /api/v1/backtest/run` 请求体 `{"symbol":"ETHUSDT","interval":"5m","days":7,"strategy":"TrendFollowStrategy"}`

#### 场景:带时间段过滤
- **当** 用户选 ETHUSDT 5m 7d + TrendFollowStrategy + 时间段 2026-06-26 ~ 2026-06-27
- **那么** 请求体含 `"start":"2026-06-26T00:00:00.000Z"` 与 `"end":"2026-06-27T00:00:00.000Z"`（ISO 8601）

### 需求:回测结果展示

回测成功后，结果区必须分三段：指标卡片（6 个 NStatistic 横排）、收益曲线图（echarts line chart，x 轴 timestamp，y 轴 equity）、交易明细表（NDataTable）。指标卡片必须含 total_return（百分比格式，正绿负红）、annualized_return（百分比，正绿负红）、annualized_vol（百分比）、sharpe_ratio（保留 2 位小数，正绿负红）、max_drawdown（百分比，永远红色因为必为负或 0）、win_rate（百分比）。收益曲线 x 轴必须显示 ISO 日期，y 轴显示美元金额。交易明细表列含 step / timestamp（ISO）/ type（NTag 染色：buy/sell 蓝色、sell_short/buy_cover 橙色、sell_final 灰色）/ price（保留 2 位小数）/ pnl（保留 2 位小数，正绿负红，无 pnl 显示「—」）。

#### 场景:回测成功渲染结果
- **当** POST /run 返回 200，含 equity_curve（2016 点）、trades（368 条）、metrics、meta
- **那么** 结果区显示 6 个指标卡片 + 收益曲线图（2016 点折线）+ 交易明细表（368 行）

#### 场景:指标卡片染色
- **当** metrics.total_return = -0.0515（负）
- **那么** total_return 卡片显示「-5.15%」红色
- **当** metrics.sharpe_ratio = 0.71（正）
- **那么** sharpe_ratio 卡片显示「0.71」绿色

#### 场景:交易明细 type 染色
- **当** trades 含 `{"type":"buy", ...}`
- **那么** type 列显示蓝色 NTag「buy」
- **当** trades 含 `{"type":"sell_short", ...}`
- **那么** type 列显示橙色 NTag「sell_short」

### 需求:回测 API 客户端

前端必须新增 `src/api/backtest.ts` 模块，导出 `fetchSymbolList`、`runBacktest` 两个异步函数，分别对应两个后端接口。模块必须导出与后端 `app.schemas.backtest` 对齐的 TypeScript 类型：`SymbolInfo`、`SymbolListResponse`、`BacktestRequest`、`EquityPoint`、`Trade`、`Metrics`、`BacktestMeta`、`BacktestResponse`。函数失败时必须 reject `{ code, message }` 结构。

#### 场景:调用 symbols 接口
- **当** 调用 `fetchSymbolList()`
- **那么** 返回 `SymbolListResponse`（含 `symbols: SymbolInfo[]` 与 `total: number`）

#### 场景:调用 run 接口
- **当** 调用 `runBacktest({symbol:'ETHUSDT', interval:'5m', days:7, strategy:'TrendFollowStrategy'})`
- **那么** 返回 `BacktestResponse`，含 equity_curve / trades / metrics / meta

### 需求:回测加载与错误状态

回测请求发起时按钮必须显示 loading 禁用，结果区显示 NSpin。请求失败时（包括 `data_not_found` / `not_found` / `no_data_in_range` / 网络错误）必须弹 NMessage error「回测失败：<message>」，结果区恢复 NEmpty「尚未跑回测」或保留上次成功结果（v0.1 选择恢复 NEmpty，避免显示陈旧数据）。请求成功后按钮恢复可用，结果区显示新结果。

#### 场景:回测请求中
- **当** POST /run 发出但未返回
- **那么** 按钮显示 loading 禁用，结果区显示 NSpin

#### 场景:回测失败
- **当** POST /run 返回 404 `{"error":{"code":"data_not_found",...}}`
- **那么** 弹 NMessage error「回测失败：data file not found: ...」，结果区显示 NEmpty

#### 场景:回测成功后按钮恢复
- **当** POST /run 返回 200
- **那么** 按钮 loading 关闭恢复可用，结果区显示新结果
