<script setup lang="ts">
import {ref,onMounted,watch} from 'vue'
import {api,allPages} from '../api'
import {useWorkspace} from '../store'
import {ElMessage,ElMessageBox} from 'element-plus'
import Icon from './Icon.vue'
const store=useWorkspace(),modules=ref<any[]>([]),cases=ref<any[]>([]),dragged=ref<any>(null),emit=defineEmits(['edit','changed'])
async function load(){[modules.value,cases.value]=await Promise.all([allPages('entities/',{project:store.projectId,kind:'module'}),allPages('assets/',{project:store.projectId,kind:'case'})])}
async function addModule(){let value;try{value=(await ElMessageBox.prompt('输入模块名称','创建模块')).value}catch{return}await api.post('entities/',{project:store.projectId,kind:'module',name:value,config:{}});load()}
async function drop(id:number|null){if(!dragged.value)return;await api.patch(`assets/${dragged.value.id}/`,{data:{...dragged.value.data,module_id:id}});dragged.value=null;ElMessage.success('用例已移动到模块');load();emit('changed')}
watch(()=>store.projectId,load,{immediate:true})
defineExpose({load})
</script>
<template><section class="panel case-map"><div class="panel-heading"><h3>{{ $t('模块 / 用例思维导图') }}</h3><el-button v-if="store.canEdit" link type="primary" @click="addModule">{{ $t('+ 创建模块') }}</el-button></div><div class="map-root"><Icon name="FolderOpened"/>{{store.project?.name}}</div><div class="map-branches"><div v-for="m in [{id:null,name:'未分组'},...modules]" :key="m.id||0" class="map-branch" @dragover.prevent @drop.prevent="drop(m.id)"><div class="map-module"><Icon name="Folder" :size="16"/>{{m.name}}<small>{{cases.filter(c=>(c.data.module_id||null)===m.id).length}}</small></div><button v-for="c in cases.filter(c=>(c.data.module_id||null)===m.id)" :key="c.id" class="map-case" :draggable="store.canEdit" @dragstart="dragged=c" @dblclick="emit('edit',c)" @click="emit('edit',c)"><span class="priority" :class="c.priority">{{c.priority}}</span>{{c.name}}</button><p v-if="!cases.some(c=>(c.data.module_id||null)===m.id)" class="helper">{{ $t('拖入用例完成归类') }}</p></div></div><p class="helper" style="padding:0 22px">{{ $t('点击用例编辑；拖拽用例到模块可立即保存分类。支持在列表中复制，再粘贴为草稿。') }}</p></section></template>
