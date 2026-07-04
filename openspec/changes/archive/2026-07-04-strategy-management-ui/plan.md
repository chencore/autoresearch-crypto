# Plan: strategy-management-ui

> **详细实现计划**。位置铁律：本文件必须位于 `openspec/changes/<change-name>/plan.md`。

---

## 计划总览

本次实现分 **6 个阶段**。

| 阶段 | 目标 | 关键输出 | 估时 |
|------|------|----------|------|
| S1 | API 客户端 | `api/strategy.ts` 类型 + 两个 fetch 函数 | 0.25d |
| S2 | 列表页改造 | `Strategies.vue` NDataTable + 加载态 | 0.5d |
| S3 | 详情抽屉骨架 | NDrawer + openDetail/closeDetail 状态机 | 0.5d |
| S4 | 抽屉四段内容 | 基本信息 / docstring / 参数表 / tag 区 | 0.75d |
| S5 | 错误态与边界 | 404 NEmpty / 关闭防异步 / typecheck | 0.25d |
| S6 | 启动验证 | 11 项浏览器 + typecheck 全绿 | 0.25d |

**总估时**：约 2.5 人日。

---

## S1. API 客户端

### 目标

封装两个接口，导出与后端对齐的 TypeScript 类型。

### 实施步骤

1. 创建 `frontend/src/api/strategy.ts`：
   ```typescript
   import client from './client'

   export interface ParamDef {
     name: string
     type: string
     default: unknown
     annotation: string | null
     required: boolean
   }

   export interface StrategySummary {
     name: string
     module: string
     file: string
     description: string
     params_count: number
     live_status: string
   }

   export interface StrategyDetail {
     name: string
     module: string
     file: string
     description: string
     class_docstring: string
     params: ParamDef[]
     signal_kind: string
     runtime_params: string[]
     live_status: string
   }

   export interface StrategyListResponse {
     strategies: StrategySummary[]
     total: number
   }

   export async function fetchStrategyList(): Promise<StrategyListResponse> {
     return client.get<unknown, StrategyListResponse>('/strategy')
   }

   export async function fetchStrategyDetail(name: string): Promise<StrategyDetail> {
     return client.get<unknown, StrategyDetail>(`/strategy/${encodeURIComponent(name)}`)
   }
   ```
   - 注释一行：`// 与 backend/app/schemas/strategy.py 对齐`
   - `default: unknown`（嵌套 dict 后端转字符串后是 string，null 是 null，数字是 number）

### ✅ 完成验证
- [ ] `cd frontend && pnpm typecheck` 无 `strategy.ts` 相关报错
- [ ] `uv run python -c "import subprocess; ..."`（不适用，纯 TS 文件，typecheck 通过即可）

---

## S2. 列表页改造

### 目标

重写 `Strategies.vue` `<script setup>` 与 `<template>`，展示 10 行策略列表。

### 实施步骤

1. 改 `frontend/src/pages/Strategies.vue`：
   ```typescript
   <script setup lang="ts">
   import { h, onMounted, ref } from 'vue'
   import { NButton, NCard, NDataTable, NMessageProvider, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
   import { fetchStrategyList, type StrategySummary } from '@/api/strategy'

   const message = useMessage()
   const strategies = ref<StrategySummary[]>([])
   const loading = ref(false)

   const columns: DataTableColumns<StrategySummary> = [
     { title: '类名', key: 'name', width: 280 },
     { title: '模块', key: 'module', ellipsis: { tooltip: true } },
     { title: '参数数量', key: 'params_count', width: 100 },
     {
       title: '实盘状态',
       key: 'live_status',
       width: 120,
       render: (row) => h(NTag, { type: 'default', size: 'small' }, { default: () => row.live_status }),
     },
     {
       title: '操作',
       key: 'operations',
       width: 120,
       render: (row) => h(NButton, { size: 'small', onClick: () => openDetail(row.name) }, { default: () => '查看详情' }),
     },
   ]

   onMounted(async () => {
     loading.value = true
     try {
       const res = await fetchStrategyList()
       strategies.value = res.strategies
     } catch (err) {
       const e = err as { code: string; message: string }
       message.error(`策略列表加载失败：${e.message}`)
     } finally {
       loading.value = false
     }
   })
   </script>
   ```
   - `useMessage` 必须在 `<NMessageProvider>` 内调用，App.vue 需包一层（见 S2.2）

2. 改 `frontend/src/App.vue`：在 `<router-view />` 外包 `<NMessageProvider>`（若 setup-frontend-scaffold 未包）
   - 检查现状：setup-frontend-scaffold 的 App.vue 没有 NMessageProvider，本 task 加上

3. `<template>`：
   ```vue
   <template>
     <NCard title="策略管理" :bordered="false">
       <template #header-extra>
         <NTag type="info" size="small">v0.1 只读</NTag>
       </template>
       <NDataTable
         :columns="columns"
         :data="strategies"
         :loading="loading"
         :pagination="false"
         :bordered="false"
         size="small"
       />
     </NCard>
   </template>
   ```

### ✅ 完成验证
- [ ] `pnpm typecheck` 通过
- [ ] 浏览器进 `/strategies`：表格 10 行，含 `PureActionV2Strategy`、`MultiTFEnsembleStrategy`
- [ ] 实盘状态列每行显示灰色「unknown」tag
- [ ] 后端停掉刷新页面：顶部弹 NMessage error「策略列表加载失败：...」

---

## S3. 详情抽屉骨架

### 目标

抽屉打开 / 关闭状态机 + 详情拉取（含 404 处理 + 关闭防异步）。

### 实施步骤

1. 在 `Strategies.vue` `<script setup>` 加状态与函数：
   ```typescript
   import { NDrawer, NDescriptions, NDescriptionsItem, NCode, NEmpty, NSpin } from 'naive-ui'
   import { fetchStrategyDetail, type StrategyDetail } from '@/api/strategy'

   const drawerOpen = ref(false)
   const currentName = ref<string | null>(null)
   const detail = ref<StrategyDetail | null>(null)
   const detailLoading = ref(false)
   const detailError = ref<string | null>(null)

   async function openDetail(name: string): Promise<void> {
     drawerOpen.value = true
     currentName.value = name
     detail.value = null
     detailError.value = null
     detailLoading.value = true
     let cancelled = false
     const closeWatcher = () => { cancelled = true }
     // 简化：用 ref 标志位
     try {
       const res = await fetchStrategyDetail(name)
       if (cancelled) return
       detail.value = res
     } catch (err) {
       if (cancelled) return
       const e = err as { code: string; message: string }
       detailError.value = e.code === 'not_found' ? '策略不存在' : e.message
     } finally {
       if (!cancelled) detailLoading.value = false
     }
   }

   function closeDetail(): void {
     drawerOpen.value = false
     currentName.value = null
     detail.value = null
     detailError.value = null
   }
   ```
   - **取消标志位简化**：用一个 `let cancelled = false` 局部变量 + closeDetail 时无法直接置位（异步闭包），所以更稳的做法是用 `onBeforeUnmount` 清理 + 检查 `drawerOpen.value`。但 v0.1 简化为：openDetail 里用 `currentName.value === name` 检查（关闭后再开别的会改 currentName），即 `if (currentName.value !== name) return`。改写如下：
   ```typescript
   async function openDetail(name: string): Promise<void> {
     drawerOpen.value = true
     currentName.value = name
     detail.value = null
     detailError.value = null
     detailLoading.value = true
     try {
       const res = await fetchStrategyDetail(name)
       if (currentName.value !== name) return  // 已切到别的或已关闭
       detail.value = res
     } catch (err) {
       if (currentName.value !== name) return
       const e = err as { code: string; message: string }
       detailError.value = e.code === 'not_found' ? '策略不存在' : e.message
     } finally {
       if (currentName.value === name) detailLoading.value = false
     }
   }
   ```

2. `<template>` 加抽屉骨架（内容 S4 填）：
   ```vue
   <NDrawer v-model:show="drawerOpen" :width="720" placement="right" @update:show="(v) => !v && closeDetail()">
     <NDrawerContent :title="currentName ?? '策略详情'" closable>
       <NSpin v-if="detailLoading" />
       <NEmpty v-else-if="detailError" :description="detailError" />
       <div v-else-if="detail">
         <!-- S4 填四段内容 -->
       </div>
     </NDrawerContent>
   </NDrawer>
   ```
   - 引入 `NDrawerContent`

### ✅ 完成验证
- [ ] 点「查看详情」抽屉滑入，标题显示策略名
- [ ] 加载时显示 NSpin
- [ ] 关闭抽屉后再开另一个，标题与内容刷新

---

## S4. 抽屉四段内容

### 目标

基本信息 + docstring + 参数表 + 信号/运行时参数 tag。

### 实施步骤

1. 第一段基本信息（NDescriptions）：
   ```vue
   <NDescriptions label-placement="left" :column="1" bordered size="small">
     <NDescriptionsItem label="类名">{{ detail.name }}</NDescriptionsItem>
     <NDescriptionsItem label="模块">{{ detail.module }}</NDescriptionsItem>
     <NDescriptionsItem label="文件">{{ detail.file }}</NDescriptionsItem>
     <NDescriptionsItem label="描述">{{ detail.description || '—' }}</NDescriptionsItem>
   </NDescriptions>
   ```

2. 第二段 class docstring：
   ```vue
   <div style="margin-top: 16px">
     <div style="font-weight: 600; margin-bottom: 8px">Class docstring</div>
     <pre v-if="detail.class_docstring" style="background: var(--n-color-target); padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font-size: 12px">{{ detail.class_docstring }}</pre>
     <span v-else>—</span>
   </div>
   ```
   - 用 `<pre>` 而非 NCode，避免引号转义；样式内联，简单可靠

3. 第三段参数定义表（NDataTable）：
   ```typescript
   const paramColumns: DataTableColumns<ParamDef> = [
     { title: '名称', key: 'name', width: 180 },
     { title: '类型', key: 'type', width: 120 },
     {
       title: '默认值',
       key: 'default',
       render: (row) => h('pre', { style: 'white-space: pre-wrap; word-break: break-all; margin: 0; font-size: 12px' }, JSON.stringify(row.default, null, 2) ?? 'null'),
     },
     {
       title: 'Annotation',
       key: 'annotation',
       width: 140,
       render: (row) => row.annotation ?? '—',
     },
     {
       title: '必填',
       key: 'required',
       width: 80,
       render: (row) => h(NTag, { type: row.required ? 'error' : 'default', size: 'small' }, { default: () => row.required ? '必填' : '可选' }),
     },
   ]
   ```
   ```vue
   <div style="margin-top: 16px">
     <div style="font-weight: 600; margin-bottom: 8px">参数定义（{{ detail.params.length }}）</div>
     <NDataTable :columns="paramColumns" :data="detail.params" :pagination="false" size="small" />
   </div>
   ```

4. 第四段信号类型 + 运行时参数：
   ```vue
   <div style="margin-top: 16px">
     <div style="font-weight: 600; margin-bottom: 8px">信号类型</div>
     <NTag :type="detail.signal_kind === 'position_target' ? 'warning' : 'info'" size="small">{{ detail.signal_kind }}</NTag>
   </div>
   <div style="margin-top: 16px">
     <div style="font-weight: 600; margin-bottom: 8px">运行时参数</div>
     <NSpace>
       <NTag v-if="detail.runtime_params.length === 0" type="default" size="small">无运行时参数</NTag>
       <NTag v-for="p in detail.runtime_params" :key="p" type="info" size="small">{{ p }}</NTag>
     </NSpace>
   </div>
   ```

### ✅ 完成验证
- [ ] 点 `HybridMeanRevMomentumStrategy`：参数表 10 行，含 rsi_period / enable_short 等
- [ ] required 列：必填参数显示红色「必填」，可选显示默认色「可选」
- [ ] 运行时参数区显示蓝色「enable_short」tag
- [ ] signal_kind 显示蓝色「discrete」
- [ ] 点 `GridStrategy`：signal_kind 显示橙色「position_target」
- [ ] 点 `TrendFollowStrategy`：运行时参数区显示灰色「无运行时参数」tag
- [ ] annotation 为 null 的参数：列显示「—」

---

## S5. 错误态与边界

### 目标

404 / 关闭防异步 / typecheck 全绿。

### 实施步骤

1. 404 验证：S3 已实现 `detailError` 为 `'策略不存在'` 时 `NEmpty` 显示
2. 关闭防异步：S3 用 `currentName.value !== name` 检查
3. typecheck：`cd frontend && pnpm typecheck`

### ✅ 完成验证
- [ ] 临时改 `openDetail('NonExistent')` 触发 404，抽屉内显示 NEmpty「策略不存在」
- [ ] 快速开关抽屉不报错（控制台无 Vue warning）
- [ ] `pnpm typecheck` 0 error

---

## S6. 启动验证

### 目标

11 项浏览器 + typecheck 全绿。

### 实施步骤

1. 后端：`cd backend && uv run uvicorn app.main:app --port 8000`
2. 前端：`cd frontend && pnpm dev`
3. 浏览器打开 `http://localhost:5173/strategies`
4. 逐项验证 tasks.md 5.3~5.11

### ✅ 完成验证
- [ ] 5.3 列表 10 行，含 PureActionV2Strategy / MultiTFEnsembleStrategy
- [ ] 5.4 实盘状态列灰色「unknown」tag
- [ ] 5.5 HybridMeanRevMomentumStrategy 详情：10 参数 + enable_short tag + discrete tag
- [ ] 5.6 GridStrategy 详情：signal_kind 橙色「position_target」
- [ ] 5.7 TrendFollowStrategy 详情：运行时参数区灰色「无运行时参数」
- [ ] 5.8 无 annotation 的参数：列显示「—」
- [ ] 5.9 切换抽屉不残留
- [ ] 5.10 404 显示 NEmpty
- [ ] 5.11 `pnpm typecheck` 通过

---

## 风险与回滚

- **[useMessage 必须在 NMessageProvider 内]** → App.vue 未包 Provider 会导致 `useMessage()` 返回 undefined 报错。缓解：S2.2 检查并补 Provider
- **[NDataTable render 函数 h() 类型]** → TS 严格模式可能报 `h(NTag, ...)` 类型不匹配。缓解：用 `DataTableColumns<T>` 类型注解 columns，必要时 `as any` 兜底（最后手段）
- **[嵌套 dict 默认值展示不友好]** → `MultiTFEnsembleStrategy.tf_params` 字符串可能很长。缓解：default 列用 `<pre>` + `white-space: pre-wrap; word-break: break-all`
- **[NMessageProvider 与 router-view 嵌套顺序]** → Provider 必须在 router-view 外层，否则页面组件里 useMessage 拿不到。缓解：App.vue 里 `<NMessageProvider><router-view /></NMessageProvider>`
- **[回滚]**：`git checkout frontend/src/pages/Strategies.vue frontend/src/App.vue` 恢复 + 删 `frontend/src/api/strategy.ts`
