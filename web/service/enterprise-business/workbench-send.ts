/** One generation POST, using generated input/path contracts and the native fetch boundary.
 * Unlike the JSON request wrapper, base does not refresh and replay after a 401.
 * Consumers must retain the client message ID and reconcile uncertain outcomes; closing
 * this stream cancels transport only, not the native model generation.
 */
import type { SendMessageEnterpriseApiV1WorkbenchAppsInstalledAppIdBranchesBranchIdMessagesPostData } from '@enterprise/business-contracts/types'
import { contract } from '@enterprise/business-contracts/orpc'
import {
  zChatSendRequest,
  zSendMessageEnterpriseApiV1WorkbenchAppsInstalledAppIdBranchesBranchIdMessagesPostPath,
} from '@enterprise/business-contracts/zod'
import { env } from '@/env'
// eslint-disable-next-line no-restricted-imports -- Streaming requires raw Response without JSON wrapper's automatic reissue; base owns cookies/CSRF and disables retries.
import { base } from '../fetch'
import { readWorkbenchEvents } from './workbench-stream'

export class WorkbenchRequestError extends Error {
  constructor() {
    super('Workbench request failed')
    this.name = 'WorkbenchRequestError'
  }
}

export async function* sendWorkbenchMessage(
  input: Pick<
    SendMessageEnterpriseApiV1WorkbenchAppsInstalledAppIdBranchesBranchIdMessagesPostData,
    'path' | 'body'
  >,
  { signal }: { signal?: AbortSignal } = {},
) {
  signal?.throwIfAborted()
  const path =
    zSendMessageEnterpriseApiV1WorkbenchAppsInstalledAppIdBranchesBranchIdMessagesPostPath.parse(
      input.path,
    )
  const body = zChatSendRequest.strict().parse(input.body)
  if (path.branch_id === '.' || path.branch_id === '..') throw new WorkbenchRequestError()
  const route =
    contract.workbench.apps.byInstalledAppId.branches.byBranchId.messages.post['~orpc'].route
  if (!route.path || route.method !== 'POST') throw new WorkbenchRequestError()
  const pathname = route.path
    .replace('{installed_app_id}', encodeURIComponent(path.installed_app_id))
    .replace('{branch_id}', encodeURIComponent(path.branch_id))
  const url = new URL(`${env.NEXT_PUBLIC_BASE_PATH}${pathname}`, window.location.origin)
  if (url.origin !== window.location.origin) throw new WorkbenchRequestError()
  let response: Response | undefined
  try {
    response = await base<Response>(
      url.href,
      {
        method: 'POST',
        signal,
        redirect: 'error',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body),
      },
      { fetchCompat: true, request: new Request(url), silent: true },
    )
    signal?.throwIfAborted()
    if (
      response.status !== 200 ||
      !response.body ||
      response.headers.get('Content-Type')?.split(';')[0]?.trim().toLowerCase() !==
        'text/event-stream'
    ) {
      throw new WorkbenchRequestError()
    }
    yield* readWorkbenchEvents(response.body, { signal })
  } catch {
    signal?.throwIfAborted()
    throw new WorkbenchRequestError()
  } finally {
    if (response?.body && !response.body.locked) await response.body.cancel().catch(() => undefined)
  }
}
