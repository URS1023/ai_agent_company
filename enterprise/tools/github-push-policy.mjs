// Local pre-push guard for the user's explicit destination/account policy.
// This does not modify GitHub branch protection or replace server permissions.
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const REPOSITORY = 'https://github.com/URS1023/ai_agent_company.git'
const oid = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/

export function validatePush(remoteUrl, input) {
  let url
  try {
    url = new URL(remoteUrl)
  } catch {
    throw new Error('repository_not_allowed')
  }
  if (
    url.protocol !== 'https:' ||
    url.hostname !== 'github.com' ||
    url.port ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname.toLowerCase().replace(/\.git$/, '') !== '/urs1023/ai_agent_company'
  )
    throw new Error('repository_not_allowed')
  if (Buffer.byteLength(input) > 65536) throw new Error('push_protocol_invalid')
  return input
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      const fields = line.split(' ')
      if (fields.length !== 4) throw new Error('push_protocol_invalid')
      const [localRef, localOid, remoteRef, remoteOid] = fields
      if (remoteRef !== 'refs/heads/main') throw new Error('only_main_destination_allowed')
      if (!oid.test(localOid) || !oid.test(remoteOid)) throw new Error('push_protocol_invalid')
      if (/^0+$/.test(localOid)) throw new Error('main_deletion_not_allowed')
      if (localRef !== 'HEAD' && !localRef.startsWith('refs/heads/'))
        throw new Error('local_branch_required')
      return { localOid, remoteOid }
    })
}

export async function verifyLogin(token, fetcher = fetch) {
  if (!token) throw new Error('github_identity_unverified')
  try {
    const response = await fetcher('https://api.github.com/user', {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'User-Agent': 'enterprise-main-push-policy',
      },
      signal: AbortSignal.timeout(15000),
      redirect: 'error',
    })
    if (!response.ok) throw new Error()
    const data = await response.json()
    if (typeof data.login !== 'string' || data.login.toLowerCase() !== 'urs1023') throw new Error()
    return data.login
  } catch {
    throw new Error('github_identity_unverified')
  }
}

function credentialToken() {
  try {
    const result = execFileSync(
      'git',
      ['-c', 'credential.interactive=never', 'credential', 'fill'],
      {
        input: `url=${REPOSITORY}\n\n`,
        encoding: 'utf8',
        windowsHide: true,
        timeout: 20000,
        stdio: ['pipe', 'pipe', 'pipe'],
        maxBuffer: 65536,
        env: { ...process.env, GCM_INTERACTIVE: 'never', GIT_TERMINAL_PROMPT: '0' },
      },
    )
    return (
      result
        .split(/\r?\n/)
        .find((line) => line.startsWith('password='))
        ?.slice(9) ?? ''
    )
  } catch {
    throw new Error('github_identity_unverified')
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const updates = validatePush(process.argv[3] ?? '', readFileSync(0, 'utf8'))
    for (const { localOid, remoteOid } of updates) {
      if (!/^0+$/.test(remoteOid)) {
        try {
          execFileSync('git', ['merge-base', '--is-ancestor', remoteOid, localOid], {
            stdio: 'pipe',
            windowsHide: true,
            timeout: 15000,
          })
        } catch {
          throw new Error('fetch_main_and_resolve_history_before_push')
        }
      }
    }
    if (updates.length) {
      const login = await verifyLogin(credentialToken())
      process.stdout.write(`Verified GitHub account ${login}; destination ai_agent_company/main.\n`)
    }
  } catch (error) {
    process.stderr.write(
      `Push stopped: ${error instanceof Error ? error.message : 'push_policy_failed'}.\n`,
    )
    process.exitCode = 1
  }
}
