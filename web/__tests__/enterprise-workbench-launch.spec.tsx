import type { sendWorkbenchMessage } from '@/service/enterprise-business/workbench-send'
import { notifyManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { WorkbenchApplication } from '@/app/components/enterprise/workbench/application'
vi.mock('@/next/dynamic', () => import('@/__mocks__/next-dynamic'))
beforeAll(async () => {
  await import('@/app/components/base/markdown/streamdown-wrapper')
})

vi.mock('@/next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/next/navigation')>()),
  useParams: () => ({}),
  usePathname: () => '/enterprise/workbench',
}))

const api = vi.hoisted(() => ({
  installed: vi.fn(),
  parameters: vi.fn(),
  upload: vi.fn(),
  root: vi.fn(),
  lookup: vi.fn(),
  send: vi.fn(),
}))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('actor'),
}))
vi.mock('@/context/workspace-state', async () => {
  const { atom } = await import('jotai')
  return {
    currentWorkspaceIdAtom: atom('workspace'),
    isCurrentWorkspaceDatasetOperatorAtom: atom(false),
  }
})
vi.mock('@/service/enterprise-business/workbench-send', () => ({ sendWorkbenchMessage: api.send }))
vi.mock('@/service/client', () => ({
  consoleQuery: {
    installedApps: {
      get: { queryOptions: (options: object) => ({ ...options, queryFn: api.installed }) },
      byInstalledAppId: {
        parameters: {
          get: { queryOptions: (options: object) => ({ ...options, queryFn: api.parameters }) },
        },
      },
    },
    files: {
      upload: { get: { queryOptions: (options: object) => ({ ...options, queryFn: api.upload }) } },
    },
    business: {
      workbench: {
        apps: {
          byInstalledAppId: {
            branches: {
              byBranchId: {
                messages: {
                  byClientMessageId: {
                    get: {
                      queryOptions: (options: object) => ({ ...options, queryFn: api.lookup }),
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
  consoleClient: {
    business: { workbench: { apps: { byInstalledAppId: { branches: { post: api.root } } } } },
  },
}))
const appId = '00000000-0000-4000-8000-000000000001'
const config = {
  user_input_form: [{ 'text-input': { variable: 'batch', label: 'Batch', required: true } }],
}
beforeEach(() => {
  vi.clearAllMocks()
  api.installed.mockResolvedValue({
    installed_apps: [{ id: appId, app: { name: 'Device assistant', mode: 'chat' } }],
  })
  api.parameters.mockResolvedValue(config)
  api.root.mockImplementation(async ({ body }: { body: { branch_id: string } }) => ({
    scope: {
      workspace_id: 'workspace',
      actor_id: 'actor',
      installed_app_id: appId,
      branch_id: body.branch_id,
    },
    state: 'ready',
    revision: 1,
  }))
  api.send.mockImplementation(async function* (input: Parameters<typeof sendWorkbenchMessage>[0]) {
    const identity = {
      conversation_id: '00000000-0000-4000-8000-000000000002',
      message_id: '00000000-0000-4000-8000-000000000003',
      task_id: 'task-1',
    }
    yield { event: 'message', ...identity, answer: '设备正常' }
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
})
afterEach(() => vi.unstubAllGlobals())
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <Provider store={createStore()}>
      <QueryClientProvider client={client}>
        <WorkbenchApplication installedAppId={appId} />
      </QueryClientProvider>
    </Provider>,
  )
  return client
}
async function launch() {
  fireEvent.change(await screen.findByRole('textbox', { name: 'Batch' }), {
    target: { value: '001' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
  await screen.findByRole('heading', { name: 'Device assistant' })
}
describe('enterprise workbench launch integration', () => {
  it('should launch and send when the browser exposes getRandomValues but not randomUUID', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    mount()
    await launch()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Check over LAN HTTP' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    await screen.findByText('设备正常')
    const rootId = api.root.mock.calls[0]?.[0].body.branch_id
    const messageId = api.send.mock.calls[0]?.[0].body.client_message_id
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
    expect(rootId).toMatch(uuid)
    expect(messageId).toMatch(uuid)
    expect(messageId).not.toBe(rootId)
  })
  it('should display an existing native file default and include its reference in the first send', async () => {
    api.parameters.mockResolvedValue({
      user_input_form: [
        {
          file: {
            variable: 'report',
            label: 'Report',
            allowed_file_types: ['document'],
            allowed_file_upload_methods: ['local_file'],
            default: {
              filename: 'report.pdf',
              size: 42,
              mime_type: 'application/pdf',
              related_id: appId,
              transfer_method: 'local_file',
              type: 'document',
            },
          },
        },
      ],
    })
    api.upload.mockResolvedValue({
      attachment_image_file_size_limit: 2,
      audio_file_size_limit: 50,
      batch_count_limit: 5,
      file_size_limit: 15,
      file_upload_limit: 5,
      image_file_batch_limit: 10,
      image_file_size_limit: 10,
      single_chunk_attachment_limit: 10,
      video_file_size_limit: 100,
      workflow_file_upload_limit: 10,
    })
    mount()
    await screen.findByText('report.pdf')
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('heading', { name: 'Device assistant' })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Analyze report' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    await screen.findByText('设备正常')
    expect(api.send.mock.calls[0]?.[0].body.payload.inputs).toEqual({
      report: { type: 'document', transfer_method: 'local_file', upload_file_id: appId, url: '' },
    })
  })
  it('should connect actual config, parameter form, root launcher, session and streamed state', async () => {
    mount()
    await launch()
    expect(api.send).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '检查设备' } })
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    await screen.findByText('设备正常')
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'common.enterprise.devices.state.succeeded',
      ),
    )
    expect(api.root).toHaveBeenCalledTimes(1)
    expect(api.send).toHaveBeenCalledTimes(1)
    expect(api.send.mock.calls[0]?.[0].body.payload.inputs).toEqual({ batch: '001' })
  })
  it('should preserve the launched session and draft when query cache receives a newer configuration', async () => {
    const client = mount()
    await launch()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'unsent draft' } })
    await act(async () => {
      client.setQueryData(
        ['enterprise-workbench-config', 'workspace', 'actor', appId, 'parameters'],
        { user_input_form: [] },
      )
      await new Promise<void>((resolve) => notifyManager.schedule(resolve))
    })
    expect(screen.getByRole('heading', { name: 'Device assistant' })).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('unsent draft')
    expect(api.root).toHaveBeenCalledTimes(1)
  })
})
