/**
 * 工作流接口层与数据结构定义（/api/v1/workflows）。
 * 职责：工作流列表/详情/保存/校验/发布停用/历史版本、AI 生成、运行（含等待输入后的恢复与取消）、
 *   运行记录与监控指标，以及导出；同时集中定义 FlowNode/FlowEdge/FlowRun 等共享契约。
 * 关键约束：
 *   1) 更新类调用都带 version 做乐观锁；发布/停用走 /publish、/disable 且需权限；
 *   2) 运行类调用带 idempotencyKey 幂等键，避免重复点击产生两次执行；
 *   3) 运行结果对 200/202/502 都返回给调用方，必须按 FlowRun.status 判定成功/失败/等待输入；
 *   4) catalogFallbacks 仅用于"前端先上线、后端未重启"的灰度兼容，服务端返回 defaultConfig 时以其为准。
 */
/** Versioned workflow contracts shared by ordinary pages and AI callers. */
import { del, get, post, put } from './request'
import { createClientId } from '../utils/randomId'
export interface NodeReference { nodeId: string; path: string }
export interface WorkflowRetryConfig { retryEnabled: boolean; maxRetries: number; retryInterval: number }
export interface StartInputVariable {
  variable: string
  label: string
  type: 'text-input' | 'paragraph' | 'select' | 'number' | 'url' | 'json' | 'json_object' | 'checkbox' | 'file' | 'file-list'
  required: boolean
  maxLength?: number | null
  default?: any
  hint?: string
  placeholder?: string
  unit?: string
  options?: string[]
}
export interface FlowNode {
  id: string
  title: string
  kind: string
  description: string
  position: { x: number; y: number }
  inputs: Record<string, NodeReference>
  config: Record<string, any>
  retryConfig: WorkflowRetryConfig
  errorStrategy: 'none' | 'default-value' | 'fail-branch'
  defaultValue?: any
}
export type FlowSide = 'left' | 'right' | 'top' | 'bottom'
export type FlowPort = 'out' | 'true' | 'false' | 'error' | `case:${string}`
export interface FlowEdge { id: string; source: string; target: string; port: FlowPort; sourceSide?: FlowSide; targetSide?: FlowSide }
export interface FlowGraph { nodes: FlowNode[]; edges: FlowEdge[] }
export interface QuestionField { name: string; label: string; type: 'text-input' | 'paragraph' | 'number' | 'select' | 'checkbox' | 'json' | 'json_object'; required: boolean; options?: string[]; placeholder?: string }
export interface WorkflowWaiting { nodeId?: string; title?: string; kind?: string; prompt?: string; answerKey?: string; required?: boolean; fields?: QuestionField[]; resumeAt?: string; seconds?: number }
export interface Validation { valid: boolean; errors: { nodeId?: string; message: string }[]; order: string[] }
export interface Workflow { id?: string; name: string; description: string; domains: string[]; graph: FlowGraph; version?: number; publishedRevision?: number | null; status: string; validation?: Validation; updateTime?: string }
export interface PublishedWorkflow { revision: number; version: number; graph: FlowGraph; name: string; domains: string[] }
export interface NodeKind { kind: string; title: string; group: string; description: string; source: string; available: boolean; configSchema: Record<string, any> | null; defaultConfig?: Record<string, any> | null }
export interface WorkflowGeneration { mode: 'model' | 'repaired' | 'fallback'; attempts: number; warnings: string[] }
export interface FlowRun { id: string; workflowId: string; revision: number; runId: string; version: number; status: string; errorMessage: string; createTime: string; durationMs: number; snapshot?: Record<string, any>; inputs?: Record<string, any>; result?: { collectionWindow?: { start: string; end: string; timeZone: string; mode: string; hasMore?: boolean; afterKey?: number }; nodes: Record<string, any>; outputs: Record<string, any>; durationMs: number; waiting?: WorkflowWaiting; controls?: Record<string, any>[] } }
export interface WorkflowMonitorPoint { date: string; runs: number; completed: number; failed: number; activeUsers: number; tokens: number; cost: number; averageDurationMs: number }
export interface WorkflowMonitor {
  timeZone: string
  days: number
  start: string
  end: string
  totals: { runs: number; activeUsers: number; cost: number; tokens: number; unpricedTokens: number; averageRunsPerUser: number; averageDurationMs: number; completed: number; failed: number }
  series: WorkflowMonitorPoint[]
}
interface Envelope<T> { data: T; success: boolean; message: string }
export interface WorkflowRunFilters { keyword?: string; status?: string; days?: number }
const browserTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
export const base = '/api/v1/workflows'
export const workflows = {
  /** 列表：GET /api/v1/workflows，limit 固定 30；keyword 按名称模糊搜索，domain 用于按域过滤（不传则不过滤）。 */
  list: (keyword = '', offset = 0, signal?: AbortSignal, domain?: string) => get<Envelope<Workflow[]>>(base, { keyword, offset, limit: 30, ...(domain ? { domain } : {}) }, { signal }),
  /** 详情：GET /api/v1/workflows/{id}（含 graph 与 validation），编辑器进入时调用。 */
  get: (id: string, signal?: AbortSignal) => get<Envelope<Workflow>>(base + '/' + id, {}, { signal }),
  /** 正式运行仅读取发布修订的不可变图，禁止用当前草稿推导输入字段。 */
  published: (id: string, signal?: AbortSignal) => get<Envelope<PublishedWorkflow>>(base + '/' + id + '/published-revision', {}, { signal }),
  /** 节点目录：GET /api/v1/workflows/catalog，返回可用节点类型与 configSchema/defaultConfig，用于节点面板与表单渲染。 */
  catalog: () => get<Envelope<NodeKind[]>>(base + '/catalog'),
  /**
   * 保存工作流：无 id → POST 新建（草稿），有 id → PUT 更新（携带 version）。
   * 只提交 name/description/domains/graph，不提交服务端维护的 status/validation。
   */
  save: (flow: Workflow) => {
    const payload = { name: flow.name, description: flow.description, domains: flow.domains, graph: flow.graph, ...(flow.id ? { version: flow.version } : {}) }
    return flow.id ? put<Envelope<Workflow>>(base + '/' + flow.id, payload) : post<Envelope<Workflow>>(base, payload)
  },
  /** 图校验：POST /api/v1/workflows/validate，返回 errors 与拓扑 order；不落库，供保存/发布前预检。 */
  validate: (graph: FlowGraph) => post<Envelope<Validation>>(base + '/validate', graph),
  /**
   * AI 生成工作流：POST /api/v1/workflows/generate。
   * 参数：prompt（自然语言描述）、name、domains、resourceIds（可选知识/数据源引用）。
   * 返回：runId + workflow + model + usage + generation（mode=model/repaired/fallback，说明是否走了修复或兜底）。
   * 不设置浏览器总时限；失败时保留提示词供用户重试。
   */
  generate: (prompt: string, name: string, domains: string[], resourceIds: string[] = []) => post<Envelope<{ runId: string; workflow: Workflow; model: string; usage: Record<string, number>; generation?: WorkflowGeneration }>>(base + '/generate', { prompt, name, domains, resourceIds }),
  /** 发布/停用：POST /api/v1/workflows/{id}/publish 或 /disable；需要 version，且有权限校验。 */
  status: (flow: Workflow, publish: boolean) => post<Envelope<Workflow>>(base + '/' + flow.id + (publish ? '/publish' : '/disable'), { version: flow.version }),
  /** 删除：DELETE /api/v1/workflows/{id}?version=...；已发布或被调度引用时后端会拒绝。 */
  remove: (flow: Workflow) => del(base + '/' + flow.id, { params: { version: flow.version } }),
  /** 历史版本：GET /api/v1/workflows/{id}/revisions（只读）。 */
  revisions: (id: string) => get<Envelope<any[]>>(base + '/' + id + '/revisions'),
  /** 回滚到指定版本：POST /api/v1/workflows/{id}/restore；会覆盖当前图，需要用户二次确认。 */
  restore: (flow: Workflow, revision: number) => post<Envelope<Workflow>>(base + '/' + flow.id + '/restore', { version: flow.version, revision }),
  /** 运行记录：GET /api/v1/workflows/{id}/runs；filters 支持 keyword/status/days，timeZone 取浏览器时区供后端分日统计。 */
  runs: (id: string, offset = 0, limit = 30, signal?: AbortSignal, filters: WorkflowRunFilters = {}) => get<Envelope<FlowRun[]>>(base + '/' + id + '/runs', { offset, limit, ...filters, timeZone: browserTimeZone() }, { signal }),
  /** 监控指标：GET /api/v1/workflows/{id}/monitor?days=N，返回总量与逐日序列（按浏览器时区切分）。 */
  monitor: (id: string, days = 7, signal?: AbortSignal) => get<Envelope<WorkflowMonitor>>(base + '/' + id + '/monitor', { days, timeZone: browserTimeZone() }, { signal }),
  /** 单次运行详情：GET /api/v1/workflows/{id}/runs/{execution}；结果面板与调试面板据此渲染。 */
  result: (id: string, execution: string) => get<Envelope<FlowRun>>(base + '/' + id + '/runs/' + execution),
  /**
   * 触发运行：POST /api/v1/workflows/{id}/runs。
   * 关键字段：version（发布版本快照）、inputs（起始节点变量）、mode（试运行/正式）、
   *   idempotencyKey（幂等，防止重复点击）、confirmWrites（是否允许写库/写外部，默认 false）、timeoutSeconds。
   * 不设置浏览器总时限；200/202/502 都返回，需按 status 判断是完成、等待输入还是失败。
   */
  run: (flow: Workflow, inputs: object, mode: string, key: string, confirmWrites = false) => post<Envelope<FlowRun>>(base + '/' + flow.id + '/runs',
    { version: flow.version, inputs, mode, idempotencyKey: key, confirmWrites }, { validateStatus: status => [200, 202, 502].includes(status) }),
  /** 恢复等待中的运行：POST /api/v1/workflows/{id}/runs/{runId}/resume，input 为 question 节点答案；同样带幂等键。 */
  resume: (run: FlowRun, input: unknown, key: string) => post<Envelope<FlowRun>>(base + '/' + run.workflowId + '/runs/' + run.id + '/resume',
    { version: run.version, input, idempotencyKey: key }, { validateStatus: status => [200, 202, 502].includes(status) }),
  /** 取消运行：POST /api/v1/workflows/{id}/runs/{runId}/cancel；取消是请求式的，需按返回的 cancel_requested 状态轮询确认。 */
  cancel: (run: FlowRun, key: string) => post<Envelope<FlowRun>>(base + '/' + run.workflowId + '/runs/' + run.id + '/cancel',
    { version: run.version, idempotencyKey: key }, { validateStatus: status => [200, 202].includes(status) }),
  /** 导出工作流定义：GET /api/v1/workflows/{id}/export，返回带 schemaVersion 的 JSON，供离线迁移/备份。 */
  export: (id: string) => get<Envelope<{ schemaVersion: number; workflow: object }>>(base + '/' + id + '/export'),
}
/** 工作流/运行状态中文名：键必须与服务端状态枚举一致（含运行态与取消中间态），禁止翻译键名。 */
export const flowStatus: Record<string, string> = { draft: '草稿', published: '已发布', disabled: '已停用', running: '执行中', waiting: '等待输入', cancel_requested: '取消处理中', completed: '执行完成', failed: '执行失败', skipped: '未执行', pending: '等待执行', cancelled: '已取消' }
/** 工作流域中文名：键与后端 domain 取值一致；用于列表筛选项与卡片标签。 */
export const domainNames: Record<string, string> = { general: '通用', analysis: '数据分析', alert: '设备预警', quality: '质量检查' }
/** 深拷贝节点/图数据：图编辑器需要"编辑副本回滚"语义，因此禁止直接把 store 里的对象交给表单双向绑定。 */
export const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value))
// Rolling-deployment compatibility for a browser that receives the new bundle
// before an already-running backend process has reloaded the catalog contract.
// Once present, the server-provided defaultConfig always wins.
const catalogFallbacks: Record<string, Record<string, any>> = {
  start: { variables: [] },
  'http-request': { datasourceId: '', datasourceVersion: null, method: null, url: null, headers: {}, params: {}, body: null, auth: { type: 'none', credentialName: null, headerName: 'Authorization' }, responseFormat: null, timeoutSeconds: null, retry: 0, retryInterval: 500, confirmWrites: false },
  'database-query': { datasourceId: '', datasourceVersion: null, query: '', parameters: {}, maxRows: 1000 },
  'report-analysis': { datasourceId: '', datasourceVersion: null, query: '', parameters: {}, maxRows: 1000 },
  'jsonpath-extract': { recordPath: '$.data', fields: [] },
  arithmetic: { fields: [{ name: '', formula: '' }] },
  condition: { expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] } },
  filter: { expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] } },
  'set-operation': { operation: 'intersection', keys: [] },
  'rule-evaluate': { mode: 'alert', rules: [{ id: 'rule_draft', name: '', severity: 'warning', message: '', expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] } }] },
  'alert-evaluate': { ruleId: '', ruleRevision: 1, deviceId: null },
  'alert-notify': { audience: 'executor', outcomes: ['issues', 'no_data'], minimumSeverity: 'warning' },
  'output-table': { targetId: '', targetVersion: 1, mode: 'insert', recordPath: '$.data', fields: [], keys: [] },
  'qc-appearance': { metricId: '', metricVersion: 1, deviceId: '', datasourceId: '', datasourceVersion: 1, requestMode: 'saved', sampleField: 'sampleId', imageField: 'imageFileId' },
  'qc-component': { metricId: '', metricVersion: 1, deviceId: '', datasourceId: '', datasourceVersion: 1, requestMode: 'saved', sampleField: 'sampleId', imageField: 'imageFileId' },
  'qc-evaluate': { templateId: '', templateRevision: 1, deviceId: '', sampleId: '', sampleMode: 'fixed' },
  llm: { modelProfile: 'reasoning', systemPrompt: '', promptTemplate: '{{ input }}', temperature: 0.1 },
  extractor: { modelProfile: 'reasoning', systemPrompt: '', promptTemplate: '{{ input }}', temperature: 0.1, fields: [{ name: '', type: 'string', description: '', required: false }] },
  agent: { botId: '', mode: 'model', usePublished: true, queryTemplate: '{{ input }}' },
  knowledge: { knowledgeBaseIds: [], queryTemplate: '{{ input }}', topK: 6, minScore: 0, mode: 'hybrid' },
  question: { prompt: '', answerKey: 'answer', required: true, fields: [] },
  classifier: { mode: 'model', modelProfile: 'reasoning', systemPrompt: '', promptTemplate: '{{ input }}', temperature: 0.1, cases: [{ id: 'case_draft', label: '', description: '', expression: { op: 'gte', args: [{ field: '' }, { value: 0 }] } }], fallback: 'other' },
  loop: { iterations: 1, updates: [], keepHistory: false, subgraph: null },
  iterator: { updates: [], subgraph: null },
  while: { expression: { op: 'eq', args: [{ field: '' }, { value: true }] }, updates: [], maxIterations: 20, subgraph: null },
  parallel: { branchNames: [] },
  aggregator: { groupBy: [], metrics: [{ name: 'count', operation: 'count', field: '' }] },
  delay: { seconds: 60 },
  subflow: { workflowId: '', workflowVersion: 1, timeoutSeconds: 120, confirmWrites: false },
  setvar: { keepInput: true, variables: [{ name: '', path: '$.input' }] },
  text: { operation: 'stringify', find: '', replacement: '', separator: ',' },
  template: { template: '{{ input }}' },
  code: { code: 'result = {}', timeoutSeconds: 120 },
  tool: { toolName: '', arguments: {}, includeInput: false },
}
/**
 * 新建节点：返回带默认 config 的 FlowNode。
 * 参数：kind（节点类型，决定 config 结构）、title（画布显示名）、index（用于初始落点排布）、defaultConfig（服务端下发的默认值）。
 * 约束：优先使用服务端 defaultConfig，缺失时回退 catalogFallbacks；新节点 id 由 createClientId 生成，前端唯一即可。
 */
export function newNode(kind: string, title: string, index: number, defaultConfig?: Record<string, any> | null): FlowNode {
  // Keep Dify's 240px node width and 60px horizontal gutter so freshly
  // inserted nodes never overlap before the user runs auto layout.
  return {
    id: 'node_' + createClientId().replaceAll('-', ''), kind, title, description: '',
    position: { x: 80 + index % 4 * 300, y: 282 + Math.floor(index / 4) * 180 }, inputs: {},
    config: copy(defaultConfig ?? catalogFallbacks[kind] ?? {}),
    retryConfig: { retryEnabled: false, maxRetries: 3, retryInterval: 1000 },
    errorStrategy: 'none', defaultValue: null,
  }
}
/** 空白工作流：默认"开始 → 结束"两点一线，域为 general、状态 draft；仅用于"新建"入口，不落库。 */
export function emptyWorkflow(): Workflow {
  const start = newNode('start', '开始', 0), end = newNode('end', '结束', 1)
  return { name: '未命名工作流', description: '', domains: ['general'], status: 'draft', graph: { nodes: [start, end], edges: [{ id: createClientId(), source: start.id, target: end.id, port: 'out', sourceSide: 'right', targetSide: 'left' }] } }
}
