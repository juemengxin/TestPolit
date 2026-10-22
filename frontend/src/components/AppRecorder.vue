<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { allPages, api } from '../api'
import { useWorkspace } from '../store'

type Locator = { resource_id?: string; content_desc?: string; text?: string }
type Step = { action: string; value?: string | number; locator?: Locator; ref?: string; x?: number; y?: number }
type Recording = { id: number; project: number; resource: number; device_id: string; device_name: string; package_name: string; status: string; steps: Step[]; error: string; width: number; height: number; screen_version: number; has_screen: boolean; job_id: number }
const store = useWorkspace(), router = useRouter()
const resources = ref<any[]>([]), models = ref<any[]>([]), devices = ref<{id:string;name:string}[]>([])
const resourceId = ref<number>(), deviceId = ref(''), packageName = ref(''), name = ref('')
const session = ref<Recording>(), sessions = ref<Recording[]>([])
const busy = ref(false), loadingDevices = ref(false), restoring = ref(false)
const videoFile = ref<File>(), visionModelId = ref<number>(), videoBusy = ref(false), videoPicker = ref<HTMLInputElement>()
const input = ref(''), sensitive = ref(true), variable = ref('recorded_secret'), submit = ref(false)
const assertion = ref(''), waitSeconds = ref(1)
const labels: Record<string,string> = {launch_app:'启动应用',tap_field:'点击页面元素',tap:'旧版点击',type_text:'输入文本',press_button:'设备按键',swipe:'滑动',assert_text:'文本断言',wait_seconds:'等待'}
const working = computed(() => busy.value || session.value?.status === 'busy')
const ready = computed(() => !working.value && session.value?.status === 'ready' && store.canEdit)
const screenUrl = computed(() => session.value?.has_screen ? `/api/app-recordings/${session.value.id}/screen/?v=${session.value.screen_version}` : '')
let disposed = false, timer: number | undefined, generation = 0

function stepContent(step: Step): string {
  if (step.action === 'tap_field') {
    const locator = step.locator || {}
    if (locator.text) return `页面文字：「${locator.text}」`
    if (locator.content_desc) return `页面元素：「${locator.content_desc}」`
    if (locator.resource_id) return `页面元素 ID：${locator.resource_id}`
    return '未记录页面元素字段'
  }
  if (step.action === 'tap') {
    if (step.ref) return `页面元素：${step.ref}`
    if (typeof step.x === 'number' && typeof step.y === 'number') return '旧版坐标点击（建议重新录制为页面字段）'
    return '页面点击'
  }
  if (step.action === 'launch_app') return `打开应用：${session.value?.package_name || ''}`
  if (step.action === 'assert_text') return `校验页面文字：「${step.value ?? ''}」`
  if (step.action === 'type_text') return `输入：${step.value ?? ''}`
  return String(step.value ?? '—')
}

function updateSession(value: Recording) {
  session.value = value
}
async function restore() {
  const project = store.projectId, requestGeneration = ++generation
  window.clearTimeout(timer); session.value = undefined; sessions.value = []; devices.value = []; resources.value = []; models.value = []; resourceId.value = undefined; deviceId.value = ''
  if (!project || !store.canEdit) return
  restoring.value = true
  try {
    const [resourceRows, recordings] = await Promise.all([allPages('resources/', {project}), api.get('app-recordings/', {params:{project}})])
    if (disposed || requestGeneration !== generation) return
    resources.value = resourceRows.filter((item:any) => item.kind==='mcp' && item.enabled && item.mode === 'live')
    models.value = resourceRows.filter((item:any) => item.kind==='model' && item.enabled && item.settings?.provider_type!=='h2o_decisions')
    visionModelId.value = models.value[0]?.id
    resourceId.value = resources.value.find((item:any) => /mobile/i.test(item.name))?.id ?? resources.value[0]?.id
    sessions.value = recordings.data
    if (sessions.value.length) updateSession(sessions.value[0]!)
    schedulePoll()
  } finally { if (requestGeneration === generation) restoring.value = false }
}
function schedulePoll() { window.clearTimeout(timer); if (!disposed) timer = window.setTimeout(poll, 1800) }
async function poll() {
  const current = session.value, requestGeneration = generation
  try {
    if (current && !busy.value) {
      const {data} = await api.get(`app-recordings/${current.id}/`)
      if (!disposed && generation === requestGeneration && session.value?.id === current.id) updateSession(data)
    }
  } catch { /* API interceptor displays failures; keep the last known recording. */ }
  finally { if (requestGeneration === generation) schedulePoll() }
}
watch(() => [store.projectId, store.canEdit], restore, {immediate:true})
watch(resourceId, () => { devices.value = []; deviceId.value = '' })
onUnmounted(() => { disposed = true; generation++; window.clearTimeout(timer) })
async function findDevices() {
  if (!resourceId.value) return
  const project = store.projectId, resource = resourceId.value
  loadingDevices.value = true
  try {
    const {data} = await api.post('app-recordings/devices/', {project, resource})
    if (project !== store.projectId || resource !== resourceId.value) return
    devices.value = data; deviceId.value = data[0]?.id || ''
    if (!data.length) ElMessage.warning('未发现在线 Android 设备，请连接手机并授权 USB 调试')
  } finally { loadingDevices.value = false }
}
async function start() {
  if (!resourceId.value || !deviceId.value || !packageName.value.trim()) { ElMessage.warning('请选择设备并填写 Android 应用包名'); return }
  busy.value = true
  const project = store.projectId
  try {
    const {data} = await api.post('app-recordings/', {project, resource:resourceId.value, device_id:deviceId.value, package_name:packageName.value.trim()})
    if (project === store.projectId) { updateSession(data); schedulePoll() }
  } finally { busy.value = false }
}
async function command(action: string, values: Record<string,unknown> = {}) {
  if (!session.value || working.value || !store.canEdit) return
  const id = session.value.id
  busy.value = true
  try {
    const {data} = await api.post(`app-recordings/${id}/command/`, {action, ...values})
    if (session.value?.id === id) updateSession(data)
    if (action === 'type_text') input.value = ''
  } finally { busy.value = false }
}
async function clickScreen(event: MouseEvent) {
  const current = session.value
  const image = event.currentTarget as HTMLImageElement
  const bounds = image.getBoundingClientRect()
  if (!current || !ready.value || bounds.width <= 0 || bounds.height <= 0) return
  const x = Math.round((event.clientX - bounds.left) / bounds.width * current.width)
  const y = Math.round((event.clientY - bounds.top) / bounds.height * current.height)
  await command('tap_screen', {x, y, screen_version:current.screen_version})
}
function selectVideo(event:Event) {
  const inputElement=event.target as HTMLInputElement
  videoFile.value=inputElement.files?.[0]
}
async function analyzeVideo() {
  if(!session.value||!videoFile.value||!visionModelId.value||!store.projectId)return
  const form=new FormData()
  form.append('project',String(store.projectId));form.append('resource_id',String(session.value.resource))
  form.append('device_id',session.value.device_id);form.append('package_name',session.value.package_name)
  form.append('model_id',String(visionModelId.value));form.append('file',videoFile.value)
  videoBusy.value=true
  try {
    const {data}=await api.post('entities/generate_app_video_case/',form)
    ElMessage.success('录屏分析任务已提交，后台将抽取关键帧并生成未验证用例')
    await router.push(`/tasks/${data.task_id}`)
  } finally { videoBusy.value=false }
}
async function finish() {
  if (!name.value.trim()) { ElMessage.warning('请填写用例名称'); return }
  if (!session.value) return
  busy.value = true
  try {
    const {data} = await api.post(`app-recordings/${session.value.id}/finish/`, {name:name.value.trim()})
    ElMessage.success(`已生成 App 用例 #${data.case_id}，请复核步骤后执行`)
    session.value = undefined
    await router.push('/ui-testing?platform=app')
  } finally { busy.value = false }
}
async function cancel() {
  if (!session.value) return
  try { await ElMessageBox.confirm('取消后不会生成用例，已发生的手机操作不会撤销。', '取消 App 录制', {type:'warning'}) } catch { return }
  busy.value = true
  try { await api.post(`app-recordings/${session.value.id}/cancel/`); session.value = undefined; await restore() }
  finally { busy.value = false }
}
</script>

<template>
  <div class="app-recorder" v-loading="restoring">
    <div class="page-heading"><div><div class="heading-eyebrow">ANDROID · MOBILE MCP</div><h1>App 操作录制</h1><p>连接真实测试机，在设备画面中操作并生成 App 自动化用例。</p></div><el-tag type="success">真实设备操作</el-tag></div>
    <el-alert title="仅记录通过本页面发出的操作；手机上直接触摸不会自动采集。画面在每次操作后刷新，并非实时视频。" type="info" :closable="false" show-icon/>
    <section v-if="!session" class="panel app-setup">
      <h3>连接测试应用</h3>
      <el-form label-position="top" :disabled="busy || !store.canEdit">
        <div class="form-grid">
          <el-form-item label="Mobile MCP 服务（真实模式）"><el-select v-model="resourceId" placeholder="选择 Mobile MCP"><el-option v-for="item in resources" :key="item.id" :value="item.id" :label="item.name"/></el-select></el-form-item>
          <el-form-item label="测试设备"><div class="inline-actions"><el-select v-model="deviceId" placeholder="先检测设备"><el-option v-for="item in devices" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id"/></el-select><el-button :loading="loadingDevices" :disabled="!resourceId" @click="findDevices">检测设备</el-button></div></el-form-item>
        </div>
        <el-form-item label="Android 应用包名"><el-input v-model="packageName" placeholder="例如 com.example.app（不是应用显示名称）" maxlength="200"/></el-form-item>
        <p class="helper">手机需开启 USB 调试并授权。Docker 的 MCP 地址请使用 host.docker.internal:8933/mcp，同时加入后端目标白名单。</p>
        <el-button type="primary" :loading="busy" :disabled="!store.projectId || !deviceId || !packageName" @click="start">启动应用并开始录制</el-button>
      </el-form>
    </section>
    <template v-else>
      <section class="panel app-session">
        <div class="inline-actions"><el-tag :type="session.status==='error'?'danger':working?'warning':'success'">{{session.status==='error'?'需检查':working?'后台操作中':'录制中'}}</el-tag><strong>{{session.device_name || session.device_id}}</strong><span>{{session.package_name}}</span><el-button :disabled="working || !store.canEdit" @click="command('refresh')">刷新画面</el-button><el-button v-if="!session.steps.length" :disabled="working" @click="command('start')">重新启动应用</el-button></div>
        <el-select v-if="sessions.length > 1" :model-value="session.id" :disabled="working" @change="(id:number) => updateSession(sessions.find(item => item.id===id)!)"><el-option v-for="item in sessions" :key="item.id" :value="item.id" :label="`${item.device_name || item.device_id} · ${item.package_name}`"/></el-select>
        <el-alert v-if="session.error" :title="session.error" type="error" :closable="false" show-icon/>
        <p class="helper">步骤保存在后台，刷新页面可恢复。失败的操作不会自动重试，请先刷新画面核对设备状态。</p>
      </section>
      <section class="panel video-import">
        <div><h3>从录屏视频生成用例</h3><p class="helper">后台会抽取最多 12 个关键帧交给所选视觉模型分析，生成未验证的 App 用例；点击定位只使用字段，不使用坐标。</p></div>
        <div class="form-grid"><el-form-item label="视觉模型"><el-select v-model="visionModelId" placeholder="选择支持图片输入的模型"><el-option v-for="model in models" :key="model.id" :value="model.id" :label="model.name"/></el-select></el-form-item><el-form-item label="录屏视频"><input ref="videoPicker" type="file" accept=".mp4,.mov,.m4v,.webm,.mkv,video/*" hidden @change="selectVideo"><div class="inline-actions"><el-button @click="videoPicker?.click()">选择视频</el-button><span class="helper">{{videoFile?.name||'MP4 / MOV / WebM / MKV，最大 100 MB，最长 10 分钟'}}</span></div></el-form-item></div>
        <el-alert title="录屏画面会发送到所选模型服务分析。上传前请遮挡密码、验证码、个人信息及其他敏感内容；输入值只会生成运行时变量引用。" type="warning" :closable="false" show-icon/>
        <el-button type="primary" :loading="videoBusy" :disabled="!videoFile||!visionModelId||!store.canEdit" @click="analyzeVideo">分析视频并生成 App 用例</el-button>
      </section>
      <div class="recording-grid">
        <section class="panel phone-panel" v-loading="working">
          <img v-if="screenUrl" :src="screenUrl" :key="screenUrl" alt="点击页面中的文字或控件，自动识别并录制元素" class="device-screen" :class="{ 'screen-clickable': ready }" @click="clickScreen"/>
          <el-empty v-else description="等待后台获取设备截图"/>
          <p class="helper">{{session.width}} × {{session.height}} · 直接点击页面文字或控件，系统自动识别元素并按字段录制</p>
        </section>
        <section class="panel control-panel">
          <h3>录制操作</h3>
          <p class="helper">直接点击左侧设备画面中的按钮、文字或输入框即可录制。系统自动匹配页面元素；若无法识别，会提示重新点击可见控件。</p>
          <el-form label-position="top" :disabled="!ready">
            <el-form-item label="设备按键"><div class="inline-actions"><el-button @click="command('press_button',{value:'BACK'})">返回</el-button><el-button @click="command('press_button',{value:'HOME'})">主页</el-button><el-button @click="command('press_button',{value:'ENTER'})">回车</el-button></div></el-form-item>
            <el-form-item label="滑动"><div class="inline-actions"><el-button v-for="(label,direction) in {up:'↑ 上',down:'↓ 下',left:'← 左',right:'→ 右'}" :key="direction" @click="command('swipe',{value:direction})">{{label}}</el-button></div></el-form-item>
            <el-form-item label="输入文本（先点击左侧画面中的输入框）"><el-input v-model="input" :type="sensitive?'password':'text'" show-password maxlength="2000"/><div class="inline-actions"><el-checkbox v-model="sensitive">敏感输入</el-checkbox><el-checkbox v-model="submit">输入后提交</el-checkbox></div><el-input v-if="sensitive" v-model="variable" placeholder="运行时变量名，如 recorded_secret"/><p v-if="sensitive" class="helper">仅临时加密传输到设备，用例保存变量引用。执行时请在准备数据中填写该变量。</p><el-button :disabled="!input" @click="command('type_text',{value:input,sensitive,variable,submit})">录制输入</el-button></el-form-item>
            <el-form-item label="预期可见文本"><el-input v-model="assertion" maxlength="200" placeholder="精确匹配设备页面文本"/><el-button :disabled="!assertion.trim()" @click="command('assert_text',{value:assertion})">校验并添加断言</el-button></el-form-item>
            <el-form-item label="等待时间"><div class="inline-actions"><el-input-number v-model="waitSeconds" :min="0" :max="30"/><span>秒</span><el-button @click="command('wait_seconds',{value:waitSeconds})">添加等待</el-button></div></el-form-item>
          </el-form>
        </section>
      </div>
      <section class="panel step-panel">
        <h3>已录制步骤（{{session.steps.length}} / 100）</h3>
        <el-table :data="session.steps" max-height="320"><el-table-column type="index" width="60"/><el-table-column label="操作" width="150"><template #default="{row}">{{labels[row.action] || row.action}}</template></el-table-column><el-table-column label="页面元素 / 操作内容"><template #default="{row}">{{stepContent(row)}}</template></el-table-column></el-table>
        <p class="helper">生成后显示在 UI 自动化 → App 用例，标记为“未验证”。点击步骤按资源 ID、无障碍描述或文本字段重新定位；需在用例编辑中补充预期结果、必要等待并复核断言。</p>
        <div class="save-row"><el-input v-model="name" maxlength="150" placeholder="用例名称，例如：登录成功流程"/><el-button type="primary" :disabled="working || !session.steps.length || !store.canEdit" @click="finish">结束并生成 App 用例</el-button><el-button :disabled="working || !store.canEdit" @click="cancel">取消录制</el-button></div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.app-recorder{max-width:1180px;margin:18px auto}.app-setup,.app-session,.phone-panel,.control-panel,.step-panel{padding:24px;margin-top:18px}.app-setup .el-select{width:100%}.app-setup .el-form{max-width:900px}.app-session .inline-actions{flex-wrap:wrap}.app-session .el-alert{margin-top:14px}.recording-grid{display:grid;grid-template-columns:minmax(280px,1fr) minmax(320px,1fr);gap:20px}.phone-panel{text-align:center;min-height:400px;background:var(--el-fill-color-light)}.device-screen{display:block;max-width:100%;max-height:680px;width:auto;height:auto;margin:auto;border-radius:10px;box-shadow:0 5px 24px #0002}.screen-clickable{cursor:crosshair;touch-action:manipulation}.control-panel .el-form-item .el-button{margin-top:8px}.control-panel .inline-actions{flex-wrap:wrap}.control-panel .el-select{min-width:240px;flex:1}.save-row{display:flex;gap:12px;margin-top:20px}.save-row .el-input{max-width:500px}.helper{line-height:1.6}.app-session .el-select{margin-top:12px}@media(max-width:850px){.recording-grid{grid-template-columns:1fr}.save-row{flex-wrap:wrap}.device-screen{max-height:550px}}
.video-import{padding:24px;margin-top:18px}.video-import h3{margin:0 0 8px}.video-import .el-select{width:100%}.video-import .el-alert{margin:4px 0 16px}@media(max-width:850px){.video-import .form-grid{grid-template-columns:1fr}}
</style>
