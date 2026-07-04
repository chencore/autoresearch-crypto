## 上下文

`backtest-api` 已暴露两个接口，返回 `BacktestResponse`（含 equity_curve / trades / metrics / meta）。前端 `Backtest.vue` 是占位页。naive-ui 无图表组件，需要引入第三方图表库画收益曲线。表单需要交易对 + 策略两个 select，策略列表可复用 strategy-management-ui 的 `fetchStrategyList`。时间段是可选过滤，留空表示用全部数据。

## 目标 / 非目标

**目标：**
- 表单：交易对 select + 策略 select + 时间段 date range + 跑回测按钮
- 结果：6 个指标卡片 + 收益曲线图 + 交易明细表
- 加载 / 错误状态明确
- TypeScript 类型与后端 schema 对齐
- 复用 strategy-management-ui 的 `fetchStrategyList`

**非目标：**
- 不实现参数编辑（v0.1 用策略默认参数，留给 v0.2）
- 不实现回测结果持久化（v0.1 内存，关闭即丢）
- 不实现多回测对比（v0.1 单回测，留给 v0.2）
- 不导出回测结果（v0.1 不做 CSV/JSON 导出，留给 v0.2）
- 不做交易明细分页（v0.1 一次性渲染所有 trades，60 天 5m 数据约数百条可接受；超大数据集留 v0.2）
- 不做图表交互（无 zoom / 无 tooltip 联动交易表；v0.1 只看曲线大势）
- 不做单元测试（v0.1 前端不强制）

## 决策

### 决策 1：图表库选 echarts + vue-echarts
- **选择**：`echarts` + `vue-echarts`（按需引入 LineChart）
- **替代方案 A**：`chart.js` + `vue-chartjs`
- **替代方案 B**：`vis-network` / `d3` 手写
- **替代方案 C**：用 SVG / canvas 手写折线图
- **理由**：echarts 是中文社区最流行的图表库，文档完善；vue-echarts 是官方 Vue 封装，支持按需引入（tree-shakeable）；chart.js 在国内文档少；d3 学习曲线陡；手写折线图看似省依赖但需自己处理坐标轴/缩放/tooltip，得不偿失。echarts bundle 增量约 300KB（gzip 后 ~100KB），单机应用可接受。

### 决策 2：交易对 select value 用 `{symbol}|{interval}|{days}` 拼接
- **选择**：select 的 value 是 `ETHUSDT|5m|7` 形式字符串，提交时 split 出三个字段
- **替代方案 A**：select 的 value 是 `SymbolInfo` 对象（用 `value-key`）
- **替代方案 B**：三个独立 select（symbol / interval / days）
- **理由**：naive-ui NSelect 支持 `value-key` 但 API 略绕；三个 select 会让表单拥挤。拼接字符串简单直接，反序列化一行 split。后端要的是三个独立字段，前端在 submit 时拆。

### 决策 3：指标卡片用 NStatistic 不用 NCard
- **选择**：6 个 NStatistic 横排（NGrid 6 列），NStatistic 自带 label + value 格式化
- **替代方案**：6 个 NCard 各显示一个指标
- **理由**：NStatistic 专为数值展示设计，自带 tabular-nums 字体；NCard 套数字显得过重。NGrid 6 列在 1280px 宽屏每列约 150px 够用。

### 决策 4：收益曲线 x 轴用 timestamp（int ms），echarts 自动转 ISO
- **选择**：传 `[[ts_ms, equity], [ts_ms, equity], ...]` 给 echarts，x 轴 type='time' 自动渲染日期
- **替代方案**：x 轴 type='category' + ISO 字符串数组
- **理由**：type='time' 自动处理日期轴刻度（按天/小时自适应），无需手动指定 category；category 模式 2016 个点会挤在一起。echarts type='time' 直接吃 ms int。

### 决策 5：交易明细表一次性渲染所有 trades，不分页
- **选择**：NDataTable `:pagination="false"`，所有 trades 一次性渲染
- **替代方案**：分页（每页 50 条）
- **理由**：60 天 5m 数据回测约 200~400 条 trades，DOM 节点 < 5000 可接受；分页增加交互复杂度。若超大数据集（如 1 年数据回测上千 trades）卡顿，v0.2 加分页。

### 决策 6：错误时结果区恢复 NEmpty，不保留上次结果
- **选择**：回测失败时清空 `result.value = null`，结果区显示 NEmpty
- **替代方案**：保留上次成功结果，避免页面闪空
- **理由**：v0.1 单机个人用，避免显示陈旧数据更重要；用户能从错误提示知道这次失败，不会误以为上次结果还在。v0.2 可加「保留上次结果」选项。

### 决策 7：表单与结果区左右分栏，不做上下堆叠
- **选择**：`<NLayout has-sider>` 左 320px 表单 + 右 flex 1 结果
- **替代方案**：上下堆叠（表单在上，结果在下）
- **理由**：1280px 宽屏左右分栏更省垂直空间；表单只 4 个控件占 320px 够；结果区横向铺开指标卡片 + 图表更舒服。窄屏（< 1024px）v0.1 不适配（R-v0.1-ck-7 仅 ≥ 1280px）。

## 风险 / 权衡

- **[echarts bundle 增量 ~300KB]** → 单机应用不敏感，但 typecheck 需 `vue-echarts` 类型定义。缓解：vue-echarts 自带 TS 类型；echarts 用 `import { use } from 'echarts/core'` 按需引入减少 bundle
- **[2016 点折线图性能]** → echarts 2016 点渲染快（< 100ms），但若数据量到 1 万点（如 60 天 1m 数据）可能卡。缓解：v0.1 只支持 5m 数据（约 17000 点为 60 天上限），echarts 自带 `sampling: 'lttb'` 降采样可启用
- **[表单 select options 加载顺序]** → 若 symbols 还没加载完用户就点跑回测，select 为空。缓解：跑回测按钮在 symbols 与 strategies 都加载完前 disabled
- **[时间段 date range 时区]** → NDatePicker range 返回 `[start_ts, end_ts]`（ms int），转 ISO 字符串时可能因时区偏移导致过滤边界差 1 天。缓解：用 `new Date(ts).toISOString()` 显式转 UTC，后端按 UTC 解析（pd.Timestamp 默认 UTC naive）
- **[trades 表 step 列与图表联动]** → v0.1 不做联动（点 trade 不高亮图表点），留给 v0.2
- **[回滚]**：`git checkout frontend/src/pages/Backtest.vue frontend/package.json` 恢复 + 删 `frontend/src/api/backtest.ts` + `pnpm remove echarts vue-echarts`

## 迁移计划

- 新增 `frontend/src/api/backtest.ts`
- 重写 `frontend/src/pages/Backtest.vue`
- `pnpm add echarts vue-echarts`
- 启动后端 + 前端，浏览器手动验证
- `pnpm typecheck` 通过
- 回滚：上述命令 + `pnpm remove`

## 待解决问题

- 收益曲线是否需要双 y 轴（左 equity、右 drawdown）？v0.1 单 y 轴，drawdown 留给 v0.2
- 交易明细表是否需要导出 CSV？留给 v0.2
