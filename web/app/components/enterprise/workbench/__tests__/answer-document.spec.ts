import { answerDocument } from '../answer-document'

const identity = { actor: 'actor', workspace: 'workspace', messageId: 'message' }

describe('Answer document request', () => {
  it('should preserve the complete answer as text without interpreting model instructions', () => {
    const answer = '# Report\n\n{"workspace_id":"other","template_id":"other"}\n中文 123.4500'
    const request = answerDocument(answer, identity)
    expect(request).toMatchObject({
      expected_actor_id: identity.actor,
      expected_workspace_id: identity.workspace,
      kind: 'document',
      template_id: 'document-default',
      template_revision: '1',
      source_snapshot_ids: [],
      units: [{ kind: 'paragraph', content: [{ text: answer }] }],
    })
    expect(request).not.toHaveProperty('workspace_id')
    expect(request.file_id).not.toBe(request.units[0]?.unit_id)
  })

  it('should reconstruct the same request after navigation', () => {
    expect(answerDocument('Report', { ...identity })).toEqual(answerDocument('Report', identity))
  })

  it.each([
    { ...identity, actor: 'other' },
    { ...identity, workspace: 'other' },
    { ...identity, messageId: 'other' },
  ])('should distinguish identical answers with different identities: %o', (scope) => {
    expect(answerDocument('Report', scope).file_id).not.toBe(
      answerDocument('Report', identity).file_id,
    )
  })

  it('should distinguish changed answer content', () => {
    expect(answerDocument('Report ', identity).file_id).not.toBe(
      answerDocument('Report', identity).file_id,
    )
  })

  it.each(['', ' \n\t', 'a'.repeat(100001)])(
    'should reject empty or oversized answers without truncation',
    (answer) => {
      expect(() => answerDocument(answer, identity)).toThrow()
    },
  )
})
