## 1. API 客户端封装

- [x] 1.1 创建 `frontend/src/api/live.ts`:
  - `import client from './client'`
  - `export interface ExchangeInfo { name: string; status: string; pid: number | null; script: string; state_file: string; log_file: string }`
  - `export interface ExchangeListResponse { exchanges: ExchangeInfo[]; total: number }`
  - `export interface StartRequest { exchange: string; symbol: string; mode: string; capital: number; leverage: number }`
  - `export interface StartResponse { exchange: string; pid: number; status: string }`
  - `export interface StopRequest { exchange: string }`
  - `export interface StopResponse { exchange: string; status: string }`
  - `export type LiveState = Record<string, unknown> | null`
  - `export interface LiveStatus { exchange: string; running: boolean; state: LiveState; updated_at: string | null }`
  - `export interface LogResponse { exchange: string; lines: string[]; total_lines: number }`
  - `export async function fetchExchanges(): Promise<ExchangeListResponse>` → `client.get('/live/exchanges')`
  - `export async function startLive(req: StartRequest): Promise<StartResponse>` → `client.post('/live/start', req)`
  - `export async function stopLive(req: StopRequest): Promise<StopResponse>` → `client.post('/live/stop', req)`
  - `export async function fetchLiveStatus(exchange: string): Promise<LiveStatus>` → `client.get(\`/live/status/${exchange}\`)`
  - `export async function fetchLiveLogs(exchange: string, tail = 200): Promise<LogResponse>` → `client.get(\`/live/logs/${exchange}?tail=${tail}\`)`

## 2. Live.vue 页面重写

- [x] 2.1 重写 `frontend/src/pages/Live.vue` `<script setup lang="ts">`:
  - imports: `ref / onMounted / onUnmounted / computed` from vue
  - naive-ui: `NCard / NGrid / NGi / NTag / NButton / NSpace / NSpin / NEmpty / NModal / NForm / NFormItem / NInput / NInputNumber / NSelect / NDescriptions / NDescriptionsItem / NText / useMessage`
  - 从 `@/api/live` 导入 5 个函数 + 类型
  - state:
    - `exchanges = ref<ExchangeInfo[]>([])`
    - `selectedExchange = ref<string | null>(null)`
    - `status = ref<LiveStatus | null>(null)`
    - `logs = ref<LogResponse | null>(null)`
    - `loading = ref(false)`(首次加载)
    - `modalVisible = ref(false)`
    - `modalExchange = ref<string>('')`(记录是哪个交易所弹的 Modal)
    - `form = ref<StartRequest>({ exchange: '', symbol: 'BTCUSDT', mode: 'demo', capital: 100, leverage: 1 })`
    - `starting = ref(false)`(Modal 确定按钮 loading)
    - `stopping = ref<string | null>(null)`(记录正在停止的 exchange,用于按钮 loading)
    - `pollSeq = ref(0)`(轮询竞态序号)
    - `exchangesTimer / detailTimer = ref<number | null>(null)`(interval id)
  - mode 选项:`const modeOptions = [{label:'demo 模拟盘', value:'demo'}, {label:'live 真实盘', value:'live'}]`

- [x] 2.2 实现 `fetchExchanges`(供 onMounted + interval 调用):
  - `const seq = ++pollSeq.value`
  - `const res = await fetchExchanges()`
  - `if (seq !== pollSeq.value) return`(过期丢弃)
  - `exchanges.value = res.exchanges; loading.value = false`

- [x] 2.3 实现 `fetchDetail`(拉 status + logs):
  - 若 `selectedExchange.value` 为 null → return
  - `const seq = ++pollSeq.value`
  - `const [statusRes, logsRes] = await Promise.allSettled([fetchLiveStatus(selected), fetchLiveLogs(selected, 200)])`
  - `if (seq !== pollSeq.value) return`
  - statusRes fulfilled → `status.value = statusRes.value`,rejected → `console.warn` + 不动 status(或置 null)
  - logsRes 同理

- [x] 2.4 实现 `onStart(exchange: string)`:
  - `modalExchange.value = exchange`
  - `form.value = { exchange, symbol: 'BTCUSDT', mode: 'demo', capital: 100, leverage: 1 }`(重置默认)
  - `modalVisible.value = true`

- [x] 2.5 实现 `confirmStart()`:
  - 校验 `form.value.symbol` 非空,空则 message warning `请输入 symbol`;capital/leverage 由 NInputNumber 控制 ≥1 不必额外校验
  - `starting.value = true`
  - try `const res = await startLive(form.value)`:
    - `modalVisible.value = false`
    - `message.success(\`已启动 ${res.exchange}(PID ${res.pid})\`)`
    - `await fetchExchanges()`(立即刷新,不等 interval)
  - catch `e`:
    - `code === 'already_running'` → `message.warning(\`${form.value.exchange} 已在运行,请先停止\`)`
    - `code === 'network'` → `message.error('启动失败:网络错误')`
    - 其他 → `message.error(\`启动失败:${e.message}\`)`
  - finally `starting.value = false`

- [x] 2.6 实现 `onStop(exchange: string)`:
  - `stopping.value = exchange`
  - try `await stopLive({ exchange })`:
    - `message.success(\`已停止 ${exchange}\`)`
    - `await fetchExchanges()`
  - catch `e`:
    - `code === 'not_running'` → `message.warning(\`${exchange} 未在运行\`)` + `await fetchExchanges()`(刷新状态)
    - `code === 'network'` → `message.error('停止失败:网络错误')`
    - 其他 → `message.error(\`停止失败:${e.message}\`)`
  - finally `stopping.value = null`

- [x] 2.7 实现 `selectExchange(exchange: string)`:
  - `selectedExchange.value = exchange`
  - `status.value = null; logs.value = null`
  - `await fetchDetail()`(立即拉一次,不等 interval)

- [x] 2.8 实现 `onMounted`:
  - `loading.value = true`
  - `await fetchExchanges()`
  - `exchangesTimer.value = window.setInterval(fetchExchanges, 2000)`
  - `detailTimer.value = window.setInterval(fetchDetail, 2000)`

- [x] 2.9 实现 `onUnmounted`:
  - `if (exchangesTimer.value) clearInterval(exchangesTimer.value)`
  - `if (detailTimer.value) clearInterval(detailTimer.value)`

- [x] 2.10 实现 `<template>`:
  - 顶部 `NCard title="实盘监控"`:
    - `<NGrid :cols="3" :x-gap="16" :y-gap="16" responsive="screen">`
      - `<NGi v-for="ex in exchanges" :key="ex.name" :span="1">`:
        - `<NCard :title="ex.name" hoverable :bordered="true" :class="{ selected: ex.name === selectedExchange }" @click="selectExchange(ex.name)">`:
          - `<NSpace align="center" justify="space-between">`:
            - `<NTag :type="ex.status === 'running' ? 'success' : 'default'">{{ ex.status }}</NTag>`
            - `<NText depth="3" style="font-size: 12px">{{ ex.status === 'running' ? \`PID: ${ex.pid}\` : 'PID: —' }}</NText>`
          - `<NText depth="3" style="font-size: 12px; display: block; margin-top: 8px">{{ ex.script }}</NText>`
          - `<NSpace style="margin-top: 12px" @click.stop>`(阻止冒泡到卡片点击):
            - `<NButton v-if="ex.status === 'running'" type="error" size="small" :loading="stopping === ex.name" @click="onStop(ex.name)">停止</NButton>`
            - `<NButton v-else type="primary" size="small" @click="onStart(ex.name)">启动</NButton>`
            - `<NButton size="small" quaternary @click="selectExchange(ex.name)">查看</NButton>`
  - 中部「状态」`NCard title="实盘状态"`(selectedExchange 不为空时显示):
    - `<NSpin v-if="!status" />`
    - `<NEmpty v-else-if="status.state === null" description="暂无状态数据,实盘启动后稍候" />`
    - `<NDescriptions v-else :column="2" bordered>`:
      - `<NDescriptionsItem v-for="[k, v] in Object.entries(status.state)" :key="k" :label="k">{{ typeof v === 'object' ? JSON.stringify(v) : v }}</NDescriptionsItem>`
    - 底部 `<NText depth="3" style="font-size: 12px">更新时间:{{ status.updated_at ?? '—' }}</NText>`
  - 底部「日志」`NCard title="实盘日志"`(selectedExchange 不为空时显示):
    - header extra:`<NText depth="3" style="font-size: 12px">共 {{ logs?.total_lines ?? 0 }} 行</NText>`
    - `<NSpin v-if="!logs" />`
    - `<NEmpty v-else-if="logs.lines.length === 0" description="暂无日志" />`
    - `<pre v-else class="log-pre">{{ logs.lines.join('\n') }}</pre>`
  - 启动 Modal:
    - `<NModal v-model:show="modalVisible" preset="card" :title="\`启动 ${modalExchange} 实盘\`" style="max-width: 480px">`:
      - `<NForm :model="form" label-placement="left" label-width="80">`:
        - `<NFormItem label="Symbol"><NInput v-model:value="form.symbol" placeholder="如 BTCUSDT" /></NFormItem>`
        - `<NFormItem label="模式"><NSelect v-model:value="form.mode" :options="modeOptions" /></NFormItem>`
        - `<NFormItem label="资金"><NInputNumber v-model:value="form.capital" :min="1" :step="10" style="width: 100%" /></NFormItem>`
        - `<NFormItem label="杠杆"><NInputNumber v-model:value="form.leverage" :min="1" :max="100" :step="1" style="width: 100%" /></NFormItem>`
      - `<template #footer>`:
        - `<NSpace justify="end">`:
          - `<NButton @click="modalVisible = false" :disabled="starting">取消</NButton>`
          - `<NButton type="primary" :loading="starting" @click="confirmStart">确定</NButton>`

- [x] 2.11 实现 `<style scoped>`:
  - `.selected { border-color: var(--n-color-target) }`(选中卡片高亮)
  - `.log-pre { background: var(--n-color); padding: 12px; max-height: 400px; overflow: auto; font-family: ui-monospace, 'Cascadia Code', Menlo, monospace; font-size: 12px; line-height: 1.5; margin: 0; border-radius: 4px }`

## 3. 验证

- [x] 3.1 `cd frontend && pnpm typecheck` 无错误
- [x] 3.2 `cd frontend && pnpm dev` 启动成功,浏览器访问 `http://localhost:5173/live` 不白屏(手动目视)
- [x] 3.3 后端启动时(`cd backend && uv run uvicorn app.main:app --port 8000`),前端 `/live` 显示 3 张卡片全 stopped(手动目视)
- [x] 3.4 点 binance「启动」→ Modal 弹出 → 确定 → success message 显示 + 卡片变 running(子进程会立刻退出,2s 后变 stopped,手动目视)
- [x] 3.5 点 binance「停止」(运行中)→ success message + 卡片变 stopped(手动目视)
- [x] 3.6 点 binance「查看」→ 下方状态区 + 日志区显示(无数据时 NEmpty,手动目视)
- [x] 3.7 切换到其他交易所,状态/日志区不闪现旧数据(竞态保护,手动目视)
