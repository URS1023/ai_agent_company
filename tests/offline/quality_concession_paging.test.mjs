import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
const ts = require('typescript')
const source = fs.readFileSync(new URL('../../apps/analytics/src/api/quality.ts', import.meta.url), 'utf8')
const functionSource = source.match(/export function readQualityArrayPage[\s\S]*?(?=export const quality)/)
assert.ok(functionSource, 'quality paging helpers must remain present in the API module')
const compiled = ts.transpileModule(functionSource[0].replace(/^export /gm, ''), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText + '\nglobalThis.readQualityArrayPage = readQualityArrayPage; globalThis.concessionStatusLabel = concessionStatusLabel; globalThis.normalizeConcessionRuleDraft = normalizeConcessionRuleDraft; globalThis.concessionEvaluationSnapshot = concessionEvaluationSnapshot'
const context = vm.createContext({})
vm.runInContext(compiled, context)

test('array pagination trims the extra probe row while preserving conservative continuation', () => {
  const page = Array.from({ length: 31 }, (_, index) => `row-${index}`)
  const result = context.readQualityArrayPage(page, 30)
  assert.equal(result.items.length, 30)
  assert.equal(result.items[29], 'row-29')
  assert.equal(result.hasMore, true)
  assert.equal(page.length, 31)
})

test('a short array page stops pagination without changing its values', () => {
  const page = ['one', 'two']
  const result = context.readQualityArrayPage(page, 30)
  assert.deepEqual(result.items, page)
  assert.equal(result.hasMore, false)
})

test('an exactly full page keeps a guarded next-page affordance', () => {
  const result = context.readQualityArrayPage(Array.from({ length: 30 }, (_, index) => index), 30)
  assert.equal(result.hasMore, true)
})

test('status labels and rule normalization preserve the approval workflow contract', () => {
  assert.equal(context.concessionStatusLabel('pending'), '待审批')
  assert.equal(context.concessionStatusLabel('approved'), '已批准')
  assert.equal(context.concessionStatusLabel('rejected'), '已驳回')
  const normalized = context.normalizeConcessionRuleDraft({
    standardMin: '', standardMax: '', minDeviation: '', maxDeviation: '', roundingMode: 'exact', roundingScale: 4,
  })
  assert.equal(normalized.standardMin, null)
  assert.equal(normalized.standardMax, null)
  assert.equal(normalized.minDeviation, null)
  assert.equal(normalized.maxDeviation, null)
  assert.equal(normalized.roundingScale, null)
})

test('failed retries identify the same manual business payload without its generated record id', () => {
  const first = context.concessionEvaluationSnapshot({
    requestKey: 'first-key', sampleId: 'sample-1', source: { kind: 'manual', recordId: 'manual-a', fields: { weight: 'manual.weight' } },
  })
  const retry = context.concessionEvaluationSnapshot({
    requestKey: 'retry-key', sampleId: 'sample-1', source: { kind: 'manual', recordId: 'manual-b', fields: { weight: 'manual.weight' } },
  })
  const changed = context.concessionEvaluationSnapshot({
    requestKey: 'retry-key', sampleId: 'sample-2', source: { kind: 'manual', recordId: 'manual-b', fields: { weight: 'manual.weight' } },
  })
  assert.equal(first, retry)
  assert.notEqual(first, changed)
})

test('failed approval keeps the typed decision note for retry', async () => {
  const panelSource = fs.readFileSync(new URL('../../apps/analytics/src/components/quality/QualityConcessionPanel.vue', import.meta.url), 'utf8')
  const makeDecisionSource = panelSource.match(/async function makeDecision[\s\S]*?\n}\n/)
  assert.ok(makeDecisionSource, 'approval handler must remain present in the panel')
  const compiledHandler = ts.transpileModule(makeDecisionSource[0], {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
  }).outputText + '\nglobalThis.makeDecision = makeDecision'
  const notes = { 'request-1': '保留这条审批依据' }
  const contextWithFailure = vm.createContext({ decisionNotes: notes, decide: async () => false })
  vm.runInContext(compiledHandler, contextWithFailure)
  await contextWithFailure.makeDecision({ id: 'request-1' }, 'approved')
  assert.equal(notes['request-1'], '保留这条审批依据')

  const contextWithSuccess = vm.createContext({ decisionNotes: notes, decide: async () => true })
  vm.runInContext(compiledHandler, contextWithSuccess)
  await contextWithSuccess.makeDecision({ id: 'request-1' }, 'approved')
  assert.equal(notes['request-1'], undefined)
})
