import assert from 'node:assert/strict'
import test from 'node:test'
import { validatePush, verifyLogin } from './github-push-policy.mjs'

const url = 'https://github.com/URS1023/ai_agent_company.git'
const sha = '1'.repeat(40)
const zero = '0'.repeat(40)
const update = (target = 'refs/heads/main', local = sha) =>
  `refs/heads/codex/enterprise-platform ${local} ${target} ${zero}\n`

test('allows only the named repository and main destination, independently of local branch', () => {
  assert.equal(validatePush(url, update()).length, 1)
  assert.equal(validatePush(url.toLowerCase(), update()).length, 1)
  assert.deepEqual(validatePush(url, ''), [])
})

test('rejects other repositories, protocols and embedded credentials', () => {
  for (const target of [
    'https://github.com/langgenius/dify.git',
    'https://github.com/OTHER/ai_agent_company.git',
    'git@github.com:URS1023/ai_agent_company.git',
    'https://token@github.com/URS1023/ai_agent_company.git',
    `${url}?token=secret`,
  ])
    assert.throws(() => validatePush(target, update()), /repository_not_allowed/)
})

test('rejects non-main refs, mixed pushes, deletion and malformed protocol input', () => {
  for (const input of [
    update('refs/heads/dev'),
    update('refs/tags/main'),
    update() + update('refs/heads/dev'),
    update('refs/heads/main', zero),
    'invalid',
    update().repeat(10000),
  ])
    assert.throws(() => validatePush(url, input))
})

test('verifies the actual Git credential account and never exposes token content', async () => {
  const fetcher = async (target, options) => {
    assert.equal(target, 'https://api.github.com/user')
    assert.equal(options.headers.Authorization, 'Bearer fixture-token')
    return new Response(JSON.stringify({ login: 'URS1023' }))
  }
  assert.equal(await verifyLogin('fixture-token', fetcher), 'URS1023')
  for (const response of [
    new Response('{"login":"other"}'),
    new Response('private error body', { status: 401 }),
    new Response('invalid'),
  ])
    await assert.rejects(
      verifyLogin('fixture-token', async () => response),
      (error) =>
        !error.message.includes('fixture-token') && !error.message.includes('private error'),
    )
})

test('fails closed on missing credentials and network errors', async () => {
  await assert.rejects(
    verifyLogin('', async () => {
      throw new Error('must not fetch')
    }),
    /github_identity_unverified/,
  )
  await assert.rejects(
    verifyLogin('fixture-token', async () => {
      throw new Error('transport token=fixture-token')
    }),
    /github_identity_unverified/,
  )
})
