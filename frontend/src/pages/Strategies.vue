<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NSpace,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  fetchStrategyDetail,
  fetchStrategyList,
  type ParamDef,
  type StrategyDetail,
  type StrategySummary,
} from '@/api/strategy'

const message = useMessage()
const strategies = ref<StrategySummary[]>([])
const loading = ref(false)

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
  try {
    const res = await fetchStrategyDetail(name)
    if (currentName.value !== name) return
    detail.value = res
  } catch (err) {
    if (currentName.value !== name) return
    const e = err as { code: string; message: string }
    detailError.value = e.code === 'not_found' ? '策略不存在' : e.message
  } finally {
    if (currentName.value === name) detailLoading.value = false
  }
}

function closeDetail(): void {
  drawerOpen.value = false
  currentName.value = null
  detail.value = null
  detailError.value = null
}

const columns: DataTableColumns<StrategySummary> = [
  { title: '类名', key: 'name', width: 280 },
  { title: '模块', key: 'module', ellipsis: { tooltip: true } },
  { title: '参数数量', key: 'params_count', width: 100 },
  {
    title: '实盘状态',
    key: 'live_status',
    width: 120,
    render: (row) =>
      h(NTag, { type: 'default', size: 'small' }, { default: () => row.live_status }),
  },
  {
    title: '操作',
    key: 'operations',
    width: 120,
    render: (row) =>
      h(
        NButton,
        { size: 'small', onClick: () => openDetail(row.name) },
        { default: () => '查看详情' },
      ),
  },
]

const paramColumns: DataTableColumns<ParamDef> = [
  { title: '名称', key: 'name', width: 180 },
  { title: '类型', key: 'type', width: 120 },
  {
    title: '默认值',
    key: 'default',
    render: (row) =>
      h(
        'pre',
        {
          style:
            'white-space: pre-wrap; word-break: break-all; margin: 0; font-size: 12px',
        },
        JSON.stringify(row.default, null, 2) ?? 'null',
      ),
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
    render: (row) =>
      h(
        NTag,
        { type: row.required ? 'error' : 'default', size: 'small' },
        { default: () => (row.required ? '必填' : '可选') },
      ),
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

  <NDrawer
    :show="drawerOpen"
    :width="720"
    placement="right"
    @update:show="(v: boolean) => !v && closeDetail()"
  >
    <NDrawerContent :title="currentName ?? '策略详情'" closable>
      <NSpin v-if="detailLoading" />
      <NEmpty v-else-if="detailError" :description="detailError" />
      <div v-else-if="detail">
        <NDescriptions label-placement="left" :column="1" bordered size="small">
          <NDescriptionsItem label="类名">{{ detail.name }}</NDescriptionsItem>
          <NDescriptionsItem label="模块">{{ detail.module }}</NDescriptionsItem>
          <NDescriptionsItem label="文件">{{ detail.file }}</NDescriptionsItem>
          <NDescriptionsItem label="描述">{{ detail.description || '—' }}</NDescriptionsItem>
        </NDescriptions>

        <div style="margin-top: 16px">
          <div style="font-weight: 600; margin-bottom: 8px">Class docstring</div>
          <pre
            v-if="detail.class_docstring"
            style="background: rgba(128, 128, 128, 0.08); padding: 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-word; font-size: 12px; margin: 0"
          >{{ detail.class_docstring }}</pre>
          <span v-else>—</span>
        </div>

        <div style="margin-top: 16px">
          <div style="font-weight: 600; margin-bottom: 8px">
            参数定义（{{ detail.params.length }}）
          </div>
          <NDataTable
            :columns="paramColumns"
            :data="detail.params"
            :pagination="false"
            size="small"
          />
        </div>

        <div style="margin-top: 16px">
          <div style="font-weight: 600; margin-bottom: 8px">信号类型</div>
          <NTag
            :type="detail.signal_kind === 'position_target' ? 'warning' : 'info'"
            size="small"
          >{{ detail.signal_kind }}</NTag>
        </div>

        <div style="margin-top: 16px">
          <div style="font-weight: 600; margin-bottom: 8px">运行时参数</div>
          <NSpace>
            <NTag
              v-if="detail.runtime_params.length === 0"
              type="default"
              size="small"
            >无运行时参数</NTag>
            <NTag
              v-for="p in detail.runtime_params"
              :key="p"
              type="info"
              size="small"
            >{{ p }}</NTag>
          </NSpace>
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>
