import type { WorkbenchSendState } from '@enterprise/business-contracts/types'
import { describe, expect, it } from 'vitest'
import {
  applyWorkbenchEvent,
  applyWorkbenchSendState,
  closeWorkbenchTurn,
  newWorkbenchTurn,
} from '../turn'

const client = '00000000-0000-4000-8000-000000000001'
const identity = {
  conversation_id: '00000000-0000-4000-8000-000000000002',
  message_id: '00000000-0000-4000-8000-000000000003',
  task_id: 'task',
}
function state(changes: Partial<WorkbenchSendState> = {}): WorkbenchSendState {
  return {
    client_message_id: client,
    status: 'accepted',
    revision: 3,
    ...identity,
    outcome: null,
    ...changes,
  }
}
const text = (answer: string) => ({ event: 'message', ...identity, answer })

describe('workbench turn', () => {
  const attachment = {
    event: 'message_file',
    ...identity,
    id: '00000000-0000-4000-8000-000000000005',
    type: 'image',
    belongs_to: 'assistant',
    url: '/files/output?sign=abc',
  }
  it('should retain an immediate native attachment without inventing missing metadata or completion', () => {
    const original = newWorkbenchTurn(client)
    const turn = applyWorkbenchEvent(original, attachment)
    expect(turn.files).toEqual([
      { id: attachment.id, type: 'image', belongs_to: 'assistant', url: attachment.url },
    ])
    expect(original.files).toEqual([])
    expect(turn.durable).toBeNull()
    expect(turn.identity).toEqual(identity)
  })
  it('should deduplicate repeated attachment events while accepting refreshed signed URLs', () => {
    const first = applyWorkbenchEvent(newWorkbenchTurn(client), attachment)
    const repeated = applyWorkbenchEvent(first, { ...attachment })
    expect(repeated.files).toHaveLength(1)
    const refreshed = applyWorkbenchEvent(repeated, {
      ...attachment,
      url: '/files/output?sign=new',
    })
    expect(refreshed.files).toHaveLength(1)
    expect(refreshed.files[0]?.url).toBe('/files/output?sign=new')
    expect(first.files[0]?.url).toBe(attachment.url)
  })
  it.each([
    { task_id: undefined },
    { conversation_id: undefined },
    { id: 'bad' },
    { url: null },
    { message_id: client },
    { belongs_to: 'user' },
    { type: 'video' },
  ])('should reject malformed, foreign or conflicting file events %j', (changes) => {
    const original = applyWorkbenchEvent(newWorkbenchTurn(client), attachment)
    expect(() => applyWorkbenchEvent(original, { ...attachment, ...changes })).toThrow(
      'Invalid workbench state',
    )
    expect(original.files).toHaveLength(1)
  })
  it('should append streamed text without treating native message_end or EOF as completion', () => {
    let turn = newWorkbenchTurn(client)
    turn = applyWorkbenchEvent(turn, text('设备'))
    turn = applyWorkbenchEvent(turn, text('在线'))
    turn = applyWorkbenchEvent(turn, { event: 'message_end', ...identity })
    turn = closeWorkbenchTurn(turn)
    expect(turn.answer).toBe('设备在线')
    expect(turn.durable).toBeNull()
    expect(turn.connection).toBe('closed')
  })
  it('should use the same validation for SSE state and fetched persisted state', () => {
    const turn = applyWorkbenchEvent(newWorkbenchTurn(client), text('answer'))
    const persisted = state({ revision: 4, outcome: 'succeeded' })
    expect(applyWorkbenchEvent(turn, { event: 'enterprise_send_state', data: persisted })).toEqual(
      applyWorkbenchSendState(turn, persisted),
    )
    expect(applyWorkbenchSendState(turn, persisted).durable?.outcome).toBe('succeeded')
  })
  it('should preserve an accepted but unfinished receipt after transport interruption', () => {
    const turn = closeWorkbenchTurn(
      applyWorkbenchSendState(newWorkbenchTurn(client), state()),
      true,
    )
    expect(turn.connection).toBe('interrupted')
    expect(turn.durable?.status).toBe('accepted')
    expect(turn.durable?.outcome).toBeNull()
  })
  it.each(['conversation_id', 'message_id', 'task_id'] as const)(
    'should reject changed native %s without altering the previous answer',
    (key) => {
      const turn = applyWorkbenchEvent(newWorkbenchTurn(client), text('original'))
      expect(() =>
        applyWorkbenchEvent(turn, {
          ...text('foreign'),
          [key]: '00000000-0000-4000-8000-000000000099',
        }),
      ).toThrow('Invalid workbench state')
      expect(turn.answer).toBe('original')
    },
  )
  it('should reject a different expected conversation on the first native event', () => {
    expect(() => applyWorkbenchEvent(newWorkbenchTurn(client, client), text('foreign'))).toThrow(
      'Invalid workbench state',
    )
  })
  it('should ignore stale state but reject a conflicting equal revision', () => {
    const turn = applyWorkbenchSendState(
      newWorkbenchTurn(client),
      state({ revision: 4, outcome: 'failed' }),
    )
    expect(applyWorkbenchSendState(turn, state())).toBe(turn)
    expect(() =>
      applyWorkbenchSendState(turn, state({ revision: 4, outcome: 'succeeded' })),
    ).toThrow('Invalid workbench state')
  })
  it.each([
    { client_message_id: identity.message_id },
    { task_id: null },
    { status: 'uncertain' as const },
    { conversation_id: '00000000-0000-0000-0000-000000000000' },
  ])('should reject inconsistent or foreign persisted state %j', (changes) => {
    expect(() => applyWorkbenchSendState(newWorkbenchTurn(client), state(changes))).toThrow(
      'Invalid workbench state',
    )
  })
  it('should reject higher revisions that regress accepted or terminal states', () => {
    const turn = applyWorkbenchSendState(newWorkbenchTurn(client), state({ outcome: 'succeeded' }))
    expect(() => applyWorkbenchSendState(turn, state({ revision: 5, outcome: null }))).toThrow(
      'Invalid workbench state',
    )
    expect(() =>
      applyWorkbenchSendState(
        turn,
        state({
          revision: 5,
          status: 'uncertain',
          conversation_id: null,
          message_id: null,
          task_id: null,
          outcome: null,
        }),
      ),
    ).toThrow('Invalid workbench state')
  })
  it('should support replacement text and errors without inventing completion', () => {
    let turn = applyWorkbenchEvent(newWorkbenchTurn(client), text('old'))
    turn = applyWorkbenchEvent(turn, { ...text('moderated'), event: 'message_replace' })
    turn = applyWorkbenchEvent(turn, { event: 'enterprise_send_error', code: 'unavailable' })
    expect(turn.answer).toBe('moderated')
    expect(turn.hadStreamError).toBe(true)
    expect(turn.durable).toBeNull()
  })
})

describe('workbench state boundaries', () => {
  it('should retain the existing identity when a legacy error omits task_id', () => {
    const turn = applyWorkbenchEvent(newWorkbenchTurn(client), text('partial'))
    const next = applyWorkbenchEvent(turn, {
      event: 'error',
      conversation_id: identity.conversation_id,
      message_id: identity.message_id,
    })
    expect(next.identity).toEqual(identity)
    expect(next.hadStreamError).toBe(true)
    expect(next.durable).toBeNull()
  })
  it('should read persisted state after closing but reject further streamed events', () => {
    const turn = closeWorkbenchTurn(newWorkbenchTurn(client), true)
    expect(applyWorkbenchSendState(turn, state({ outcome: 'failed' })).durable?.outcome).toBe(
      'failed',
    )
    expect(() => applyWorkbenchEvent(turn, text('late'))).toThrow('Invalid workbench state')
  })
  it('should leave previous snapshots and caller-owned state objects unchanged', () => {
    const original = newWorkbenchTurn(client)
    const observed = state()
    const next = applyWorkbenchSendState(original, observed)
    observed.task_id = 'mutated'
    expect(original.identity).toBeNull()
    expect(next.identity).toEqual(identity)
    expect(next.durable?.task_id).toBe('task')
  })
  it('should allow queued through uncertain observations without claiming a receipt', () => {
    let turn = newWorkbenchTurn(client)
    for (const [index, status] of (['queued', 'dispatched', 'uncertain'] as const).entries()) {
      turn = applyWorkbenchSendState(
        turn,
        state({
          status,
          revision: index + 1,
          conversation_id: null,
          message_id: null,
          task_id: null,
        }),
      )
      expect(turn.identity).toBeNull()
      expect(turn.durable?.outcome).toBeNull()
    }
    expect(applyWorkbenchSendState(turn, state({ revision: 4 })).identity).toEqual(identity)
  })
  it('should reject malformed text without losing the previous valid answer', () => {
    const turn = applyWorkbenchEvent(newWorkbenchTurn(client), text('partial'))
    expect(() => applyWorkbenchEvent(turn, { ...text(''), answer: null })).toThrow(
      'Invalid workbench state',
    )
    expect(turn.answer).toBe('partial')
  })
})
