import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import {
  DASHBOARD_TEMPLATES,
  createDashboardTemplateContent,
} from '../lynx/src/reportDashboardTemplates.js'
import { post } from './no-business-network.mjs'
import { preparePreview } from './preview.mjs'
import { preserveMetricPrecision } from './renderer-compat.mjs'

test('every preview keeps original widgets and uses the original canvas dimensions', () => {
  for (const template of DASHBOARD_TEMPLATES) {
    const original = createDashboardTemplateContent(template.id)
    const actual = preparePreview(template.id)
    assert.deepEqual(actual.widgets, original.widgets)
    assert.equal(actual.page.design.width, original.canvas.width)
    assert.equal(actual.page.design.height, original.canvas.height)
    assert.equal(actual.page.backgroundColor, original.canvas.backgroundColor)
    for (const widget of actual.widgets) {
      assert.equal(widget.config.dataType, 1)
      assert.deepEqual(widget.config.chartDataParsed, [])
    }
  }
})

test('a preview never accepts a caller-supplied visual document', () => {
  assert.throws(() => preparePreview({ canvas: {}, widgets: [] }))
  assert.throws(() => preparePreview('unregistered'))
})

test('the legacy business request alias never dispatches SQL or HTTP', async () => {
  await assert.rejects(post('/api/data-analysis/execute-sql'), /viewer_business_network_disabled/)
})

test('API metric data retains tagged precision without touching template visuals', async () => {
  const { prepareDashboardView } = await import('./api-data.mjs')
  const templateId = 'equipment-digital-ops'
  const original = preparePreview(templateId)
  const metric = original.widgets.find((widget) => widget.component === 'JStatsSummary')
  const expected = {
    id: 'dashboard-1',
    template_id: templateId,
    design_identity: 'design-1',
    renderer_build_id: 'renderer-1',
  }
  for (const value of ['98.2500', '9007199254740993', '1E-1000', '-0.00']) {
    const view = {
      ...expected,
      revision: 1,
      status: 'ready',
      last_attempt_id: 'batch-1',
      failures: [],
      current: {
        batch_id: 'batch-1',
        slots: [
          {
            slot_id: metric.id,
            rows: [
              { name: { kind: 'string', value: '设备指标' }, value: { kind: 'decimal', value } },
            ],
          },
        ],
      },
    }
    const actual = prepareDashboardView(view, expected)
    const changed = actual.content.widgets.find((widget) => widget.id === metric.id)
    assert.equal(changed.config.chartDataParsed[0].value, value)
    changed.config.chartDataParsed = []
    changed.config.chartData = '[]'
    assert.deepEqual(actual.content, original)
    assert.throws(
      () => prepareDashboardView(view, { ...expected, design_identity: 'other' }),
      /dashboard_view_mismatch/,
    )
  }
})

test('chart numeric conversion rejects silent rounding and underflow', async () => {
  const { chartNumber } = await import('./api-data.mjs')
  for (const value of ['98.2500', '1E-3', '-0.00', '0.1'])
    assert.equal(chartNumber(value), Number(value))
  for (const value of ['9007199254740993', '1E-1000', '1E1000', '0.1234567890123456789']) {
    assert.throws(() => chartNumber(value), /dashboard_chart_precision_loss/)
  }
})

test('API data adapter keeps failed and empty state explicit and rejects invalid slot payloads', async () => {
  const { prepareDashboardView } = await import('./api-data.mjs')
  const expected = {
    id: 'd',
    template_id: 'equipment-digital-ops',
    design_identity: 'design',
    renderer_build_id: 'renderer',
  }
  const metric = preparePreview(expected.template_id).widgets.find(
    (widget) => widget.component === 'JStatsSummary',
  )
  const slot = {
    slot_id: metric.id,
    rows: [{ name: { kind: 'string', value: '设备' }, value: { kind: 'integer', value: '0' } }],
  }
  const base = {
    ...expected,
    revision: 2,
    status: 'ready',
    current: { batch_id: 'batch-1', slots: [slot] },
    last_attempt_id: 'batch-1',
    failures: [],
  }
  const failed = prepareDashboardView(
    {
      ...base,
      status: 'failed',
      last_attempt_id: 'batch-2',
      failures: [{ slot_id: metric.id, code: 'source_query_failed' }],
    },
    expected,
  )
  assert.equal(failed.status, 'failed')
  assert.equal(failed.lastAttemptId, 'batch-2')
  assert.equal(failed.failures.length, 1)
  const empty = prepareDashboardView(
    { ...base, status: 'empty', current: null, last_attempt_id: null },
    expected,
  )
  assert.equal(empty.status, 'empty')
  for (const slots of [
    [slot, slot],
    [{ ...slot, slot_id: 'unknown' }],
    [{ ...slot, rows: [{ ...slot.rows[0], style: { kind: 'string', value: 'red' } }] }],
    [{ ...slot, rows: [{ ...slot.rows[0], value: { kind: 'null', value: null } }] }],
  ])
    assert.throws(
      () => prepareDashboardView({ ...base, current: { ...base.current, slots } }, expected),
      /invalid_series_batch/,
    )
})

test('renderer compatibility changes only forced rounding and detects source drift', async () => {
  const source = await readFile(
    new URL('../lynx-renderer/src/components/JimuWidgetRenderer.vue', import.meta.url),
    'utf8',
  )
  const changed = preserveMetricPrecision(source)
  const legacy = "return value.toLocaleString('zh-CN', { maximumFractionDigits: 1 })"
  assert.equal(changed, source.replace(legacy, 'return String(value)'))
  assert.throws(() => preserveMetricPrecision(changed), /renderer_formatter_changed/)
  assert.throws(() => preserveMetricPrecision(source + legacy), /renderer_formatter_changed/)
})

test('data batches change only chart data across every registered template', async () => {
  const { applySeriesBatch } = await import('./series-data.mjs')
  for (const template of DASHBOARD_TEMPLATES) {
    const original = preparePreview(template.id)
    const targets = original.widgets.filter((widget) =>
      [
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
      ].includes(widget.component),
    )
    assert.ok(targets.length > 0, template.id)
    const rows = [{ name: '设备合格率', value: 98.25 }]
    const actual = applySeriesBatch(
      template.id,
      targets.map((widget) => ({ slotId: widget.id, rows })),
    )
    for (const target of targets) {
      const updated = actual.widgets.find((widget) => widget.id === target.id)
      assert.deepEqual(updated.config.chartDataParsed, rows)
      assert.equal(updated.config.chartData, JSON.stringify(rows))
      updated.config.chartDataParsed = []
      updated.config.chartData = '[]'
    }
    assert.deepEqual(actual, original)
    rows[0].value = 0
    assert.deepEqual(preparePreview(template.id), original)
  }
})

test('series batches reject visual edits, invalid numbers, duplicate and decorative slots atomically', async () => {
  const { applySeriesBatch } = await import('./series-data.mjs')
  const id = 'equipment-digital-ops'
  const original = preparePreview(id)
  const slotId = original.widgets.find((widget) => widget.component === 'JStatsSummary').id
  const valid = { slotId, rows: [{ name: '设备', value: 5 }] }
  for (const batch of [
    [valid, valid],
    [{ ...valid, style: { color: 'red' } }],
    [{ ...valid, slotId: 'unknown' }],
    [{ ...valid, slotId: original.widgets[0].id }],
    [{ slotId, rows: [{ name: '设备', value: NaN }] }],
    [{ slotId, rows: [{ name: '设备', value: Infinity }] }],
    [{ slotId, rows: [{ name: '设备', value: '5' }] }],
    [{ slotId, rows: [{ name: '设备', value: 5, itemStyle: {} }] }],
    [{ slotId, rows: new Array(1001).fill({ name: '设备', value: 5 }) }],
    [valid, { slotId: 'unknown', rows: [] }],
  ]) {
    assert.throws(() => applySeriesBatch(id, batch), /invalid_series_batch/)
    assert.deepEqual(preparePreview(id), original)
  }
  const actual = applySeriesBatch(id, [valid])
  valid.rows[0].value = 999
  assert.equal(
    actual.widgets.find((widget) => widget.id === slotId).config.chartDataParsed[0].value,
    5,
  )
})
