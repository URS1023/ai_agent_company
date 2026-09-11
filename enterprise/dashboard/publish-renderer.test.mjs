import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { publishRenderer } from './publish-renderer.mjs'

async function fixture(run) {
  const root = await mkdtemp(join(tmpdir(), 'dashboard-publish-'))
  const source = join(root, 'source')
  const target = join(root, 'target')
  await mkdir(join(source, '.vite'), { recursive: true })
  await mkdir(join(source, 'assets'))
  const content = 'export const mountDashboardView = () => {}'
  const resources = [
    { name: 'assets/runtime.js', sha256: createHash('sha256').update(content).digest('hex') },
  ]
  const build = createHash('sha256').update(JSON.stringify(resources)).digest('hex')
  await writeFile(join(source, 'assets/runtime.js'), content)
  await writeFile(
    join(source, '.vite/manifest.json'),
    JSON.stringify({ 'runtime.mjs': { file: 'assets/runtime.js', isEntry: true } }),
  )
  await writeFile(
    join(source, 'template-catalog.json'),
    JSON.stringify({
      schema_version: 1,
      templates: [{ template_id: 'screen', renderer_build_id: build, asset_digests: resources }],
    }),
  )
  try {
    await run({ source, target, build, content })
  } finally {
    assert.equal(dirname(resolve(root)), resolve(tmpdir()))
    assert.ok(root.startsWith(join(tmpdir(), 'dashboard-publish-')))
    await rm(root, { recursive: true, force: true })
  }
}

test('publishes exact verified resources under build identity and supports identical reruns', async () => {
  await fixture(async ({ source, target, build, content }) => {
    const result = await publishRenderer(source, target)
    assert.equal(result.rendererBuildId, build)
    assert.equal(result.entry, `/${build}/assets/runtime.js`)
    assert.equal(await readFile(join(target, build, 'assets/runtime.js'), 'utf8'), content)
    assert.deepEqual(await publishRenderer(source, target), result)
  })
})

test('rejects source tampering and never replaces different published bytes', async () => {
  await fixture(async ({ source, target, build }) => {
    await publishRenderer(source, target)
    await writeFile(join(target, build, 'assets/runtime.js'), 'existing different bytes')
    await assert.rejects(publishRenderer(source, target))
    assert.equal(
      await readFile(join(target, build, 'assets/runtime.js'), 'utf8'),
      'existing different bytes',
    )
    await writeFile(join(source, 'assets/runtime.js'), 'tampered')
    await assert.rejects(publishRenderer(source, target))
  })
})

test('rejects traversal in resource paths before publication', async () => {
  await fixture(async ({ source, target }) => {
    const path = join(source, 'template-catalog.json')
    const catalog = JSON.parse(await readFile(path, 'utf8'))
    catalog.templates[0].asset_digests[0].name = '../outside.js'
    await writeFile(path, JSON.stringify(catalog))
    await assert.rejects(publishRenderer(source, target))
  })
})

test('published frontend resources match the generated build metadata byte for byte', async () => {
  const metadata = JSON.parse(
    await readFile(
      new URL(
        '../../web/app/components/enterprise/dashboards/renderer-build.json',
        import.meta.url,
      ),
      'utf8',
    ),
  )
  assert.equal(
    createHash('sha256').update(JSON.stringify(metadata.resources)).digest('hex'),
    metadata.rendererBuildId,
  )
  for (const item of metadata.resources) {
    const bytes = await readFile(
      new URL(
        `../../web/public/enterprise/renderers/${metadata.rendererBuildId}/${item.name}`,
        import.meta.url,
      ),
    )
    assert.equal(createHash('sha256').update(bytes).digest('hex'), item.sha256)
  }
})
