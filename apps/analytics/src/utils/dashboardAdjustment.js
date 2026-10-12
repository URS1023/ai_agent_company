/**
 * dashboardAdjustment —— 解析"整张大屏换成绿色科技风"这类自然语言调整指令，并落到页面/组件样式。
 *
 * 位置：JimuBigScreenReplica 的 AI 调整流程（AI_ADJUSTMENT_EXAMPLES 即为本模块的输入示例）。
 * 关键约束：
 * - 主题预设（THEME_PRESETS）是配色与样式的唯一来源，解析结果只能引用预设，
 *   不接受模型返回的任意色值直接写入组件（避免出现不可控/低对比度配色）；
 * - 解析必须容错：无法识别的指令返回空调整（不抛错），由调用方决定提示"未识别"；
 * - 应用调整时只改视觉属性（主题/圆角/发光等），不得改动数据绑定、SQL 或组件结构。
 */
const THEME_PRESETS = Object.freeze({
  green: {
    id: 'emerald-matrix',
    primary: '#48ff9a',
    secondary: '#00d7a7',
    accent: '#b8ff5a',
    danger: '#ff667d',
    panel: '#06231ccc',
    panelStrong: '#08352add',
    grid: '#17634f',
    text: '#effff8',
    muted: '#82b9a4',
    background: '#020d0a',
    palette: ['#48ff9a', '#00d7a7', '#b8ff5a', '#30b9ff', '#ffd166', '#ff667d', '#7df5d0', '#9a8cff']
  },
  blue: {
    id: 'sales-electric',
    primary: '#21e7ff',
    secondary: '#3478ff',
    accent: '#8b7cff',
    danger: '#ff6577',
    panel: '#06182bcc',
    panelStrong: '#08213bdd',
    grid: '#164766',
    text: '#e9faff',
    muted: '#7ca3b8',
    background: '#030b18',
    palette: ['#21e7ff', '#3478ff', '#8b7cff', '#55f991', '#ff6577', '#29c7a2', '#e66fff', '#7dd8ff']
  },
  purple: {
    id: 'violet-nebula',
    primary: '#b788ff',
    secondary: '#6f72ff',
    accent: '#ff71ce',
    danger: '#ff6577',
    panel: '#17102dcc',
    panelStrong: '#241744dd',
    grid: '#533f78',
    text: '#f8f1ff',
    muted: '#ac9cc2',
    background: '#090615',
    palette: ['#b788ff', '#6f72ff', '#ff71ce', '#51e5ff', '#ffd166', '#ff6577', '#7af7c7', '#8ea1ff']
  },
  orange: {
    id: 'amber-command',
    primary: '#ffbf4b',
    secondary: '#ff7a45',
    accent: '#5df0c0',
    danger: '#ff5d72',
    panel: '#241809cc',
    panelStrong: '#38230bdd',
    grid: '#735126',
    text: '#fff8e9',
    muted: '#c2a77f',
    background: '#100a03',
    palette: ['#ffbf4b', '#ff7a45', '#5df0c0', '#5bbcff', '#ff5d72', '#f4e26b', '#bc8cff', '#7de0ff']
  },
  red: {
    id: 'crimson-alert',
    primary: '#ff6577',
    secondary: '#ff3f8f',
    accent: '#ffb657',
    danger: '#ff334f',
    panel: '#2a0b16cc',
    panelStrong: '#3c1020dd',
    grid: '#733044',
    text: '#fff1f4',
    muted: '#c69aa5',
    background: '#110309',
    palette: ['#ff6577', '#ff3f8f', '#ffb657', '#65dfff', '#9b88ff', '#51efad', '#ffdf69', '#ff91a0']
  }
})

const COLOR_PATTERNS = [
  [/绿|emerald|green/i, 'green'],
  [/紫|violet|purple/i, 'purple'],
  [/橙|琥珀|amber|orange/i, 'orange'],
  [/红|crimson|red/i, 'red'],
  [/蓝|青|cyan|blue/i, 'blue']
]

const CHART_TYPES = [
  [/面积图|area/i, { component: 'JArea', seriesType: 'line', area: true }],
  [/折线图|趋势线|line/i, { component: 'JLine', seriesType: 'line' }],
  [/柱状图|条形图|bar/i, { component: 'JBar', seriesType: 'bar' }],
  [/饼图|环形图|pie|ring/i, { component: 'JPie', seriesType: 'pie' }],
  [/雷达图|radar/i, { component: 'JRadar', seriesType: 'radar' }]
]

const DATA_SEMANTIC = /(?:口径|sql|字段|查询|统计|聚合|求和|平均|最大|最小|同比|环比|按(?:天|日|周|月|年)|数据源|表关联|明细条件)/i
const GLOBAL_SCOPE = /(?:整体|全局|整页|整张|大屏|页面|所有|全部)/i
const COMPONENT_SCOPE = /(?:当前|选中|这个|该).{0,4}(?:组件|图表|指标卡)|(?:组件|图表|指标卡).{0,4}(?:当前|选中|这个|该)|组件样式/i
const STYLE_SIGNAL = /(?:颜色|色调|主题|风格|样式|绿色|蓝色|青色|紫色|橙色|红色|发光|更亮|玻璃|简洁|紧凑|圆角|折线图|面积图|柱状图|条形图|饼图|环形图|雷达图|换一个)/i

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function detectTheme(text) {
  const match = COLOR_PATTERNS.find(([pattern]) => pattern.test(text))
  return match ? clone(THEME_PRESETS[match[1]]) : null
}

function chartFromFragment(fragment = '') {
  for (const [pattern, chart] of CHART_TYPES) {
    if (pattern.test(fragment)) return { ...chart }
  }
  return null
}

function detectChart(text) {
  const prefixedReplacement = text.match(/(?:用|以)\s*([^，。；]+?)\s*(?:替换|取代)/i)
  const suffixedReplacement = text.match(/(?:换成|改成|变成|转换为|替换为|切换为)\s*([^，。；]*)/i)
  const explicitTarget = chartFromFragment(prefixedReplacement?.[1] || suffixedReplacement?.[1])
  if (explicitTarget) return explicitTarget

  let target = null
  for (const [pattern, chart] of CHART_TYPES) {
    const match = pattern.exec(text)
    if (match && (!target || match.index >= target.index)) {
      target = { index: match.index, chart }
    }
  }
  return target ? { ...target.chart } : null
}

function isDecoration(widget = {}) {
  return widget.type === 'decoration' || /JDragBorder|JDragDecoration/i.test(widget.component || '')
}

function isChartWidget(widget = {}) {
  return widget.type === 'chart'
    || /^(?:JArea|JBar|JBasic|JBi|JCapsule|JColumn|JDynamic|JEarth|JFunnel|JGauge|JHistogram|JHorizontal|JLine|JMap|JMix|JMultiple|JNegative|JPercent|JPictorial|JPie|JPyramid|JRadar|JRectangle|JRing|JRose|JScatter|JStack)/i.test(widget.component || '')
    || Array.isArray(widget.config?.option?.series)
}

function chartDimensions(widget, option) {
  const configured = Array.isArray(option.dataset?.dimensions)
    ? option.dataset.dimensions.filter(Boolean)
    : []
  if (configured.length) return configured
  const firstRow = Array.isArray(widget.config?.chartDataParsed)
    ? widget.config.chartDataParsed.find(row => row && typeof row === 'object' && !Array.isArray(row))
    : null
  return firstRow ? Object.keys(firstRow) : []
}

function removeSeriesFields(series, fields) {
  fields.forEach(field => delete series[field])
  return series
}

function withoutExplicitSeriesColors(series = {}) {
  const next = clone(series)
  for (const field of ['itemStyle', 'lineStyle', 'areaStyle']) {
    if (next[field] && typeof next[field] === 'object') {
      delete next[field].color
    }
  }
  if (next.emphasis?.itemStyle) {
    delete next.emphasis.itemStyle.color
  }
  return next
}

function patchChartTheme(widget, theme) {
  if (!theme || !isChartWidget(widget)) return
  widget.config ||= {}
  const option = widget.config.option || (widget.config.option = {})
  option.color = clone(theme.palette)
  if (Array.isArray(option.series)) {
    option.series = option.series.map(withoutExplicitSeriesColors)
  }
  const backup = widget.config.chartConversionBackup
  if (Array.isArray(backup?.series)) {
    backup.series = backup.series.map(withoutExplicitSeriesColors)
  }
}

function rememberCartesianState(widget, option, sourceSeries) {
  const cartesian = Boolean(option.xAxis || option.yAxis)
    || sourceSeries.some(series => /^(?:line|bar)$/i.test(series.type || ''))
  if (!cartesian) return
  const backup = { series: clone(sourceSeries) }
  for (const field of ['xAxis', 'yAxis', 'grid']) {
    if (option[field] != null) backup[field] = clone(option[field])
  }
  widget.config.chartConversionBackup = backup
}

function patchChart(widget, chart) {
  if (!chart || !isChartWidget(widget)) return
  widget.config ||= {}
  const option = widget.config.option || (widget.config.option = {})
  const dimensions = chartDimensions(widget, option)
  const sourceSeries = Array.isArray(option.series) && option.series.length
    ? option.series
    : [{}]
  widget.component = chart.component
  widget.chartType = chart.seriesType

  if (chart.seriesType === 'pie') {
    rememberCartesianState(widget, option, sourceSeries)
    const labelField = dimensions[0] || 'name'
    const valueField = dimensions[1] || dimensions[0] || 'value'
    const next = removeSeriesFields(
      { ...sourceSeries[0], type: 'pie' },
      ['areaStyle', 'smooth', 'showSymbol', 'symbol', 'xAxisIndex', 'yAxisIndex', 'stack', 'data']
    )
    next.encode = {
      itemName: labelField,
      value: valueField,
      tooltip: dimensions.length > 1 ? dimensions.slice(1) : [valueField]
    }
    option.series = [next]
    delete option.xAxis
    delete option.yAxis
    delete option.grid
    delete option.radar
    return
  }

  if (chart.seriesType === 'radar') {
    rememberCartesianState(widget, option, sourceSeries)
    const next = removeSeriesFields(
      { ...sourceSeries[0], type: 'radar' },
      ['encode', 'radius', 'center', 'roseType', 'xAxisIndex', 'yAxisIndex', 'stack', 'data']
    )
    option.series = [next]
    delete option.xAxis
    delete option.yAxis
    delete option.grid
    return
  }

  const backup = widget.config.chartConversionBackup
  const cartesianSeries = Array.isArray(backup?.series) && backup.series.length
    ? clone(backup.series)
    : sourceSeries
  delete option.radar
  option.xAxis = backup?.xAxis != null ? clone(backup.xAxis) : (option.xAxis || { type: 'category' })
  option.yAxis = backup?.yAxis != null ? clone(backup.yAxis) : (option.yAxis || { type: 'value' })
  option.grid = backup?.grid != null
    ? clone(backup.grid)
    : (option.grid || { top: 46, right: 24, bottom: 24, left: 30, containLabel: true })
  option.series = cartesianSeries.map((series, index) => {
    const next = removeSeriesFields(
      { ...series, type: chart.seriesType },
      ['radius', 'center', 'roseType', 'data']
    )
    if (dimensions.length > 1) {
      next.encode = {
        x: dimensions[0],
        y: dimensions[Math.min(index + 1, dimensions.length - 1)],
        tooltip: dimensions.slice(1)
      }
    } else {
      delete next.encode
    }
    if (chart.area) next.areaStyle = next.areaStyle || {}
    else delete next.areaStyle
    return next
  })
  delete widget.config.chartConversionBackup
}

function patchVisualStyle(widget, adjustment) {
  widget.style ||= {}
  if (adjustment.glow) {
    const signal = adjustment.theme?.primary || widget.theme?.primary || '#21e7ff'
    widget.style.glow = signal
    widget.style.shadow = `0 0 32px ${signal}42, inset 0 0 22px ${signal}14`
  }
  if (adjustment.rounded) widget.style.radius = Math.max(10, Number(widget.style.radius || 0))
  if (adjustment.panelVariant) widget.panelVariant = adjustment.panelVariant
  if (adjustment.compact) {
    widget.style.density = 'compact'
    widget.style.padding = 8
  }
}

export function parseDashboardAdjustment(text = '', context = {}) {
  const original = String(text || '').trim()
  if (!original) return { handled: false, reason: 'empty' }
  // Only affirmative clauses may drive destructive, auto-saved style changes.
  // Keep a following positive clause ("不要改成饼图，只调整圆角").
  const prompt = original.split(/[，,。；;！!？?\n]|但是|而是|但|\bbut\b/i)
    .filter(clause => !/(?:不要|不需要|不用|无需|不能|禁止|别|不改|不换|不变|不调整|保持|保留|勿|do\s+not|don't|never|keep\s+)/i.test(clause))
    .join('，')
  if (!prompt.trim()) return { handled: false, reason: 'no-affirmative-adjustment' }
  if (DATA_SEMANTIC.test(prompt)) return { handled: false, reason: 'data-semantic' }
  if (!STYLE_SIGNAL.test(prompt)) return { handled: false, reason: 'unrecognized' }

  const selectedId = context.selectedId && context.widgets?.some(widget => String(widget.id) === String(context.selectedId))
    ? String(context.selectedId)
    : ''
  const theme = detectTheme(prompt)
  const chart = detectChart(prompt)
  const scope = GLOBAL_SCOPE.test(prompt) || !selectedId
    ? 'global'
    : (COMPONENT_SCOPE.test(prompt) || chart || !theme ? 'selected' : 'global')
  const glow = /发光|更亮|炫酷|霓虹|科技/i.test(prompt)
  const rounded = /圆角|圆一点/i.test(prompt)
  const compact = /紧凑|缩小间距/i.test(prompt)
  const glass = /玻璃|全息/i.test(prompt)
  const minimal = /简洁|极简/i.test(prompt)
  const selectedWidget = selectedId
    ? context.widgets?.find(widget => String(widget.id) === selectedId)
    : null
  const cyclePanel = /(?:换|更换)(?:一个|一种|个|种)?.{0,4}样式|样式.{0,4}(?:换|更换)(?:一个|一种|个|种)?/i.test(prompt)
  const panelVariants = ['glass-hologram', 'neon-command', 'minimal-signal']
  const currentPanelIndex = panelVariants.indexOf(selectedWidget?.panelVariant)
  const cycledPanel = panelVariants[(currentPanelIndex + 1) % panelVariants.length]
  const panelVariant = glass
    ? 'glass-hologram'
    : (minimal ? 'minimal-signal' : (glow ? 'neon-command' : (cyclePanel ? cycledPanel : '')))
  if (chart && scope === 'selected' && selectedWidget && !isChartWidget(selectedWidget)
      && !theme && !glow && !rounded && !compact && !panelVariant) {
    return { handled: false, reason: 'incompatible-chart' }
  }

  return {
    handled: Boolean(theme || chart || glow || rounded || compact || panelVariant),
    scope,
    selectedId,
    theme,
    chart,
    glow,
    rounded,
    compact,
    panelVariant,
    summary: [
      scope === 'global' ? '已调整整张大屏' : '已调整当前组件',
      theme ? `${theme.id} 主题` : '',
      chart ? chart.component.replace(/^J/, '') : '',
      panelVariant ? '视觉样式' : ''
    ].filter(Boolean).join(' · ')
  }
}

export function applyDashboardAdjustment(page = {}, widgets = [], adjustment = {}) {
  const nextPage = clone(page)
  const nextWidgets = clone(widgets)
  if (!adjustment?.handled) return { page: nextPage, widgets: nextWidgets }

  if (adjustment.scope === 'global' && adjustment.theme) {
    nextPage.themeId = adjustment.theme.id
    nextPage.theme = clone(adjustment.theme)
    nextPage.backgroundColor = adjustment.theme.background
    nextPage.backgroundImage = ''
  }

  for (const widget of nextWidgets) {
    const decoration = isDecoration(widget)
    const targeted = adjustment.scope === 'global'
      ? true
      : String(widget.id) === String(adjustment.selectedId)
    if (!targeted) continue
    if (adjustment.theme) {
      widget.themeId = adjustment.theme.id
      widget.theme = clone(adjustment.theme)
      widget.style ||= {}
      widget.style.borderColor = adjustment.theme.grid
      if (decoration) {
        widget.config ||= {}
        widget.config.option ||= {}
        widget.config.option.mainColor = adjustment.theme.primary
        widget.config.option.subColor = adjustment.theme.secondary
        widget.style.mainColor = adjustment.theme.primary
        widget.style.subColor = adjustment.theme.secondary
      }
      if (!decoration) patchChartTheme(widget, adjustment.theme)
    }
    if (!decoration) patchChart(widget, adjustment.chart)
    patchVisualStyle(widget, adjustment)
  }

  return { page: nextPage, widgets: nextWidgets }
}

export { THEME_PRESETS }
