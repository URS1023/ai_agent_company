/**
 * 数据回写目标接口层（/api/v1/writeback）。
 * 职责：管理"写回目标"（表 + 列 + 写入模式）及按执行 id 查询写回凭据（一次执行对某表实际写入多少行、成败）。
 * 约束：目标保存/删除都带 version 乐观锁；operations 为只读审计数据，
 *   状态必须按 writeStatus 展示（尤其 uncertain 表示"提交结果不确定"，不能显示为失败或成功）。
 */
import { del, get, post, put } from './request'
export interface WriteTarget { id?: string; name: string; datasourceId: string; sourceVersion: number; tableName: string; columns: string[]; modes: string[]; status: string; version?: number }
export interface WriteOperation { id: string; executionId: string; nodeId: string; targetId: string; status: string; requestedRows: number; affectedRows: number | null; errorMessage: string; snapshot: Record<string, any>; createTime: string }
interface Envelope<T> { data: T }
const base = '/api/v1/writeback'
export const writeback = {
  /** 目标分页列表：GET /api/v1/writeback/targets，limit 固定 30，按 offset 翻页。 */
  list: (offset = 0, signal?: AbortSignal) => get<PagedEnvelope<WriteTarget>>(base + '/targets', { offset, limit: 30 }, { signal }),
  /** 单个目标详情：GET /api/v1/writeback/targets/{id}；用于编辑弹窗回填最新字段与 version。 */
  get: (id: string, signal?: AbortSignal) => get<Envelope<WriteTarget>>(base + '/targets/' + id, {}, { signal }),
  /**
   * 保存目标：无 id → POST /targets 新建，有 id → PUT /targets/{id} 更新。
   * 关键字段：datasourceId/sourceVersion（绑定数据源版本）、tableName、columns、modes。
   * 注意：提交前会剔除 id，避免把前端记录 id 混进请求体。
   */
  save: (row: WriteTarget) => {
    const { id, ...payload } = row
    return id ? put<Envelope<WriteTarget>>(base + '/targets/' + id, payload) : post<Envelope<WriteTarget>>(base + '/targets', payload)
  },
  /** 删除目标：DELETE /targets/{id}?version=...；目标被工作流节点引用时后端会拒绝。 */
  remove: (row: WriteTarget) => del(base + '/targets/' + row.id, { params: { version: row.version } }),
  /** 按执行 id 查写回凭据：GET /api/v1/writeback/operations?executionId=...，只读审计，用于运行结果面板展示"已提交/未提交"。 */
  operations: (executionId: string, signal?: AbortSignal) => get<Envelope<WriteOperation[]>>(base + '/operations', { executionId }, { signal }),
}
/** 写回状态中文名：键与服务端 WriteOperation.status 一致；uncertain 表示提交结果不确定，禁止渲染为失败或成功。 */
export const writeStatus: Record<string, string> = { succeeded: '已确认提交', failed: '未提交', uncertain: '提交结果不确定', unconfirmed: '尚未取得最终凭据' }
/** 写入模式中文名：键与后端 mode 字段一致，禁止翻译键名。 */
export const modeNames: Record<string, string> = { insert: '追加记录', update: '按唯一键更新' }
/** 新建写回目标的空表单初值；sourceVersion 从 1 起，必须与所选数据源当前版本一致才允许保存。 */
export const emptyTarget = (): WriteTarget => ({ name: '', datasourceId: '', sourceVersion: 1, tableName: '', columns: [], modes: [], status: 'active' })

interface PagedEnvelope<T> { data: T[]; total: number; limit: number; offset: number; hasMore: boolean }
