import type { BranchContext, WorkbenchSendState } from '@enterprise/business-contracts/types'
import { loadCompletedWorkbenchFiles } from '../completed-files'

const api = vi.hoisted(() => ({ branch: vi.fn(), history: vi.fn() }))
vi.mock('@/service/client', () => ({
  consoleClient: {
    business: {
      workbench: { apps: { byInstalledAppId: { branches: { byBranchId: { get: api.branch } } } } },
    },
    installedApps: { byInstalledAppId: { messages: { get: api.history } } },
  },
}))
const scope = {
  workspace_id: 'workspace',
  actor_id: 'actor',
  installed_app_id: '00000000-0000-4000-8000-000000000001',
  branch_id: 'branch',
}
const receipt: WorkbenchSendState = {
  client_message_id: '00000000-0000-4000-8000-000000000002',
  conversation_id: '00000000-0000-4000-8000-000000000003',
  message_id: '00000000-0000-4000-8000-000000000004',
  task_id: 'task',
  status: 'accepted',
  revision: 4,
  outcome: 'succeeded',
}
const branch: BranchContext = {
  scope,
  state: 'ready',
  revision: 3,
  conversation_id: receipt.conversation_id,
  head_message_id: receipt.message_id,
}
const files = [
  {
    id: 'persisted-file',
    type: 'document',
    filename: 'quality.pdf',
    transfer_method: 'tool_file',
    belongs_to: 'assistant',
    url: '/files/quality',
  },
]
beforeEach(() => {
  vi.clearAllMocks()
  api.branch.mockResolvedValue(branch)
  api.history.mockResolvedValue({
    data: [
      {
        id: receipt.message_id,
        conversation_id: receipt.conversation_id,
        parent_message_id: null,
        query: 'query',
        answer: 'answer',
        inputs: {},
        status: 'normal',
        total_tokens: 0,
        agent_thoughts: [],
        extra_contents: [],
        message_files: files,
        retriever_resources: [],
      },
    ],
    limit: 100,
    has_more: false,
  })
})

// Native persisted message identity, not workflow file-reference IDs, governs enrichment.
describe('completed workbench files', () => {
  it('should authorize the branch and return detached persisted metadata for the exact completed message', async () => {
    const result = await loadCompletedWorkbenchFiles(scope, receipt)
    expect(result).toEqual(files)
    expect(result).not.toBe(files)
    expect(api.branch).toHaveBeenCalledTimes(2)
    expect(api.history).toHaveBeenCalledTimes(1)
  })
  it('should reject an unfinished receipt before reading any file metadata', async () => {
    await expect(
      loadCompletedWorkbenchFiles(scope, { ...receipt, outcome: null }),
    ).rejects.toThrow()
    expect(api.branch).not.toHaveBeenCalled()
  })
  it('should reject files from a different completed message', async () => {
    await expect(
      loadCompletedWorkbenchFiles(scope, { ...receipt, message_id: scope.installed_app_id }),
    ).rejects.toThrow()
  })
  it('should not read native files after branch authorization is denied', async () => {
    api.branch.mockRejectedValue(new Error('denied'))
    await expect(loadCompletedWorkbenchFiles(scope, receipt)).rejects.toThrow()
    expect(api.history).not.toHaveBeenCalled()
  })
  it('should cancel before issuing a read if the caller has left the session', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(loadCompletedWorkbenchFiles(scope, receipt, controller.signal)).rejects.toThrow()
    expect(api.branch).not.toHaveBeenCalled()
  })
})
