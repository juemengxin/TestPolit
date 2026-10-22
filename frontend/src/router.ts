import { createRouter, createWebHistory } from 'vue-router'

const catalogView = () => import('./views/Catalog.vue')
const catalogTitles: Record<string, string> = {
  knowledge: '知识库',
  'ui-testing': 'UI 自动化',
  templates: '模板与策略',
  'skill-library': 'Skills 技能库',
}

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/Dashboard.vue'), meta: { title: '工作台', section: '概览' } },
    { path: '/projects', component: () => import('./views/Projects.vue'), meta: { title: '项目管理', section: '工作空间' } },
    { path: '/resources/:kind', component: () => import('./views/Resources.vue'), meta: { title: '智能资源', section: '能力中心' } },
    { path: '/assets/case', redirect: '/ui-testing' },
    { path: '/ui-testing/record', component: () => import('./views/UiRecorder.vue'), meta: { title: 'Playwright 录制', section: '智能测试' } },
    { path: '/assets/:kind', component: () => import('./views/Assets.vue'), meta: { title: '测试资产', section: '测试管理' } },
    { path: '/runs', component: () => import('./views/Runs.vue'), meta: { title: '测试报告', section: '测试管理' } },
    { path: '/runs/:id', component: () => import('./views/Runs.vue'), meta: { title: '报告详情', section: '测试管理' } },
    { path: '/data-preparation', component: () => import('./views/DataPreparation.vue'), meta: { title: '数据准备', section: '测试管理' } },
    { path: '/api-testing', component: () => import('./views/ApiWorkbench.vue'), meta: { title: '接口自动化', section: '智能测试' } },
    { path: '/api-testing/manage', component: catalogView, meta: { catalog: 'api-testing', title: '接口用例与配置', section: '智能测试' } },
    { path: '/requirements', component: () => import('./views/Requirements.vue'), meta: { title: '需求管理', section: '智能测试' } },
    ...Object.entries(catalogTitles).map(([catalog, title]) => ({
      path: `/${catalog}`,
      component: catalogView,
      meta: { catalog, title, section: '智能测试' },
    })),
    { path: '/ai', component: () => import('./views/AIStudio.vue'), meta: { title: 'AI 智能工作室', section: '智能中心' } },
    { path: '/chat', component: () => import('./views/AIStudio.vue'), meta: { title: 'Agent 对话', section: '智能中心' } },
    { path: '/tasks', component: () => import('./views/Tasks.vue'), meta: { title: '任务中心', section: '执行中心' } },
    { path: '/tasks/:id', component: () => import('./views/Tasks.vue'), meta: { title: '任务详情', section: '执行中心' } },
    { path: '/system', component: () => import('./views/System.vue'), meta: { title: '系统管理', section: '团队管理' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
