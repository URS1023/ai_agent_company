import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
const ts = require('typescript')
const vue = require('vue')
function client(get) {
  const source = fs.readFileSync(new URL('../../apps/analytics/src/composables/useDataSources.ts', import.meta.url), 'utf8')
    .replace(/^import .+\r?\n/gm, '').replace('export function', 'function')
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText
  const context = vm.createContext({ shallowRef: vue.shallowRef, shallowReadonly: vue.shallowReadonly, URLSearchParams, get })
  return vm.runInContext(code + '\nuseDataSources()', context)
}

test('datasource management loads one server page and retains bounded paging on refresh', async () => {
  const paths = []
  const api = client(async path => {
    paths.push(path)
    return { data: { items: [{ id: 'on-page-3' }], total: 101, page: 3, pageSize: 12 } }
  })
  await api.load({ page: 3, pageSize: 12, keyword: '生产' })
  assert.equal(api.sources.value.length, 1)
  assert.equal(api.total.value, 101)
  assert.equal(api.page.value, 3)
  assert.equal(new URL(paths[0], 'http://localhost').searchParams.get('keyword'), '生产')
  assert.match(paths[0], /\/datasources\/page\?page=3&page_size=12/)
  await api.load()
  assert.equal(paths[1], paths[0])
})

test('rapid page changes ignore stale success and failure and preserve latest loading state', async () => {
  const requests = []
  const api = client(() => new Promise((resolve, reject) => requests.push({ resolve, reject })))
  const first = api.load({ page: 2, pageSize: 12 })
  const second = api.load({ page: 1, pageSize: 12 })
  requests[0].reject(new Error('old request failure'))
  await first
  assert.equal(api.loading.value, true)
  assert.equal(api.loadError.value, '')
  requests[1].resolve({ data: { items: [{ id: 'latest' }], total: 14, page: 1, pageSize: 12 } })
  await second
  assert.equal(api.sources.value[0].id, 'latest')
  assert.equal(api.loading.value, false)
})

test('server-clamped page is retained after deleting the final item', async () => {
  const paths = []
  const api = client(async path => {
    paths.push(path)
    return { data: { items: [], total: 0, page: 1, pageSize: 12 } }
  })
  await api.load({ page: 8, pageSize: 12 })
  await api.load()
  assert.equal(api.page.value, 1)
  assert.match(paths[1], /page=1&/)
})

for (const status of [404, 405]) {
  test(`older API ${status} falls back to authorized list with search and page clamping`, async () => {
    const paths = []
    const api = client(async path => {
      paths.push(path)
      if (path.includes('/page?')) throw Object.assign(new Error('Method Not Allowed'), { response: { status } })
      return { success: true, data: [{ id: '1', name: '生产 A' }, { id: '2', name: '财务' }, { id: '3', name: '生产 B' }] }
    })
    await api.load({ page: 8, pageSize: 1, keyword: '生产' })
    assert.equal(api.sources.value[0].id, '3')
    assert.equal(api.total.value, 2)
    assert.equal(api.page.value, 2)
    assert.equal(api.loadError.value, '')
    await api.load({ page: 1, pageSize: 12, keyword: '不存在' })
    assert.equal(api.total.value, 0)
    assert.equal(api.page.value, 1)
    assert.equal(api.sources.value.length, 0)
    assert.equal(paths.length, 3)
    assert.equal(paths[2], '/api/v1/analytics/datasources')
  })
}

for (const status of [401, 403, 500, undefined]) {
  test(`API failure ${status} is reported without fallback or retry`, async () => {
    let calls = 0
    const api = client(async () => {
      calls++
      throw Object.assign(new Error('original failure'), { response: { status } })
    })
    await assert.rejects(api.load(), /original failure/)
    assert.equal(calls, 1)
    assert.match(api.loadError.value, /original failure/)
    assert.equal(api.loading.value, false)
  })
}

test('stale missing-route errors cannot switch a newer successful request to the legacy API', async () => {
  const requests = []
  const api = client(path => new Promise((resolve, reject) => requests.push({ path, resolve, reject })))
  const old = api.load({ page: 2, pageSize: 12 })
  const latest = api.load({ page: 1, pageSize: 12 })
  requests[1].resolve({ data: { items: [{ id: 'latest' }], total: 1, page: 1, pageSize: 12 } })
  await latest
  requests[0].reject(Object.assign(new Error('old 405'), { response: { status: 405 } }))
  await old
  const refreshed = api.load()
  assert.match(requests[2].path, /\/datasources\/page\?/)
  requests[2].resolve({ data: { items: [{ id: 'latest' }], total: 1, page: 1, pageSize: 12 } })
  await refreshed
  assert.equal(api.sources.value[0].id, 'latest')
})
