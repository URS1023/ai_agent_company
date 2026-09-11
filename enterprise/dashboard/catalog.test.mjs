import assert from 'node:assert/strict'
import test from 'node:test'
import { createTemplateCatalog } from './catalog.mjs'
import { DASHBOARD_TEMPLATES } from './lynx/src/reportDashboardTemplates.js'
import { preparePreview } from './viewer/preview.mjs'

test('catalog exports all original designs and only currently supported data slots', () => {
  const resources = [{ name: 'assets/runtime.js', sha256: 'a'.repeat(64) }]
  const catalog = createTemplateCatalog('b'.repeat(64), resources)
  assert.equal(catalog.schema_version, 1)
  assert.equal(catalog.templates.length, 20)
  for (const template of catalog.templates) {
    assert.ok(DASHBOARD_TEMPLATES.some((item) => item.id === template.template_id))
    const original = preparePreview(template.template_id)
    assert.deepEqual(JSON.parse(template.visual_json), original)
    assert.equal(template.renderer_build_id, 'b'.repeat(64))
    assert.deepEqual(template.asset_digests, resources)
    assert.ok(template.slots.length > 0)
    assert.equal(new Set(template.slots.map((slot) => slot.slot_id)).size, template.slots.length)
    for (const slot of template.slots) {
      const widget = original.widgets.find((item) => item.id === slot.slot_id)
      assert.ok(widget)
      assert.notEqual(widget.component, 'JText')
      assert.equal(slot.required, false)
      assert.equal(slot.row_limit, 1000)
      assert.deepEqual(
        slot.columns.map((column) => column.name),
        ['name', 'value'],
      )
    }
  }
  resources[0].sha256 = 'c'.repeat(64)
  assert.equal(catalog.templates[0].asset_digests[0].sha256, 'a'.repeat(64))
})

test('catalog rejects unpinned or duplicate rendering resources', () => {
  for (const resources of [
    [],
    [{ name: 'x', sha256: 'wrong' }],
    [
      { name: 'x', sha256: 'a'.repeat(64) },
      { name: 'x', sha256: 'a'.repeat(64) },
    ],
  ]) {
    assert.throws(
      () => createTemplateCatalog('b'.repeat(64), resources),
      /invalid_renderer_resources/,
    )
  }
  assert.throws(
    () => createTemplateCatalog('placeholder', [{ name: 'x', sha256: 'a'.repeat(64) }]),
    /invalid_renderer_resources/,
  )
})
