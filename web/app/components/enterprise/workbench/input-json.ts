import type { JsonObject } from '@dify/contracts/api/console/installed-apps/types.gen'
import { zJsonObject } from '@dify/contracts/api/console/installed-apps/zod.gen'

type ParsedInput =
  | { status: 'empty' }
  | { status: 'invalid' }
  | { status: 'valid'; value: JsonObject }

export function parseWorkbenchJsonInput(text: string): ParsedInput {
  if (!text.trim()) return { status: 'empty' }
  try {
    const value: unknown = JSON.parse(text, (_key, entry: unknown) => {
      // Reject numeric overflow and integer rounding before values enter the send payload.
      if (
        typeof entry === 'number' &&
        (!Number.isFinite(entry) || (Number.isInteger(entry) && !Number.isSafeInteger(entry)))
      )
        throw new Error('Invalid JSON number')
      return entry
    })
    return { status: 'valid', value: zJsonObject.parse(value) }
  } catch {
    return { status: 'invalid' }
  }
}
