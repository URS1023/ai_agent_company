// @vitest-environment node
import { GET, POST, PUT } from '../../[...path]/route'

const state = vi.hoisted(() => ({ enabled: true, upstream: 'http://enterprise-api:8000' }))
vi.mock('@/app/components/enterprise/feature-flag', () => ({
  isEnterprisePortalEnabled: () => state.enabled,
}))
vi.mock('@/env', () => ({
  env: {
    get ENTERPRISE_API_URL() {
      return state.upstream
    },
  },
}))

describe('enterprise Next route boundary', () => {
  beforeEach(() => {
    state.enabled = true
    state.upstream = 'http://enterprise-api:8000'
  })

  it('forwards an exact request-key lookup query without interpreting the key as a path', async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ id: 'run-1' }))
    const response = await GET(
      new Request(
        'https://portal.test/enterprise/api/v1/run-requests/lookup?request_key=key%2F0001%20%25',
      ),
      { params: Promise.resolve({ path: ['v1', 'run-requests', 'lookup'] }) },
    )
    expect(response.status).toBe(200)
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe(
      'http://enterprise-api:8000/enterprise/api/v1/run-requests/lookup?request_key=key%2F0001%20%25',
    )
  })

  it('preserves list filters but removes the browser deployment base path', async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ items: [], total: 0 }))
    const response = await GET(
      new Request(
        'https://portal.test/dify/enterprise/api/v1/devices?q=0001&department=A&limit=20',
      ),
      { params: Promise.resolve({ path: ['v1', 'devices'] }) },
    )
    expect(response.status).toBe(200)
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe(
      'http://enterprise-api:8000/enterprise/api/v1/devices?q=0001&department=A&limit=20',
    )
  })

  it('forwards source capabilities and one idempotent creation to the fixed backend', async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ write_enabled: false }))
    const capabilities = await GET(
      new Request('https://portal.test/enterprise/api/v1/sources/capabilities'),
      { params: Promise.resolve({ path: ['v1', 'sources', 'capabilities'] }) },
    )
    expect(capabilities.status).toBe(200)
    const creation = await POST(
      new Request('https://portal.test/enterprise/api/v1/sources', {
        method: 'POST',
        headers: {
          Origin: 'https://portal.test',
          'Content-Type': 'application/json',
          'Idempotency-Key': 'original-key',
        },
        body: '{"name":"Equipment"}',
      }),
      { params: Promise.resolve({ path: ['v1', 'sources'] }) },
    )
    expect(creation.status).toBe(200)
    expect(new Headers(vi.mocked(fetch).mock.calls[1]?.[1]?.headers).get('idempotency-key')).toBe(
      'original-key',
    )
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('preserves the expected source revision when forwarding an update', async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ revision: 3 }))
    const response = await PUT(
      new Request('https://portal.test/enterprise/api/v1/sources/source-1', {
        method: 'PUT',
        headers: {
          Origin: 'https://portal.test',
          'Content-Type': 'application/json',
          'If-Match': '"2"',
        },
        body: '{}',
      }),
      { params: Promise.resolve({ path: ['v1', 'sources', 'source-1'] }) },
    )
    expect(response.status).toBe(200)
    expect(new Headers(vi.mocked(fetch).mock.calls[0]?.[1]?.headers).get('if-match')).toBe('"2"')
  })

  it('returns a real 404 without forwarding when the feature is disabled', async () => {
    state.enabled = false
    const response = await GET(new Request('https://portal.test/enterprise/api/v1/me'), {
      params: Promise.resolve({ path: ['v1', 'me'] }),
    })
    expect(response.status).toBe(404)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('masks transport failure and forwards a write only once', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('private upstream address'))
    const response = await POST(
      new Request('https://portal.test/enterprise/api/v1/devices', {
        method: 'POST',
        headers: { Origin: 'https://portal.test', 'Content-Type': 'application/json' },
        body: '{}',
      }),
      { params: Promise.resolve({ path: ['v1', 'devices'] }) },
    )
    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({ code: 'enterprise_upstream_unavailable' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
