import { DASHBOARD_SHOWCASES, createDashboardShowcaseContent } from './reportDashboardShowcases.js'

const clone = value => JSON.parse(JSON.stringify(value))

const TEMPLATE_OPTION_DATA_KEYS = new Set([
  'data', 'dataset', 'source', 'dimensions', 'encode', 'sql', 'query', 'datasourceid',
  'chartdata', 'chartdataparsed', 'rawdata', 'text', 'subtext', 'header', 'unit', 'value',
  'values', 'name', 'names', 'formatter', 'categories', 'nodes', 'links', 'indicator',
  'coords', 'geocoord', 'transform', 'datasetid', 'fromdatasetid'
])

function sanitizeTemplateVisualOption(value) {
  if (Array.isArray(value)) return value.map(sanitizeTemplateVisualOption)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !TEMPLATE_OPTION_DATA_KEYS.has(key.toLowerCase()))
    .map(([key, entry]) => [key, sanitizeTemplateVisualOption(entry)]))
}

const neutralLabels = Object.freeze({
  ambient: '环境背景', frame: '分区框架', 'dashboard-title': '主标题占位', 'live-clock': '实时时间',
  metric: '指标卡区域', line: '趋势分析区域', area: '面积趋势区域', bar: '对比分析区域',
  hbar: '排行分析区域', pie: '结构分析区域', gauge: '进度分析区域', radar: '多维分析区域',
  table: '明细列表区域', map: '区域分布主视觉', hero: '中心主视觉', 'hero-map': '区域分布主视觉',
  'hero-gauge': '核心进度主视觉', 'hero-pie': '结构分析主视觉', 'hero-rings': '结构分析主视觉',
  'hero-bars': '对比分析主视觉', 'hero-number': '核心指标主视觉', 'hero-orb': '中心态势主视觉',
  'hero-trend': '核心趋势主视觉'
})

function neutralWidget(widget, templateId) {
  const copy = clone(widget)
  const role = copy.visualRole || copy.type || 'component'
  const label = neutralLabels[role] || neutralLabels[copy.type] || '数据组件区域'
  copy.id = `${templateId}-${copy.id}`
  copy.i = copy.id
  copy.componentName = label
  copy.title = undefined
  copy.content = undefined
  copy.value = undefined
  copy.locked = false
  copy.visible = true
  copy.datasourceId = undefined
  copy.query = undefined
  copy.config = copy.config || {}
  delete copy.config.sql
  delete copy.config.datasourceId
  delete copy.config.query
  delete copy.config.drilldown
  copy.config.dataType = 1
  copy.config.chartData = '[]'
  copy.config.chartDataParsed = []
  if (copy.config.option?.title && typeof copy.config.option.title === 'object') {
    copy.config.option.title = { ...copy.config.option.title, text: '' }
  }
  if (copy.config.option?.body) copy.config.option.body = { ...copy.config.option.body, text: '' }
  if (copy.config.option?.header) copy.config.option.header = []
  if (copy.type === 'metric') copy.config.option = { ...copy.config.option, title: '', unit: '' }
  return copy
}

export function convertShowcaseToTemplateContent(showcase) {
  const content = createDashboardShowcaseContent(showcase)
  if (!showcase || !content) return null
  return {
    canvas: {
      ...content.canvas,
      showcaseId: undefined,
      sourceShowcaseId: showcase.id,
      templateId: showcase.templateId,
      backgroundImage: '',
      referencePreview: undefined
    },
    widgets: content.widgets.map(widget => neutralWidget(widget, showcase.templateId))
  }
}

export const DASHBOARD_TEMPLATES = Object.freeze(DASHBOARD_SHOWCASES.map(showcase => {
  const content = convertShowcaseToTemplateContent(showcase)
  return Object.freeze({
    id: showcase.templateId,
    sourceShowcaseId: showcase.id,
    geometrySignature: showcase.geometrySignature,
    code: showcase.code,
    name: showcase.name,
    shortName: showcase.shortName,
    summary: showcase.summary,
    preview: showcase.preview,
    visualBlueprint: showcase.theme.id,
    accent: showcase.theme.primary,
    secondary: showcase.theme.secondary,
    defaultDatasourceId: null,
    componentCount: content.widgets.length,
    componentCountLabel: `${showcase.regions.length} 个业务分区`,
    tags: Object.freeze([showcase.shortName, showcase.theme.id, showcase.regions.some(item => item.role === 'hero-map') ? '地图焦点' : '多组件组合']),
    layout: Object.freeze([...showcase.layout]),
    visualTokens: Object.freeze({ ...showcase.theme, frame: '沿用成品报表的面板边界、层级、留白和焦点比例' }),
    componentSuggestions: Object.freeze([...showcase.components]),
    aiConstraints: Object.freeze([...showcase.constraints]),
    prompt: `沿用 ${showcase.theme.id} 成品报表的构图、色彩、组件角色与信息密度，只替换业务内容和数据绑定。`,
    datasourceHints: Object.freeze([
      '先读取当前数据源的真实表、字段注释和关联关系',
      '每个业务组件单独生成只读查询并校验非空结果',
      '分类、趋势、结构、状态和明细使用可读业务字段',
      '剔除内部标识符、空值、全零序列和无法解释的分类'
    ]),
    content: Object.freeze(content)
  })
}))

export function getDashboardTemplate(templateId) {
  return DASHBOARD_TEMPLATES.find(template => template.id === templateId) || null
}

export function createDashboardTemplateContent(templateOrId) {
  const template = typeof templateOrId === 'string' ? getDashboardTemplate(templateOrId) : templateOrId
  return template?.content ? clone(template.content) : null
}

export function buildDashboardTemplateReference(templateOrId) {
  const template = typeof templateOrId === 'string' ? getDashboardTemplate(templateOrId) : templateOrId
  if (!template) return ''
  const content = createDashboardTemplateContent(template)
  const blueprint = {
    canvas: {
      width: content.canvas.width,
      height: content.canvas.height,
      backgroundColor: content.canvas.backgroundColor,
      themeId: content.canvas.themeId,
      theme: content.canvas.theme,
      visualBlueprint: content.canvas.visualBlueprint
    },
    widgets: content.widgets.map(widget => ({
      component: widget.component,
      type: widget.type,
      visualRole: widget.visualRole,
      panelVariant: widget.panelVariant,
      chromeMode: widget.chromeMode,
      frameMode: widget.frameMode,
      x: widget.x,
      y: widget.y,
      w: widget.w,
      h: widget.h,
      rotation: widget.rotation || 0,
      style: widget.style,
      option: widget.config?.option
        ? sanitizeTemplateVisualOption(widget.config.option)
        : undefined
    }))
  }
  return [
    '参考模板（仅作设计参照，用户当前需求优先）：',
    `模板ID：${template.id}`,
    `模板名称：${template.name}`,
    `视觉骨架标识：${template.visualBlueprint}`,
    `模板定位：${template.summary}`,
    `布局重点：${template.layout.join('；')}`,
    `视觉语言：背景 ${template.visualTokens.background}，面板 ${template.visualTokens.panel}，主色 ${template.visualTokens.primary}，辅色 ${template.visualTokens.secondary}，强调色 ${template.visualTokens.accent}，${template.visualTokens.frame}`,
    `组件建议：${template.componentSuggestions.join('、')}`,
    `AI约束：${template.aiConstraints.join('；')}`,
    `组件规模：${template.componentCountLabel}，保留成品报表的组件角色、几何比例和信息密度，允许在固定分区内按真实数据调整。`,
    `数据方向：${template.datasourceHints.join('；')}`,
    `精确视觉蓝图JSON：${JSON.stringify(blueprint)}`,
    '内容规则：业务文字、指标值、单位和查询必须依据用户需求与所选真实数据库重新生成；成品报表中的示例标题、示例数值和示例数据不得进入结果。',
    '执行原则：用户需求和真实数据优先；模板只约束视觉、配色、布局和组件组合，不固定业务语义。'
  ].join('\n')
}

export function createDashboardTemplateReportPayload(templateOrId, overrides = {}) {
  const template = typeof templateOrId === 'string' ? getDashboardTemplate(templateOrId) : templateOrId
  const content = createDashboardTemplateContent(template)
  if (!template || !content) return null
  const canvas = content.canvas || {}
  return {
    reportName: overrides.reportName || template.name,
    description: overrides.description || `${template.summary}（仅载入视觉骨架，业务内容由 AI 与数据源生成）`,
    datasourceId: overrides.datasourceId ?? template.defaultDatasourceId ?? null,
    width: Number(canvas.width || 1920),
    height: Number(canvas.height || 1080),
    backgroundColor: canvas.backgroundColor || template.visualTokens.background,
    backgroundImage: '',
    refreshSeconds: 60,
    thumbnail: overrides.thumbnail || template.preview,
    enabled: 1,
    content: JSON.stringify(content)
  }
}
