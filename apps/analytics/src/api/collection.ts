/** Fixed-source HTTP collection contracts; never call upstream URLs in the browser. */
/**
 * 固定源 HTTP 采集接口层（/api/v1/collection）。
 * 职责：采集源（HttpSource）的增删改查、一次性采集执行、抽取样例预览、采集历史与结果回读。
 * 约束：浏览器不直接请求上游 URL —— 所有外部调用都由后端按"已保存的请求模板"发起，
 *   前端只提交源配置与确认标记；采集结果会以 queryId/runId/datasourceId 落库，供后续分析复用。
 * 依赖：统一走 ./request 的 axios 实例（含鉴权头、workspace 头与同源白名单校验）。
 * 错误语义：调用方需处理 post 抛出的业务错误（如未授权自动调用、超时、上游 5xx）；
 *   collect 对 200/207/502 均视为"已拿到结果"，需按 CollectionResult.status 判定成功/部分完成。
 */
import { del, get, post, put } from './request'

export interface ExtractField {
  name: string; path: string; type: 'auto' | 'string' | 'integer' | 'decimal' | 'boolean' | 'json'
  required: boolean; multiple: boolean; comment: string
}
export interface HttpSourceInput {
  responseFormat?: 'json' | 'text'; pagination?: { parameter: string; start: number; step: number; pages: number } | null
  name: string; url: string; method: 'GET' | 'POST'; headers: Record<string, string>; bodies: unknown[]
  recordPath: string; fields: ExtractField[]; credentials?: Record<string, string> | null
  allowPrivate: boolean; allowAutomatic: boolean; timeoutSeconds: number; batchTimeoutSeconds: number
  maxRows: number; continueOnError: boolean; enabled: boolean; description: string; version?: number
}
export interface HttpSource extends HttpSourceInput {
  id: string; type: 'http'; version: number; credentialNames: string[]; hasCredentials: boolean
  createTime: string; updateTime: string
}
export interface CollectionColumn { columnName: string; dataType: string; comment: string }
export interface ExtractionResult { data: Record<string, unknown>[]; columns: CollectionColumn[]; totalCount: number; truncated: boolean }
export interface CollectionResult extends ExtractionResult {
  queryId: string; runId: string; datasourceId: string; datasourceVersion: number
  executionTime: number; status: 'success' | 'partial' | 'failed'; rowOrigins: number[]; analysis: string
  outcomes: { index: number; status: 'success' | 'failed' | 'skipped'; rows: number; httpStatus: number | null; executionTime: number; message: string; truncated: boolean }[]
}
export interface CollectionHistory { id: string; status: string; resultCount: number; executionTime: number; createTime: string }
interface Envelope<T> { data: T; success: boolean; message: string }
const base = '/api/v1/collection'
export const httpCollection = {
  /** 采集源分页列表：GET /api/v1/collection/sources，固定 limit=50，按 offset 翻页；失败由调用方弹出错误。 */
  list: (offset = 0, signal?: AbortSignal) => get<PagedEnvelope<HttpSource>>(base + '/sources', { limit: 50, offset }, { signal }),
  /** 保存采集源：无 id 时 POST /sources 新建，带 id 时 PUT /sources/{id} 更新（后端按 version 做乐观锁冲突校验）。 */
  save: (payload: HttpSourceInput, id?: string) => id ? put<Envelope<HttpSource>>(base + '/sources/' + id, payload) : post<Envelope<HttpSource>>(base + '/sources', payload),
  /** 删除采集源：DELETE /sources/{id}，以 version 作为并发保护；删除前需由调用方二次确认。 */
  remove: (source: HttpSource) => del(base + '/sources/' + source.id, { params: { version: source.version } }),
  /**
   * 执行一次采集：POST /sources/{id}/collect。
   * 关键字段：version（乐观锁）、confirmed（POST 方法必须由用户确认后才允许发送，避免误触发上游写操作）。
   * 超时按源配置的 batchTimeoutSeconds + 20s 放宽；200/207/502 都返回给调用方按 status 判定，其余 HTTP 码走异常。
   */
  collect: (source: HttpSource) => post<Envelope<CollectionResult>>(base + '/sources/' + source.id + '/collect',
    { version: source.version, confirmed: source.method === 'POST' }, { timeout: (source.batchTimeoutSeconds + 20) * 1000, validateStatus: status => [200, 207, 502].includes(status) }),
  /** 抽取预览：POST /extract-preview，用一段样例响应验证 recordPath 与字段 JSONPath 是否取到值，不落库、不访问上游。 */
  preview: (sample: unknown, recordPath: string, fields: ExtractField[]) => post<Envelope<ExtractionResult>>(base + '/extract-preview', { sample, recordPath, fields }),
  /** 采集历史：GET /sources/{id}/history，固定 limit=50，按 offset 翻页，用于结果面板的"历史执行"列表。 */
  history: (source: HttpSource, offset = 0) => get<PagedEnvelope<CollectionHistory>>(base + '/sources/' + source.id + '/history', { limit: 50, offset }),
  /** 回读某次采集详情：GET /history/{id}，返回完整 CollectionResult（含逐请求 outcomes 与行来源 rowOrigins）。 */
  result: (id: string) => get<Envelope<CollectionResult>>(base + '/history/' + id),
}
/** 采集执行状态中文名映射；键必须与后端 CollectionResult.status 取值保持一致，禁止翻译键名。 */
export const collectionStatus: Record<string, string> = { running: '执行中', success: '成功', partial: '部分完成', failed: '失败', skipped: '未执行' }
/** 本次采集将向上游发起的 HTTP 请求总数：分页模式取 pages，否则按请求体模板条数，最少 1。 */
export function httpRequestCount(source: Pick<HttpSourceInput, 'pagination' | 'bodies'>) { return source.pagination?.pages || source.bodies?.length || 1 }
/** 表格单元格展示：null/undefined 显示占位符，对象/数组序列化，其余转字符串（历史数据可能是任意类型，需兼容）。 */
export function jsonCell(value: unknown) { return value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value) }

interface PagedEnvelope<T> { data: T[]; total: number; limit: number; offset: number; hasMore: boolean }
