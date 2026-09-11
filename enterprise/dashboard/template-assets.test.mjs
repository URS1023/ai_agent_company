import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const root = new URL('./', import.meta.url)
const digest = (value) => createHash('sha256').update(value).digest('hex')

test('imported Lynx assets match their recorded byte hashes', async () => {
  const manifest = JSON.parse(await readFile(new URL('lynx/manifest.json', root), 'utf8'))
  assert.equal(manifest.schemaVersion, 1)
  assert.match(manifest.sourceCommit, /^[a-f0-9]{40}$/)
  assert.equal(manifest.files.length, 23)
  assert.equal(new Set(manifest.files.map((file) => file.path)).size, manifest.files.length)
  for (const file of manifest.files) {
    assert.match(
      file.path,
      /^(?:src\/reportDashboard(?:Templates|Showcases)\.js|LICENSE|previews\/template-\d{2}\.jpg)$/,
    )
    const bytes = await readFile(new URL(`lynx/${file.path}`, root))
    assert.equal(bytes.length, file.bytes, file.path)
    assert.equal(digest(bytes), file.sha256, file.path)
    if (file.path.endsWith('.jpg')) assert.equal(bytes.subarray(0, 2).toString('hex'), 'ffd8')
  }
})

test('all twenty actual template definitions are reproducible and clone independently', async () => {
  const { DASHBOARD_TEMPLATES, createDashboardTemplateContent } =
    await import('./lynx/src/reportDashboardTemplates.js')
  const manifest = JSON.parse(await readFile(new URL('lynx/manifest.json', root), 'utf8'))
  assert.equal(DASHBOARD_TEMPLATES.length, 20)
  assert.equal(manifest.templates.length, 20)
  assert.equal(new Set(DASHBOARD_TEMPLATES.map((item) => item.id)).size, 20)
  for (const item of DASHBOARD_TEMPLATES) {
    const recorded = manifest.templates.find((template) => template.id === item.id)
    assert.ok(recorded, item.id)
    const content = createDashboardTemplateContent(item.id)
    assert.ok(content.canvas)
    assert.ok(content.widgets.length > 0)
    assert.equal(new Set(content.widgets.map((widget) => widget.id)).size, content.widgets.length)
    assert.equal(digest(JSON.stringify(content)), recorded.contentSha256)
    assert.equal(content.widgets.length, recorded.widgetCount)
    assert.ok(manifest.files.some((file) => file.path === recorded.preview))
    content.widgets[0].x = -999
    assert.equal(
      digest(JSON.stringify(createDashboardTemplateContent(item.id))),
      recorded.contentSha256,
    )
  }
  assert.equal(createDashboardTemplateContent('unregistered-template'), null)
})
