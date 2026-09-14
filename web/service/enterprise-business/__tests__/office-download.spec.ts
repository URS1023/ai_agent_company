import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchOfficeDocument } from '../office-download'

vi.mock('@/env', () => ({ env: { NEXT_PUBLIC_BASE_PATH: '/company' } }))
vi.mock('js-cookie', () => ({ default: { get: () => 'csrf-fixture' } }))
vi.mock('@/config', () => ({
  API_PREFIX: '/console/api',
  APP_VERSION: 'test',
  CSRF_COOKIE_NAME: () => 'csrf',
  CSRF_HEADER_NAME: 'X-CSRF-Token',
  IS_MARKETPLACE: false,
  MARKETPLACE_API_PREFIX: '',
  PASSPORT_HEADER_NAME: 'X-Passport',
  PUBLIC_API_PREFIX: '',
  WEB_APP_SHARE_CODE_HEADER_NAME: 'X-Share',
}))
vi.mock('@/service/webapp-auth', () => ({
  getWebAppAccessToken: vi.fn(),
  getWebAppPassport: vi.fn(),
}))

const fileId = '00000000-0000-4000-8000-000000000001'
const input = { path: { file_id: fileId }, query: { expected_revision: '1' } }
const mediaType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const fetchMock = vi.fn<typeof fetch>()

describe('fetchOfficeDocument', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValue(
      new Response(new Uint8Array([80, 75, 3, 4, 255]), {
        headers: { 'Content-Type': mediaType },
      }),
    )
  })

  it('should retain binary bytes and use native credentials with the generated route', async () => {
    const result = await fetchOfficeDocument(input)
    expect(Array.from(new Uint8Array(await result.blob.arrayBuffer()))).toEqual([80, 75, 3, 4, 255])
    expect(result.filename).toBe(`office-${fileId}-r1.docx`)
    const call = fetchMock.mock.calls[0]
    if (!call) throw new Error('Expected request')
    const request = new Request(call[0], call[1])
    expect(new URL(request.url).pathname).toBe(
      `/company/enterprise/api/v1/office/files/${fileId}/document`,
    )
    expect(new URL(request.url).searchParams.get('expected_revision')).toBe('1')
    expect(request.credentials).toBe('include')
    expect(request.redirect).toBe('error')
    expect(request.headers.get('Accept')).toBe(mediaType)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it.each([401, 403, 409, 503])(
    'should not retry or expose response text for HTTP %s',
    async (status) => {
      fetchMock.mockResolvedValue(new Response('private detail', { status }))
      await expect(fetchOfficeDocument(input)).rejects.toThrow('Office download failed')
      expect(fetchMock).toHaveBeenCalledOnce()
    },
  )

  it('should reject a login page rather than download HTML as Word', async () => {
    fetchMock.mockResolvedValue(
      new Response('<html>login</html>', { headers: { 'Content-Type': 'text/html' } }),
    )
    await expect(fetchOfficeDocument(input)).rejects.toThrow('Office download failed')
  })

  it('should validate revision before any request', async () => {
    await expect(
      fetchOfficeDocument({ ...input, query: { expected_revision: '1.0' } }),
    ).rejects.toThrow()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('should honor cancellation before requesting', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(fetchOfficeDocument(input, { signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('download response lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('should reject partial content rather than return an incomplete document', async () => {
    fetchMock.mockResolvedValue(
      new Response('partial', { status: 206, headers: { 'Content-Type': mediaType } }),
    )
    await expect(fetchOfficeDocument(input)).rejects.toThrow('Office download failed')
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('should cancel an unread body when its MIME is rejected', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({ cancel })
    fetchMock.mockResolvedValue(new Response(body, { headers: { 'Content-Type': 'text/html' } }))
    await expect(fetchOfficeDocument(input)).rejects.toThrow('Office download failed')
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('should reject bytes when cancellation occurs during body reading', async () => {
    const abort = new AbortController()
    const body = new ReadableStream<Uint8Array>(
      {
        pull(controller) {
          abort.abort()
          controller.enqueue(new Uint8Array([80, 75, 3, 4]))
          controller.close()
        },
      },
      { highWaterMark: 0 },
    )
    fetchMock.mockResolvedValue(new Response(body, { headers: { 'Content-Type': mediaType } }))
    await expect(fetchOfficeDocument(input, { signal: abort.signal })).rejects.toMatchObject({
      name: 'AbortError',
    })
  })
})
