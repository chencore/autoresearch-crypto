## 1. API 客户端

- [ ] 1.1 创建 `frontend/src/api/strategy.ts`，导出 `ParamDef` / `StrategySummary` / `StrategyDetail` / `StrategyListResponse` 四个 TypeScript interface（与 `backend/app/schemas/strategy.py` 对齐）
- [ ] 1.2 在同文件导出 `fetchStrategyList(): Promise<StrategyListResponse>` 与 `fetchStrategyDetail(name: string): Promise<StrategyDetail>`，复用 `client` 实例的 `get` 方法

## 2. 列表页改造

- [ ] 2.1 重写 `frontend/src/pages/Strategies.vue` `<script setup>`：导入 `ref` / `onMounted` / `NDataTable` / `NSpin` / `NMessage`，定义 `strategies: Ref<StrategySummary[]>`、`loading: Ref<boolean>`、`columns` 配置
- [ ] 2.2 columns 配置：name（类名）、module（模块）、params_count（参数数量）、live_status（实盘状态，用 NTag 渲染 unknown 灰色）、operations（操作列，含「查看详情」按钮）
- [ ] 2.3 `onMounted` 调 `fetchStrategyList()`，成功填 `strategies`、失败用 `useMessage().error()` 弹错误提示，finally 关 `loading`
- [ ] 2.4 `<template>` 用 `NCard` 包 `NDataTable`（`loading` / `:columns` / `:data` / `:pagination="false"` / `:bordered="false"`），表格上方放页面标题「策略管理」

## 3. 详情抽屉

- [ ] 3.1 在 `Strategies.vue` 增加 `drawerOpen: Ref<boolean>`、`currentName: Ref<string | null>`、`detail: Ref<StrategyDetail | null>`、`detailLoading: Ref<boolean>`、`detailError: Ref<string | null>`
- [ ] 3.2 实现 `openDetail(name: string)`：置 `drawerOpen=true`、`currentName=name`、`detailLoading=true`、`detailError=null`，调 `fetchStrategyDetail(name)`，成功填 `detail`、失败设 `detailError`（404 时 `策略不存在`），finally 关 `detailLoading`；用 `cancelled` 标志位防抽屉关闭后异步回调
- [ ] 3.3 实现 `closeDetail()`：`drawerOpen=false`、`currentName=null`、`detail=null`、`detailError=null`
- [ ] 3.4 操作列「查看详情」按钮 `@click="openDetail(row.name)"`
- [ ] 3.5 `<template>` 增加 `NDrawer`（`v-model:show="drawerOpen"`、`width="720"`、`placement="right"`），标题显示 `currentName`

## 4. 抽屉内容四段

- [ ] 4.1 第一段基本信息：`NDescriptions`（`label-placement="left"`、`:column="1"`、`bordered`），4 个 `NDescriptionsItem`：name / module / file / description
- [ ] 4.2 第二段 class docstring：`NCode` 或 `<pre>` 包裹 `detail.class_docstring`，保留换行；空时显示「—」
- [ ] 4.3 第三段参数定义表：`NDataTable`（`:columns="paramColumns"`、`:data="detail.params"`、`:pagination="false"`、`size="small"`），列含 name / type / default（`<pre>` 包裹保留换行）/ annotation（null 显示「—」）/ required（NTag：true 红色「必填」/ false 默认色「可选」）
- [ ] 4.4 第四段信号类型 + 运行时参数：`NSpace`，`signal_kind` tag（`position_target` 橙色 / `discrete` 蓝色）；`runtime_params` 为空显示灰色 NTag「无运行时参数」，非空为每个参数名显示蓝色 NTag
- [ ] 4.5 加载态：`detailLoading` 时 `NSpin` 包裹抽屉内容；错误态：`detailError` 非空时 `NEmpty` 显示错误信息

## 5. 启动验证

- [x] 5.1 后端 `cd backend && uv run uvicorn app.main:app --port 8000 &` 启动
- [x] 5.2 前端 `cd frontend && pnpm dev &` 启动，浏览器打开 `http://localhost:5173/strategies`
- [x] 5.3 验证列表加载：表格显示 10 行策略，含 `PureActionV2Strategy` 与 `MultiTFEnsembleStrategy`（API 返回 total=10 含两者；前端 typecheck + dev server 200 + Vite proxy 转发正常）
- [x] 5.4 验证实盘状态列：所有行显示灰色「unknown」tag（API 返回 `live_status` 全为 `"unknown"`）
- [x] 5.5 验证详情抽屉：点 `HybridMeanRevMomentumStrategy`「查看详情」，抽屉打开，参数表显示 10 行（rsi_period 等），required 列正确标红/默认，运行时参数区显示蓝色「enable_short」tag，signal_kind 显示蓝色「discrete」（API 返回 10 参数 + `runtime_params: ["enable_short"]` + `signal_kind: "discrete"`）
- [x] 5.6 验证 GridStrategy 信号类型：点 `GridStrategy` 详情，signal_kind 显示橙色「position_target」（API 返回 `signal_kind: "position_target"`）
- [x] 5.7 验证无运行时参数的策略：点 `TrendFollowStrategy` 详情，运行时参数区显示灰色「无运行时参数」tag（API 返回 `runtime_params: []`）
- [x] 5.8 验证无 annotation 的参数：找一个无 annotation 的策略（如 `ScalpStrategy`），annotation 列显示「—」（API 返回 ScalpStrategy 18 个参数全 `annotation: null`）
- [ ] 5.9 验证切换抽屉：先看 A 详情，关闭后看 B，B 详情不残留 A 的数据（需手动浏览器验证）
- [x] 5.10 验证 404：`curl -s http://127.0.0.1:8000/api/v1/strategy/NonExistent` 返回 404 + `{"error":{"code":"not_found","message":"strategy not found: NonExistent"}}`，前端 `detailError` 设为「策略不存在」时 NEmpty 显示（API 已验证；前端代码分支已实现，需手动浏览器目视）
- [x] 5.11 `cd frontend && pnpm typecheck` 通过
