<template>
  <div>
    <h1>分批入库</h1>
    <select v-model.number="item_id"><option v-for="i in items" :value="i.id">{{ i.name }}</option></select>
    <input type="number" v-model.number="qty" placeholder="数量" />
    <input v-model="expiry" placeholder="到期 YYYY-MM-DD" />
    <button @click="go" :disabled="item_id == null">入库</button>
    <p v-if="msg" class="err">{{ msg }}</p>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const item_id = ref(null)
const qty = ref(1)
const expiry = ref('2026-12-01')
const msg = ref('')
// 入库下拉只列启用品，与服务端确认校验同源
async function load() {
  items.value = await api('/items?active=1')
  if (!items.value.some(i => i.id === item_id.value)) item_id.value = items.value[0]?.id ?? null
}
async function go() {
  msg.value = ''
  try {
    await api('/lots', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value, expiry: expiry.value }) })
    alert('已入库')
  } catch (e) {
    // 确认时品项已停用：预检表单作废，刷新下拉使该品即时消失
    msg.value = e.message === 'item_inactive' ? '该品项已停用，无法入库' : '入库失败：' + e.message
    await load()
  }
}
onMounted(load)
</script>
