<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NTag,
  NText,
  useMessage,
  type DataTableColumns,
  type SelectOption,
} from 'naive-ui'
import {
  buildDataDownloadWsUrl,
  listFiles,
  startDownload,
  type CompletedEvent,
  type DownloadEvent,
  type DownloadFile,
  type DownloadInterval,
} from '@/api/data-download'

const message = useMessage()

const symbol = ref<string>('')
const interval = ref<DownloadInterval>('5m')
const days = ref<number>(60)
const proxyUrl = ref<string>('')
const force = ref<boolean>(false)

const taskId = ref<string | null>(null)
const status = ref<
  'idle' | 'running' | 'completed' | 'failed' | 'stopped' | 'disconnected'
>('idle')
const eventLog = ref<string[]>([])
const finalResult = ref<CompletedEvent | null>(null)
const files = ref<DownloadFile[]>([])

const starting = ref(false)
const stopping = ref(false)
const ws = ref<WebSocket | null>(null)
const logPre = ref<HTMLPreElement | null>(null)

const intervalOptions: SelectOption[] = [
  { label: '1m', value: '1m' },
  { label: '5m', value: '5m' },
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '4h', value: '4h' },
  { label: '1d', value: '1d' },
]

const statusTagType = computed(() => {
  switch (status.value) {
    case 'running':
      return 'info'
    case 'completed':
      return 'success'
    case 'failed':
      return 'error'
    case 'stopped':
      return 'warning'
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

const startDisabled = computed(
  () => !symbol.value.trim() || status.value === 'running' || starting.value,
)

function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)}MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)}GB`
}

function formatMtime(s: string): string {
  const d = new Date(s)
  if (isNaN(d.getTime())) return s
  return d.toLocaleString()
}

const fileColumns = computed<DataTableColumns<DownloadFile>>(() => [
  { title: '文件名', key: 'filename', minWidth: 240 },
  { title: '交易对', key: 'symbol', width: 100 },
  { title: '周期', key: 'interval', width: 80 },
  { title: '天数', key: 'days', width: 80 },
  {
    title: '大小',
    key: 'size_bytes',
    width: 100,
    render: (row) => formatBytes(row.size_bytes),
  },
  {
    title: '修改时间',
    key: 'mtime',
    width: 200,
    render: (row) => formatMtime(row.mtime),
  },
])

function appendLog(line: string): void {
  eventLog.value.push(line)
  if (eventLog.value.length > 500) eventLog.value = eventLog.value.slice(-300)
  nextTick(() => {
    if (logPre.value) logPre.value.scrollTop = logPre.value.scrollHeight
  })
}

function handleWsMessage(event: MessageEvent): void {
  let data: DownloadEvent
  try {
    data = JSON.parse(event.data) as DownloadEvent
  } catch {
    return
  }
  switch (data.type) {
    case 'started':
      status.value = 'running'
      appendLog(`[启动] symbol=${data.symbol} interval=${data.interval} days=${data.days}`)
      break
    case 'progress':
      appendLog(`[${data.line_number}] ${data.line}`)
      break
    case 'completed':
      status.value = 'completed'
      finalResult.value = data
      appendLog(`[完成] file=${data.file_path} (${data.line_count} 行)`)
      message.success('下载完成')
      closeWs()
      void refreshFiles()
      break
    case 'error':
      status.value = 'failed'
      appendLog(`[错误] ${data.code}: ${data.message}`)
      message.error(`下载失败:${data.message}`)
      closeWs()
      break
    case 'stopped':
      status.value = 'stopped'
      stopping.value = false
      appendLog('[已停止]')
      closeWs()
      void refreshFiles()
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

async function startDownloadTask(): Promise<void> {
  starting.value = true
  try {
    const req = {
      symbol: symbol.value.trim(),
      interval: interval.value,
      days: days.value,
      ...(proxyUrl.value.trim() ? { proxy_url: proxyUrl.value.trim() } : {}),
      force: force.value,
    }
    const res = await startDownload(req)
    taskId.value = res.task_id
    status.value = 'running'
    eventLog.value = []
    finalResult.value = null
    const url = buildDataDownloadWsUrl(res.task_id)
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

function stopDownloadTask(): void {
  if (!ws.value || ws.value.readyState !== WebSocket.OPEN) return
  stopping.value = true
  ws.value.send(JSON.stringify({ action: 'stop' }))
}

async function refreshFiles(): Promise<void> {
  try {
    const res = await listFiles()
    files.value = res.files
  } catch (e) {
    const err = e as { code: string; message: string }
    message.error(`加载文件列表失败:${err.message}`)
  }
}

onMounted(() => {
  void refreshFiles()
})

onUnmounted(() => {
  closeWs()
})
</script>

<template>
  <NCard title="数据下载">
    <NForm label-placement="left" label-width="100" inline>
      <NFormItem label="交易对">
        <NInput
          v-model:value="symbol"
          placeholder="如 ETHUSDT"
          style="width: 200px"
        />
      </NFormItem>
      <NFormItem label="周期">
        <NSelect
          v-model:value="interval"
          :options="intervalOptions"
          style="width: 120px"
        />
      </NFormItem>
      <NFormItem label="天数">
        <NInputNumber
          v-model:value="days"
          :min="1"
          :max="365"
          :step="1"
          style="width: 140px"
        />
      </NFormItem>
      <NFormItem label="代理 URL">
        <NInput
          v-model:value="proxyUrl"
          placeholder="http://127.0.0.1:7890 或 socks5://..."
          style="width: 280px"
        />
      </NFormItem>
      <NFormItem label=" ">
        <NCheckbox v-model:checked="force">强制重新下载(忽略已有文件)</NCheckbox>
      </NFormItem>
      <NFormItem>
        <NSpace>
          <NButton
            type="primary"
            :loading="starting"
            :disabled="startDisabled"
            @click="startDownloadTask"
          >
            启动下载
          </NButton>
          <NButton
            type="error"
            :loading="stopping"
            :disabled="status !== 'running'"
            @click="stopDownloadTask"
          >
            停止
          </NButton>
        </NSpace>
      </NFormItem>
    </NForm>
  </NCard>

  <NCard v-if="taskId" title="运行状态" style="margin-top: 16px">
    <NSpace align="center">
      <NTag :type="statusTagType">{{ statusText }}</NTag>
      <NText depth="3" style="font-size: 12px">
        task_id: {{ taskId.slice(0, 8) }}...
      </NText>
    </NSpace>
  </NCard>

  <NCard title="事件日志" style="margin-top: 16px">
    <NEmpty v-if="eventLog.length === 0" description="暂无事件,启动下载后显示" />
    <pre v-else ref="logPre" class="event-log">{{ eventLog.join('\n') }}</pre>
  </NCard>

  <NCard v-if="finalResult" title="最终结果" style="margin-top: 16px">
    <NDescriptions :column="1" bordered>
      <NDescriptionsItem label="文件路径">
        {{ finalResult.file_path }}
      </NDescriptionsItem>
      <NDescriptionsItem label="行数">
        {{ finalResult.line_count }}
      </NDescriptionsItem>
    </NDescriptions>
  </NCard>

  <NCard title="已下载文件" style="margin-top: 16px">
    <NEmpty v-if="files.length === 0" description="暂无文件" />
    <NDataTable
      v-else
      :columns="fileColumns"
      :data="files"
      :bordered="true"
      :single-line="false"
      size="small"
    />
  </NCard>
</template>

<style scoped>
.event-log {
  background: var(--n-color);
  padding: 12px;
  max-height: 320px;
  overflow: auto;
  font-family: ui-monospace, 'Cascadia Code', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
  border-radius: 4px;
  white-space: pre-wrap;
}
</style>
