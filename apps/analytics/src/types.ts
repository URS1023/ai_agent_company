/**
 * analytics 模块的公共响应类型（与后端 FastAPI 的 schema 对应，字段名保持一致、不翻译）。
 * 说明：Envelope 为多数接口的外层包装；QueryResult/History 描述一次查询的输入输出与历史记录；
 *   本文件只放类型，不含任何运行时逻辑，改动字段时需同步后端 backend/app/modules/analytics/schemas.py。
 */
export interface Envelope<T> { success: boolean; data: T; message: string }
export interface Source {
  id: string; name: string; type: string; enabled: boolean; version: number
  databaseName: string; description: string; maxRows: number
  configuredDescription?: string; autoDescription?: string; descriptionSource?: 'configured' | 'metadata' | 'empty'
  environmentSummary?: string; metadataStatus?: string; semanticIndexStatus?: string
  businessTableCount?: number; fieldCount?: number; businessDomains?: string[]
}
export interface Column { columnName: string; dataType: string; comment: string; primaryKey?: boolean; nullable?: boolean }
export interface Table { tableName: string; tableComment: string; columns: Column[]; rowCount: number | null }
export interface QueryResult {
  queryId: string; runId: string; datasourceId: string; generatedSql: string; totalCount: number
  queryType?: string
  parameters?: Record<string, string | number | boolean | null>
  truncated: boolean; executionTime: number; data: Record<string, unknown>[]; columns: Column[]
  statistics: Record<string, any>; analysis: string; insights: Record<string, any>[]
  enhancedAnalysis: Record<string, any>; retrievalEvidence: Record<string, any>
  chartRecommendations: Record<string, any>[]
}
export interface QueryTemplate {
  id: string; name: string; category: string; content: string
  mode: 'natural_language' | 'sql'; parameters: Record<string, unknown>
  usageCount: number; version: number
}
export interface History {
  id: string; datasourceId: string; queryText: string; generatedSql: string; queryMode: string
  status: string; executionTime: number; resultCount: number; createTime: string; errorMessage: string
  parameters?: Record<string, string | number | boolean | null>
}
export interface PromptCategory {
  id: string; name: string; description: string; icon: string; color: string
  sortOrder: number; builtin: boolean; version: number
}
export interface Prompt {
  id: string; name: string; categoryId: string; content: string; description: string; tags: string
  mode: 'screen' | 'widget' | 'query'; builtin: boolean; useCount: number; version: number
}
