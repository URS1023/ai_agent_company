import { inferSeriesSchema, normalizeSeriesRows, toMapSeriesData } from './dashboardData.js'

function clone(value) {
  if (value == null) return value
  return JSON.parse(JSON.stringify(value))
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
  return normalizeSeriesRows(data).map(item => ({ name: item.label, value: item.value }))
}

function namedRowsForMeasure(data, measureIndex) {
  const schema = inferSeriesSchema(data)
  const measure = schema.series[measureIndex] || schema.series[0]
  if (!measure || !schema.labels.length) return namedRows(data)
  return schema.labels.map((name, index) => ({ name, value: measure.data[index] ?? 0 }))
}

function mergeNamedData(existing, replacement) {
  const visualItems = Array.isArray(existing) ? existing : []
  return replacement.map((item, index) => {
    const visual = visualItems[index]
    if (!visual || typeof visual !== 'object' || Array.isArray(visual)) return item
    return { ...visual, name: item.name, value: item.value }
  })
}

function gaugeOption(source, data) {
  const rows = namedRows(data)
  const sourceSeries = seriesList(source)
  const series = sourceSeries.length ? sourceSeries : [{ type: 'gauge' }]
  return {
    ...source,
    series: series.map((item, index) => {
      const row = rows[index] || rows[0] || { name: '', value: 0 }
      const existing = Array.isArray(item?.data) && item.data[0] && typeof item.data[0] === 'object'
        ? item.data[0]
        : {}
      return {
        ...item,
        type: item?.type || 'gauge',
        data: [{ ...existing, value: Number.isFinite(Number(row.value)) ? Number(row.value) : 0, name: row.name }]
      }
    })
  }
}

function radarValues(data) {
  const schema = inferSeriesSchema(data)
  if (data.length === 1 && schema.numericKeys.length > 1) {
    return {
      labels: schema.numericKeys,
      values: schema.numericKeys.map(key => {
        const value = Number(data[0]?.[key])
        return Number.isFinite(value) ? value : 0
      })
    }
  }
  const rows = normalizeSeriesRows(data)
  return {
    labels: rows.map(item => item.label),
    values: rows.map(item => Number.isFinite(Number(item.value)) ? Number(item.value) : 0)
  }
}

function radarOption(source, data, widget) {
  const { labels, values } = radarValues(data)
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
        value: values
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

function progressOption(source, data) {
  const row = namedRows(data)[0] || { name: '', value: 0 }
  const progress = Math.max(0, Math.min(100, Number.isFinite(Number(row.value)) ? Number(row.value) : 0))
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
            { ...(existing[0] && typeof existing[0] === 'object' ? existing[0] : {}), value: progress, name: row.name },
            { ...(existing[1] && typeof existing[1] === 'object' ? existing[1] : {}), value: 100 - progress }
          ]
        }
      }
      return { ...item, data: [progress] }
    })
  }
}

function scatterOption(source, data) {
  const schema = inferSeriesSchema(data)
  const normalized = normalizeSeriesRows(data)
  const points = schema.series.length >= 2
    ? schema.labels.map((_, index) => schema.series.map(series => series.data[index] ?? 0))
    : normalized.map((item, index) => [index, item.value])
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
  const series = sourceSeries.map((item, index) => ({
    ...item,
    data: (schema.series[index] || schema.series[0])?.data || fallbackValues
  }))
  return {
    ...source,
    xAxis: replaceCategoryAxisData(source.xAxis, labels),
    yAxis: replaceCategoryAxisData(source.yAxis, labels),
    series
  }
}

/**
 * The backend AI owns every ECharts visual property for generated dashboards.
 * This adapter deliberately performs no theme polishing or component recipe;
 * it only replaces business data with the latest verified query result.
 */
export function buildAiAuthoritativeChartOption({ widget = {}, source = {}, data = [], map = false }) {
  const option = clone(source) || {}
  const rows = Array.isArray(data) ? clone(data) : []
  const type = String(widget.component || '')

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
  if (/Ring|Pie|Rose|Doughnut|Radial/i.test(type)) return namedSeriesOption(option, rows, 'pie')
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

