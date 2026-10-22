<script setup lang="ts">
import {computed,ref} from 'vue'
const value=defineModel<string>({required:true})
defineProps<{disabled?:boolean}>()
const mode=ref('form')
const types:Record<string,string>={status:'HTTP 状态码',json_equals:'JSON 字段相等',contains:'响应包含文本',duration_lt:'耗时小于（毫秒）',db_equals:'数据库值相等'}
const rules=computed<Record<string,any>[]|null>(()=>{
  try{const parsed=JSON.parse(value.value);return Array.isArray(parsed)&&parsed.every(rule=>rule&&typeof rule==='object'&&!Array.isArray(rule)&&types[rule.type])?parsed:null}catch{return null}
})
function update(index:number,changes:Record<string,unknown>){
  if(!rules.value)return
  value.value=JSON.stringify(rules.value.map((rule,position)=>position===index?{...rule,...changes}:rule),null,2)
}
function changeType(index:number,type:string){
  const expected=type==='status'?200:type==='duration_lt'?1000:''
  update(index,{type,expected})
}
function expectedType(expected:unknown){return expected===null?'null':typeof expected==='object'?'json':typeof expected}
function changeExpectedType(index:number,type:string){update(index,{expected:type==='number'?0:type==='boolean'?true:type==='null'?null:type==='json'?{}:''})}
function updateNumber(index:number,input:string){const expected=Number(input);if(Number.isFinite(expected))update(index,{expected})}
function add(){value.value=JSON.stringify([...(rules.value||[]),{type:'status',expected:200}],null,2)}
function remove(index:number){value.value=JSON.stringify(rules.value?.filter((_,position)=>position!==index),null,2)}
</script>

<template>
  <div class="assertion-editor">
    <div class="assertion-toolbar"><span>全部断言通过时，接口才判定为通过。</span><el-radio-group v-model="mode" size="small"><el-radio-button value="form">表单</el-radio-button><el-radio-button value="json">JSON</el-radio-button></el-radio-group></div>
    <template v-if="mode==='json'"><el-input v-model="value" :disabled="disabled" type="textarea" :rows="9" class="code-input" aria-label="断言 JSON"/></template>
    <el-alert v-else-if="!rules" type="warning" title="无法以表单展示，请切换 JSON 修正格式或断言类型；原始配置已保留。" :closable="false"/>
    <template v-else>
      <p v-if="!rules.length" class="empty-assertions">暂无断言。没有断言的真实请求不会被判定为通过。</p>
      <div v-for="(rule,index) in rules" :key="index" class="assertion-card">
        <div class="assertion-title"><b>断言 {{index+1}}</b><el-select :model-value="rule.type" :disabled="disabled" :aria-label="`断言类型 ${index+1}`" @update:model-value="changeType(index,$event)"><el-option v-for="(label,type) in types" :key="type" :label="label" :value="type"/></el-select><el-button text type="danger" :disabled="disabled" :aria-label="`删除断言 ${index+1}`" @click="remove(index)">删除</el-button></div>
        <label v-if="rule.type==='json_equals'">JSON 路径<el-input :model-value="rule.path||''" :disabled="disabled" placeholder="例如 data.id、items.0.name" :aria-label="`JSON 断言路径 ${index+1}`" @update:model-value="update(index,{path:$event})"/></label>
        <div v-if="rule.type==='db_equals'" class="database-fields"><label>数据库配置 ID<el-input-number :model-value="rule.database_id" :disabled="disabled" :min="1" :precision="0" @update:model-value="update(index,{database_id:$event})"/></label><label>只读查询<el-input :model-value="rule.query||''" :disabled="disabled" placeholder="SELECT ..." @update:model-value="update(index,{query:$event})"/></label></div>
        <div class="expected-row"><label>预期值类型<el-select :model-value="expectedType(rule.expected)" :disabled="disabled" :aria-label="`预期值类型 ${index+1}`" @update:model-value="changeExpectedType(index,$event)"><el-option label="数字" value="number"/><el-option label="文本" value="string"/><el-option label="布尔" value="boolean"/><el-option label="空值 null" value="null"/><el-option label="对象 / 数组（JSON 编辑）" value="json"/></el-select></label><label>预期值
          <el-input v-if="typeof rule.expected==='number'" type="number" step="any" :model-value="String(rule.expected)" :disabled="disabled" :aria-label="`断言预期值 ${index+1}`" @update:model-value="updateNumber(index,$event)"/>
          <el-select v-else-if="typeof rule.expected==='boolean'" :model-value="rule.expected" :disabled="disabled" :aria-label="`断言预期值 ${index+1}`" @update:model-value="update(index,{expected:$event})"><el-option label="true" :value="true"/><el-option label="false" :value="false"/></el-select>
          <el-input v-else-if="typeof rule.expected==='string'" :model-value="rule.expected" :disabled="disabled" :aria-label="`断言预期值 ${index+1}`" @update:model-value="update(index,{expected:$event})"/>
          <div v-else class="complex-value"><code>{{JSON.stringify(rule.expected)??'未设置'}}</code><el-button v-if="rule.expected!==null" link type="primary" @click="mode='json'">编辑 JSON</el-button></div>
        </label></div>
      </div>
      <el-button v-if="!disabled" plain @click="add">＋ 添加断言</el-button>
    </template>
  </div>
</template>

<style scoped>
.assertion-editor{display:grid;gap:12px}.assertion-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--el-text-color-secondary)}.assertion-card{border:1px solid var(--el-border-color);border-radius:8px;padding:14px;display:grid;gap:12px}.assertion-title{display:grid;grid-template-columns:auto minmax(140px,1fr) auto;align-items:center;gap:14px}.assertion-title b{font-size:12px}.assertion-card label{display:grid;gap:8px;font-size:12px;color:var(--el-text-color-regular)}.expected-row,.database-fields{display:grid;grid-template-columns:minmax(130px,1fr) minmax(150px,2fr);gap:12px}.assertion-card .el-input-number{width:100%}.complex-value{min-height:32px;display:flex;align-items:center;gap:8px;overflow-wrap:anywhere;min-width:0}.complex-value code{max-height:100px;overflow:auto}.empty-assertions{padding:20px 0;color:var(--el-text-color-secondary);font-size:12px}.assertion-editor>.el-button{justify-self:start}@media(max-width:850px){.expected-row,.database-fields{grid-template-columns:1fr}}
</style>
