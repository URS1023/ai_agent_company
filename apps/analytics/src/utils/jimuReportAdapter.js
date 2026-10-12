/**
 * jimuReportAdapter —— 报表后端内容与"大屏页面/组件模型"之间的双向适配层。
 *
 * 位置：JimuBigScreenReplica 打开（toJimuScreen）与保存（toReportContent）报表时调用，
 * 是前后端报表结构的唯一边界。
 * 依赖：`data/jimuScreenPage.json`（页面种子默认值）、`utils/reportWidgetData.resolveGeneratedDataBinding`、
 * `utils/widgetSizing.resolveGeneratedWidgetSize`、`utils/widgetText`（文本组件归一化）。
 * 关键约束：
 * - 模板锁：来源模板是不可变的视觉文档（后端对应 modules/reports/template_lock.py）。
 *   锁定状态下只能持久化数据源/SQL/查询与运行结果，因此 LOCKED_*_RUNTIME_KEYS 三个集合
 *   必须与后端 template_lock.py 保持一致 —— 前端不得改写被锁字段（视觉样式、结构），
 *   也不得擅自扩大可写范围；
 * - preserveSourceWidgets（或 canvas.styleLocked）时组件对象按原样保留（不改结构、不重新归一化），
 *   但设计器仍允许移动/缩放并保存；
 * - ambient 视觉层是画布装饰而非可移动组件：运行时克隆需按设计尺寸铺满（来源常按预览像素存储），
 *   sourceContent 保持不动，锁定模板仍从该原始契约持久化；
 * - 所有函数返回新对象（clone），不得就地修改入参。
 */
import pageSeed from '@/data/jimuScreenPage.json'
import { resolveGeneratedDataBinding } from './reportWidgetData'
import { resolveGeneratedWidgetSize } from './widgetSizing'
import { isTextWidget, normalizeTextConfig } from './widgetText'
import { snapshotScopeContext } from './snapshotWidgetLayout'

const clone = value => JSON.parse(JSON.stringify(value))
const own = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key)

/**
 * 受模板锁保护的运行期字段白名单，必须与后端 template_lock.py 一致。
 * 前端只允许持久化这些字段，其余（视觉/结构）一律不得改写。
 */

// Keep this boundary aligned with backend/app/modules/reports/template_lock.py.
// Source templates are immutable visual documents: the designer may only
// persist datasource/query bindings and verified runtime results.
const LOCKED_WIDGET_RUNTIME_KEYS = new Set([
  'data', 'value', 'rawData', 'datasourceId', 'datasourceIds', 'query',
  'generatedSql', 'queryId', 'verifiedAt', 'dataStatus', 'dataLoading',
  'dataError', 'lineage'
])
const LOCKED_CONFIG_RUNTIME_KEYS = new Set([
  'datasourceId', 'datasourceIds', 'sql', 'query', 'generatedSql', 'parameters',
  'queryId', 'verifiedAt', 'dataStatus', 'dataLoading', 'dataError', 'dataType',
  'chartData', 'chartDataParsed', 'rawData', 'datasetId', 'fromDatasetId',
  'headers', 'body', 'apiUrl', 'sourceBinding', 'dataEntity', 'dsName',
  'dsEnabled', 'setDimensional', 'setChartFilter', 'lineage', 'referenceSample', 'dataMapping', 'resultColumns'
])
const LOCKED_DRILLDOWN_RUNTIME_KEYS = new Set([
  'datasourceId', 'sql', 'query', 'generatedSql', 'parameters', 'queryId',
  'verifiedAt', 'dataStatus', 'dataLoading', 'dataError', 'headers', 'body', 'apiUrl'
])

function copyRuntimeKeys(target, current, keys) {
  for (const key of keys) {
    if (own(current, key) && current[key] !== undefined) target[key] = clone(current[key])
    else delete target[key]
  }
}

function hasRuntimeKeys(value, keys) {
  return Boolean(value && typeof value === 'object' && [...keys].some(
    key => own(value, key) && value[key] !== undefined
  ))
}

function mergeDrilldownRuntime(base, current) {
  const merged = clone(base && typeof base === 'object' ? base : {})
  copyRuntimeKeys(merged, current && typeof current === 'object' ? current : {}, LOCKED_DRILLDOWN_RUNTIME_KEYS)
  return merged
}

function mergeLockedWidgetRuntime(base, current = {}) {
  const merged = clone(base)
  copyRuntimeKeys(merged, current, LOCKED_WIDGET_RUNTIME_KEYS)

  if (own(base, 'drilldown') || hasRuntimeKeys(current.drilldown, LOCKED_DRILLDOWN_RUNTIME_KEYS)) {
    merged.drilldown = mergeDrilldownRuntime(base.drilldown, current.drilldown)
  }

  const baseConfig = base.config && typeof base.config === 'object' ? base.config : {}
  const currentConfig = current.config && typeof current.config === 'object' ? current.config : {}
  const config = clone(baseConfig)
  copyRuntimeKeys(config, currentConfig, LOCKED_CONFIG_RUNTIME_KEYS)
  if (own(baseConfig, 'drilldown') || hasRuntimeKeys(currentConfig.drilldown, LOCKED_DRILLDOWN_RUNTIME_KEYS)) {
    config.drilldown = mergeDrilldownRuntime(baseConfig.drilldown, currentConfig.drilldown)
  }
  merged.config = config
  return merged
}

/**
 * 旧版报表组件类型 → 大屏组件名的兼容映射（历史报表必须仍能打开）。
 * 与 chartComponentMap 一起构成组件名解析链，任一环缺失都会让老报表退化成文本组件。
 */
const legacyComponentMap = {
  chart: 'JBar',
  metric: 'JStatsSummary',
  table: 'JScrollBoard',
  title: 'JText',
  text: 'JText',
  time: 'JCurrentTime',
  status: 'JSystemStatus',
  image: 'JDragImg',
  decoration: 'JDragBorder',
  border: 'JDragBorder'
}

const chartComponentMap = {
  bar: 'JBar',
  line: 'JLine',
  area: 'JArea',
  pie: 'JPie',
  rose: 'JRose',
  horizontalbar: 'JHorizontalBar',
  capsule: 'JCapsuleChart',
  scatter: 'JScatter',
  radar: 'JRadar',
  gauge: 'JGauge',
  funnel: 'JFunnel',
  treemap: 'JRectangleTree',
  progress: 'JRoundProgress',
  map: 'JAreaMap',
  table: 'JScrollBoard'
}

/**
 * 把报表后端的一个组件转换成大屏 widget。
 * 组件名兼容三种来源：已归一化的 component、按图表类型映射（chartComponentMap）、
 * 以及旧版 type 映射（legacyComponentMap）；全部无法识别时退化为 JText，保证内容不丢。
 * @param {object} widget 后端组件
 * @param {number} index 组件序号，用于生成默认名称
 * @returns {object} 大屏 widget（新对象）
 */
export function toJimuWidget(widget = {}, index = 0) {
  const position = widget.position || {}
  const style = widget.style || {}
  const component = widget.component
    || (widget.type === 'chart' ? chartComponentMap[String(widget.chartType || '').toLowerCase()] : '')
    || legacyComponentMap[widget.type]
    || 'JText'
  const title = widget.componentName || widget.title || widget.label || widget.content || `组件${index + 1}`
  const metricData = component === 'JStatsSummary' && widget.value != null
    ? [{ name: title, value: widget.value, suffix: widget.unit || '' }]
    : []
  const chartData = widget.config?.chartDataParsed
    ?? widget.config?.chartData
    ?? widget.data
    ?? widget.echartsOption?.dataset?.source
    ?? metricData
  const normalizedConfig = normalizeTextConfig({ ...widget, component })
  const option = normalizedConfig.option || widget.echartsOption || {}
  const dataBinding = resolveGeneratedDataBinding(widget)
  const size = resolveGeneratedWidgetSize(component, widget, position)
  const textOption = isTextWidget({ component }) ? {
    ...option,
    body: {
      ...(option.body || {}),
      text: option.body?.text ?? title,
      color: option.body?.color ?? style.color ?? '#d0e0f0',
      fontSize: option.body?.fontSize ?? style.fontSize ?? 28,
      fontFamily: option.body?.fontFamily ?? style.fontFamily,
      fontWeight: option.body?.fontWeight ?? style.fontWeight,
      letterSpacing: option.body?.letterSpacing ?? style.letterSpacing,
      textAlign: option.body?.textAlign ?? style.textAlign,
      lineHeight: option.body?.lineHeight ?? style.lineHeight,
      textShadow: option.body?.textShadow ?? style.textShadow
    }
  } : option
  const themedOption = {
    ...textOption,
    mainColor: textOption.mainColor || style.mainColor,
    subColor: textOption.subColor || style.subColor
  }
  return {
    ...widget,
    id: String(widget.id || `jimu-${Date.now()}-${index}`),
    i: String(widget.i || widget.id || `jimu-${Date.now()}-${index}`),
    component,
    componentName: title,
    x: widget.x ?? position.left ?? 0,
    y: widget.y ?? position.top ?? 0,
    w: size.w,
    h: size.h,
    orderNum: widget.orderNum ?? position.zIndex ?? index,
    visible: widget.visible ?? true,
    locked: widget.locked ?? false,
    rotation: widget.rotation ?? 0,
    config: {
      dataType: dataBinding.dataType,
      background: widget.config?.background || style.background || '#FFFFFF00',
      borderColor: widget.config?.borderColor || style.borderColor || '#FFFFFF00',
      ...normalizedConfig,
      datasourceId: dataBinding.datasourceId,
      sql: dataBinding.sql,
      drilldown: widget.drilldown || widget.config?.drilldown,
      chartData: typeof chartData === 'string' ? chartData : JSON.stringify(chartData),
      chartDataParsed: typeof chartData === 'string' ? undefined : chartData,
      option: component === 'JDragBorder'
        ? {
            type: widget.variant === 'line' ? '5' : (widget.variant === 'b' ? '12' : '7'),
            mainColor: style.mainColor || themedOption.mainColor,
            subColor: style.subColor || themedOption.subColor,
            ...themedOption
          }
        : themedOption
    }
  }
}

/**
 * 把报表内容（对象或 JSON 字符串）转换成设计器使用的页面模型。
 * 缺省字段一律用页面种子补齐，并把原始内容同时保存在 sourceCanvas / sourceContent 中，
 * 供保存时按模板锁规则回写；解析失败时退化为空对象而不是抛错（老数据可能不是合法 JSON）。
 * @param {object|string} content 报表内容
 * @param {{ preserveSourceWidgets?: boolean, snapshotDescription?: string }} options 源组件保留策略与实际报表口径说明
 * @returns {object} 大屏页面模型
 */
export function toJimuScreen(content = {}, options = {}) {
  let source = content
  if (typeof content === 'string') {
    try {
      source = JSON.parse(content || '{}')
    } catch {
      source = {}
    }
  }
  const page = clone(pageSeed)
  const canvas = source?.canvas || source?.design || {}
  const preserveSourceWidgets = Boolean(options.preserveSourceWidgets)
  page.sourceCanvas = clone(canvas)
  page.sourceContent = clone(source || {})
  page.preserveSourceWidgets = preserveSourceWidgets
  page.design = { ...page.design, ...(canvas.design || {}) }
  page.refreshSeconds = canvas.refreshSeconds ?? 0
  page.design.width = canvas.width || page.design.width
  page.design.height = canvas.height || page.design.height
  page.backgroundColor = source?.backgroundColor || canvas.backgroundColor || page.backgroundColor
  page.backgroundImage = Object.prototype.hasOwnProperty.call(source, 'backgroundImage')
    ? source.backgroundImage
    : (Object.prototype.hasOwnProperty.call(canvas, 'backgroundImage') ? canvas.backgroundImage : page.backgroundImage)
  page.backgroundLayers = source?.backgroundLayers || canvas.backgroundLayers || ''
  page.backgroundBlendMode = source?.backgroundBlendMode || canvas.backgroundBlendMode || ''
  page.themeId = source?.themeId || canvas.themeId || page.themeId || ''
  page.theme = source?.theme || canvas.theme || page.theme || null
  page.templateId = source?.templateId || canvas.templateId || ''
  page.visualBlueprint = source?.visualBlueprint || canvas.visualBlueprint || ''
  page.heroAsset = source?.heroAsset || canvas.heroAsset || ''
  page.visualMode = source?.visualMode || canvas.visualMode || ''
  // Direct source imports already use the normalized widget contract. Preserve
  // those objects byte-for-byte at load time without conflating preservation
  // with edit locking; the designer may still move, resize and save them.
  page.widgets = preserveSourceWidgets || canvas.styleLocked
    ? clone(source?.widgets || [])
    : (source?.widgets || []).map(toJimuWidget)
  page.widgets.forEach(widget => {
    if (!widget.theme && page.theme) widget.theme = clone(page.theme)
    if (!widget.themeId && page.themeId) widget.themeId = page.themeId
  })
  const savedQueries = page.widgets.filter(widget => widget.config?.snapshotOnly && widget.config?.queryId)
  if (savedQueries.length) {
    const capturedAt = savedQueries.map(widget => widget.config?.verifiedAt || '').filter(Boolean).sort().at(-1) || ''
    const scope = snapshotScopeContext(page.widgets, options.snapshotDescription)
    // 字号按预览宽度适配，纵向留白仍受画布高度约束，避免宽矮布局挤掉指标与时间正文。
    const fontScale = page.design.width / 652
    const layoutScale = Math.min(fontScale, page.design.height / 368)
    page.widgets.forEach(widget => { widget.snapshotContext = { capturedAt, layoutScale, fontScale, ...scope } })
  }
  // Source reports store several full-screen ambient layers in preview pixels
  // (for example 450x253 on a 929x523 canvas).  The layer is semantic canvas
  // chrome, not a movable business widget, so normalize the runtime clone for
  // every load path.  sourceContent remains untouched and locked templates are
  // still persisted from that source contract by toReportContent().
  page.widgets.forEach(widget => {
    if (widget.visualRole === 'ambient' && Number(widget.x || 0) === 0 && Number(widget.y || 0) === 0) {
      widget.w = page.design.width
      widget.h = page.design.height
    }
  })
  return page
}

/**
 * Apply the persisted report identity to template title placeholders. Source
 * templates often keep this slot empty until a report is saved; leaving the
 * empty value in place makes an otherwise complete dashboard look unfinished.
 */
/**
 * 把报表名称写入模板的标题占位组件。
 * 仅在未保留源组件、且标题组件存在、且用户未手动编辑过标题（config.textEdited）时替换，
 * 避免覆盖用户自定义标题；找不到标题组件时原样返回。
 * @returns 同一个 page 对象（就地设置 page.name 与标题文本）
 */
export function applyReportIdentity(page, reportName = '') {
  const name = String(reportName || page?.name || '').trim()
  if (!page || !name) return page
  page.name = name
  if (page.preserveSourceWidgets) return page
  const titleWidget = (page.widgets || []).find(widget => (
    widget.visualRole === 'dashboard-title'
    || widget.modelKey === 'dashboard-title'
    || (widget.component === 'JText' && /(?:主标题|标题占位|dashboard[-_ ]?title)/i.test(String(widget.componentName || '')))
    || (widget.component === 'JText' && String(widget.id || '').toLowerCase().endsWith('-title'))
    || (widget.component === 'JText' && String(widget.id || '').toLowerCase().startsWith('title-'))
    || (widget.component === 'JText' && String(widget.id || '').toLowerCase().includes('title') && Number(widget.y || 0) < 100)
    || (widget.component === 'JText' && Number(widget.y || 0) < 100 && Number(widget.w || 0) >= 500)
  ))
  if (!titleWidget) return page
  if (titleWidget.config?.textEdited) return page
  const body = titleWidget.config?.option?.body || {}
  titleWidget.config = titleWidget.config || {}
  titleWidget.config.option = titleWidget.config.option || {}
  titleWidget.config.option.body = { ...body, text: name }
  titleWidget.componentName = name
  titleWidget.title = name
  return page
}

/**
 * 把设计器页面模型写回报表内容（保存路径）。
 * 模板锁定且未要求编辑模板布局时，以 sourceContent 的组件为基准，只回填允许的运行期字段
 * （数据源/SQL/查询/运行结果），视觉结构保持来源模板原样；否则写出当前编辑结果。
 * @param {object} page 大屏页面模型
 * @param {{ editTemplateLayout?: boolean }} options editTemplateLayout 为真表示用户显式编辑模板布局（需更高权限）
 * @returns {object} 待提交给后端的报表内容
 */
export function toReportContent(page, { editTemplateLayout = false } = {}) {
  if (!editTemplateLayout && (page.templateStyleLocked || page.sourceCanvas?.styleLocked)) {
    const sourceWidgets = page.sourceContent?.widgets || []
    const currentWidgets = new Map((page.widgets || []).map(widget => [String(widget.id), widget]))
    return {
      ...(page.sourceContent || {}),
      canvas: clone(page.sourceCanvas),
      widgets: sourceWidgets.map(widget => mergeLockedWidgetRuntime(
        widget,
        currentWidgets.get(String(widget.id)) || {}
      ))
    }
  }
  return {
    ...(page.sourceContent || {}),
    canvas: {
      ...(page.sourceCanvas || {}),
      ...(editTemplateLayout ? { styleLocked: false } : {}),
      design: clone(page.design),
      refreshSeconds: page.refreshSeconds || 0,
      width: page.design.width,
      height: page.design.height,
      backgroundColor: page.backgroundColor,
      backgroundImage: page.backgroundImage,
      backgroundLayers: page.backgroundLayers || '',
      backgroundBlendMode: page.backgroundBlendMode || '',
      themeId: page.themeId || '',
      theme: page.theme || null,
      templateId: page.templateId || '',
      visualBlueprint: page.visualBlueprint || '',
      heroAsset: page.heroAsset || '',
      visualMode: page.visualMode || ''
    },
    widgets: page.widgets
  }
}
