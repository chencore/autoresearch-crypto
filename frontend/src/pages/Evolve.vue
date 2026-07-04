<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInputNumber,
  NProgress,
  NSelect,
  NSpace,
  NTag,
  NText,
  useMessage,
  type DataTableColumns,
  type SelectOption,
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'
import { fetchSymbolList, type SymbolInfo } from '@/api/backtest'
import {
  buildEvolveWsUrl,
  startEvolution as startEvolutionAPI,
  type CompletedEvent,
  type EvolveAgentState,
  type EvolveEngine,
  type EvolveEvent,
} from '@/api/evolve'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const message = useMessage()
const symbols = ref<SymbolInfo[]>([])
const engine = ref<EvolveEngine>('atlas')
const selectedSymbol = ref<string | null>(null)
const generations = ref<number>(10)
const evolutionInterval = ref<number>(5)

const runId = ref<string | null>(null)
const status = ref<
  'idle' | 'running' | 'completed' | 'failed' | 'stopped' | 'disconnected'
>('idle')
const currentGen = ref<number>(0)
const totalGen = ref<number>(0)
const agents = ref<EvolveAgentState[]>([])
const events = ref<EvolveEvent[]>([])
const eventLog = ref<string[]>([])
const finalResult = ref<CompletedEvent | null>(null)
const starting = ref(false)
const stopping = ref(false)
const ws = ref<WebSocket | null>(null)
const logPre = ref<HTMLPreElement | null>(null)

const AGENT_NAMES = ['Alpha', 'Beta', 'Gamma', 'Delta']

const engineOptions: SelectOption[] = [
  { label: 'ATLAS 多策略进化', value: 'atlas' },
  { label: 'GEPA 反思式进化', value: 'gepa' },
]

const symbolOptions = computed<SelectOption[]>(() =>
  symbols.value.map((s) => ({
    label: `${s.symbol} ${s.interval} ${s.days}d`,
    value: `${s.symbol}|${s.interval}|${s.days}`,
  })),
)

const statusTagType = computed(() => {
  switch (status.value) {
    case 'running':
      return 'info'
    case 'completed':
      return 'success'
    case 'failed':
      return 'error'
    default:
      return 'default'
  }
})

const statusText = computed(() => {
  switch (status.value) {
    case 'idle':
      return '未启动'
    case 'running':
      return '运行中'
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'stopped':
      return '已停止'
    case 'disconnected':
      return '已断开'
  }
})

const progressPct = computed(() =>
  totalGen.value > 0
    ? Math.round((currentGen.value / totalGen.value) * 100)
    : 0,
)

const chartOption = computed(() => {
  const xSet = new Set<number>()
  const seriesMap: Record<string, Record<number, number | null>> = {}
  for (const name of AGENT_NAMES) {
    seriesMap[name] = {}
  }
  for (const e of events.value) {
    if (e.type === 'generation') {
      xSet.add(e.generation)
      for (const a of e.agents) {
        seriesMap[a.name][e.generation] = a.score
      }
    } else if (e.type === 'cycle') {
      xSet.add(e.cycle)
      for (const a of e.agents) {
        if (a.name === e.agent) {
          seriesMap[a.name][e.cycle] = e.score_after
        } else if (seriesMap[a.name][e.cycle] === undefined) {
          seriesMap[a.name][e.cycle] = null
        }
      }
    }
  }
  const xData = Array.from(xSet).sort((a, b) => a - b)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: AGENT_NAMES },
    xAxis: { type: 'category', data: xData, name: '代/周期' },
    yAxis: { type: 'value', name: 'score' },
    series: AGENT_NAMES.map((name) => ({
      name,
      type: 'line',
      data: xData.map((x) => seriesMap[name][x] ?? null),
      connectNulls: false,
    })),
  }
})

const agentsColumns = computed<DataTableColumns<EvolveAgentState>>(() => [
  { title: '名称', key: 'name', width: 100 },
  { title: '风格', key: 'style', width: 120 },
  {
    title: '评分',
    key: 'score',
    width: 100,
    render: (row) => (engine.value === 'gepa' ? '—' : row.score.toFixed(4)),
  },
  {
    title: '权重',
    key: 'weight',
    width: 100,
    render: (row) => `${(row.weight * 100).toFixed(2)}%`,
  },
  { title: '代数', key: 'generation', width: 80 },
])

function formatTime(): string {
  const d = new Date()
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function appendEvent(event: EvolveEvent): void {
  events.value.push(event)
  if (events.value.length > 200) events.value = events.value.slice(-200)

  let line = ''
  switch (event.type) {
    case 'started':
      line = `[${formatTime()}] 进化启动 (${event.config.engine}, ${event.total} 代/周期)`
      break
    case 'generation':
      line = `[${formatTime()}] 第 ${event.generation}/${event.total} 代: ${event.agents
        .map((a) => `${a.name}=${a.score.toFixed(2)}`)
        .join(' ')}`
      break
    case 'cycle':
      line = `[${formatTime()}] 第 ${event.cycle}/${event.total} 周期 (${event.agent}): ${event.score_before.toFixed(2)} → ${event.score_after.toFixed(2)} ${event.accepted ? '✓' : '✗'}`
      break
    case 'meta_reflection':
      line = `[${formatTime()}] 元反思: ${event.summary.slice(0, 100)}`
      break
    case 'completed':
      if (event.best_agent) {
        line = `[${formatTime()}] 进化完成: 最佳=${event.best_agent.name}`
      } else {
        line = `[${formatTime()}] 进化完成: 实验=${event.experiment_count ?? 0}`
      }
      break
    case 'stopped':
      line = `[${formatTime()}] 用户停止 (第 ${event.generation ?? event.cycle ?? 0} 代/周期)`
      break
    case 'error':
      line = `[${formatTime()}] 错误 ${event.code}: ${event.message}`
      break
  }
  eventLog.value.push(line)
  if (eventLog.value.length > 200) eventLog.value = eventLog.value.slice(-200)

  nextTick(() => {
    if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight
  })
}

function handleWsMessage(event: MessageEvent): void {
  let data: EvolveEvent
  try {
    data = JSON.parse(event.data) as EvolveEvent
  } catch {
    return
  }
  appendEvent(data)
  switch (data.type) {
    case 'started':
      status.value = 'running'
      totalGen.value = data.total
      currentGen.value = 0
      finalResult.value = null
      break
    case 'generation':
      status.value = 'running'
      currentGen.value = data.generation
      agents.value = data.agents
      break
    case 'cycle':
      status.value = 'running'
      currentGen.value = data.cycle
      agents.value = data.agents
      break
    case 'meta_reflection':
      message.info(`第 ${data.cycle} 周期元反思完成`)
      break
    case 'completed':
      status.value = 'completed'
      currentGen.value = totalGen.value
      finalResult.value = data
      closeWs()
      break
    case 'stopped':
      status.value = 'stopped'
      stopping.value = false
      closeWs()
      break
    case 'error':
      status.value = 'failed'
      message.error(`${data.code}: ${data.message}`)
      closeWs()
      break
  }
}

function closeWs(): void {
  if (ws.value) {
    ws.value.onmessage = null
    ws.value.onclose = null
    ws.value.onerror = null
    try {
      ws.value.close()
    } catch {
      // ignore close errors
    }
    ws.value = null
  }
}

async function startEvolution(): Promise<void> {
  if (!selectedSymbol.value) {
    message.warning('请选择交易对')
    return
  }
  starting.value = true
  try {
    const [symbol, interval, daysStr] = selectedSymbol.value.split('|')
    const req = {
      engine: engine.value,
      symbol,
      interval,
      days: Number(daysStr),
      generations: generations.value,
      ...(engine.value === 'atlas'
        ? { evolution_interval: evolutionInterval.value }
        : {}),
    }
    const res = await startEvolutionAPI(req)
    runId.value = res.run_id
    status.value = 'running'
    currentGen.value = 0
    totalGen.value = generations.value
    events.value = []
    eventLog.value = []
    agents.value = []
    finalResult.value = null
    const url = buildEvolveWsUrl(res.run_id)
    ws.value = new WebSocket(url)
    ws.value.onmessage = handleWsMessage
    ws.value.onerror = () => console.warn('WS error')
    ws.value.onclose = () => {
      if (status.value === 'running') {
        status.value = 'disconnected'
        message.warning('WebSocket 连接断开')
      }
      ws.value = null
    }
  } catch (e) {
    const err = e as { code: string; message: string }
    if (err.code === 'network') {
      message.error('启动失败:网络错误')
    } else {
      message.error(`启动失败:${err.message}`)
    }
    status.value = 'idle'
  } finally {
    starting.value = false
  }
}

function stopEvolution(): void {
  if (!ws.value || ws.value.readyState !== WebSocket.OPEN) return
  stopping.value = true
  ws.value.send(JSON.stringify({ action: 'stop' }))
}

onMounted(async () => {
  try {
    const res = await fetchSymbolList()
    symbols.value = res.symbols
  } catch (e) {
    const err = e as { code: string; message: string }
    message.error(`加载交易对失败:${err.message}`)
  }
})

onUnmounted(() => {
  closeWs()
})
</script>

<template>
  <NCard title="参数调优">
    <NForm label-placement="left" label-width="100" inline>
      <NFormItem label="引擎">
        <NSelect
          v-model:value="engine"
          :options="engineOptions"
          style="width: 200px"
        />
      </NFormItem>
      <NFormItem label="交易对">
        <NSelect
          v-model:value="selectedSymbol"
          :options="symbolOptions"
          placeholder="选择交易对"
          style="width: 240px"
          filterable
        />
      </NFormItem>
      <NFormItem label="代数">
        <NInputNumber
          v-model:value="generations"
          :min="1"
          :max="100"
          :step="1"
          style="width: 120px"
        />
      </NFormItem>
      <NFormItem v-if="engine === 'atlas'" label="进化间隔">
        <NInputNumber
          v-model:value="evolutionInterval"
          :min="1"
          :max="20"
          :step="1"
          style="width: 120px"
        />
      </NFormItem>
      <NFormItem>
        <NSpace>
          <NButton
            type="primary"
            :loading="starting"
            :disabled="!selectedSymbol || status === 'running'"
            @click="startEvolution"
          >
            启动
          </NButton>
          <NButton
            type="error"
            :loading="stopping"
            :disabled="status !== 'running'"
            @click="stopEvolution"
          >
            停止
          </NButton>
        </NSpace>
      </NFormItem>
    </NForm>
  </NCard>

  <NCard v-if="runId" title="运行状态" style="margin-top: 16px">
    <NSpace align="center">
      <NTag :type="statusTagType">{{ statusText }}</NTag>
      <NProgress
        type="line"
        :percentage="progressPct"
        indicator-placement="inside"
        style="width: 300px"
      />
      <NText depth="3" style="font-size: 12px">
        run_id: {{ runId.slice(0, 8) }}... · {{ currentGen }}/{{ totalGen }}
      </NText>
    </NSpace>
  </NCard>

  <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" style="margin-top: 16px">
    <NGi :span="1">
      <NCard title="Agents">
        <NEmpty v-if="agents.length === 0" description="暂无数据,启动进化后显示" />
        <NDataTable
          v-else
          :columns="agentsColumns"
          :data="agents"
          :bordered="true"
          :single-line="false"
          size="small"
        />
      </NCard>
    </NGi>
    <NGi :span="1">
      <NCard title="进化曲线">
        <NEmpty
          v-if="!events.some((e) => e.type === 'generation' || e.type === 'cycle')"
          description="暂无数据,启动进化后展示"
        />
        <VChart
          v-else
          :option="chartOption"
          autoresize
          style="height: 320px"
        />
      </NCard>
    </NGi>
    <NGi :span="2">
      <NCard title="事件日志">
        <NEmpty v-if="eventLog.length === 0" description="暂无事件" />
        <pre v-else ref="logPre" class="event-log">{{ eventLog.join('\n') }}</pre>
      </NCard>
    </NGi>
  </NGrid>

  <NCard v-if="finalResult" title="最终结果" style="margin-top: 16px">
    <NDescriptions v-if="finalResult.best_agent" :column="2" bordered>
      <NDescriptionsItem label="最佳 Agent">
        {{ finalResult.best_agent.name }} ({{ finalResult.best_agent.style }})
      </NDescriptionsItem>
      <NDescriptionsItem label="评分">
        {{ finalResult.best_agent.score.toFixed(4) }}
      </NDescriptionsItem>
      <NDescriptionsItem label="权重">
        {{ (finalResult.best_agent.weight * 100).toFixed(2) }}%
      </NDescriptionsItem>
      <NDescriptionsItem
        v-for="[name, w] in Object.entries(finalResult.final_weights || {})"
        :key="name"
        :label="`${name} 权重`"
      >
        {{ (w * 100).toFixed(2) }}%
      </NDescriptionsItem>
    </NDescriptions>
    <NDescriptions v-else :column="2" bordered>
      <NDescriptionsItem label="实验总数">
        {{ finalResult.experiment_count ?? 0 }}
      </NDescriptionsItem>
      <NDescriptionsItem label="元反思次数">
        {{ finalResult.meta_count ?? 0 }}
      </NDescriptionsItem>
      <NDescriptionsItem label="盲点发现" :span="2">
        <NSpace vertical>
          <NText
            v-for="(bs, i) in finalResult.blind_spots || []"
            :key="i"
          >• {{ bs }}</NText>
        </NSpace>
      </NDescriptionsItem>
    </NDescriptions>
  </NCard>
</template>

<style scoped>
.event-log {
  background: var(--n-color);
  padding: 12px;
  max-height: 240px;
  overflow: auto;
  font-family: ui-monospace, 'Cascadia Code', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
  border-radius: 4px;
  white-space: pre-wrap;
}
</style>
