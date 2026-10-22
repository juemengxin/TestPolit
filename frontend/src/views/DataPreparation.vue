<script setup lang="ts">
import {ref, watch} from 'vue'
import {ElMessage, ElMessageBox} from 'element-plus'
import {api, fmt} from '../api'
import {useWorkspace} from '../store'
import {preparationDraft, type PreparationResult} from '../dataPreparation'
import DataPreparationEditor from '../components/DataPreparationEditor.vue'
import Icon from '../components/Icon.vue'

const store = useWorkspace()
const items = ref<any[]>([]), total = ref(0), page = ref(1), search = ref(''), outputFormat = ref('')
const loading = ref(false), saving = ref(false), dialog = ref(false), name = ref('')
const selected = ref<any>(null), draft = ref(preparationDraft()), initialResult = ref<PreparationResult>()
let revision = 0

async function load() {
  if (!store.projectId) return
  const current = ++revision
  loading.value = true
  try {
    const {data} = await api.get('entities/', {params: {project: store.projectId, kind: 'data_preparation', search: search.value, page: page.value, output_format: outputFormat.value}})
    if (current === revision) {items.value = data.results; total.value = data.count}
  } finally {if (current === revision) loading.value = false}
}
watch(() => store.projectId, () => {dialog.value = false; items.value = []; total.value = 0; page.value = 1; void load()}, {immediate: true})
function filter() {page.value = 1; void load()}
function edit(item?: any) {
  selected.value = item || null
  name.value = item?.name || ''
  draft.value = preparationDraft(item?.config)
  initialResult.value = item?.config
  dialog.value = true
}
async function save() {
  if (!name.value.trim()) {ElMessage.warning('请填写方案名称'); return}
  const project = store.projectId
  const payload = {project, kind: 'data_preparation', name: name.value.trim(), config: draft.value, ...(selected.value ? {version: selected.value.version} : {})}
  saving.value = true
  try {
    await (selected.value ? api.patch(`entities/${selected.value.id}/`, payload) : api.post('entities/', payload))
    if (project !== store.projectId) return
    dialog.value = false
    ElMessage.success('已生成并保存数据准备方案')
    await load()
  } finally {saving.value = false}
}
async function remove(item: any) {
  try {await ElMessageBox.confirm(`删除数据准备方案「${item.name}」？`, '删除方案', {type: 'warning'})}
  catch {return}
  await api.delete(`entities/${item.id}/`)
  if (items.value.length === 1 && page.value > 1) page.value--
  await load()
}
function download(item: any) {
  window.location.assign(`/api/entities/${item.id}/prepared_output/`)
}
</script>

<template>
  <div>
    <div class="page-heading"><div><div class="heading-eyebrow">TEST DATA PREPARATION</div><h1>数据准备</h1><p>按字段和值生成 JSON 或 SQL，将可复用的测试数据保存在项目中。</p></div><el-button v-if="store.canEdit" type="primary" @click="edit()"><Icon name="Plus"/>新建数据准备</el-button></div>
    <div class="panel preparation-list">
      <div class="filters"><el-input v-model="search" clearable placeholder="搜索方案名称" aria-label="搜索数据准备" @keyup.enter="filter" @clear="filter"/><el-select v-model="outputFormat" clearable placeholder="全部格式" aria-label="筛选输出格式" @change="filter"><el-option label="JSON" value="json"/><el-option label="SQL" value="sql"/></el-select><el-button @click="filter">查询</el-button></div>
      <el-table v-loading="loading" :data="items" empty-text="暂无数据准备方案，填写字段即可生成">
        <el-table-column prop="name" label="方案名称" min-width="220"/>
        <el-table-column label="数据条数" width="100"><template #default="{row}">{{row.config.count||1}}</template></el-table-column>
        <el-table-column label="字段数" width="90"><template #default="{row}">{{row.config.fields.length}}</template></el-table-column>
        <el-table-column label="输出格式" width="150"><template #default="{row}"><el-tag :type="row.config.output_format==='sql'?'warning':'info'">{{row.config.output_format==='sql'?row.config.dialect.toUpperCase()+' SQL':'JSON'}}</el-tag></template></el-table-column>
        <el-table-column label="版本" width="80"><template #default="{row}">v{{row.version}}</template></el-table-column>
        <el-table-column label="更新时间" width="150"><template #default="{row}">{{fmt(row.updated_at)}}</template></el-table-column>
        <el-table-column label="操作" width="220"><template #default="{row}"><el-button link type="primary" @click="edit(row)">{{store.canEdit?'编辑 / 预览':'查看'}}</el-button><el-button link @click="download(row)">下载结果</el-button><el-button v-if="store.canEdit" link type="danger" @click="remove(row)">删除</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-model:current-page="page" :page-size="10" :total="total" layout="total, prev, pager, next" @current-change="load"/>
    </div>
    <el-dialog v-model="dialog" :title="selected?'数据准备 · '+selected.name:'新建数据准备'" width="980px" style="max-width:96vw" :close-on-click-modal="false" destroy-on-close>
      <el-form label-position="top"><el-form-item label="方案名称"><el-input v-model="name" maxlength="150" :disabled="!store.canEdit||saving" placeholder="例如：用户注册基础数据" aria-label="数据准备名称"/></el-form-item></el-form>
      <DataPreparationEditor v-if="dialog" :key="`${selected?.id||'new'}-${selected?.version||0}`" v-model="draft" :initial-result="initialResult" :disabled="!store.canEdit||saving"/>
      <template #footer><el-button @click="dialog=false">关闭</el-button><el-button v-if="store.canEdit" type="primary" :loading="saving" @click="save">生成并保存</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.preparation-list{padding:20px}.filters{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}.filters .el-input{width:300px}.filters .el-select{width:160px}.el-pagination{margin-top:20px;justify-content:flex-end}
</style>
