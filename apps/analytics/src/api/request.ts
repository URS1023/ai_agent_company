/**
 * 统一请求层：Cloud Brain 会话桥 + 路径改写 + 鉴权/工作区请求头 + 错误消息归一。
 * 职责：导出 get/post/put/del 四个方法（返回值直接是 data），并维护全局会话状态（userId/workspaceId/permissions）。
 * 关键约束：
 *   1) 只允许请求本平台 /api/ 下的接口，写死拦截外部 URL（错误文案见拦截器），外部数据必须走数据源/连接器；
 *   2) 旧路径通过 mappings 改写到 /api/v1/...，避免前端散落硬编码；
 *   3) 401 时若处于 iframe 内会向父窗口 postMessage('cloud-brain-session-expired')，由宿主统一弹登录；
 *   4) embeddedReportWorkspace 只读取路由里的 workspace_id，不写全局状态（防止串工作区）。
 */
/** Cloud Brain session bridge for the embedded analytics application. */
import axios, { type AxiosRequestConfig } from 'axios'
import { reactive, readonly } from 'vue'

const mappings = [
  ['/api/query-history', '/api/v1/analytics/history'],
  ['/api/data-analysis', '/api/v1/analytics'],
  ['/api/report-prompt', '/api/v1/report-prompts'],
  ['/api/report', '/api/v1/reports'],
]

const session = reactive({
  ready: false,
  userId: '',
  workspaceId: '',
  permissions: new Set<string>(),
})
/** 只读会话视图（userId/workspaceId/permissions/ready）；权限用 can() 判断，避免各页面自行解析权限码。 */
export const access = readonly(session)
/** 权限判定：参数是不翻译的权限码字符串（与后端 navigation.permissions 返回值一致）。 */
export const can = (permission: string) => session.permissions.has(permission)
/** 生成按"工作区 + 用户"隔离的 localStorage 键，避免多工作区共用同一浏览器时互相覆盖草稿。 */
export const scopedKey = (key: string) => ['cloud-brain', session.workspaceId, session.userId, key].join(':')

const WORKSPACE_ID_PATTERN = /^[A-Za-z0-9_-]{1,128}$/

/** Resolve the workspace embedded in a report route without changing global state. */
/**
 * 从（可能被宿主 iframe 包裹的）地址里解析报表内嵌路由的 workspace_id。
 * 只识别 /report/{view|designer|basic}/{id} 且 workspace_id 通过格式校验，其余情况返回空串；
 * 该函数不修改任何全局状态，调用方自行决定是否写入 localStorage。
 */
export function embeddedReportWorkspace(href = location.href) {
  const hostUrl = new URL(href, location.origin)
  const embeddedPath = hostUrl.searchParams.get('path')
  const routeUrl = new URL(embeddedPath || `${hostUrl.pathname}${hostUrl.search}`, location.origin)
  if (!/^\/report\/(?:view|designer|basic)\/[A-Za-z0-9_-]+$/.test(routeUrl.pathname)) return ''
  const workspaceId = routeUrl.searchParams.get('workspace_id') || ''
  return WORKSPACE_ID_PATTERN.test(workspaceId) ? workspaceId : ''
}

function mappedUrl(url: string) {
  for (const [before, after] of mappings) {
    if (url === before || url.startsWith(before + '/') || url.startsWith(before + '?')) return after + url.slice(before.length)
  }
  return url
}

// 请求拦截：拒绝跨域/非平台接口，改写旧路径，补 Authorization 与 X-Workspace-Id。
axios.interceptors.request.use((config) => {
  const url = new URL(mappedUrl(config.url || ''), location.origin)
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) {
    throw new Error('业务请求只能访问本平台接口；外部数据请先配置数据源或连接器。')
  }
  config.url = url.pathname + url.search
  const token = localStorage.getItem('agent_token')
  const workspace = embeddedReportWorkspace() || localStorage.getItem('agent_workspace_id')
  if (token) config.headers.set('Authorization', 'Bearer ' + token)
  if (workspace) config.headers.set('X-Workspace-Id', workspace)
  return config
})
// 响应拦截：把后端的 message/detail（含 Blob 响应体）统一塞进 error.message，
// 这样各页面的 catch 里直接展示 error.message 即可，无需再解析响应体。
axios.interceptors.response.use(
  (response) => response,
  async (error) => {
    let payload = error.response?.data
    if (payload instanceof Blob) {
      try { payload = JSON.parse(await payload.text()) } catch { payload = null }
    }
    error.message = payload?.message || payload?.detail || error.message || '请求失败'
    if (payload?.clarification && !String(error.message).includes(String(payload.clarification))) {
      error.message += '：' + payload.clarification
    }
    if (error.response?.status === 401 && window.parent !== window) {
      window.parent.postMessage({ type: 'cloud-brain-session-expired' }, location.origin)
    }
    return Promise.reject(error)
  },
)

/** GET 封装：params 走 query，config 可覆盖 signal/timeout/responseType；返回响应体 data（不再包一层 axios response）。 */
export async function get<T = any>(url: string, params: object = {}, config: AxiosRequestConfig = {}): Promise<T> {
  return (await axios.get(url, { ...config, params })).data
}
/** POST 封装：data 为请求体；返回响应体 data。 */
export async function post<T = any>(url: string, data?: unknown, config: AxiosRequestConfig = {}): Promise<T> {
  return (await axios.post(url, data, config)).data
}
/** PUT 封装：data 为请求体；更新类接口需在 body 里带 version 做乐观锁。 */
export async function put<T = any>(url: string, data?: unknown, config: AxiosRequestConfig = {}): Promise<T> {
  return (await axios.put(url, data, config)).data
}
/** DELETE 封装：删除参数通常通过 config.params（如 version）传递。 */
export async function del<T = any>(url: string, config: AxiosRequestConfig = {}): Promise<T> {
  return (await axios.delete(url, config)).data
}

/**
 * 初始化访问上下文：并发拉取当前用户与平台导航（含权限码），并把结果写入只读会话。
 * 副作用：写 session.userId/workspaceId/permissions 并置 ready=true；内嵌报表场景下会缓存 workspace_id 到 localStorage。
 * 失败表现：临时密码未修改时抛出业务错误（由调用方跳转改密页）；接口异常则原样抛出，页面应停留在未就绪态。
 */
export async function initializeAccess() {
  const [me, navigation] = await Promise.all([get('/api/auth/me'), get('/api/v1/platform/navigation')])
  if (me.user.must_change_password) throw new Error('请先从平台登录页修改临时密码。')
  session.userId = me.user.id
  session.workspaceId = me.workspace.id
  if (embeddedReportWorkspace() === me.workspace.id) {
    localStorage.setItem('agent_workspace_id', me.workspace.id)
  }
  session.permissions = new Set<string>(navigation.permissions)
  session.ready = true
}

/**
 * 导出分析结果并触发浏览器下载。
 * 参数：queryId（历史查询 id）、format（导出格式，作为兜底文件名后缀）。
 * 副作用：POST /api/v1/analytics/export（responseType=blob）→ 创建临时 <a> 点击下载 → 1s 后释放 objectURL。
 * 失败表现：无权限或结果过期时抛异常，调用方需提示用户重新执行查询。
 */
export async function downloadQuery(queryId: string, format: string) {
  const response = await axios.post('/api/v1/analytics/export', { queryId, format }, { responseType: 'blob' })
  const filename = String(response.headers['content-disposition'] || '').match(/filename="([^"]+)"/)?.[1] || ('analysis.' + format)
  const url = URL.createObjectURL(response.data)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export default axios
