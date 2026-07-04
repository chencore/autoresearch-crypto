# Plan: backtest-ui

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **6 个阶段**。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | 依赖 + API 客户端 | `api/backtest.ts` + echarts 装好 | 0.25d |
| S2 | 表单区 | 交易对 select + 策略 select + 时间段 + 按钮 | 0.5d |
| S3 | 跑回测逻辑 | runBacktest 函数 + 请求构造 + 错误处理 | 0.25d |
| S4 | 结果区 | 指标卡片 + 收益曲线图 + 交易明细表 | 0.75d |
| S5 | 加载与错误态 | NEmpty / NSpin / NMessage | 0.25d |
| S6 | 启动验证 | 10 项浏览器 + typecheck 全绿 | 0.25d |

**总估时**：约 2.25 人日。

---

## S1. 依赖与 API 客户端

### 目标

装好 echarts，封装两个接口与 TS 类型。

### 实施步骤

1. `cd frontend && pnpm add echarts vue-echarts`
2. 创建 `frontend/src/api/backtest.ts`：
   ```typescript
   // 与 backend/app/schemas/backtest.py 对齐
   import client from './client'

   export interface SymbolInfo {
     symbol: string
     interval: string
     days: int
     file: string
   }
   export interface SymbolListResponse {
     symbols: SymbolInfo[]
     total: number
   }
   export interface BacktestRequest {
     symbol: string
     interval: string
     days: number
     strategy: string
     start?: string
     end?: string
   }
   export interface EquityPoint {
     step: number
     timestamp: number
     equity: number
   }
   export interface Trade {
     type: string
     step: number
     timestamp: number
     price: number
     pnl: number | null
   }
   export interface Metrics {
     total_return: number
     annualized_return: number
     annualized_vol: number
     sharpe_ratio: number
     max_drawdown: number
     win_rate: number
   }
   export interface BacktestMeta {
     strategy: string
     symbol: string
     interval: string
     bars: number
   }
   export interface BacktestResponse {
     equity_curve: EquityPoint[]
     trades: Trade[]
     metrics: Metrics
     meta: BacktestMeta
   }

   export async function fetchSymbolList(): Promise<SymbolListResponse> {
     return client.get<unknown, SymbolListResponse>('/backtest/symbols')
   }
   export async function runBacktest(req: BacktestRequest): Promise<BacktestResponse> {
     return client.post<unknown, BacktestResponse>('/backtest/run', req)
   }
   ```
   - 注意：`days: int` 在 TS 应是 `number`（修正）

### ✅ 完成验证
- [ ] `pnpm typecheck` 无 backtest.ts 相关报错

---

## S2. 表单区

### 目标

左侧表单 4 个控件，options 从两个接口拉取。

### 实施步骤

1. 重写 `frontend/src/pages/Backtest.vue` `<script setup>`：
   ```typescript
   import { computed, h, onMounted, ref } from 'vue'
   import {
     NButton, NCard, NDataTable, NDatePicker, NEmpty, NGi, NGrid,
     NSelect, NSpin, NStatistic, NTag, useMessage,
     type DataTableColumns, type SelectOption,
   } from 'naive-ui'
   import { use } from 'echarts/core'
   import { CanvasRenderer } from 'echarts/renderers'
   import { LineChart } from 'echarts/charts'
   import { GridComponent, TooltipComponent } from 'echarts/components'
   import VChart from 'vue-echarts'
   import {
     fetchSymbolList, runBacktest as runBacktestAPI,
     type BacktestResponse, type BacktestRequest, type SymbolInfo, type Trade,
   } from '@/api/backtest'
   import { fetchStrategyList, type StrategySummary } from '@/api/strategy'

   use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

   const message = useMessage()
   const symbols = ref<SymbolInfo[]>([])
   const strategies = ref<StrategySummary[]>([])
   const formLoading = ref(false)
   const selectedSymbol = ref<string | null>(null)
   const selectedStrategy = ref<string | null>(null)
   const dateRange = ref<[number, number] | null>(null)
   const running = ref(false)
   const result = ref<BacktestResponse | null>(null)

   const symbolOptions = computed<SelectOption[]>(() =>
     symbols.value.map((s) => ({
       label: `${s.symbol} ${s.interval} ${s.days}d`,
       value: `${s.symbol}|${s.interval}|${s.days}`,
     })),
   )
   const strategyOptions = computed<SelectOption[]>(() =>
     strategies.value.map((s) => ({ label: s.name, value: s.name })),
   )
   const runBtnDisabled = computed(() =>
     formLoading.value || !selectedSymbol.value || !selectedStrategy.value || running.value,
   )

   onMounted(async () => {
     formLoading.value = true
     try {
       const [symRes, stratRes] = await Promise.all([
         fetchSymbolList(),
         fetchStrategyList(),
       ])
       symbols.value = symRes.symbols
       strategies.value = stratRes.strategies
     } catch (err) {
       const e = err as { code: string; message: string }
       message.error(`表单加载失败：${e.message}`)
     } finally {
       formLoading.value = false
     }
   })
   ```

2. `<template>` 表单区：
   ```vue
   <NLayout has-sider style="height: calc(100vh - 48px)">
     <NLayoutSider bordered :width="320" :native-scrollbar="false">
       <div style="padding: 16px">
         <div style="font-weight: 600; margin-bottom: 12px">回测配置</div>
         <NSpace vertical>
           <div>
             <div style="margin-bottom: 4px; font-size: 12px; color: var(--n-text-color-3)">交易对</div>
             <NSelect :options="symbolOptions" :value="selectedSymbol" :loading="formLoading" placeholder="选择交易对" @update:value="(v) => selectedSymbol = v" />
           </div>
           <div>
             <div style="margin-bottom: 4px; font-size: 12px; color: var(--n-text-color-3)">策略</div>
             <NSelect :options="strategyOptions" :value="selectedStrategy" :loading="formLoading" placeholder="选择策略" @update:value="(v) => selectedStrategy = v" />
           </div>
           <div>
             <div style="margin-bottom: 4px; font-size: 12px; color: var(--n-text-color-3)">时间段（可选）</div>
             <NDatePicker v-model:value="dateRange" type="datetimerange" clearable />
           </div>
           <NButton type="primary" :loading="running" :disabled="runBtnDisabled" @click="runBacktest" block>跑回测</NButton>
         </NSpace>
       </div>
     </NLayoutSider>
     <NLayoutContent style="padding: 16px">
       <!-- S4 结果区 -->
     </NLayoutContent>
   </NLayout>
   ```
   - 引入 `NLayout` / `NLayoutSider` / `NLayoutContent` / `NSpace`

### ✅ 完成验证
- [ ] `pnpm typecheck` 通过
- [ ] 浏览器进 `/backtest`：左侧表单 4 个控件，右侧空区
- [ ] 两个 select 加载完显示 options

---

## S3. 跑回测逻辑

### 目标

`runBacktest` 函数：校验 → 构造请求 → 调 API → 处理结果。

### 实施步骤

1. 在 `<script setup>` 加：
   ```typescript
   async function runBacktest(): Promise<void> {
     if (!selectedSymbol.value || !selectedStrategy.value) {
       message.warning('请选择交易对与策略')
       return
     }
     const [symbol, interval, daysStr] = selectedSymbol.value.split('|')
     const req: BacktestRequest = {
       symbol,
       interval,
       days: parseInt(daysStr, 10),
       strategy: selectedStrategy.value,
     }
     if (dateRange.value) {
       req.start = new Date(dateRange.value[0]).toISOString()
       req.end = new Date(dateRange.value[1]).toISOString()
     }
     running.value = true
     result.value = null
     try {
       result.value = await runBacktestAPI(req)
     } catch (err) {
       const e = err as { code: string; message: string }
       message.error(`回测失败：${e.message}`)
     } finally {
       running.value = false
     }
   }
   ```

### ✅ 完成验证
- [ ] 选 ETHUSDT 5m 7d + TrendFollowStrategy，点跑回测，按钮 loading
- [ ] 成功后 `result` 非空，控制台无错误

---

## S4. 结果区

### 目标

指标卡片 + 收益曲线图 + 交易明细表。

### 实施步骤

1. 指标卡片（NGrid 6 列）：
   ```typescript
   const metricsDisplay = computed(() => {
     if (!result.value) return null
     const m = result.value.metrics
     return [
       { label: '总收益', value: (m.total_return * 100).toFixed(2) + '%', positive: m.total_return >= 0 },
       { label: '年化收益', value: (m.annualized_return * 100).toFixed(2) + '%', positive: m.annualized_return >= 0 },
       { label: '年化波动', value: (m.annualized_vol * 100).toFixed(2) + '%', positive: null },
       { label: 'Sharpe', value: m.sharpe_ratio.toFixed(2), positive: m.sharpe_ratio >= 0 },
       { label: '最大回撤', value: (m.max_drawdown * 100).toFixed(2) + '%', positive: false },
       { label: '胜率', value: (m.win_rate * 100).toFixed(2) + '%', positive: null },
     ]
   })
   ```
   ```vue
   <NGrid :cols="6" :x-gap="12">
     <NGi v-for="m in metricsDisplay" :key="m.label">
       <NCard size="small">
         <NStatistic :label="m.label" :value="m.value"
           :style="{ color: m.positive === true ? '#18a058' : m.positive === false ? '#d03050' : undefined }" />
       </NCard>
     </NGi>
   </NGrid>
   ```

2. 收益曲线图：
   ```typescript
   const chartOption = computed(() => {
     if (!result.value) return {}
     return {
       tooltip: { trigger: 'axis' },
       xAxis: { type: 'time', name: '时间' },
       yAxis: { type: 'value', name: 'Equity ($)' },
       series: [{
         type: 'line',
         showSymbol: false,
         data: result.value.equity_curve.map((p) => [p.timestamp, p.equity]),
         lineStyle: { width: 1.5 },
       }],
       grid: { left: 60, right: 20, top: 30, bottom: 40 },
     }
   })
   ```
   ```vue
   <NCard size="small" title="收益曲线" style="margin-top: 12px">
     <VChart :option="chartOption" autoresize style="height: 320px" />
   </NCard>
   ```

3. 交易明细表：
   ```typescript
   const tradeColumns: DataTableColumns<Trade> = [
     { title: 'Step', key: 'step', width: 80 },
     {
       title: '时间',
       key: 'timestamp',
       width: 200,
       render: (row) => new Date(row.timestamp).toISOString().replace('T', ' ').slice(0, 19),
     },
     {
       title: '类型',
       key: 'type',
       width: 120,
       render: (row) => {
         const color = ['buy', 'sell'].includes(row.type) ? 'info' : ['sell_short', 'buy_cover'].includes(row.type) ? 'warning' : 'default'
         return h(NTag, { type: color, size: 'small' }, { default: () => row.type })
       },
     },
     { title: '价格', key: 'price', width: 120, render: (row) => row.price.toFixed(2) },
     {
       title: 'PnL',
       key: 'pnl',
       width: 120,
       render: (row) => {
         if (row.pnl === null || row.pnl === undefined) return '—'
         const color = row.pnl >= 0 ? '#18a058' : '#d03050'
         return h('span', { style: `color: ${color}` }, row.pnl.toFixed(2))
       },
     },
   ]
   ```
   ```vue
   <NCard size="small" title="交易明细" style="margin-top: 12px">
     <NDataTable :columns="tradeColumns" :data="result.trades" :pagination="false" :max-height="400" size="small" />
   </NCard>
   ```

### ✅ 完成验证
- [ ] 跑回测后指标卡片显示 6 个，染色正确
- [ ] 收益曲线图渲染 2016 点折线
- [ ] 交易明细表显示所有 trades，type 染色正确

---

## S5. 加载与错误态

### 目标

NEmpty / NSpin / NMessage 三态切换。

### 实施步骤

1. 结果区条件渲染：
   ```vue
   <NLayoutContent style="padding: 16px">
     <NSpin v-if="running" size="large" style="display: flex; justify-content: center; padding: 80px" />
     <NEmpty v-else-if="!result" description="尚未跑回测" style="padding: 80px" />
     <div v-else>
       <!-- 指标卡片 + 图表 + 交易明细表 -->
     </div>
   </NLayoutContent>
   ```

### ✅ 完成验证
- [ ] 初次进页显示 NEmpty
- [ ] 跑回测时显示 NSpin
- [ ] 成功显示结果
- [ ] 失败弹 NMessage + 恢复 NEmpty

---

## S6. 启动验证

### 目标

10 项浏览器 + typecheck 全绿。

### 实施步骤

1. 后端 `cd backend && uv run uvicorn app.main:app --port 8000`
2. 前端 `cd frontend && pnpm dev`
3. 浏览器 `http://localhost:5173/backtest`

### ✅ 完成验证
- [ ] 6.3 表单加载：交易对 select 含 ETHUSDT 5m 7d，策略 select 含 10 个策略
- [ ] 6.4 未选校验：清空 select 点跑回测 → 弹 warning
- [ ] 6.5 正常回测：ETHUSDT 5m 7d + TrendFollowStrategy → 6 指标卡片 + 收益曲线图 + 交易明细表
- [ ] 6.6 指标染色：负值红色、正值绿色
- [ ] 6.7 时间段过滤：选 2026-06-26 ~ 2026-06-27 → 交易明细行数减少
- [ ] 6.8 错误态：手动改 selectedStrategy 为 NonExistent → 弹 error + 恢复 NEmpty
- [ ] 6.9 加载态：跑回测时按钮 loading + NSpin
- [ ] 6.10 `pnpm typecheck` 通过

---

## 风险与回滚

- **[echarts 类型定义]** → vue-echarts 自带 TS 类型；echarts core 用 `import { use } from 'echarts/core'` 按需引入
- **[2016 点折线图性能]** → echarts 2016 点 < 100ms 渲染；可加 `sampling: 'lttb'`
- **[NDatePicker range 时区]** → `new Date(ts).toISOString()` 显式 UTC
- **[typecheck 报 `int` 不存在]** → TS 类型用 `number`（S1 已修正）
- **[回滚]**：`git checkout frontend/src/pages/Backtest.vue frontend/package.json` 恢复 + 删 `frontend/src/api/backtest.ts` + `pnpm remove echarts vue-echarts`
