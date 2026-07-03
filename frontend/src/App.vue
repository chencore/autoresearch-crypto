<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NLayout, NLayoutSider, NLayoutContent, NMenu, type MenuOption } from 'naive-ui'

const route = useRoute()
const router = useRouter()

const activeKey = computed(() => route.path)

const menuOptions: MenuOption[] = [
  { label: '策略管理', key: '/strategies' },
  { label: '回测可视化', key: '/backtest' },
  { label: '实盘监控', key: '/live' },
  { label: '参数调优', key: '/evolve' },
]

function onMenuSelect(key: string): void {
  router.push(key)
}
</script>

<template>
  <NLayout has-sider style="height: 100vh">
    <NLayoutSider bordered :width="220">
      <div style="padding: 16px 20px; font-weight: 600; font-size: 16px; border-bottom: 1px solid var(--n-border-color)">
        autoresearch-crypto
      </div>
      <NMenu :options="menuOptions" :value="activeKey" @update:value="onMenuSelect" />
    </NLayoutSider>
    <NLayoutContent style="padding: 24px">
      <router-view />
    </NLayoutContent>
  </NLayout>
</template>
