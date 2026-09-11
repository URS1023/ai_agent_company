import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, mkdir, writeFile } from 'node:fs/promises'

const root = new URL('./lynx/', import.meta.url)
const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'))
const destination = new URL('../../web/public/enterprise/dashboard-templates/', import.meta.url)
const metadata = new URL(
  '../../web/app/components/enterprise/dashboards/template-previews.json',
  import.meta.url,
)
assert.equal(manifest.templates.length, 20)
assert.equal(new Set(manifest.templates.map((item) => item.id)).size, 20)
await mkdir(destination, { recursive: true })
const previews = []
for (const template of manifest.templates) {
  assert.match(template.preview, /^previews\/template-\d{2}\.jpg$/)
  const source = manifest.files.find((item) => item.path === template.preview)
  const bytes = await readFile(new URL(template.preview, root))
  const sha256 = createHash('sha256').update(bytes).digest('hex')
  assert.equal(sha256, source.sha256)
  assert.equal(bytes.length, source.bytes)
  const file = template.preview.split('/').at(-1).replace('.jpg', `-${sha256}.jpg`)
  const target = new URL(file, destination)
  try {
    await writeFile(target, bytes, { flag: 'wx' })
  } catch (error) {
    if (error.code !== 'EEXIST') throw error
  }
  assert.deepEqual(await readFile(target), bytes)
  previews.push({
    id: template.id,
    name: template.name,
    templateRevision: 1,
    contentSha256: template.contentSha256,
    src: `/enterprise/dashboard-templates/${file}`,
  })
}
await writeFile(metadata, JSON.stringify(previews, null, 2) + '\n')
assert.deepEqual(JSON.parse(await readFile(metadata, 'utf8')), previews)
console.log(
  JSON.stringify({ templates: previews.length, metadata: metadata.href, images: destination.href }),
)
