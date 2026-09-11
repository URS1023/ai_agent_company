/** Pure browser presentation state. Native text/end events are not completion evidence.
 * The route owner scopes GET requests; this module reconciles their generated projection
 * with the current client/native identities. It never sends, retries or stops a model.
 */
import type { WorkbenchSendState } from '@enterprise/business-contracts/types'
import { zMessageFile } from '@dify/contracts/api/console/installed-apps/zod.gen'
import { zWorkbenchSendState } from '@enterprise/business-contracts/zod'
import { z } from 'zod'

const zero = '00000000-0000-0000-0000-000000000000'
const uuid = zWorkbenchSendState.shape.client_message_id.refine((value) => value !== zero)
const streamFileSchema = zMessageFile
  .pick({ id: true, type: true, belongs_to: true, url: true })
  .extend({
    id: uuid,
    type: z.string().min(1),
    belongs_to: z.string().min(1),
    url: z.string().min(1),
  })
const identitySchema = z.object({
  conversation_id: uuid,
  message_id: uuid,
  task_id: zWorkbenchSendState.shape.task_id.unwrap(),
})
const rank: Record<WorkbenchSendState['status'], number> = {
  queued: 0,
  dispatched: 1,
  uncertain: 2,
  accepted: 3,
}

export type WorkbenchTurn = Readonly<{
  clientMessageId: string
  expectedConversationId: string | null
  identity: Readonly<z.infer<typeof identitySchema>> | null
  durable: Readonly<WorkbenchSendState> | null
  answer: string
  files: readonly Readonly<z.infer<typeof streamFileSchema>>[]
  connection: 'open' | 'closed' | 'interrupted'
  hadStreamError: boolean
}>

export class WorkbenchStateError extends Error {
  constructor() {
    super('Invalid workbench state')
    this.name = 'WorkbenchStateError'
  }
}

export function newWorkbenchTurn(
  clientMessageId: string,
  expectedConversationId: string | null = null,
): WorkbenchTurn {
  return {
    clientMessageId: uuid.parse(clientMessageId),
    expectedConversationId:
      expectedConversationId === null ? null : uuid.parse(expectedConversationId),
    identity: null,
    durable: null,
    answer: '',
    files: [],
    connection: 'open',
    hadStreamError: false,
  }
}

function matchIdentity(turn: WorkbenchTurn, value: unknown) {
  const identity = identitySchema.parse(value)
  if (
    turn.expectedConversationId !== null &&
    identity.conversation_id !== turn.expectedConversationId
  )
    throw new WorkbenchStateError()
  if (
    turn.identity &&
    (identity.conversation_id !== turn.identity.conversation_id ||
      identity.message_id !== turn.identity.message_id ||
      identity.task_id !== turn.identity.task_id)
  )
    throw new WorkbenchStateError()
  return identity
}

export function applyWorkbenchSendState(turn: WorkbenchTurn, value: unknown): WorkbenchTurn {
  try {
    const next = zWorkbenchSendState.strict().parse(value)
    if (next.client_message_id !== turn.clientMessageId) throw new WorkbenchStateError()
    const accepted = next.status === 'accepted'
    if (
      !accepted &&
      (next.conversation_id !== null ||
        next.message_id !== null ||
        next.task_id !== null ||
        next.outcome !== null)
    )
      throw new WorkbenchStateError()
    const identity = accepted ? matchIdentity(turn, next) : turn.identity
    const previous = turn.durable
    if (previous) {
      if (next.revision < previous.revision) return turn
      if (next.revision === previous.revision) {
        if (JSON.stringify(previous) !== JSON.stringify(next)) throw new WorkbenchStateError()
        return turn
      }
      if (
        rank[next.status] < rank[previous.status] ||
        (previous.outcome !== null && previous.outcome !== next.outcome)
      )
        throw new WorkbenchStateError()
    }
    return { ...turn, identity, durable: next }
  } catch {
    throw new WorkbenchStateError()
  }
}

export function applyWorkbenchEvent(
  turn: WorkbenchTurn,
  event: Record<string, unknown>,
): WorkbenchTurn {
  try {
    if (turn.connection !== 'open') throw new WorkbenchStateError()
    if (event.event === 'enterprise_send_state') return applyWorkbenchSendState(turn, event.data)
    if (event.event === 'enterprise_send_error') return { ...turn, hadStreamError: true }
    const isText =
      event.event === 'message' ||
      event.event === 'agent_message' ||
      event.event === 'message_replace'
    let identity = turn.identity
    if (isText || event.event === 'message_file' || event.task_id !== undefined)
      identity = matchIdentity(turn, event)
    else if (identity) {
      if (
        (event.conversation_id !== undefined &&
          event.conversation_id !== identity.conversation_id) ||
        (event.message_id !== undefined && event.message_id !== identity.message_id)
      )
        throw new WorkbenchStateError()
    }
    if (event.event === 'message_file') {
      const file = streamFileSchema.parse(event)
      const previous = turn.files.find((item) => item.id === file.id)
      if (previous && (previous.type !== file.type || previous.belongs_to !== file.belongs_to))
        throw new WorkbenchStateError()
      return {
        ...turn,
        identity,
        files: previous
          ? turn.files.map((item) => (item.id === file.id ? file : item))
          : [...turn.files, file],
      }
    }
    if (isText) {
      if (typeof event.answer !== 'string') throw new WorkbenchStateError()
      const answer = event.event === 'message_replace' ? event.answer : turn.answer + event.answer
      if (answer.length > 8 * 1024 * 1024) throw new WorkbenchStateError()
      return { ...turn, identity, answer }
    }
    return { ...turn, identity, hadStreamError: turn.hadStreamError || event.event === 'error' }
  } catch {
    throw new WorkbenchStateError()
  }
}

export function closeWorkbenchTurn(turn: WorkbenchTurn, interrupted = false): WorkbenchTurn {
  return { ...turn, connection: interrupted ? 'interrupted' : 'closed' }
}
