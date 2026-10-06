<template>
  <div>
    <h1>设置</h1>
    <h2>品项管理</h2>
    <table class="items">
      <tr v-for="i in items" :key="i.id">
        <td>{{ i.name }}</td>
        <td class="muted">{{ i.layer }} · {{ i.unit }}</td>
        <td><span :class="i.active ? 'tag-on' : 'tag-off'">{{ i.active ? '启用中' : '已停用' }}</span></td>
        <td>
          <button v-if="i.active" @click="deactivate(i)">停用</button>
          <button v-else @click="enable(i)">启用</button>
        </td>
      </tr>
    </table>
    <p v-if="err" class="err">{{ err }}</p>
    <h2>参数</h2>
    <pre>{{ s }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const s = ref('')
const items = ref([])
const err = ref('')
async function load() {
  items.value = await api('/items')
  s.value = JSON.stringify(await api('/settings'), null, 2)
}
async function deactivate(i) {
  err.value = ''
  try {
    // 预览只读：确认期间全层名单不变
    const p = await api(`/items/${i.id}/deactivate-preview`)
    if (!confirm(`停用「${i.name}」？\n在架 ${p.on_shelf_lots} 批 / 余量 ${p.on_shelf_qty}${i.unit} 仍可消费，停用后不可新入库。`)) return
    await api(`/items/${i.id}/status`, { method: 'POST', body: JSON.stringify({ active: 0 }) })
    await load()
  } catch (e) {
    err.value = '停用失败：' + e.message
    await load() // 失败则下拉与全层回到操作前
  }
}
async function enable(i) {
  err.value = ''
  try {
    await api(`/items/${i.id}/status`, { method: 'POST', body: JSON.stringify({ active: 1 }) })
    await load()
  } catch (e) {
    err.value = '启用失败：' + e.message
    await load()
  }
}
onMounted(load)
</script>
