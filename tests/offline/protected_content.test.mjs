import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import { contentUrl, fetchContent } from '../../apps/web/assets/platform/protected-content.js'

test('protected content accepts only same-origin content routes and drops legacy URL secrets', () => {
  const origin = 'http://localhost:8011'
  for (const url of ['https://example.org/api/files/a/download', '//evil.test/api/files/a/download', '/api/files', '/api/files/a/delete', 'javascript:alert(1)']) {
    assert.equal(contentUrl(url, origin), null)
  }
  const url = contentUrl('/api/files/a/download?token=old-secret&workspace_id=ws', origin)
  assert.equal(url.searchParams.has('token'), false)
  assert.equal(url.searchParams.get('workspace_id'), 'ws')
})

test('fetch sends credentials in headers and preserves the link workspace', async t => {
  globalThis.location = { origin: 'http://localhost:8011' }
  globalThis.localStorage = { getItem: key => ({ agent_token: 'test-only-secret', agent_workspace_id: 'current-ws' })[key] }
  let call
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    call = { url, options }
    return new Response(new Blob(['offline'], { type: 'image/png' }), { headers: { 'Content-Disposition': "attachment; filename*=UTF-8''%E5%9B%BE.png" } })
  })
  const result = await fetchContent('/api/artifacts/a/content?token=old-secret&workspace_id=link-ws')
  assert.equal(call.options.headers.Authorization, 'Bearer test-only-secret')
  assert.equal(call.options.headers['X-Workspace-Id'], 'link-ws')
  assert.equal(call.url.includes('secret'), false)
  assert.equal(result.name, '图.png')
  assert.equal(await result.blob.text(), 'offline')
})

test('actual chat URL and image markup builders never embed a login token', () => {
  const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
  const ts = require('typescript')
  const source = fs.readFileSync(new URL('../../apps/web/app.js', import.meta.url), 'utf8')
  const ast = ts.createSourceFile('app.js', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
  const code = ast.statements.filter(node => ts.isFunctionDeclaration(node) && ['authUrl', 'artifactImageSource'].includes(node.name?.text)).map(node => node.getText(ast)).join('\n')
  const context = vm.createContext({ URL, location: { origin: 'http://localhost:8011' }, localStorage: { getItem: () => 'ws' }, esc: x => x })
  vm.runInContext(code, context)
  assert.equal(context.authUrl('/api/files/a/download?token=secret'), '/api/files/a/download?workspace_id=ws')
  assert.equal(context.authUrl('//example.org/a'), 'http://example.org/a')
  assert.equal(context.authUrl('javascript:alert(1)'), '#')
  assert.match(context.artifactImageSource('/api/artifacts/a/content'), /^data-content-src=/)
  assert.match(context.artifactImageSource('https://example.org/public.png'), /^src=/)
})
