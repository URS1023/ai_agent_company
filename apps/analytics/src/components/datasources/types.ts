/**
 * 数据源工作区（/datasources）的类型定义，字段名与后端 analytics 模块 schema 保持一致，不翻译。
 * 说明：DataSourceSummary 为列表/卡片视图用的摘要（含健康检查与元数据/语义索引状态）；
 *   DataSourcePolicy 为治理策略；DataSourceTable/Column 为表结构浏览；DataSourceHealth 为健康检查历史。
 * 约束：本文件只放类型，不含运行时逻辑；改动字段需同步后端 backend/app/modules/analytics/schemas.py。
 */
export type DataSourceSummary = {
  id: string
  name: string
  type: 'mysql' | 'postgres' | 'sqlite' | 'elasticsearch'
  host: string
  port: number | null
  databaseName: string
  username: string
  hasPassword: boolean
  description: string
  configuredDescription?: string
  autoDescription?: string
  descriptionSource?: 'configured' | 'metadata' | 'empty'
  environmentSummary?: string
  enabled: boolean
  healthStatus: 'unknown' | 'healthy' | 'unhealthy'
  lastHealthCheckAt?: string
  healthLatencyMs?: number | null
  version: number
  ssl: boolean
  allowedTables: string[]
  allowedColumns: Record<string, string[]>
  timeoutSeconds: number
  maxRows: number
  metadataStatus?: string
  tableCount?: number
  businessTableCount?: number
  fieldCount?: number
  commentedTableCount?: number
  commentedFieldCount?: number
  semanticIndexStatus?: string
  semanticIndexedTableCount?: number
  businessDomains?: string[]
  metadataSyncedAt?: string
}

export type DataSourcePolicy = {
  datasourceId: string
  version: number
  maxConcurrency: number
  dailyQueryLimit: number
  healthCheckEnabled: boolean
  healthIntervalMinutes: number
  healthScheduleId: string
  scheduleStatus: string
  nextHealthCheckAt: string
  activeQueries: number
  queriesToday: number
}

export type DataSourceHealth = {
  id: string
  datasourceId: string
  datasourceVersion: number
  triggerType: 'manual' | 'schedule'
  status: 'healthy' | 'unhealthy'
  latencyMs: number
  detail: string
  errorCode: string
  checkedAt: string
}

export type DataSourceTable = {
  tableName: string
  tableComment?: string
  tableType?: string
  rowCount?: number | null
  rowCountEstimated?: boolean
  dataSizeBytes?: number | null
  columns?: DataSourceColumn[]
}

export type DataSourceColumn = {
  columnName: string
  dataType: string
  comment?: string
  nullable?: boolean
  primaryKey?: boolean
}
