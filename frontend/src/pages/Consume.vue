<template>
  <div>
    <h1>按临期消费</h1>
    <select v-model.number="item_id">
      <option v-if="!items.length" disabled :value="null">暂无可消费品项（在架正余量）</option>
      <option v-for="i in items" :key="i.item_id" :value="i.item_id">
        {{ i.name }}（余量 {{ i.qty_total }}{{ i.unit }}）<template v-if="!i.active">［已停用］</template>
      </option>
    </select>
    <input type="number" v-model.number="qty" />
    <button @click="go">FEFO 扣减</button>
    <pre>{{ result }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const item_id = ref(null)
const qty = ref(1)
const result = ref('')
onMounted(async () => {
  items.value = await api('/consumable-items')
  if (items.value[0]) item_id.value = items.value[0].item_id
})
async function go() {
  try {
    result.value = JSON.stringify(await api('/consume', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value }) }), null, 2)
  } catch (e) { result.value = e.message }
}
</script>
