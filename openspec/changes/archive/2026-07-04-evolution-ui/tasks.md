## 1. API 客户端封装

- [x] 1.1 创建 `frontend/src/api/evolve.ts`:
  - `import client from './client'`
  - `import type { SymbolInfo } from './backtest'`(复用类型,不重新定义)
  - REST 类型:
    - `export interface EvolveStartRequest { engine: 'atlas' | 'gepa'; symbol: string; interval: string; days: number; generations: number; evolution_interval?: number }`
    - `export interface EvolveStartResponse { run_id: string; status: string }`
    - `export interface EvolveAgentState { name: string; style: string; score: number; weight: number; generation: number; params: Record<string, unknown> }`
    - `export interface EvolveRunSummary { id: string; engine: string; symbol: string; status: string; current_gen: number; total_generations: number; started_at: string; completed_at: string | null; error: string | null }`
    - `export interface EvolveRunListResponse { runs: EvolveRunSummary[]; total: number }`
    - `export interface EvolveRunDetail { id: string; engine: string; symbol: string; interval: string; days: number; generations: number; status: string; current_gen: number; started_at: string; completed_at: string | null; error: string | null; agents: EvolveAgentState[]; event_count: number }`
    - `export interface EvolveStopResponse { run_id: string; status: string }`
  - 事件类型(union):
    - `export interface StartedEvent { type: 'started'; run_id: string; config: Record<string, unknown>; total: number }`
    - `export interface GenerationEvent { type: 'generation'; run_id: string; generation: number; total: number; agents: EvolveAgentState[] }`
    - `export interface CycleEvent { type: 'cycle'; run_id: string; cycle: number; total: number; agent: string; hypothesis: string; score_before: number; score_after: number; accepted: boolean; reflection: string }`
    - `export interface MetaReflectionEvent { type: 'meta_reflection'; run_id: string; cycle: number; summary: string }`
    - `export interface CompletedEvent { type: 'completed'; run_id: string; best_agent?: { name: string; style: string; score: number; weight: number; params: Record<string, unknown> }; final_weights?: Record<string, number>; experiment_count?: number; meta_count?: number; blind_spots?: string[] }`
    - `export interface StoppedEvent { type: 'stopped'; run_id: string; generation?: number; cycle?: number }`
    - `export interface ErrorEvent { type: 'error'; run_id?: string; code: string; message: string }`
    - `export type EvolveEvent = StartedEvent | GenerationEvent | CycleEvent | MetaReflectionEvent | CompletedEvent | StoppedEvent | ErrorEvent`
  - REST 函数:
    - `export async function startEvolution(req: EvolveStartRequest): Promise<EvolveStartResponse>` → `client.post('/evolve/start', req)`
    - `export async function fetchRunList(): Promise<EvolveRunListResponse>` → `client.get('/evolve/runs')`
    - `export async function fetchRunDetail(runId: string): Promise<EvolveRunDetail>` → `client.get(\`/evolve/runs/${runId}\`)`
    - `export async function stopEvolution(runId: string): Promise<EvolveStopResponse>` → `client.post('/evolve/stop', { run_id: runId })`
  - WS URL 帮助:
    - `export function buildEvolveWsUrl(runId: string): string`:
      - `const base = import.meta.env.VITE_API_BASE_URL || '/api/v1'`
      - 若 base 以 `http://` 开头 → `base.replace('http://', 'ws://') + '/evolve/' + runId`
      - 若以 `https://` 开头 → `base.replace('https://', 'wss://') + '/evolve/' + runId`
      - 否则(相对路径)→ `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${base}/evolve/${runId}`

## 2. Evolve.vue 页面重写

- [x] 2.1 重写 `frontend/src/pages/Evolve.vue` `<script setup lang="ts">`:
  - imports:`ref / computed / onUnmounted / nextTick / watch` from vue
  - naive-ui:`NCard / NForm / NFormItem / NSelect / NInputNumber / NButton / NProgress / NTag / NDataTable / NEmpty / NSpin / NSpace / NDescriptions / NDescriptionsItem / NStatistic / useMessage` + `type SelectOption / type DataTableColumns`
  - echarts:`use` from `echarts/core` + `CanvasRenderer` + `LineChart` + `GridComponent / TooltipComponent / LegendComponent` + `VChart` from `vue-echarts`
  - 从 `@/api/evolve` 导入 4 个 REST 函数 + `buildEvolveWsUrl` + 类型(EvolveEvent / EvolveAgentState / CompletedEvent 等)
  - 从 `@/api/backtest` 导入 `fetchSymbolList` + `SymbolInfo`
  - `use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])`
  - state:
    - `symbols = ref<SymbolInfo[]>([])`
    - `engine = ref<'atlas' | 'gepa'>('atlas')`
    - `selectedSymbol = ref<string | null>(null)`(值 `${symbol}|${interval}|${days}`)
    - `generations = ref<number>(10)`
    - `evolutionInterval = ref<number>(5)`(仅 atlas 显示)
    - `runId = ref<string | null>(null)`
    - `status = ref<'idle' | 'running' | 'completed' | 'failed' | 'stopped' | 'disconnected'>('idle')`
    - `currentGen = ref<number>(0)`
    - `totalGen = ref<number>(0)`
    - `agents = ref<EvolveAgentState[]>([])`
    - `events = ref<EvolveEvent[]>([])`(cap 200)
    - `eventLog = ref<string[]>([])`(cap 200,展示用)
    - `finalResult = ref<CompletedEvent | null>(null)`
    - `starting = ref(false)`(启动按钮 loading)
    - `stopping = ref(false)`(停止按钮 loading)
    - `ws = ref<WebSocket | null>(null)`
    - `logPre = ref<HTMLPreElement | null>(null)`(用于自动滚动)
  - mode 选项:`const engineOptions: SelectOption[] = [{label:'ATLAS 多策略进化', value:'atlas'}, {label:'GEPA 反思式进化', value:'gepa'}]`
  - symbol 选项 computed:从 `symbols.value` 派生 `[{label: '${s.symbol} ${s.interval} ${s.days}d', value: '${s.symbol}|${s.interval}|${s.days}'}]`

- [x] 2.2 实现 `onMounted`:
  - `fetchSymbolList().then(res => symbols.value = res.symbols).catch(err => message.error('加载交易对失败: ' + err.message))`

- [x] 2.3 实现 `chartOption` computed(echarts option):
  - `legend: { data: ['Alpha', 'Beta', 'Gamma', 'Delta'] }`
  - `xAxis: { type: 'category', data: [...generations 1..totalGen] }`(从 events 累积)
  - `yAxis: { type: 'value', name: 'score' }`
  - `series: [{name:'Alpha', type:'line', data: [...]}, ...]`(4 个 agent)
  - 数据点从 `events` 中所有 `generation` / `cycle` 事件提取:
    - atlas:每个 generation 事件 4 个 agent 都有 score,append 到对应 series
    - gepa:每个 cycle 事件只有 1 个 agent 的 score_after,append 到该 agent series(其他 series 该 x 无点 → null)

- [x] 2.4 实现 `agentsColumns` computed(NDataTable columns):
  - `{title:'名称', key:'name', width:100}`
  - `{title:'风格', key:'style', width:120}`
  - `{title:'评分', key:'score', width:100, render: (row) => engine.value === 'gepa' ? '—' : row.score.toFixed(4)}`
  - `{title:'权重', key:'weight', width:100, render: (row) => (row.weight * 100).toFixed(2) + '%'}`
  - `{title:'代数', key:'generation', width:80}`

- [x] 2.5 实现 `appendEvent(event: EvolveEvent)`:
  - `events.value.push(event)`;`if (events.value.length > 200) events.value = events.value.slice(-200)`
  - 根据 event.type 构造日志字符串 push 到 `eventLog.value`(cap 200):
    - started → `[time] 进化启动 (${engine}, ${total} 代/周期)`
    - generation → `[time] 第 ${generation}/${total} 代: ${agents.map(a => `${a.name}=${a.score.toFixed(2)}`).join(' ')}`
    - cycle → `[time] 第 ${cycle}/${total} 周期 (${agent}): ${score_before.toFixed(2)} → ${score_after.toFixed(2)} ${accepted ? '✓' : '✗'}`
    - meta_reflection → `[time] 元反思: ${summary.slice(0, 100)}`
    - completed → atlas: `[time] 进化完成: 最佳=${best_agent.name}`;gepa: `[time] 进化完成: 实验=${experiment_count}`
    - stopped → `[time] 用户停止 (第 ${generation ?? cycle} 代/周期)`
    - error → `[time] 错误 ${code}: ${message}`
  - `nextTick(() => { if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight })`

- [x] 2.6 实现 `handleWsMessage(event: MessageEvent)`:
  - `const data = JSON.parse(event.data) as EvolveEvent`
  - `appendEvent(data)`
  - switch data.type:
    - started → `status.value = 'running'; totalGen.value = data.total; currentGen.value = 0; finalResult.value = null`
    - generation → `status.value = 'running'; currentGen.value = data.generation; agents.value = data.agents`
    - cycle → `status.value = 'running'; currentGen.value = data.cycle; agents.value = data.agents`(agents 字段在 cycle 事件中也存在)
    - meta_reflection → `message.info(\`第 ${data.cycle} 周期元反思完成\`)`
    - completed → `status.value = 'completed'; currentGen.value = totalGen.value; finalResult.value = data; closeWs()`
    - stopped → `status.value = 'stopped'; stopping.value = false; closeWs()`
    - error → `status.value = 'failed'; message.error(\`${data.code}: ${data.message}\`); closeWs()`

- [x] 2.7 实现 `startEvolution()`:
  - 校验 selectedSymbol + engine,缺则 warning
  - `starting.value = true`
  - try:`const [symbol, interval, days] = selectedSymbol.value!.split('|')`;`const req: EvolveStartRequest = {engine: engine.value, symbol, interval, days: Number(days), generations: generations.value, ...(engine.value === 'atlas' ? {evolution_interval: evolutionInterval.value} : {})}`;`const res = await startEvolutionAPI(req)`
    - `runId.value = res.run_id; status.value = 'running'; currentGen.value = 0; totalGen.value = generations.value; events.value = []; eventLog.value = []; agents.value = []; finalResult.value = null`
    - `const url = buildEvolveWsUrl(res.run_id)`
    - `ws.value = new WebSocket(url)`
    - `ws.value.onmessage = handleWsMessage`
    - `ws.value.onerror = () => console.warn('WS error')`
    - `ws.value.onclose = () => { if (status.value === 'running') { status.value = 'disconnected'; message.warning('WebSocket 连接断开') } ws.value = null }`
  - catch `e`:
    - `code === 'invalid_engine' / 'data_not_found'` → `message.error(\`${e.message}\`)`
    - `code === 'network'` → `message.error('启动失败: 网络错误')`
    - 其他 → `message.error(\`启动失败: ${e.message}\`)`
    - `status.value = 'idle'`
  - finally `starting.value = false`

- [x] 2.8 实现 `stopEvolution()`:
  - `if (!ws.value || ws.value.readyState !== WebSocket.OPEN) return`
  - `stopping.value = true`
  - `ws.value.send(JSON.stringify({action: 'stop'}))`

- [x] 2.9 实现 `closeWs()`:
  - `if (ws.value) { ws.value.onmessage = null; ws.value.onclose = null; ws.value.onerror = null; ws.value.close(); ws.value = null }`

- [x] 2.10 实现 `onUnmounted`:`closeWs()`

- [x] 2.11 实现 `<template>`:
  - 顶部 `NCard title="参数调优"`:
    - `<NForm :model="form" label-placement="left" label-width="100" inline>`:
      - `<NFormItem label="引擎"><NSelect v-model:value="engine" :options="engineOptions" style="width: 200px" /></NFormItem>`
      - `<NFormItem label="交易对"><NSelect v-model:value="selectedSymbol" :options="symbolOptions" placeholder="选择交易对" style="width: 240px" filterable /></NFormItem>`
      - `<NFormItem label="代数"><NInputNumber v-model:value="generations" :min="1" :max="100" :step="1" style="width: 120px" /></NFormItem>`
      - `<NFormItem v-if="engine === 'atlas'" label="进化间隔"><NInputNumber v-model:value="evolutionInterval" :min="1" :max="20" :step="1" style="width: 120px" /></NFormItem>`
      - `<NFormItem><NSpace><NButton type="primary" :loading="starting" :disabled="!selectedSymbol || status === 'running'" @click="startEvolution">启动</NButton><NButton type="error" :loading="stopping" :disabled="status !== 'running'" @click="stopEvolution">停止</NButton></NSpace></NFormItem>`
  - 中部 `NCard title="运行状态"`(runId 不为空时显示):
    - `<NSpace align="center">`
      - `<NTag :type="statusTagType">{{ statusText }}</NTag>`
      - `<NProgress type="line" :percentage="progressPct" :indicator-placement="'inside'" style="width: 300px" />`
      - `<NText depth="3" style="font-size: 12px">run_id: {{ runId?.slice(0, 8) }}...</NText>`
  - 下部 `NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen"`:
    - `<NGi :span="1">` `NCard title="Agents"`:
      - `<NEmpty v-if="agents.length === 0" description="暂无数据,启动进化后显示" />`
      - `<NDataTable v-else :columns="agentsColumns" :data="agents" :bordered="true" :single-line="false" size="small" />`
    - `<NGi :span="1">` `NCard title="进化曲线"`:
      - `<NEmpty v-if="events.filter(e => e.type === 'generation' || e.type === 'cycle').length === 0" description="暂无数据,启动进化后展示" />`
      - `<VChart v-else :option="chartOption" autoresize style="height: 320px" />`
    - `<NGi :span="2">` `NCard title="事件日志"`:
      - `<NEmpty v-if="eventLog.length === 0" description="暂无事件" />`
      - `<pre v-else ref="logPre" class="event-log">{{ eventLog.join('\n') }}</pre>`
  - 底部 `NCard v-if="finalResult" title="最终结果"`:
    - atlas(finalResult.best_agent 存在):`<NDescriptions :column="2" bordered>`
      - `<NDescriptionsItem label="最佳 Agent">{{ finalResult.best_agent.name }} ({{ finalResult.best_agent.style }})</NDescriptionsItem>`
      - `<NDescriptionsItem label="评分">{{ finalResult.best_agent.score.toFixed(4) }}</NDescriptionsItem>`
      - `<NDescriptionsItem label="权重">{{ (finalResult.best_agent.weight * 100).toFixed(2) }}%</NDescriptionsItem>`
      - `<NDescriptionsItem v-for="[name, w] in Object.entries(finalResult.final_weights || {})" :key="name" :label="`${name} 权重`">{{ (w * 100).toFixed(2) }}%</NDescriptionsItem>`
    - gepa(finalResult.experiment_count 存在):`<NDescriptions :column="2" bordered>`
      - `<NDescriptionsItem label="实验总数">{{ finalResult.experiment_count }}</NDescriptionsItem>`
      - `<NDescriptionsItem label="元反思次数">{{ finalResult.meta_count }}</NDescriptionsItem>`
      - `<NDescriptionsItem label="盲点发现" :span="2">`
        - `<NSpace vertical><NText v-for="(bs, i) in finalResult.blind_spots" :key="i">• {{ bs }}</NText></NSpace>`
      - `</NDescriptionsItem>`

- [x] 2.12 实现 `<style scoped>`:
  - `.event-log { background: var(--n-color); padding: 12px; max-height: 240px; overflow: auto; font-family: ui-monospace, 'Cascadia Code', Menlo, monospace; font-size: 12px; line-height: 1.5; margin: 0; border-radius: 4px; white-space: pre-wrap; }`

- [x] 2.13 实现 computed 辅助:
  - `statusTagType`:idle/stopped/disconnected → 'default';running → 'info';completed → 'success';failed → 'error'
  - `statusText`:idle → '未启动';running → '运行中';completed → '已完成';failed → '失败';stopped → '已停止';disconnected → '已断开'
  - `progressPct`:`totalGen.value > 0 ? Math.round(currentGen.value / totalGen.value * 100) : 0`

## 3. 验证

- [x] 3.1 `cd frontend && pnpm typecheck` 无错误
- [x] 3.2 `cd frontend && pnpm dev` 启动成功,浏览器访问 `/evolve` 不白屏(手动)
- [x] 3.3 后端启动时,前端 `/evolve` 显示表单 + symbol 下拉含 `ETHUSDT 5m 7d`(手动)
- [x] 3.4 选 atlas + ETHUSDT + 10 代 → 启动 → 进度条更新 + agents 表显示 4 行 + 曲线显示 4 条线 + 事件日志累积(手动)
- [x] 3.5 进化完成 → status tag「已完成」绿 + 最终结果卡显示 best_agent(手动)
- [x] 3.6 选 gepa + ETHUSDT + 6 周期 → 启动 → 事件日志显示 cycle + meta_reflection 行(手动)
- [x] 3.7 启动长 run(gepa 100 周期 on 30d 数据)→ 点停止 → status tag「已停止」灰(手动)
- [x] 3.8 离开 `/evolve` 切到 `/backtest` → 控制台无 WS 错误(onUnmounted 清理生效,手动)
