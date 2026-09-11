// @vitest-environment node
import { proxyEnterprise } from '..'

describe('dashboard data proxy', () => {
  it('forwards selected trial IDs for a multi-slot preview without adding execution inputs', async () => {
    const path = 'v1/dashboards/dashboard-1/sql-proposals/draft-1/preview'
    const body = { trial_ids: ['trial-1', 'trial-2'] }
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ status: 'preview_only' }))
    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path}`, {
        method: 'POST',
        headers: { Origin: 'https://portal.test', 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
      path.split('/'),
      { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
    )
    expect(response.status).toBe(200)
    expect(await new Response(fetcher.mock.calls[0]?.[1]?.body).json()).toEqual(body)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
  })
  it.each(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])(
    'restricts preview to GET (%s)',
    async (method) => {
      const path = 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trials/trial-1/preview'
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValue(Response.json({ status: 'preview_only' }))
      const response = await proxyEnterprise(
        new Request(`https://portal.test/enterprise/api/${path}`, {
          method,
          headers: { Origin: 'https://portal.test' },
        }),
        path.split('/'),
        { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
      )
      expect(response.status).toBe(method === 'GET' ? 200 : 404)
      if (method === 'GET') {
        expect(fetcher).toHaveBeenCalledOnce()
        expect(String(fetcher.mock.calls[0]![0])).toBe(
          `http://enterprise-api:8000/enterprise/api/${path}`,
        )
        expect(response.headers.get('cache-control')).toBe('private, no-store')
      } else expect(fetcher).not.toHaveBeenCalled()
    },
  )
  it.each(
    ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].flatMap((method) =>
      ['', '/automatic'].map((suffix) => [method, suffix]),
    ),
  )('restricts sample checks to GET (%s %s)', async (method, suffix) => {
    const path = `v1/dashboards/dashboard-1/sql-proposals/draft-1/trials/trial-1/sample-check${suffix}`
    const query = suffix ? '' : '?policy_id=device-count&policy_revision=v1'
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ policy_revision: 'v1' }))
    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path}${query}`, {
        method,
        headers: { Origin: 'https://portal.test' },
      }),
      path.split('/'),
      { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
    )
    if (method === 'GET') {
      expect(response.status).toBe(200)
      expect(String(fetcher.mock.calls[0]![0])).toBe(
        `http://enterprise-api:8000/enterprise/api/${path}${query}`,
      )
      expect(response.headers.get('cache-control')).toBe('private, no-store')
    } else {
      expect(response.status).toBe(404)
      expect(fetcher).not.toHaveBeenCalled()
    }
  })
  it.each(['POST', 'PUT', 'DELETE'])(
    'should block %s on saved trial inspection',
    async (method) => {
      const fetcher = vi.fn<typeof fetch>()
      const path = 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trials/trial-1'
      const response = await proxyEnterprise(
        new Request(`https://portal.test/enterprise/api/${path}`, {
          method,
          headers: { Origin: 'https://portal.test' },
        }),
        path.split('/'),
        { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
      )
      expect(response.status).toBe(404)
      expect(fetcher).not.toHaveBeenCalled()
    },
  )
  it('should preserve the binding command and committed response when saving', async () => {
    const command = {
      expected_revision: 2,
      expected_design_identity: 'a'.repeat(64),
      bindings: [
        {
          slot_id: 'temperature',
          query_ref: 'approved-temperature',
          query_revision: 'revision-3',
          field_map: { name: 'device_name', value: 'temperature' },
          parameters: { period: 'hour' },
        },
      ],
    }
    const receipt = { dashboard_id: 'dashboard-1', revision: 3 }
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(receipt))

    const response = await proxyEnterprise(
      new Request('https://portal.test/enterprise/api/v1/dashboards/dashboard-1/bindings', {
        method: 'PUT',
        headers: {
          Origin: 'https://portal.test',
          'Content-Type': 'application/json',
          Authorization: 'Bearer session',
          'X-CSRF-Token': 'csrf',
        },
        body: JSON.stringify(command),
      }),
      ['v1', 'dashboards', 'dashboard-1', 'bindings'],
      { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
    )

    expect(fetcher).toHaveBeenCalledOnce()
    const [url, init] = fetcher.mock.calls[0]!
    expect(String(url)).toBe(
      'http://enterprise-api:8000/enterprise/api/v1/dashboards/dashboard-1/bindings',
    )
    expect(init?.method).toBe('PUT')
    expect(await new Response(init?.body).json()).toEqual(command)
    const headers = new Headers(init?.headers)
    expect(headers.get('origin')).toBe('https://portal.test')
    expect(headers.get('authorization')).toBe('Bearer session')
    expect(headers.get('x-csrf-token')).toBe('csrf')
    expect(response.status).toBe(200)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
    expect(await response.json()).toEqual(receipt)
  })

  it.each([
    ['GET', 'v1/dashboard-templates'],
    ['GET', 'v1/dashboard-queries'],
    ['GET', 'v1/dashboards'],
    ['POST', 'v1/dashboards'],
    ['GET', 'v1/dashboards/dashboard-1'],
    ['GET', 'v1/dashboards/dashboard-1/bindings'],
    ['PUT', 'v1/dashboards/dashboard-1/bindings'],
    ['POST', 'v1/dashboards/dashboard-1/refresh'],
    ['POST', 'v1/dashboards/dashboard-1/sql-proposals'],
    ['GET', 'v1/dashboards/dashboard-1/sql-proposals/draft-1'],
    ['POST', 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trial'],
    ['GET', 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trials/trial-1'],
    ['GET', 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trials'],
    ['GET', 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trials/trial-1/assessment'],
  ])('forwards only the registered %s %s operation', async (method, path) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ revision: 2 }))
    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path}`, {
        method,
        headers: {
          Origin: 'https://portal.test',
          Authorization: 'Bearer session',
          'X-CSRF-Token': 'csrf',
          'Content-Type': 'application/json',
          'Idempotency-Key': 'create-1',
        },
        body: method === 'POST' ? JSON.stringify({ expected_revision: 1 }) : undefined,
      }),
      path.split('/'),
      { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
    )
    expect(response.status).toBe(200)
    expect(response.headers.get('cache-control')).toBe('private, no-store')
    expect(fetcher).toHaveBeenCalledOnce()
    if (method === 'POST')
      expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get('Idempotency-Key')).toBe(
        'create-1',
      )
  })

  it.each(['GET', 'PUT', 'DELETE'])(
    'should block %s on SQL proposal generation',
    async (method) => {
      const fetcher = vi.fn<typeof fetch>()
      const path = 'v1/dashboards/dashboard-1/sql-proposals'
      const response = await proxyEnterprise(
        new Request(`https://portal.test/enterprise/api/${path}`, {
          method,
          headers: { Origin: 'https://portal.test' },
        }),
        path.split('/'),
        { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
      )
      expect(response.status).toBe(404)
      expect(fetcher).not.toHaveBeenCalled()
    },
  )

  it('preserves bounded list pagination without changing workspace identity', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ items: [], next_cursor: null }))
    const response = await proxyEnterprise(
      new Request('https://portal.test/enterprise/api/v1/dashboards?after=dashboard-1&limit=2'),
      ['v1', 'dashboards'],
      { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
    )
    expect(response.status).toBe(200)
    expect(String(fetcher.mock.calls[0]?.[0])).toBe(
      'http://enterprise-api:8000/enterprise/api/v1/dashboards?after=dashboard-1&limit=2',
    )
  })

  it.each(['GET', 'PUT', 'DELETE'])(
    'should not forward %s as a trial execution',
    async (method) => {
      const path = 'v1/dashboards/dashboard-1/sql-proposals/draft-1/trial'
      const fetcher = vi.fn<typeof fetch>()
      const response = await proxyEnterprise(
        new Request(`https://portal.test/enterprise/api/${path}`, {
          method,
          headers: { Origin: 'https://portal.test' },
        }),
        path.split('/'),
        { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
      )
      expect(response.status).toBe(404)
      expect(fetcher).not.toHaveBeenCalled()
    },
  )

  it.each([
    'v1/dashboards/dashboard-1/sql',
    'v1/dashboards/dashboard-1/design',
    'v1/dashboard-templates',
  ])('blocks unimplemented writes to %s', async (path) => {
    const fetcher = vi.fn<typeof fetch>()
    const response = await proxyEnterprise(
      new Request(`https://portal.test/enterprise/api/${path}`, { method: 'POST' }),
      path.split('/'),
      { enabled: true, upstream: 'http://enterprise-api:8000', fetcher },
    )
    expect(response.status).toBe(404)
    expect(fetcher).not.toHaveBeenCalled()
  })
})
