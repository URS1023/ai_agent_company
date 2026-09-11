import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('published template previews preserve all imported image bytes and names', async () => {
  const manifest = JSON.parse(
    await readFile(new URL('./lynx/manifest.json', import.meta.url), 'utf8'),
  )
  const published = JSON.parse(
    await readFile(
      new URL(
        '../../web/app/components/enterprise/dashboards/template-previews.json',
        import.meta.url,
      ),
      'utf8',
    ),
  )
  assert.equal(published.length, 20)
  for (const template of manifest.templates) {
    const item = published.find((entry) => entry.id === template.id)
    assert.ok(item)
    assert.equal(item.name, template.name)
    assert.equal(item.templateRevision, 1)
    assert.equal(item.contentSha256, template.contentSha256)
    assert.match(item.src, /^\/enterprise\/dashboard-templates\/template-\d{2}-[a-f0-9]{64}\.jpg$/)
    const bytes = await readFile(new URL(`../../web/public${item.src}`, import.meta.url))
    const original = await readFile(new URL(`./lynx/${template.preview}`, import.meta.url))
    assert.deepEqual(bytes, original)
    assert.ok(item.src.includes(createHash('sha256').update(bytes).digest('hex')))
  }
})
