<script setup lang="ts">
import {ref, watch} from 'vue'
import {ElMessage} from 'element-plus'
import {api, allPages} from '../api'
import {useWorkspace} from '../store'

defineProps<{disabled?: boolean}>()
const emit = defineEmits<{apply: [data: Record<string, unknown>]}>()
const store = useWorkspace()
const items = ref<any[]>([])
const selectedId = ref<number | null>(null)
const loading = ref(false)
let revision = 0
watch(() => store.projectId, () => {revision++; items.value = []; selectedId.value = null; loading.value = false})
async function load() {
  const current = ++revision
  loading.value = true
  try {
    const data = await allPages('entities/', {project: store.projectId, kind: 'data_preparation'})
    if (current === revision) items.value = data
  } finally {if (current === revision) loading.value = false}
}
async function apply() {
  if (!selectedId.value) return
  const project = store.projectId
  loading.value = true
  try {
    const {data} = await api.get(`entities/${selectedId.value}/`)
    if (project !== store.projectId) return
    emit('apply', data.config.variables)
    ElMessage.success((data.config.count>1?'已加载该批次第一条字段数据；':'已加载字段数据；')+'保存或执行后生效')
  } finally {loading.value = false}
}
</script>

<template>
  <div class="prepared-data-select">
    <el-select v-model="selectedId" filterable clearable :disabled="disabled||loading" placeholder="选择已保存的数据准备方案" aria-label="数据准备方案" @visible-change="(visible:boolean)=>{if(visible)load()}"><el-option v-for="item in items" :key="item.id" :value="item.id" :label="`${item.name} · ${item.config.output_format.toUpperCase()} · ${item.config.count||1} 条（加载首条）`"/></el-select>
    <el-button :disabled="disabled||!selectedId" :loading="loading" @click="apply">加载字段数据</el-button>
    <router-link to="/data-preparation" target="_blank">管理数据准备 ↗</router-link>
  </div>
</template>

<style scoped>
.prepared-data-select{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.prepared-data-select .el-select{width:290px}.prepared-data-select a{font-size:12px;color:var(--el-color-primary)}
</style>
