<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api,allPages,fmt} from '../api'
import {useWorkspace} from '../store'
import Icon from './Icon.vue'

const props=defineProps<{asset?:number,run?:number,results?:any[]}>()
const store=useWorkspace(),files=ref<any[]>([]),busy=ref(false),link=ref('')
const mediaFiles=computed(()=>files.value.filter(f=>/\.(png|jpe?g|webp|gif|avif|mp4|webm|mov|m4v|ogv)$/i.test(f.name)))
const imageFiles=computed(()=>mediaFiles.value.filter(f=>/\.(png|jpe?g|webp|gif|avif)$/i.test(f.name)))
const otherFiles=computed(()=>files.value.filter(f=>!mediaFiles.value.includes(f)))
const imageUrls=computed(()=>imageFiles.value.map(inlineUrl))
function inlineUrl(file:any){return `${file.download_url}${file.download_url.includes('?')?'&':'?'}inline=1`}
async function load(){files.value=await allPages('attachments/',{project:store.projectId,asset:props.asset,run:props.run})}
watch(()=>[props.asset,props.run],load,{immediate:true})
async function upload(event:Event){const input=event.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;const data=new FormData();data.append('file',file);data.append('project',String(store.projectId));data.append(props.asset?'asset':'run',String(props.asset||props.run));if(link.value){const [caseId,stepIndex]=link.value.split(':');data.append('case_id',caseId);data.append('step_index',stepIndex)}busy.value=true;try{await api.post('attachments/',data);ElMessage.success('附件已上传并保存');await load()}finally{busy.value=false;input.value=''}}
async function remove(id:number){try{await ElMessageBox.confirm('确认删除这个附件？','删除附件',{type:'warning'})}catch{return}await api.delete(`attachments/${id}/`);load()}
</script>
<template>
  <div class="attachments">
    <div class="attachment-heading">
      <h3>{{ $t('关联附件') }}<span class="count-badge">{{files.length}}</span></h3>
      <div v-if="store.canEdit" class="inline-actions">
        <el-select v-if="run&&results?.length" v-model="link" :placeholder="$t('选择截图关联步骤（可选）')" clearable style="width:245px">
          <el-option v-for="s in results.flatMap(r=>r.steps.map((s:any)=>({label:`${r.name} · 步骤 ${s.index+1}`,value:`${r.case_id}:${s.index}`})))" :key="s.value" :label="s.label" :value="s.value"/>
        </el-select>
        <label class="upload-button" :class="{disabled:busy}"><Icon name="Upload" :size="15"/>{{busy?'上传中…':'上传附件'}}<input type="file" :disabled="busy" @change="upload" accept=".pdf,.txt,.md,.csv,.json,.png,.jpg,.jpeg,.webp,.docx,.xlsx"/></label>
      </div>
    </div>
    <p class="helper">执行器会自动关联运行截图和视频；图片可点击放大，视频可直接播放。</p>
    <div v-if="mediaFiles.length" class="artifact-preview-grid">
      <figure v-for="f in mediaFiles" :key="f.id" class="artifact-preview">
        <el-image v-if="imageFiles.includes(f)" :src="inlineUrl(f)" :preview-src-list="imageUrls" :initial-index="imageFiles.findIndex(image=>image.id===f.id)" fit="contain" preview-teleported/>
        <video v-else :src="inlineUrl(f)" controls preload="metadata" playsinline/>
        <figcaption><span>{{f.name}}<small v-if="f.case_id">用例 #{{f.case_id}}<template v-if="f.step_index!==null"> · 步骤 {{f.step_index+1}}</template></small></span><a :href="f.download_url">下载</a></figcaption>
      </figure>
    </div>
    <div v-for="f in otherFiles" :key="f.id" class="file-row">
      <Icon name="Paperclip"/><a :href="f.download_url">{{f.name}}</a>
      <span v-if="f.case_id" class="mini-badge">用例 #{{f.case_id}} · 步骤 {{f.step_index+1}}</span>
      <small>{{(f.size/1024).toFixed(1)}} KB · {{fmt(f.created_at)}}</small>
      <el-button v-if="store.canEdit" link type="danger" @click="remove(f.id)">{{ $t('删除') }}</el-button>
    </div>
    <p v-if="!files.length" class="muted">{{ $t('暂无附件') }}</p>
  </div>
</template>
