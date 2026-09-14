import type { OfficeCreateRequest } from '@enterprise/business-contracts/types'
import { zOfficeCreateRequest } from '@enterprise/business-contracts/zod'
import { v5 as uuidV5 } from 'uuid'

type AnswerIdentity = { actor: string; workspace: string; messageId: string }

/** Transcript identity survives navigation; it is not an authorization credential. */
export function answerDocument(answer: string, identity: AnswerIdentity): OfficeCreateRequest {
  if (!answer.trim() || answer.length > 100000)
    throw new Error('Answer document content is empty or too large')
  if (!identity.actor || !identity.workspace || !identity.messageId)
    throw new Error('Answer document identity is incomplete')
  const key = JSON.stringify([
    'enterprise-answer-document-v1',
    identity.workspace,
    identity.actor,
    identity.messageId,
    answer,
  ])
  return zOfficeCreateRequest.parse({
    expected_actor_id: identity.actor,
    expected_workspace_id: identity.workspace,
    file_id: uuidV5(JSON.stringify(['file', key]), uuidV5.URL),
    kind: 'document',
    template_id: 'document-default',
    template_revision: '1',
    source_snapshot_ids: [],
    units: [
      {
        unit_id: uuidV5(JSON.stringify(['unit', key]), uuidV5.URL),
        kind: 'paragraph',
        content: [{ text: answer }],
      },
    ],
  })
}
