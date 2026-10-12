import test from 'node:test'
import assert from 'node:assert/strict'
import { createGenerationRequester } from '../../apps/analytics/src/utils/generationRequest.js'

test('double clicks share one request and transport retries keep the original payload and key', async () => {
  let fail, id = 0
  const calls = []
  const request = createGenerationRequester({ createId: () => `id${++id}`, send: (_kind, body) => {
    calls.push(body)
    return calls.length === 1 ? new Promise((_, reject) => { fail = reject }) : Promise.resolve({ success: true })
  } })
  const first = request('screen', { theme: '需求\n本次设计种子：a\n', generationSeed: 'a' })
  assert.equal(request('screen', { theme: '需求\n本次设计种子：b\n', generationSeed: 'b' }), first)
  await Promise.resolve()
  fail(new Error('timeout'))
  await assert.rejects(first, /timeout/)
  assert.equal(calls.length, 1) // no automatic retry
  await request('screen', { theme: '需求\n本次设计种子：c\n', generationSeed: 'c' })
  assert.deepEqual(calls[1], calls[0])
  await request('screen', { theme: '需求\n本次设计种子：d\n', generationSeed: 'd' })
  assert.notEqual(calls[2].sessionId, calls[0].sessionId) // explicit new generation after success
})

test('changed requirements and workspace isolate requests; definite failures allow a fresh key', async () => {
  let id = 0, workspace = 'a'
  const calls = []
  const request = createGenerationRequester({ createId: () => `id${++id}`, scope: () => workspace,
    send: async (_, body) => { calls.push(body); throw { response: { status: body.theme === 'invalid' ? 422 : 504 } } } })
  for (const theme of ['old', 'new', 'invalid', 'invalid']) await assert.rejects(request('screen', { theme }))
  workspace = 'b'
  await assert.rejects(request('screen', { theme: 'old' }))
  assert.equal(new Set(calls.map(body => body.sessionId)).size, 5)
})
