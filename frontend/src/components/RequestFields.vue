<script setup lang="ts">
export interface RequestField { key:string; value:string; enabled:boolean }
const rows=defineModel<RequestField[]>({required:true})
withDefaults(defineProps<{disabled?:boolean; keyLabel?:string; valueLabel?:string}>(),{keyLabel:'参数名',valueLabel:'值'})
</script>

<template>
  <div class="request-fields">
    <div class="field-heading"><span></span><span>{{keyLabel}}</span><span>{{valueLabel}}</span><span></span></div>
    <div v-for="(row,index) in rows" :key="index" class="field-row">
      <el-checkbox v-model="row.enabled" :disabled="disabled" :aria-label="`启用第 ${index+1} 行`"/>
      <el-input v-model="row.key" :disabled="disabled" :placeholder="keyLabel" :aria-label="`${keyLabel} ${index+1}`"/>
      <el-input v-model="row.value" :disabled="disabled" :placeholder="valueLabel" :aria-label="`${valueLabel} ${index+1}`"/>
      <el-button text :disabled="disabled" :aria-label="`删除第 ${index+1} 行`" @click="rows.splice(index,1)">×</el-button>
    </div>
    <el-button v-if="!disabled" plain size="small" @click="rows.push({key:'',value:'',enabled:true})">＋ 添加参数</el-button>
    <p v-if="!rows.length&&disabled" class="helper">未配置参数</p>
  </div>
</template>

<style scoped>
.request-fields{display:grid;gap:10px}.field-heading,.field-row{display:grid;grid-template-columns:24px minmax(100px,1fr) minmax(120px,1.5fr) 34px;gap:10px;align-items:center}.field-heading{color:var(--el-text-color-secondary);font-size:12px}.request-fields>.el-button{justify-self:start;margin-top:6px}.field-row .el-button{margin:0}
</style>
