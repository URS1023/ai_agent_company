/**
 * 质检模块接口层（/api/v1/quality）。
 * 职责：质检指标/检验模板/质检记录的通用增删改查、图片上传与读取、模板历史版本、记录复核，
 *   以及让步接收（concessions：规则 concessionRules + 申请/评估/审批 concessions）两套子资源。
 * 关键约束：保存与删除必须带 version（乐观锁）；数值字段（标准上下限、偏差、扣重）在提交前由
 *   normalizeConcessionRuleDraft 把空串转 null，避免后端把 '' 当数值解析失败；
 *   图片接口用 encodeURIComponent 处理 id，读取内容返回 Blob，调用方负责释放 URL。
 * 错误语义：所有请求失败抛 Error；调用方按业务提示（如"记录已被他人修改，请刷新"）呈现。
 */
import { del, get, post, put } from './request'
export type QualityKind = 'metrics' | 'templates' | 'inspections'
export interface QualityRecord { id?: string; version?: number; [key: string]: any }
export interface QualityImage { id: string; name: string; mimeType: string; size: number; sha256: string; width: number; height: number; orientation: number }
export type DefectBox = [number, number, number, number]
export interface QualityDefect { defectType: string; confidence: string; severity: string; suggest?: string; bbox?: DefectBox | null }
export interface ConcessionRule {
  id?: string; version?: number; code: string; name: string; productCodes: string[]; metricIds: string[]; weightUnit: string; metricUnit: string
  standardMin: string | null; standardMax: string | null; minDeviation: string | null; maxDeviation: string | null
  deductionMode: 'rate' | 'weight'; deductionValue: string; roundingScale: number | null
  roundingMode: 'exact' | 'half_up' | 'half_even' | 'down' | 'up'; status: 'active' | 'inactive' | 'deleted'
  description: string; createTime?: string; updateTime?: string
}
export interface ConcessionMeasurement { metricId: string; value: string | null; unit: string; standardMin: string | null; standardMax: string | null; sourceField: string }
export interface ConcessionRequest {
  id: string; version: number; requestKey: string; inspectionId: string | null; ruleId: string | null; ruleVersion: number | null; ruleSnapshot: Record<string, unknown>; sampleId: string
  productCode: string; source: Record<string, unknown>; measurements: ConcessionMeasurement[]; originalWeight: string | null; weightUnit?: string | null
  deviation: string | null; proposedDeduction: string | null; proposedSettlementWeight: string | null; settlementWeight: string | null
  status: 'needs_confirmation' | 'pending' | 'approved' | 'rejected'; reason: string; decisionNote: string
  decidedBy: string | null; decidedAt: string | null; createTime: string; updateTime: string
}
export interface ConcessionSourceField { key: string; path: string; dataType: 'number' | 'text' | 'boolean' | 'empty' }
export interface ConcessionSourceOption { kind: 'workflow' | 'inspection'; recordId: string; sampleId: string; workflowId: string | null; inspectionId: string | null; label: string; status: string; fields: ConcessionSourceField[]; hasOriginalWeight: boolean; measurementCount: number }
export interface ConcessionSourcePage { items: ConcessionSourceOption[]; total: number | null; limit: number; offset: number; hasMore: boolean }
/** 质检列表默认页大小；与后端 limit 上限保持一致，前端"是否还有更多"的判定也依赖它。 */
export const QUALITY_PAGE_SIZE = 30
/**
 * 从一次性返回的数组里切出"当前页"。
 * 返回：{ items, hasMore }；hasMore 用 page.length >= pageSize 判定，
 * 因此调用方必须按 pageSize（默认 QUALITY_PAGE_SIZE）请求，否则会出现"永远还有更多"。
 */
export function readQualityArrayPage<T>(page: T[], pageSize = QUALITY_PAGE_SIZE) {
  return { items: page.slice(0, pageSize), hasMore: page.length >= pageSize }
}
/** 让步接收状态中文名；键与后端 status 枚举一致，禁止翻译键名。 */
export function concessionStatusLabel(status: ConcessionRequest['status']) {
  return status === 'pending' ? '待审批' : status === 'needs_confirmation' ? '待确认' : status === 'approved' ? '已批准' : '已驳回'
}
/**
 * 提交前的让步规则清洗：把"用户清空的数值输入框"（空串）统一转成 null，
 * 并在"精确模式"下清掉落位参数（精确模式不应带 roundingScale，否则后端会按无关字段校验）。
 */
export function normalizeConcessionRuleDraft(row: Partial<ConcessionRule>): Partial<ConcessionRule> {
  const payload = { ...row }
  for (const key of ['standardMin', 'standardMax', 'minDeviation', 'maxDeviation'] as const) {
    if (payload[key] === '') payload[key] = null
  }
  if (payload.roundingMode === 'exact') payload.roundingScale = null
  return payload
}
/**
 * 生成"评估快照"字符串，用于比较草稿是否真的发生变化（决定是否允许提交/是否需要重新评估）。
 * 副作用：会剔除 requestKey（每次重建都不同，否则永远判定为"已修改"），
 * 并把手动来源的 recordId 归一成 '<manual>'（手工录入没有真实记录 id）。
 */
export function concessionEvaluationSnapshot(payload: Record<string, any>) {
  const source = payload.source && typeof payload.source === 'object' ? payload.source as Record<string, unknown> : null
  const normalized = { ...payload }
  delete normalized.requestKey
  if (source?.kind === 'manual') normalized.source = { ...source, recordId: '<manual>' }
  return JSON.stringify(normalized)
}
export const qualityBase = '/api/v1/quality'
export const qualitySections = { inspections: '质检记录', templates: '检验模板', metrics: '质检指标' }
export const qualityOutcomes: Record<string, string> = { passed: '合格', failed: '不合格', review: '待复核', incomplete: '未完成检测', retest: '要求复检' }
export const metricCategories: Record<string, string> = { numeric: '数值测量', component: '成分检测', appearance: '外观缺陷' }
export const quality = {
  /** 上传质检图片：POST /api/v1/quality/images（multipart，字段名 file）；返回图片元数据（sha256/size/尺寸），不含内容。 */
  uploadImage: (file: File, signal?: AbortSignal) => { const body = new FormData(); body.append('file', file); return post<{data: QualityImage}>(qualityBase + '/images', body, { signal }) },
  /** 图片元数据：GET /api/v1/quality/images/{id}（只读）。 */
  image: (id: string, signal?: AbortSignal) => get<{data: QualityImage}>(qualityBase + '/images/' + encodeURIComponent(id), {}, { signal }),
  /**
   * 读图片内容：GET /api/v1/quality/images/{id}/content，或质检记录上下文
   * GET /api/v1/quality/inspections/{inspectionId}/images/{id}/content（带记录时会校验归属与权限）。
   * 返回 Blob；调用方需自行 createObjectURL 并 revoke。
   */
  imageContent: (id: string, inspectionId?: string, signal?: AbortSignal) => get<Blob>(inspectionId ? qualityBase + '/inspections/' + encodeURIComponent(inspectionId) + '/images/' + encodeURIComponent(id) : qualityBase + '/images/' + encodeURIComponent(id) + '/content', {}, { signal, responseType: 'blob' }),
  /** 分页列表：GET /api/v1/quality/{kind}，默认 limit=30；params 可带关键字、状态等过滤条件。 */
  list: (kind: QualityKind, params: object = {}, signal?: AbortSignal) => get<{data: QualityRecord[]}>(qualityBase + '/' + kind, { limit: 30, ...params }, { signal }),
  /** 单条详情：GET /api/v1/quality/{kind}/{id}。 */
  get: (kind: QualityKind, id: string, signal?: AbortSignal) => get<{data: QualityRecord}>(qualityBase + '/' + kind + '/' + id, {}, { signal }),
  /** 保存指标/模板：无 id → POST，有 id → PUT（带 version）；字段按 emptyQualityRecord 补齐后再提交。 */
  save: (kind: 'metrics' | 'templates', row: QualityRecord) => {
    const defaults = emptyQualityRecord(kind)
    const payload = Object.fromEntries(Object.keys(defaults).map(key => [key, row[key] ?? defaults[key]]))
    if (row.id) payload.version = row.version
    return row.id ? put<{data: QualityRecord}>(qualityBase + '/' + kind + '/' + row.id, payload) : post<{data: QualityRecord}>(qualityBase + '/' + kind, payload)
  },
  /** 删除指标/模板/记录：DELETE /api/v1/quality/{kind}/{id}?version=...。 */
  remove: (kind: QualityKind, row: QualityRecord) => del(qualityBase + '/' + kind + '/' + row.id, { params: { version: row.version } }),
  /** 模板历史版本：GET /api/v1/quality/templates/{id}/revisions，limit 固定 30，按 offset 翻页。 */
  revisions: (id: string, offset = 0, signal?: AbortSignal) => get<{data: QualityRecord[]}>(qualityBase + '/templates/' + id + '/revisions', { limit: 30, offset }, { signal }),
  /** 新建质检记录：POST /api/v1/quality/inspections；payload 由调用方按模板结构组装，后端做完整性校验。 */
  createInspection: (payload: object) => post<{data: QualityRecord}>(qualityBase + '/inspections', payload),
  /** 复核质检记录：POST /api/v1/quality/inspections/{id}/reviews；必须带 version，避免复核与编辑并发冲突。 */
  review: (row: QualityRecord, payload: object) => post<{data: QualityRecord}>(qualityBase + '/inspections/' + row.id + '/reviews', { version: row.version, ...payload }),
  /** 让步接收规则（只读列表 + 增改 + 删除），规则用于自动计算扣重与结算重量。 */
  concessionRules: {
    /** 规则列表：GET /api/v1/quality/concession-rules，默认 limit=30。 */
    list: (params: object = {}, signal?: AbortSignal) => get<{data: ConcessionRule[]}>(qualityBase + '/concession-rules', { limit: 30, ...params }, { signal }),
    /**
     * 保存规则：无 id → POST，有 id → PUT（带 version）。
     * 关键字段：productCodes/metricIds（适用面）、standardMin/Max 与偏差区间、deductionMode + deductionValue（扣重方式与数值）、
     *   roundingMode/roundingScale（落位规则）；未填写的项提交 null 而不是空串。
     */
    save: (row: Partial<ConcessionRule>) => {
      const payload = {
        code: row.code, name: row.name, productCodes: row.productCodes || [], metricIds: row.metricIds || [],
        weightUnit: row.weightUnit, metricUnit: row.metricUnit, standardMin: row.standardMin ?? null,
        standardMax: row.standardMax ?? null, minDeviation: row.minDeviation ?? null, maxDeviation: row.maxDeviation ?? null,
        deductionMode: row.deductionMode, deductionValue: row.deductionValue, roundingScale: row.roundingScale ?? null,
        roundingMode: row.roundingMode || 'exact', status: row.status || 'active', description: row.description || '',
        ...(row.id ? { version: row.version } : {}),
      }
      return row.id ? put<{data: ConcessionRule}>(qualityBase + '/concession-rules/' + encodeURIComponent(row.id), payload) : post<{data: ConcessionRule}>(qualityBase + '/concession-rules', payload)
    },
    /** 删除规则：DELETE /api/v1/quality/concession-rules/{id}?version=...。 */
    remove: (row: ConcessionRule) => del(qualityBase + '/concession-rules/' + encodeURIComponent(row.id || ''), { params: { version: row.version } }),
  },
  /** 让步接收申请（依赖规则与来源记录）。 */
  concessions: {
    /** 可选来源：GET /api/v1/quality/concession-sources，返回可发起让步的工作流记录/质检记录及其可用字段（分页）。 */
    sources: (params: object = {}, signal?: AbortSignal) => get<{data: ConcessionSourcePage}>(qualityBase + '/concession-sources', { limit: QUALITY_PAGE_SIZE, ...params }, { signal }),
    /** 申请列表：GET /api/v1/quality/concessions，默认 limit=30，可按状态过滤。 */
    list: (params: object = {}, signal?: AbortSignal) => get<{data: ConcessionRequest[]}>(qualityBase + '/concessions', { limit: 30, ...params }, { signal }),
    /** 试算：POST /api/v1/quality/concessions/evaluate；只计算不落库，返回建议扣重与结算重量（needs_confirmation 表示需人工确认）。 */
    evaluate: (payload: object) => post<{data: ConcessionRequest}>(qualityBase + '/concessions/evaluate', payload),
    /** 审批：POST /api/v1/quality/concessions/{id}/decision；带 version，decision 仅 approved/rejected，note 为审批意见。 */
    decide: (row: ConcessionRequest, decision: 'approved' | 'rejected', note: string) => post<{data: ConcessionRequest}>(qualityBase + '/concessions/' + encodeURIComponent(row.id) + '/decision', { version: row.version, decision, note }),
  },
}
/** 指标/模板的空白表单初值：指标含类别（numeric/component/appearance）、上下限与阻断等级；模板含设备类型与指标集合。 */
export function emptyQualityRecord(kind: 'metrics' | 'templates'): QualityRecord {
  return kind === 'metrics' ? { name: '', category: 'numeric', unit: '', standardMin: null, standardMax: null, componentName: '', minimumConfidence: '0.8', blockingSeverities: ['moderate', 'severe'], description: '', status: 'active' }
    : { name: '', deviceType: '', description: '', metricIds: [], status: 'active' }
}
