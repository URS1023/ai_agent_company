import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import { withRequestDeadline } from '../../apps/web/assets/platform/request-deadline.js'
import { api, allPages } from '../../apps/web/assets/platform/api.js'
import { currentPage, pageUrl, redirectEmptyPage } from '../../apps/web/assets/platform/list-navigation.js'

const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
const ts = require('typescript')
const read = path => fs.readFileSync(new URL('../../' + path, import.meta.url), 'utf8')

test('deadline aborts stuck responses without repeating requests or claiming writes failed', async () => {
  let calls = 0, signal
  await assert.rejects(withRequestDeadline(s => { calls++; signal = s; return new Promise(() => {}) },
    { timeoutMs: 10, method: 'POST' }), error => error.name === 'TimeoutError' && error.message.includes('服务器可能仍在处理'))
  assert.equal(calls, 1)
  assert.equal(signal.aborted, true)
})

test('external cancellation prevents a pre-aborted request and propagates later cancellation', async () => {
  const controller = new AbortController()
  controller.abort()
  let calls = 0
  await assert.rejects(withRequestDeadline(() => { calls++ }, { signal: controller.signal }), { name: 'AbortError' })
  assert.equal(calls, 0)
  const later = new AbortController()
  const request = withRequestDeadline(() => new Promise(() => {}), { signal: later.signal })
  later.abort()
  await assert.rejects(request, { name: 'AbortError' })
})

test('API deadline includes response body parsing and forwards write options', async t => {
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, headers: new Headers({ 'Content-Type': 'application/json' }), json: () => new Promise(() => {}) }))
  const client = { request: api.request, post: api.post, token: '', workspace: '' }
  await assert.rejects(client.post('/offline-only', {}, { timeoutMs: 10 }), { name: 'TimeoutError' })
  assert.equal(fetch.mock.callCount(), 1)
})

test('pagination rejects invalid page numbers, preserves filters, and clamps stale last pages', () => {
  for (const value of ['-1', 'NaN', 'Infinity', '2.5', '9007199254740992']) assert.equal(currentPage('?page=' + value), 1)
  assert.equal(currentPage('?page=3'), 3)
  assert.equal(pageUrl(3, 'http://localhost/system/security?tab=devices&status=pending#list'), '/system/security?tab=devices&status=pending&page=3#list')
  let destination
  globalThis.location = { search: '?page=4', href: 'http://localhost/system/operations?tab=messages&page=4', replace: url => { destination = url } }
  assert.equal(redirectEmptyPage({ total: 50 }, 50), true)
  assert.equal(destination, '/system/operations?tab=messages&page=1')
})

test('option loading includes files and members beyond the first hundred', async t => {
  const pages = []
  t.mock.method(api, 'get', async path => {
    const page = Number(new URL(path, 'http://localhost').searchParams.get('page'))
    pages.push(page)
    return { total: 101, items: page === 1 ? Array.from({ length: 100 }, (_, id) => ({ id })) : [{ id: 100 }] }
  })
  assert.equal((await allPages('/offline-only'))[100].id, 100)
  assert.deepEqual(pages, [1, 2])
})

test('actual message/device/event renderers request selected pages and provide navigation', async () => {
  for (const [file, name, expected] of [
    ['system-tools.js', 'renderMessages', '/inbox?limit=50&offset=100'],
    ['security-governance.js', 'renderDevices', '/security/devices?page=3&page_size=100&status=pending'],
    ['security-governance.js', 'renderEvents', '/security/login-events?page=3&page_size=100&outcome=failed'],
  ]) {
    const source = read('apps/web/assets/platform/' + file)
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
    const fn = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(ast)
    let requested, wired, markup = ''
    const result = { items: [], total: 305, unread: 5, page: 3, page_size: 100 }
    const context = vm.createContext({ BASE: '/offline', currentPage: () => 3, redirectEmptyPage: () => false,
      location: { search: '?page=3&status=pending&outcome=failed' }, URLSearchParams,
      api: { get: async path => { requested = path; return result } }, allPages: async () => [],
      compactStatus: () => '', select: () => '', emptyState: () => '', pagination: () => '<nav>pages</nav>',
      wirePageNavigation: (_, value) => { wired = value }, $: () => ({ addEventListener() {} }), $$: () => [],
      root: { insertAdjacentHTML: (_, html) => { markup += html } },
    })
    await vm.runInContext(fn + '\n' + name + '(root)', context)
    assert.equal(requested, '/offline' + expected)
    assert.equal(wired.page, 3)
    assert.equal(wired.total, 305)
    assert.match(markup, /<nav>pages<\/nav>/)
  }
})

test('actual method page renders bounded results and wires the server page', async () => {
  const source = read('apps/web/assets/platform/capability-governance.js')
  const ast = ts.createSourceFile('methods.js', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
  const fn = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'renderMethods').getText(ast)
  const paths = [], root = { innerHTML: '' }
  let wired
  const context = vm.createContext({ BASE: '/offline', location: { search: '?page=3&keyword=needle' }, currentPage: () => 3,
    URLSearchParams, encodeURIComponent, root, get: async path => { paths.push(path); return path.includes('/methods') ? { items: [], total: 101, limit: 50, offset: 100 } : { items: [] } },
    can: () => false, esc: value => value, select: () => '', emptyState: () => '', pageHeader: () => '', capabilityGovernanceTabs: () => '',
    pagination: () => '<nav>pages</nav>', wirePageNavigation: (_, value) => { wired = value }, $: () => null, $$: () => [],
  })
  await vm.runInContext(fn + '\nrenderMethods(root)', context)
  assert.match(paths[0], /limit=50&offset=100&keyword=needle/)
  assert.equal(wired.page, 3)
  assert.match(root.innerHTML, /<nav>pages<\/nav>/)
})
