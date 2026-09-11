// @vitest-environment node
import { proxyEnterprise } from '..'

const upstream = 'http://enterprise-api:8000'

// Management is browser-accessible; execution remains a worker operation.
describe('schedule management proxy', () => {
  beforeEach(() => vi.clearAllMocks())

  it.each([
    ['POST', 'v1/devices/device-1/alert/schedule', { expected_binding_revision: 1 }],
    ['POST', 'v1/devices/device-1/quality/schedule', { expected_binding_revision: 2 }],
    ['GET', 'v1/schedules/schedule-1', undefined],
    ['GET', 'v1/devices/device-1/alert/schedule/actors', undefined],
    ['GET', 'v1/devices/device-1/quality/schedule/actors', undefined],
    ['GET', 'v1/devices/device-1/alert/schedule', undefined],
    ['GET', 'v1/devices/device-1/quality/schedule', undefined],
    ['POST', 'v1/schedules/schedule-1/state', { expected_revision: 1, enabled: true }],
    ['POST', 'v1/schedules/schedule-1/state', { expected_revision: 2, enabled: false }],
  ])('should forward management %s %s unchanged', async (method, path, body) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ revision: 2 }))
    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path}`, {
        method,
        headers: {
          Origin: 'https://portal.test',
          'Content-Type': 'application/json',
          Authorization: 'Bearer session',
          Cookie: 'access_token=session; unrelated=secret',
          'X-CSRF-Token': 'csrf',
          'X-Workspace-ID': 'forged',
        },
        body: body ? JSON.stringify(body) : undefined,
      }),
      path.split('/'),
      { enabled: true, upstream, fetcher },
    )

    expect(response.status).toBe(200)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
    expect(fetcher).toHaveBeenCalledTimes(1)
    const [url, options] = fetcher.mock.calls[0]!
    expect(url).toBe(`${upstream}/enterprise/api/${path}`)
    expect(options?.method).toBe(method)
    const headers = new Headers(options?.headers)
    expect(headers.get('authorization')).toBe('Bearer session')
    expect(headers.get('cookie')).toBe('access_token=session')
    expect(headers.get('x-csrf-token')).toBe('csrf')
    expect(headers.has('x-workspace-id')).toBe(false)
    if (body) expect(await new Response(options?.body).json()).toEqual(body)
    else expect(options?.body).toBeUndefined()
  })

  it.each([
    ['POST', 'v1/schedules/poll'],
    ['POST', 'v1/devices/device-1/alert/schedule/actors'],
    ['GET', 'v1/devices/device-1/quality/schedule/actors/extra'],
    ['GET', 'v1/devices/device-1/unknown/schedule/actors'],
    ['POST', 'v1/schedules/schedule-1/tick'],
    ['POST', 'v1/schedules/schedule-1/run'],
    ['GET', 'v1/schedules'],
    ['POST', 'v1/schedules/schedule-1'],
    ['DELETE', 'v1/schedules/schedule-1'],
    ['PUT', 'v1/schedules/schedule-1/state'],
    ['GET', 'v1/schedules/schedule-1/state'],
    ['POST', 'v1/schedules/schedule-1/state/extra'],
    ['GET', `v1/schedules/${'a'.repeat(129)}`],
    ['GET', 'v1/schedules/a%2Fb'],
    ['POST', 'v1/devices/device-1/unknown/schedule'],
    ['DELETE', 'v1/devices/device-1/alert/schedule'],
    ['POST', 'v1/devices/device-1/alert/schedule/extra'],
  ])('should reject unregistered %s %s before transport', async (method, path) => {
    const fetcher = vi.fn<typeof fetch>()
    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path}`, {
        method,
        headers: { Origin: 'https://portal.test' },
      }),
      path.split('/'),
      { enabled: true, upstream, fetcher },
    )

    expect(response.status).toBe(404)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it.each(['v1/devices/device-1/alert/schedule', 'v1/schedules/schedule-1/state'])(
    'should require an origin before forwarding a schedule write %s',
    async (path) => {
      const fetcher = vi.fn<typeof fetch>()
      const response = await proxyEnterprise(
        new Request(`https://portal.test/enterprise/api/${path}`, {
          method: 'POST',
        }),
        path.split('/'),
        { enabled: true, upstream, fetcher },
      )

      expect(response.status).toBe(403)
      expect(fetcher).not.toHaveBeenCalled()
    },
  )
})
