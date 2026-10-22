<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { apiErrorMessage } from '../errors'
import { useWorkspace } from '../store'
import Icon from './Icon.vue'

const router = useRouter()
const workspace = useWorkspace()
const targetUrl = ref('')
const caseName = ref('')
const traceFile = ref<File | null>(null)
const picker = ref<HTMLInputElement>()
const saving = ref(false)
const recording = ref(false)
const sessionId = ref('')
const stopping = ref(false)
const projectReady = computed(() => Boolean(workspace.projectId))
const recorderScreenUrl = '/recorder/vnc.html?autoconnect=1&resize=scale&quality=8&compression=5&path=recorder/websockify'

const restoring = ref(true)
const busy = ref(false)
const actionCount = ref(0)
const recordingWarnings = ref<string[]>([])
const recordingError = ref('')
const recordedSteps = ref<any[]>([])
const cancelling = ref(false)
let pollTimer: number | undefined
let disposed = false
const actionNames: Record<string, string> = { goto:'打开页面', click:'点击', fill:'输入', press:'按键', select:'选择', check:'勾选' }

async function restoreSession() {
  try {
    const { data } = await api.get('entities/recording_status/')
    busy.value = Boolean(data.active && !data.session_id)
    sessionId.value = data.session_id || ''
    recording.value = Boolean(data.active && data.session_id)
    if (recording.value) targetUrl.value = data.target_url
    actionCount.value = data.count || 0
    recordedSteps.value = data.steps || []
    recordingWarnings.value = data.warnings || []
    recordingError.value = data.error || ''
  } finally {
    restoring.value = false
  }
}

async function pollSession() {
  if (disposed) return
  try {
    if (!saving.value && !stopping.value && !cancelling.value) await restoreSession()
  } catch {
    // Keep the known session until a successful response confirms it ended.
  } finally {
    if (!disposed) pollTimer = window.setTimeout(pollSession, recording.value ? 1200 : 3000)
  }
}
onMounted(pollSession)
onUnmounted(() => { disposed = true; window.clearTimeout(pollTimer) })

async function startRecording() {
  if (!projectReady.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (!workspace.canEdit) {
    ElMessage.warning('当前账号没有编辑权限，无法开始录制')
    return
  }
  if (recording.value) return
  saving.value = true
  try {
    const { data } = await api.post('entities/recording_start/', { project: workspace.projectId, url: targetUrl.value.trim() })
    sessionId.value = data.session_id
    recording.value = true
    actionCount.value = 1
    recordedSteps.value = [{action:'goto',selector:'',element_label:'录制起始页面'}]
    recordingError.value = ''
    recordingWarnings.value = []
    ElMessage.success('浏览器已启动，请在录制窗口中完成操作')
  } catch (error: unknown) {
    ElMessage.error(apiErrorMessage(error, '无法启动录制，请检查目标白名单和浏览器服务'))
  } finally {
    saving.value = false
  }
}

async function stopRecording() {
  if (!sessionId.value) return
  stopping.value = true
  try {
    const { data } = await api.post('entities/recording_stop/', { session_id: sessionId.value, name: caseName.value.trim() })
    sessionId.value = ''
    recording.value = false
    recordedSteps.value = []
    workspace.projectId = data.project
    if (data.config?.recording_warnings?.length) ElMessage.warning('用例已生成，录制注意事项已保存到用例备注，请复核后执行')
    else ElMessage.success(`已生成 Web 用例「${data.name}」`)
    await router.push('/ui-testing?platform=web')
  } catch (error: unknown) {
    ElMessage.error(apiErrorMessage(error, '结束录制失败；如浏览器已关闭，请取消会话后重试'))
  } finally {
    stopping.value = false
    await restoreSession().catch(() => {})
  }
}

async function cancelRecording() {
  if (!sessionId.value || stopping.value || cancelling.value) return
  cancelling.value = true
  try {
    await api.post('entities/recording_cancel/', { session_id: sessionId.value })
    sessionId.value = ''
    recording.value = false
    recordedSteps.value = []
    ElMessage.info('已取消录制，未生成用例')
  } catch {
    await restoreSession().catch(() => {})
  } finally {
    cancelling.value = false
  }
}

function selectTrace(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] || null
  if (file && !file.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请选择 Playwright Trace ZIP 文件')
    input.value = ''
    return
  }
  traceFile.value = file
}

async function saveRecording() {
  if (!projectReady.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (!traceFile.value) {
    ElMessage.warning('请先选择 Playwright Trace ZIP 文件')
    return
  }
  saving.value = true
  try {
    const payload = new FormData()
    payload.append('project', String(workspace.projectId))
    payload.append('file', traceFile.value)
    const { data } = await api.post('entities/import_playwright_recording/', payload)
    ElMessage.success(`录制已转换为 Web 用例「${data.name}」，请在用例列表中复核步骤和断言`)
    await router.push('/ui-testing?platform=web')
  } catch (error: unknown) {
    ElMessage.error(apiErrorMessage(error, '录制导入失败，请检查 Trace ZIP 格式后重试'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="ui-recorder-page">
    <el-button link class="back-button" @click="router.push('/ui-testing')"><Icon name="ArrowLeft" :size="14"/>返回 UI 自动化</el-button>
    <div class="page-heading">
      <div>
        <div class="heading-eyebrow">PLAYWRIGHT RECORDER</div>
        <h1><Icon name="VideoCamera"/> Web 录制工作台</h1>
        <p>在平台 Chromium 浏览器中操作录制，结束后自动生成可编辑的 Web 用例。</p>
      </div>
      <span class="recording-badge"><i/>平台 Chromium 录制</span>
    </div>

    <section class="panel recorder-target">
      <div class="recorder-section-title"><span class="recorder-step">1</span><div><h3>选择录制目标</h3><p>填写白名单中的 Web 地址，启动平台 Chromium 后直接操作页面。</p></div></div>
      <div class="recorder-target-row">
        <el-input v-model="targetUrl" placeholder="https://example.test/login" :disabled="recording || saving" clearable @keyup.enter="!recording && startRecording()"><template #prepend>页面 URL</template></el-input>
        <el-button v-if="!recording" type="primary" :loading="saving" :disabled="!workspace.canEdit || restoring || busy || !projectReady || !targetUrl.trim()" @click="startRecording"><Icon name="VideoCamera"/>开始录制</el-button>
        <el-button v-else type="danger" :loading="stopping" :disabled="cancelling || !!recordingError" @click="stopRecording"><Icon name="VideoCamera"/>结束并生成用例</el-button>
      </div>
      <el-alert v-if="busy" type="info" :closable="false" title="另一位用户正在录制，请等待该会话结束。"/>
      <el-alert v-if="recordingError" type="error" :closable="false" :title="recordingError"/>
      <el-alert v-for="warning in recordingWarnings" :key="warning" type="warning" :closable="false" :title="warning"/>
      <div v-if="recording" class="active-recorder"><div class="recorder-browser-heading"><span><i/>正在录制 · 已采集 {{ actionCount }} 个步骤</span><el-button text type="danger" :loading="cancelling" :disabled="stopping" @click="cancelRecording">取消录制</el-button></div><iframe :src="recorderScreenUrl" title="Playwright 录制浏览器" allow="clipboard-read; clipboard-write"/><div class="recorded-step-preview"><h4>已识别的操作与页面元素</h4><el-table :data="recordedSteps" size="small" max-height="250"><el-table-column label="#" type="index" width="54"/><el-table-column label="操作" width="90"><template #default="{row}">{{actionNames[row.action]||row.action}}</template></el-table-column><el-table-column label="页面文字 / 元素" min-width="160"><template #default="{row}">{{row.element_label||'未识别文本'}}</template></el-table-column><el-table-column label="字段定位器" min-width="200"><template #default="{row}"><code v-if="row.selector">{{row.selector}}</code><span v-else class="muted">页面地址</span></template></el-table-column><template #empty><el-empty description="点击或操作页面后，识别结果会显示在这里" :image-size="48"/></template></el-table></div><p>在浏览器画面中直接点击、输入、勾选和选择；平台会自动采集操作、页面文字/字段定位器，并生成可编辑的 Web 用例。录制最长 30 分钟，最多 100 步。请使用测试账号；跨窗口、iframe、上传和拖拽操作需手动配置。</p></div>
      <p v-else class="helper">开始前需将目标域名加入管理员配置的 TESTPILOT_ALLOWED_TARGETS。浏览器画面通过平台登录权限校验；仅录制创建者且具有项目编辑权限时可访问。</p>
    </section>

    <section v-if="!recording" class="panel recorder-import">
      <div class="recorder-section-title"><span class="recorder-step">2</span><div><h3>导入已有 Playwright Trace</h3><p>如果已有 Trace ZIP，也可以直接转换为 Web 用例。</p></div></div>
      <input ref="picker" type="file" accept=".zip,application/zip" hidden @change="selectTrace"/>
      <button class="trace-dropzone" :disabled="!workspace.canEdit" @click="picker?.click()">
        <span class="trace-upload-icon"><Icon name="UploadFilled" :size="24"/></span>
        <strong>{{ traceFile?.name || '选择 Trace ZIP 文件' }}</strong>
        <small>{{ traceFile ? `${(traceFile.size / 1024 / 1024).toFixed(2)} MB` : '选择 Playwright Trace ZIP（trace.zip）' }}</small>
      </button>
      <div class="recorder-actions">
        <span class="helper">保存后会创建 Web 用例，并出现在 UI 自动化的 Web 用例列表。</span>
        <el-button type="primary" :disabled="!workspace.canEdit" :loading="saving" @click="saveRecording"><Icon name="VideoCamera"/>生成 Web 用例</el-button>
      </div>
      <el-alert v-if="!workspace.canEdit" type="warning" :closable="false" title="当前账号没有编辑权限，无法导入录制用例。"/>
    </section>

    <section class="panel recorder-notes">
      <h3>录制流程</h3>
      <ol>
        <li>填写已加入白名单的目标页面，点击“开始录制”启动平台 Chromium。</li>
        <li>在浏览器画面中操作，完成后点击“结束并生成用例”。</li>
        <li>在 Web 用例列表中补充前置条件、预期结果和断言，复核敏感输入后再配置执行。</li>
      </ol>
      <el-form v-if="recording" label-position="top" class="recorded-case-name"><el-form-item label="用例名称（可选）"><el-input v-model="caseName" maxlength="150" placeholder="例如：登录页正常流程"/></el-form-item></el-form>
      <p class="helper">平台采集真实的点击、输入、按键、勾选和下拉选择，连续输入合并为一步。录制不代表测试通过；生成用例标记为“未验证”，临时 Trace 在结束后删除。</p>
    </section>
  </div>
</template>

<style scoped>
.ui-recorder-page{max-width:1100px;margin:0 auto}.ui-recorder-page>.page-heading{align-items:flex-start;margin-top:14px}.page-heading h1{display:flex;align-items:center;gap:10px}.recording-badge{display:flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid #d7e5dc;border-radius:999px;background:#f3faf5;color:#397550;font-size:12px}.recording-badge i,.recorder-browser-heading i{width:8px;height:8px;border-radius:50%;background:#4da879}.recorder-target,.recorder-import,.recorder-notes{padding:24px;margin-bottom:18px}.recorder-section-title{display:flex;align-items:flex-start;gap:14px;margin-bottom:20px}.recorder-step{display:grid;place-items:center;flex:none;width:30px;height:30px;border-radius:50%;background:#eef0ff;color:#5c60cf;font-weight:700}.recorder-section-title h3,.recorder-notes h3{margin:2px 0 5px}.recorder-section-title p{margin:0;color:var(--el-text-color-secondary);font-size:13px}.recorder-target-row{display:flex;gap:12px}.recorder-target-row .el-input{max-width:760px}.trace-dropzone{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;width:100%;min-height:175px;border:1px dashed #b9bfd4;border-radius:12px;background:#fafbff;color:inherit;cursor:pointer}.trace-dropzone:hover{border-color:#7478df;background:#f7f7ff}.trace-dropzone:disabled{cursor:not-allowed;opacity:.6}.trace-upload-icon{display:grid;place-items:center;width:46px;height:46px;border-radius:14px;background:#ededff;color:#6669d7}.trace-dropzone strong{font-size:14px}.trace-dropzone small{color:var(--el-text-color-secondary)}.recorder-actions{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:16px}.recorder-notes ol{padding-left:22px;color:var(--el-text-color-regular);line-height:2}.recorder-notes .helper{margin-top:10px}.active-recorder{margin-top:20px;border:1px solid var(--el-border-color);border-radius:10px;overflow:hidden}.recorder-browser-heading{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:#f7f8fc}.recorder-browser-heading span{display:flex;align-items:center;gap:8px;color:#397550;font-size:13px}.recorder-browser-heading i{background:#e85858}.active-recorder iframe{display:block;width:100%;height:min(65vh,760px);border:0;background:#1b1b1b}.recorded-step-preview{padding:12px 14px;border-top:1px solid var(--el-border-color)}.recorded-step-preview h4{margin:0 0 10px;font-size:13px}.recorded-step-preview code{overflow-wrap:anywhere;color:var(--el-color-primary)}.active-recorder>p{margin:0;padding:10px 14px;color:var(--el-text-color-secondary);font-size:12px}.recorded-case-name{max-width:560px}
</style>
