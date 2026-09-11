import type { MessageScope } from '@enterprise/business-contracts/types'
import { zBranchContext, zJsonObject, zMessageScope } from '@enterprise/business-contracts/zod'
import { consoleClient } from '@/service/client'
import { loadWorkbenchHistory } from './history-loader'

export async function loadWorkbenchResume(expected: MessageScope, signal?: AbortSignal) {
  const scope = zMessageScope.parse(expected)
  const readBranch = async () => {
    signal?.throwIfAborted()
    const result =
      await consoleClient.business.workbench.apps.byInstalledAppId.branches.byBranchId.get(
        {
          params: { installed_app_id: scope.installed_app_id, branch_id: scope.branch_id },
        },
        { signal },
      )
    signal?.throwIfAborted()
    const branch = zBranchContext.parse(result)
    if (
      branch.scope.actor_id !== scope.actor_id ||
      branch.scope.workspace_id !== scope.workspace_id ||
      branch.scope.installed_app_id !== scope.installed_app_id ||
      branch.scope.branch_id !== scope.branch_id
    )
      throw new Error('Workbench resume unavailable')
    return branch
  }
  const branch = await readBranch()
  if (!branch.conversation_id || !branch.head_message_id || branch.state === 'preparing')
    throw new Error('Workbench resume unavailable')
  const history = await loadWorkbenchHistory(
    scope.installed_app_id,
    branch.conversation_id,
    branch.head_message_id,
    signal,
  )
  const head = history.at(-1)
  if (!head || head.id !== branch.head_message_id) throw new Error('Workbench resume unavailable')
  const verified = await readBranch()
  if (JSON.stringify(verified) !== JSON.stringify(branch))
    throw new Error('Workbench resume unavailable')
  return { branch, history, inputs: zJsonObject.parse(structuredClone(head.inputs)) }
}
