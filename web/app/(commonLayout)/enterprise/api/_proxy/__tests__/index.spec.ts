// @vitest-environment node
import { proxyEnterprise } from '..'

const upstream = 'http://enterprise-api:8000'

describe('same-origin enterprise API proxy', () => {
  beforeEach(() => vi.restoreAllMocks())

  it.each([
    [
      'POST',
      'v1/workflow-setups/setup-1/provisioning',
      { config_ref: 'config-1', config_revision: 2 },
    ],
    ['GET', 'v1/workflow-provisioning/provisioning-1', undefined],
    ['POST', 'v1/workflow-provisioning/provisioning-1/advance', { expected_revision: 3 }],
    ['POST', 'v1/workflow-provisioning/provisioning-1/enrollment', {}],
    ['GET', 'v1/workflow-provisioning/provisioning-1/enrollment', undefined],
    ['GET', 'v1/workflow-enrollments/enrollment-1', undefined],
    ['GET', 'v1/workflow-enrollments/enrollment-1/activation', undefined],
    ['GET', 'v1/workflow-enrollments/enrollment-1/activation-specifications', undefined],
    [
      'POST',
      'v1/workflow-enrollments/enrollment-1/activation',
      { specification_revision: 'spec-1' },
    ],
    ['GET', 'v1/workflow-activations/activation-1', undefined],
    ['POST', 'v1/workflow-activations/activation-1/revoke', { expected_revision: 1 }],
    ['POST', 'v1/workflow-enrollments/enrollment-1/advance', { expected_revision: 1 }],
  ])('should forward exact provisioning operation %s %s', async (method, path, body) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ revision: 4 }))
    const request = new Request(`https://portal.test/enterprise/api/${path}`, {
      method,
      body: body ? JSON.stringify(body) : undefined,
      headers: {
        Origin: 'https://portal.test',
        'Content-Type': 'application/json',
        Authorization: 'Bearer session',
        Cookie: 'access_token=session; unrelated=secret',
        'X-CSRF-Token': 'csrf',
        'Idempotency-Key': 'provision-request-1',
        'X-Enterprise-Expected-Workspace': 'forged',
      },
    })

    const response = await proxyEnterprise(request, path.split('/'), {
      enabled: true,
      upstream,
      fetcher,
    })

    expect(response.status).toBe(200)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
    expect(fetcher).toHaveBeenCalledTimes(1)
    const [url, options] = fetcher.mock.calls[0]!
    expect(String(url)).toBe(`${upstream}/enterprise/api/${path}`)
    expect(options?.method).toBe(method)
    expect(options?.cache).toBe('no-store')
    const headers = new Headers(options?.headers)
    expect(headers.get('authorization')).toBe('Bearer session')
    expect(headers.get('cookie')).toBe('access_token=session')
    expect(headers.get('x-csrf-token')).toBe('csrf')
    expect(headers.get('idempotency-key')).toBe('provision-request-1')
    expect(headers.has('x-enterprise-expected-workspace')).toBe(false)
    if (body) expect(await new Response(options?.body).json()).toEqual(body)
  })

  it.each([
    ['POST', 'v1/workflow-setups/setup-1/provisioning-profiles'],
    ['PUT', 'v1/workflow-setups/setup-1/provisioning-profiles'],
    ['GET', 'v1/workflow-setups/setup-1/provisioning-profiles/extra'],
    ['PUT', 'v1/workflow-setups/setup-1/provisioning'],
    ['POST', 'v1/workflow-provisioning/provisioning-1'],
    ['DELETE', 'v1/workflow-provisioning/provisioning-1'],
    ['GET', 'v1/workflow-provisioning/provisioning-1/advance'],
    ['POST', 'v1/workflow-provisioning/provisioning-1/retry'],
    ['POST', 'v1/workflow-provisioning/provisioning-1/advance/extra'],
    ['POST', 'v1/workflow-setups/setup-1/provisioning/extra'],
    ['GET', 'v1/workflow-provisioning'],
    ['GET', `v1/workflow-provisioning/${'a'.repeat(129)}`],
    ['GET', 'v1/workflow-provisioning/a%2Fb'],
    ['DELETE', 'v1/workflow-provisioning/provisioning-1/enrollment'],
    ['POST', 'v1/workflow-enrollments/enrollment-1'],
    ['DELETE', 'v1/workflow-enrollments/enrollment-1'],
    ['GET', 'v1/workflow-enrollments/enrollment-1/advance'],
    ['POST', 'v1/workflow-enrollments/enrollment-1/retry'],
    ['DELETE', 'v1/workflow-enrollments/enrollment-1/activation'],
    ['POST', 'v1/workflow-enrollments/enrollment-1/activation-specifications'],
    ['GET', 'v1/workflow-enrollments/enrollment-1/activation-specifications/extra'],
    ['GET', 'v1/workflow-activations/activation-1/revoke'],
    ['DELETE', 'v1/workflow-activations/activation-1'],
    ['POST', 'v1/workflow-activations/activation-1'],
    ['POST', 'v1/workflow-activations/activation-1/reactivate'],
    ['POST', 'v1/workflow-activations/activation-1/revoke/extra'],
    ['GET', 'v1/workflow-activations/a%2Fb'],
    ['GET', `v1/workflow-activations/${'a'.repeat(129)}`],
    ['POST', 'v1/workflow-enrollments/enrollment-1/advance/extra'],
    ['GET', 'v1/workflow-enrollments/a%2Fb'],
    ['GET', `v1/workflow-enrollments/${'a'.repeat(129)}`],
  ])(
    'should reject unregistered provisioning operation %s %s before transport',
    async (method, path) => {
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
    },
  )

  it.each(['provisioning', 'provisioning-profiles'])(
    'should forward scoped setup %s reads with private caching and unchanged pagination',
    async (operation) => {
      const path = ['v1', 'workflow-setups', 'setup-1', operation]
      const query = operation === 'provisioning' ? '?offset=20&limit=10' : ''
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValue(Response.json({ items: [], total: 0 }))
      const response = await proxyEnterprise(
        new Request(`https://portal.test/enterprise/api/${path.join('/')}${query}`, {
          headers: {
            Authorization: 'Bearer session',
            Cookie: 'access_token=session; unrelated=secret',
            'X-CSRF-Token': 'csrf',
            'X-Workspace-ID': 'forged',
          },
        }),
        path,
        { enabled: true, upstream, fetcher },
      )
      expect(response.status).toBe(200)
      expect(response.headers.get('cache-control')).toBe('private, no-store')
      expect(fetcher).toHaveBeenCalledTimes(1)
      const [url, options] = fetcher.mock.calls[0]!
      expect(String(url)).toBe(`${upstream}/enterprise/api/${path.join('/')}${query}`)
      expect(options?.method).toBe('GET')
      expect(options?.body).toBeUndefined()
      expect(options?.cache).toBe('no-store')
      const headers = new Headers(options?.headers)
      expect(headers.get('authorization')).toBe('Bearer session')
      expect(headers.get('cookie')).toBe('access_token=session')
      expect(headers.get('x-csrf-token')).toBe('csrf')
      expect(headers.has('x-workspace-id')).toBe(false)
    },
  )

  it.each(['alert', 'quality'])(
    'should forward durable %s draft creation without accepting caller workspace headers',
    async (scenario) => {
      const path = ['v1', 'devices', 'device-1', 'bindings', scenario, 'workflow-setups']
      const body = JSON.stringify({ source_id: 'source-1', expected_source_revision: 2 })
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValue(Response.json({ state: 'draft_ready' }, { status: 201 }))
      const request = new Request(`https://portal.test/enterprise/api/${path.join('/')}`, {
        method: 'POST',
        body,
        headers: {
          Origin: 'https://portal.test',
          'Content-Type': 'application/json',
          'Idempotency-Key': 'setup-request-1',
          'X-Enterprise-Expected-Workspace': 'forged-workspace',
        },
      })

      const response = await proxyEnterprise(request, path, { enabled: true, upstream, fetcher })

      expect(response.status).toBe(201)
      expect(fetcher).toHaveBeenCalledTimes(1)
      const [url, options] = fetcher.mock.calls[0]!
      expect(String(url)).toBe(`${upstream}/enterprise/api/${path.join('/')}`)
      const headers = new Headers(options?.headers)
      expect(headers.get('idempotency-key')).toBe('setup-request-1')
      expect(headers.has('x-enterprise-expected-workspace')).toBe(false)
    },
  )

  it.each([
    ['v1', 'devices', 'device-1', 'bindings', 'quality', 'workflow-setups'],
    ['v1', 'workflow-setups', 'setup-1'],
  ])('should read only registered setup collection or detail: %j', async (...path) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({}))

    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path.join('/')}`),
      path,
      { enabled: true, upstream, fetcher },
    )

    expect(response.status).toBe(200)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('forwards an exact registered route and only native authentication/concurrency headers', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('{"id":"0001"}', {
        status: 201,
        headers: {
          'Content-Type': 'application/json',
          ETag: '"1"',
          'Set-Cookie': 'untrusted=value',
        },
      }),
    )
    const request = new Request('https://portal.test/enterprise/api/v1/devices', {
      method: 'POST',
      body: '{"device_code":"0001","name":"Pump"}',
      headers: {
        'Content-Type': 'application/json',
        Origin: 'https://portal.test',
        Cookie: 'access_token=session; csrf_token=csrf; refresh_token=private; unrelated=secret',
        Authorization: 'Bearer session',
        'X-CSRF-Token': 'csrf',
        'If-Match': '"1"',
        'Idempotency-Key': 'request-1',
        'X-Workspace-ID': 'forged',
      },
    })
    const response = await proxyEnterprise(request, ['v1', 'devices'], {
      enabled: true,
      upstream,
      fetcher,
    })
    expect(response.status).toBe(201)
    expect(response.headers.get('etag')).toBe('"1"')
    expect(response.headers.get('cache-control')).toBe('private, no-store')
    expect(response.headers.has('set-cookie')).toBe(false)
    const [url, options] = fetcher.mock.calls[0]!
    expect(String(url)).toBe(`${upstream}/enterprise/api/v1/devices`)
    const headers = new Headers(options?.headers)
    expect(headers.get('cookie')).toBe('access_token=session; csrf_token=csrf')
    expect(headers.get('x-workspace-id')).toBeNull()
    expect(headers.get('x-csrf-token')).toBe('csrf')
    expect(headers.get('idempotency-key')).toBe('request-1')
    expect(options?.redirect).toBe('manual')
    expect(options?.cache).toBe('no-store')
  })

  it.each([
    ['console', 'api', 'accounts'],
    ['v1', 'devices', '..'],
    ['v1', 'devices', 'a/b'],
    ['internal', 'capture'],
    ['v1', 'devices', 'd', 'bindings', 'other'],
  ])('rejects unregistered or traversal paths before transport: %j', async (...path) => {
    const fetcher = vi.fn<typeof fetch>()
    const response = await proxyEnterprise(
      new Request('https://portal.test/enterprise/api/path'),
      path,
      { enabled: true, upstream, fetcher },
    )
    expect(response.status).toBe(404)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('keeps disabled and unconfigured services distinct without network access', async () => {
    const fetcher = vi.fn<typeof fetch>()
    const request = new Request('https://portal.test/enterprise/api/v1/me')
    expect(
      (await proxyEnterprise(request, ['v1', 'me'], { enabled: false, upstream, fetcher })).status,
    ).toBe(404)
    expect((await proxyEnterprise(request, ['v1', 'me'], { enabled: true, fetcher })).status).toBe(
      503,
    )
    expect(fetcher).not.toHaveBeenCalled()
  })

  it.each([
    'https://user:password@api.test',
    'file:///tmp/backend',
    'http://api.test/path',
    'http://api.test?target=evil',
  ])('rejects invalid server endpoint %s without disclosing it', async (target) => {
    const fetcher = vi.fn<typeof fetch>()
    const response = await proxyEnterprise(
      new Request('https://portal.test/enterprise/api/v1/me'),
      ['v1', 'me'],
      { enabled: true, upstream: target, fetcher },
    )
    expect(response.status).toBe(503)
    expect(await response.text()).not.toContain(target)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('preserves upstream authentication failure and never retries a mutation', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('{"code":"unauthenticated"}', {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const response = await proxyEnterprise(
      new Request('https://portal.test/enterprise/api/v1/me'),
      ['v1', 'me'],
      { enabled: true, upstream, fetcher },
    )
    expect(response.status).toBe(401)
    expect(await response.json()).toEqual({ code: 'unauthenticated' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('does not follow an upstream redirect or leak internal response text', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('internal password', {
        status: 307,
        headers: { Location: 'https://unregistered.test' },
      }),
    )
    const response = await proxyEnterprise(
      new Request('https://portal.test/enterprise/api/v1/me'),
      ['v1', 'me'],
      { enabled: true, upstream, fetcher },
    )
    expect(response.status).toBe(502)
    expect(await response.text()).not.toContain('password')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('requires Origin on writes and rejects oversized commands before forwarding', async () => {
    const fetcher = vi.fn<typeof fetch>()
    const url = 'https://portal.test/enterprise/api/v1/devices'
    expect(
      (
        await proxyEnterprise(new Request(url, { method: 'POST', body: '{}' }), ['v1', 'devices'], {
          enabled: true,
          upstream,
          fetcher,
        })
      ).status,
    ).toBe(403)
    const request = new Request(url, {
      method: 'POST',
      body: 'x'.repeat(1024 * 1024 + 1),
      headers: { Origin: 'https://portal.test', 'Content-Type': 'application/json' },
    })
    expect(
      (await proxyEnterprise(request, ['v1', 'devices'], { enabled: true, upstream, fetcher }))
        .status,
    ).toBe(413)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('limits upstream response bytes and preserves successful no-content deletion', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response('x'.repeat(8 * 1024 * 1024 + 1), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const read = new Request('https://portal.test/enterprise/api/v1/me')
    expect(
      (await proxyEnterprise(read, ['v1', 'me'], { enabled: true, upstream, fetcher })).status,
    ).toBe(502)
    const remove = new Request('https://portal.test/enterprise/api/v1/devices/d', {
      method: 'DELETE',
      headers: { Origin: 'https://portal.test' },
    })
    const response = await proxyEnterprise(remove, ['v1', 'devices', 'd'], {
      enabled: true,
      upstream,
      fetcher,
    })
    expect(response.status).toBe(204)
    expect(await response.text()).toBe('')
  })

  it('cancels a stalled response read with the original request signal', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => {
      queueMicrotask(() => controller.abort())
      return new Response(new ReadableStream(), { headers: { 'Content-Type': 'application/json' } })
    })
    const request = new Request('https://portal.test/enterprise/api/v1/me', {
      signal: controller.signal,
    })
    const response = await proxyEnterprise(request, ['v1', 'me'], {
      enabled: true,
      upstream,
      fetcher,
    })
    expect(response.status).toBe(504)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
