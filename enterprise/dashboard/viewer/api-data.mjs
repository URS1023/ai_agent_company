import { zDashboardView } from '../../contracts/generated/zod.gen.ts'
import { preparePreview } from './preview.mjs'
import { applySeriesBatch } from './series-data.mjs'

function canonicalDecimal(value) {
  if (typeof value !== 'string' || value.length > 512)
    throw new Error('dashboard_chart_precision_loss')
  const match = /^([+-]?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/.exec(value)
  if (!match) throw new Error('dashboard_chart_precision_loss')
  let digits = (match[2] + (match[3] || '')).replace(/^0+/, '')
  if (!digits) return '0'
  let exponent = BigInt(match[4] || '0') - BigInt((match[3] || '').length)
  while (digits.endsWith('0')) {
    digits = digits.slice(0, -1)
    exponent += 1n
  }
  return `${match[1] === '-' ? '-' : ''}${digits}e${exponent}`
}

// Check decimal display round trips; this is not a claim of exact IEEE-754 arithmetic.
export function chartNumber(text) {
  const canonical = canonicalDecimal(text)
  const value = Number(text)
  if (!Number.isFinite(value) || canonicalDecimal(String(value)) !== canonical) {
    throw new Error('dashboard_chart_precision_loss')
  }
  return value
}

export function prepareDashboardView(input, expected) {
  const view = zDashboardView.parse(input)
  if (
    ['id', 'template_id', 'design_identity', 'renderer_build_id'].some(
      (key) => view[key] !== expected[key],
    )
  ) {
    throw new Error('dashboard_view_mismatch')
  }
  if (
    (view.status === 'ready' &&
      (!view.current || view.current.batch_id !== view.last_attempt_id || view.failures.length)) ||
    (view.status === 'empty' && view.current)
  )
    throw new Error('dashboard_view_mismatch')
  const original = preparePreview(view.template_id)
  const widgets = new Map(original.widgets.map((widget) => [widget.id, widget]))
  const seen = new Set()
  const numeric = []
  const metrics = []
  for (const slot of view.current?.slots || []) {
    const widget = widgets.get(slot.slot_id)
    if (!widget || seen.has(slot.slot_id) || slot.rows.length > 1000)
      throw new Error('invalid_series_batch')
    seen.add(slot.slot_id)
    const metric = widget.component === 'JStatsSummary'
    const rows = slot.rows.map((row) => {
      if (
        Object.keys(row).length !== 2 ||
        row.name?.kind !== 'string' ||
        row.name.value.length > 240 ||
        !['integer', 'decimal'].includes(row.value?.kind)
      )
        throw new Error('invalid_series_batch')
      canonicalDecimal(row.value.value)
      return {
        name: row.name.value,
        value: metric ? row.value.value : chartNumber(row.value.value),
      }
    })
    const target = { slotId: slot.slot_id, rows }
    if (metric) metrics.push(target)
    else numeric.push(target)
  }
  const content = applySeriesBatch(view.template_id, numeric)
  for (const metric of metrics) {
    const widget = content.widgets.find((item) => item.id === metric.slotId)
    widget.config.chartDataParsed = metric.rows
    widget.config.chartData = JSON.stringify(metric.rows)
  }
  return {
    content,
    revision: view.revision,
    status: view.status,
    lastAttemptId: view.last_attempt_id,
    failures: view.failures,
  }
}
