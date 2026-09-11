import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('original renderer source and map retain their exact migration provenance', async () => {
  const root = new URL('./lynx-renderer/', import.meta.url)
  const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'))
  assert.equal(manifest.kind, 'renderer-source-only')
  assert.equal(manifest.files.length, 8)
  assert.match(manifest.sourceCommit, /^[a-f0-9]{40}$/)
  assert.equal(new Set(manifest.files.map((file) => file.path)).size, 8)
  for (const file of manifest.files) {
    assert.match(
      file.path,
      /^(?:LICENSE|public\/china\.json|src\/(?:components\/Jimu(?:Canvas|Chart|WidgetRenderer)\.vue|utils\/(?:dashboardTheme|dashboardData|aiChartOption)\.js))$/,
    )
    const bytes = await readFile(new URL(file.path, root))
    assert.equal(bytes.length, file.bytes)
    assert.equal(createHash('sha256').update(bytes).digest('hex'), file.sha256, file.path)
  }
  const map = JSON.parse(await readFile(new URL('public/china.json', root), 'utf8'))
  assert.equal(map.type, 'FeatureCollection')
  assert.ok(map.features.length > 0)
  assert.deepEqual(manifest.externalModules, ['@/api/request', 'echarts', 'vue'])
})
