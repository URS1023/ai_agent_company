/**
 * 质检流程向导（多设备标准组 + 检测器绑定）的组合式函数。
 * 职责：维护草稿（分组/模板修订/检测器/回写）、拉取可用 HTTP 检测服务、逐步校验并汇总首个阻断原因。
 * 关键约束：
 *   1) 每组必须选择设备与固定模板修订，且每个指标都要绑定可用检测服务（datasourceVersion 一并提交）；
 *   2) 预估节点数超过 100 直接阻断（避免生成过大的图）；
 *   3) 回写开关要求填写目标表与字段映射；分组上限 30 且至少保留 1 组；
 *   4) 表单整体替换（form.value = {...}）以触发派生校验重算。
 * 加载/取消：持有单个 AbortController，重复 loadSources 先 abort；卸载后不再写状态。
 * 错误暴露：error（页面提示条）；submit 失败返回 null 而不抛出，由界面决定是否保留向导弹窗。
 */
import { computed, onBeforeUnmount, ref, shallowRef } from 'vue'
import { get } from '../api/request'
import type { HttpSource } from '../api/collection'
import {
  newQualityWorkflowGroup, qualityWorkflows, type QualityWorkflowCreated,
  type QualityWorkflowDraft, type QualityWorkflowGroupDraft,
} from '../api/qualityWorkflows'
import { createClientId } from '../utils/randomId'

/** 草稿初值：默认一个空分组、关闭回写；requestKey 为幂等键，reset 时会重新生成（视为全新一次创建）。 */
function emptyDraft(): QualityWorkflowDraft {
  return {
    requestKey: createClientId().replaceAll('-', ''),
    name: '',
    description: '',
    groups: [newQualityWorkflowGroup()],
    writebackEnabled: false,
    writeback: { targetId: '', targetVersion: 1, mode: 'insert', recordPath: '$.data', fields: [], keys: [] },
  }
}

/**
 * 质检流程向导状态。
 * 返回：form（草稿）、sources（可选 HTTP 检测服务）、loading/busy/error、
 *   detectorCount/nodeCount（预估节点数）、incompleteReason/canSubmit（校验与提交闸门），
 *   以及 loadSources/updateGroup/addGroup/removeGroup/reset/submit。
 * 副作用：loadSources 只读；submit 会创建并发布质检工作流（成功后由调用方跳转/刷新）。
 */
export function useQualityWorkflowWizard() {
  const form = ref<QualityWorkflowDraft>(emptyDraft())
  const sources = shallowRef<HttpSource[]>([])
  const loading = shallowRef(false), busy = shallowRef(false), error = shallowRef('')
  let request: AbortController | null = null, stopped = false

  const detectorCount = computed(() => form.value.groups.reduce((sum, group) => sum + group.detectors.length, 0))
  const nodeCount = computed(() => 2 + detectorCount.value + form.value.groups.length
    + form.value.groups.filter(group => group.detectors.length > 1).length
    + Number(form.value.groups.length > 1) + Number(form.value.writebackEnabled))
  const incompleteReason = computed(() => {
    if (!form.value.name.trim()) return '请填写检测流程名称'
    if (!form.value.groups.length) return '至少添加一个设备标准组'
    for (const [index, group] of form.value.groups.entries()) {
      if (!group.deviceId) return `第 ${index + 1} 组尚未选择设备`
      if (!group.templateId || !group.templateRevision) return `第 ${index + 1} 组尚未选择固定模板修订`
      if (!group.detectors.length) return `第 ${index + 1} 组没有可自动检测的指标绑定`
      if (group.detectors.some(item => !item.datasourceId || !item.datasourceVersion)) return `第 ${index + 1} 组尚未为每个指标选择可用检测服务`
    }
    if (nodeCount.value > 100) return '生成流程超过 100 个节点，请拆分检测流程'
    if (form.value.writebackEnabled && (!form.value.writeback.targetId || !form.value.writeback.fields?.length)) return '请完整配置授权写入目标及字段映射'
    return ''
  })
  const canSubmit = computed(() => !loading.value && !busy.value && !incompleteReason.value)

  async function loadSources() {
    request?.abort(); const active = new AbortController(); request = active
    loading.value = true; error.value = ''
    try {
      const response = await get<{ data: HttpSource[] }>('/api/v1/collection/sources', { limit: 500 }, { signal: active.signal })
      if (!active.signal.aborted && !stopped) sources.value = response.data
    } catch (cause) {
      if (!active.signal.aborted && !stopped) error.value = (cause as Error).message
    } finally {
      if (active === request && !stopped) loading.value = false
    }
  }

  function updateGroup(index: number, group: QualityWorkflowGroupDraft) {
    form.value = { ...form.value, groups: form.value.groups.map((item, i) => i === index ? group : item) }
  }
  function addGroup() {
    if (form.value.groups.length < 30) form.value = { ...form.value, groups: [...form.value.groups, newQualityWorkflowGroup()] }
  }
  function removeGroup(index: number) {
    if (form.value.groups.length > 1) form.value = { ...form.value, groups: form.value.groups.filter((_, i) => i !== index) }
  }
  function reset() { form.value = emptyDraft(); error.value = '' }
  async function submit(): Promise<QualityWorkflowCreated | null> {
    if (!canSubmit.value) return null
    busy.value = true; error.value = ''
    try { return (await qualityWorkflows.create(form.value)).data }
    catch (cause) { if (!stopped) error.value = (cause as Error).message; return null }
    finally { if (!stopped) busy.value = false }
  }

  onBeforeUnmount(() => { stopped = true; request?.abort() })
  return { form, sources, loading, busy, error, detectorCount, nodeCount, incompleteReason, canSubmit,
    loadSources, updateGroup, addGroup, removeGroup, reset, submit }
}
