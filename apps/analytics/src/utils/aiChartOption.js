/**
 * aiChartOption —— AI 生成图表的 option 装配与空数据呈现。
 *
 * 位置：JimuChart 在 visualMode === 'ai' 时调用（普通模式用 dashboardData 的默认配方）。
 * 依赖：`utils/dashboardData`（inferSeriesSchema / normalizeSeriesRows / toMapSeriesData）。
 * 关键约束：
 * - AI 生成的图表以模型下发的 ECharts option 为权威：本模块刻意不做主题美化、不套组件配方，
 *   只把业务数据替换为最新一次校验通过的查询结果，避免覆盖模型给出的视觉设计；
 * - 空查询结果既不是"数值为 0"，也不允许复用示例数据：必须返回 emptyChartOption
 *   显示"暂无数据"，并清空 series/dataset/xAxis/yAxis 与图例提示；
 * - 所有函数返回新对象（clone），不修改传入的 option 与数据。
 */
import { inferSeriesSchema, normalizeSeriesRows, toMapSeriesData } from './dashboardData.js'
import { chartPoint, chartValueText, withExactChartTooltip } from './chartPrecision.js'

function clone(value) {
  if (value == null) return value
  return JSON.parse(JSON.stringify(value))
}

/**
 * 空数据兜底 option：清空数据类配置，仅保留原 option 的样式并叠加"暂无数据"文字。
 * 用于区分"查询为空"与"数值为 0"，也防止把上一次的示例数据留在图上。
 * @param {object} source 原始 option（保留其 textStyle 等样式）
 * @returns {object} 新的空态 option
 */

// An empty query is neither a zero measurement nor permission to reuse samples.
export function emptyChartOption(source = {}) {
  const option = clone(source) || {}
  return {
    ...option,
    series: [],
    dataset: [],
    xAxis: [],
    yAxis: [],
    legend: { show: false },
    tooltip: { show: false },
    graphic: [{ type: 'text', left: 'center', top: 'middle',
      style: { text: '暂无数据', fill: option.textStyle?.color || '#8a9aae', fontSize: 14 } }],
  }
}

function own(value, key) {
  return Boolean(value && Object.prototype.hasOwnProperty.call(value, key))
}

function seriesList(source) {
  if (Array.isArray(source.series)) return source.series
  if (source.series && typeof source.series === 'object') return [source.series]
  return []
}

function replaceDatasetSource(dataset, data) {
  if (Array.isArray(dataset)) {
    return dataset.map((entry, index) => index === 0
      ? { ...(entry || {}), source: data }
      : entry)
  }
  return { ...(dataset || {}), source: data }
}

function withoutInlineData(series) {
  const result = { ...(series || {}) }
  delete result.data
  return result
}

function replaceCategoryAxisData(axis, labels) {
  if (Array.isArray(axis)) {
    return axis.map(item => replaceCategoryAxisData(item, labels))
  }
  if (!axis || typeof axis !== 'object') return axis
  if (axis.type !== 'category' && !own(axis, 'data')) return axis
  return { ...axis, data: labels }
}

function namedRows(data) {
  return normalizeSeriesRows(data).map(item => ({ ...item, name: item.label }))
}

function namedRowsForMeasure(data, measureIndex) {
  const schema = inferSeriesSchema(data)
  const measure = schema.series[measureIndex] || schema.series[0]
  if (!measure || !schema.labels.length) return namedRows(data)
  return schema.labels.map((name, index) => ({ name, value: measure.data[index],
    exactValue: measure.exactValues[index], approximate: measure.approximateValues[index] }))
}

function mergeNamedData(existing, replacement) {
  const visualItems = Array.isArray(existing) ? existing : []
  return replacement.map((item, index) => {
    const visual = visualItems[index]
    if (!visual || typeof visual !== 'object' || Array.isArray(visual)) return item
    return { ...visual, ...item }
  })
}

function gaugeOption(source, data) {
  const rows = namedRows(data)
  const sourceSeries = seriesList(source)
  const series = sourceSeries.length ? sourceSeries : [{ type: 'gauge' }]
  return {
    ...source,
    series: series.map((item, index) => {
      const row = rows[index] || rows[0] || { name: '', ...chartPoint(null) }
      const existing = Array.isArray(item?.data) && item.data[0] && typeof item.data[0] === 'object'
        ? item.data[0]
        : {}
      return {
        ...item,
        type: item?.type || 'gauge',
        detail: { ...(item?.detail || {}), valueAnimation: false, formatter: () => chartValueText(row) },
        data: [{ ...existing, ...row }]
      }
    })
  }
}

function radarValues(data) {
  const schema = inferSeriesSchema(data)
  if (data.length === 1 && schema.numericKeys.length > 1) {
    return {
      labels: schema.numericKeys,
      values: schema.numericKeys.map(key => chartPoint(data[0]?.[key]).value),
      exactValues: schema.numericKeys.map(key => chartPoint(data[0]?.[key]).exactValue),
      approximateValues: schema.numericKeys.map(key => chartPoint(data[0]?.[key]).approximate)
    }
  }
  const rows = normalizeSeriesRows(data)
  return {
    labels: rows.map(item => item.label),
    values: rows.map(item => item.value),
    exactValues: rows.map(item => item.exactValue),
    approximateValues: rows.map(item => item.approximate)
  }
}

function radarOption(source, data, widget) {
  const { labels, values, exactValues, approximateValues } = radarValues(data)
  const maxValue = Math.max(...values.map(value => Math.abs(value)), 1)
  const radarSource = source.radar && typeof source.radar === 'object' ? source.radar : {}
  const existingIndicators = Array.isArray(radarSource.indicator) ? radarSource.indicator : []
  const sourceSeries = seriesList(source)
  const visualSeries = sourceSeries[0] || { type: 'radar' }
  const existingItem = Array.isArray(visualSeries.data)
    && visualSeries.data[0]
    && typeof visualSeries.data[0] === 'object'
    ? visualSeries.data[0]
    : {}
  return {
    ...source,
    radar: {
      ...radarSource,
      indicator: labels.map((name, index) => ({
        ...(existingIndicators[index] || {}),
        name,
        max: Math.max(maxValue * 1.2, Math.abs(values[index]) * 1.2, 1)
      }))
    },
    series: [{
      ...visualSeries,
      type: visualSeries.type || 'radar',
      data: [{
        ...existingItem,
        name: existingItem.name || widget.componentName || '',
        value: values, exactValue: exactValues, exactLabels: labels, exactApproximate: approximateValues
      }]
    }]
  }
}

function namedSeriesOption(source, data, fallbackType) {
  const sourceSeries = seriesList(source)
  const series = sourceSeries.length ? sourceSeries : [{ type: fallbackType }]
  return {
    ...source,
    series: series.map((item, index) => ({
      ...item,
      type: item?.type || fallbackType,
      data: mergeNamedData(item?.data, namedRowsForMeasure(data, index))
    }))
  }
}

function circularSeriesOption(source, data) {
  const option = namedSeriesOption(source, data, 'pie')
  const rows = namedRows(data)
  if (rows.length === 1) {
    const row = rows[0]
    return {
      ...option,
      legend: { ...(option.legend || {}), show: false },
      series: (option.series || []).map(item => {
        if (item.type !== 'pie' || own(item, 'radius')) return item
        return {
          ...item,
          radius: ['48%', '70%'],
          center: item.center || ['50%', '57%'],
          labelLine: { ...(item.labelLine || {}), show: false },
          label: {
            ...(item.label || {}),
            show: true,
            position: 'center',
            formatter: () => `${chartValueText(row)}\n${row.name}`,
            color: item.label?.color || '#EAF8FF',
            fontFamily: item.label?.fontFamily || 'Bahnschrift, Microsoft YaHei UI, sans-serif',
            fontSize: item.label?.fontSize || 16,
            fontWeight: item.label?.fontWeight || 600,
            lineHeight: item.label?.lineHeight || 27,
          },
        }
      })
    }
  }
  return {
    ...option,
    series: (option.series || []).map(item => {
      if (item.type !== 'pie') return item
      return {
        ...item,
        radius: item.radius || ['46%', '68%'],
        padAngle: item.padAngle ?? 2,
        itemStyle: {
          borderRadius: 4,
          ...(item.itemStyle || {})
        }
      }
    })
  }
}

function progressOption(source, data) {
  const row = namedRows(data)[0] || { name: '', ...chartPoint(null) }
  const progress = row.value == null ? null : Math.max(0, Math.min(100, row.value))
  const sourceSeries = seriesList(source)
  const series = sourceSeries.length ? sourceSeries : [{ type: 'bar' }]
  return {
    ...source,
    series: series.map(item => {
      if (item?.type === 'pie') {
        const existing = Array.isArray(item.data) ? item.data : []
        return {
          ...item,
          data: [
            { ...(existing[0] && typeof existing[0] === 'object' ? existing[0] : {}), ...row, value: progress },
            { ...(existing[1] && typeof existing[1] === 'object' ? existing[1] : {}), ...chartPoint(progress == null ? null : 100 - progress), name: '剩余（近似）' }
          ]
        }
      }
      return { ...item, data: [{ ...row, value: progress }] }
    })
  }
}

function scatterOption(source, data) {
  const schema = inferSeriesSchema(data)
  const normalized = normalizeSeriesRows(data)
  const points = schema.series.length >= 2
    ? schema.labels.map((_, index) => ({ value: schema.series.map(series => series.data[index]),
      exactValue: schema.series.map(series => series.exactValues[index]), exactLabels: schema.numericKeys,
      exactApproximate: schema.series.map(series => series.approximateValues[index]) }))
    : normalized.map((item, index) => ({ value: [index, item.value], exactValue: [String(index), item.exactValue],
      exactLabels: ['序号', '数值'], exactApproximate: [false, item.approximate] }))
  const sourceSeries = seriesList(source)
  const series = sourceSeries.length ? sourceSeries : [{ type: 'scatter' }]
  return {
    ...source,
    series: series.map(item => ({ ...item, type: item?.type || 'scatter', data: points }))
  }
}

function wordCloudOption(source, data) {
  if (!Array.isArray(source.graphic)) return source
  const rows = namedRows(data)
  return {
    ...source,
    graphic: source.graphic.map((item, index) => {
      if (!item || typeof item !== 'object' || !item.style || !rows[index]) return item
      return { ...item, style: { ...item.style, text: rows[index].name } }
    })
  }
}

function directSeriesOption(source, data) {
  const schema = inferSeriesSchema(data)
  const rows = normalizeSeriesRows(data)
  const labels = schema.labels.length ? schema.labels : rows.map(item => item.label)
  const fallbackValues = rows.map(item => item.value)
  const sourceSeries = seriesList(source)
  const series = sourceSeries.map((item, index) => {
    const measure = schema.series[index] || schema.series[0]
    const updated = {
      ...item,
      data: measure?.data || fallbackValues,
      exactValues: measure?.exactValues || rows.map(row => row.exactValue),
      approximateValues: measure?.approximateValues || rows.map(row => row.approximate)
    }
    if (item?.type === 'line' && item.smooth === undefined) {
      updated.smooth = 0.35
    }
    if (item?.type === 'bar') {
      if (!item.barMaxWidth) updated.barMaxWidth = 24
      if (!item.itemStyle?.borderRadius) {
        updated.itemStyle = {
          borderRadius: [4, 4, 0, 0],
          ...(item.itemStyle || {})
        }
      }
    }
    return updated
  })
  return {
    ...source,
    xAxis: replaceCategoryAxisData(source.xAxis, labels),
    yAxis: replaceCategoryAxisData(source.yAxis, labels),
    series
  }
}

/**
 * 以 AI 下发的 option 为准，仅替换其中的业务数据。
 * 按组件类型选择数据注入方式：地图 → toMapSeriesData；仪表盘/雷达/进度/散点/词云/环形/漏斗/矩形树
 * 走各自的系列构造；含 dataset 时替换 dataset.source 并移除内联 data（避免两份数据冲突）；
 * 其余情况直接按行列数据生成系列。
 * 数据为空时返回 emptyChartOption（不复用旧数据）。
 * @param {{widget?: object, source?: object, data?: Array, map?: boolean}} params
 * @returns {object} 新的 ECharts option
 */

/**
 * The backend AI owns every ECharts visual property for generated dashboards.
 * This adapter deliberately performs no theme polishing or component recipe;
 * it only replaces business data with the latest verified query result.
 */
function buildChartOption({ widget = {}, source = {}, data = [], map = false }) {
  const option = clone(source) || {}
  const rawRows = Array.isArray(data) ? clone(data) : []
  const type = String(widget.component || '')
  const rows = rawRows
  if (!rows.length) return emptyChartOption(option)

  if (map || /Map|Earth/i.test(type)) {
    const sourceSeries = seriesList(option)
    const series = sourceSeries.length ? sourceSeries : [{ type: 'map', map: 'china' }]
    return {
      ...option,
      series: series.map(item => ({
        ...item,
        type: item?.type || 'map',
        map: item?.map || 'china',
        data: toMapSeriesData(rows)
      }))
    }
  }

  if (/Gauge/i.test(type)) return gaugeOption(option, rows)
  if (/Radar/i.test(type)) return radarOption(option, rows, widget)
  if (/Progress|Liquid/i.test(type)) return progressOption(option, rows)
  if (/Scatter|Quadrant|Bubble/i.test(type)) return scatterOption(option, rows)
  if (/WordCloud/i.test(type)) return wordCloudOption(option, rows)
  if (/Ring|Pie|Rose|Doughnut|Radial/i.test(type)) return circularSeriesOption(option, rows)
  if (/Funnel|Pyramid/i.test(type)) return namedSeriesOption(option, rows, 'funnel')
  if (/Rectangle|Treemap/i.test(type)) return namedSeriesOption(option, rows, 'treemap')

  if (own(option, 'dataset')) {
    return {
      ...option,
      dataset: replaceDatasetSource(option.dataset, rows),
      series: seriesList(option).map(withoutInlineData)
    }
  }

  return directSeriesOption(option, rows)
}

/** 数据注入后统一安装原值提示，模板的近似格式不能覆盖查询原文。 */
export function buildAiAuthoritativeChartOption(params) {
  return withExactChartTooltip(buildChartOption(params))
}
