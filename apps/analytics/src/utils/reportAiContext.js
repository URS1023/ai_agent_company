/**
 * reportAiContext —— 构造发给模型的报表/大屏上下文与调整需求文本。
 *
 * 位置：JimuBigScreenReplica 的 AI 生成与 AI 调整请求（buildScreenAiRequest / buildWidgetAiRequirement）。
 * 关键约束：
 * - 必须对送入模型的内容做长度裁剪（TEXT_LIMIT / COMPONENT_LIMIT）：画布可能含几十个组件与长 SQL，
 *   不加限制会撑爆上下文；裁剪后需保留"已截断"的可辨识痕迹（追加 ...）；
 * - 只发送结构摘要与业务语义（名称/类型/位置/尺寸/数据源标识），不发送查询结果明细与凭据；
 * - 组件顺序必须与画布顺序一致，便于模型按位置引用组件。
 */
import { createClientId } from './randomId'

const TEXT_LIMIT = 1200
const COMPONENT_LIMIT = 30
let generationSequence = 0

function compactText(value, limit = TEXT_LIMIT) {
  if (value == null || value === '') return ''
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  return text.length > limit ? `${text.slice(0, limit)}...` : text
}
function componentSummary(widget = {}) {
  const config = widget.config || {}
  const dataBinding = [
    config.datasourceId ? `数据源=${config.datasourceId}` : '',
    config.tableName ? `数据表=${config.tableName}` : '',
    config.apiUrl ? `接口=${config.apiUrl}` : '',
    config.sql ? `SQL=${compactText(config.sql, 500)}` : ''
  ].filter(Boolean).join('；')

  return [
    `名称=${widget.componentName || widget.title || widget.id || '未命名'}`,
    `类型=${widget.component || widget.type || '未知'}`,
    `位置=(${widget.x ?? 0},${widget.y ?? 0})`,
    `尺寸=${widget.w ?? 0}x${widget.h ?? 0}`,
    dataBinding
  ].filter(Boolean).join('，')
}

function canvasSummary(page = {}, includeBackground = true) {
  const design = page.design || page.canvas || {}
  return [
    `宽=${design.width || 1920}`,
    `高=${design.height || 1080}`,
    includeBackground ? `背景=${page.backgroundColor || design.backgroundColor || '未设置'}` : ''
  ].filter(Boolean).join('，')
}

function surroundingSummary(widgets = [], selectedId) {
  return widgets
    .filter(widget => String(widget.id) !== String(selectedId || ''))
    .slice(0, COMPONENT_LIMIT)
    .map((widget, index) => `${index + 1}. ${componentSummary(widget)}`)
    .join('\n') || '无'
}

export function buildScreenAiRequirement({
  userPrompt = '',
  page = {},
  widgets = [],
  datasourceId,
  templateReference = '',
  templateId = '',
  generationSeed = ''
} = {}) {
  const templateMode = Boolean(templateId)
  const designMode = templateMode ? '指定模板，只生成 SQL' : '自动参考模板'
  const visualInstruction = templateMode
    ? [
        '只生成模板中数据组件的只读 SQL、数据源绑定和参数。',
        '完整保留模板画布尺寸、组件、位置、标题、单位、表头、配色及装饰；不得调整布局或重写样式。',
        '查询结果列名和数值单位必须匹配原模板，缺少真实数据或口径时说明具体缺失项，不能替换模板指标。'
      ]
    : [
        '视觉要求：自动参考可用模板的布局、组件分区、边框、字体和配色，结合本次真实业务数据生成。',
        '根据需求调整参考模板的指标和组件，覆盖全部业务板块，不把模板示例数值当作真实数据。'
      ]
  return [
    '请生成一张可直接使用的数据可视化大屏。',
    `用户需求：${userPrompt}`,
    `数据源ID：${datasourceId || '未选择'}`,
    `目标画布尺寸：${canvasSummary(page, false)}`,
    `生成模式：${designMode}`,
    `本次设计种子：${generationSeed || '由服务端生成'}`,
    templateMode ? `模板ID：${templateId}` : '',
    templateMode ? templateReference : '',
    '当前画布内容会整体替换，不把上一轮组件、坐标、样式、查询或背景作为本轮输入。',
    '内容优先级：权限和真实数据 > 用户业务需求 > 模板示例语义。无数据时明确说明，不伪造指标，也不掩盖真实的零值。',
    `数据绑定：使用${datasourceId ? '指定数据源 ' + datasourceId : '本次授权范围内适合的数据源'}真实表字段生成只读SQL；多数据源没有可靠关联键时分别展示。`,
    '文案一致性：页面标题、指标名、单位和图表语义必须与用户需求一致；图例、流程名称、明细列必须与真实查询结果逐项对应。',
    ...visualInstruction
  ].filter(Boolean).join('\n')
}

export function createScreenGenerationSeed() {
  generationSequence += 1
  const entropy = createClientId()
  return `design-${Date.now().toString(36)}-${generationSequence.toString(36)}-${entropy}`
}

export function buildScreenAiRequest({
  userPrompt = '',
  page = {},
  widgets = [],
  datasourceId,
  templateMode = 'auto',
  templateId = '',
  templateReference = '',
  generationSeed = ''
} = {}) {
  const resolvedTemplateId = String(templateId || '').trim()
  const useManualTemplate = templateMode === 'manual'
  const resolvedSeed = String(generationSeed || '').trim() || createScreenGenerationSeed()
  const design = page.design || page.canvas || {}

  return {
    datasourceId: datasourceId ? String(datasourceId) : null,
    templateMode: useManualTemplate ? 'manual' : 'auto',
    templateId: useManualTemplate ? resolvedTemplateId : null,
    generationSeed: resolvedSeed,
    theme: buildScreenAiRequirement({
      userPrompt,
      page,
      widgets,
      datasourceId,
      templateReference: useManualTemplate ? templateReference : '',
      templateId: useManualTemplate ? resolvedTemplateId : '',
      generationSeed: resolvedSeed
    }),
    width: Number(design.width || 1920),
    height: Number(design.height || 1080)
  }
}

export function buildWidgetAiRequirement({
  userPrompt = '',
  page = {},
  widgets = [],
  selectedWidget,
  datasourceId,
  generationSeed = ''
} = {}) {
  const resolvedSeed = String(generationSeed || '').trim() || createScreenGenerationSeed()
  return [
    '请生成一个可直接放入当前大屏的数据可视化组件。',
    `用户需求：${userPrompt}`,
    `数据源ID：${datasourceId || '未选择'}`,
    `本次组件设计种子：${resolvedSeed}`,
    `当前画布：${canvasSummary(page)}`,
    `当前选中组件：${selectedWidget ? componentSummary(selectedWidget) : '无，将创建新组件'}`,
    '周边组件：',
    surroundingSummary(widgets, selectedWidget?.id),
    '要求：使用真实表字段生成只读查询；背景、标题、网格、坐标轴、图例、提示框、系列形态、字体、配色和留白全部重新生成，不复用统一面板配方，并避免与周边组件重复。'
  ].join('\n')
}
