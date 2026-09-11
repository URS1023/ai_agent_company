import type { ExploreMessageListItem } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { BranchContext } from '@enterprise/business-contracts/types'
import type { sendWorkbenchMessage } from '@/service/enterprise-business/workbench-send'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkbenchSession } from '../session'
vi.mock('@/next/dynamic', () => import('@/__mocks__/next-dynamic'))
beforeAll(async () => {
  await import('@/app/components/base/markdown/streamdown-wrapper')
})
const { send, lookup, readBranch, readHistory } = vi.hoisted(() => ({
  send: vi.fn(),
  lookup: vi.fn(),
  readBranch: vi.fn(),
  readHistory: vi.fn(),
}))
vi.mock('@/service/enterprise-business/workbench-send', () => ({ sendWorkbenchMessage: send }))
vi.mock('@/service/client', () => ({
  consoleClient: {
    business: {
      workbench: { apps: { byInstalledAppId: { branches: { byBranchId: { get: readBranch } } } } },
    },
    installedApps: { byInstalledAppId: { messages: { get: readHistory } } },
  },
  consoleQuery: {
    business: {
      workbench: {
        apps: {
          byInstalledAppId: {
            branches: {
              byBranchId: {
                messages: {
                  byClientMessageId: {
                    get: {
                      queryOptions: (options: { queryKey: readonly unknown[] }) => ({
                        ...options,
                        queryFn: lookup,
                      }),
                    },
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
const branch: BranchContext = {
  scope: {
    workspace_id: 'workspace',
    actor_id: 'actor',
    installed_app_id: '00000000-0000-4000-8000-000000000001',
    branch_id: 'branch',
  },
  state: 'ready',
  revision: 1,
  conversation_id: null,
  head_message_id: null,
  inflight_client_message_id: null,
}
const identity = {
  conversation_id: '00000000-0000-4000-8000-000000000003',
  message_id: '00000000-0000-4000-8000-000000000004',
  task_id: 'task',
}
function mount(initialBranch = branch, history?: ExploreMessageListItem[]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <Provider store={createStore()}>
      <QueryClientProvider client={queryClient}>
        <WorkbenchSession
          title="设备助手"
          branch={initialBranch}
          inputs={{ count: 0 }}
          history={history}
        />
      </QueryClientProvider>
    </Provider>,
  )
}
function submit() {
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '检查设备' } })
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
}
describe('WorkbenchSession', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    readBranch.mockRejectedValue(new Error('not configured'))
    readHistory.mockRejectedValue(new Error('not configured'))
  })
  it.each([false, true])(
    'should load completed attachments without replaying generation (explicit read retry: %s)',
    async (retryRead) => {
      readBranch.mockResolvedValue({
        ...branch,
        revision: 3,
        conversation_id: identity.conversation_id,
        head_message_id: identity.message_id,
      })
      readHistory.mockResolvedValue({
        data: [
          {
            id: identity.message_id,
            conversation_id: identity.conversation_id,
            parent_message_id: null,
            query: '检查设备',
            answer: 'Done',
            inputs: { count: 0 },
            status: 'normal',
            total_tokens: 0,
            agent_thoughts: [],
            extra_contents: [],
            retriever_resources: [],
            message_files: [
              {
                id: 'persisted-report',
                filename: 'quality-report.pdf',
                type: 'document',
                transfer_method: 'tool_file',
                belongs_to: 'assistant',
                url: '/files/report?sign=real',
              },
            ],
          },
        ],
        limit: 100,
        has_more: false,
      })
      send.mockImplementation(async function* (input: Parameters<typeof sendWorkbenchMessage>[0]) {
        yield { event: 'message_end', ...identity, files: [] }
        yield {
          event: 'enterprise_send_state',
          data: {
            ...identity,
            client_message_id: input.body.client_message_id,
            status: 'accepted',
            revision: 4,
            outcome: 'succeeded',
          },
        }
      })
      mount()
      if (retryRead) readBranch.mockRejectedValueOnce(new Error('temporary read failure'))
      submit()
      if (retryRead) {
        expect(await screen.findByRole('alert')).toHaveTextContent(
          'common.fileUploader.uploadFromComputerReadError',
        )
        expect(readBranch).toHaveBeenCalledTimes(1)
        expect(readHistory).not.toHaveBeenCalled()
        fireEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
      }
      expect(await screen.findByRole('link', { name: 'quality-report.pdf' })).toHaveAttribute(
        'href',
        '/files/report?sign=real',
      )
      expect(readBranch).toHaveBeenCalledTimes(retryRead ? 3 : 2)
      expect(readHistory).toHaveBeenCalledTimes(1)
      expect(send).toHaveBeenCalledTimes(1)
    },
  )
  it('should expose a generated file during this send without inventing a filename or successful outcome', async () => {
    const fileId = '00000000-0000-4000-8000-000000000009'
    send.mockImplementation(async function* () {
      yield {
        event: 'message_file',
        ...identity,
        id: fileId,
        type: 'image',
        belongs_to: 'assistant',
        url: '/files/output?sign=abc',
      }
    })
    mount()
    submit()
    expect(await screen.findByRole('link', { name: fileId })).toHaveAttribute(
      'href',
      '/files/output?sign=abc',
    )
    expect(screen.queryByText(/generated_image/)).not.toBeInTheDocument()
    expect(
      await screen.findByRole('button', { name: 'common.enterprise.workbench.checkState' }),
    ).toBeInTheDocument()
    expect(send).toHaveBeenCalledTimes(1)
  })
  it('should connect a send, stream answer and terminal state without duplicating rapid submission', async () => {
    send.mockImplementation(async function* (input: Parameters<typeof sendWorkbenchMessage>[0]) {
      yield { event: 'message', ...identity, answer: '运行正常' }
      yield {
        event: 'enterprise_send_state',
        data: {
          ...identity,
          client_message_id: input.body.client_message_id,
          status: 'accepted',
          revision: 4,
          outcome: 'succeeded',
        },
      }
    })
    mount()
    submit()
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    await screen.findByText('运行正常')
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'common.enterprise.devices.state.succeeded',
      ),
    )
    expect(send).toHaveBeenCalledOnce()
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(lookup).not.toHaveBeenCalled()
  })
  it('should retain the draft after a failed stream and check durable state without resending', async () => {
    send.mockImplementation(async function* () {
      yield { event: 'message', ...identity, answer: '部分结果' }
      throw new Error('connection lost')
    })
    mount()
    submit()
    await screen.findByRole('button', { name: 'common.enterprise.workbench.checkState' })
    expect(screen.getByRole('textbox')).toHaveValue('检查设备')
    const id = send.mock.calls[0]?.[0].body.client_message_id
    lookup.mockResolvedValue({
      ...identity,
      client_message_id: id,
      status: 'accepted',
      revision: 4,
      outcome: 'failed',
    })
    fireEvent.click(screen.getByRole('button', { name: 'common.enterprise.workbench.checkState' }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'common.enterprise.devices.state.failed',
      ),
    )
    expect(send).toHaveBeenCalledOnce()
    expect(lookup).toHaveBeenCalledOnce()
    expect(await screen.findByText('部分结果')).toBeInTheDocument()
  })
})

describe('WorkbenchSession lifecycle', () => {
  beforeEach(() => vi.clearAllMocks())
  it('should display saved history without a fabricated receipt and continue from its confirmed head', async () => {
    const saved: ExploreMessageListItem = {
      id: identity.message_id,
      conversation_id: identity.conversation_id,
      parent_message_id: null,
      query: '以前的问题',
      answer: '## 保存的回答',
      inputs: { count: 0 },
      status: 'normal',
      total_tokens: 0,
      agent_thoughts: [],
      extra_contents: [],
      message_files: [],
      retriever_resources: [],
    }
    send.mockImplementation(async function* () {
      yield { event: 'message', ...identity, answer: '继续' }
    })
    mount(
      {
        ...branch,
        conversation_id: identity.conversation_id,
        head_message_id: identity.message_id,
      },
      [saved],
    )
    expect(await screen.findByRole('heading', { name: '保存的回答' })).toBeInTheDocument()
    expect(screen.getByText('以前的问题')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(send).not.toHaveBeenCalled()
    submit()
    await waitFor(() => expect(send).toHaveBeenCalledOnce())
    expect(send.mock.calls[0]?.[0].body.payload).toEqual({
      inputs: { count: 0 },
      query: '检查设备',
      conversation_id: identity.conversation_id,
      parent_message_id: identity.message_id,
    })
  })
  it('should withhold a used session when its history is absent', () => {
    mount({
      ...branch,
      conversation_id: identity.conversation_id,
      head_message_id: identity.message_id,
    })
    expect(screen.getByRole('alert')).toHaveTextContent('common.enterprise.loadError')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(send).not.toHaveBeenCalled()
  })
  it('should keep adopted inputs and the draft when same-scope parent data refreshes', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(
      <QueryClientProvider client={client}>
        <WorkbenchSession title="助手" branch={branch} inputs={{ version: 'original' }} />
      </QueryClientProvider>,
    )
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '继续检查' } })
    view.rerender(
      <QueryClientProvider client={client}>
        <WorkbenchSession
          title="助手"
          branch={{ ...branch, revision: 2 }}
          inputs={{ version: 'refreshed' }}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByRole('textbox')).toHaveValue('继续检查')
    send.mockImplementation(async function* () {
      yield { event: 'message', ...identity, answer: '结果' }
    })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    await waitFor(() => expect(send).toHaveBeenCalledOnce())
    expect(send.mock.calls[0]?.[0].body.payload.inputs).toEqual({ version: 'original' })
  })
  it('should reject history belonging to a different native conversation', () => {
    const saved: ExploreMessageListItem = {
      id: identity.message_id,
      conversation_id: branch.scope.installed_app_id,
      parent_message_id: null,
      query: 'foreign question',
      answer: 'foreign answer',
      inputs: {},
      status: 'normal',
      total_tokens: 0,
      agent_thoughts: [],
      extra_contents: [],
      message_files: [],
      retriever_resources: [],
    }
    mount(
      {
        ...branch,
        conversation_id: identity.conversation_id,
        head_message_id: identity.message_id,
      },
      [saved],
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('foreign answer')).not.toBeInTheDocument()
    expect(send).not.toHaveBeenCalled()
  })
  it('should keep a restored history read-only while a known send is in flight', async () => {
    const saved: ExploreMessageListItem = {
      id: identity.message_id,
      conversation_id: identity.conversation_id,
      parent_message_id: null,
      query: 'saved question',
      answer: 'saved answer',
      inputs: {},
      status: 'normal',
      total_tokens: 0,
      agent_thoughts: [],
      extra_contents: [],
      message_files: [],
      retriever_resources: [],
    }
    mount(
      {
        ...branch,
        conversation_id: identity.conversation_id,
        head_message_id: identity.message_id,
        inflight_client_message_id: branch.scope.installed_app_id,
      },
      [saved],
    )
    expect(await screen.findByText('saved answer')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.workbench.checkState' }),
    ).toBeEnabled()
    expect(send).not.toHaveBeenCalled()
  })
  it('should resume observation of an in-flight ID without sending on mount', async () => {
    mount({ ...branch, inflight_client_message_id: identity.message_id })
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.workbench.checkState' }),
    ).toBeEnabled()
    expect(send).not.toHaveBeenCalled()
    expect(lookup).not.toHaveBeenCalled()
  })
  it('should use the confirmed previous head and a new client ID on the next send', async () => {
    send.mockImplementation(async function* (input: Parameters<typeof sendWorkbenchMessage>[0]) {
      yield { event: 'message', ...identity, answer: '结果' }
      yield {
        event: 'enterprise_send_state',
        data: {
          ...identity,
          client_message_id: input.body.client_message_id,
          status: 'accepted',
          revision: 4,
          outcome: 'succeeded',
        },
      }
    })
    mount()
    submit()
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''))
    submit()
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2))
    const first = send.mock.calls[0]?.[0]
    const second = send.mock.calls[1]?.[0]
    expect(second.body.client_message_id).not.toBe(first.body.client_message_id)
    expect(second.body.payload.conversation_id).toBe(identity.conversation_id)
    expect(second.body.payload.parent_message_id).toBe(identity.message_id)
  })
  it('should abort the active transport and close its iterator on unmount', async () => {
    let signal: AbortSignal | undefined
    let closed = false
    send.mockImplementation(async function* (...args: Parameters<typeof sendWorkbenchMessage>) {
      signal = args[1]?.signal
      try {
        yield { event: 'message', ...identity, answer: 'partial' }
        await new Promise<void>((resolve) =>
          signal?.addEventListener('abort', () => resolve(), { once: true }),
        )
      } finally {
        closed = true
      }
    })
    const view = mount()
    submit()
    await screen.findByText('partial')
    view.unmount()
    expect(signal?.aborted).toBe(true)
    await waitFor(() => expect(closed).toBe(true))
    expect(send).toHaveBeenCalledOnce()
  })
  it('should keep the pending turn locked when a state lookup belongs to a different message', async () => {
    mount({ ...branch, inflight_client_message_id: identity.message_id })
    lookup.mockResolvedValue({
      ...identity,
      client_message_id: identity.conversation_id,
      status: 'accepted',
      revision: 4,
      outcome: 'succeeded',
    })
    fireEvent.click(screen.getByRole('button', { name: 'common.enterprise.workbench.checkState' }))
    await waitFor(() => expect(lookup).toHaveBeenCalledOnce())
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'common.enterprise.workbench.checkState' }),
      ).toBeEnabled(),
    )
    expect(screen.getByRole('status')).toHaveTextContent('common.enterprise.workbench.waiting')
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
    expect(send).not.toHaveBeenCalled()
  })
})
