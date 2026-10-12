/**
 * 前端入口：初始化会话 → 计算首屏路由 → 挂载到 #analytics-app，并监听宿主导航消息。
 * 关键约束：
 *   1) 必须先 await initializeAccess() 再挂载，权限码未就绪时路由守卫会全部判为无权限；
 *   2) 首屏路径优先取 ?path=（宿主内嵌），其次 /analytics-app 固定入口，否则用当前地址；
 *   3) workflow-standalone 类名只在"独立打开工作流页"时加，用于隐藏宿主外壳的间距样式；
 *   4) 初始化失败时用纯 DOM 兜底渲染错误文案（此时 Vue 尚未挂载，不能依赖组件）。
 */
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as icons from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import '@fortawesome/fontawesome-free/css/all.min.css'
import './styles/business.css'
import App from './App.vue'
import { router } from './router'
import { initializeAccess } from './api/request'

/**
 * 启动流程：等待会话就绪后解析首屏路径、挂载应用并注册宿主导航监听。
 * 失败表现：任何一步抛错都由调用方 catch 后渲染兜底错误页（含"请重新登录"提示）。
 */
async function start() {
  await initializeAccess()
  const hostUrl = new URL(location.href)
  const embeddedPath = hostUrl.searchParams.get('path')
  const path = embeddedPath || (hostUrl.pathname === '/analytics-app'
    ? '/analytics'
    : hostUrl.pathname + hostUrl.search)
  const standaloneWorkflow = window.parent === window && /^(?:\/workflows\/[A-Za-z0-9_-]+|\/(?:alerts|quality)\/devices\/[A-Za-z0-9_-]+\/workflow)(?:\/(?:access-point|logs|overview|run))?$/.test(hostUrl.pathname)
  document.documentElement.classList.toggle('workflow-standalone', standaloneWorkflow)
  document.body.classList.toggle('workflow-standalone', standaloneWorkflow)
  await router.replace(path)
  const app = createApp(App)
  app.use(ElementPlus, { locale: zhCn })
  app.use(router)
  for (const [name, component] of Object.entries(icons)) app.component(name, component)
  await router.isReady()
  app.mount('#analytics-app')
  const requestedRoute = new URL(path, location.origin)
  if (window.parent !== window && requestedRoute.searchParams.get('chat_preview') === '1'
      && router.currentRoute.value.path === '/unavailable') {
    window.parent.postMessage({ type: 'cloud-brain-chat-preview', status: 'error',
      message: String(router.currentRoute.value.query.reason || '当前账号没有查看这份大屏的权限。') }, location.origin)
  }
  window.addEventListener('message', (event) => {
    if (event.origin === location.origin && event.source === window.parent && event.data?.type === 'cloud-brain-module-navigate') {
      router.replace(event.data.path)
    }
  })
}
// 宿主（父窗口）通过 postMessage 通知切换模块；只接受同源且来自父窗口的消息，避免被第三方页面驱动跳转。
start().catch((error) => {
  const embeddedPath = new URL(location.href).searchParams.get('path')
  if (window.parent !== window && embeddedPath && new URL(embeddedPath, location.origin).searchParams.get('chat_preview') === '1') {
    window.parent.postMessage({ type: 'cloud-brain-chat-preview', status: 'error', message: error.message || '大屏初始化失败，请重新登录或刷新。' }, location.origin)
  }
  const root = document.querySelector('#analytics-app')
  if (root) {
    const message = document.createElement('p')
    message.className = 'business-page'
    message.setAttribute('role', 'alert')
    message.textContent = error.message || '模块初始化失败，请重新登录或刷新。'
    root.replaceChildren(message)
  }
})
