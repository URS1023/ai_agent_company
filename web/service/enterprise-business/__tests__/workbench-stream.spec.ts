import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readWorkbenchEvents } from '../workbench-stream'
const encoder = new TextEncoder()
function source(bytes: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of bytes) controller.enqueue(chunk)
      controller.close()
    },
  })
}
async function collect(stream: ReadableStream<Uint8Array>, options = {}) {
  const events = []
  for await (const event of readWorkbenchEvents(stream, options)) events.push(event)
  return events
}
describe('readWorkbenchEvents', () => {
  beforeEach(() => vi.clearAllMocks())
  it.each(['\n', '\r\n', '\r'])(
    'should preserve UTF-8 at every byte boundary with %j separators',
    async (newline) => {
      const bytes = encoder.encode(
        `\uFEFF: heartbeat${newline}${newline}data: {"event":"message","answer":"设备🌡"}${newline}${newline}`,
      )
      for (let split = 1; split < bytes.length; split++) {
        expect(await collect(source([bytes.slice(0, split), bytes.slice(split)]))).toEqual([
          { event: 'message', answer: '设备🌡' },
        ])
      }
    },
  )
  it('should join data lines and preserve unknown events without inventing terminal state', async () => {
    const bytes = encoder.encode(
      'event: ignored\nid: ignored\ndata: {"event":"future_event",\ndata: "value":0}\n\n',
    )
    expect(await collect(source([bytes]))).toEqual([{ event: 'future_event', value: 0 }])
  })
  it.each([
    'data: {}\n\n',
    'data: []\n\n',
    'data: {"event":1}\n\n',
    'data: {"event":"message","value":1e999}\n\n',
    'data: {"event":"message"}\n',
  ])('should reject invalid or truncated frames: %j', async (text) => {
    await expect(collect(source([encoder.encode(text)]))).rejects.toThrow(
      'Invalid workbench stream',
    )
  })
  it('should reject invalid UTF-8 instead of replacing bytes', async () => {
    await expect(collect(source([new Uint8Array([0xff])]))).rejects.toThrow(
      'Invalid workbench stream',
    )
  })
  it('should bound frames and total bytes including heartbeats', async () => {
    await expect(
      collect(source([encoder.encode('data: '.repeat(20))]), { maxFrameCharacters: 16 }),
    ).rejects.toThrow('Invalid workbench stream')
    await expect(
      collect(source([encoder.encode(': ping\n\n'.repeat(20))]), { maxStreamBytes: 16 }),
    ).rejects.toThrow('Invalid workbench stream')
  })
  it('should cancel and release the body when the consumer stops early', async () => {
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"event":"message"}\n\n'))
      },
      cancel,
    })
    const events = readWorkbenchEvents(stream)
    expect(await events.next()).toEqual({ done: false, value: { event: 'message' } })
    await events.return(undefined)
    expect(cancel).toHaveBeenCalledOnce()
    expect(stream.locked).toBe(false)
  })
  it('should interrupt a pending read and release the body on abort', async () => {
    const controller = new AbortController()
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({ cancel })
    const result = collect(stream, { signal: controller.signal })
    controller.abort()
    await expect(result).rejects.toMatchObject({ name: 'AbortError' })
    expect(cancel).toHaveBeenCalledOnce()
    expect(stream.locked).toBe(false)
  })
})

// Cancellation and transport termination are not native generation acknowledgements.
describe('workbench stream termination', () => {
  beforeEach(() => vi.clearAllMocks())
  it('should preserve enterprise state and error envelopes in order without adding completion', async () => {
    const events = [
      { event: 'enterprise_send_state', data: { status: 'dispatched', outcome: null } },
      { event: 'error', task_id: 'task' },
      { event: 'enterprise_send_error', code: 'dependency_unavailable' },
    ]
    const bytes = encoder.encode(
      events.map((value) => `data: ${JSON.stringify(value)}\n\n`).join(''),
    )
    expect(await collect(source([bytes]))).toEqual(events)
  })
  it('should release the body when the underlying read fails', async () => {
    const failure = new Error('transport lost')
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.error(failure)
      },
    })
    await expect(collect(stream)).rejects.toBe(failure)
    expect(stream.locked).toBe(false)
  })
  it('should cancel the body on malformed data even if more bytes might arrive', async () => {
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: invalid\n\n'))
      },
      cancel,
    })
    await expect(collect(stream)).rejects.toThrow('Invalid workbench stream')
    expect(cancel).toHaveBeenCalledOnce()
    expect(stream.locked).toBe(false)
  })
  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, 1.5])(
    'should reject invalid bounds %s before locking a body',
    async (maxStreamBytes) => {
      const stream = source([])
      await expect(collect(stream, { maxStreamBytes })).rejects.toThrow('Invalid workbench stream')
      expect(stream.locked).toBe(false)
    },
  )
})
