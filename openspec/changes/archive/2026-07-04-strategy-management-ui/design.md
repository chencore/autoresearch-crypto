## 上下文

`setup-frontend-scaffold` 已搭好 Vue 3 + Vite + TypeScript + Naive UI 骨架，`Strategies.vue` 是占位页（直接 `JSON.stringify` 原始响应）。`strategy-management-api` 已暴露列表 + 详情两个接口，返回 `StrategyListResponse` 与 `StrategyDetail`。本 task 把占位页换成真实的列表表格 + 详情抽屉，对应 R-v0.1-ck-3 只读展示需求。

## 目标 / 非目标

**目标：**
- 列表表格展示 10 个策略的摘要
- 抽屉展示完整详情（基本信息 + docstring + 参数表 + 信号/运行时参数 tag）
- TypeScript 类型与后端 schema 对齐
- 加载 / 错误状态明确，404 不崩页
- 复用 `client.ts` 的拦截器，统一错误格式

**非目标：**
- 不实现参数编辑（v0.1 只读，留给 v0.2）
- 不实现实盘启停（live_status 占位 `"unknown"`，留给 `live-monitor-ui`）
- 不实现回测入口跳转（留给 `backtest-ui`）
- 不引入状态管理（Pinia 已装但本 task 用局部 ref 即可，无跨页共享需求）
- 不做服务端缓存（每次进页都重新拉，10 条数据无性能压力）
- 不做单元测试（v0.1 前端不强制，留待 v0.2 引入 vitest）

## 决策

### 决策 1：API 类型定义放 `api/strategy.ts`，不另起 `types/` 目录
- **选择**：在 `src/api/strategy.ts` 里 `export interface ParamDef {...}` 等类型，与 fetch 函数同文件
- **替代方案 A**：新建 `src/types/strategy.ts` 集中类型
- **替代方案 B**：用 `openapi-typescript` 从后端 OpenAPI 自动生成
- **理由**：v0.1 后端接口少（4 个模块各 2~3 个接口），手写类型 + 同文件管理最简单；自动生成需引依赖 + 配 build 脚本，过度工程化。types 目录留待接口数量上来再拆。

### 决策 2：列表用 NDataTable 而非手写 table
- **选择**：`NDataTable` + columns 配置，含 loading / pagination（关掉，10 行不分页）
- **替代方案**：手写 `<table>` + v-for
- **理由**：NDataTable 自带 loading 态、空状态、列对齐、响应式，省样式；与详情抽屉的参数表风格统一

### 决策 3：详情用 NDrawer 而非新页面 / Modal
- **选择**：右侧滑入 NDrawer（width 720px），列表保持可见
- **替代方案 A**：跳转 `/strategies/:name` 独立路由
- **替代方案 B**：NModal 居中弹窗
- **理由**：抽屉能保留列表上下文（用户看完详情可直接关抽屉看下一个），符合「管理台」交互习惯；独立路由会切走列表，每次都要回去；Modal 横向占屏在桌面端不如抽屉。

### 决策 4：参数表用 NDataTable 嵌套在抽屉里，不另起 NCard
- **选择**：抽屉内用 NDescriptions（基本信息）+ NCode（docstring）+ NDataTable（参数表）+ NSpace（tag 区），纵向堆叠
- **替代方案**：每段套 NCard 分块
- **理由**：抽屉本身已有容器边距，再套 NCard 多一层 padding 浪费空间；用 NDescriptions 的 title 区分即可。docstring 用 NCode 保留缩进。

### 决策 5：每次打开抽屉都重新拉详情，不缓存
- **选择**：抽屉 `open` 时调 `fetchStrategyDetail(name)`，关闭时清空 `detail.value`
- **替代方案**：用 Map 缓存已拉过的策略
- **理由**：策略元数据虽稳定，但 v0.1 开发态可能改 dex 代码后重启后端，缓存会显示旧数据；10 个策略每次拉 < 100ms，无性能问题。简单可靠优先。

### 决策 6：错误处理用 NMessage 一次性提示，不用 NResult 全屏
- **选择**：catch 里 `message.error('策略列表加载失败：' + err.message)`
- **替代方案**：用 NResult 显示全屏错误状态 + 重试按钮
- **理由**：列表加载失败时用户多半会刷新页面重试，NMessage 顶部一闪而过足够；全屏 NResult 对 10 行数据的场景过重。详情抽屉内 404 才用 NEmpty（局部错误）。

## 风险 / 权衡

- **[后端 schema 变化]** → TypeScript 类型与后端不同步。缓解：类型定义在 `api/strategy.ts` 注释里写「与 backend/app/schemas/strategy.py 对齐」，改后端时 grep 这里。v0.2 引入 openapi-typescript 自动生成可根治。
- **[嵌套 dict 默认值展示不友好]** → `MultiTFEnsembleStrategy.tf_params` 默认值是嵌套 dict，后端转字符串后前端展示一长串。缓解：详情表 default 列用 `<pre>` 包裹保留换行，超长用 CSS `white-space: pre-wrap; word-break: break-all`。本 task 不做 JSON 折叠组件。
- **[NDataTable 列宽]** → 参数表 annotation 列含 `float | None` 这种长字符串，可能挤行。缓解：用 `ellipsis: { tooltip: true }` 配置列，超长省略 + hover 显示完整。
- **[抽屉关闭时异步请求未完成]** → 用户快速关闭抽屉，请求回来后 setState 到已卸载组件。缓解：用 `let cancelled = false` 标志位，请求回来检查 `if (cancelled) return`。Vue 不像 React 有 strict mode 双调用，但仍需防。

## 迁移计划

- 新增 `frontend/src/api/strategy.ts`
- 重写 `frontend/src/pages/Strategies.vue`
- 启动后端 + 前端，浏览器手动验证 5 个场景：列表加载、详情抽屉、404、GridStrategy signal_kind、参数表必填/可选 tag
- `pnpm typecheck` 通过
- 回滚：`git checkout frontend/src/pages/Strategies.vue` 恢复占位 + 删 `api/strategy.ts`

## 待解决问题

- 列表是否需要搜索框？v0.1 10 个策略不需要，留给 v0.2 策略数 > 20 时再加
- 详情抽屉是否需要「复制 JSON」按钮？开发态调试有用，但非 v0.1 需求，留给 v0.2
