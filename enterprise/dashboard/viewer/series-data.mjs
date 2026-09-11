import { preparePreview } from './preview.mjs'

const seriesComponents = new Set([
  'JStatsSummary',
  'JBar',
  'JLine',
  'JArea',
  'JHorizontalBar',
  'JMultipleBar',
  'JRing',
  'JGauge',
  'JAreaMap',
  'JReplicaRankingList',
  'JReplicaProgressRing',
])

export const supportsSeriesComponent = (component) => seriesComponents.has(component)

function reject() {
  throw new Error('invalid_series_batch')
}

function exactRecord(value, fields) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) return false
  const descriptors = Object.getOwnPropertyDescriptors(value)
  const keys = Reflect.ownKeys(descriptors)
  return (
    keys.length === fields.length &&
    fields.every(
      (field) => Object.hasOwn(descriptors, field) && Object.hasOwn(descriptors[field], 'value'),
    )
  )
}

// Rendering boundary only: the server owns authorization, query execution and revisions.
export function applySeriesBatch(templateId, batch) {
  const preview = preparePreview(templateId)
  if (!Array.isArray(batch) || batch.length > preview.widgets.length) reject()
  const widgets = new Map(preview.widgets.map((widget) => [widget.id, widget]))
  const seen = new Set()
  for (const entry of batch) {
    if (!exactRecord(entry, ['slotId', 'rows'])) reject()
    const { slotId, rows } = entry
    const widget = widgets.get(slotId)
    if (!widget || !seriesComponents.has(widget.component) || seen.has(slotId)) reject()
    seen.add(slotId)
    if (!Array.isArray(rows) || rows.length > 1000) reject()
    const detached = []
    for (const row of rows) {
      if (!exactRecord(row, ['name', 'value'])) reject()
      if (
        typeof row.name !== 'string' ||
        row.name.length > 240 ||
        typeof row.value !== 'number' ||
        !Number.isFinite(row.value)
      )
        reject()
      detached.push({ name: row.name, value: row.value })
    }
    widget.config.chartDataParsed = detached
    widget.config.chartData = JSON.stringify(detached)
  }
  return preview
}
