/**
 * queryContext —— 查询工作区的会话级上下文（SQL 草稿、上次使用的数据源、参数解析）。
 *
 * 位置：SqlWorkspace / 数据查询相关视图。
 * 依赖：`api/request.scopedKey`（按环境/用户隔离存储键）、`types` 的 Source / Table。
 * 关键约束：
 * - 存储一律用 sessionStorage + scopedKey：跨环境/跨用户不得串数据；浏览器禁用存储时全部函数必须
 *   静默降级（读写不抛错），功能仍可用；
 * - 显式指定但不可用的数据源**绝不**回退到另一个库（可能连到错误的生产库），
 *   此时 preferredQuerySource 返回空串让用户重新选择；
 * - 命名参数只允许文本/数字/布尔/null，数组与嵌套对象必须报中文错误拒绝（后端参数绑定不支持）。
 */
import { scopedKey } from '../api/request'
import type { Source, Table } from '../types'

export interface SqlDraft { sql: string; parameterJson: string; maxRows: number }

/** 读取该数据源的 SQL 草稿；无草稿、格式不符或存储不可用时返回 null。 */
export function readSqlDraft(sourceId: string): SqlDraft | null {
  if (!sourceId) return null
  try {
    const stored = JSON.parse(sessionStorage.getItem(scopedKey('analysis-sql-draft:' + sourceId)) || 'null')
    return stored && typeof stored.sql === 'string' && typeof stored.parameterJson === 'string'
      ? { sql: stored.sql, parameterJson: stored.parameterJson, maxRows: Number(stored.maxRows) || 1000 }
      : null
  } catch { return null }
}

/** 记住 SQL 草稿；内容为空（无 SQL 且参数为 {}）时清除键，避免残留空草稿。 */
export function rememberSqlDraft(sourceId: string, draft: SqlDraft) {
  if (!sourceId) return
  try {
    const key = scopedKey('analysis-sql-draft:' + sourceId)
    if (!draft.sql && draft.parameterJson === '{}') sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, JSON.stringify(draft))
  } catch { /* Editing remains available if browser session storage is disabled. */ }
}

export function rememberQuerySource(id: string) {
  try {
    if (id) sessionStorage.setItem(scopedKey('analysis-source'), id)
    else sessionStorage.removeItem(scopedKey('analysis-source'))
  } catch { /* Queries remain available when browser storage is disabled. */ }
}

/**
 * 选择要使用的数据源：显式请求优先，其次会话记忆。
 * 只有"存在且启用"的数据源才会被返回；显式指定但不可用时返回空串，绝不静默改用其他数据库。
 */
export function preferredQuerySource(sources: Source[], requested = '') {
  let remembered = ''
  try { remembered = sessionStorage.getItem(scopedKey('analysis-source')) || '' } catch { /* Optional preference. */ }
  // An explicit unavailable source must never silently fall back to another DB.
  const candidate = requested || remembered
  return sources.some(source => source.id === candidate && source.enabled) ? candidate : ''
}

export function suggestedQueryTables(tables: Table[]) {
  const business = tables.filter(table => !/^(?:[._]|act_|flw_|qrtz_|sys_|system_|audit_|t_meta_|information_schema|mysql\.|pg_|sqlite_)/i.test(table.tableName))
  return (business.length ? business : tables).slice(0, 2)
}

export function parseQueryParameters(text: string): Record<string, string | number | boolean | null> {
  let value: unknown
  try { value = JSON.parse(text) } catch { throw new Error('命名参数格式有误，请输入有效的 JSON 对象，例如 {"name":"设备"}') }
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('命名参数需要是 JSON 对象')
  if (Object.values(value).some(item => item !== null && !['string', 'number', 'boolean'].includes(typeof item))) {
    throw new Error('命名参数的值只支持文本、数字、布尔值或 null，不支持数组或嵌套对象')
  }
  return value as Record<string, string | number | boolean | null>
}
