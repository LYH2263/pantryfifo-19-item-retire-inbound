<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <span v-for="x in rows" :key="x.id" class="lot">{{ x.name }}<span v-if="!x.active" class="tag-off">已停用</span> ×{{ x.qty_remain }} · {{ x.expiry }}</span>
  </div>
</template>
<script setup>
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ layer: String })
const rows = ref([])
async function load() { rows.value = await api('/fridge?layer=' + props.layer) }
watch(() => props.layer, load)
onMounted(load)
</script>
