import type { ClientLink } from '@orpc/client'
import type { ConsoleClientContext } from '@/service/client'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { withEnterpriseBusinessLink } from '../link'

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }))

vi.mock('@/service/base', () => ({ request: requestMock }))
vi.mock('@/env', () => ({ env: { NEXT_PUBLIC_BASE_PATH: '/company' } }))

// The wrapper only owns business paths; original console and paid enterprise remain untouched.
describe('withEnterpriseBusinessLink', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('should include the configured Next base path when requesting a generated business route', async () => {
    requestMock.mockResolvedValue(Response.json({ items: [], offset: 0, limit: 20, total: 0 }))
    const nativeCall = vi.fn<ClientLink<ConsoleClientContext>['call']>()
    const link = withEnterpriseBusinessLink(
      { call: nativeCall },
      () => new URL('https://portal.example/'),
    )

    await link.call(['business', 'devices', 'get'], {}, { context: {} })

    expect(requestMock.mock.calls[0]?.[0]).toBe(
      'https://portal.example/company/enterprise/api/v1/devices',
    )
    expect(nativeCall).not.toHaveBeenCalled()
  })

  it('should create a workbench branch through the existing authenticated request boundary', async () => {
    const appId = '00000000-0000-0000-0000-000000000001'
    const branch = {
      scope: {
        workspace_id: 'workspace',
        actor_id: 'actor',
        installed_app_id: appId,
        branch_id: 'new-chat',
      },
      revision: 0,
      state: 'ready',
      conversation_id: null,
      head_message_id: null,
      inflight_client_message_id: null,
      origin: null,
    }
    requestMock.mockResolvedValue(Response.json(branch))
    const nativeCall = vi.fn<ClientLink<ConsoleClientContext>['call']>()
    const link = withEnterpriseBusinessLink(
      { call: nativeCall },
      () => new URL('https://portal.example/'),
    )

    const result = await link.call(
      ['business', 'workbench', 'apps', 'byInstalledAppId', 'branches', 'post'],
      { params: { installed_app_id: appId }, body: { branch_id: 'new-chat' } },
      { context: { silent: true } },
    )

    expect(result).toEqual(branch)
    expect(nativeCall).not.toHaveBeenCalled()
    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    expect(url).toBe(
      `https://portal.example/company/enterprise/api/v1/workbench/apps/${appId}/branches`,
    )
    expect(options).toMatchObject({ fetchCompat: true, silent: true })
    expect(options.request.method).toBe('POST')
    expect(await options.request.clone().json()).toEqual({ branch_id: 'new-chat' })
  })

  it.each([['apps', 'get'], ['enterprise', 'subscription', 'get'], []])(
    'should pass the original path and request unchanged to the native link for %j',
    async (...path) => {
      const nativeCall = vi
        .fn<ClientLink<ConsoleClientContext>['call']>()
        .mockResolvedValue('native-result')
      const rootURL = vi.fn(() => new URL('https://portal.example/'))
      const link = withEnterpriseBusinessLink({ call: nativeCall }, rootURL)
      const input = { query: { page: 2 } }
      const options = { context: { silent: true }, signal: new AbortController().signal }

      expect(await link.call(path, input, options)).toBe('native-result')

      expect(nativeCall).toHaveBeenCalledWith(path, input, options)
      expect(rootURL).not.toHaveBeenCalled()
      expect(requestMock).not.toHaveBeenCalled()
    },
  )

  it('should allow a later explicit call after lazy link initialization fails', async () => {
    const nativeCall = vi.fn<ClientLink<ConsoleClientContext>['call']>()
    const rootURL = vi
      .fn(() => new URL('https://portal.example/'))
      .mockImplementationOnce(() => {
        throw new Error('URL not ready')
      })
    const link = withEnterpriseBusinessLink({ call: nativeCall }, rootURL)

    await expect(link.call(['business', 'devices', 'get'], {}, { context: {} })).rejects.toThrow(
      'URL not ready',
    )
    expect(requestMock).not.toHaveBeenCalled()
    requestMock.mockResolvedValue(Response.json({ items: [], offset: 0, limit: 20, total: 0 }))
    await expect(
      link.call(['business', 'devices', 'get'], {}, { context: {} }),
    ).resolves.toMatchObject({ total: 0 })

    expect(rootURL).toHaveBeenCalledTimes(2)
    expect(requestMock).toHaveBeenCalledTimes(1)
  })
})
