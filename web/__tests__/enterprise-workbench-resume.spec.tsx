import type { UrlUpdateEvent } from 'nuqs/adapters/testing'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { WorkbenchHistory } from '@/app/components/enterprise/workbench/history-page'
vi.mock('@/next/dynamic', () => import('@/__mocks__/next-dynamic'))
beforeAll(async () => {
  await import('@/app/components/base/markdown/streamdown-wrapper')
})
const api = vi.hoisted(() => ({ list: vi.fn(), branch: vi.fn(), history: vi.fn(), send: vi.fn() }))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('actor'),
}))
vi.mock('@/context/workspace-state', async () => ({
  currentWorkspaceIdAtom: (await import('jotai')).atom('workspace'),
  isCurrentWorkspaceDatasetOperatorAtom: (await import('jotai')).atom(false),
}))
vi.mock('@/service/enterprise-business/workbench-send', () => ({ sendWorkbenchMessage: api.send }))
vi.mock('@/service/client', () => ({
  consoleClient: {
    business: {
      workbench: { apps: { byInstalledAppId: { branches: { byBranchId: { get: api.branch } } } } },
    },
    installedApps: { byInstalledAppId: { messages: { get: api.history } } },
  },
  consoleQuery: {
    business: {
      workbench: {
        apps: {
          byInstalledAppId: {
            branches: {
              get: { queryOptions: (options: object) => ({ ...options, queryFn: api.list }) },
              byBranchId: {
                messages: {
                  byClientMessageId: {
                    get: { queryOptions: (options: object) => ({ ...options, queryFn: vi.fn() }) },
                  },
                },
              },
            },
          },
        },
      },
    },
  },
}))
const appId = '00000000-0000-4000-8000-000000000001'
const branch = {
  scope: {
    workspace_id: 'workspace',
    actor_id: 'actor',
    installed_app_id: appId,
    branch_id: 'saved',
  },
  revision: 2,
  state: 'ready',
  conversation_id: '00000000-0000-4000-8000-000000000002',
  head_message_id: '00000000-0000-4000-8000-000000000003',
}
beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue({ items: [branch] })
  api.branch.mockResolvedValue(branch)
  api.history.mockResolvedValue({
    data: [
      {
        id: branch.head_message_id,
        conversation_id: branch.conversation_id,
        parent_message_id: null,
        query: 'Past question',
        answer: '## Past answer',
        inputs: { batch: 'saved-batch' },
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
  api.send.mockImplementation(async function* () {
    yield { event: 'ping' }
  })
})
function mount(searchParams = '') {
  const onUrlUpdate = vi.fn<(event: UrlUpdateEvent) => void>()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = createStore()
  const surface = (query: string) => (
    <NuqsTestingAdapter
      searchParams={query}
      onUrlUpdate={onUrlUpdate}
      hasMemory
      resetUrlUpdateQueueOnMount={false}
    >
      <QueryClientProvider client={client}>
        <Provider store={store}>
          <WorkbenchHistory installedAppId={appId} />
        </Provider>
      </QueryClientProvider>
    </NuqsTestingAdapter>
  )
  const view = render(surface(searchParams))
  return { ...view, onUrlUpdate, navigate: (query: string) => view.rerender(surface(query)) }
}
describe('workbench history selection and resume', () => {
  it('should preserve an unsent draft across leaving and returning to the branch', async () => {
    const { navigate } = mount('?branch=saved')
    await screen.findByRole('heading', { name: 'Past answer' })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Keep this draft' } })
    navigate('')
    await screen.findByRole('button', { name: 'saved' })
    navigate('?branch=saved')
    await screen.findByRole('heading', { name: 'Past answer' })
    expect(screen.getByRole('textbox')).toHaveValue('Keep this draft')
    expect(api.send).not.toHaveBeenCalled()
  })
  it('should follow external URL navigation and reauthorize the restored selection', async () => {
    const { navigate } = mount('?branch=saved')
    await screen.findByRole('heading', { name: 'Past answer' })
    navigate('')
    await screen.findByRole('button', { name: 'saved' })
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    navigate('?branch=saved')
    await screen.findByRole('heading', { name: 'Past answer' })
    expect(api.branch).toHaveBeenCalledTimes(4)
    expect(api.send).not.toHaveBeenCalled()
  })
  it('should write and clear the selected branch while preserving unrelated URL parameters', async () => {
    const { onUrlUpdate } = mount('?filter=active')
    fireEvent.click(await screen.findByRole('button', { name: 'saved' }))
    await screen.findByRole('heading', { name: 'Past answer' })
    await waitFor(() =>
      expect(onUrlUpdate.mock.lastCall?.[0].searchParams.get('branch')).toBe('saved'),
    )
    expect(onUrlUpdate.mock.lastCall?.[0].searchParams.get('filter')).toBe('active')
    expect(onUrlUpdate.mock.lastCall?.[0].options.history).toBe('push')
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.back' }))
    await screen.findByRole('button', { name: 'saved' })
    await waitFor(() =>
      expect(onUrlUpdate.mock.lastCall?.[0].searchParams.has('branch')).toBe(false),
    )
    expect(onUrlUpdate.mock.lastCall?.[0].searchParams.get('filter')).toBe('active')
  })
  it.each(['', ' padded ', 'x'.repeat(129)])(
    'should show the directory without requesting an invalid URL branch: %s',
    async (value) => {
      mount(`?branch=${encodeURIComponent(value)}`)
      await screen.findByRole('button', { name: 'saved' })
      expect(api.branch).not.toHaveBeenCalled()
      expect(api.history).not.toHaveBeenCalled()
    },
  )
  it('should resume a branch directly from its URL without listing or sending', async () => {
    mount('?branch=saved')
    await screen.findByRole('heading', { name: 'Past answer' })
    expect(api.list).not.toHaveBeenCalled()
    expect(api.branch).toHaveBeenCalledTimes(2)
    expect(api.send).not.toHaveBeenCalled()
  })
  it('should still require native branch authorization for a direct URL', async () => {
    api.branch.mockRejectedValue(new Error('denied'))
    mount('?branch=saved')
    await screen.findByRole('alert')
    expect(api.branch).toHaveBeenCalledOnce()
    expect(api.history).not.toHaveBeenCalled()
    expect(api.send).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })
  it('should reauthorize and reload when a conversation is selected again', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'saved' }))
    await screen.findByRole('heading', { name: 'Past answer' })
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.back' }))
    fireEvent.click(await screen.findByRole('button', { name: 'saved' }))
    await screen.findByRole('heading', { name: 'Past answer' })
    expect(api.branch).toHaveBeenCalledTimes(4)
    expect(api.history).toHaveBeenCalledTimes(2)
    expect(api.send).not.toHaveBeenCalled()
  })
  it('should read, display and continue a selected conversation using its saved inputs', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'saved' }))
    await screen.findByRole('heading', { name: 'Past answer' })
    expect(api.send).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Continue' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    await waitFor(() => expect(api.send).toHaveBeenCalledOnce())
    expect(api.send.mock.calls[0]?.[0].body.payload).toEqual({
      inputs: { batch: 'saved-batch' },
      query: 'Continue',
      conversation_id: branch.conversation_id,
      parent_message_id: branch.head_message_id,
    })
  })
  it('should withhold the composer and allow explicit retry after access fails', async () => {
    api.branch.mockRejectedValueOnce(new Error('private')).mockResolvedValue(branch)
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'saved' }))
    expect(await screen.findByRole('alert')).not.toHaveTextContent('private')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(api.history).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    await screen.findByRole('heading', { name: 'Past answer' })
  })
})
