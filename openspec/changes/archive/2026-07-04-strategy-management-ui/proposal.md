## 为什么

后端 `strategy-management-api` 已暴露 `GET /api/v1/strategy` 与 `GET /api/v1/strategy/{name}`，但前端 `Strategies.vue` 仍是 setup-frontend-scaffold 留下的占位页（直接 `JSON.stringify` 原始响应）。需要把 10 个策略的列表 + 详情可视化出来，让用户在浏览器里一站式查看策略类名、参数定义、信号类型、运行时参数，对应 R-v0.1-ck-3「策略管理（只读）」需求。

## 变更内容

- 重写 `frontend/src/pages/Strategies.vue`：策略列表表格（类名 / 模块 / 参数数量 / 实盘状态 / 操作），点「查看详情」抽屉展示完整元数据
- 新增 `frontend/src/api/strategy.ts`：封装列表与详情接口，导出 TypeScript 类型（与后端 `app.schemas.strategy` 对齐）
- 详情抽屉分四段：基本信息（模块 / 文件 / 描述 / docstring）、参数定义表（name / type / default / annotation / required）、信号类型 tag、运行时参数 tag
- 实盘状态 v0.1 直接展示 `live_status: "unknown"` 占位（后端尚未接进程状态），用灰色 tag
- 加载 / 错误状态：列表加载时 NSpin，请求失败弹 NMessage 错误提示

## 功能 (Capabilities)

### 新增功能
- `strategy-management-ui`: 前端策略管理页（只读），列表 + 详情抽屉，对接 `GET /api/v1/strategy` 与 `GET /api/v1/strategy/{name}`

### 修改功能
<!-- 无 — strategy-management 主规范是后端契约，本 task 不动后端，前端实现属于本 task 新增功能 -->

## 影响

- 新增文件：`frontend/src/api/strategy.ts`
- 修改文件：`frontend/src/pages/Strategies.vue`（占位重写）
- 复用：`frontend/src/api/client.ts`（axios 实例 + 错误拦截器）
- 依赖：`naive-ui` 的 NDataTable / NDrawer / NTag / NSpin / NSpace / NCard / NDescriptions / NMessage（已在 setup-frontend-scaffold 装好）
- 不动：后端、路由结构、App.vue、其他三个页面占位
