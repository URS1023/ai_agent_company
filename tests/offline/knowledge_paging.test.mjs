import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
const ts = require('typescript')
const source = fs.readFileSync(new URL('../../apps/web/assets/platform/knowledge.js', import.meta.url), 'utf8')
const ast = ts.createSourceFile('knowledge.js', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
const functionText = name => ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(ast)

test('knowledge list requests and renders the selected document page with total count', async () => {
  const requests = []
  let wired, markup = ''
  const page = { items: [], total: 130, page: 2, page_size: 50 }
  const context = vm.createContext({
    api: { get: async path => { requests.push(path); return path.includes('/documents') ? page : {} } },
    currentPage: () => 2, redirectEmptyPage: () => false, can: () => false,
    emptyState: () => '', pagination: value => `<nav>${value.total}</nav>`,
    wirePageNavigation: (_, value) => { wired = value }, $$: () => [],
    root: { insertAdjacentHTML: (_, html) => { markup += html } }, kb: { id: 'kb1' },
  })
  await vm.runInContext(functionText('renderDocuments') + '\nrenderDocuments(root, kb)', context)
  assert.ok(requests.includes('/api/knowledge/kb1/documents?page=2&page_size=50'))
  assert.equal(wired.total, 130)
  assert.ok(markup.includes('<nav>130</nav>'))
})

test('file picker checks membership across all KB pages and disables already imported candidates', async () => {
  const requests = []
  let options
  const picker = { options: [{}], disabled: true, innerHTML: '' }
  const search = { value: 'manual', addEventListener() {} }
  const status = { textContent: '' }
  const dialog = { contains: () => true, addEventListener() {} }
  const context = vm.createContext({
    can: () => false, createDocumentUploadQueue: () => ({}),
    openDialog: (_title, _html, config) => { options = config },
    field: () => '', input: () => '', esc: String, URLSearchParams, clearTimeout, setTimeout,
    $: selector => ({ '[name=file_query]': search, '[name=file_id]': picker, '[data-file-search-status]': status,
      '[data-existing-document]': {} }[selector]), $$: () => [],
    api: { get: async path => {
      requests.push(path)
      if (path.startsWith('/api/files')) return { total: 2, items: [{ id: 'already', name: 'Imported manual' }, { id: 'new', name: 'New manual' }] }
      // Imported document is beyond the list's current page. Distinct IDs also
      // handle historical duplicate documents without a pagination truncation.
      return { items: [], total: 201, existing_file_ids: ['already'] }
    } },
  })
  vm.runInContext(functionText('addDocumentDialog') + '\naddDocumentDialog({}, {id:"kb1"}, {})', context)
  await options.onReady(dialog)
  const params = new URL(requests[1], 'http://offline.invalid').searchParams
  assert.deepEqual(params.getAll('file_ids'), ['already', 'new'])
  assert.match(picker.innerHTML, /value="already" disabled/)
  assert.match(picker.innerHTML, /value="new" >New manual/)
  assert.equal(picker.disabled, false)
})
