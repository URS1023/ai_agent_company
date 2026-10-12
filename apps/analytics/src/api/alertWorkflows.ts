/**
 * 预警工作流创建接口层（/api/v1/alerts/workflows）。
 * 职责：把"预警向导"里攒下的草稿（AlertWorkflowDraft）一次性提交给后端，
 *   由后端生成工作流图、绑定点位/计算/通知/回写/调度，并返回可跳转编辑的 workflow。
 * 约束：requestKey 为客户端幂等键（后端按其去重，重复提交返回 reused=true，前端需据此提示"已存在"）；
 *   草稿里的计算项 key 只是前端列表标识，提交时会被剔除，不进入后端契约。
 * 错误语义：校验失败（如公式非法、测点不属于该设备）由后端返回 message，前端弹出并保留草稿。
 */
import { post } from './request'
import type { Workflow } from './workflows'
import { createClientId } from '../utils/randomId'

export interface AlertCalculationDraft { key: string; name: string; formula: string }
export interface AlertWorkflowDraft {
  requestKey: string
  name: string
  description: string
  ruleId: string
  ruleRevision: number
  source: { datasourceId: string; datasourceVersion: number; query: string; parametersJson: string; maxRows: number }
  confirmHttpWrites: boolean
  pointIds: string[]
  calculations: AlertCalculationDraft[]
  notificationEnabled: boolean
  notification: { audience: 'executor' | 'all'; outcomes: string[]; minimumSeverity: 'info' | 'warning' | 'critical' }
  writebackEnabled: boolean
  writeback: Record<string, any>
  scheduleEnabled: boolean
  schedule: { scheduleKind: 'interval' | 'cron'; scheduleExpression: string; timezone: string; status: 'active' | 'paused' }
}

export interface AlertWorkflowCreated {
  workflow: Workflow
  schedule: Record<string, any> | null
  selectedFields: string[]
  reused: boolean
}

export const alertWorkflows = {
  /**
   * 创建（或复用）预警工作流：POST /api/v1/alerts/workflows。
   * 关键字段：requestKey（幂等键，重复提交返回 reused=true 而非报错）、name/description、
   *   ruleId + ruleRevision（锁定策略版本）、source（数据源 + 查询 + 参数 + maxRows）、
   *   pointIds、calculations、notification / writeback / schedule（未启用则显式传 null 表示不配置）。
   * 副作用：后端会新建工作流图与调度任务，前端拿到 workflow 后通常直接跳转编辑页。
   * 失败表现：策略版本过期、点位不匹配、调度表达式非法等返回业务错误，草稿需保留以便修改。
   */
  create: (draft: AlertWorkflowDraft) => post<{ data: AlertWorkflowCreated }>('/api/v1/alerts/workflows', {
    requestKey: draft.requestKey,
    name: draft.name.trim(),
    description: draft.description.trim(),
    ruleId: draft.ruleId,
    ruleRevision: draft.ruleRevision,
    confirmHttpWrites: draft.confirmHttpWrites,
    source: {
      datasourceId: draft.source.datasourceId,
      datasourceVersion: draft.source.datasourceVersion,
      query: draft.source.query.trim(),
      parameters: JSON.parse(draft.source.parametersJson || '{}'),
      maxRows: draft.source.maxRows,
    },
    pointIds: draft.pointIds,
    calculations: draft.calculations.map(({ key: _key, ...item }) => ({ ...item, name: item.name.trim(), formula: item.formula.trim() })),
    notification: draft.notificationEnabled ? draft.notification : null,
    writeback: draft.writebackEnabled ? draft.writeback : null,
    schedule: draft.scheduleEnabled ? draft.schedule : null,
  }),
}

/** 新建一条空计算项；key 仅前端持有（提交时会被剔除），name/formula 由用户填写，公式语法由后端校验。 */
export function newAlertCalculation(): AlertCalculationDraft {
  return { key: createClientId(), name: '', formula: '' }
}
