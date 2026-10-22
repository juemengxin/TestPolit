<script setup lang="ts">
import {computed} from 'vue'

const props=defineProps<{config:any;pageObjects:any[];resources:any[]}>()
defineEmits<{mode:[executable:boolean]}>()
const webActions=[['goto','打开页面'],['click','点击元素'],['fill','填写内容'],['press','按键'],['wait_visible','等待元素出现'],['assert_text','校验文案'],['assert_visible','校验元素可见'],['assert_url','校验页面地址'],['select','选择下拉项'],['check','勾选'],['hover','悬浮'],['screenshot','截图']]
const appActions=[['launch_app','启动应用'],['tap_field','按页面字段点击'],['tap_text','按可见文案点击'],['type_text','输入文本'],['press_button','按设备按键'],['swipe','滑动页面'],['wait_seconds','等待秒数'],['assert_text','校验屏幕文案'],['screenshot','截图']]
const draft=computed(()=>props.config.automation_status==='needs_mapping')
const steps=computed(()=>draft.value?props.config.design_steps:props.config.steps)
const actions=computed(()=>props.config.executor==='mobile-mcp'?appActions:webActions)
const liveResources=computed(()=>props.resources.filter(resource=>resource.enabled&&resource.mode==='live'))
function elements(pageId:number){return props.pageObjects.find(item=>item.id===pageId)?.config?.elements||{}}
function addStep(){if(steps.value.length>=100)return;steps.value.push(draft.value?{action:'填写操作步骤',expected:'填写预期结果'}:props.config.executor==='mobile-mcp'?{action:'assert_text',value:''}:{action:'click',selector:''})}
function prepareStep(step:any){if(step.action==='tap_field')step.locator={resource_id:'',content_desc:'',text:'',...(step.locator||{})}}
function prepareDesignStep(step:any){if(step.recorded_action==='tap_field')step.locator={resource_id:'',content_desc:'',text:'',...(step.locator||{})}}
function moveStep(index:number,offset:number){const target=index+offset;if(target<0||target>=steps.value.length)return;[steps.value[index],steps.value[target]]=[steps.value[target],steps.value[index]]}
</script>

<template>
  <div class="ui-editor">
    <div class="case-metadata">
      <el-form-item label="优先级"><el-select v-model="config.priority"><el-option v-for="priority in ['P0','P1','P2','P3']" :key="priority" :label="priority" :value="priority"/></el-select></el-form-item>
      <el-form-item label="用例类型"><el-select v-model="config.test_type"><el-option v-for="[value,label] in [['smoke','冒烟'],['functional','功能'],['boundary','边界'],['exception','异常'],['permission','权限'],['security','安全'],['compatibility','兼容']]" :key="value" :label="label" :value="value"/></el-select></el-form-item>
    </div>
    <el-form-item label="前置条件"><el-input v-model="config.preconditions" type="textarea" :rows="2" placeholder="例如：用户已登录，当前环境有可用测试数据"/></el-form-item>
    <el-alert v-if="config.platform==='web'&&!draft" title="Web 浏览器用例" description="按步骤保存可执行操作；运行环境可在列表页选择。页面对象可复用定位器。" type="info" :closable="false"/>
    <el-alert v-if="config.platform==='web'&&draft" title="设计草稿不会进入执行队列" description="先在公共数据保存 Web 基址，再从列表勾选用例并应用配置、转换步骤。" type="warning" :closable="false"/>
    <template v-else>
      <el-form-item label="测试 App 名称"><el-input v-model="config.app_name" placeholder="金姆脉知"/></el-form-item>
      <el-form-item label="Android 包名"><el-input v-model="config.package_name" placeholder="com.example.app"/></el-form-item>
      <el-form-item label="设备 ID"><el-input v-model="config.device_id" placeholder="已连接设备的 serial"/></el-form-item>
      <el-form-item label="Mobile MCP 资源"><el-select v-model="config.mcp_resource_id" clearable placeholder="选择已启用的真实执行资源"><el-option v-for="resource in liveResources" :key="resource.id" :label="resource.name" :value="resource.id"/></el-select></el-form-item>
      <el-alert v-if="draft" title="设计草稿不会进入执行队列" description="先在公共数据保存 App 名称、包名、设备与 Mobile MCP 资源；然后在列表勾选用例并应用配置、转换执行步骤。" type="warning" :closable="false"/>
      <template v-else>
        <div v-if="config.design_steps?.length" class="design-reference"><strong>需求设计步骤 · 映射参考</strong><ol><li v-for="(design,index) in config.design_steps" :key="index"><span>{{design.action}}</span><small v-if="design.expected">预期：{{design.expected}}</small></li></ol><p>执行步骤由你确认。系统可从预期结果生成文案断言；请在对应断言前补充点击、输入或滑动等实际操作。</p></div>
      </template>
    </template>
    <el-alert v-if="!draft&&config.verification_status==='unverified'" title="执行步骤尚未验证" :description="config.automation_notes?.length?`请先检查待补充内容：${config.automation_notes.join('；')}`:'步骤由设计说明生成，实际运行结果才可验证。'" type="warning" :closable="false"/>
    <el-form-item v-if="config.platform==='web'" label="默认网址"><el-input v-model="config.base_url" placeholder="https://test.example.com"/></el-form-item>
    <div class="step-heading"><strong>{{draft?'设计步骤':'执行步骤'}}</strong><el-button size="small" @click="addStep" :disabled="steps.length>=100">添加步骤</el-button></div>
    <el-empty v-if="!steps.length" description="添加第一步操作" :image-size="60"/>
    <article v-for="(step,index) in steps" :key="index" class="step-card">
      <header><span class="step-number">{{String(index+1).padStart(2,'0')}}</span><strong>{{draft?'操作设计':'步骤 '+(index+1)}}</strong><el-button text :disabled="index===0" @click="moveStep(index,-1)">上移</el-button><el-button text :disabled="index===steps.length-1" @click="moveStep(index,1)">下移</el-button><el-button text type="danger" @click="steps.splice(index,1)">移除</el-button></header>
      <template v-if="draft">
        <el-input v-model="step.action" placeholder="操作，例如：输入账号并提交"/>
        <template v-if="config.executor==='mobile-mcp'">
          <el-select v-model="step.recorded_action" placeholder="确认识别到的操作" @change="prepareDesignStep(step)"><el-option v-for="[value,label] in actions.filter(([value])=>['tap_field','type_text','press_button','swipe','wait_seconds','assert_text'].includes(value))" :key="value" :label="label" :value="value"/></el-select>
          <template v-if="step.recorded_action==='tap_field'"><el-input v-model="step.locator.resource_id" placeholder="资源 ID（如已知）"/><el-input v-model="step.locator.content_desc" placeholder="无障碍描述（如已知）"/><el-input v-model="step.locator.text" placeholder="画面可见的控件文本"/></template>
          <el-input v-if="step.recorded_action==='type_text'" v-model="step.value" placeholder="运行时变量，例如 {{ recorded_input_1 }}"/>
          <el-select v-if="step.recorded_action==='swipe'" v-model="step.value" placeholder="滑动方向"><el-option v-for="[value,label] in [['up','向上'],['down','向下'],['left','向左'],['right','向右']]" :key="value" :label="label" :value="value"/></el-select>
          <el-select v-if="step.recorded_action==='press_button'" v-model="step.value" placeholder="设备按键"><el-option v-for="button in ['BACK','HOME','ENTER','VOLUME_UP','VOLUME_DOWN']" :key="button" :label="button" :value="button"/></el-select>
          <el-input-number v-if="step.recorded_action==='wait_seconds'" v-model="step.value" :min="0" :max="600"/>
          <small class="helper">视频草稿只允许字段定位；点击至少补充一个可匹配字段，输入内容应填写运行时变量引用。</small>
        </template>
        <el-input v-model="step.expected" placeholder="预期结果，例如：进入首页并显示欢迎信息"/>
      </template>
      <template v-else>
        <el-select v-model="step.action" placeholder="选择操作" @change="prepareStep(step)">
          <el-option v-for="[value,label] in actions" :key="value" :label="label" :value="value"/>
        </el-select>
        <template v-if="config.executor!=='mobile-mcp'">
          <el-select v-model="step.page_id" clearable filterable placeholder="可选：使用页面对象" @change="step.element=''" >
            <el-option v-for="page in pageObjects" :key="page.id" :label="page.name" :value="page.id"/>
          </el-select>
          <el-select v-if="step.page_id" v-model="step.element" clearable filterable placeholder="选择页面元素">
            <el-option v-for="(selector,name) in elements(step.page_id)" :key="name" :label="name+' · '+selector" :value="name"/>
          </el-select>
          <el-input v-else-if="!['goto','assert_url','screenshot'].includes(step.action)" v-model="step.selector" placeholder="CSS 或 XPath 定位器，例如 #login-submit"/>
          <small v-if="config.platform==='web'&&step.element_label" class="recorded-element-label">录制识别：{{step.element_label}}</small>
        </template>
        <template v-if="config.executor==='mobile-mcp'">
          <template v-if="step.action==='tap_field'"><el-input v-model="step.locator.resource_id" placeholder="资源 ID，如 com.app:id/login"/><el-input v-model="step.locator.content_desc" placeholder="无障碍描述（content-desc）"/><el-input v-model="step.locator.text" placeholder="页面文本（精确匹配）"/><small class="helper">至少填写一个页面字段。运行时会从最新页面结构重新定位，拒绝含糊匹配；不使用坐标。</small></template>
          <template v-if="step.action==='tap'"><el-input v-model="step.ref" placeholder="兼容旧用例的临时 ref，例如 @e3"/><small class="helper">旧用例兼容字段。新录制请使用“按页面字段点击”。</small></template>
          <el-input v-if="step.action==='tap_text'" v-model="step.value" placeholder="屏幕上可见的按钮或文案；运行时动态定位"/>
          <template v-if="step.action==='type_text'"><el-input v-model="step.value" placeholder="输入文本"/><el-checkbox v-model="step.submit">输入后提交 Enter</el-checkbox></template>
          <el-select v-if="step.action==='press_button'" v-model="step.value" placeholder="选择设备按键"><el-option v-for="button in ['BACK','HOME','ENTER','VOLUME_UP','VOLUME_DOWN']" :key="button" :label="button" :value="button"/></el-select>
          <el-select v-if="step.action==='swipe'" v-model="step.value" placeholder="滑动方向"><el-option v-for="[value,label] in [['up','向上'],['down','向下'],['left','向左'],['right','向右']]" :key="value" :label="label" :value="value"/></el-select>
          <el-input-number v-if="step.action==='wait_seconds'" v-model="step.value" :min="0" :max="600" controls-position="right" placeholder="等待秒数（最多 600）"/>
        </template>
        <el-input v-if="['goto','fill','press','assert_text','assert_url','select'].includes(step.action)" v-model="step.value" :placeholder="step.action==='goto'?'路径，例如 /login':step.action==='fill'?'要填写的内容':step.action==='press'?'按键，例如 Enter':step.action==='assert_url'?'预期 URL 片段':'预期内容或选项值'"/>
      </template>
    </article>
    <el-form-item label="备注"><el-input v-model="config.notes" type="textarea" :rows="2" placeholder="补充特殊数据、环境约束或执行说明"/></el-form-item>
    <p class="helper">最多 100 步。步骤失败会停止当前用例，并保留截图与 Trace。</p>
  </div>
</template>

<style scoped>
.ui-editor{display:grid;gap:14px}.case-metadata{display:grid;grid-template-columns:1fr 1fr;gap:14px}.case-metadata :deep(.el-form-item){margin-bottom:0}.case-metadata :deep(.el-select){width:100%}.step-heading,.step-card header{display:flex;align-items:center;justify-content:space-between;gap:12px}.step-heading{margin-top:8px}.step-card{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px;border:1px solid var(--el-border-color);border-radius:10px;background:var(--el-fill-color-blank)}.step-card header{grid-column:1/-1;justify-content:flex-start}.step-number{display:grid;place-items:center;width:28px;height:28px;border-radius:8px;background:var(--el-color-primary-light-9);color:var(--el-color-primary);font-size:12px;font-weight:700}.step-card :deep(.el-select){width:100%}.step-card :deep(.el-input){min-width:0}.recorded-element-label{grid-column:1/-1;color:var(--el-text-color-secondary);font-size:12px}.design-reference{grid-column:1/-1;padding:12px 14px;border:1px solid var(--el-color-primary-light-7);border-radius:8px;background:var(--el-color-primary-light-9)}.design-reference ol{margin:8px 0;padding-left:22px}.design-reference li{padding:3px 0}.design-reference li small{display:block;color:var(--el-text-color-secondary)}.design-reference p{margin:6px 0 0;color:var(--el-text-color-secondary);font-size:12px}
</style>
