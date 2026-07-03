<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NTag, NSpace } from 'naive-ui'
import client from '@/api/client'

const resp = ref<string>('loading...')

onMounted(async () => {
  try {
    const data = await client.get<unknown, { module: string; status: string }>('/strategy')
    resp.value = JSON.stringify(data)
  } catch (e) {
    resp.value = `error: ${JSON.stringify(e)}`
  }
})
</script>

<template>
  <NCard title="策略管理">
    <NSpace>
      <NTag type="info">todo</NTag>
    </NSpace>
    <pre style="margin-top: 16px">{{ resp }}</pre>
  </NCard>
</template>
