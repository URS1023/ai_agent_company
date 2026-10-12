/**
 * 质检批量执行接口层（/api/v1/quality/workflows/{id}/*）。
 * 职责：按已发布版本生成"执行计划"（按检测节点分组）、提交批量运行、按执行 id 回读结果。
 * 关键约束：plan 返回的 groups/writes 是执行前确认界面（含回写表与字段）的唯一依据，
 *   submit 必须带上 publishedRevision + idempotencyKey（幂等键，避免重复点击产生两次质检），
 *   confirmWrites 由用户在确认弹窗中显式勾选；大批量运行超时放宽到 330s。
 * 错误语义：200/202/502 都作为结果返回，需按 execution.status 判定成功/部分失败；其余 HTTP 码抛异常。
 */
import { get, post } from './request'
import type { QualityRecord } from './quality'
import type { FlowRun } from './workflows'

export interface QualityRunGroupPlan {
  nodeId: string; title: string; device: QualityRecord; template: QualityRecord
  templateRevision: number; imageRequired: boolean; detectors: { nodeId: string; metricId: string; kind: string }[]
}
export interface QualityRunPlan {
  workflowId: string; name: string; version: number; publishedRevision: number
  groups: QualityRunGroupPlan[]; writes: { nodeId: string; name: string; tableName: string; mode: string; fields: { column: string; path?: string; origin: string }[] }[]
}
export interface QualityRunSample { sampleId: string; imageFileId: string | null }
export interface QualityRunPayload {
  version: number; publishedRevision: number; idempotencyKey: string; confirmWrites: boolean; timeoutSeconds?: number
  groups: { nodeId: string; samples: QualityRunSample[] }[]
}
export interface QualityBatchResult {
  execution: FlowRun
  samples: { nodeId: string; sampleId: string; inspectionId: string | null; outcome: string; effectiveOutcome?: string; deviceId?: string; deviceName?: string; templateName?: string; status?: string }[]
}
const base = '/api/v1/quality/workflows'
export const qualityRuns = {
  /** 执行计划：GET {base}/{id}/run-plan；返回按节点分组的设备/模板/检测器与将要写入的表，只读，不触发实际检测。 */
  plan: (id: string, signal?: AbortSignal) => get<{data: QualityRunPlan}>(base + '/' + id + '/run-plan', {}, { signal }),
  /**
   * 提交批量运行：POST {base}/{id}/runs。
   * 关键字段：version/publishedRevision（版本与发布快照）、idempotencyKey（幂等）、confirmWrites（用户已在确认弹窗勾选回写）、
   *   groups[].samples（节点 → 样本 + 图片 fileId）。
   * 不设置浏览器总时限（批量检测可能较慢）；502 表示上游检测服务不可用，仍返回 body 供展示逐样本结果。
   */
  submit: (id: string, payload: QualityRunPayload) => {
    // 旧版恢复点可能保存过 timeoutSeconds；不把旧的整轮截止时间继续发送给服务端。
    const { timeoutSeconds: _timeoutSeconds, ...requestPayload } = payload
    return post<{data: QualityBatchResult}>(base + '/' + id + '/runs', requestPayload,
      { validateStatus: status => [200, 202, 502].includes(status) })
  },
  /** 回读批量结果：GET {base}/{id}/runs/{executionId}；用于刷新或从"质检执行"入口恢复上次结果。 */
  result: (id: string, executionId: string, signal?: AbortSignal) => get<{data: QualityBatchResult}>(base + '/' + id + '/runs/' + executionId, {}, { signal }),
}
