import type { ExploreMessageListItem } from '@dify/contracts/api/console/installed-apps/types.gen'
import {
  zExploreMessageInfiniteScrollPagination,
  zExploreMessageListItem,
} from '@dify/contracts/api/console/installed-apps/zod.gen'
import { z } from 'zod'

const nil = '00000000-0000-0000-0000-000000000000'
const identity = z.uuid().refine((value) => value !== nil)
const unavailable = () => new Error('Workbench history unavailable')

function decodeMessage(response: unknown, conversationId: string): ExploreMessageListItem {
  const parsed = zExploreMessageListItem.safeParse(response)
  if (!parsed.success) throw unavailable()
  const message = parsed.data
  if (
    !identity.safeParse(message.id).success ||
    message.conversation_id !== conversationId ||
    (message.parent_message_id != null &&
      message.parent_message_id !== nil &&
      (!identity.safeParse(message.parent_message_id).success ||
        message.parent_message_id === message.id))
  )
    throw unavailable()
  return message
}

/** Native pagination is ascending within each page; first_id requests older rows. */
export function decodeWorkbenchHistoryPage(
  response: unknown,
  conversationId: string,
  firstId?: string,
) {
  const parsed = zExploreMessageInfiniteScrollPagination.safeParse(response)
  if (
    !identity.safeParse(conversationId).success ||
    !parsed.success ||
    (firstId !== undefined && !identity.safeParse(firstId).success)
  )
    throw unavailable()
  const page = parsed.data
  if (
    !Number.isInteger(page.limit) ||
    page.limit < 1 ||
    page.limit > 100 ||
    page.data.length > page.limit ||
    (page.has_more && page.data.length === 0)
  )
    throw unavailable()
  const ids = new Set<string>()
  const messages = page.data.map((value) => {
    const message = decodeMessage(value, conversationId)
    if (ids.has(message.id) || message.id === firstId) throw unavailable()
    ids.add(message.id)
    return message
  })
  const olderCursor = page.has_more ? messages[0]?.id : null
  if (olderCursor === undefined) throw unavailable()
  return { messages, olderCursor }
}

/** Native history is not a send-ledger observation. Never synthesize durable outcomes. */
export function resolveWorkbenchHistory(
  messages: readonly ExploreMessageListItem[],
  conversationId: string,
  headMessageId: string,
  hasOlderPages: boolean,
): { status: 'incomplete' } | { status: 'ready'; messages: ExploreMessageListItem[] } {
  if (!identity.safeParse(conversationId).success || !identity.safeParse(headMessageId).success)
    throw unavailable()
  const indexed = new Map<string, ExploreMessageListItem>()
  for (const value of messages) {
    const message = decodeMessage(value, conversationId)
    if (indexed.has(message.id)) throw unavailable()
    indexed.set(message.id, message)
  }
  const lineage: ExploreMessageListItem[] = []
  const visited = new Set<string>()
  let id: string | null | undefined = headMessageId
  while (id != null && id !== nil) {
    if (visited.has(id)) throw unavailable()
    visited.add(id)
    const message = indexed.get(id)
    if (!message) {
      if (hasOlderPages) return { status: 'incomplete' }
      throw unavailable()
    }
    lineage.push(message)
    id = message.parent_message_id
  }
  return { status: 'ready', messages: lineage.reverse() }
}
