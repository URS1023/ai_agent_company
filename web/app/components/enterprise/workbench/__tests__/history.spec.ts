import type { ExploreMessageListItem } from '@dify/contracts/api/console/installed-apps/types.gen'
import { decodeWorkbenchHistoryPage, resolveWorkbenchHistory } from '../history'

const conversation = '00000000-0000-4000-8000-000000000001'
const first = '00000000-0000-4000-8000-000000000002'
const second = '00000000-0000-4000-8000-000000000003'
function message(id: string, parent: string | null = null): ExploreMessageListItem {
  return {
    id,
    conversation_id: conversation,
    parent_message_id: parent,
    query: 'question',
    answer: 'answer',
    inputs: { count: 0 },
    status: 'normal',
    total_tokens: 0,
    answer_tokens: 0,
    message_tokens: 0,
    provider_response_latency: 0,
    agent_thoughts: [],
    extra_contents: [],
    message_files: [],
    retriever_resources: [],
  }
}
const page = (data: ExploreMessageListItem[], has_more = false) => ({ data, has_more, limit: 50 })

describe('workbench native history pages', () => {
  it('should preserve native metadata and derive the older cursor from the first row', () => {
    const response = page([message(first), message(second, first)], true)
    const result = decodeWorkbenchHistoryPage(response, conversation)
    expect(result.messages).toEqual(response.data)
    expect(result.messages).not.toBe(response.data)
    expect(result.olderCursor).toBe(first)
    expect(result.messages[0]).not.toHaveProperty('durable')
  })
  it('should retain an empty final page', () => {
    expect(decodeWorkbenchHistoryPage(page([]), conversation)).toEqual({
      messages: [],
      olderCursor: null,
    })
  })
  it.each([
    page([{ ...message(first), conversation_id: second }]),
    page([message(first), message(first)]),
    page([], true),
    page([{ ...message(first), id: 'invalid' }]),
    page([{ ...message(first), parent_message_id: first }]),
    { data: [], has_more: false, limit: 1000 },
    { data: 'invalid' },
  ])('should reject malformed, foreign or non-progressing pages: %j', (response) => {
    expect(() => decodeWorkbenchHistoryPage(response, conversation)).toThrow(
      'Workbench history unavailable',
    )
  })
  it('should reject a cursor repeated in its older page', () => {
    expect(() => decodeWorkbenchHistoryPage(page([message(first)]), conversation, first)).toThrow(
      'Workbench history unavailable',
    )
  })
})

describe('workbench branch history lineage', () => {
  it('should follow parents from the confirmed head, excluding sibling answers', () => {
    const sibling = '00000000-0000-4000-8000-000000000004'
    const messages = [message(second, first), message(sibling, first), message(first)]
    expect(resolveWorkbenchHistory(messages, conversation, second, false)).toEqual({
      status: 'ready',
      messages: [message(first), message(second, first)],
    })
  })
  it('should require older pages when a parent is not loaded yet', () => {
    expect(resolveWorkbenchHistory([message(second, first)], conversation, second, true)).toEqual({
      status: 'incomplete',
    })
  })
  it('should not treat a missing head as an empty conversation', () => {
    expect(() => resolveWorkbenchHistory([], conversation, second, false)).toThrow(
      'Workbench history unavailable',
    )
  })
  it('should reject missing parents after pagination is exhausted', () => {
    expect(() =>
      resolveWorkbenchHistory([message(second, first)], conversation, second, false),
    ).toThrow('Workbench history unavailable')
  })
  it('should reject cyclic parent relationships', () => {
    expect(() =>
      resolveWorkbenchHistory(
        [message(first, second), message(second, first)],
        conversation,
        second,
        false,
      ),
    ).toThrow('Workbench history unavailable')
  })
  it('should recognize the native nil UUID as the root parent', () => {
    const root = message(first, '00000000-0000-0000-0000-000000000000')
    expect(resolveWorkbenchHistory([root], conversation, first, false)).toEqual({
      status: 'ready',
      messages: [root],
    })
  })
  it('should reject duplicate IDs across pages rather than choose a conflicting copy', () => {
    expect(() =>
      resolveWorkbenchHistory(
        [message(first), { ...message(first), answer: 'changed' }],
        conversation,
        first,
        false,
      ),
    ).toThrow('Workbench history unavailable')
  })
})
