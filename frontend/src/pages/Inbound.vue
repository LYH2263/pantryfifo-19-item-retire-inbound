<template>
  <div>
    <h1>分批入库</h1>
    <div v-if="error" class="bar error-bar">{{ error }}</div>
    <div v-if="ok" class="bar ok-bar">{{ ok }}</div>
    <select v-model.number="item_id">
      <option v-if="!items.length" disabled :value="null">暂无可入库品项（品项可能已停用）</option>
      <option v-for="i in items" :key="i.id" :value="i.id">{{ i.name }}</option>
    </select>
    <input type="number" v-model.number="qty" placeholder="数量" />
    <input v-model="expiry" placeholder="到期 YYYY-MM-DD" />
    <button @click="go">入库</button>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const item_id = ref(null)
const qty = ref(1)
const expiry = ref('2026-12-01')
const error = ref('')
const ok = ref('')
onMounted(async () => {
  items.value = await api('/items')
  if (items.value[0]) item_id.value = items.value[0].id
})
async function go() {
  error.value = ''; ok.value = ''
  try {
    await api('/lots', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value, expiry: expiry.value }) })
    ok.value = '已入库'
  } catch (e) {
    // Confirm-time recheck inside the write tx: a disabled item fails the whole ticket, no lot row.
    error.value = e.message === 'item_disabled'
      ? '该品项已停用，新入库被拒绝'
      : e.message === 'item_not_found' ? '品项不存在' : e.message
  }
}
</script>
