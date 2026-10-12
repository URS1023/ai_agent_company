/**
 * 质检工作流创建接口层（/api/v1/quality/workflows）。
 * 职责：把质检向导草稿（设备 + 模板 + 检测器绑定）一次性提交，后端生成工作流并返回可运行的 runPlan。
 * 约束：requestKey 为幂等键（重复提交复用，reused=true）；草稿里的 group.key 仅前端持有，提交时剔除；
 *   writebackEnabled 为 false 时显式提交 null，表示不配置回写。
 * 配套：detectorSourceState 是"前端预检"，只做友好提示，最终以服务端校验为准（见函数注释）。
 */
import { post } from './request'
import type { HttpSource } from './collection'
import type { QualityRecord } from './quality'
import type { QualityRunPlan } from './qualityRuns'
import type { Workflow } from './workflows'
import { createClientId } from '../utils/randomId'

export interface DetectorBindingDraft {
  metricId: string
  metricVersion: number
  datasourceId: string
  datasourceVersion: number
}

export interface QualityWorkflowGroupDraft {
  key: string
  title: string
  deviceId: string
  templateId: string
  templateRevision: number
  detectors: DetectorBindingDraft[]
}

export interface QualityWorkflowDraft {
  requestKey: string
  name: string
  description: string
  groups: QualityWorkflowGroupDraft[]
  writebackEnabled: boolean
  writeback: Record<string, any>
}

export interface QualityWorkflowCreated {
  workflow: Workflow
  runPlan: QualityRunPlan
  reused: boolean
}

export interface DetectorSourceState { ready: boolean; reason: string }

function inputNames(source: HttpSource): Set<string> {
  const names = new Set<string>()
  const expression = /\$\{input\.([A-Za-z][A-Za-z0-9]*)\}/g
  for (const match of JSON.stringify(source.bodies || []).matchAll(expression)) names.add(match[1])
  return names
}

/** Client guidance mirrors server preflight; the server remains authoritative. */
/**
 * 判断某 HTTP 采集源能否作为自动检测源（前端预检，仅用于按钮禁用与原因提示）。
 * 参数：source 采集源定义；category 检测类别（外观/成分等，来自质检指标）。
 * 返回：DetectorSourceState { ready, reason }，reason 为中文原因，可直接展示给用户。
 * 约束：客户端判断与服务端预检保持同样规则；服务端始终是权威，前端结论只影响交互提示。
 */
export function detectorSourceState(source: HttpSource, category: string): DetectorSourceState {
  if (!source.enabled) return { ready: false, reason: '服务已停用' }
  if (!source.allowAutomatic) return { ready: false, reason: '未允许工作流自动调用' }
  if (source.method !== 'POST' || source.bodies?.length !== 1) return { ready: false, reason: '需保存一条 POST 请求模板' }
  const names = inputNames(source)
  if (!names.has('sampleId')) return { ready: false, reason: '请求体缺少 ${input.sampleId}' }
  if (category === 'appearance' && !names.has('imageBase64')) return { ready: false, reason: '外观服务缺少 ${input.imageBase64}' }
  if (category === 'component' && [...names].some(name => name !== 'sampleId')) return { ready: false, reason: '成分服务仅接受样本参数' }
  const selected = new Set((source.fields || []).map(field => field.name))
  const required = category === 'appearance' ? ['sampleId', 'defects'] : ['sampleId', 'components']
  if (selected.size && required.some(name => !selected.has(name))) return { ready: false, reason: 'JSONPath 输出字段未覆盖 ' + required.join('、') }
  return { ready: true, reason: '配置可用于自动检测' }
}

export const qualityWorkflows = {
  /**
   * 创建（或复用）质检工作流：POST /api/v1/quality/workflows。
   * 关键字段：requestKey（幂等）、name/description、groups[].deviceId/templateId/templateRevision/detectors、writeback。
   * 返回：QualityWorkflowCreated（workflow + runPlan + reused），前端据此进入执行确认或提示"已存在"。
   */
  create: (draft: QualityWorkflowDraft) => post<{ data: QualityWorkflowCreated }>('/api/v1/quality/workflows', {
    requestKey: draft.requestKey,
    name: draft.name.trim(),
    description: draft.description.trim(),
    groups: draft.groups.map(({ key: _key, ...group }) => group),
    writeback: draft.writebackEnabled ? draft.writeback : null,
  }),
}

/** 新建一个空质检分组（一台设备 + 一个模板 + 若干检测器）；key 仅用于前端列表与增删定位，提交时会被剔除。 */
export function newQualityWorkflowGroup(): QualityWorkflowGroupDraft {
  return { key: createClientId(), title: '', deviceId: '', templateId: '', templateRevision: 1, detectors: [] }
}

/**
 * 汇总某设备已绑定的数据源 id 集合（设备主数据源 + 质检专用数据源）。
 * 用途：检测器下拉里标记"本设备已关联"，以及保存前校验绑定是否属于当前设备。
 */
export function linkedQualitySourceIds(device: QualityRecord | null): Set<string> {
  return new Set([...(device?.qualityDatasourceIds || []), ...(device?.datasourceId ? [device.datasourceId] : [])])
}
