<script setup lang="ts">
import FailureTriage from '../components/FailureTriage.vue'
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {onBeforeRouteLeave,useRouter} from 'vue-router'
import {ElMessage,ElMessageBox} from 'element-plus'
import {api,allPages} from '../api'
import {useWorkspace} from '../store'
import RequestFields,{type RequestField} from '../components/RequestFields.vue'
import Icon from '../components/Icon.vue'
import AssertionEditor from '../components/AssertionEditor.vue'
import PreparedDataSelect from '../components/PreparedDataSelect.vue'
import {buildRequestTree,responseOutcome,type RequestNode} from '../apiWorkbench'

interface Draft {
  key:string; id?:number; version?:number; name:string; parent:number|null; config:Record<string,any>
  headers:RequestField[]; params:RequestField[]; extract:RequestField[]; variables:RequestField[]
  body:string; before:string; after:string; assertions:string; baseline:string
  job:any; busy:boolean; saving:boolean; requestTab:string; responseTab:string; environment:number|null; mode:string
}
const store=useWorkspace(),router=useRouter()
const items=ref<any[]>([]),modules=ref<any[]>([]),environments=ref<any[]>([]),models=ref<any[]>([]),drafts=ref<Draft[]>([])
const activeKey=ref(''),search=ref(''),moduleFilter=ref<number|undefined>(undefined),loading=ref(false)
const failureAnalysisModelId=ref<number|null>(null)
const importOpen=ref(false),importText=ref(''),importing=ref(false),secretName=ref(''),loadError=ref(false)
const active=computed(()=>drafts.value.find(draft=>draft.key===activeKey.value))
const requestTree=computed(()=>buildRequestTree(modules.value,items.value,search.value,moduleFilter.value))
const result=computed(()=>active.value?.job?.result?.cases?.[0])
const outcome=computed(()=>responseOutcome(active.value?.job))
const failureAnalysisModels=computed(()=>models.value.filter(model=>model.enabled&&model.settings?.provider_type!=='h2o_decisions'))
function modelLocation(model:any){
  try{
    const hostname=new URL(model.endpoint).hostname.toLowerCase()
    return hostname==='localhost'||hostname==='127.0.0.1'||hostname==='::1'||hostname==='host.docker.internal'||hostname.endsWith('.local')||/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(hostname)?'本地':'外部'
  }catch{return '未识别'}
}
function selectNode(node:RequestNode){if(node.item)open(node.item)}
const methods=['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS']
let generation=0
const timers=new Map<string,ReturnType<typeof setTimeout>>()
const projectDrafts=new Map<number,Draft[]>()
const hasChanges=()=>drafts.value.some(dirty)||Array.from(projectDrafts.entries()).some(([project,items])=>project!==store.projectId&&items.some(dirty))
const pretty=(value:unknown)=>typeof value==='string'?value:JSON.stringify(value,null,2)??''
const fields=(value:Record<string,unknown>={})=>Object.entries(value).map(([key,value])=>({key,value:typeof value==='string'?value:JSON.stringify(value),enabled:true}))
function fingerprint(draft:Draft){return JSON.stringify([draft.name,draft.parent,draft.config,draft.headers,draft.params,draft.extract,draft.body,draft.before,draft.after,draft.assertions])}
function dirty(draft:Draft){return fingerprint(draft)!==draft.baseline}
function clearTimers(){timers.forEach(timer=>clearTimeout(timer));timers.clear()}
async function load(){
  const project=store.projectId,token=++generation
  if(!project)return
  loading.value=true;loadError.value=false
  try{
    const [requests,folders,envs,configuredModels]=await Promise.all([allPages('entities/',{project,kind:'api'}),allPages('entities/',{project,kind:'module'}),allPages('entities/',{project,kind:'environment'}),allPages('resources/',{project,kind:'model'})])
    if(token!==generation)return
    items.value=requests;modules.value=folders;environments.value=envs;models.value=configuredModels
  }catch{if(token===generation)loadError.value=true}finally{if(token===generation)loading.value=false}
}
watch(()=>store.projectId,(project,previous)=>{
  if(previous)projectDrafts.set(previous,drafts.value)
  failureAnalysisModelId.value=null
  clearTimers();drafts.value=project?projectDrafts.get(project)||[]:[];activeKey.value=drafts.value[0]?.key||''
  moduleFilter.value=undefined;items.value=[];modules.value=[];environments.value=[];void load()
  if(project)for(const draft of drafts.value){
    if(draft.job&&['queued','running','awaiting_approval'].includes(draft.job.status)){draft.busy=true;void poll(draft,project,Date.now())}
  }
},{immediate:true})
function open(item?:any){
  const existing=item&&drafts.value.find(draft=>draft.id===item.id)
  if(existing){activeKey.value=existing.key;return}
  const key=item?`api-${item.id}`:`new-${crypto.randomUUID()}`
  if(drafts.value.some(draft=>draft.key===key)){activeKey.value=key;return}
  const config=JSON.parse(JSON.stringify(item?.config||{method:'GET',url:'',headers:{},params:{},body:{},assertions:[{type:'status',expected:200}],extract:{},before:[],after:[]}))
  const draft:Draft={key,id:item?.id,version:item?.version,name:item?.name||'',parent:item?item.parent:(moduleFilter.value&&moduleFilter.value>0?moduleFilter.value:null),config,
    headers:fields(config.headers),params:fields(config.params),extract:fields(config.extract),variables:[],body:pretty(config.body??{}),before:pretty(config.before??[]),after:pretty(config.after??[]),assertions:pretty(config.assertions??[]),baseline:'',job:null,busy:false,saving:false,requestTab:'headers',responseTab:'response-body',environment:null,mode:'live'}
  draft.baseline=fingerprint(draft);drafts.value.push(draft);activeKey.value=key
}
async function discard(message:string){try{await ElMessageBox.confirm(message,'未保存的更改',{confirmButtonText:'放弃更改',cancelButtonText:'继续编辑',type:'warning'});return true}catch{return false}}
async function close(key:string|number){
  const draft=drafts.value.find(item=>item.key===key)
  if(!draft||draft.busy||draft.saving)return
  if(dirty(draft)&&!await discard('关闭后将丢失此接口尚未保存的修改。'))return
  const timer=timers.get(draft.key);if(timer)clearTimeout(timer);timers.delete(draft.key)
  drafts.value=drafts.value.filter(item=>item.key!==key)
  if(activeKey.value===key)activeKey.value=drafts.value.at(-1)?.key||''
}
function dictionary(rows:RequestField[],label:string){
  const entries=rows.filter(row=>row.enabled&&(row.key.trim()||row.value))
  const keys=entries.map(row=>row.key.trim())
  if(keys.some(key=>!key)||new Set(keys).size!==keys.length)throw new Error(`${label}参数名不能为空或重复`)
  return Object.fromEntries(entries.map(row=>[row.key.trim(),row.value]))
}
function parse(value:string,label:string,array=false){
  let parsed:any
  try{parsed=JSON.parse(value)}catch{throw new Error(`${label}不是有效 JSON`)}
  if(array?!Array.isArray(parsed):!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error(`${label}必须是 JSON ${array?'数组':'对象'}`)
  return parsed
}
function validateSecretReferences(config:Record<string,any>){
  const visit=(value:any,path:string)=>{
    if(Array.isArray(value)){value.forEach((item,index)=>visit(item,`${path}[${index}]`));return}
    if(!value||typeof value!=='object')return
    for(const [key,item] of Object.entries(value)){
      const field=path?`${path}.${key}`:key
      if(/password|secret|token|authorization|cookie|api.?key|^key$/i.test(key)&&typeof item==='string'&&item&&!item.includes('{{secret:'))throw new Error(`${field} 是敏感配置，请填写 {{secret:密钥名称}}，并在服务端配置 TESTPILOT_SECRET_密钥名称`)
      visit(item,field)
    }
  }
  visit(config,'')
}
async function save(draft:Draft,notify=true){
  if(!store.canEdit||draft.saving)return false
  let payload:any
  try{
    if(!draft.name.trim()||!draft.config.url.trim())throw new Error('请填写接口名称和请求地址')
    const config={...draft.config,headers:dictionary(draft.headers,'Header'),params:dictionary(draft.params,'Query'),extract:dictionary(draft.extract,'提取变量'),body:parse(draft.body,'Body'),before:parse(draft.before,'前置操作',true),after:parse(draft.after,'后置操作',true),assertions:parse(draft.assertions,'断言',true)}
    validateSecretReferences(config)
    payload={project:store.projectId,kind:'api',name:draft.name.trim(),parent:draft.parent,config,...(draft.id?{version:draft.version}:{})}
  }catch(error){ElMessage.warning((error as Error).message);return false}
  draft.saving=true
  try{
    const {data}=await(draft.id?api.patch(`entities/${draft.id}/`,payload):api.post('entities/',payload))
    draft.id=data.id;draft.version=data.version;draft.config=data.config;draft.name=data.name;draft.baseline=fingerprint(draft)
    if(store.projectId===payload.project){const index=items.value.findIndex(item=>item.id===data.id);if(index<0)items.value.unshift(data);else items.value[index]=data}
    if(notify)ElMessage.success('接口已保存')
    return true
  }catch{return false}finally{draft.saving=false}
}
async function poll(draft:Draft,project:number,started:number){
  if(store.projectId!==project||!drafts.value.includes(draft))return
  try{
    const {data}=await api.get(`jobs/${draft.job.id}/`)
    if(store.projectId!==project||!drafts.value.includes(draft))return
    draft.job=data
    if(['queued','running','awaiting_approval'].includes(data.status)){
      if(Date.now()-started>120000){draft.busy=false;ElMessage.warning('任务仍在处理中，可打开任务详情继续查看');return}
      timers.set(draft.key,setTimeout(()=>void poll(draft,project,started),1200))
    }else{draft.busy=false;timers.delete(draft.key)}
  }catch{draft.busy=false}
}
async function run(){
  const draft=active.value,project=store.projectId
  if(!draft||!project||draft.busy)return
  let variables:Record<string,string>
  try{variables=dictionary(draft.variables,'运行变量')}catch(error){ElMessage.warning((error as Error).message);return}
  draft.busy=true
  try{
    if((!draft.id||dirty(draft))&&!await save(draft,false)){draft.busy=false;return}
    if(store.projectId!==project){draft.busy=false;return}
    draft.job=null
    const payload:any={entity_ids:[draft.id],environment_id:draft.environment,preparation_data:variables}
    if(failureAnalysisModelId.value)payload.failure_analysis_model_id=failureAnalysisModelId.value
    const {data}=await api.post('jobs/',{project,kind:'api',name:`调试 · ${draft.name}`,mode:draft.mode,payload})
    draft.job=data;draft.responseTab='response-body'
    void poll(draft,project,Date.now())
  }catch{draft.busy=false}
}
async function optimizeWithAi(){
  const draft=active.value
  if(!draft||!store.canEdit)return
  if((!draft.id||dirty(draft))&&!await save(draft,false))return
  router.push({path:'/ai',query:{operation:'optimize',entity:String(draft.id),target:'api'}})
}
async function addModule(){
  try{
    const project=store.projectId
    const {value}=await ElMessageBox.prompt('模块名称','新建模块',{inputValidator:value=>!!value?.trim()&&value.trim().length<=150||'请输入 1–150 个字符'})
    const {data}=await api.post('entities/',{project,kind:'module',name:value.trim(),config:{}})
    if(project===store.projectId){modules.value.push(data);moduleFilter.value=data.id}
  }catch{}
}
async function remove(){
  const draft=active.value,project=store.projectId
  if(!draft?.id)return
  try{await ElMessageBox.confirm(`删除「${draft.name}」及其版本记录？`,'删除接口',{type:'warning'});await api.delete(`entities/${draft.id}/`);if(project!==store.projectId)return;items.value=items.value.filter(item=>item.id!==draft.id);draft.baseline=fingerprint(draft);await close(draft.key)}catch{}
}
async function importSpec(){
  const project=store.projectId
  let spec:any
  try{spec=parse(importText.value,'OpenAPI 文档');if(!spec.paths||typeof spec.paths!=='object')throw new Error('OpenAPI 文档必须包含 paths')}catch(error){ElMessage.warning((error as Error).message);return}
  if(drafts.value.some(dirty)&&!await discard('同步可能更新已有接口，未保存的修改将被丢弃。'))return
  importing.value=true
  try{const {data}=await api.post('entities/sync_openapi/',{project,spec});if(project!==store.projectId)return;clearTimers();drafts.value=[];activeKey.value='';importOpen.value=false;await load();ElMessage.success(`已同步 ${data.synced} 个接口`)}catch{}finally{importing.value=false}
}
function setAuth(){
  if(!active.value)return
  if(!/^[A-Za-z0-9_]+$/.test(secretName.value)){ElMessage.warning('请输入服务端密钥名称，仅支持字母、数字和下划线');return}
  const value=`Bearer {{secret:${secretName.value}}}`
  const row=active.value.headers.find(row=>row.key.toLowerCase()==='authorization')
  if(row){row.value=value;row.enabled=true}else active.value.headers.push({key:'Authorization',value,enabled:true})
  ElMessage.success('已添加到 Headers，保存后生效')
}
function setQuerySecret(){
  if(!active.value)return
  if(!/^[A-Za-z0-9_]+$/.test(secretName.value)){ElMessage.warning('请输入服务端密钥名称，仅支持字母、数字和下划线');return}
  const value=`{{secret:${secretName.value}}}`
  const row=active.value.params.find(row=>row.key.toLowerCase()==='key')
  if(row){row.value=value;row.enabled=true}else active.value.params.push({key:'key',value,enabled:true})
  ElMessage.success('已添加安全的 key 参数引用，保存后生效')
}
function beforeUnload(event:BeforeUnloadEvent){if(hasChanges()){event.preventDefault();event.returnValue=''}}
onMounted(()=>window.addEventListener('beforeunload',beforeUnload))
onBeforeRouteLeave(async()=>!hasChanges()||await discard('离开工作台将丢失尚未保存的接口修改（包括其他项目的草稿）。'))
onBeforeUnmount(()=>{generation++;clearTimers();window.removeEventListener('beforeunload',beforeUnload)})
</script>

<template>
  <div class="api-workbench">
    <div class="page-heading">
      <div><div class="heading-eyebrow">API WORKSPACE</div><h1>接口自动化 <span class="heading-tag">{{items.length}}</span></h1><p>组织接口、构建请求，在同一工作台完成调试与验证。</p></div>
      <div class="inline-actions"><el-button @click="router.push('/api-testing/manage')"><Icon name="Setting"/>用例与配置</el-button><el-button v-if="store.canEdit" type="primary" @click="open()"><Icon name="Plus"/>新建接口</el-button></div>
    </div>
    <div class="workspace-nav panel"><strong>接口工作台</strong><el-button text @click="router.push('/api-testing/manage')">批量执行 / 环境 / 公共函数</el-button><span class="nav-spacer"/><el-button v-if="store.canEdit" text :disabled="drafts.some(draft=>draft.busy||draft.saving)" @click="importOpen=true">同步 OpenAPI</el-button><a :href="`/api/entities/export/?project=${store.projectId}&kind=api`">导出接口</a></div>
    <div class="workspace-grid">
      <aside class="request-sidebar panel" v-loading="loading">
        <div class="sidebar-heading"><h3>模块与接口</h3><el-button v-if="store.canEdit" text type="primary" @click="addModule">＋ 模块</el-button></div>
        <el-input v-model="search" clearable placeholder="搜索名称、路径或方法" aria-label="搜索接口"><template #prefix><Icon name="Search"/></template></el-input>
        <el-select v-model="moduleFilter" placeholder="全部模块" clearable @clear="moduleFilter=undefined" aria-label="筛选模块"><el-option label="未分组" :value="-1"/><el-option v-for="folder in modules" :key="folder.id" :label="folder.name" :value="folder.id"/></el-select>
        <div class="list-summary"><span>{{requestTree.count}} 个接口 · 含子模块</span><el-button text size="small" @click="load">刷新</el-button></div>
        <div v-if="loadError" class="sidebar-empty">加载失败，请点击刷新重试。</div>
        <div v-else-if="!requestTree.nodes.length" class="sidebar-empty"><Icon name="FolderOpened" :size="32"/><p>{{search?'没有匹配的接口':'此模块暂无接口'}}</p><el-button v-if="store.canEdit" link type="primary" @click="open()">创建第一个接口</el-button></div>
        <el-tree v-if="!loadError" class="module-tree" :data="requestTree.nodes" node-key="key" :current-node-key="active?.id?`api-${active.id}`:undefined" highlight-current default-expand-all :expand-on-click-node="true" @node-click="selectNode">
          <template #default="{data}"><button v-if="data.item" class="tree-request" :title="`${data.label}\n${data.item.config.url}`" @click.stop="open(data.item)"><span class="method" :class="data.item.config.method?.toLowerCase()">{{data.item.config.method||'GET'}}</span><span class="tree-label"><b>{{data.label}}</b><small>{{data.item.config.url}}</small></span></button><span v-else class="tree-folder"><Icon name="Folder"/><span class="tree-label">{{data.label}}</span><small>{{data.count}}</small></span></template>
        </el-tree>
        <p class="sidebar-note">接口和版本保存在当前项目中。切换项目暂存草稿，离开工作台前请保存。</p>
      </aside>
      <div class="request-main">
        <div v-if="!active" class="panel workspace-empty"><span class="empty-symbol"><Icon name="Connection" :size="38"/></span><h2>从一个请求开始</h2><p>从左侧打开接口，或创建新请求。调试结果将直接显示在这里。</p><el-button v-if="store.canEdit" type="primary" @click="open()">＋ 新建接口</el-button><div class="empty-features"><span>01 配置请求</span><span>02 选择环境</span><span>03 调试与断言</span></div></div>
        <template v-else>
          <section class="panel request-editor">
            <el-tabs v-model="activeKey" type="card" closable @tab-remove="close"><el-tab-pane v-for="draft in drafts" :key="draft.key" :name="draft.key" :label="`${dirty(draft)?'• ':''}${draft.name||'新请求'}`"/></el-tabs>
            <fieldset :disabled="!store.canEdit||active.busy||active.saving" class="editor-fields">
              <div class="request-line"><el-select v-model="active.config.method" aria-label="HTTP 方法" :disabled="!store.canEdit||active.busy||active.saving"><el-option v-for="method in methods" :key="method" :label="method" :value="method"/></el-select><el-input v-model="active.config.url" aria-label="请求地址" placeholder="https://api.example.com/users 或 /users"/><el-button type="primary" :disabled="!store.canEdit||active.saving" :loading="active.busy" @click="run"><Icon name="VideoPlay"/>调试</el-button><el-button :disabled="!store.canEdit||active.busy" :loading="active.saving" @click="save(active!)">保存</el-button><el-button v-if="store.canEdit" :disabled="active.busy||active.saving" @click="optimizeWithAi"><Icon name="MagicStick"/>AI 编辑 / 修复</el-button></div>
              <div class="request-meta"><el-input v-model="active.name" placeholder="接口名称" maxlength="150" aria-label="接口名称"/><el-select v-model="active.parent" clearable :value-on-clear="null" placeholder="所属模块" :disabled="!store.canEdit||active.busy||active.saving"><el-option v-for="folder in modules" :key="folder.id" :label="folder.name" :value="folder.id"/></el-select><el-select v-model="active.environment" clearable :value-on-clear="null" placeholder="不使用环境" :disabled="!store.canEdit||active.busy||active.saving"><el-option v-for="env in environments" :key="env.id" :label="env.name" :value="env.id"/></el-select><el-select v-model="active.mode" aria-label="执行模式" :disabled="!store.canEdit||active.busy||active.saving"><el-option label="真实请求" value="live"/><el-option label="模拟（不发请求）" value="simulation"/></el-select><el-select v-model="failureAnalysisModelId" clearable placeholder="失败分析模型（可选）" aria-label="失败分析模型" :disabled="!store.canEdit||active.busy||active.saving"><el-option v-for="model in failureAnalysisModels" :key="model.id" :value="model.id" :label="`${model.name} · ${modelLocation(model)}`"/></el-select></div>
              <p class="helper model-analysis-hint">选择后仅在真实接口用例失败时创建独立 AI 分析任务；不选择则使用本地规则分诊，模拟执行不会调用模型。<el-button v-if="!failureAnalysisModels.length" link type="primary" @click="router.push('/resources/model')">前往模型管理</el-button></p>
              <div class="request-hint"><span>{{active.id?`#${active.id} · v${active.version}`:'尚未保存'}} {{dirty(active)?'· 有未保存修改':''}}</span><span>调试会先保存当前请求 · 真实请求受目标白名单控制</span></div>
              <el-tabs v-model="active.requestTab" class="request-options">
                <el-tab-pane label="Headers" name="headers"><RequestFields v-model="active.headers" :disabled="!store.canEdit||active.busy||active.saving" key-label="Header 名称"/></el-tab-pane>
                <el-tab-pane label="Params" name="params"><p class="tab-help">API Key 等敏感参数必须引用服务端密钥，不能保存明文。</p><div class="auth-row"><el-input v-model="secretName" placeholder="密钥名称，例如 JUHE_API_KEY" aria-label="Query 密钥名称"/><el-button :disabled="!store.canEdit||active.busy||active.saving" @click="setQuerySecret">设置 key 参数</el-button></div><RequestFields v-model="active.params" :disabled="!store.canEdit||active.busy||active.saving" key-label="Query 参数"/></el-tab-pane>
                <el-tab-pane label="Body" name="body"><PreparedDataSelect :disabled="!store.canEdit||active.busy||active.saving" @apply="value=>{if(active)active.body=pretty(value)}"/><p class="tab-help">JSON 对象；GET / HEAD 不发送请求体。变量格式为双花括号包裹变量名。</p><el-input v-model="active.body" type="textarea" :rows="8" class="code-input" aria-label="JSON 请求体"/></el-tab-pane>
                <el-tab-pane label="Auth" name="auth"><p class="tab-help">Bearer 认证使用服务端密钥引用，不在接口配置中保存明文凭据。已有认证可在 Headers 中编辑。</p><div class="auth-row"><el-input v-model="secretName" placeholder="服务端密钥名称，例如 API_TOKEN" aria-label="认证密钥名称"/><el-button :disabled="!store.canEdit||active.busy||active.saving" @click="setAuth">设置 Bearer Header</el-button></div><p class="tab-help">由管理员配置对应的 TESTPILOT_SECRET_名称 环境变量。</p></el-tab-pane>
                <el-tab-pane label="运行变量" name="variables"><p class="tab-help">仅用于本次调试，不随接口保存。URL 可使用 /users/双花括号变量名 来替换路径参数；同名值覆盖环境变量。</p><RequestFields v-model="active.variables" :disabled="!store.canEdit||active.busy||active.saving" key-label="变量名"/></el-tab-pane>
                <el-tab-pane label="断言" name="assertions"><AssertionEditor :key="active.key" v-model="active.assertions" :disabled="!store.canEdit||active.busy||active.saving"/></el-tab-pane>
                <el-tab-pane label="提取变量" name="extract"><p class="tab-help">从响应 JSON 提取数据，路径示例：data.id 或 items.0.name。</p><RequestFields v-model="active.extract" :disabled="!store.canEdit||active.busy||active.saving" key-label="变量名" value-label="JSON 路径"/></el-tab-pane>
                <el-tab-pane label="前置 / 后置" name="hooks"><p class="tab-help">声明式操作支持 set、uuid、timestamp、concat。示例：[{"op":"uuid","name":"request_id"}]</p><div class="hook-grid"><label>前置操作<el-input v-model="active.before" type="textarea" :rows="6" class="code-input"/></label><label>后置操作<el-input v-model="active.after" type="textarea" :rows="6" class="code-input"/></label></div></el-tab-pane>
              </el-tabs>
            </fieldset>
            <div class="editor-footer"><span>取消勾选的参数不会发送或保存</span><el-button v-if="active.id&&store.canEdit" text type="danger" :disabled="active.busy||active.saving" @click="remove">删除接口</el-button></div>
          </section>
          <section class="panel response-panel">
            <div class="response-heading"><h3>响应结果</h3><div v-if="active.job" class="response-badges"><el-tag :type="outcome.type">{{outcome.label}}</el-tag><el-tag v-if="result?.response" type="info">HTTP {{result.response.status_code}} · {{result.response.duration_ms}} ms</el-tag><el-button text type="primary" @click="router.push('/tasks/'+active!.job.id)">任务 #{{active.job.id}} ↗</el-button></div></div>
            <div v-if="!active.job" class="response-empty"><Icon name="Document" :size="32"/><p>暂无响应数据</p><small>点击调试，查看响应、断言结果与提取变量。</small></div>
            <template v-else>
              <el-alert v-if="active.busy" :title="active.job.status==='queued'?'任务排队中，等待 Worker 执行…':'正在执行请求，结果自动更新…'" type="info" :closable="false"/>
              <el-alert v-if="result?.detail||active.job.result?.error" :title="result?.detail||active.job.result.error" :type="active.job.mode==='simulation'?'info':'warning'" :closable="false"/>
              <el-tabs v-model="active.responseTab">
                <el-tab-pane label="响应体" name="response-body"><pre v-if="result?.response">{{pretty(result.response.body)}}</pre><div v-else class="response-empty">{{active.job.mode==='simulation'?'模拟执行不会发送请求，也不会生成响应体。':'尚无响应体，可查看完整结果或任务详情。'}}</div></el-tab-pane>
                <el-tab-pane label="响应头" name="response-headers"><pre>{{pretty(result?.response?.headers||{})}}</pre></el-tab-pane>
                <el-tab-pane label="请求信息" name="request"><pre>{{pretty(result?.request||{})}}</pre></el-tab-pane>
                <el-tab-pane label="验证结果" name="checks"><el-table :data="result?.assertions||[]" empty-text="暂无断言结果"><el-table-column prop="type" label="断言" width="130"/><el-table-column label="结果" width="90"><template #default="{row}"><el-tag :type="row.passed?'success':'danger'">{{row.passed?'通过':'失败'}}</el-tag></template></el-table-column><el-table-column label="预期"><template #default="{row}">{{pretty(row.expected)}}</template></el-table-column><el-table-column label="实际"><template #default="{row}">{{row.detail||pretty(row.actual)}}</template></el-table-column></el-table></el-tab-pane>
                <el-tab-pane label="运行变量结果" name="response-variables"><pre>{{pretty(active.job.result?.variables||{})}}</pre></el-tab-pane>
                <el-tab-pane label="失败分诊" name="triage"><FailureTriage :data="active.job.result?.triage"/><p v-if="!active.job.result?.triage" class="tab-help">历史执行可在任务详情中提交失败分诊。</p></el-tab-pane>
                <el-tab-pane label="完整结果" name="all"><pre>{{pretty(active.job.result||{})}}</pre></el-tab-pane>
              </el-tabs>
            </template>
          </section>
        </template>
      </div>
    </div>
    <el-dialog v-model="importOpen" title="同步 OpenAPI" width="min(720px, 92vw)" :close-on-click-modal="false"><p class="tab-help">粘贴包含 paths 的 OpenAPI JSON。相同方法和路径的接口将更新为新版本。</p><el-input v-model="importText" type="textarea" :rows="15" class="code-input" aria-label="OpenAPI 文档"/><template #footer><el-button @click="importOpen=false">取消</el-button><el-button type="primary" :loading="importing" @click="importSpec">同步接口</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.tree-request{border:0;background:transparent;color:inherit;text-align:left;padding:0;cursor:pointer;width:100%}
.module-tree{max-height:580px;overflow:auto;background:transparent;--el-tree-node-hover-bg-color:var(--el-color-primary-light-9)}.module-tree :deep(.el-tree-node__content){height:auto;min-height:38px;padding-top:7px;padding-bottom:7px;border-radius:6px}.tree-folder,.tree-request{display:flex;gap:8px;align-items:center;min-width:0;flex:1;padding-right:8px}.tree-label{min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px}.tree-label b{font-weight:550}.tree-label small{display:block;overflow:hidden;text-overflow:ellipsis;color:var(--workspace-muted);margin-top:5px;font-size:11px}.tree-folder small{margin-left:auto;color:var(--workspace-muted);font-size:11px}.tree-folder .tree-label{flex:1}
.api-workbench{--workspace-muted:var(--el-text-color-secondary,#8b95a7)}.workspace-nav{display:flex;align-items:center;gap:16px;padding:12px 20px;margin-bottom:16px;flex-wrap:wrap}.workspace-nav strong{color:var(--el-color-primary);padding:8px 12px;background:var(--el-color-primary-light-9);border-radius:6px}.workspace-nav a{font-size:13px}.nav-spacer{flex:1}.workspace-grid{display:grid;grid-template-columns:265px minmax(0,1fr);gap:16px;align-items:start}.request-sidebar{padding:18px 14px;display:flex;flex-direction:column;gap:12px;min-height:650px}.sidebar-heading,.list-summary,.response-heading,.response-badges,.request-hint,.editor-footer{display:flex;align-items:center;justify-content:space-between;gap:12px}.sidebar-heading h3,.response-heading h3{font-size:15px}.list-summary{font-size:12px;color:var(--workspace-muted)}.request-list{display:grid;gap:5px;max-height:580px;overflow:auto}.request-item{border:1px solid transparent;background:transparent;text-align:left;display:flex;align-items:start;gap:10px;padding:12px 8px;width:100%;border-radius:7px;color:inherit}.request-item:hover,.request-item.selected{background:var(--el-color-primary-light-9);border-color:var(--el-color-primary-light-8)}.request-item>span:last-child{min-width:0}.request-item b{font-size:13px;font-weight:550;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.request-item small{display:block;color:var(--workspace-muted);margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.method{font-size:10px;font-weight:750;min-width:43px;padding-top:2px;color:#6877da}.method.get{color:#209b78}.method.post{color:#c18a26}.method.delete{color:#d85870}.sidebar-note{margin-top:auto;padding-top:24px;font-size:11px;line-height:1.7;color:var(--workspace-muted)}.sidebar-empty{display:grid;justify-items:center;gap:12px;padding:38px 0;color:var(--workspace-muted);font-size:13px}.request-main{min-width:0;display:grid;gap:16px}.workspace-empty{min-height:650px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;padding:40px;text-align:center}.workspace-empty h2{font-size:23px}.workspace-empty p{max-width:400px;line-height:1.8;color:var(--workspace-muted)}.empty-symbol{display:grid;place-items:center;background:var(--el-color-primary-light-9);color:var(--el-color-primary);width:80px;height:80px;border-radius:22px}.empty-features{display:flex;gap:24px;font-size:12px;color:var(--workspace-muted);padding-top:40px}.request-editor{padding:12px 20px 0;overflow:hidden}.editor-fields{border:0;margin:0;padding:4px 0;min-width:0}.request-line{display:grid;grid-template-columns:112px minmax(160px,1fr) repeat(3,auto);gap:8px}.request-line .el-button{margin:0}.request-meta{display:grid;grid-template-columns:1.3fr 1fr 1fr 150px minmax(190px,1fr);gap:10px;margin-top:14px}.model-analysis-hint{font-size:11px;line-height:1.6;margin:8px 0 0;color:var(--workspace-muted)}.request-hint{font-size:11px;color:var(--workspace-muted);margin:14px 0;flex-wrap:wrap}.request-options{min-height:260px}.request-options :deep(.el-tabs__content){padding:12px 0 20px}.tab-help{font-size:12px;color:var(--workspace-muted);line-height:1.8;margin-bottom:14px}.auth-row{display:flex;gap:10px;margin-bottom:12px}.hook-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.hook-grid label{display:grid;gap:8px;font-size:12px}.editor-footer{border-top:1px solid var(--el-border-color);font-size:11px;color:var(--workspace-muted);min-height:44px}.response-panel{padding:20px;min-height:300px;overflow:hidden}.response-heading{margin-bottom:16px;flex-wrap:wrap}.response-badges{justify-content:flex-end;flex-wrap:wrap}.response-empty{min-height:170px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:var(--workspace-muted);font-size:13px;text-align:center}.response-empty small{font-size:12px}.response-panel pre{font:12px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;margin:0;padding:16px;background:var(--el-fill-color-light);border-radius:7px}.response-panel :deep(.el-alert){margin-bottom:10px}
@media(min-width:1600px){.workspace-grid{grid-template-columns:300px minmax(0,1fr)}}
@media(max-width:1200px){.workspace-grid{grid-template-columns:215px minmax(0,1fr)}.request-meta{grid-template-columns:1fr 1fr}.workspace-nav{gap:6px}.request-line{grid-template-columns:95px minmax(100px,1fr) auto}.request-line>.el-button:last-child{grid-column:3}.request-editor{padding:12px 14px 0}}
@media(max-width:850px){.workspace-grid{grid-template-columns:1fr}.request-sidebar{min-height:0}.request-list{max-height:220px}.sidebar-note{display:none}.workspace-empty{min-height:380px}.request-line{grid-template-columns:100px 1fr}.request-line>.el-button:last-child{grid-column:auto}.hook-grid{grid-template-columns:1fr}.empty-features{gap:10px}.workspace-nav .nav-spacer{display:none}}
</style>
