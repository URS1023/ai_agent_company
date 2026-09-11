import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { locateRendererAssets } from './renderer-compat.mjs'

test('map asset resolution follows the renderer bundle, not the current dashboard route', async () => {
  const source = await readFile(
    new URL('../lynx-renderer/src/components/JimuChart.vue', import.meta.url),
    'utf8',
  )
  const transformed = locateRendererAssets(source)
  assert.ok(
    transformed.includes(
      "new URL(/* @vite-ignore */ '../jimu-screen/china.json', import.meta.url)",
    ),
  )
  assert.equal(transformed.includes('import.meta.env.BASE_URL'), false)
  const bundle = 'https://portal.test/base/enterprise/renderers/build/assets/Preview.js'
  assert.equal(
    new URL('../jimu-screen/china.json', bundle).href,
    'https://portal.test/base/enterprise/renderers/build/jimu-screen/china.json',
  )
})

test('map compatibility transform rejects missing or ambiguous upstream targets', () => {
  assert.throws(() => locateRendererAssets('changed upstream component'))
  const target = 'fetch(`${import.meta.env.BASE_URL}jimu-screen/china.json`)'
  assert.throws(() => locateRendererAssets(`${target}\n${target}`))
})
