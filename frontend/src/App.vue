<script setup lang="ts">
import {ref, onMounted, onBeforeUnmount, computed} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useWorkspace} from './store'
import {api} from './api'
import Icon from './components/Icon.vue'
import {uiLanguage} from './i18n'
import enLocale from 'element-plus/es/locale/lang/en'
import zhLocale from 'element-plus/es/locale/lang/zh-cn'
const preference=ref({theme:'light',language:'zh'})
async function changePreference(){await api.patch('preferences/',preference.value);uiLanguage.value=preference.value.language;document.documentElement.classList.toggle('dark',preference.value.theme==='dark');document.documentElement.lang=preference.value.language==='en'?'en':'zh-CN'}
const en:Record<string,string>={'工作台':'Overview','项目管理':'Projects','模型管理':'Models','Skill 管理':'Skills','MCP 服务':'MCP Services','Agent 管理':'Agents','测试文档':'Documents','测试用例':'Test Cases','测试计划':'Test Plans','测试套件':'Test Suites','测试流程':'Flows','测试报告':'Reports','数据准备':'Data Preparation','AI 智能工作室':'AI Studio','需求管理':'Requirements','知识库':'Knowledge','Agent 对话':'Agent Chat','模板与策略':'Templates','接口自动化':'API Testing','UI 自动化':'UI Testing','任务中心':'Tasks','Skills 技能库':'Skill Library','系统管理':'System'}
function label(text:string){return preference.value.language==='en'?en[text]||text:text}
const store=useWorkspace(), route=useRoute(),router=useRouter()
const loading=ref(true),busy=ref(false),error=ref(false),loginForm=ref({username:'',password:''})
const nav=[{label:'智能中心',items:[['AI 智能工作室','/ai','MagicStick'],['需求管理','/requirements','DocumentChecked'],['知识库','/knowledge','Collection'],['Agent 对话','/chat','ChatDotRound']]},{label:'工作空间',items:[['工作台','/','Odometer'],['项目管理','/projects','FolderOpened']]},{label:'能力中心',items:[['模型管理','/resources/model','Connection'],['Skill 管理','/resources/skill','MagicStick'],['MCP 服务','/resources/mcp','Link'],['Agent 管理','/resources/agent','Cpu']]},{label:'测试管理',items:[['测试文档','/assets/document','Document'],['数据准备','/data-preparation','Coin'],['测试计划','/assets/plan','Calendar'],['测试套件','/assets/suite','FolderChecked'],['测试流程','/ui-testing?tab=ui_flow','SetUp'],['测试报告','/runs','DataAnalysis']]},{label:'自动化与治理',items:[['接口自动化','/api-testing','Connection'],['UI 自动化','/ui-testing','Monitor'],['任务中心','/tasks','Operation'],['模板与策略','/templates','SetUp'],['Skills 技能库','/skill-library','Shop'],['系统管理','/system','Setting']]}]
const title=computed(()=>nav.flatMap(n=>n.items).find(i=>i[1]===route.path)?.[0]||route.meta.title)
async function init(){loading.value=true;error.value=false;try{await store.init();if(store.user){preference.value=(await api.get('preferences/')).data;uiLanguage.value=preference.value.language;document.documentElement.classList.toggle('dark',preference.value.theme==='dark')}}catch{error.value=true}finally{loading.value=false}}
async function login(){busy.value=true;try{const {data}=await api.post('auth/login/',loginForm.value);store.user=data.user;await store.loadProjects();preference.value=(await api.get('preferences/')).data;uiLanguage.value=preference.value.language;document.documentElement.classList.toggle('dark',preference.value.theme==='dark')}finally{busy.value=false}}
async function logout(){await api.post('auth/logout/');store.user=null;store.projects=[];store.projectId=null;loginForm.value.password='';router.push('/')}
function handleAuthenticationExpired(){store.user=null;store.projects=[];store.projectId=null;loginForm.value.password=''}
onMounted(()=>{window.addEventListener('testpilot:auth-expired',handleAuthenticationExpired);void init()})
onBeforeUnmount(()=>window.removeEventListener('testpilot:auth-expired',handleAuthenticationExpired))
</script>
<template>
<el-config-provider :locale="preference.language==='en'?enLocale:zhLocale">
<div v-if="loading" class="boot">{{ $t('正在连接测试工作台…') }}</div>
<div v-else-if="error" class="boot"><h2>{{ $t('暂时无法连接服务') }}</h2><el-button @click="init">{{ $t('重新连接') }}</el-button></div>
<div v-else-if="!store.user" class="login-page">
 <div class="login-art"><div class="brand white"><span class="brand-logo">T<span>·</span></span>TestPilot</div><div><span class="eyebrow">INTELLIGENT TESTING WORKSPACE</span><h1>{{ $t('从想法到验证，') }}<br/>{{ $t('让每一次交付更可靠。') }}</h1><p>{{ $t('连接智能能力，沉淀测试资产。') }}<br/>{{ $t('属于团队的一站式智能测试工作台。') }}</p><div class="orbit"><div class="orbit-core"><Icon name="Cpu" :size="44"/></div><span>MODEL</span><span>AGENT</span><span>SKILL</span><span>MCP</span></div></div><small>{{ $t('TestPilot · 智能测试平台') }}</small></div>
 <form class="login-form" @submit.prevent="login"><span class="eyebrow">WELCOME BACK</span><h2>{{ $t('登录工作空间') }}</h2><p>{{ $t('使用你的团队账号，继续测试之旅') }}</p><label for="login-username">{{ $t('用户名') }}</label><el-input id="login-username" v-model="loginForm.username" size="large" :placeholder="$t('请输入用户名')" autocomplete="username" required/><label for="login-password">{{ $t('密码') }}</label><el-input id="login-password" v-model="loginForm.password" type="password" show-password size="large" :placeholder="$t('请输入密码')" autocomplete="current-password" required/><el-button type="primary" size="large" native-type="submit" :loading="busy">{{ $t('登录平台') }}<Icon name="Right"/></el-button><small>{{ $t('账号由管理员创建 · 数据安全保存在本地') }}</small></form>
</div>
<div v-else class="app-layout">
 <aside class="sidebar"><router-link to="/" class="brand"><span class="brand-logo">T<span>·</span></span><div>TestPilot<small>{{ $t('智能测试平台') }}</small></div></router-link>
 <div class="workspace-switch"><span class="workspace-icon"><Icon name="OfficeBuilding" :size="18"/></span><div>{{ $t('团队工作空间') }}<small>TEAM WORKSPACE</small></div><Icon name="ArrowDown" :size="12"/></div>
 <nav><section v-for="group in nav" :key="group.label"><p>{{$t(group.label)}}</p><router-link v-for="item in group.items" :key="item[1]" :to="item[1]" :class="{active:route.path===item[1]||(item[1]==='/runs'&&route.path.startsWith('/runs/'))}"><Icon :name="item[2]"/><span>{{label(item[0])}}</span><span v-if="item[1]==='/resources/agent'" class="nav-badge">AI</span></router-link></section></nav>
 <div class="sidebar-bottom"><span class="online-dot"></span>{{ $t('本地工作空间') }}<small>v2.0.0</small></div>
 </aside>
 <div class="main-layout"><header class="topbar"><div class="breadcrumbs">{{$t(route.meta.section)}}<span>/</span><b>{{$t(title)}}</b></div><div class="topbar-right"><el-button link :aria-label="preference.theme==='light'?'切换深色':'切换浅色'" @click="preference.theme=preference.theme==='light'?'dark':'light';changePreference()"><Icon :name="preference.theme==='light'?'Moon':'Sunny'" :size="17"/></el-button><el-button link @click="preference.language=preference.language==='zh'?'en':'zh';changePreference()">{{preference.language==='zh'?'EN':'中'}}</el-button><span class="project-label">{{ $t('当前项目') }}</span><el-select v-model="store.projectId" :placeholder="$t('选择项目')" style="width:185px"><el-option v-for="p in store.projects" :key="p.id" :value="p.id" :label="p.name"/></el-select><span class="divider"></span><el-dropdown @command="logout"><div class="user-menu"><span class="avatar">{{store.user.username.slice(0,1).toUpperCase()}}</span><span>{{store.user.username}}</span><Icon name="ArrowDown" :size="12"/></div><template #dropdown><el-dropdown-menu><el-dropdown-item command="logout">{{ $t('退出登录') }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></header>
 <main><div v-if="!store.projectId&&route.path!='/projects'" class="empty-workspace"><Icon name="FolderAdd" :size="48"/><h2>{{ $t('创建你的第一个项目') }}</h2><p>{{ $t('将模型、测试资产和执行报告组织到同一个工作空间。') }}</p><el-button type="primary" @click="router.push('/projects')">{{ $t('创建项目') }}</el-button></div><router-view v-else :key="String(route.params.kind||'')"/></main>
 <footer>{{ $t('TestPilot 智能测试平台') }}<span>{{ $t('让测试更智能，让交付更可靠') }}</span></footer></div>
</div>
</el-config-provider>
</template>
