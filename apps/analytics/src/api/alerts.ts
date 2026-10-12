/**
 * 设备预警台账接口层（/api/v1/alerts）。
 * 职责：设备台账、测点、专家经验、预警策略四类登记表（RegistryKind）的通用增删改查，
 *   以及预警报告列表与"问题"处置动作（确认/解决/忽略）。
 * 关键约束：所有保存都先按 emptyAlertRecord 的空表补齐字段，避免后端收到半结构化 payload；
 *   更新/删除必须带 version（乐观锁，冲突时后端报错，由调用方提示"数据已被他人修改"）。
 * 错误语义：底层 get/put/post/del 统一在 request.ts 的响应拦截器里把后端 message/detail 转成 Error.message。
 */
/** Equipment and alert contracts; all UI calls use the same authorized backend. */
import { del, get, post, put } from './request'
import { createClientId } from '../utils/randomId'
export type RegistryKind = 'devices' | 'points' | 'advice' | 'rules'
export type AlertSection = RegistryKind | 'reports' | 'problems'
export interface AlertRecord { id?: string; version?: number; [key: string]: any }
export const alertSections: Record<AlertSection, string> = { devices: '设备台账', points: '测点管理', advice: '专家经验', rules: '预警策略', reports: '预警报告', problems: '问题列表' }
export const equipmentStatus: Record<string, string> = { active: '在用', inactive: '停用', maintenance: '维护', open: '待处理', acknowledged: '已确认', resolved: '已解决', ignored: '已忽略' }
export const outcomeNames: Record<string, string> = { issues: '发现问题', normal: '未发现问题', no_data: '无数据，未判定' }
export const alertBase = '/api/v1/alerts'
export const alerts = {
  /** 分页列表：GET /api/v1/alerts/{kind}，默认 limit=30；kind 决定台账类型（devices/points/advice/rules/reports/problems）。 */
  list: (kind: AlertSection, params: object = {}, signal?: AbortSignal) => get<{ data: AlertRecord[] }>(alertBase + '/' + kind, { limit: 30, ...params }, { signal }),
  /** 单条详情：GET /api/v1/alerts/{kind}/{id}；404 会以异常抛出，调用方需回退到列表或提示已删除。 */
  get: (kind: AlertSection, id: string, signal?: AbortSignal) => get<{ data: AlertRecord }>(alertBase + '/' + kind + '/' + id, {}, { signal }),
  /**
   * 新建/更新：无 id → POST /api/v1/alerts/{kind}，有 id → PUT /api/v1/alerts/{kind}/{id}。
   * 关键字段：payload 只保留空表定义过的键并补默认值；更新时附带 version 做乐观锁，冲突由后端拒绝。
   */
  save: (kind: RegistryKind, row: AlertRecord) => {
    const defaults = emptyAlertRecord(kind)
    const payload = Object.fromEntries(Object.keys(defaults).map(key => [key, row[key] ?? defaults[key]]))
    if (row.id) payload.version = row.version
    return row.id ? put<{ data: AlertRecord }>(alertBase + '/' + kind + '/' + row.id, payload) : post<{ data: AlertRecord }>(alertBase + '/' + kind, payload)
  },
  /** 删除：DELETE /api/v1/alerts/{kind}/{id}?version=...；被工作流引用的记录会被后端拒绝删除。 */
  remove: (kind: RegistryKind, row: AlertRecord) => del(alertBase + '/' + kind + '/' + row.id, { params: { version: row.version } }),
  /** 预警策略历史版本：GET /api/v1/alerts/rules/{id}/revisions，供策略详情抽屉展示变更记录（只读）。 */
  revisions: (id: string) => get<{ data: AlertRecord[] }>(alertBase + '/rules/' + id + '/revisions'),
  /**
   * 处置问题：POST /api/v1/alerts/problems/{id}/actions。
   * 关键字段：version（乐观锁，避免两人同时处置同一问题）、status（open/acknowledged/resolved/ignored）、note（处置说明）。
   * 失败表现：版本过期返回业务错误，调用方应刷新列表后重试。
   */
  handle: (row: AlertRecord, status: string, note: string) => post(alertBase + '/problems/' + row.id + '/actions', { version: row.version, status, note }),
  /** 问题处置流水：GET /api/v1/alerts/problems/{id}/actions，按 offset 翻页，limit 固定 50。 */
  actions: (id: string, offset = 0) => get<{ data: AlertRecord[] }>(alertBase + '/problems/' + id + '/actions', { offset, limit: 50 }),
}
/** 构造一条空白预警条件（策略规则的最小单元）；id 由 createClientId 生成，仅用于前端列表 key 与增删定位。 */
export function newAlertCondition() { return { id: 'rule_' + createClientId().slice(0, 8), name: '', expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] }, severity: 'warning', message: '' } }
/**
 * 空记录模板：既用于"新建"表单初值，也用于 save() 裁剪字段与补默认值。
 * 返回：对应 kind 的完整字段骨架；调用方不要删减键，否则后端可能写入缺省值。
 */
export function emptyAlertRecord(kind: RegistryKind): AlertRecord {
  const rows: Record<RegistryKind, AlertRecord> = {
    devices: { code: '', name: '', category: '', model: '', manufacturer: '', location: '', protocol: 'HTTP', datasourceId: null, qualityDatasourceIds: [], domains: ['alert', 'quality'], description: '', status: 'active' },
    points: { deviceId: '', code: '', name: '', fieldName: '', unit: '', pointType: 'number', deviceKey: '', prefix: '', frequencyMinutes: null, failureDiagnosis: false, description: '', status: 'active' },
    advice: { name: '', conditionCode: '', operatingCondition: '', alertType: '', conditionDescription: '', content: '', responsePlan: '', planShort: '', status: 'active' },
    rules: { name: '', description: '', deviceId: '', adviceId: null, recordKeyField: '', rules: [newAlertCondition()], status: 'active' },
  }
  return rows[kind]
}
