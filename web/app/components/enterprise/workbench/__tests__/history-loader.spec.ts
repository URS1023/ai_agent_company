import { loadWorkbenchHistory } from '../history-loader'

const api = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('@/service/client', () => ({
  consoleClient: { installedApps: { byInstalledAppId: { messages: { get: api.read } } } },
}))
const app = '00000000-0000-4000-8000-000000000001'
const conversation = '00000000-0000-4000-8000-000000000002'
const head = '00000000-0000-4000-8000-000000000003'
const root = '00000000-0000-4000-8000-000000000004'
const message = (id: string, parent: string | null = null) => ({
  id,
  parent_message_id: parent,
  conversation_id: conversation,
  query: id,
  answer: 'saved answer',
  inputs: { count: 0 },
  status: 'normal',
  total_tokens: 0,
  agent_thoughts: [],
  extra_contents: [],
  message_files: [],
  retriever_resources: [],
})
beforeEach(() => {
  vi.clearAllMocks()
})
describe('workbench native history loading', () => {
  it('should validate all native identities before reading', async () => {
    await expect(loadWorkbenchHistory('invalid', conversation, head)).rejects.toThrow(
      'Workbench history unavailable',
    )
    expect(api.read).not.toHaveBeenCalled()
  })
  it('should discard a response received after cancellation', async () => {
    const controller = new AbortController()
    api.read.mockImplementation(async () => {
      controller.abort()
      return { data: [message(head)], limit: 100, has_more: false }
    })
    await expect(loadWorkbenchHistory(app, conversation, head, controller.signal)).rejects.toThrow()
    expect(api.read).toHaveBeenCalledTimes(1)
  })
  it('should fail explicitly at the page budget rather than return partial history', async () => {
    let index = 0
    api.read.mockImplementation(async () => {
      index++
      const unrelated = `00000000-0000-4000-9000-${index.toString().padStart(12, '0')}`
      return { data: [message(unrelated)], limit: 100, has_more: true }
    })
    await expect(loadWorkbenchHistory(app, conversation, head)).rejects.toThrow(
      'Workbench history unavailable',
    )
    expect(api.read).toHaveBeenCalledTimes(100)
  })
  it('should follow older-page cursors and stop after resolving the branch lineage', async () => {
    api.read
      .mockResolvedValueOnce({ data: [message(head, root)], limit: 100, has_more: true })
      .mockResolvedValueOnce({ data: [message(root)], limit: 100, has_more: false })
    const result = await loadWorkbenchHistory(app, conversation, head)
    expect(result.map((item) => item.id)).toEqual([root, head])
    expect(api.read).toHaveBeenLastCalledWith(
      {
        params: { installed_app_id: app },
        query: { conversation_id: conversation, limit: 100, first_id: head },
      },
      { signal: undefined },
    )
  })
  it('should not retry a native error', async () => {
    api.read.mockRejectedValue(new Error('offline'))
    await expect(loadWorkbenchHistory(app, conversation, head)).rejects.toThrow()
    expect(api.read).toHaveBeenCalledTimes(1)
  })
  it('should stop before issuing a read when aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(loadWorkbenchHistory(app, conversation, head, controller.signal)).rejects.toThrow()
    expect(api.read).not.toHaveBeenCalled()
  })
  it('should reject repeated page data rather than loop', async () => {
    api.read.mockResolvedValue({ data: [message(head, root)], limit: 100, has_more: true })
    await expect(loadWorkbenchHistory(app, conversation, head)).rejects.toThrow(
      'Workbench history unavailable',
    )
    expect(api.read).toHaveBeenCalledTimes(2)
  })
})
