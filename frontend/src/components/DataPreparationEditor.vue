<script setup lang="ts">
import {computed, nextTick, ref, watch} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from '../api'
import {useWorkspace} from '../store'
import type {PreparationDraft, PreparationField, PreparationResult} from '../dataPreparation'

const draft = defineModel<PreparationDraft>({required: true})
const props = defineProps<{disabled?: boolean; initialResult?: PreparationResult}>()
const store = useWorkspace()
const generating = ref(false)
const result = ref<PreparationResult | null>(props.initialResult || null)
const output = computed(() => result.value?.output_format === 'sql' ? result.value.generated_sql : result.value?.generated_json || '')
const fieldTypes = [
  ['string', '字符串'], ['integer', '整数'], ['number', '小数'],
  ['boolean', '布尔值'], ['null', '空值 NULL'], ['json', 'JSON 对象 / 数组'],
]
watch(() => draft.value.count, count => {if(count>1) draft.value.json_shape='array'}, {immediate:true})
watch(() => [draft.value, store.projectId], () => {result.value = null}, {deep: true})

function changeType(field: PreparationField, type: PreparationField['type']) {
  field.type = type
  field.random = false
  field.random_mode = 'example'
  field.min = 0
  field.max = 100
  field.length = 12
  if (type === 'boolean') field.value = 'true'
  else if (type === 'null') field.value = ''
  else if (type === 'json') field.value = '{}'
}

async function generate() {
  const project = store.projectId
  const snapshot = JSON.stringify(draft.value)
  generating.value = true
  try {
    const {data} = await api.post('entities/prepare_data/', {project, config: {...JSON.parse(snapshot), random_seed: undefined}})
    if (project === store.projectId && snapshot === JSON.stringify(draft.value)) {
      draft.value.random_seed = data.random_seed
      await nextTick()
      result.value = data
    }
  } finally {
    generating.value = false
  }
}

async function copy() {
  try {await navigator.clipboard.writeText(output.value); ElMessage.success('生成结果已复制')}
  catch {ElMessage.warning('剪贴板不可用，请在结果框中手动复制')}
}
</script>

<template>
  <div class="preparation-editor">
    <el-form label-position="top" :disabled="disabled || generating">
      <div class="output-options">
        <el-form-item label="生成数量"><el-input-number v-model="draft.count" :min="1" :max="1000" :precision="0" aria-label="生成数量"/></el-form-item>
        <el-form-item label="输出格式"><el-radio-group v-model="draft.output_format"><el-radio-button value="json">JSON</el-radio-button><el-radio-button value="sql">SQL INSERT</el-radio-button></el-radio-group></el-form-item>
        <el-form-item v-if="draft.output_format==='json'" label="JSON 结构"><el-select v-model="draft.json_shape" :disabled="draft.count>1" aria-label="JSON 结构"><el-option label="对象 {}" value="object"/><el-option label="数组 [{}]" value="array"/></el-select></el-form-item>
        <template v-else>
          <el-form-item label="SQL 方言"><el-select v-model="draft.dialect" aria-label="SQL 方言"><el-option label="SQLite" value="sqlite"/><el-option label="MySQL" value="mysql"/><el-option label="PostgreSQL" value="postgresql"/></el-select></el-form-item>
          <el-form-item label="目标表名"><el-input v-model="draft.table_name" placeholder="例如 test_users" maxlength="63" aria-label="目标表名"/></el-form-item>
        </template>
      </div>
      <el-table :data="draft.fields" border class="field-table">
        <el-table-column label="字段名" min-width="160"><template #default="{row,$index}"><el-input v-model="row.name" placeholder="例如 user_id" maxlength="128" :aria-label="`字段名 ${$index+1}`"/></template></el-table-column>
        <el-table-column label="类型" width="180"><template #default="{row,$index}"><el-select :model-value="row.type" :aria-label="`字段类型 ${$index+1}`" @change="(value:PreparationField['type'])=>changeType(row,value)"><el-option v-for="[value,label] in fieldTypes" :key="value" :value="value" :label="label"/></el-select></template></el-table-column>
        <el-table-column label="随机" width="80"><template #default="{row,$index}"><el-switch v-model="row.random" :disabled="!['string','integer','number','boolean'].includes(row.type)" :aria-label="`随机字段 ${$index+1}`"/></template></el-table-column>
        <el-table-column label="示例数据 / 随机规则" min-width="310"><template #default="{row,$index}">
          <template v-if="row.random">
            <el-select v-model="row.random_mode" :aria-label="`随机方式 ${$index+1}`"><el-option label="按示例随机" value="example"/><el-option label="按范围 / 长度随机" value="rules"/></el-select>
            <template v-if="row.random_mode==='example'">
              <el-select v-if="row.type==='boolean'" v-model="row.value" :aria-label="`示例数据 ${$index+1}`"><el-option label="true" value="true"/><el-option label="false" value="false"/></el-select>
              <el-input v-else v-model="row.value" :maxlength="256" placeholder="例如 13800138000、user@example.com、ORD-0001" :aria-label="`示例数据 ${$index+1}`"/>
            </template>
            <template v-else>
            <el-input-number v-if="row.type==='string'" v-model="row.length" :min="1" :max="256" :precision="0" :aria-label="`随机字符串长度 ${$index+1}`"/><span v-if="row.type==='string'"> 位字母数字</span>
            <div v-if="row.type==='integer'||row.type==='number'" class="random-range"><el-input-number v-model="row.min" :min="-1000000000" :max="1000000000" :precision="row.type==='integer'?0:undefined" :aria-label="`随机最小值 ${$index+1}`"/><span>至</span><el-input-number v-model="row.max" :min="-1000000000" :max="1000000000" :precision="row.type==='integer'?0:undefined" :aria-label="`随机最大值 ${$index+1}`"/></div>
            <span v-if="row.type==='boolean'">随机 true / false</span>
            </template>
          </template>
          <el-select v-else-if="row.type==='boolean'" v-model="row.value" :aria-label="`字段值 ${$index+1}`"><el-option label="true" value="true"/><el-option label="false" value="false"/></el-select>
          <el-input v-else-if="row.type==='null'" model-value="NULL" disabled/>
          <el-input v-else v-model="row.value" :type="row.type==='json'?'textarea':'text'" :rows="2" :maxlength="20000" :placeholder="row.type==='json'?'填写 JSON 对象或数组':'填写字段值；字符串可为空'" :aria-label="`字段值 ${$index+1}`"/>
        </template></el-table-column>
        <el-table-column label="操作" width="76"><template #default="{$index}"><el-button text type="danger" :disabled="draft.fields.length===1" :aria-label="`删除字段 ${$index+1}`" @click="draft.fields.splice($index,1)">删除</el-button></template></el-table-column>
      </el-table>
      <div class="field-actions"><el-button :disabled="draft.fields.length>=200" @click="draft.fields.push({name:'',type:'string',value:'',random:false,random_mode:'example',length:12,min:0,max:100})">＋ 添加字段</el-button><span>{{draft.fields.length}} 个字段</span></div>
    </el-form>
    <p class="helper">每次生成 1–1000 条；多条 JSON 自动使用数组。开启随机后默认按填写的示例生成：手机号保留前三位、邮箱保留域名、编号保留数字前缀和分隔符、日期保留格式、中文姓名保留长度、数字保留位数与正负号。可切换为按范围 / 长度随机；未开启随机的字段保持填写值。随机值不保证唯一；再次预览会生成新批次，保存后导出保持同一批次。敏感字段使用服务端密钥引用，生成时不读取密钥。</p>
    <p v-if="draft.output_format==='sql'" class="helper">仅生成 INSERT 文本，不连接数据库执行。SQL 表名、字段名仅支持字母、数字和下划线；MySQL 字符串使用 UTF-8 十六进制表达式，避免不同 SQL 模式造成转义歧义。</p>
    <div class="result-heading"><el-button v-if="!disabled" type="primary" :loading="generating" @click="generate">生成预览</el-button><strong>生成结果</strong><el-button :disabled="!output" @click="copy">复制结果</el-button></div>
    <el-input :model-value="output" readonly type="textarea" :rows="9" class="code-input" aria-label="生成结果" placeholder="填写字段并生成预览；字段修改后需重新生成"/>
  </div>
</template>

<style scoped>
.random-range{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.random-range .el-input-number{width:130px}.preparation-editor{display:grid;gap:12px}.output-options{display:flex;gap:18px;flex-wrap:wrap}.output-options .el-form-item{min-width:180px}.field-actions,.result-heading{display:flex;align-items:center;gap:14px;margin-top:12px}.field-actions span{color:var(--el-text-color-secondary);font-size:12px}.result-heading strong{flex:1}.helper{margin:0}.field-table :deep(.el-select){width:100%}
</style>
