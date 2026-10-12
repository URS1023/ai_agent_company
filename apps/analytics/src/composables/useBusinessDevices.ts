/**
 * 业务设备工作台的组合式函数（预警 / 质检共用，由 domain 区分）。
 * 职责：设备目录分页查询（含关键字搜索）、设备设置弹窗的增改、移除确认、跳转设备工作流，以及列表状态记忆。
 * 加载/取消策略：每次 load 前 abort 上一次请求，并用 controller 比对丢弃过期响应；
 *   页码超界（如删除末页最后一条）会先收敛到最后一页再重新加载。
 * 状态记忆：关键字/页码/每页条数/滚动位置写入 sessionStorage（键按工作区+用户隔离），
 *   离开列表去工作流页再返回时可恢复到原位置。
 * 错误暴露：error（页面提示条）+ ElMessage/ElMessageBox 交互反馈；busy 为写操作忙碌锁，防止重复提交。
 * 权限：数据源下拉依赖 can('datasources.view')，无权限时不请求、选项为空。
 */
import { nextTick, onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { alerts, emptyAlertRecord, type AlertRecord } from '../api/alerts'
import { can, get, scopedKey } from '../api/request'
import { deviceWorkflowPath, type BusinessDeviceDomain } from '../api/deviceWorkflows'
import { openTopLevel } from '../utils/topLevelNavigation'

interface SavedListState { keyword: string; scrollY: number; page: number; pageSize: number }

/**
 * 设备列表状态机。
 * 参数：domain 为业务域（'alert' | 'quality'），切换时按该域恢复记忆的分页状态并重新加载。
 * 返回：列表/分页/加载与错误状态、设备表单状态，以及 load/changePage/changeSize/search/edit/save/remove/openWorkflow/closeForm。
 * 副作用：save/remove 会写库；openWorkflow 会以顶层方式打开工作流页（先记忆当前列表位置）。
 * 失败表现：异常统一写入 error，弹窗保持打开；删除与关闭表单前均有二次确认。
 */
export function useBusinessDevices(domain: Ref<BusinessDeviceDomain>) {
  const rows = shallowRef<AlertRecord[]>([])
  const sources = shallowRef<AlertRecord[]>([])
  const form = ref<AlertRecord | null>(null)
  const loading = shallowRef(false)
  const total = shallowRef(0), page = shallowRef(1), pageSize = shallowRef(12)
  const busy = shallowRef(false)
  const error = shallowRef('')
  const keyword = shallowRef('')
  const formOpen = shallowRef(false)
  let controller: AbortController | null = null
  let stopped = false
  let sourcesLoaded = false

  const stateKey = () => scopedKey(`business-device-list:${domain.value}`)

  function savedState(): SavedListState {
    try {
      const value = JSON.parse(sessionStorage.getItem(stateKey()) || '{}')
      return { keyword: typeof value.keyword === 'string' ? value.keyword : '', scrollY: Number(value.scrollY) || 0,
        page: Math.max(1, Math.floor(Number(value.page) || 1)), pageSize: [12, 24, 48].includes(value.pageSize) ? value.pageSize : 12 }
    } catch {
      return { keyword: '', scrollY: 0, page: 1, pageSize: 12 }
    }
  }

  function rememberList() {
    sessionStorage.setItem(stateKey(), JSON.stringify({ keyword: keyword.value, scrollY: window.scrollY, page: page.value, pageSize: pageSize.value }))
  }

  async function load(restoreScroll = false) {
    controller?.abort()
    controller = new AbortController()
    const active = controller
    loading.value = true
    error.value = ''
    try {
      const response = await get<{ data: { items: AlertRecord[]; total: number } }>('/api/v1/alerts/device-catalogue',
        { domain: domain.value, limit: pageSize.value, offset: (page.value - 1) * pageSize.value, keyword: keyword.value.trim() }, { signal: active.signal })
      if (stopped || controller !== active) return
      total.value = response.data.total
      const last = Math.max(1, Math.ceil(total.value / pageSize.value))
      if (page.value > last) { page.value = last; await load(restoreScroll); return }
      rows.value = response.data.items
      if (restoreScroll) {
        const top = savedState().scrollY
        await nextTick()
        if (!stopped) window.scrollTo({ top, behavior: 'auto' })
      }
    } catch (cause) {
      if (!active.signal.aborted && !stopped) error.value = (cause as Error).message
    } finally {
      if (!stopped && controller === active) loading.value = false
    }
  }

  async function loadSources() {
    if (sourcesLoaded || !can('datasources.view')) return
    const [http, database] = await Promise.all([
      get<any>('/api/v1/collection/sources', { limit: 200 }),
      get<any>('/api/v1/analytics/datasources', { limit: 200 }),
    ])
    sources.value = [...(http.data || []), ...(database.data || [])]
    sourcesLoaded = true
  }

  async function edit(device?: AlertRecord) {
    if (busy.value) return
    busy.value = true
    error.value = ''
    try {
      await loadSources()
      form.value = device?.id
        ? (await alerts.get('devices', device.id)).data
        : { ...emptyAlertRecord('devices'), domains: [domain.value] }
      formOpen.value = true
    } catch (cause) {
      error.value = (cause as Error).message
    } finally {
      busy.value = false
    }
  }

  async function save() {
    if (!form.value || busy.value) return
    busy.value = true
    error.value = ''
    try {
      if (!form.value.domains?.length) throw new Error('请至少选择一个适用业务')
      await alerts.save('devices', form.value)
      formOpen.value = false
      form.value = null
      ElMessage.success('设备设置已保存')
      await load()
    } catch (cause) {
      error.value = (cause as Error).message
    } finally {
      busy.value = false
    }
  }

  async function remove(device: AlertRecord) {
    if (busy.value) return
    try {
      await ElMessageBox.confirm(
        `移除“${device.name}”？历史流程、报告和运行记录仍会保留。`,
        '移除设备',
        { confirmButtonText: '确认移除', cancelButtonText: '取消' },
      )
      busy.value = true
      await alerts.remove('devices', device)
      ElMessage.success('设备已从当前列表移除')
      await load()
    } catch (cause) {
      if (cause instanceof Error) error.value = cause.message
    } finally {
      busy.value = false
    }
  }

  async function openWorkflow(device: AlertRecord, workflowId?: string) {
    if (!device.id) return
    rememberList()
    openTopLevel(workflowId ? `/workflows/${encodeURIComponent(workflowId)}` : deviceWorkflowPath(domain.value, device.id))
  }

  function search() {
    page.value = 1
    rememberList()
    void load()
  }

  function changePage(value: number) { page.value = value; rememberList(); void load() }
  function changeSize(value: number) { pageSize.value = value; page.value = 1; rememberList(); void load() }

  function closeForm(done: () => void) {
    if (!busy.value && window.confirm('关闭设备设置？尚未保存的修改将丢失。')) done()
  }

  watch(domain, () => {
    const state = savedState()
    keyword.value = state.keyword
    page.value = state.page; pageSize.value = state.pageSize
    rows.value = []
    void load(true)
  }, { immediate: true })
  onBeforeUnmount(() => {
    stopped = true
    controller?.abort()
    rememberList()
  })

  return {
    rows, sources, form, loading, total, page, pageSize, busy, error, keyword, formOpen,
    load, changePage, changeSize, search, edit, save, remove, openWorkflow, closeForm,
  }
}
