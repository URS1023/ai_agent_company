import type { BranchContext, MessageScope } from '@enterprise/business-contracts/types'
import { zBranchContext } from '@enterprise/business-contracts/zod'

/** New-root launch only. Resuming history is a different operation, never an empty transcript. */
export function confirmWorkbenchRoot(response: unknown, expected: MessageScope): BranchContext {
  const parsed = zBranchContext.safeParse(response)
  if (!parsed.success) throw new Error('Workbench launch unavailable')
  const branch = parsed.data
  if (
    branch.scope.workspace_id !== expected.workspace_id ||
    branch.scope.actor_id !== expected.actor_id ||
    branch.scope.installed_app_id !== expected.installed_app_id ||
    branch.scope.branch_id !== expected.branch_id ||
    branch.state !== 'ready' ||
    branch.origin != null ||
    branch.conversation_id != null ||
    branch.head_message_id != null ||
    branch.inflight_client_message_id != null
  )
    throw new Error('Workbench launch unavailable')
  return branch
}
