/**
 * 设备预警工作台的组合式函数（alert 域）。
 * 职责：按 section（devices/points/advice/rules/reports/problems）驱动列表加载、增删改、报告与问题处置、
 *   策略历史版本查看，并托管"打开预警流程向导"的状态。
 * 加载/取消策略：列表请求持有单个 AbortController，切换 section 或重新 load 时先 abort 上一次；
 *   组件卸载置 stopped 后所有回调不再写状态（避免卸载后更新响应式数据）。
 * 错误暴露：所有动作经 action() 包一层 busy 忙碌锁，异常统一写入 error（页面用提示条展示），不向调用方抛出。
 * 权限：列表读写受 can('devices.view') / can('alerts.view') / can('datasources.view') 约束，
 *   无权限的下拉选项直接退化为空数组，不发起请求。
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import request, { can, get } from '../api/request'
import { alerts, alertBase, emptyAlertRecord, type AlertRecord, type AlertSection, type RegistryKind } from '../api/alerts'
import type { AlertWorkflowCreated } from '../api/alertWorkflows'

/**
 * 预警工作台状态机。
 * 参数：section 为当前页签（响应式），切换时自动重置分页/筛选与已打开的弹窗并重新加载。
 * 返回：列表与分页状态、各类弹窗状态（form/report/handling/revisions/workflow）、
 *   以及 load/edit/save/remove/openReport/reportPage/exportReport/openHandling/eventsPage/handle/history/createWorkflow/workflowCreated。
 * 副作用：执行保存、删除、处置、导出、创建流程；创建成功后 router.push 到工作流编辑页（用户已确认才触发）。
 * 失败表现：错误写入 error 并由页面展示；保存/处置失败时弹窗保持打开，用户填写内容不丢失。
 */
/** List requests are cancellable; writes are explicit and never triggered by navigation. */
export function useAlerts(section: Ref<AlertSection>) {
  const router = useRouter(), route = useRoute()
  const rows = shallowRef<AlertRecord[]>([]), loading = shallowRef(false), busy = shallowRef(false), error = shallowRef('')
  const keyword = shallowRef(''), statusFilter = shallowRef(''), offset = shallowRef(0)
  const devices = shallowRef<AlertRecord[]>([]), advice = shallowRef<AlertRecord[]>([]), sources = shallowRef<AlertRecord[]>([]), points = shallowRef<AlertRecord[]>([])
  const form = ref<AlertRecord | null>(null), formOpen = shallowRef(false), editingKind = shallowRef<RegistryKind>('devices')
  const report = shallowRef<AlertRecord | null>(null), reportOpen = shallowRef(false), problems = shallowRef<AlertRecord[]>([]), problemOffset = shallowRef(0)
  const handling = shallowRef<AlertRecord | null>(null), handlingOpen = shallowRef(false), actionStatus = shallowRef('acknowledged'), actionNote = shallowRef(''), events = shallowRef<AlertRecord[]>([]), eventOffset = shallowRef(0)
  const revisions = shallowRef<AlertRecord[]>([]), revisionsOpen = shallowRef(false)
  const workflowRule = shallowRef<AlertRecord | null>(null), workflowOpen = shallowRef(false)
  const permission = computed(() => ['devices', 'points'].includes(section.value) ? 'devices' : 'alerts')
  const registry = computed(() => ['devices', 'points', 'advice', 'rules'].includes(section.value))
  let controller: AbortController | null = null, stopped = false
  // 统一忙碌锁：任何写操作期间禁止重入（页面按钮用 busy 置灰，避免重复提交产生脏数据）。
  async function action(callback: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; try { await callback() } catch (cause) { if (cause instanceof Error) error.value = cause.message } finally { busy.value = false } }
  // 列表加载：先 abort 上一次请求再发新请求；只有"最后一次请求"与未卸载状态才允许回写结果。
  async function load() {
    controller?.abort(); controller = new AbortController(); const active = controller
    rows.value = []; loading.value = true; error.value = ''
    try {
      const response = await alerts.list(section.value, { offset: offset.value, ...(registry.value ? { keyword: keyword.value } : section.value === 'problems' ? { status: statusFilter.value } : {}) }, active.signal)
      if (active === controller && !stopped) rows.value = response.data
    } catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
    finally { if (active === controller && !stopped) loading.value = false }
  }
  // 下拉候选项按权限分别拉取，无权限时用空数组占位，保证表单仍可打开（只是选项为空）。
  async function loadChoices() {
    const [d, a, p, http, sql] = await Promise.all([
      can('devices.view') ? alerts.list('devices', { limit: 200 }) : { data: [] },
      can('alerts.view') ? alerts.list('advice', { limit: 200 }) : { data: [] },
      can('devices.view') ? alerts.list('points', { limit: 200 }) : { data: [] },
      can('datasources.view') ? get('/api/v1/collection/sources', { limit: 200 }) : { data: [] },
      can('datasources.view') ? get('/api/v1/analytics/datasources', { limit: 200 }) : { data: [] },
    ])
    devices.value = d.data; advice.value = a.data; points.value = p.data; sources.value = [...http.data, ...sql.data]
  }
  async function edit(row?: AlertRecord) { await action(async () => { editingKind.value = section.value as RegistryKind; await loadChoices(); form.value = row?.id ? (await alerts.get(editingKind.value, row.id)).data : emptyAlertRecord(editingKind.value); formOpen.value = true }) }
  async function save() { await action(async () => { if (!form.value) return; await alerts.save(editingKind.value, form.value); formOpen.value = false; form.value = null; ElMessage.success('配置已保存，未调用设备'); await load() }) }
  async function remove(row: AlertRecord) { await action(async () => { await ElMessageBox.confirm('移除“' + row.name + '”？仅从当前列表移除，保留历史。', '移除记录', { confirmButtonText: '确认移除', cancelButtonText: '取消' }); await alerts.remove(section.value as RegistryKind, row); await load() }) }
  async function openReport(id: string) { await action(async () => { const response = await alerts.get('reports', id); report.value = response.data; problemOffset.value = 0; problems.value = (await alerts.list('problems', { reportId: id, limit: 30 })).data; reportOpen.value = true }) }
  async function reportPage(next: number) { await action(async () => { if (!report.value) return; problems.value = (await alerts.list('problems', { reportId: report.value.id, offset: next, limit: 30 })).data; problemOffset.value = next }) }
  async function exportReport() { await action(async () => { if (!report.value) return; const response = await request.get(alertBase + '/reports/' + report.value.id + '/export', { responseType: 'blob' }); const url = URL.createObjectURL(response.data), link = document.createElement('a'); link.href = url; link.download = 'alert-report-' + report.value.id + '.html'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }) }
  async function openHandling(row: AlertRecord) { await action(async () => { handling.value = row; actionStatus.value = row.status === 'open' ? 'acknowledged' : row.status; actionNote.value = ''; eventOffset.value = 0; events.value = (await alerts.actions(row.id!)).data; handlingOpen.value = true }) }
  async function eventsPage(next: number) { await action(async () => { if (!handling.value?.id) return; events.value = (await alerts.actions(handling.value.id, next)).data; eventOffset.value = next }) }
  async function handle() { await action(async () => { if (!handling.value) return; if (!actionNote.value.trim()) throw new Error('请填写处置理由'); await alerts.handle(handling.value, actionStatus.value, actionNote.value); handlingOpen.value = false; await load(); if (report.value && reportOpen.value) problems.value = (await alerts.list('problems', { reportId: report.value.id, offset: problemOffset.value })).data }) }
  async function history(row: AlertRecord) { await action(async () => { revisions.value = (await alerts.revisions(row.id!)).data; revisionsOpen.value = true }) }
  function createWorkflow(row: AlertRecord) { workflowRule.value = row; workflowOpen.value = true }
  async function workflowCreated(value: AlertWorkflowCreated) {
    workflowOpen.value = false; workflowRule.value = null
    ElMessage.success(value.reused ? '已返回此前创建的预警流程' : '预警流程已创建并发布')
    await router.push('/workflows/' + value.workflow.id)
  }
  watch(section, () => { offset.value = 0; keyword.value = ''; statusFilter.value = ''; formOpen.value = false; reportOpen.value = false; void load() }, { immediate: true })
  watch(() => route.query.report, id => { if (typeof id === 'string') void openReport(id) }, { immediate: true })
  onBeforeUnmount(() => { stopped = true; controller?.abort() })
  return { rows, loading, busy, error, keyword, statusFilter, offset, devices, advice, sources, points, form, formOpen, editingKind, permission, registry, report, reportOpen, problems, problemOffset, handling, handlingOpen, actionStatus, actionNote, events, eventOffset, revisions, revisionsOpen, workflowRule, workflowOpen,
    load, edit, save, remove, openReport, reportPage, exportReport, openHandling, eventsPage, handle, history, createWorkflow, workflowCreated }
}
