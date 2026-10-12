/**
 * 数据源列表的组合式函数（数据源工作区）。
 * 职责：分页加载数据源、删除、启用/停用切换、连通性体检、元数据同步。
 * 加载/取消策略：用自增的 requestRevision 作为"请求代际"，旧响应一律丢弃（不依赖 AbortController，
 *   因为这些是写后刷新场景）；失败时同时写 loadError 并向上抛出，调用方可再弹提示。
 * 兼容策略：首次优先请求分页接口，仅在 404/405（老版本后端没有该路由）时退回全量列表并在前端分页，
 *   鉴权失败与 5xx 必须原样抛出（禁止把权限问题当成"路由不存在"而静默降级）。
 * 错误暴露：sources/total/page/loading/loadError 均为只读视图，调用方无法直接改写列表状态。
 */
import { shallowReadonly, shallowRef } from 'vue'
import { del, get, post, put } from '../api/request'
import type { DataSourceHealth, DataSourceSummary } from '../components/datasources/types'

type SourcePage = { items: DataSourceSummary[]; total: number; page: number; pageSize: number }
type PageOptions = { page: number; pageSize: number; keyword?: string }

type Envelope<T> = { success: boolean; data: T; message?: string }

/**
 * 数据源工作区状态。
 * 返回：只读列表/总数/页码/加载中/加载错误，以及 load（可传 page/pageSize/keyword）、
 *   remove、toggle（启用停用）、checkHealth（体检并就地更新该行）、sync（同步元数据）。
 * 副作用：remove/toggle/sync 会调用写接口并随后刷新列表；checkHealth 只更新行内状态（失败时标记 unhealthy 并抛出）。
 */
export function useDataSources() {
  const sourceState = shallowRef<DataSourceSummary[]>([])
  const totalState = shallowRef(0)
  const pageState = shallowRef(1)
  let currentOptions: PageOptions = { page: 1, pageSize: 12 }
  let requestRevision = 0
  let legacyList = false
  const loadingState = shallowRef(false)
  const errorState = shallowRef('')

  function replaceSource(id: string, values: Partial<DataSourceSummary>) {
    sourceState.value = sourceState.value.map((source) => (
      source.id === id ? { ...source, ...values } : source
    ))
  }

  async function load(options: PageOptions = currentOptions) {
    currentOptions = { ...options }
    const revision = ++requestRevision
    loadingState.value = true
    errorState.value = ''
    try {
      const query = new URLSearchParams({ page: String(options.page), page_size: String(options.pageSize), keyword: options.keyword || '' })
      let response: Envelope<SourcePage> | undefined
      if (!legacyList) {
        try {
          response = await get<Envelope<SourcePage>>('/api/v1/analytics/datasources/page?' + query)
        } catch (cause) {
          const status = (cause as { response?: { status?: number } }).response?.status
          if (revision !== requestRevision) return
          // During rolling upgrades the running API can predate this UI.
          // Only missing routes fall back; authorization and server failures
          // must keep their original meaning and never trigger another call.
          if (status !== 404 && status !== 405) throw cause
          legacyList = true
        }
      }
      if (revision !== requestRevision) return
      if (!response) {
        const result = await get<Envelope<DataSourceSummary[]>>('/api/v1/analytics/datasources')
        if (revision !== requestRevision) return
        const keyword = (options.keyword || '').trim().toLocaleLowerCase()
        const rows = result.data.filter(source => !keyword || source.name.toLocaleLowerCase().includes(keyword))
        const page = Math.min(options.page, Math.max(1, Math.ceil(rows.length / options.pageSize)))
        response = { success: result.success, data: { items: rows.slice((page - 1) * options.pageSize, page * options.pageSize), total: rows.length, page, pageSize: options.pageSize } }
      }
      if (revision !== requestRevision) return
      totalState.value = response.data.total
      pageState.value = response.data.page
      currentOptions = { ...options, page: response.data.page }
      sourceState.value = response.data.items.map((source) => ({
        ...source,
        healthStatus: source.healthStatus || 'unknown',
      }))
    } catch (caught) {
      if (revision !== requestRevision) return
      errorState.value = '加载数据源失败：' + (caught as Error).message
      throw caught
    } finally {
      if (revision === requestRevision) loadingState.value = false
    }
  }

  async function remove(source: DataSourceSummary) {
    await del('/api/v1/analytics/datasources/' + source.id)
    await load()
  }

  async function toggle(source: DataSourceSummary) {
    await put('/api/v1/analytics/datasources/' + source.id, {
      id: source.id,
      name: source.name,
      type: source.type,
      host: source.host,
      port: source.port,
      databaseName: source.databaseName,
      username: source.username,
      description: source.configuredDescription || '',
      enabled: !source.enabled,
      version: source.version,
      ssl: source.ssl,
      allowedTables: source.allowedTables,
      allowedColumns: source.allowedColumns,
      timeoutSeconds: source.timeoutSeconds,
      maxRows: source.maxRows,
    })
    await load()
  }

  async function checkHealth(source: DataSourceSummary) {
    try {
      const response = await post<Envelope<DataSourceHealth>>(
        `/api/v1/analytics/datasources/${source.id}/health-checks`,
      )
      replaceSource(source.id, {
        healthStatus: response.data.status,
        lastHealthCheckAt: response.data.checkedAt,
        healthLatencyMs: response.data.latencyMs,
      })
      return response.data
    } catch (caught) {
      replaceSource(source.id, { healthStatus: 'unhealthy' })
      throw caught
    }
  }

  async function sync(source: DataSourceSummary) {
    const response = await post<Envelope<Record<string, unknown>>>(
      `/api/v1/analytics/datasources/${source.id}/sync`,
    )
    await load()
    return response.data
  }

  return {
    sources: shallowReadonly(sourceState),
    total: shallowReadonly(totalState),
    page: shallowReadonly(pageState),
    loading: shallowReadonly(loadingState),
    loadError: shallowReadonly(errorState),
    load,
    remove,
    toggle,
    checkHealth,
    sync,
  }
}
