/**
 * 数据分析（analytics）子应用路由表与访问控制。
 * 运行环境：以内存历史（createMemoryHistory）挂载在宿主平台的 iframe/容器里，路由不落浏览器地址栏；
 *   无 iframe 时由 afterEach 用 history.replaceState 同步地址，保证刷新与分享链接可用。
 * 权限模型：route.meta.permission 为权限码（如 workflows.view / quality.view / dashboards.edit），
 *   在 beforeEach 里统一校验；跨模块场景（预警、质检、报表设计器）另有额外的组合权限判断。
 * 约束：新增路由必须声明 meta.permission（公开页除外）；组件全部按需 import，避免首屏加载全部业务页面。
 */
import { createMemoryHistory, createRouter } from 'vue-router'
import { can } from './api/request'
import type { AlertSection } from './api/alerts'
import type { QualityKind } from './api/quality'

export const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/alerts/devices/:deviceId/workflow/:section(access-point|logs|overview|run)', component: () => import('./views/DeviceWorkflowWorkspace.vue'), props: route => ({ deviceId: String(route.params.deviceId), domain: 'alert' }), meta: { permission: 'workflows.view' } },
    { path: '/alerts/devices/:deviceId/workflow', component: () => import('./views/DeviceWorkflowWorkspace.vue'), props: route => ({ deviceId: String(route.params.deviceId), domain: 'alert' }), meta: { permission: 'workflows.view' } },
    { path: '/quality/devices/:deviceId/workflow/:section(access-point|logs|overview|run)', component: () => import('./views/DeviceWorkflowWorkspace.vue'), props: route => ({ deviceId: String(route.params.deviceId), domain: 'quality' }), meta: { permission: 'workflows.view' } },
    { path: '/quality/devices/:deviceId/workflow', component: () => import('./views/DeviceWorkflowWorkspace.vue'), props: route => ({ deviceId: String(route.params.deviceId), domain: 'quality' }), meta: { permission: 'workflows.view' } },
    { path: '/quality', component: () => import('./views/QualityInspectionWorkspace.vue'), meta: { permission: 'quality.view' } },
    { path: '/quality/:section(metrics|templates|inspections)', component: () => import('./components/quality/QualityWorkbench.vue'), props: route => ({ kind: route.params.section as QualityKind }), meta: { permission: 'quality.view' } },
    { path: '/alerts', component: () => import('./views/AlertWorkspace.vue') },
    { path: '/alerts/devices', redirect: to => ({ path: '/alerts', query: to.query }) },
    { path: '/alerts/points', component: () => import('./components/alerts/AlertWorkbench.vue'), props: { section: 'points' }, meta: { permission: 'devices.view' } },
    { path: '/alerts/:section(advice|rules|reports|problems)', component: () => import('./components/alerts/AlertWorkbench.vue'), props: route => ({ section: route.params.section as AlertSection }), meta: { permission: 'alerts.view' } },
    { path: '/workflows/:id/:section(access-point|logs|overview|run)', component: () => import('./views/WorkflowWorkspace.vue'), meta: { permission: 'workflows.view' } },
    { path: '/workflows/:id?', component: () => import('./views/WorkflowWorkspace.vue'), meta: { permission: 'workflows.view' } },
    { path: '/analytics', component: () => import('./views/AnalysisLayout.vue'), meta: { permission: 'analytics.view' }, children: [
      { path: '', component: () => import('./components/IntelligentQuery.vue') },
      { path: 'sql', component: () => import('./views/SqlWorkspace.vue') },
      { path: 'history', component: () => import('./views/QueryHistory.vue') },
      { path: 'quality', component: () => import('./views/QualityWorkspace.vue') },
    ] },
    { path: '/datasources', component: () => import('./views/DataSourcesWorkspace.vue'), meta: { permission: 'datasources.view' } },
    { path: '/analytics/sources', redirect: '/datasources' },
    { path: '/data-query', redirect: (to) => ({ path: '/analytics', query: to.query }) },
    { path: '/dashboards', component: () => import('./components/ReportManage.vue'), meta: { permission: 'dashboards.view' } },
    { path: '/report/designer/:id?', component: () => import('./components/JimuBigScreenReplica.vue'), meta: { permission: 'dashboards.view' } },
    { path: '/report/basic/:id', component: () => import('./components/ReportDesigner.vue'), meta: { permission: 'dashboards.edit' } },
    { path: '/report/view/:id', component: () => import('./components/ReportViewer.vue'), meta: { permission: 'dashboards.view' } },
    { path: '/templates', component: () => import('./views/TemplateCenter.vue'), meta: { permission: 'templates.view' } },
    { path: '/prompts', component: () => import('./views/PromptCenter.vue'), meta: { permission: 'prompts.view' } },
    { path: '/jimu-screen', redirect: '/report/designer' },
    { path: '/report', redirect: '/dashboards' },
    { path: '/:pathMatch(.*)*', component: () => import('./views/UnavailablePage.vue') },
  ],
})
/**
 * 全局前置守卫：集中做权限拦截，无权限统一跳到 /unavailable 并带上中文原因。
 * 返回：undefined 表示放行；返回路由对象表示重定向（不做任何请求，纯本地判断）。
 * 约束：权限码不得在此处硬编码中文，原因文案只用于展示；报表设计器对"新建/编辑"分别要求 dashboards.create / dashboards.edit。
 */
router.beforeEach((to) => {
  if (to.path === '/alerts' || to.path.startsWith('/alerts/devices/')) {
    if (!can('devices.view') || !can('alerts.view')) return { path: '/unavailable', query: { reason: '没有访问此设备预警页面的权限' } }
  }
  if (to.path === '/quality' || to.path.startsWith('/quality/')) {
    if (!can('quality.view') || (to.path.startsWith('/quality/devices/') && !can('devices.view'))) return { path: '/unavailable', query: { reason: '没有访问此质量检查页面的权限' } }
  }
  const permission = to.meta.permission as string | undefined
  if (permission && !can(permission)) return { path: '/unavailable', query: { reason: '没有访问此业务模块的权限' } }
  if (to.path === '/workflows/new' && !can('workflows.create')) return { path: '/unavailable', query: { reason: '没有创建工作流的权限' } }
  if (to.path.startsWith('/report/designer') && !can(to.params.id ? 'dashboards.edit' : 'dashboards.create')) {
    return to.params.id ? { path: '/report/view/' + to.params.id } : { path: '/unavailable', query: { reason: '没有创建报表的权限' } }
  }
})
/**
 * 全局后置守卫：让宿主地址与当前可见页面保持一致。
 * 副作用：内嵌（iframe）时向父窗口 postMessage('cloud-brain-module-route')；独立打开时改写浏览器地址。
 * 约束：被离开守卫拒绝（failure）时必须回退到 from，否则宿主 URL 会指向一个并未真正渲染的页面。
 */
router.afterEach((to, from, failure) => {
  // A rejected leave guard must keep the host URL aligned with the visible page.
  const visibleRoute = failure ? from : to
  if (window.parent !== window && visibleRoute.matched.length) {
    window.parent.postMessage({ type: 'cloud-brain-module-route', path: visibleRoute.fullPath }, location.origin)
  } else if (visibleRoute.matched.length && location.pathname + location.search !== visibleRoute.fullPath) {
    history.replaceState({}, '', visibleRoute.fullPath)
  }
})
