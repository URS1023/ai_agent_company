import type { ChatSendRequest } from '@enterprise/business-contracts/types'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { sendWorkbenchMessage } from '../workbench-send'

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

const appId = '00000000-0000-4000-8000-000000000001'
const body: ChatSendRequest = {
  client_message_id: '00000000-0000-4000-8000-000000000002',
  payload: { query: '设备状态', inputs: {} },
}
const input = { path: { installed_app_id: appId, branch_id: 'branch 1' }, body }
const fetchMock = vi.fn<typeof fetch>()
async function collect(value = input, signal?: AbortSignal) {
  const events = []
  for await (const event of sendWorkbenchMessage(value, { signal })) events.push(event)
  return events
}
function response() {
  return new Response('data: {"event":"message","answer":"在线"}\n\n', {
    headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
  })
}

describe('sendWorkbenchMessage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValue(response())
  })
  it('should send the frozen client ID once through native cookies and CSRF at the configured base path', async () => {
    expect(await collect()).toEqual([{ event: 'message', answer: '在线' }])
    expect(fetchMock).toHaveBeenCalledOnce()
    const call = fetchMock.mock.calls[0]
    expect(call).toBeDefined()
    if (!call) throw new Error('Expected request')
    const request = new Request(call[0], call[1])
    expect(new URL(request.url).pathname).toBe(
      `/company/enterprise/api/v1/workbench/apps/${appId}/branches/branch%201/messages`,
    )
    expect(request.method).toBe('POST')
    expect(request.credentials).toBe('include')
    expect(request.redirect).toBe('error')
    expect(request.headers.get('X-CSRF-Token')).toBe('csrf-fixture')
    expect(request.headers.get('Accept')).toBe('text/event-stream')
    expect(await request.json()).toEqual(body)
  })
  it.each([401, 403, 409, 429, 500, 503])(
    'should not replay a generation POST after HTTP %s',
    async (status) => {
      fetchMock.mockResolvedValue(Response.json({ code: 'fixture_error' }, { status }))
      await expect(collect()).rejects.toThrow('Workbench request failed')
      expect(fetchMock).toHaveBeenCalledOnce()
    },
  )
  it('should not replay after a network failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('private transport error'))
    await expect(collect()).rejects.toThrow('Workbench request failed')
    expect(fetchMock).toHaveBeenCalledOnce()
  })
  it('should reject a non-stream response without exposing its content', async () => {
    fetchMock.mockResolvedValue(Response.json({ private: 'content' }))
    await expect(collect()).rejects.toThrow('Workbench request failed')
  })
  it('should perform no request if the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(collect(input, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it.each(['.', '..'])(
    'should reject dot path segments %s before requesting',
    async (branch_id) => {
      await expect(collect({ ...input, path: { ...input.path, branch_id } })).rejects.toThrow()
      expect(fetchMock).not.toHaveBeenCalled()
    },
  )
})

// Real native base fetch and reader. Ky tees responses for hooks, so original stream locks
// are not consumer-owned; the underlying cancel callback proves resource cancellation.
describe('workbench send cancellation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
  })
  it('should cancel the response when the consumer returns early without sending another POST', async () => {
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"event":"message"}\n\n'))
      },
      cancel,
    })
    fetchMock.mockResolvedValue(
      new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    )
    const events = sendWorkbenchMessage(input)
    expect(await events.next()).toEqual({ done: false, value: { event: 'message' } })
    await events.return(undefined)
    expect(cancel).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
  })
  it('should abort a pending body read without marking completion or resending', async () => {
    const controller = new AbortController()
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({
      start(sink) {
        sink.enqueue(new TextEncoder().encode('data: {"event":"message"}\n\n'))
      },
      cancel,
    })
    fetchMock.mockResolvedValue(
      new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    )
    const events = sendWorkbenchMessage(input, { signal: controller.signal })
    await events.next()
    const pending = events.next()
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(cancel).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledOnce()
  })
  it('should cancel a rejected content type without consuming private response text', async () => {
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({ cancel })
    fetchMock.mockResolvedValue(new Response(stream, { headers: { 'Content-Type': 'text/html' } }))
    await expect(collect()).rejects.toThrow('Workbench request failed')
    expect(cancel).toHaveBeenCalledOnce()
  })
})
