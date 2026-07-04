<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  NText,
  useMessage,
  type SelectOption,
} from 'naive-ui'
import {
  fetchExchanges as fetchExchangesAPI,
  fetchLiveLogs,
  fetchLiveStatus,
  startLive,
  stopLive,
  type ExchangeInfo,
  type LiveStatus,
  type LogResponse,
  type StartRequest,
} from '@/api/live'

const message = useMessage()
const exchanges = ref<ExchangeInfo[]>([])
const selectedExchange = ref<string | null>(null)
const status = ref<LiveStatus | null>(null)
const logs = ref<LogResponse | null>(null)
const loading = ref(true)

const modalVisible = ref(false)
const modalExchange = ref<string>('')
const form = ref<StartRequest>({
  exchange: '',
  symbol: 'BTCUSDT',
  mode: 'demo',
  capital: 100,
  leverage: 1,
})
const starting = ref(false)
const stopping = ref<string | null>(null)

let pollSeq = 0
let exchangesTimer: number | null = null
let detailTimer: number | null = null

const modeOptions: SelectOption[] = [
  { label: 'demo 模拟盘', value: 'demo' },
  { label: 'live 真实盘', value: 'live' },
]

async function fetchExchanges(): Promise<void> {
  const seq = ++pollSeq
  try {
    const res = await fetchExchangesAPI()
    if (seq !== pollSeq) return
    exchanges.value = res.exchanges
    loading.value = false
  } catch (e) {
    if (seq !== pollSeq) return
    console.warn('poll exchanges failed', e)
  }
}

async function fetchDetail(): Promise<void> {
  const exchange = selectedExchange.value
  if (!exchange) return
  const seq = ++pollSeq
  const [statusRes, logsRes] = await Promise.allSettled([
    fetchLiveStatus(exchange),
    fetchLiveLogs(exchange, 200),
  ])
  if (seq !== pollSeq) return
  if (statusRes.status === 'fulfilled') {
    status.value = statusRes.value
  } else {
    console.warn('poll status failed', statusRes.reason)
  }
  if (logsRes.status === 'fulfilled') {
    logs.value = logsRes.value
  } else {
    console.warn('poll logs failed', logsRes.reason)
  }
}

function onStart(exchange: string): void {
  modalExchange.value = exchange
  form.value = {
    exchange,
    symbol: 'BTCUSDT',
    mode: 'demo',
    capital: 100,
    leverage: 1,
  }
  modalVisible.value = true
}

async function confirmStart(): Promise<void> {
  if (!form.value.symbol.trim()) {
    message.warning('请输入 symbol')
    return
  }
  starting.value = true
  try {
    const res = await startLive(form.value)
    modalVisible.value = false
    message.success(`已启动 ${res.exchange}(PID ${res.pid})`)
    await fetchExchanges()
  } catch (e) {
    const err = e as { code: string; message: string }
    if (err.code === 'already_running') {
      message.warning(`${form.value.exchange} 已在运行,请先停止`)
    } else if (err.code === 'network') {
      message.error('启动失败:网络错误')
    } else {
      message.error(`启动失败:${err.message}`)
    }
  } finally {
    starting.value = false
  }
}

async function onStop(exchange: string): Promise<void> {
  stopping.value = exchange
  try {
    await stopLive({ exchange })
    message.success(`已停止 ${exchange}`)
    await fetchExchanges()
  } catch (e) {
    const err = e as { code: string; message: string }
    if (err.code === 'not_running') {
      message.warning(`${exchange} 未在运行`)
      await fetchExchanges()
    } else if (err.code === 'network') {
      message.error('停止失败:网络错误')
    } else {
      message.error(`停止失败:${err.message}`)
    }
  } finally {
    stopping.value = null
  }
}

async function selectExchange(exchange: string): Promise<void> {
  selectedExchange.value = exchange
  status.value = null
  logs.value = null
  await fetchDetail()
}

onMounted(async () => {
  await fetchExchanges()
  exchangesTimer = window.setInterval(fetchExchanges, 2000)
  detailTimer = window.setInterval(fetchDetail, 2000)
})

onUnmounted(() => {
  if (exchangesTimer !== null) clearInterval(exchangesTimer)
  if (detailTimer !== null) clearInterval(detailTimer)
})
</script>

<template>
  <NCard title="实盘监控">
    <NSpin v-if="loading" />
    <NGrid v-else :cols="3" :x-gap="16" :y-gap="16" responsive="screen">
      <NGi v-for="ex in exchanges" :key="ex.name" :span="1">
        <NCard
          :title="ex.name"
          hoverable
          :class="{ selected: ex.name === selectedExchange }"
          @click="selectExchange(ex.name)"
        >
          <NSpace align="center" justify="space-between">
            <NTag :type="ex.status === 'running' ? 'success' : 'default'" size="small">
              {{ ex.status }}
            </NTag>
            <NText depth="3" style="font-size: 12px">
              {{ ex.status === 'running' ? `PID: ${ex.pid}` : 'PID: —' }}
            </NText>
          </NSpace>
          <NText depth="3" style="font-size: 12px; display: block; margin-top: 8px">
            {{ ex.script }}
          </NText>
          <NSpace style="margin-top: 12px" @click.stop>
            <NButton
              v-if="ex.status === 'running'"
              type="error"
              size="small"
              :loading="stopping === ex.name"
              @click="onStop(ex.name)"
            >
              停止
            </NButton>
            <NButton v-else type="primary" size="small" @click="onStart(ex.name)">
              启动
            </NButton>
            <NButton size="small" quaternary @click="selectExchange(ex.name)">
              查看
            </NButton>
          </NSpace>
        </NCard>
      </NGi>
    </NGrid>
  </NCard>

  <NCard v-if="selectedExchange" title="实盘状态" style="margin-top: 16px">
    <NSpin v-if="!status" />
    <NEmpty v-else-if="status.state === null" description="暂无状态数据,实盘启动后稍候" />
    <NDescriptions v-else :column="2" bordered>
      <NDescriptionsItem
        v-for="[k, v] in Object.entries(status.state)"
        :key="k"
        :label="k"
      >
        {{ typeof v === 'object' ? JSON.stringify(v) : v }}
      </NDescriptionsItem>
    </NDescriptions>
    <NText depth="3" style="font-size: 12px; display: block; margin-top: 8px">
      更新时间:{{ status?.updated_at ?? '—' }}
    </NText>
  </NCard>

  <NCard v-if="selectedExchange" title="实盘日志" style="margin-top: 16px">
    <template #header-extra>
      <NText depth="3" style="font-size: 12px">共 {{ logs?.total_lines ?? 0 }} 行</NText>
    </template>
    <NSpin v-if="!logs" />
    <NEmpty v-else-if="logs.lines.length === 0" description="暂无日志" />
    <pre v-else class="log-pre">{{ logs.lines.join('\n') }}</pre>
  </NCard>

  <NModal
    v-model:show="modalVisible"
    preset="card"
    :title="`启动 ${modalExchange} 实盘`"
    style="max-width: 480px"
  >
    <NForm :model="form" label-placement="left" label-width="80">
      <NFormItem label="Symbol">
        <NInput v-model:value="form.symbol" placeholder="如 BTCUSDT" />
      </NFormItem>
      <NFormItem label="模式">
        <NSelect v-model:value="form.mode" :options="modeOptions" />
      </NFormItem>
      <NFormItem label="资金">
        <NInputNumber
          v-model:value="form.capital"
          :min="1"
          :step="10"
          style="width: 100%"
        />
      </NFormItem>
      <NFormItem label="杠杆">
        <NInputNumber
          v-model:value="form.leverage"
          :min="1"
          :max="100"
          :step="1"
          style="width: 100%"
        />
      </NFormItem>
    </NForm>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="modalVisible = false" :disabled="starting">取消</NButton>
        <NButton type="primary" :loading="starting" @click="confirmStart">
          确定
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.selected {
  border-color: var(--n-color-target);
}
.log-pre {
  background: var(--n-color);
  padding: 12px;
  max-height: 400px;
  overflow: auto;
  font-family: ui-monospace, 'Cascadia Code', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
  border-radius: 4px;
}
</style>
