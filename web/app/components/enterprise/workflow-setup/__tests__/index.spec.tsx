import type { contract } from '@enterprise/business-contracts/orpc'
import type { SetupView } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkflowSetup } from '../index'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  provisioningList: vi.fn<Client['workflowSetups']['bySetupId']['provisioning']['get']>(),
  profiles: vi.fn<Client['workflowSetups']['bySetupId']['provisioningProfiles']['get']>(),
  provisioningStart: vi.fn<Client['workflowSetups']['bySetupId']['provisioning']['post']>(),
  list: vi.fn<Client['devices']['byDeviceId']['bindings']['byScenario']['workflowSetups']['get']>(),
  create:
    vi.fn<Client['devices']['byDeviceId']['bindings']['byScenario']['workflowSetups']['post']>(),
  sources: vi.fn<Client['sources']['get']>(),
  get: vi.fn<Client['workflowSetups']['bySetupId']['get']>(),
}))
vi.mock('@/service/client', async () => {
  const business = {
    devices: {
      byDeviceId: {
        bindings: { byScenario: { workflowSetups: { get: api.list, post: api.create } } },
      },
    },
    sources: { get: api.sources },
    workflowSetups: {
      bySetupId: {
        get: api.get,
        provisioning: { get: api.provisioningList, post: api.provisioningStart },
        provisioningProfiles: { get: api.profiles },
      },
    },
    workflowProvisioning: { byProvisioningId: { get: vi.fn(), advance: { post: vi.fn() } } },
  }
  return {
    consoleClient: { business },
    consoleQuery: {
      business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils(business),
    },
  }
})
function setup(overrides: Partial<SetupView> = {}): SetupView {
  return {
    id: 'setup-1',
    workspace_id: 'workspace-1',
    device_id: 'device-1',
    scenario: 'alert',
    name: 'Default alert',
    source_id: 'source-1',
    source_revision: 's1',
    read_id: 'read-1',
    read_revision: 'r1',
    expected_source_revision: 4,
    expected_binding_revision: null,
    state: 'draft_ready',
    revision: 2,
    app_id: 'app-1',
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
    ...overrides,
  }
}
function mount(disabled = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const tree = (next: boolean) => (
    <QueryClientProvider client={client}>
      <WorkflowSetup
        deviceId="device-1"
        scenario="alert"
        scope={['enterprise-business', 'workspace-1', 'user-1']}
        expectedBindingRevision={null}
        disabled={next}
      />
    </QueryClientProvider>
  )
  const result = render(tree(disabled))
  return { ...result, update: (next: boolean) => result.rerender(tree(next)) }
}
beforeEach(() => {
  vi.resetAllMocks()
  api.provisioningList.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 })
  api.profiles.mockResolvedValue([
    { config_ref: 'profile', config_revision: 1, display_name: 'Production' },
  ])
  api.list.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 })
  api.sources.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 })
})
describe('WorkflowSetup', () => {
  it('opens real persisted draft history without treating import as published', async () => {
    api.list.mockResolvedValue({ items: [setup()], total: 1, offset: 0, limit: 20 })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    expect(
      await screen.findByRole('link', { name: 'common.enterprise.setup.openDraft' }),
    ).toHaveAttribute('href', '/app/app-1/workflow')
    expect(screen.getByText('common.enterprise.setup.remaining')).toBeInTheDocument()
  })
  it('rejects cross-workspace responses', async () => {
    api.list.mockResolvedValue({
      items: [setup({ workspace_id: 'other' })],
      total: 1,
      offset: 0,
      limit: 20,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: 'common.enterprise.setup.openDraft' }),
    ).not.toBeInTheDocument()
  })
  it('retains unresolved history through binding refresh and close', async () => {
    api.list.mockResolvedValue({
      items: [setup({ state: 'uncertain', app_id: null })],
      total: 1,
      offset: 0,
      limit: 20,
    })
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    expect(await screen.findByText('common.enterprise.setup.state.uncertain')).toBeInTheDocument()
    view.update(true)
    expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.close' }))
    view.update(false)
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    await waitFor(() =>
      expect(screen.getByText('common.enterprise.setup.state.uncertain')).toBeInTheDocument(),
    )
    expect(api.create).not.toHaveBeenCalled()
  })
})

function source(): Awaited<ReturnType<Client['sources']['get']>>['items'][number] {
  return {
    name: 'Plant readings',
    enabled: true,
    workspace_id: 'workspace-1',
    source_id: 'source-1',
    source_revision: 's1',
    read_id: 'read-1',
    read_revision: 'r1',
    revision: 4,
    device_ids: ['device-1'],
    device_parameter: 'device',
    device_column: 'device_code',
    scope_attribute: 'device_code',
    parameters: [],
    connection_status: 'not_tested',
    connection: {
      kind: 'db',
      dialect: 'postgresql',
      host: 'db.internal',
      port: 5432,
      database: 'readings',
      tls: true,
      sql: 'SELECT * FROM public.measurements WHERE device_code = :device',
      allowed_tables: ['public.measurements'],
      credentials_configured: true,
    },
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  }
}
async function selectSource() {
  await userEvent.click(
    await screen.findByRole('combobox', { name: 'common.enterprise.devices.source' }),
  )
  await userEvent.click(await screen.findByRole('option', { name: 'Plant readings · 4' }))
}
describe('Workflow setup recovery and pagination', () => {
  it('preserves the same key and frozen payload after a lost response, close and binding refresh', async () => {
    api.sources.mockResolvedValue({ items: [source()], total: 1, offset: 0, limit: 20 })
    api.create.mockRejectedValueOnce(new Error('lost response')).mockResolvedValueOnce(setup())
    api.get.mockResolvedValue(setup())
    const view = mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    await selectSource()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.create' }))
    expect(await screen.findByText('common.enterprise.setup.state.uncertain')).toBeInTheDocument()
    const original = api.create.mock.calls[0]?.[0]
    expect(original).toBeDefined()
    if (!original) throw new Error('Expected the initial setup request')
    expect(original.body).toEqual({
      source_id: 'source-1',
      expected_source_revision: 4,
      expected_binding_revision: null,
    })
    expect(original.headers['idempotency-key']).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.close' }))
    api.sources.mockResolvedValue({
      items: [{ ...source(), revision: 5 }],
      total: 1,
      offset: 0,
      limit: 20,
    })
    view.update(true)
    view.update(false)
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.setup.retryAttempt' }),
    )
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
    expect(api.create.mock.calls[1]?.[0]).toEqual(original)
    expect(
      await screen.findByRole('link', { name: 'common.enterprise.setup.openDraft' }),
    ).toBeInTheDocument()
  })
  it('reads older persisted operations before enabling creation and blocks an unresolved old operation', async () => {
    api.list.mockImplementation(async ({ query }) =>
      query?.offset === 20
        ? {
            items: [setup({ id: 'older', state: 'importing', app_id: null })],
            total: 21,
            offset: 20,
            limit: 20,
          }
        : { items: [setup()], total: 21, offset: 0, limit: 20 },
    )
    api.sources.mockResolvedValue({ items: [source()], total: 1, offset: 0, limit: 20 })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    await selectSource()
    expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeDisabled()
    await userEvent.click(
      screen.getByRole('button', { name: 'common.enterprise.setup.moreHistory' }),
    )
    expect(await screen.findByText('common.enterprise.setup.state.importing')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeDisabled()
    expect(api.list.mock.calls[1]?.[0].query?.offset).toBe(20)
  })
  it('paginates sources instead of silently limiting selection to the first page', async () => {
    api.sources.mockImplementation(async ({ query }) =>
      query?.offset === 20
        ? { items: [source()], total: 21, offset: 20, limit: 20 }
        : { items: [{ ...source(), device_ids: ['other'] }], total: 21, offset: 0, limit: 20 },
    )
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    expect(await screen.findByText('common.enterprise.setup.noSources')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await selectSource()
    expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeEnabled()
  })
  it('shows an unavailable service without exposing a create action as enabled', async () => {
    const { ORPCError } = await import('@orpc/client')
    api.list.mockRejectedValue(new ORPCError('SERVICE_UNAVAILABLE'))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    expect(await screen.findByText('common.enterprise.setup.unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeDisabled()
  })
})

describe('Workflow setup response integrity', () => {
  it.each([
    { workspace_id: 'another-workspace' },
    { device_id: 'another-device' },
    { scenario: 'quality' as const },
    { read_revision: 'another-read' },
  ])('blocks recovery and shows a mismatch instead of accepting %o', async (overrides) => {
    api.sources.mockResolvedValue({ items: [source()], total: 1, offset: 0, limit: 20 })
    api.create.mockResolvedValue(setup(overrides))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    await selectSource()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.create' }))
    expect(await screen.findByText('common.enterprise.devices.accessDenied')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.setup.retryAttempt' }),
    ).toBeDisabled()
    expect(
      screen.queryByRole('link', { name: 'common.enterprise.setup.openDraft' }),
    ).not.toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalled()
  })
  it('never offers an editor for confirmation-required imports', async () => {
    api.list.mockResolvedValue({
      items: [setup({ state: 'confirmation_required' })],
      total: 1,
      offset: 0,
      limit: 20,
    })
    api.get.mockResolvedValue(setup({ state: 'confirmation_required' }))
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.setup.inspect' }),
    )
    expect(
      await screen.findByText('common.enterprise.setup.state.confirmation_required'),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: 'common.enterprise.setup.openDraft' }),
    ).not.toBeInTheDocument()
  })
})

describe('Current source eligibility', () => {
  it('should exclude disabled sources from new workflow setup', async () => {
    api.sources.mockResolvedValue({
      items: [{ ...source(), enabled: false }],
      total: 1,
      offset: 0,
      limit: 20,
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
    expect(await screen.findByText('common.enterprise.setup.noSources')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeDisabled()
    expect(api.create).not.toHaveBeenCalled()
  })

  it.each([{ revision: 5 }, { device_ids: ['other-device'] }, { enabled: false }])(
    'requires reselection when a pre-attempt source changes: %o',
    async (changes) => {
      api.sources.mockResolvedValue({ items: [source()], total: 1, offset: 0, limit: 20 })
      mount()
      await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
      await selectSource()
      expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeEnabled()
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.close' }))
      api.sources.mockResolvedValue({
        items: [{ ...source(), ...changes }],
        total: 1,
        offset: 0,
        limit: 20,
      })
      await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
      await waitFor(() => expect(api.sources).toHaveBeenCalledTimes(2))
      expect(screen.getByRole('button', { name: 'common.enterprise.setup.create' })).toBeDisabled()
      expect(api.create).not.toHaveBeenCalled()
    },
  )
})

it('retains the frozen provisioning start attempt through dialog close and reopen', async () => {
  api.list.mockResolvedValue({ items: [setup()], total: 1, offset: 0, limit: 20 })
  api.get.mockResolvedValue(setup())
  api.provisioningStart.mockRejectedValue(new Error('lost'))
  mount()
  await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
  await userEvent.click(
    await screen.findByRole('button', { name: 'common.enterprise.setup.inspect' }),
  )
  await userEvent.click(
    await screen.findByRole('combobox', { name: 'common.enterprise.provisioning.profile' }),
  )
  await userEvent.click(await screen.findByRole('option', { name: 'Production · 1' }))
  await userEvent.click(
    screen.getByRole('button', { name: 'common.enterprise.provisioning.start' }),
  )
  expect(await screen.findByText('common.enterprise.provisioning.review')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'common.operation.close' }))
  await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.setup.title' }))
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.start' }),
  ).toBeDisabled()
  expect(api.provisioningStart).toHaveBeenCalledTimes(1)
})
