import type { MessageScope, WorkbenchSendState } from '@enterprise/business-contracts/types'
import { zWorkbenchSendState } from '@enterprise/business-contracts/zod'
import { loadWorkbenchResume } from './resume'

/** Read persisted message files only after a durable terminal receipt; never replay generation. */
export async function loadCompletedWorkbenchFiles(
  scope: MessageScope,
  value: WorkbenchSendState,
  signal?: AbortSignal,
) {
  signal?.throwIfAborted()
  const receipt = zWorkbenchSendState.parse(value)
  if (
    receipt.status !== 'accepted' ||
    receipt.outcome === null ||
    !receipt.message_id ||
    !receipt.conversation_id
  )
    throw new Error('Completed workbench files unavailable')
  const snapshot = await loadWorkbenchResume(scope, signal)
  signal?.throwIfAborted()
  const message = snapshot.history.find(
    (item) => item.id === receipt.message_id && item.conversation_id === receipt.conversation_id,
  )
  if (!message) throw new Error('Completed workbench files unavailable')
  return message.message_files
}
