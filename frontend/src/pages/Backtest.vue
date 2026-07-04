<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NEmpty,
  NGi,
  NGrid,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NSelect,
  NSpace,
  NSpin,
  NStatistic,
  NTag,
  useMessage,
  type DataTableColumns,
  type SelectOption,
} from 'naive-ui'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import {
  fetchSymbolList,
  runBacktest as runBacktestAPI,
  type BacktestRequest,
  type BacktestResponse,
  type SymbolInfo,
  type Trade,
} from '@/api/backtest'
import { fetchStrategyList, type StrategySummary } from '@/api/strategy'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent])

const message = useMessage()
const symbols = ref<SymbolInfo[]>([])
const strategies = ref<StrategySummary[]>([])
const formLoading = ref(false)
const selectedSymbol = ref<string | null>(null)
const selectedStrategy = ref<string | null>(null)
const dateRange = ref<[number, number] | null>(null)
const running = ref(false)
const result = ref<BacktestResponse | null>(null)

const symbolOptions = computed<SelectOption[]>(() =>
  symbols.value.map((s) => ({
    label: `${s.symbol} ${s.interval} ${s.days}d`,
    value: `${s.symbol}|${s.interval}|${s.days}`,
  })),
)
const strategyOptions = computed<SelectOption[]>(() =>
  strategies.value.map((s) => ({ label: s.name, value: s.name })),
)
const runBtnDisabled = computed(
  () =>
    formLoading.value ||
    !selectedSymbol.value ||
    !selectedStrategy.value ||
    running.value,
)

onMounted(async () => {
  formLoading.value = true
  try {
    const [symRes, stratRes] = await Promise.all([
      fetchSymbolList(),
      fetchStrategyList(),
    ])
    symbols.value = symRes.symbols
    strategies.value = stratRes.strategies
  } catch (err) {
    const e = err as { code: string; message: string }
    message.error(`表单加载失败：${e.message}`)
  } finally {
    formLoading.value = false
  }
})

async function runBacktest(): Promise<void> {
  if (!selectedSymbol.value || !selectedStrategy.value) {
    message.warning('请选择交易对与策略')
    return
  }
  const [symbol, interval, daysStr] = selectedSymbol.value.split('|')
  const req: BacktestRequest = {
    symbol,
    interval,
    days: parseInt(daysStr, 10),
    strategy: selectedStrategy.value,
  }
  if (dateRange.value) {
    req.start = new Date(dateRange.value[0]).toISOString()
    req.end = new Date(dateRange.value[1]).toISOString()
  }
  running.value = true
  result.value = null
  try {
    result.value = await runBacktestAPI(req)
  } catch (err) {
    const e = err as { code: string; message: string }
    message.error(`回测失败：${e.message}`)
  } finally {
    running.value = false
  }
}

const metricsDisplay = computed<
  { label: string; value: string; positive: boolean | null }[] | null
>(() => {
  if (!result.value) return null
  const m = result.value.metrics
  return [
    {
      label: '总收益',
      value: (m.total_return * 100).toFixed(2) + '%',
      positive: m.total_return >= 0,
    },
    {
      label: '年化收益',
      value: (m.annualized_return * 100).toFixed(2) + '%',
      positive: m.annualized_return >= 0,
    },
    {
      label: '年化波动',
      value: (m.annualized_vol * 100).toFixed(2) + '%',
      positive: null,
    },
    {
      label: 'Sharpe',
      value: m.sharpe_ratio.toFixed(2),
      positive: m.sharpe_ratio >= 0,
    },
    {
      label: '最大回撤',
      value: (m.max_drawdown * 100).toFixed(2) + '%',
      positive: false,
    },
    {
      label: '胜率',
      value: (m.win_rate * 100).toFixed(2) + '%',
      positive: null,
    },
  ]
})

const chartOption = computed(() => {
  if (!result.value) return {}
  return {
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'time', name: '时间' },
    yAxis: { type: 'value', name: 'Equity ($)' },
    series: [
      {
        type: 'line',
        showSymbol: false,
        data: result.value.equity_curve.map((p) => [p.timestamp, p.equity]),
        lineStyle: { width: 1.5 },
      },
    ],
    grid: { left: 60, right: 20, top: 30, bottom: 40 },
  }
})

const tradeColumns: DataTableColumns<Trade> = [
  { title: 'Step', key: 'step', width: 80 },
  {
    title: '时间',
    key: 'timestamp',
    width: 200,
    render: (row) =>
      new Date(row.timestamp).toISOString().replace('T', ' ').slice(0, 19),
  },
  {
    title: '类型',
    key: 'type',
    width: 120,
    render: (row) => {
      const color: 'info' | 'warning' | 'default' = ['buy', 'sell'].includes(
        row.type,
      )
        ? 'info'
        : ['sell_short', 'buy_cover'].includes(row.type)
          ? 'warning'
          : 'default'
      return h(
        NTag,
        { type: color, size: 'small' },
        { default: () => row.type },
      )
    },
  },
  {
    title: '价格',
    key: 'price',
    width: 120,
    render: (row) => row.price.toFixed(2),
  },
  {
    title: 'PnL',
    key: 'pnl',
    width: 120,
    render: (row) => {
      if (row.pnl === null || row.pnl === undefined) return '—'
      const color = row.pnl >= 0 ? '#18a058' : '#d03050'
      return h('span', { style: `color: ${color}` }, row.pnl.toFixed(2))
    },
  },
]
</script>

<template>
  <NLayout has-sider style="height: calc(100vh - 48px)">
    <NLayoutSider bordered :width="320" :native-scrollbar="false">
      <div style="padding: 16px">
        <div style="font-weight: 600; margin-bottom: 12px">回测配置</div>
        <NSpace vertical>
          <div>
            <div
              style="margin-bottom: 4px; font-size: 12px; color: var(--n-text-color-3)"
            >
              交易对
            </div>
            <NSelect
              :options="symbolOptions"
              :value="selectedSymbol"
              :loading="formLoading"
              placeholder="选择交易对"
              :filterable="true"
              @update:value="(v: string | null) => (selectedSymbol = v)"
            />
          </div>
          <div>
            <div
              style="margin-bottom: 4px; font-size: 12px; color: var(--n-text-color-3)"
            >
              策略
            </div>
            <NSelect
              :options="strategyOptions"
              :value="selectedStrategy"
              :loading="formLoading"
              placeholder="选择策略"
              :filterable="true"
              @update:value="(v: string | null) => (selectedStrategy = v)"
            />
          </div>
          <div>
            <div
              style="margin-bottom: 4px; font-size: 12px; color: var(--n-text-color-3)"
            >
              时间段（可选）
            </div>
            <NDatePicker
              v-model:value="dateRange"
              type="datetimerange"
              clearable
            />
          </div>
          <NButton
            type="primary"
            :loading="running"
            :disabled="runBtnDisabled"
            block
            @click="runBacktest"
          >跑回测</NButton>
        </NSpace>
      </div>
    </NLayoutSider>
    <NLayoutContent style="padding: 16px">
      <NSpin
        v-if="running"
        size="large"
        style="display: flex; justify-content: center; padding: 80px"
      />
      <NEmpty
        v-else-if="!result"
        description="尚未跑回测"
        style="padding: 80px"
      />
      <div v-else>
        <NGrid :cols="6" :x-gap="12">
          <NGi
            v-for="m in metricsDisplay"
            :key="m.label"
          >
            <NCard size="small">
              <NStatistic
                :label="m.label"
                :value="m.value"
                :style="{
                  color:
                    m.positive === true
                      ? '#18a058'
                      : m.positive === false
                        ? '#d03050'
                        : undefined,
                }"
              />
            </NCard>
          </NGi>
        </NGrid>

        <NCard size="small" title="收益曲线" style="margin-top: 12px">
          <VChart :option="chartOption" autoresize style="height: 320px" />
        </NCard>

        <NCard size="small" title="交易明细" style="margin-top: 12px">
          <NDataTable
            :columns="tradeColumns"
            :data="result.trades"
            :pagination="false"
            :max-height="400"
            size="small"
          />
        </NCard>
      </div>
    </NLayoutContent>
  </NLayout>
</template>
