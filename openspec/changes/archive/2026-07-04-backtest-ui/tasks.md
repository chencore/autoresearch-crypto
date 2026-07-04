## 1. 依赖与 API 客户端

- [x] 1.1 在 `frontend/` 运行 `pnpm add echarts vue-echarts`
- [x] 1.2 创建 `frontend/src/api/backtest.ts`：导出 `SymbolInfo` / `SymbolListResponse` / `BacktestRequest` / `EquityPoint` / `Trade` / `Metrics` / `BacktestMeta` / `BacktestResponse` 8 个 TypeScript interface，导出 `fetchSymbolList()` 与 `runBacktest(req: BacktestRequest)` 两个函数，复用 `client` 实例

## 2. 表单区

- [x] 2.1 重写 `frontend/src/pages/Backtest.vue` `<script setup>`：导入 `ref` / `onMounted` / `computed`，naive-ui 的 NSelect / NDatePicker / NButton / NGrid / NGi / NStatistic / NCard / NEmpty / NSpin / NDataTable / NTag / useMessage，echarts 的 `use` / `CanvasRenderer` / `LineChart` / `GridComponent` / `TooltipComponent` / `TitleComponent` / `XAxisComponent` / `YAxisComponent`，vue-echarts 的 `VChart`
- [x] 2.2 状态：`symbols: Ref<SymbolInfo[]>`、`strategies: Ref<StrategySummary[]>`、`formLoading: Ref<boolean>`、`selectedSymbol: Ref<string | null>`、`selectedStrategy: Ref<string | null>`、`dateRange: Ref<[number, number] | null>`、`running: Ref<boolean>`、`result: Ref<BacktestResponse | null>`
- [x] 2.3 `symbolOptions` computed：从 `symbols` 映射 `{label: '${symbol} ${interval} ${days}d', value: '${symbol}|${interval}|${days}'}`
- [x] 2.4 `strategyOptions` computed：从 `strategies` 映射 `{label: name, value: name}`
- [x] 2.5 `onMounted` 并行调 `fetchSymbolList()` 与 `fetchStrategyList()`，成功填 state、失败弹 NMessage error，finally 关 `formLoading`
- [x] 2.6 `runBtnDisabled` computed：`formLoading || !selectedSymbol || !selectedStrategy || running`

## 3. 跑回测逻辑

- [x] 3.1 实现 `runBacktest()` 函数：
  - 校验 `selectedSymbol` 与 `selectedStrategy` 已选，未选弹 warning return
  - split `selectedSymbol` 成 `symbol/interval/days`
  - 构造 `BacktestRequest`：`{symbol, interval, days: int, strategy, start?, end?}`
  - 若 `dateRange` 非空，`start = new Date(dateRange[0]).toISOString()`、`end = new Date(dateRange[1]).toISOString()`
  - 设 `running=true`、`result=null`
  - try `const res = await runBacktestAPI(req)` → `result.value = res`，catch 弹 NMessage error「回测失败：<message>」，finally `running=false`
- [x] 3.2 「跑回测」按钮 `@click="runBacktest"` `:loading="running"` `:disabled="runBtnDisabled"`

## 4. 结果区

- [x] 4.1 结果区分三段：指标卡片（NGrid 6 列）+ 收益曲线图（VChart）+ 交易明细表（NDataTable）
- [x] 4.2 指标卡片 6 个 NStatistic：total_return（百分比，正绿负红）、annualized_return（百分比，正绿负红）、annualized_vol（百分比）、sharpe_ratio（2 位小数，正绿负红）、max_drawdown（百分比，红色）、win_rate（百分比）
  - 用 `<NStatistic :value="..." />` + `:style="{ color: ... }"` 染色
  - 百分比格式化：`(v * 100).toFixed(2) + '%'`
- [x] 4.3 收益曲线图：`<VChart :option="chartOption" autoresize />`
  - `chartOption` computed：`{xAxis: {type: 'time'}, yAxis: {type: 'value', name: 'Equity ($)'}, series: [{type: 'line', data: equity_curve.map(p => [p.timestamp, p.equity]), showSymbol: false}], tooltip: {trigger: 'axis'}}`
- [x] 4.4 交易明细表 NDataTable：列含 step / timestamp（ISO）/ type（NTag 染色）/ price（2 位小数）/ pnl（2 位小数，正绿负红，无 pnl 显示「—」）
  - `:pagination="false"`，`:max-height="400"`，`:data="result.trades"`，`:columns="tradeColumns"`

## 5. 加载与错误态

- [x] 5.1 `result === null && !running` 时结果区显示 NEmpty「尚未跑回测」
- [x] 5.2 `running === true` 时结果区显示 NSpin
- [x] 5.3 `result !== null` 时结果显示三段内容
- [x] 5.4 失败时 `result.value = null`（清空旧数据），NMessage 弹错误

## 6. 启动验证

- [x] 6.1 后端 `cd backend && uv run uvicorn app.main:app --port 8000 &` 启动
- [x] 6.2 前端 `cd frontend && pnpm dev &` 启动，浏览器打开 `http://localhost:5173/backtest`
- [x] 6.3 验证表单加载：交易对 select 含 ETHUSDT 5m 7d，策略 select 含 10 个策略
- [x] 6.4 验证未选校验：清空 select 点「跑回测」→ 弹 warning「请选择交易对与策略」
- [x] 6.5 验证正常回测：选 ETHUSDT 5m 7d + TrendFollowStrategy → 点跑回测 → 结果区显示 6 个指标卡片 + 收益曲线图（约 2016 点折线）+ 交易明细表（约 368 行）
- [x] 6.6 验证指标染色：total_return 负值红色、sharpe_ratio 负值红色、win_rate 百分比
- [x] 6.7 验证时间段过滤：选 ETHUSDT 5m 7d + TrendFollowStrategy + 时间段 2026-06-26 ~ 2026-06-27 → 跑回测 → 交易明细表行数明显少于无过滤版本
- [x] 6.8 验证错误态：选 ETHUSDT 5m 7d + NonExistent 策略（手动改 selectedStrategy 触发）→ 弹 NMessage error「回测失败：strategy not found: NonExistent」→ 结果区恢复 NEmpty
- [x] 6.9 验证加载态：跑回测时按钮 loading、结果区 NSpin
- [x] 6.10 `cd frontend && pnpm typecheck` 通过
