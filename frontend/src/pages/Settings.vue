<template>
  <div>
    <h1>设置</h1>
    <h2 class="muted" style="font-size:16px">品项管理</h2>
    <div v-if="error" class="bar error-bar">{{ error }}</div>
    <div v-for="i in items" :key="i.id" class="item-row">
      <span class="meta">{{ i.name }} <span class="muted">{{ layerLabel[i.layer] || i.layer }} · {{ i.unit }}</span></span>
      <span class="state" :class="{ off: !i.active }">{{ i.active ? '启用中' : '已停用' }}</span>
      <button
        :class="{ secondary: !i.active }"
        :disabled="busyId === i.id"
        @click="toggle(i)">
        {{ i.active ? '停用' : '启用' }}
      </button>
    </div>
    <h2 class="muted" style="font-size:16px;margin-top:20px">系统设置</h2>
    <pre>{{ s }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const s = ref('')
const busyId = ref(null)
const error = ref('')
const layerLabel = { upper: '上层', mid: '中层', lower: '下层' }

async function load() {
  items.value = await api('/items?scope=all')
}
function friendly(e) {
  if (e.message === 'item_not_found') return '品项不存在，列表可能已变更，已重新加载'
  if (e.message === 'database_busy') return '系统繁忙，操作未生效，已恢复到操作前状态'
  return e.message
}
async function toggle(i) {
  error.value = ''
  busyId.value = i.id
  try {
    const r = await api('/items/' + i.id, { method: 'PATCH', body: JSON.stringify({ active: !i.active }) })
    i.active = r.active  // pessimistic: only mutate local state after the server commits
  } catch (e) {
    // Failure rolls back server-side; refetch so dropdown/shelf stay consistent with pre-action state.
    error.value = friendly(e)
    await load()
  } finally {
    busyId.value = null
  }
}
onMounted(async () => {
  await load()
  s.value = JSON.stringify(await api('/settings'), null, 2)
})
</script>
