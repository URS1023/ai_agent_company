/**
 * reportPromptLibrary —— 报表/大屏 AI 提示词模板库的纯数据处理工具。
 *
 * 位置：ReportPromptLibraryPanel 与 JimuBigScreenReplica 的 AI 生成流程。
 * 关键约束：
 * - 分类与模板来自接口，二者通过 categoryId ↔ id 关联，关联时统一转成字符串比较
 *   （后端可能返回数字或字符串 id），否则模板会全部落到空分类；
 * - 所有函数纯函数、不修改入参，便于在 computed 中直接使用。
 */

/**
 * 把模板按分类归组。
 * @returns 新数组：每个分类附带 templates 字段（无模板时为空数组，不是 undefined）
 */
export function mergePromptLibrary(categories = [], templates = []) {
  const byCategory = new Map()
  templates.forEach(template => {
    const categoryId = String(template.categoryId)
    const list = byCategory.get(categoryId) || []
    list.push(template)
    byCategory.set(categoryId, list)
  })
  return categories.map(category => ({
    ...category,
    templates: byCategory.get(String(category.id)) || []
  }))
}

/**
 * 按关键词过滤模板（匹配名称/内容/描述/标签，忽略大小写）；关键词为空时原样返回。
 * @returns 过滤后的新数组
 */
export function filterPromptTemplates(templates = [], keyword = '') {
  const normalized = String(keyword || '').trim().toLowerCase()
  if (!normalized) return templates
  return templates.filter(template => [template.name, template.content, template.description, template.tags]
    .some(value => String(value || '').toLowerCase().includes(normalized)))
}

/** 把模板整理为可编辑草稿（字段缺失时给空串，id 缺失为 null 表示新建）。 */
export function promptTemplateToDraft(template = {}) {
  return {
    id: template.id || null,
    name: template.name || '',
    content: template.content || '',
    description: template.description || '',
    tags: template.tags || ''
  }
}

/**
 * 追加提示词文本：用于"套用模板"时在用户已写内容后补一段，而不是整体覆盖。
 * 任一侧为空则返回另一侧；已包含相同内容时不重复追加（幂等，避免多次点击叠加重复文本）。
 */
export function appendPromptText(current = '', addition = '') {
  const left = String(current || '').trim()
  const right = String(addition || '').trim()
  if (!left) return right
  if (!right) return left
  if (left === right || left.includes(right)) return left
  return `${left}\n\n${right}`
}
