/**
 * 质检批量运行的组合式函数（质检记录的"执行"入口）。
 * 职责：按 ?qualityWorkflow= 加载执行计划、维护各节点的样本草稿、提交批量检测、展示/回读运行结果。
 * 恢复与幂等（关键约束）：
 *   1) 提交前先固化 payload（含 idempotencyKey）并写入 sessionStorage 恢复点，提交成功后清除；
 *   2) 网络类失败（pendingFailure='unknown'）保留 payload 供用户"恢复原提交"重试同一幂等键，
 *      明确失败（400/401/403/404/422/429，'definite'）则清空，避免继续重试注定失败的请求；
 *   3) 存在待恢复记录时禁止直接新建检测，必须先明确选择"恢复"或"放弃"，避免产生重复检测；
 *   4) 恢复信息按工作区/用户/入口路径/工作流隔离，并在退出登录（token 清理、会话过期）时统一清除。
 * 加载/取消：计划加载与结果回读各持有 AbortController，参数变化时 abort 上一次。
 * 错误暴露：error（页面提示条）；提交过程中的异常不抛出，改由 pendingFailure 与 error 表达。
 */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { isAxiosError } from 'axios'
import { access, scopedKey } from '../api/request'
import { qualityRuns, type QualityBatchResult, type QualityRunPayload, type QualityRunPlan, type QualityRunSample } from '../api/qualityRuns'
import { createClientId } from '../utils/randomId'
import {
  QUALITY_RUN_RECOVERY_SCOPE,
  clearQualityRunRecovery,
  createQualityRunRecoveryStore,
  purgeExpiredQualityRunRecovery,
  purgeOtherQualityRunRecoveryScopes,
  qualityRunRecoveryKey,
  type QualityRunRecoveryRecord,
} from '../utils/qualityRunRecovery'

/** 样本草稿：在接口字段外附加前端唯一 key，用于列表 v-for 与增删定位（不提交到后端）。 */
export interface SampleDraft extends QualityRunSample { key: string }
/** 新建一条空样本草稿（sampleId 为空、无图片）；图片需求由所在分组的 imageRequired 决定。 */
export const newSample = (): SampleDraft => ({ key: createClientId(), sampleId: '', imageFileId: null })

/**
 * 质检运行状态机。
 * 返回：plan/loading/busy/error/result、groups（按节点分组的样本草稿）与 uploads/uploading、
 *   pending/pendingRecovery/pendingFailure（提交与恢复状态）、restorePending/discardPending、
 *   readResult/submit/nextBatch。
 * 副作用：submit 会提交批量检测并在成功后 router.replace 写入 ?qualityRun=，同时操作 sessionStorage 恢复点；
 *   注册 beforeunload 阻止"提交中/存在待提交内容"时关闭页面。
 */
export function useQualityRun() {
  const route = useRoute(), router = useRouter()
  const workflowId = shallowRef(''), plan = shallowRef<QualityRunPlan | null>(null), planLoading = shallowRef(false), resultLoading = shallowRef(false), busy = shallowRef(false)
  const loading = computed(() => planLoading.value || resultLoading.value)
  const error = shallowRef(''), result = shallowRef<QualityBatchResult | null>(null), confirmWrites = shallowRef(false)
  const groups = ref<Record<string, SampleDraft[]>>({}), pending = shallowRef<QualityRunPayload | null>(null)
  const pendingRecovery = shallowRef<QualityRunRecoveryRecord | null>(null)
  const pendingFailure = shallowRef<'unknown' | 'definite' | null>(null)
  const uploads = ref<Record<string, boolean>>({}), reload = shallowRef(0)
  const uploading = computed(() => Object.values(uploads.value).some(Boolean))
  const count = computed(() => Object.values(groups.value).reduce((sum, rows) => sum + rows.length, 0))
  let stopped = false, resultRequest: AbortController | null = null
  let resultTimer: ReturnType<typeof setTimeout> | null = null
  let activeWorkflowId = ''

  // 运行中的批次必须持续读取服务端终态；浏览器断开一次读取不等于检测已经失败。
  const activeStatuses = new Set(['running', 'waiting', 'cancel_requested'])
  function clearResultTimer() {
    if (resultTimer) clearTimeout(resultTimer)
    resultTimer = null
  }
  function scheduleResultRead(workflowId: string, executionId: string) {
    clearResultTimer()
    const execution = result.value?.execution
    if (stopped || !execution || execution.id !== executionId
      || !activeStatuses.has(execution.status)) return
    resultTimer = setTimeout(() => {
      resultTimer = null
      void readResult(workflowId, executionId)
    }, 3000)
  }

  const entryPath = () => route.path || '/quality/inspections'
  // 恢复点存储：必须等会话与工作流 id 就绪后再取，否则键会拼成不完整的作用域。
  const sessionStore = () => {
    if (!access.ready || !access.workspaceId || !access.userId || !workflowId.value) return null
    const scope = scopedKey(QUALITY_RUN_RECOVERY_SCOPE)
    return createQualityRunRecoveryStore(qualityRunRecoveryKey(scope, entryPath(), workflowId.value))
  }
  const emptyGroups = (value: QualityRunPlan) => Object.fromEntries(value.groups.map(group => [group.nodeId, [newSample()]]))
  const restoredGroups = (payload: QualityRunPayload) => Object.fromEntries(payload.groups.map(group => [
    group.nodeId,
    group.samples.map(sample => ({ ...sample, key: createClientId() })),
  ]))
  function cleanRecoveryStorage() {
    if (!access.ready || !access.workspaceId || !access.userId) return
    purgeExpiredQualityRunRecovery()
    purgeOtherQualityRunRecoveryScopes(scopedKey(QUALITY_RUN_RECOVERY_SCOPE + ':'))
  }
  const clearRecoveryOnLogout = () => clearQualityRunRecovery()
  const clearRecoveryOnTokenLogout = (event: StorageEvent) => {
    if (event.key === 'agent_token' && event.newValue === null) clearRecoveryOnLogout()
  }

  watch(() => [access.workspaceId, access.userId, access.ready], cleanRecoveryStorage, { immediate: true })
  watch([workflowId, reload], async ([id], _, cleanup) => {
    const request = new AbortController(); cleanup(() => request.abort())
    clearResultTimer()
    const switchedWorkflow = activeWorkflowId !== id
    activeWorkflowId = id
    if (switchedWorkflow) {
      pending.value = null
      pendingRecovery.value = null
      pendingFailure.value = null
    }
    plan.value = null; groups.value = {}; uploads.value = {}; confirmWrites.value = false; error.value = ''
    if (!id || typeof route.query.qualityRun === 'string') { planLoading.value = false; return }
    planLoading.value = true
    try {
      const value = (await qualityRuns.plan(id, request.signal)).data
      if (!request.signal.aborted) {
        plan.value = value
        if (pending.value) groups.value = restoredGroups(pending.value)
        else {
          groups.value = emptyGroups(value)
          pendingRecovery.value = sessionStore()?.load(id, entryPath()) || null
        }
      }
    } catch (cause) { if (!request.signal.aborted) error.value = (cause as Error).message }
    finally { if (!request.signal.aborted) planLoading.value = false }
  })
  async function readResult(id: string, runId: string) {
    clearResultTimer()
    resultRequest?.abort(); const active = new AbortController(); resultRequest = active
    resultLoading.value = true; error.value = ''
    try {
      const value = (await qualityRuns.result(id, runId, active.signal)).data
      if (!value?.execution) throw new Error('服务端未返回批次执行状态，请点击刷新结果重试')
      if (!active.signal.aborted) {
        result.value = value
        scheduleResultRead(id, runId)
      }
    }
    catch (cause) {
      if (!active.signal.aborted) {
        error.value = (cause as Error).message
        // 结果回读失败可能只是网络短暂中断；已有运行记录时继续排队回读，直到服务端进入终态。
        if (result.value?.execution?.id === runId && activeStatuses.has(result.value.execution.status)) scheduleResultRead(id, runId)
      }
    }
    finally { if (!active.signal.aborted) resultLoading.value = false }
  }
  watch(() => [route.query.qualityWorkflow, route.query.qualityRun], ([id, runId]) => {
    if (typeof id === 'string') {
      workflowId.value = id
      if (typeof runId === 'string') {
        if (result.value?.execution?.id !== runId || result.value.execution.workflowId !== id) void readResult(id, runId)
      } else {
        clearResultTimer()
        result.value = null
      }
    }
  }, { immediate: true })
  /**
   * 从本地恢复点还原待提交内容（用户选择"恢复原提交"时调用）。
   * 约束：恢复点的 workflowId 与 entryPath 必须与当前页面一致，否则忽略（避免把别的入口的提交恢复过来）。
   */
  function restorePending() {
    const recovery = pendingRecovery.value
    if (!recovery || recovery.workflowId !== workflowId.value || recovery.entryPath !== entryPath()) return
    pending.value = recovery.payload as QualityRunPayload
    confirmWrites.value = pending.value.confirmWrites
    groups.value = restoredGroups(pending.value)
    pendingFailure.value = 'unknown'
    pendingRecovery.value = null
    error.value = ''
  }
  /**
   * 放弃本地恢复信息（显式二次确认）：只清本地记录，不会撤销服务器上已产生的检测，
   * 因此确认文案要求用户先核对运行记录；清除后按最新 plan 重置样本草稿。
   */
  function discardPending() {
    if (busy.value || !window.confirm('仅清除本地恢复信息，不会撤销服务器上的检测。请先核对运行记录，确认仍要重新填写？')) return
    sessionStore()?.clear()
    pendingRecovery.value = null
    pending.value = null
    pendingFailure.value = null
    error.value = ''
    if (plan.value) {
      groups.value = emptyGroups(plan.value)
      confirmWrites.value = false
    }
  }
  /**
   * 提交批量检测。
   * 约束：忙碌中/有图片上传中/无计划时不提交；存在待恢复记录时先让用户二选一；
   *   首次提交前做完整前端校验（1–100 个样本、样本编号非空、外观样本必须有图、同组样本编号不重复），
   *   随后固化 payload（含幂等键）并落恢复点，保证失败后可安全重试同一请求。
   * 失败表现：网络类失败保留 payload 并把 pendingFailure 置为 'unknown'；明确业务失败清空并置 'definite'。
   */
  async function submit() {
    if (busy.value || uploading.value || !plan.value) return
    if (pendingRecovery.value) { error.value = '请先选择恢复原提交或放弃恢复信息，系统不会自动创建新的检测请求'; return }
    error.value = ''
    if (!pending.value) {
      if (!count.value || count.value > 100) { error.value = '每次运行需要 1–100 个设备样本组合'; return }
      for (const group of plan.value.groups) {
        const samples = groups.value[group.nodeId] || []
        if (!samples.length || samples.some(row => !row.sampleId.trim() || (group.imageRequired && !row.imageFileId))) { error.value = '请为每组填写样本编号，并为外观样本上传图片'; return }
        if (new Set(samples.map(row => row.sampleId.trim())).size !== samples.length) { error.value = '同一设备标准组不能使用重复样本编号'; return }
      }
      pending.value = { version: plan.value.version, publishedRevision: plan.value.publishedRevision, idempotencyKey: createClientId().replaceAll('-', ''),
        confirmWrites: confirmWrites.value,
        groups: plan.value.groups.map(group => ({ nodeId: group.nodeId, samples: groups.value[group.nodeId]!.map(row => ({ sampleId: row.sampleId.trim(), imageFileId: row.imageFileId })) })) }
      pendingFailure.value = null
      sessionStore()?.save(workflowId.value, entryPath(), pending.value)
    }
    busy.value = true
    try {
      const value = (await qualityRuns.submit(workflowId.value, pending.value)).data
      if (!value?.execution) throw new Error('运行响应未包含执行记录，请使用同一提交键核对，勿重复创建检测')
      if (!stopped) {
        sessionStore()?.clear()
        result.value = value; pending.value = null; pendingFailure.value = null
        scheduleResultRead(workflowId.value, value.execution.id)
        await router.replace({ query: { ...route.query, qualityWorkflow: workflowId.value, qualityRun: value.execution.id } })
      }
    } catch (cause) {
      if (!stopped) {
        error.value = (cause as Error).message
        if (isAxiosError(cause) && [400,401,403,404,422,429].includes(cause.response?.status || 0)) {
          sessionStore()?.clear()
          pending.value = null
          pendingFailure.value = 'definite'
        } else {
          pendingFailure.value = 'unknown'
          if (pending.value) sessionStore()?.save(workflowId.value, entryPath(), pending.value)
        }
      }
    } finally { if (!stopped) busy.value = false }
  }
  /** 切换到下一批检测：清空结果并移除 URL 上的 qualityRun/qualityWorkflow 参数，同时触发计划重新加载。 */
  async function nextBatch() {
    if (busy.value || pending.value || pendingRecovery.value) return
    result.value = null
    const query = { ...route.query }; delete query.qualityWorkflow; delete query.qualityRun
    await router.replace({ query }); reload.value++
  }
  function beforeUnload(event: BeforeUnloadEvent) { if (busy.value || pending.value) { event.preventDefault(); event.returnValue = '' } }
  window.addEventListener('beforeunload', beforeUnload)
  window.addEventListener('storage', clearRecoveryOnTokenLogout)
  window.addEventListener('cloud-brain-session-expired', clearRecoveryOnLogout)
  onBeforeUnmount(() => {
    stopped = true; clearResultTimer(); resultRequest?.abort()
    window.removeEventListener('beforeunload', beforeUnload)
    window.removeEventListener('storage', clearRecoveryOnTokenLogout)
    window.removeEventListener('cloud-brain-session-expired', clearRecoveryOnLogout)
  })
  return { workflowId, plan, loading, busy, error, result, confirmWrites, groups, pending, pendingRecovery, pendingFailure, restorePending, discardPending, uploads, uploading, count, reload, readResult, submit, nextBatch }
}
