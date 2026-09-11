<script setup>
import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { normalizeDashboardTheme, shouldShowPanelChrome, withAlpha } from '@/utils/dashboardTheme'
import {
  inferSeriesSchema,
  normalizeSeriesRows,
  sanitizeChartOption,
  toMapSeriesData
} from '@/utils/dashboardData'
import { buildAiAuthoritativeChartOption } from '@/utils/aiChartOption'

const props = defineProps({
  widget: { type: Object, required: true },
  data: { type: [Array, Object], default: null }
})
const emit = defineEmits(['data-click'])

const chartEl = ref(null)
let chart = null
let resizeObserver = null
let chinaMapPromise = null
let latestRenderSequence = 0

function clone(value) {
  return JSON.parse(JSON.stringify(value || {}))
}

function chartData(widget) {
  if (Array.isArray(props.data)) return props.data
  const config = widget.config || {}
  if (Array.isArray(config.chartDataParsed)) return config.chartDataParsed
  if (Array.isArray(config.chartData)) return config.chartData
  if (typeof config.chartData === 'string' && config.chartData.trim()) {
    try {
      const parsed = JSON.parse(config.chartData)
      if (Array.isArray(parsed)) return parsed
    } catch {
      return []
    }
  }
  if (widget.visualMode === 'ai') return []
  return [
    { name: '一月', value: 36 },
    { name: '二月', value: 58 },
    { name: '三月', value: 42 },
    { name: '四月', value: 76 },
    { name: '五月', value: 64 }
  ]
}

function safeColor(value, fallback = '#5ba8f0') {
  if (!value || value === '#FFFFFF00') return fallback
  return value
}

function chartTitle(widget, sourceTitle, theme) {
  const source = typeof sourceTitle === 'string'
    ? { text: sourceTitle }
    : (sourceTitle && typeof sourceTitle === 'object' ? sourceTitle : {})
  const hasTopChrome = shouldShowPanelChrome(widget)
  return {
    ...source,
    show: source.show !== false,
    text: source.text || widget.componentName || '',
    left: source.left ?? 18,
    top: source.top ?? (hasTopChrome ? 37 : 14),
    textStyle: {
      color: theme.text,
      fontFamily: 'Bahnschrift, Microsoft YaHei UI, sans-serif',
      fontSize: widget.chromeMode === 'hero' ? 16 : 13,
      fontWeight: 600,
      letterSpacing: 1,
      ...(source.textStyle || {})
    },
    subtextStyle: {
      color: theme.muted,
      fontSize: 9,
      lineHeight: 16,
      ...(source.subtextStyle || {})
    }
  }
}

function polishGrid(grid, widget) {
  if (Array.isArray(grid)) return grid.map(item => polishGrid(item, widget))
  const source = grid || {}
  if (widget.visualMode === 'ai') return { containLabel: true, ...source }
  const hasTopChrome = shouldShowPanelChrome(widget)
  const minimumTop = hasTopChrome ? 82 : 54
  const configuredTop = Number(source.top)
  return {
    ...source,
    top: Number.isFinite(configuredTop) ? Math.max(minimumTop, configuredTop) : minimumTop,
    right: source.right ?? 22,
    bottom: source.bottom ?? 18,
    left: source.left ?? 18,
    containLabel: true,
  }
}

function polishLegend(legend, theme, widget = {}) {
  if (Array.isArray(legend)) return legend.map(item => polishLegend(item, theme, widget))
  const hasTopChrome = shouldShowPanelChrome(widget)
  return {
    ...(legend || {}),
    top: legend?.top ?? (hasTopChrome ? 43 : 17),
    right: legend?.right ?? 16,
    itemWidth: legend?.itemWidth ?? 13,
    itemHeight: legend?.itemHeight ?? 6,
    icon: legend?.icon || 'roundRect',
    textStyle: { color: theme.muted, fontSize: 9, ...(legend?.textStyle || {}) }
  }
}

function polishAxis(axis, theme) {
  if (Array.isArray(axis)) return axis.map(item => polishAxis(item, theme))
  if (!axis || typeof axis !== 'object') return axis
  return {
    ...axis,
    axisTick: { show: false, ...(axis.axisTick || {}) },
    axisLine: { show: false, lineStyle: { color: theme.grid, ...(axis.axisLine?.lineStyle || {}) }, ...(axis.axisLine || {}) },
    axisLabel: { color: theme.muted, fontSize: 10, margin: 12, hideOverlap: true, ...(axis.axisLabel || {}) },
    splitLine: { show: true, lineStyle: { color: theme.grid, opacity: .18, type: 'dashed', ...(axis.splitLine?.lineStyle || {}) }, ...(axis.splitLine || {}) }
  }
}

function polishSeries(series, index, theme, palette) {
  const current = clone(series)
  const signal = palette[index % palette.length]
  if (current.type === 'line') {
    current.smooth ??= true
    current.showSymbol ??= false
    current.symbolSize ??= 7
    current.lineStyle = {
      color: signal,
      width: 2.4,
      shadowBlur: 9,
      shadowColor: `${signal}66`,
      ...(current.lineStyle || {})
    }
    current.itemStyle = { color: theme.text, borderColor: signal, borderWidth: 2, ...(current.itemStyle || {}) }
    if (current.areaStyle) {
      current.areaStyle = {
        opacity: .42,
        color: current.areaStyle.color || new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: `${signal}aa` },
          { offset: .72, color: `${signal}20` },
          { offset: 1, color: `${signal}00` }
        ]),
        ...current.areaStyle
      }
    }
    current.emphasis = { focus: 'series', ...(current.emphasis || {}) }
  } else if (current.type === 'bar') {
    const explicitColor = current.itemStyle?.color
    const itemColors = Array.isArray(current.itemColors) ? current.itemColors : null
    delete current.itemColors
    current.barMaxWidth ??= 34
    current.itemStyle = {
      borderRadius: current.itemStyle?.borderRadius || [4, 4, 0, 0],
      shadowBlur: 8,
      shadowColor: `${signal}42`,
      ...(current.itemStyle || {}),
      color: explicitColor || (itemColors
        ? params => itemColors[params.dataIndex % itemColors.length]
        : new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: signal },
            { offset: .55, color: `${signal}cc` },
            { offset: 1, color: `${theme.secondary}66` }
          ]))
    }
    current.showBackground ??= true
    current.backgroundStyle = { color: `${theme.grid}22`, borderRadius: 4, ...(current.backgroundStyle || {}) }
  } else if (current.type === 'pie') {
    current.padAngle ??= 2
    current.itemStyle = { borderColor: theme.background, borderWidth: 2, ...(current.itemStyle || {}) }
    current.emphasis = { scaleSize: 7, itemStyle: { shadowBlur: 18, shadowColor: `${signal}77` }, ...(current.emphasis || {}) }
  } else if (current.type === 'radar') {
    current.symbolSize ??= 5
    current.lineStyle = { width: 2, shadowBlur: 8, shadowColor: `${signal}66`, ...(current.lineStyle || {}) }
    current.areaStyle = { opacity: .24, ...(current.areaStyle || {}) }
  }
  return current
}

function baseOption(widget) {
  const config = widget.config || {}
  const source = sanitizeChartOption(clone(config.option || {}))
  const data = chartData(widget)
  if (widget.visualMode === 'ai') {
    return buildAiAuthoritativeChartOption({ widget, source, data })
  }
  const normalizedRows = normalizeSeriesRows(data)
  const inferredSchema = inferSeriesSchema(data)
  const labels = normalizedRows.map(item => item.label)
  const values = normalizedRows.map(item => item.value)
  const firstValue = Number(values[0])
  const resolvedFirstValue = Number.isFinite(firstValue) ? firstValue : 0
  const type = widget.component || ''
  const theme = normalizeDashboardTheme(widget)
  const palette = source.color || theme.palette
  const usesSpecialRenderer = /Gauge|Radar|Funnel|Pyramid|Progress|Liquid|Rectangle|WordCloud|Ring|Pie|Rose|Doughnut|Radial|Scatter|Quadrant|Bubble/i.test(type)

  const common = {
    animationDuration: 1100,
    animationDurationUpdate: 720,
    animationEasing: 'cubicOut',
    animationEasingUpdate: 'cubicInOut',
    backgroundColor: source.backgroundColor ?? 'transparent',
    textStyle: { color: theme.text, fontFamily: 'Microsoft YaHei, Arial, sans-serif' },
    title: chartTitle(widget, source.title, theme),
    legend: polishLegend(source.legend, theme, widget),
    tooltip: {
      trigger: type.includes('Ring') || type.includes('Pie') ? 'item' : 'axis',
      backgroundColor: `${theme.background}ee`,
      borderColor: `${theme.primary}55`,
      borderWidth: 1,
      extraCssText: 'box-shadow:0 18px 48px rgba(0,0,0,.46);border-radius:12px;backdrop-filter:blur(12px);',
      textStyle: { color: theme.text, fontSize: 11 }
    }
  }

  // AI report widgets use ECharts dataset + encode. Keep that multi-series
  // contract instead of reducing every row to name/value.
  if (source.dataset && Array.isArray(source.series) && source.series.length
      && Array.isArray(data) && data.length && !usesSpecialRenderer) {
    return {
      ...common,
      ...source,
      color: palette,
      dataset: { ...source.dataset, source: data },
      animationDuration: source.animationDuration ?? 1100,
      animationEasing: source.animationEasing || 'cubicOut',
      backgroundColor: source.backgroundColor ?? 'transparent',
      title: chartTitle(widget, source.title, theme),
      legend: polishLegend(source.legend, theme, widget),
      textStyle: { ...common.textStyle, ...(source.textStyle || {}) },
      tooltip: { ...common.tooltip, ...(source.tooltip || {}) },
      grid: polishGrid(source.grid, widget),
      xAxis: polishAxis(source.xAxis, theme),
      yAxis: polishAxis(source.yAxis, theme),
      series: source.series.map((series, index) => polishSeries(
        /Area/i.test(type) && series.type === 'line' && !series.areaStyle
          ? { ...series, areaStyle: {} }
          : series,
        index,
        theme,
        palette
      ))
    }
  }

  if (/Gauge/i.test(type)) {
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      series: [{
        type: 'gauge',
        startAngle: type.includes('Semi') ? 180 : 220,
        endAngle: type.includes('Semi') ? 0 : -40,
        center: ['50%', type.includes('Semi') ? '68%' : '56%'],
        radius: type.includes('Semi') ? '78%' : '72%',
        progress: { show: true, width: 12, itemStyle: { color: palette[0] } },
        axisLine: { lineStyle: { width: 12, color: [[1, '#19324d']] } },
        axisTick: { show: false },
        splitLine: { length: 7, lineStyle: { color: '#5b7892', width: 1 } },
        axisLabel: { color: '#8ea7bc', distance: 18, fontSize: 9 },
        pointer: { show: !type.includes('Antv'), width: 3, itemStyle: { color: '#f7c948' } },
        anchor: { show: true, size: 7, itemStyle: { color: '#f7c948' } },
        detail: { valueAnimation: true, color: '#d8ecff', fontSize: 21, offsetCenter: [0, '42%'] },
        data: [{ value: resolvedFirstValue, name: labels[0] || '' }]
      }]
    }
  }

  if (/Radar/i.test(type)) {
    const radarCandidates = normalizedRows.slice(0, 8)
    const positiveRows = radarCandidates.filter(item => item.value > 0)
    const radarRows = positiveRows.length >= 3 ? positiveRows : radarCandidates
    const radarLabels = radarRows.map(item => item.label)
    const radarValues = radarRows.map(item => Math.abs(Number(item.value) || 0))
    const radarMax = Math.max(...radarValues, 1)
    const average = radarValues.length
      ? radarValues.reduce((sum, value) => sum + value, 0) / radarValues.length
      : 0
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      legend: {
        top: 34,
        right: 14,
        itemWidth: 10,
        itemHeight: 6,
        textStyle: { color: theme.muted, fontSize: 9 },
        data: ['实际分布', '均值基线']
      },
      radar: {
        ...(source.radar || {}),
        center: source.radar?.center || ['50%', '60%'],
        radius: source.radar?.radius || '57%',
        shape: source.radar?.shape || (type.includes('Circle') ? 'circle' : 'polygon'),
        indicator: radarValues.map((value, index) => ({
          name: radarLabels[index] || `${index + 1}`,
          max: Math.max(radarMax * 1.2, value * 1.35, 1)
        })),
        axisName: { color: theme.muted, fontSize: 9, ...(source.radar?.axisName || {}) },
        splitLine: { lineStyle: { color: `${theme.grid}aa`, ...(source.radar?.splitLine?.lineStyle || {}) } },
        splitArea: { areaStyle: { color: [withAlpha(theme.panelStrong, '88'), withAlpha(theme.background, '33')] } },
        axisLine: { lineStyle: { color: `${theme.primary}55` } }
      },
      series: [{
        ...(source.series?.[0] || {}),
        type: 'radar',
        symbolSize: 5,
        data: [
          {
            name: '实际分布',
            value: radarValues,
            lineStyle: { color: palette[0], width: 2.4, shadowBlur: 9, shadowColor: `${palette[0]}77` },
            itemStyle: { color: theme.text, borderColor: palette[0], borderWidth: 2 },
            areaStyle: { color: palette[0], opacity: .3 }
          },
          {
            name: '均值基线',
            value: radarValues.map(() => average),
            symbol: 'none',
            lineStyle: { color: theme.accent, width: 1, type: 'dashed', opacity: .78 },
            areaStyle: { color: theme.accent, opacity: .06 }
          }
        ]
      }]
    }
  }

  if (/Funnel|Pyramid/i.test(type)) {
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      series: [{
        type: 'funnel',
        top: 46,
        bottom: 12,
        left: '16%',
        width: '68%',
        sort: type.includes('Pyramid') ? 'ascending' : 'descending',
        gap: 2,
        label: { color: '#d8e8f4', fontSize: 9 },
        labelLine: { length: 8, lineStyle: { color: '#5d7d96' } },
        itemStyle: { borderColor: '#091b2b', borderWidth: 1 },
        data: data.map((item, index) => ({ name: labels[index], value: values[index] }))
      }]
    }
  }

  if (/Scatter|Quadrant|Bubble/i.test(type)) {
    const points = values.map((value, index) => [index * 18 + 14, value, type.includes('Bubble') ? value / 2 + 8 : 10])
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      grid: polishGrid(source.grid, widget),
      xAxis: polishAxis({ min: 0, max: 100 }, theme),
      yAxis: polishAxis({ min: 0, max: 100 }, theme),
      series: [{
        type: 'scatter',
        data: points,
        symbolSize: point => point[2],
        itemStyle: { color: palette[0], opacity: .78, shadowBlur: 8, shadowColor: palette[0] }
      }]
    }
  }

  if (/Progress|Liquid/i.test(type)) {
    const progress = Math.max(0, Math.min(100, resolvedFirstValue))
    if (/Round|Ring|Liquid/i.test(type)) {
      return {
        ...common,
        color: palette,
        title: chartTitle(widget, source.title, theme),
        series: [{
          type: 'pie',
          radius: ['54%', '70%'],
          center: ['50%', '58%'],
          silent: true,
          label: { show: true, position: 'center', formatter: `${progress}%`, color: '#d8ecff', fontSize: 23 },
          data: [
            { value: progress, itemStyle: { color: palette[0], shadowBlur: 8, shadowColor: palette[0] } },
            { value: 100 - progress, itemStyle: { color: '#17324b' } }
          ]
        }]
      }
    }
    return {
      ...common,
      title: chartTitle(widget, source.title, theme),
      grid: polishGrid({ top: 64, left: 24, right: 36, bottom: 26 }, widget),
      xAxis: { type: 'value', max: 100, show: false },
      yAxis: { type: 'category', data: [labels[0] || ''], axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: '#a9bfd1' } },
      series: [{ type: 'bar', data: [progress], barWidth: 14, showBackground: true, backgroundStyle: { color: '#17324b', borderRadius: 8 }, itemStyle: { color: palette[0], borderRadius: 8 }, label: { show: true, position: 'right', color: '#d8ecff', formatter: '{c}%' } }]
    }
  }

  if (/Rectangle/i.test(type)) {
    return {
      ...common,
      title: chartTitle(widget, source.title, theme),
      series: [{
        type: 'treemap',
        top: widget.chromeMode === 'hero' ? 74 : 48,
        left: 12,
        right: 12,
        bottom: 12,
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        label: { color: '#dcecff', fontSize: 10 },
        itemStyle: { borderColor: '#091b2b', gapWidth: 2 },
        data: data.map((item, index) => ({ name: labels[index], value: values[index], itemStyle: { color: palette[index % palette.length] } }))
      }]
    }
  }

  if (/WordCloud/i.test(type)) {
    return {
      ...common,
      title: chartTitle(widget, source.title, theme),
      graphic: labels.map((label, index) => ({
        type: 'text',
        left: `${12 + (index * 19) % 70}%`,
        top: `${28 + (index * 23) % 54}%`,
        rotation: index % 3 === 0 ? .18 : 0,
        style: { text: label, fill: palette[index % palette.length], fontSize: 15 + values[index] / 7, fontWeight: 600 }
      }))
    }
  }

  if (/Ring|Pie|Rose|Doughnut|Radial/i.test(type)) {
    const seriesSource = clone(source.series?.[0] || {})
    const total = values.reduce((sum, value) => sum + value, 0)
    const showCenterSummary = !/Pie|Rose/i.test(type)
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      legend: props.widget.w < 470
      ? polishLegend({ ...source.legend, orient: 'horizontal', left: 16, right: 16, top: 'auto', bottom: 8 }, theme, widget)
      : polishLegend({ ...source.legend, orient: 'vertical', right: '3%', top: 'middle' }, theme, widget),
      graphic: showCenterSummary ? [{
        type: 'group',
        left: props.widget.w < 470 ? '50%' : '42%',
        top: '56%',
        children: [
          { type: 'text', style: { text: total.toLocaleString('zh-CN', { maximumFractionDigits: 1 }), fill: theme.text, font: '600 22px Bahnschrift', textAlign: 'center' } },
          { type: 'text', top: 27, style: { text: 'TOTAL', fill: theme.muted, font: '9px Consolas', textAlign: 'center' } }
        ]
      }] : undefined,
      series: [{
        ...seriesSource,
        type: 'pie',
        radius: seriesSource.radius || (type.includes('Pie') || type.includes('Rose') ? ['12%', '68%'] : ['43%', '70%']),
        center: seriesSource.center || (props.widget.w < 470 ? ['50%', '56%'] : ['43%', '58%']),
        label: { show: false },
        labelLine: { show: false },
        roseType: type.includes('Rose') ? 'radius' : undefined,
        emphasis: { scaleSize: 7, itemStyle: { shadowBlur: 16, shadowColor: `${theme.primary}77` } },
        itemStyle: { borderColor: theme.background, borderWidth: 2 },
        data: data.map((item, index) => ({
          name: labels[index],
          value: values[index]
        }))
      }]
    }
  }

  const isBar = /Bar|Histogram|Column|Capsule|Pictorial/i.test(type)
  const isHorizontal = /Horizontal|Dynamic|Negative|Percent|Capsule/i.test(type)
  const isArea = /Area/i.test(type)
  const primary = safeColor(palette[0], isBar ? '#f7c948' : '#36d7b7')
  const seriesSource = clone(source.series?.[0] || {})
  const grid = polishGrid(source.grid, widget)
  const axisLine = { show: false, lineStyle: { color: theme.grid } }
  const axisLabel = { color: theme.muted, fontSize: 10, margin: 12, hideOverlap: true }
  const barData = type.includes('Negative')
    ? values.map((value, index) => index % 2 ? -value : value)
    : values
  const barGradient = new echarts.graphic.LinearGradient(
    0,
    0,
    isHorizontal ? 1 : 0,
    isHorizontal ? 0 : 1,
    [
      { offset: 0, color: isHorizontal ? `${primary}99` : primary },
      { offset: .58, color: isHorizontal ? primary : `${primary}cc` },
      { offset: 1, color: isHorizontal ? theme.secondary : `${theme.secondary}66` }
    ]
  )

  let chartSeries = [{
    ...seriesSource,
    type: isBar ? 'bar' : 'line',
    data: barData,
    smooth: !isBar,
    symbol: isBar ? 'none' : 'circle',
    showSymbol: false,
    symbolSize: 7,
    barMaxWidth: isBar ? (isHorizontal ? 17 : 32) : undefined,
    barCategoryGap: isHorizontal ? '44%' : '36%',
    showBackground: isBar,
    backgroundStyle: isBar ? { color: `${theme.grid}25`, borderRadius: 8 } : undefined,
    lineStyle: isBar ? undefined : { color: primary, width: widget.chromeMode === 'hero' ? 3 : 2.2, shadowBlur: 10, shadowColor: `${primary}55` },
    itemStyle: isBar
      ? {
          color: barGradient,
          borderRadius: /Capsule|Horizontal/i.test(type) ? 10 : (isHorizontal ? [0, 5, 5, 0] : [4, 4, 0, 0]),
          shadowBlur: 8,
          shadowColor: `${primary}28`
        }
      : { color: theme.text, borderColor: primary, borderWidth: 2 },
    areaStyle: isArea
      ? {
          opacity: .72,
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: `${primary}99` },
            { offset: .58, color: `${primary}24` },
            { offset: 1, color: `${primary}00` }
          ])
        }
      : undefined
  }]

  if (inferredSchema.series.length > 1) {
    chartSeries = inferredSchema.series.map((measure, index) => {
      const signal = safeColor(palette[index % palette.length], primary)
      const secondarySeries = isBar && index > 0
      return {
        type: secondarySeries ? 'line' : (isBar ? 'bar' : 'line'),
        name: measure.name,
        data: measure.data,
        yAxisIndex: !isHorizontal && inferredSchema.series.length > 1 ? Math.min(index, 1) : 0,
        smooth: !isBar || secondarySeries,
        showSymbol: secondarySeries,
        symbolSize: secondarySeries ? 5 : 7,
        barMaxWidth: isBar && !secondarySeries ? (isHorizontal ? 17 : 28) : undefined,
        showBackground: isBar && !secondarySeries,
        backgroundStyle: isBar && !secondarySeries
          ? { color: `${theme.grid}25`, borderRadius: 8 }
          : undefined,
        lineStyle: secondarySeries || !isBar
          ? { color: signal, width: 2.2, shadowBlur: 8, shadowColor: `${signal}55` }
          : undefined,
        itemStyle: isBar && !secondarySeries
          ? {
              color: new echarts.graphic.LinearGradient(
                0, 0, isHorizontal ? 1 : 0, isHorizontal ? 0 : 1,
                [
                  { offset: 0, color: `${signal}99` },
                  { offset: .6, color: signal },
                  { offset: 1, color: `${theme.secondary}77` }
                ]
              ),
              borderRadius: isHorizontal ? [0, 6, 6, 0] : [4, 4, 0, 0]
            }
          : { color: signal, borderColor: theme.text, borderWidth: secondarySeries ? 1 : 0 },
        areaStyle: isArea && index === 0
          ? {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: `${signal}88` },
                { offset: 1, color: `${signal}00` }
              ])
            }
          : undefined
      }
    })
  }

  if (/StackBar|MultipleBar|PercentBar|MixLineBar/i.test(type)) {
    const stacked = /StackBar|PercentBar/i.test(type)
    chartSeries = [0.52, 0.31, 0.17].map((ratio, index) => ({
      type: 'bar',
      name: `${index + 1}`,
      stack: stacked ? 'total' : undefined,
      barWidth: stacked ? 28 : 12,
      data: values.map(value => Math.round(value * ratio)),
      itemStyle: { color: palette[index % palette.length], borderRadius: stacked ? 0 : [3, 3, 0, 0] }
    }))
    if (type.includes('MixLineBar')) {
      chartSeries.push({
        type: 'line',
        data: values.map(value => Math.round(value * .82)),
        smooth: true,
        symbolSize: 5,
        lineStyle: { color: '#f7c948', width: 2 },
        itemStyle: { color: '#f7c948' }
      })
    }
  }

  return {
    ...common,
    color: palette,
    title: chartTitle(widget, source.title, theme),
    grid,
    xAxis: polishAxis({
      type: isHorizontal ? 'value' : 'category',
      data: isHorizontal ? undefined : labels,
      boundaryGap: isBar,
      axisTick: { show: false },
      axisLine,
      axisLabel,
      ...(source.xAxis || {})
    }, theme),
    yAxis: !isHorizontal && inferredSchema.series.length > 1
      ? inferredSchema.series.slice(0, 2).map((measure, index) => polishAxis({
          type: 'value',
          name: measure.name,
          position: index === 0 ? 'left' : 'right',
          nameTextStyle: { color: palette[index % palette.length], fontSize: 9 },
          axisTick: { show: false },
          axisLine,
          axisLabel: { ...axisLabel, color: palette[index % palette.length] },
          splitLine: { show: index === 0, lineStyle: { color: theme.grid, opacity: .16, type: 'dashed' } }
        }, theme))
      : polishAxis({
          type: isHorizontal ? 'category' : 'value',
          data: isHorizontal ? labels : undefined,
          axisTick: { show: false },
          axisLine,
          axisLabel,
          splitLine: { show: !isHorizontal, lineStyle: { color: theme.grid, opacity: .16, type: 'dashed' } },
          ...(source.yAxis || {})
        }, theme),
    series: chartSeries
  }
}

async function ensureChinaMap() {
  if (echarts.getMap('china')) return
  if (!chinaMapPromise) {
    chinaMapPromise = fetch(`${import.meta.env.BASE_URL}jimu-screen/china.json`)
      .then(response => {
        if (!response.ok) throw new Error(`China map request failed: ${response.status}`)
        return response.json()
      })
      .then(geoJson => echarts.registerMap('china', geoJson))
  }
  await chinaMapPromise
}

async function mapOption(widget) {
  await ensureChinaMap()
  const source = sanitizeChartOption(clone(widget.config?.option || {}))
  const data = toMapSeriesData(chartData(widget))
  if (widget.visualMode === 'ai') {
    return buildAiAuthoritativeChartOption({ widget, source, data: chartData(widget), map: true })
  }
  const max = Math.max(...data.map(item => Number(item.value || 0)), 1)
  const theme = normalizeDashboardTheme(widget)
  const hasTopChrome = shouldShowPanelChrome(widget)
  const hero = widget.chromeMode === 'hero' || widget.visualRole === 'primary-insight' || widget.visualRole === 'hero-map'
  return {
    animationDuration: 1300,
    animationEasing: 'cubicOut',
    backgroundColor: source.backgroundColor ?? 'transparent',
    title: chartTitle(widget, source.title, theme),
    tooltip: {
      trigger: 'item',
      backgroundColor: `${theme.background}ee`,
      borderColor: theme.primary,
      textStyle: { color: theme.text }
    },
    visualMap: {
      ...(source.visualMap || {}),
      min: 0,
      max,
      left: '5%',
      bottom: '3%',
      itemWidth: 10,
      itemHeight: 95,
      calculable: true,
      precision: 0,
      textStyle: { color: theme.muted, fontSize: 10 },
      inRange: {
        color: [
          theme.backgroundRaised || theme.background,
          theme.surfaceLift || theme.panelStrong,
          theme.secondary,
          theme.primary,
          theme.accent
        ]
      }
    },
    series: [{
      ...(source.series?.[0] || {}),
      type: 'map',
      map: 'china',
      roam: source.geo?.roam ?? hero,
      zoom: source.geo?.zoom ?? (hero ? 1.14 : 1.04),
      top: hasTopChrome ? 62 : 28,
      right: hero ? '8%' : '5%',
      bottom: 14,
      left: hero ? '8%' : '5%',
      selectedMode: 'single',
      label: {
        show: hero && widget.w >= 680,
        color: withAlpha(theme.text, .78),
        fontSize: 9,
        textBorderColor: withAlpha(theme.background, .84),
        textBorderWidth: 2
      },
      itemStyle: {
        areaColor: theme.surfaceLift || theme.panelStrong,
        borderColor: withAlpha(theme.primary, .78),
        borderWidth: 1.15,
        shadowBlur: hero ? 18 : 9,
        shadowColor: withAlpha(theme.primary, hero ? .46 : .28)
      },
      emphasis: {
        label: { show: true, color: '#fff', fontWeight: 700 },
        itemStyle: {
          areaColor: theme.accent,
          borderColor: theme.text,
          borderWidth: 1.5,
          shadowBlur: 24,
          shadowColor: withAlpha(theme.accent, .72)
        }
      },
      select: {
        label: { show: true, color: theme.text, fontWeight: 700 },
        itemStyle: { areaColor: theme.secondary, borderColor: theme.primary }
      },
      data
    }]
  }
}

async function render() {
  const renderSequence = ++latestRenderSequence
  if (!chartEl.value) return
  await nextTick()
  if (!chart) {
    const renderer = /Map|Earth/i.test(props.widget.component) ? 'svg' : 'canvas'
    chart = echarts.init(chartEl.value, null, { renderer })
    chart.on('click', handleChartDataClick)
    chart.getZr().on('click', handleChartCanvasClick)
  }
  const option = /Map|Earth/i.test(props.widget.component)
    ? await mapOption(props.widget)
    : baseOption(props.widget)
  if (renderSequence !== latestRenderSequence) return
  chart.setOption(option, true)
  chart.resize()
}

function handleChartDataClick(params = {}) {
  const rows = chartData(props.widget)
  emit('data-click', {
    name: params.name,
    value: params.value,
    dataIndex: params.dataIndex,
    seriesName: params.seriesName,
    data: rows[params.dataIndex] ?? params.data
  })
}

function handleChartCanvasClick(event = {}) {
  // ECharts dispatches its semantic data event before/alongside the zrender
  // event. A graphical target means the semantic handler owns the click;
  // emitting a second empty payload here would erase the clicked dimension.
  if (event?.target) return
  emit('data-click', {})
}

onMounted(() => {
  render()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartEl.value)
})

watch([() => props.widget, () => props.data], render, { deep: true })

onBeforeUnmount(() => {
  latestRenderSequence += 1
  resizeObserver?.disconnect()
  chart?.off('click', handleChartDataClick)
  chart?.getZr().off('click', handleChartCanvasClick)
  chart?.dispose()
})
</script>

<template>
  <div ref="chartEl" class="jimu-chart"></div>
</template>

<style scoped>
.jimu-chart {
  width: 100%;
  height: 100%;
}
</style>
