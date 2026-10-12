/**
 * AI报表大屏 API
 * 路径前缀 /api/v1/reports（另有两个跨域前缀：/api/v1/report-templates、/api/v1/reports/{id}/widgets）。
 * 职责：报表/大屏的增删改查、AI 生成（单组件与整屏）、组件数据钻取与取数、历史版本回滚。
 * 关键约束：写操作只提交 fields 白名单里的字段（reportInput 过滤），避免把渲染态字段写库；
 *   AI 生成走 createGenerationRequester（带 requestKey 幂等与明确重试）；生成类请求等待服务端终态，
 *   不用固定客户端秒数提前判定长任务失败。
 * 错误语义：失败抛 Error（中文 message 由 request.ts 拦截器统一转换），调用方需保留用户已填内容。
 */
import { get, post, put, del, scopedKey } from './request'
import { createClientId } from '../utils/randomId'
import { createGenerationRequester } from '../utils/generationRequest'

const P = '/api/v1/reports'
const requestGeneration = createGenerationRequester({
  send: (kind, data, options) => post(`${P}/${kind}/ai-generate`, data, options),
  createId: createClientId,
  scope: () => scopedKey('report-generation'),
})
const fields = ['id', 'reportName', 'description', 'datasourceId', 'datasourceIds', 'content', 'width', 'height',
  'backgroundColor', 'backgroundImage', 'thumbnail', 'refreshSeconds', 'enabled', 'status', 'templateMode', 'templateId', 'version', 'editTemplateLayout']
function reportInput(data) {
  return Object.fromEntries(fields.filter(key => data[key] !== undefined).map(key => [key, data[key]]))
}

export const reportApi = {
  // 报表列表
  list: (keyword) => get(`${P}/list`, keyword ? { keyword } : {}),

  // 报表详情
  getById: (id) => get(`${P}/${id}`),

  // 新增报表
  create: (data) => post(P, reportInput(data)),

  // 更新报表
  update: (id, data) => put(`${P}/${id}`, reportInput(data)),

  // 删除报表
  delete: (id) => del(`${P}/${id}`),

  // 将已确认报表固化为“只填数据”模板；源报表不会被修改
  createTemplateFromReport: (id, data) => post(`/api/v1/report-templates/from-report/${id}`, data),

  // AI生成图表组件；等待服务端终态，不用固定客户端总时限截断长任务。
  aiGenerate: (data) => requestGeneration('widget', data),

  // AI整屏生成；服务端完成后返回，传输层不以固定秒数判定失败。
  aiGenerateScreen: (data) => requestGeneration('screen', { templateMode: 'auto', ...data }),

  // 组件点击后的真实数据钻取
  /** 真实数据钻取：POST /api/v1/reports/drilldown，入参含组件上下文与点击维度；超时 60s，返回明细行。 */
  drilldown: (data) => post(`${P}/drilldown`, data, { timeout: 60000 }),
  /** 单个组件取数：GET /api/v1/reports/{id}/widgets/{widgetId}/data；widgetId 必须编码后再拼路径。 */
  widgetData: (id, widgetId) => get(`${P}/${id}/widgets/${encodeURIComponent(widgetId)}/data`),
  /** 历史版本列表：GET /api/v1/reports/{id}/revisions（只读，用于版本下拉）。 */
  revisions: (id) => get(`${P}/${id}/revisions`),
  /** 回滚到指定版本：POST /api/v1/reports/{id}/restore；副作用是覆盖当前内容，需前端二次确认。 */
  restore: (id, data) => post(`${P}/${id}/restore`, data),
}

export default reportApi
