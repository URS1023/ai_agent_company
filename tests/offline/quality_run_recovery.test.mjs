import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../../apps/analytics/package.json', import.meta.url))
const ts = require('typescript')
const source = fs.readFileSync(new URL('../../apps/analytics/src/utils/qualityRunRecovery.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source.replace(/^export /gm, '') + '\nglobalThis.subject={createQualityRunRecoveryStore,qualityRunRecoveryKey,purgeExpiredQualityRunRecovery,purgeOtherQualityRunRecoveryScopes,clearQualityRunRecovery,QUALITY_RUN_RECOVERY_TTL_MS};', {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText
const context = vm.createContext({ Date, JSON, Number, RegExp, Set, encodeURIComponent, decodeURIComponent })
vm.runInContext(compiled, context)
const recovery = context.subject

class MemoryStorage {
  values = new Map()
  get length() { return this.values.size }
  key(index) { return [...this.values.keys()][index] ?? null }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null }
  setItem(key, value) { this.values.set(key, String(value)) }
  removeItem(key) { this.values.delete(key) }
}

const payload = {
  version: 7, publishedRevision: 3, idempotencyKey: 'quality-key-123', confirmWrites: true, timeoutSeconds: 120,
  groups: [{ nodeId: 'detector_a', samples: [{ sampleId: 'sample-01', imageFileId: 'file_01' }] }],
}

test('disabled browser storage does not prevent the quality page from opening', () => {
  const denied = new Proxy({}, { get() { throw new Error('SecurityError') } })
  const store = recovery.createQualityRunRecoveryStore('scope:quality-run-pending:key', denied)
  assert.equal(store.load('flow', '/quality'), null)
  assert.equal(store.save('flow', '/quality', payload), false)
  assert.doesNotThrow(() => store.clear())
  assert.doesNotThrow(() => recovery.purgeExpiredQualityRunRecovery(denied))
  assert.doesNotThrow(() => recovery.purgeOtherQualityRunRecoveryScopes('scope', denied))
  assert.doesNotThrow(() => recovery.clearQualityRunRecovery(denied))
})

test('recovery keeps the exact request key and allow-listed request without file bytes or secrets', () => {
  const storage = new MemoryStorage()
  const key = recovery.qualityRunRecoveryKey('cloud-brain:workspace:user:quality-run-pending', '/quality/inspections', 'flow-1')
  const store = recovery.createQualityRunRecoveryStore(key, storage, () => 1000)
  assert.equal(store.save('flow-1', '/quality/inspections', { ...payload, secret: 'must-not-persist', imageBytes: 'not-a-file' }), true)
  const raw = JSON.parse(storage.getItem(key))
  assert.equal(raw.payload.secret, undefined)
  assert.equal(raw.payload.imageBytes, undefined)
  assert.equal(JSON.stringify(store.load('flow-1', '/quality/inspections').payload), JSON.stringify(payload))
  assert.equal(store.load('other-flow', '/quality/inspections'), null)
})

test('recovery is isolated by business entry and identity scope', () => {
  const storage = new MemoryStorage()
  const first = recovery.qualityRunRecoveryKey('cloud-brain:w1:u1:quality-run-pending', '/quality/inspections', 'flow-1')
  const second = recovery.qualityRunRecoveryKey('cloud-brain:w2:u2:quality-run-pending', '/quality/inspections', 'flow-1')
  recovery.createQualityRunRecoveryStore(first, storage, () => 1000).save('flow-1', '/quality/inspections', payload)
  recovery.createQualityRunRecoveryStore(second, storage, () => 1000).save('flow-1', '/quality/inspections', { ...payload, idempotencyKey: 'quality-key-456' })
  assert.equal(storage.length, 2)
  recovery.purgeOtherQualityRunRecoveryScopes('cloud-brain:w2:u2:quality-run-pending:', storage)
  assert.equal(storage.getItem(first), null)
  assert.notEqual(storage.getItem(second), null)
})

test('expired or malformed recovery is removed and logout cleanup clears the scope', () => {
  const storage = new MemoryStorage()
  let now = 1000
  const key = recovery.qualityRunRecoveryKey('cloud-brain:w1:u1:quality-run-pending', '/quality/inspections', 'flow-1')
  const store = recovery.createQualityRunRecoveryStore(key, storage, () => now)
  store.save('flow-1', '/quality/inspections', payload)
  now += recovery.QUALITY_RUN_RECOVERY_TTL_MS + 1
  recovery.purgeExpiredQualityRunRecovery(storage, () => now)
  assert.equal(storage.getItem(key), null)
  storage.setItem(key, '{bad json')
  recovery.purgeExpiredQualityRunRecovery(storage, () => now)
  assert.equal(storage.getItem(key), null)
  storage.setItem(key, '{}')
  recovery.clearQualityRunRecovery(storage)
  assert.equal(storage.length, 0)
})
