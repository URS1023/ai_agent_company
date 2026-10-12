/**
 * 报表提示词库接口封装（/api/report-prompt → 网关映射到 /api/v1/report-prompts）。
 * 职责：维护"场景分类 + 提示词模板"，供报表/大屏 AI 生成时插入提示词。
 * 关键约束：分类与模板均为可编辑资源（有创建/更新/删除），删除分类前需确认其下模板的处理方式；
 *   useTemplate 只做"使用次数/最近使用"登记，不产生生成请求。
 * 错误语义：所有方法失败时抛 Error（message 已在 request.ts 拦截器里转成后端返回的中文提示）。
 */
import { get, post, put, del } from './request'

const P = '/api/report-prompt'

export const reportPromptApi = {
  /** 分类列表：GET /categories（只读，无分页）。 */
  listCategories: () => get(`${P}/categories`),
  /** 新建分类：POST /categories，body 至少含名称与排序字段。 */
  createCategory: (data) => post(`${P}/categories`, data),
  /** 更新分类：PUT /categories/{id}。 */
  updateCategory: (id, data) => put(`${P}/categories/${id}`, data),
  /** 删除分类：DELETE /categories/{id}；分类下仍有模板时由后端决定拒绝或级联，前端需处理错误提示。 */
  deleteCategory: (id) => del(`${P}/categories/${id}`),
  /** 模板分页查询：GET /templates，params 支持分类、关键字与分页参数。 */
  listTemplates: (params = {}) => get(`${P}/templates`, params),
  /** 新建模板：POST /templates，body 含分类 id、标题、提示词正文。 */
  createTemplate: (data) => post(`${P}/templates`, data),
  /** 更新模板：PUT /templates/{id}。 */
  updateTemplate: (id, data) => put(`${P}/templates/${id}`, data),
  /** 删除模板：DELETE /templates/{id}。 */
  deleteTemplate: (id) => del(`${P}/templates/${id}`),
  /** 标记模板被使用：POST /templates/{id}/use（仅登记使用记录，不触发生成）。 */
  useTemplate: (id) => post(`${P}/templates/${id}/use`)
}

export default reportPromptApi
