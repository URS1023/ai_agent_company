import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const hash = (bytes) => createHash('sha256').update(bytes).digest('hex')
const validPath = (name) =>
  typeof name === 'string' && /^(?:assets\/[\w.-]+|jimu-screen\/china\.json)$/.test(name)

export async function publishRenderer(source, destination) {
  assert.ok(isAbsolute(source) && isAbsolute(destination))
  const catalog = JSON.parse(await readFile(join(source, 'template-catalog.json'), 'utf8'))
  const manifest = JSON.parse(await readFile(join(source, '.vite/manifest.json'), 'utf8'))
  assert.equal(catalog.schema_version, 1)
  assert.ok(catalog.templates.length > 0)
  const first = catalog.templates[0]
  const resources = first.asset_digests
  assert.ok(
    resources.length > 0 &&
      resources.every((item) => validPath(item.name) && /^[a-f0-9]{64}$/.test(item.sha256)),
  )
  assert.equal(new Set(resources.map((item) => item.name)).size, resources.length)
  const build = hash(JSON.stringify(resources))
  for (const template of catalog.templates) {
    assert.equal(template.renderer_build_id, build)
    assert.deepEqual(template.asset_digests, resources)
  }
  const runtime = manifest['runtime.mjs']
  assert.equal(runtime?.isEntry, true)
  assert.ok(validPath(runtime.file))
  const names = new Set(resources.map((item) => item.name))
  for (const entry of Object.values(manifest)) {
    for (const name of [entry.file, ...(entry.css ?? []), ...(entry.assets ?? [])])
      assert.ok(names.has(name), 'Manifest resource missing from pinned catalog')
    for (const name of [...(entry.imports ?? []), ...(entry.dynamicImports ?? [])])
      assert.ok(Object.hasOwn(manifest, name), 'Missing manifest dependency')
  }
  const files = []
  for (const resource of resources) {
    const bytes = await readFile(join(source, resource.name))
    assert.equal(hash(bytes), resource.sha256, 'Renderer resource digest mismatch')
    files.push({ ...resource, bytes })
  }
  // Check the entire source before writing anything; partial copies never get metadata.
  for (const file of files) {
    const target = join(destination, build, file.name)
    await mkdir(dirname(target), { recursive: true })
    try {
      await writeFile(target, file.bytes, { flag: 'wx' })
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
    }
    assert.deepEqual(
      await readFile(target),
      file.bytes,
      'Published renderer differs; retain existing bytes',
    )
  }
  return {
    rendererBuildId: build,
    entry: `/${build}/${runtime.file}`,
    styles: resources
      .filter((item) => item.name.endsWith('.css'))
      .map((item) => `/${build}/${item.name}`),
    resources,
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await publishRenderer(
    fileURLToPath(new URL('../artifacts/dashboard-viewer/', import.meta.url)),
    fileURLToPath(new URL('../../web/public/enterprise/renderers/', import.meta.url)),
  )
  const metadata = new URL(
    '../../web/app/components/enterprise/dashboards/renderer-build.json',
    import.meta.url,
  )
  await writeFile(metadata, JSON.stringify(result, null, 2) + '\n')
  assert.deepEqual(JSON.parse(await readFile(metadata, 'utf8')), result)
  console.log(JSON.stringify({ ...result, metadata: metadata.href }))
}
