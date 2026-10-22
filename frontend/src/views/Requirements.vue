<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {onBeforeRouteLeave,useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api,allPages,fmt,stateNames} from '../api'
import {jobNames} from '../platform'
import {useWorkspace} from '../store'
import {requirementStates,requirementTree,parentCandidates,type Requirement} from '../requirements'
import Icon from '../components/Icon.vue'

const store=useWorkspace(),router=useRouter()
const items=ref<Requirement[]>([]),selected=ref<Requirement|null>(null),context=ref<any>(null),revisions=ref<any[]>([]),files=ref<any[]>([])
const search=ref(''),status=ref(''),priority=ref(''),tab=ref('document'),loading=ref(false),detailLoading=ref(false),failed=ref(false),detailFailed=ref(false)
const editorOpen=ref(false),form=ref<any>({}),baseline=ref(''),saving=ref(false),operation=ref(false),importing=ref(false),importJob=ref<number|null>(null)
const fileInput=ref<HTMLInputElement>(),attachmentInput=ref<HTMLInputElement>(),historyVersion=ref<number|null>(null),linked=ref<any>(null)
const jsonOpen=ref(false),jsonText=ref(''),jsonBusy=ref(false)
const tree=computed(()=>requirementTree(items.value,search.value,status.value,priority.value))
const parents=computed(()=>parentCandidates(items.value,form.value.id))
const dirty=computed(()=>editorOpen.value&&JSON.stringify(form.value)!==baseline.value)
const revision=computed(()=>revisions.value.find(item=>item.version===historyVersion.value))
const statistics=computed(()=>({total:items.value.length,pending:items.value.filter(item=>['ready','reviewing'].includes(item.config.status)).length,approved:items.value.filter(item=>item.config.status==='approved').length,high:items.value.filter(item=>item.config.priority==='P0'&&item.config.status!=='archived').length}))
let listToken=0,detailToken=0
function label(state:string){return requirementStates[state]||state||'草稿'}
function stateType(state:string){return state==='approved'?'success':state==='reviewing'||state==='ready'?'warning':'info'}
async function load(preferred?:number){
  const project=store.projectId,token=++listToken
  if(!project)return
  loading.value=true;failed.value=false
  try{
    const data=await allPages('entities/',{project,kind:'requirement'})
    if(token!==listToken||store.projectId!==project)return
    items.value=data
    const next=data.find(item=>item.id===(preferred??selected.value?.id))||data[0]
    if(next)await select(next);else{selected.value=null;context.value=null;revisions.value=[];files.value=[]}
  }catch{if(token===listToken)failed.value=true}finally{if(token===listToken)loading.value=false}
}
async function select(item:Requirement){
  const token=++detailToken,project=store.projectId
  selected.value=item;context.value=null;revisions.value=[];files.value=[];detailLoading.value=true;detailFailed.value=false
  try{
    const [detail,trace,versions,attachments]=await Promise.all([api.get(`entities/${item.id}/`),api.get(`entities/${item.id}/requirement_context/`),api.get(`entities/${item.id}/revisions/`),allPages('artifacts/',{project,entity:item.id})])
    if(token!==detailToken||store.projectId!==project)return
    selected.value=detail.data;context.value=trace.data;revisions.value=versions.data;files.value=attachments
    historyVersion.value=versions.data.find((version:any)=>version.version!==detail.data.version)?.version??detail.data.version
  }catch{if(token===detailToken)detailFailed.value=true}finally{if(token===detailToken)detailLoading.value=false}
}
watch(()=>store.projectId,()=>{detailToken++;items.value=[];selected.value=null;linked.value=null;importJob.value=null;editorOpen.value=false;jsonOpen.value=false;search.value='';status.value='';priority.value='';void load()},{immediate:true})
function edit(item?:Requirement,asChild=false){
  if(!store.canEdit)return
  form.value=item&&!asChild?JSON.parse(JSON.stringify(item)):{project:store.projectId,name:'',content:'',parent:asChild?item?.id:null,config:{status:'draft',priority:'P1',owner:'',tags:[],acceptance_criteria:''}}
  form.value.project=store.projectId
  form.value.config={status:'draft',priority:'P1',owner:'',tags:[],acceptance_criteria:'',...form.value.config}
  baseline.value=JSON.stringify(form.value);editorOpen.value=true
}
async function confirmDiscard(){if(!dirty.value)return true;try{await ElMessageBox.confirm('未保存的需求修改将丢失。','离开编辑',{confirmButtonText:'放弃修改',cancelButtonText:'继续编辑',type:'warning'});return true}catch{return false}}
async function closeEditor(done?:()=>void){if(saving.value)return;if(await confirmDiscard()){editorOpen.value=false;done?.()}}
async function save(){
  if(!form.value.name.trim()){ElMessage.warning('请填写需求标题');return}
  const payload={...form.value,name:form.value.name.trim(),kind:'requirement'}
  saving.value=true
  try{
    const {data}=await(payload.id?api.patch(`entities/${payload.id}/`,payload):api.post('entities/',payload))
    if(store.projectId!==payload.project)return
    editorOpen.value=false;ElMessage.success('需求已保存并生成新版本');await load(data.id)
  }catch{}finally{saving.value=false}
}
function ai(kind:string){if(!selected.value?.content.trim()){ElMessage.warning('请先填写或解析需求正文');return}router.push({path:'/ai',query:{operation:kind,entity:String(selected.value.id),target:'ui_case'}})}
async function syncModules(){
  const item=selected.value,project=store.projectId
  if(!item)return
  operation.value=true
  try{const {data}=await api.post(`entities/${item.id}/sync_modules/`);if(store.projectId===project&&selected.value?.id===item.id){ElMessage.success(`已同步 ${data.count} 个功能模块`);await select(item);tab.value='trace'}}catch{}finally{operation.value=false}
}
async function duplicate(){
  if(!selected.value)return
  const project=store.projectId;operation.value=true
  try{const {data}=await api.post(`entities/${selected.value.id}/duplicate/`);if(store.projectId===project){await load(data.id);ElMessage.success('已复制需求')}}catch{}finally{operation.value=false}
}
async function restore(){
  const item=selected.value,target=revision.value,project=store.projectId
  if(!item||!target)return
  try{await ElMessageBox.confirm(`将 v${target.version} 的标题、正文和需求属性恢复为新版本，现有历史会保留。`,'恢复版本',{type:'warning'})}catch{return}
  operation.value=true
  try{await api.post(`entities/${item.id}/restore/`,{version:target.version,expected_version:item.version});if(store.projectId===project){await load(item.id);ElMessage.success('历史内容已恢复为新版本')}}catch{}finally{operation.value=false}
}
async function importFile(event:Event,replace=false){
  const input=event.target as HTMLInputElement,file=input.files?.[0],project=store.projectId,item=selected.value
  input.value='';if(!file||!project)return
  if(file.size>10*1024*1024||!(/\.(md|doc|docx)$/i).test(file.name)){ElMessage.warning('请选择不超过 10MB 的 Markdown 或 Word 文件');return}
  if(replace){if(!item)return;try{await ElMessageBox.confirm('文件解析完成后会替换当前需求正文并记录新版本。','上传并更新正文',{type:'warning'})}catch{return}}
  const data=new FormData();data.append('file',file);data.append(replace?'entity':'project',String(replace?item!.id:project));importing.value=true
  try{const response=await api.post(replace?'artifacts/':'entities/import_requirement/',data);if(project===store.projectId){importJob.value=response.data.job;await load(replace?item!.id:response.data.entity.id);ElMessage.success('文件已提交解析，可查看任务进度；完成后刷新需求')}}catch{}finally{importing.value=false}
}
async function importJson(){
  let records:any
  try{records=JSON.parse(jsonText.value);if(!Array.isArray(records)||!records.length||records.some(item=>!item||item.kind!=='requirement'))throw new Error()}catch{ElMessage.warning('请输入仅包含 requirement 记录的 JSON 数组');return}
  const project=store.projectId;jsonBusy.value=true
  try{await api.post('entities/import/',{project,items:records});if(project===store.projectId){jsonOpen.value=false;await load();ElMessage.success('需求已导入')}}catch{}finally{jsonBusy.value=false}
}
async function viewLinked(resource:string,id:number){
  const project=store.projectId,source=selected.value?.id
  try{const {data}=await api.get(`${resource}/${id}/`);if(project===store.projectId&&source===selected.value?.id)linked.value=data}catch{}
}
function beforeUnload(event:BeforeUnloadEvent){if(dirty.value){event.preventDefault();event.returnValue=''}}
onBeforeRouteLeave(confirmDiscard)
onMounted(()=>window.addEventListener('beforeunload',beforeUnload))
onBeforeUnmount(()=>{listToken++;detailToken++;window.removeEventListener('beforeunload',beforeUnload)})
</script>

<template>
  <div class="requirements-workspace">
    <div class="page-heading"><div><div class="heading-eyebrow">REQUIREMENTS WORKSPACE</div><h1>需求管理</h1><p>从业务需求到验证依据，让版本、评审与测试资产保持关联。</p></div><div class="inline-actions"><el-button v-if="store.canEdit" :loading="importing" @click="fileInput?.click()"><Icon name="Upload"/>导入文档</el-button><el-button v-if="store.canEdit" type="primary" @click="edit()"><Icon name="Plus"/>新建需求</el-button></div></div>
    <div class="requirement-stats"><div v-for="(title,key) in {total:'全部需求',pending:'待评审 / 评审中',approved:'已确认',high:'P0 重点需求'}" :key="key" class="panel"><span>{{title}}</span><strong>{{statistics[key]}}</strong></div></div>
    <el-alert v-if="importJob" type="info" :closable="false" class="import-notice"><template #title>文档已提交后台解析 <el-button link type="primary" @click="router.push('/tasks/'+importJob)">查看任务 #{{importJob}}</el-button><el-button link @click="load()">刷新需求</el-button></template></el-alert>
    <div class="requirement-grid">
      <aside class="panel requirement-sidebar" v-loading="loading">
        <div class="section-heading"><h3>需求目录</h3><el-button text size="small" @click="load()">刷新</el-button></div>
        <el-input v-model="search" placeholder="搜索标题、负责人或标签" clearable aria-label="搜索需求"/>
        <div class="filter-grid"><el-select v-model="status" clearable placeholder="全部状态" aria-label="筛选需求状态"><el-option v-for="(name,value) in requirementStates" :key="value" :label="name" :value="value"/></el-select><el-select v-model="priority" clearable placeholder="优先级" aria-label="筛选需求优先级"><el-option v-for="value in ['P0','P1','P2','P3']" :key="value" :label="value" :value="value"/></el-select></div>
        <el-alert v-if="failed" title="加载失败，请刷新重试" type="error" :closable="false"/>
        <el-tree v-else-if="tree.length" :data="tree" node-key="id" default-expand-all highlight-current :current-node-key="selected?.id" class="requirement-tree"><template #default="{data}"><button class="requirement-node" :class="{muted:!data.matched}" :title="data.label" @click.stop="select(data.item)"><span>{{data.label}}</span><small>{{label(data.item.config.status)}}</small></button></template></el-tree>
        <el-empty v-else :image-size="65" :description="items.length?'没有匹配的需求':'暂无需求，创建或导入文档开始使用'"/>
        <div class="sidebar-footer"><a :href="`/api/entities/export/?project=${store.projectId}&kind=requirement`">导出 JSON</a><el-button v-if="store.canEdit" text size="small" @click="jsonText='';jsonOpen=true">导入 JSON</el-button></div>
      </aside>
      <section class="panel requirement-detail" v-loading="detailLoading">
        <el-empty v-if="!selected" description="选择左侧需求，查看正文、版本和关联资产"/>
        <template v-else>
          <div class="detail-heading"><div><div class="detail-badges"><span>#REQ-{{selected.id}}</span><el-tag :type="stateType(selected.config.status)">{{label(selected.config.status)}}</el-tag><el-tag :type="selected.config.priority==='P0'?'danger':'info'">{{selected.config.priority||'P1'}}</el-tag><span>v{{selected.version}}</span></div><h2>{{selected.name}}</h2><p>负责人：{{selected.config.owner||'未分配'}} · 更新于 {{fmt(selected.updated_at)}}</p></div><el-button v-if="store.canEdit" :disabled="detailLoading" type="primary" plain @click="edit(selected)">编辑需求</el-button></div>
          <div v-if="selected.config.tags?.length" class="requirement-tags"><el-tag v-for="tag in selected.config.tags" :key="tag" type="info" size="small">{{tag}}</el-tag></div>
          <div v-if="store.canEdit" class="requirement-actions"><el-button :disabled="!selected.content.trim()||detailLoading" @click="ai('review')">AI 评审</el-button><el-button :disabled="!selected.content.trim()||detailLoading" @click="ai('split')">拆分子需求</el-button><el-button :disabled="!selected.content.trim()||detailLoading" @click="ai('design')">生成模块用例</el-button><el-button :loading="operation" :disabled="!selected.content.trim()||detailLoading" @click="syncModules">同步功能模块</el-button><el-button text @click="edit(selected,true)">＋ 子需求</el-button><el-button text :disabled="operation" @click="duplicate">复制</el-button></div>
          <el-alert v-if="detailFailed" title="部分详情加载失败，请刷新重试。" type="error" :closable="false"/>
          <el-alert v-if="selected.config.status==='parsing'" title="文档正在解析。若长时间未完成，请在关联任务中查看错误或 Worker 状态。" type="info" :closable="false"/>
          <el-tabs v-model="tab">
            <el-tab-pane label="需求正文" name="document"><div class="requirement-document"><div class="section-heading"><h3>业务描述</h3><span>{{selected.content.length}} 字符</span></div><pre v-if="selected.content" class="document-content">{{selected.content}}</pre><el-empty v-else :image-size="55" description="尚未填写正文，可编辑需求或导入文档"/><section v-if="selected.config.acceptance_criteria" class="acceptance"><h3>验收标准</h3><pre>{{selected.config.acceptance_criteria}}</pre></section><p class="helper">正文按原文展示，支持 Markdown 标题供功能模块识别；AI 评审不自动改变人工确认状态。</p></div></el-tab-pane>
            <el-tab-pane label="关联追踪" name="trace"><template v-if="context"><div class="trace-summary"><span>功能模块 <b>{{context.counts.modules}}</b></span><span>关联用例 <b>{{context.counts.cases}}</b></span><span>处理任务 <b>{{context.counts.jobs}}</b></span></div><p class="helper">关联数量不等同于测试覆盖率；历史关联按来源标识追踪，无来源标识的旧资产不会自动推断。</p><h3 class="trace-title">功能模块</h3><div class="module-chips"><el-button v-for="module in context.modules" :key="module.id" plain @click="viewLinked('entities',module.id)">{{module.name}}<el-tag v-if="module.config.source_version&&module.config.source_version!==selected.version" type="warning" size="small">源 v{{module.config.source_version}}</el-tag></el-button><span v-if="!context.modules.length" class="helper">暂无关联模块，可通过「同步功能模块」从正文标题建立关联。</span></div><h3 class="trace-title">关联用例</h3><el-table :data="context.cases" empty-text="尚无关联用例"><el-table-column label="名称" min-width="190"><template #default="{row}"><el-button link type="primary" @click="viewLinked(row.resource,row.id)">{{row.name}}</el-button></template></el-table-column><el-table-column label="类型" width="115"><template #default="{row}">{{row.kind==='api'?'接口用例':row.kind==='ui_case'?'UI 用例':'功能用例'}}</template></el-table-column><el-table-column label="状态" width="125"><template #default="{row}">{{row.status==='needs_mapping'?'待配置执行':stateNames[row.status]||row.status}}</template></el-table-column></el-table><h3 class="trace-title">评审与处理任务</h3><el-table :data="context.jobs" empty-text="暂无相关任务"><el-table-column label="任务" min-width="180"><template #default="{row}"><el-button link type="primary" @click="router.push('/tasks/'+row.id)">{{row.name}}</el-button><small class="task-caption">{{jobNames[row.kind]||row.kind}} · {{row.mode==='live'?'真实执行':row.mode==='simulation'?'模拟模式':'本地处理'}}</small></template></el-table-column><el-table-column label="状态" width="100"><template #default="{row}">{{stateNames[row.status]||row.status}}</template></el-table-column><el-table-column label="源版本" width="125"><template #default="{row}"><el-tag v-if="row.source_version" :type="row.source_version===selected.version?'info':'warning'">v{{row.source_version}}{{row.source_version===selected.version?'':' · 历史'}}</el-tag><span v-else>未记录</span></template></el-table-column></el-table><p class="helper trace-limit">显示 {{context.modules.length}} / {{context.counts.modules}} 个模块、{{context.cases.length}} / {{context.counts.cases}} 个用例、最近 {{context.jobs.length}} / {{context.counts.jobs}} 个任务。完整记录可在对应管理页面查看。</p></template></el-tab-pane>
            <el-tab-pane label="版本对照" name="versions"><div class="version-controls"><el-select v-model="historyVersion" aria-label="选择历史版本"><el-option v-for="version in revisions" :key="version.version" :label="`v${version.version} · ${fmt(version.created_at)}`" :value="version.version"/></el-select><el-button v-if="store.canEdit" :disabled="!revision||revision.version===selected.version" :loading="operation" @click="restore">恢复为新版本</el-button></div><div v-if="revision" class="version-grid"><section><h3>历史版本 v{{revision.version}}</h3><h4>{{revision.name}}</h4><pre>{{revision.content||'（无正文）'}}</pre><h4>需求属性</h4><pre>{{JSON.stringify(revision.config,null,2)}}</pre></section><section><h3>当前版本 v{{selected.version}}</h3><h4>{{selected.name}}</h4><pre>{{selected.content||'（无正文）'}}</pre><h4>需求属性</h4><pre>{{JSON.stringify(selected.config,null,2)}}</pre></section></div></el-tab-pane>
            <el-tab-pane :label="`原始附件（${files.length}）`" name="files"><p class="helper">保留原始文档，上传新文件会解析并替换正文；历史正文仍可在版本对照中恢复。</p><el-button v-if="store.canEdit" :loading="importing" class="upload-requirement" @click="attachmentInput?.click()">上传 Word / Markdown 并更新正文</el-button><el-table :data="files" empty-text="暂无原始附件"><el-table-column prop="name" label="文件名称" min-width="180"/><el-table-column label="上传时间" width="130"><template #default="{row}">{{fmt(row.created_at)}}</template></el-table-column><el-table-column label="操作" width="80"><template #default="{row}"><a :href="row.url">下载</a></template></el-table-column></el-table></el-tab-pane>
          </el-tabs>
        </template>
      </section>
    </div>
    <input ref="fileInput" type="file" accept=".md,.doc,.docx" hidden @change="importFile($event)"/><input ref="attachmentInput" type="file" accept=".md,.doc,.docx" hidden @change="importFile($event,true)"/>
    <el-dialog v-model="editorOpen" :title="form.id?'编辑需求':'新建需求'" width="min(980px,94vw)" :close-on-click-modal="false" :before-close="closeEditor">
      <el-form label-position="top" :disabled="saving"><el-form-item label="需求标题" required><el-input v-model="form.name" maxlength="150" show-word-limit aria-label="需求标题"/></el-form-item><div class="editor-metadata"><el-form-item label="状态"><el-select v-model="form.config.status" aria-label="需求状态"><el-option v-for="(name,value) in requirementStates" :key="value" :label="name" :value="value" :disabled="value==='parsing'"/></el-select></el-form-item><el-form-item label="优先级"><el-select v-model="form.config.priority" aria-label="需求优先级"><el-option v-for="value in ['P0','P1','P2','P3']" :key="value" :label="value" :value="value"/></el-select></el-form-item><el-form-item label="负责人"><el-input v-model="form.config.owner" maxlength="100" placeholder="负责人姓名" aria-label="需求负责人"/></el-form-item><el-form-item label="父需求"><el-select v-model="form.parent" filterable clearable :value-on-clear="null" aria-label="父需求"><el-option v-for="parent in parents" :key="parent.id" :label="parent.name" :value="parent.id"/></el-select></el-form-item></div><el-form-item label="标签"><el-select v-model="form.config.tags" multiple filterable allow-create default-first-option :multiple-limit="20" placeholder="输入标签后回车添加"/></el-form-item><el-form-item label="业务描述（Markdown / 纯文本）"><el-input v-model="form.content" type="textarea" :rows="12" maxlength="500000" placeholder="建议使用 # 标题、## 功能模块、### 功能点组织需求" aria-label="需求正文"/></el-form-item><el-form-item label="验收标准"><el-input v-model="form.config.acceptance_criteria" type="textarea" :rows="4" maxlength="20000" placeholder="描述可验证的输入、行为、结果和异常场景" aria-label="需求验收标准"/></el-form-item><p class="helper">状态由团队人工维护；保存时保留已有配置，并检查版本冲突，避免覆盖他人修改。</p></el-form><template #footer><el-button :disabled="saving" @click="closeEditor()">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存需求</el-button></template>
    </el-dialog>
    <el-dialog v-model="jsonOpen" title="导入需求 JSON" width="min(720px,94vw)"><p class="helper">使用导出的 requirement 数组；批量导入创建独立需求，不恢复父子层级。</p><el-input v-model="jsonText" type="textarea" :rows="14" aria-label="需求 JSON"/><template #footer><el-button type="primary" :loading="jsonBusy" @click="importJson">导入</el-button></template></el-dialog>
    <el-dialog :model-value="!!linked" :title="linked?.name||'关联详情'" width="min(820px,94vw)" @close="linked=null"><pre class="linked-content">{{linked?.content||linked?.description}}</pre><pre class="linked-content">{{JSON.stringify(linked?.config||linked?.data||{},null,2)}}</pre></el-dialog>
  </div>
</template>

<style scoped>
.requirement-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}.requirement-stats>div{padding:18px 22px;display:flex;align-items:center;justify-content:space-between}.requirement-stats span{font-size:12px;color:var(--el-text-color-secondary)}.requirement-stats strong{font-size:27px;color:var(--el-color-primary)}.requirement-grid{display:grid;grid-template-columns:285px minmax(0,1fr);gap:18px;align-items:start}.requirement-sidebar{padding:16px;display:grid;gap:14px;min-height:540px}.section-heading,.detail-heading,.detail-badges,.sidebar-footer,.version-controls{display:flex;justify-content:space-between;align-items:center;gap:12px}.section-heading h3{font-size:15px}.section-heading span,.detail-badges,.detail-heading p{font-size:12px;color:var(--el-text-color-secondary)}.filter-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:8px}.requirement-tree{min-height:290px;max-height:620px;overflow:auto;background:transparent}.requirement-tree :deep(.el-tree-node__content){height:auto;min-height:55px;border-radius:6px}.requirement-node{display:grid;gap:6px;padding:10px 6px;border:0;background:transparent;color:inherit;text-align:left;min-width:0;width:100%}.requirement-node span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:13px}.requirement-node small{font-size:11px;color:var(--el-text-color-secondary)}.requirement-node.muted{opacity:.6}.sidebar-footer{border-top:1px solid var(--el-border-color);padding-top:12px;font-size:12px}.requirement-detail{min-height:650px;padding:24px;min-width:0}.detail-heading{align-items:flex-start}.detail-heading h2{font-size:23px;margin:12px 0;overflow-wrap:anywhere}.detail-badges{justify-content:flex-start;flex-wrap:wrap}.requirement-actions{display:flex;gap:8px;flex-wrap:wrap;padding:20px 0 10px}.requirement-actions .el-button{margin:0}.requirement-tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:14px}.requirement-document{padding:14px 0}.document-content,.acceptance pre,.version-grid pre,.linked-content{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;font-family:inherit;line-height:1.9;max-height:640px;overflow:auto;margin:14px 0}.document-content{font-family:inherit;padding:18px;background:var(--el-fill-color-light);border-radius:8px;min-height:210px}.acceptance{border-left:3px solid var(--el-color-primary);padding:16px 20px;background:var(--el-color-primary-light-9);margin:18px 0}.acceptance h3{font-size:14px}.acceptance pre{font-family:inherit;margin-bottom:0}.trace-summary{display:flex;gap:25px;flex-wrap:wrap;padding:16px;background:var(--el-fill-color-light);margin:12px 0}.trace-summary span{font-size:13px}.trace-summary b{color:var(--el-color-primary);font-size:20px;margin-left:8px}.trace-title{font-size:15px;margin:24px 0 12px}.module-chips{display:flex;flex-wrap:wrap;gap:8px}.module-chips .el-button{margin:0;max-width:100%;white-space:normal}.task-caption{display:block;color:var(--el-text-color-secondary);font-size:11px;margin-top:5px}.trace-limit{margin-top:12px}.version-controls{margin:15px 0}.version-controls .el-select{max-width:290px}.version-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.version-grid section{border:1px solid var(--el-border-color);border-radius:8px;padding:16px;min-width:0}.version-grid h3{font-size:14px;color:var(--el-color-primary);margin-bottom:15px}.version-grid h4{font-size:13px}.version-grid pre{font-size:12px;max-height:360px}.editor-metadata{display:grid;grid-template-columns:1fr 1fr 1fr 1.5fr;gap:14px}.import-notice{margin-bottom:16px}.upload-requirement{margin:14px 0}.linked-content{font:12px/1.8 ui-monospace,monospace}
@media(max-width:1200px){.requirement-grid{grid-template-columns:235px minmax(0,1fr)}.requirement-detail{padding:18px}.requirement-stats>div{padding:16px}.editor-metadata{grid-template-columns:1fr 1fr}.version-grid{grid-template-columns:1fr}}
@media(max-width:850px){.requirement-grid{grid-template-columns:1fr}.requirement-sidebar{min-height:0}.requirement-tree{min-height:0;max-height:230px}.requirement-stats{grid-template-columns:1fr 1fr}.detail-heading{flex-wrap:wrap}.editor-metadata{grid-template-columns:1fr}}
</style>
