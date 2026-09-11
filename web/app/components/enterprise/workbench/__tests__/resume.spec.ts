import type { BranchContext } from '@enterprise/business-contracts/types'
import { loadWorkbenchResume } from '../resume'
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
  branch_id: 'saved',
}
const branch: BranchContext = {
  scope,
  revision: 2,
  state: 'ready',
  conversation_id: '00000000-0000-4000-8000-000000000002',
  head_message_id: '00000000-0000-4000-8000-000000000003',
}
beforeEach(() => {
  vi.clearAllMocks()
  api.branch.mockResolvedValue(branch)
  api.history.mockResolvedValue({
    data: [
      {
        id: branch.head_message_id,
        conversation_id: branch.conversation_id,
        parent_message_id: null,
        query: 'saved question',
        answer: 'saved answer',
        inputs: { batch: 'original' },
        status: 'normal',
        total_tokens: 0,
        agent_thoughts: [],
        extra_contents: [],
        message_files: [],
        retriever_resources: [],
      },
    ],
    limit: 100,
    has_more: false,
  })
})
describe('workbench resume snapshot', () => {
  it('should authorize before reading native history and recheck the branch afterwards', async () => {
    const snapshot = await loadWorkbenchResume(scope)
    expect(snapshot.inputs).toEqual({ batch: 'original' })
    expect(snapshot.history).toHaveLength(1)
    expect(api.branch).toHaveBeenCalledTimes(2)
    expect(api.branch.mock.invocationCallOrder[0]).toBeLessThan(
      api.history.mock.invocationCallOrder[0]!,
    )
    expect(api.branch.mock.invocationCallOrder[1]).toBeGreaterThan(
      api.history.mock.invocationCallOrder[0]!,
    )
  })
  it('should not read history when branch access is denied', async () => {
    api.branch.mockRejectedValue(new Error('denied'))
    await expect(loadWorkbenchResume(scope)).rejects.toThrow()
    expect(api.history).not.toHaveBeenCalled()
  })
  it('should reject a foreign branch before accessing native messages', async () => {
    api.branch.mockResolvedValue({ ...branch, scope: { ...scope, actor_id: 'foreign' } })
    await expect(loadWorkbenchResume(scope)).rejects.toThrow('Workbench resume unavailable')
    expect(api.history).not.toHaveBeenCalled()
  })
  it('should reject a branch changed during history loading', async () => {
    api.branch.mockResolvedValueOnce(branch).mockResolvedValueOnce({ ...branch, revision: 3 })
    await expect(loadWorkbenchResume(scope)).rejects.toThrow('Workbench resume unavailable')
  })
  it('should not substitute current app defaults for a root without saved inputs', async () => {
    api.branch.mockResolvedValue({ ...branch, conversation_id: null, head_message_id: null })
    await expect(loadWorkbenchResume(scope)).rejects.toThrow('Workbench resume unavailable')
    expect(api.history).not.toHaveBeenCalled()
  })
})
