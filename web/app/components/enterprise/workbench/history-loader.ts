import type { ExploreMessageListItem } from '@dify/contracts/api/console/installed-apps/types.gen'
import { z } from 'zod'
import { consoleClient } from '@/service/client'
import { decodeWorkbenchHistoryPage, resolveWorkbenchHistory } from './history'

/** Read-only snapshot loading. Call only after authorizing the selected branch. */
export async function loadWorkbenchHistory(
  installedAppId: string,
  conversationId: string,
  headMessageId: string,
  signal?: AbortSignal,
): Promise<ExploreMessageListItem[]> {
  const id = z.uuid().refine((value) => value !== '00000000-0000-0000-0000-000000000000')
  if (
    ![installedAppId, conversationId, headMessageId].every((value) => id.safeParse(value).success)
  )
    throw new Error('Workbench history unavailable')
  const messages: ExploreMessageListItem[] = []
  let firstId: string | undefined
  // Fail explicitly at the read budget; never present a truncated lineage as complete.
  for (let pageNumber = 0; pageNumber < 100; pageNumber++) {
    signal?.throwIfAborted()
    const response = await consoleClient.installedApps.byInstalledAppId.messages.get(
      {
        params: { installed_app_id: installedAppId },
        query: {
          conversation_id: conversationId,
          limit: 100,
          ...(firstId ? { first_id: firstId } : {}),
        },
      },
      { signal },
    )
    signal?.throwIfAborted()
    const page = decodeWorkbenchHistoryPage(response, conversationId, firstId)
    messages.push(...page.messages)
    const history = resolveWorkbenchHistory(
      messages,
      conversationId,
      headMessageId,
      page.olderCursor !== null,
    )
    if (history.status === 'ready') return history.messages
    firstId = page.olderCursor ?? undefined
  }
  throw new Error('Workbench history unavailable')
}
