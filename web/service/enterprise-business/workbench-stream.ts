/** Frame parsing only: EOF is not generation completion. The owner validates event identity
 * and persisted terminal envelopes. This module performs no HTTP request or retry.
 */
import { z } from 'zod'

const eventEnvelope = z.object({ event: z.string().min(1) }).catchall(z.unknown())

export class WorkbenchStreamError extends Error {
  constructor() {
    super('Invalid workbench stream')
    this.name = 'WorkbenchStreamError'
  }
}

export async function* readWorkbenchEvents(
  body: ReadableStream<Uint8Array>,
  {
    signal,
    maxFrameCharacters = 1024 * 1024,
    maxStreamBytes = 32 * 1024 * 1024,
  }: {
    signal?: AbortSignal
    maxFrameCharacters?: number
    maxStreamBytes?: number
  } = {},
): AsyncGenerator<z.infer<typeof eventEnvelope>> {
  for (const limit of [maxFrameCharacters, maxStreamBytes]) {
    if (!Number.isSafeInteger(limit) || limit < 1) throw new WorkbenchStreamError()
  }
  signal?.throwIfAborted()
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let cancellation: Promise<void> | undefined
  const cancel = () => {
    cancellation ??= reader.cancel().catch(() => undefined)
    return cancellation
  }
  const abort = () => {
    void cancel()
  }
  signal?.addEventListener('abort', abort, { once: true })
  let line = ''
  let data: string[] = []
  let afterCR = false
  let frameCharacters = 0
  let totalBytes = 0
  let exhausted = false
  function consumeLine() {
    if (line === '') {
      const payload = data.length ? data.join('\n') : undefined
      data = []
      frameCharacters = 0
      return payload
    }
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    let value = separator < 0 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') data.push(value)
    return undefined
  }
  try {
    while (true) {
      signal?.throwIfAborted()
      const chunk = await reader.read()
      signal?.throwIfAborted()
      if (!chunk.done) {
        totalBytes += chunk.value.byteLength
        if (totalBytes > maxStreamBytes) throw new WorkbenchStreamError()
      }
      let text: string
      try {
        text = chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true })
      } catch {
        throw new WorkbenchStreamError()
      }
      for (const char of text) {
        signal?.throwIfAborted()
        if (afterCR && char === '\n') {
          afterCR = false
          continue
        }
        afterCR = char === '\r'
        frameCharacters += char.length
        if (frameCharacters > maxFrameCharacters) throw new WorkbenchStreamError()
        if (char !== '\r' && char !== '\n') {
          line += char
          continue
        }
        const payload = consumeLine()
        line = ''
        if (payload === undefined) continue
        try {
          const value: unknown = JSON.parse(payload, (_key, item: unknown) => {
            if (typeof item === 'number' && !Number.isFinite(item)) throw new WorkbenchStreamError()
            return item
          })
          yield eventEnvelope.parse(value)
        } catch {
          throw new WorkbenchStreamError()
        }
      }
      if (chunk.done) {
        if (line || data.length) throw new WorkbenchStreamError()
        exhausted = true
        return
      }
    }
  } finally {
    signal?.removeEventListener('abort', abort)
    if (!exhausted) await cancel()
    reader.releaseLock()
  }
}
