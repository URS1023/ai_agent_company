import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { createTemplateCatalog } from './catalog.mjs'

const root = new URL('../artifacts/dashboard-viewer/', import.meta.url)
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')
const manifest = JSON.parse(await readFile(new URL('.vite/manifest.json', root), 'utf8'))
assert.ok(manifest['runtime.mjs']?.isEntry, 'Build the dashboard runtime before exporting')
const names = new Set(['jimu-screen/china.json'])
for (const entry of Object.values(manifest)) {
  names.add(entry.file)
  for (const name of [...(entry.css || []), ...(entry.assets || [])]) names.add(name)
}
const resources = []
for (const name of [...names].sort()) {
  assert.match(name, /^(?:assets\/[\w.-]+|index\.html|china\.json|jimu-screen\/china\.json)$/)
  resources.push({ name, sha256: hash(await readFile(new URL(name, root))) })
}
const catalog = createTemplateCatalog(hash(JSON.stringify(resources)), resources)
const output = new URL('template-catalog.json', root)
await writeFile(output, JSON.stringify(catalog, null, 2) + '\n')
assert.deepEqual(JSON.parse(await readFile(output, 'utf8')), catalog)
console.log(
  JSON.stringify({
    output: output.href,
    templates: catalog.templates.length,
    rendererBuildId: catalog.templates[0].renderer_build_id,
    resources: resources.length,
  }),
)
