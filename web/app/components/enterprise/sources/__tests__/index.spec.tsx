import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider } from 'jotai'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { SourcesPage } from '../index'

type Client = ContractRouterClient<typeof contract>
const workspaceAtom = await vi.hoisted(async () =>
  (await import('jotai')).atom({ id: 'workspace-1', name: 'Plant' }),
)
const api = vi.hoisted(() => ({
  access: vi.fn<Client['me']['get']>(),
  capabilities: vi.fn<Client['sources']['capabilities']['get']>(),
  list: vi.fn<Client['sources']['get']>(),
  get: vi.fn<Client['sources']['bySourceId']['get']>(),
  create: vi.fn<Client['sources']['post']>(),
  update: vi.fn<Client['sources']['bySourceId']['put']>(),
  devices: vi.fn<Client['devices']['get']>(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      me: { get: api.access },
      devices: { get: api.devices },
      sources: {
        capabilities: { get: api.capabilities },
        get: api.list,
        post: api.create,
        bySourceId: { get: api.get, put: api.update },
      },
    }),
  },
}))
vi.mock('@/context/workspace-state', () => ({ currentWorkspaceAtom: workspaceAtom }))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('user-1'),
}))

function source(): Awaited<ReturnType<Client['sources']['bySourceId']['get']>> {
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
    department_parameter: null,
    parameters: [],
    limits: { max_rows: 1000, max_bytes: 2097152, max_pages: 20, timeout_seconds: 15 },
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
    connection_status: 'not_tested',
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  }
}
function renderSources(searchParams = '') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const store = createStore()
  const onUrlUpdate = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <Provider store={store}>
        <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate}>
          <SourcesPage />
        </NuqsTestingAdapter>
      </Provider>
    </QueryClientProvider>,
  )
  return { client, store, onUrlUpdate }
}
async function fillDatabase() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'common.enterprise.sources.create' }))
  for (const [key, value] of Object.entries({
    name: 'New readings',
    host: 'db.internal',
    database: 'readings',
    username: 'reader',
    password: 'test-only-password',
    allowedTables: 'public.measurements',
    sql: 'SELECT * FROM public.measurements WHERE device_code = :device',
  })) {
    await user.type(screen.getByLabelText(`common.enterprise.sources.${key}`), value)
  }
  await user.click(await screen.findByRole('checkbox', { name: '0001 · Pump' }))
  return user
}

describe('Sources ordinary configuration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    api.capabilities.mockResolvedValue({ can_manage: true, write_enabled: true, reason_code: null })
    api.list.mockResolvedValue({ items: [source()], total: 1, offset: 0, limit: 20 })
    api.get.mockResolvedValue(source())
    api.create.mockResolvedValue(source())
    api.update.mockResolvedValue({ ...source(), revision: 8 })
    api.devices.mockResolvedValue({
      items: [
        {
          id: 'device-1',
          workspace_id: 'workspace-1',
          device_code: '0001',
          name: 'Pump',
          department: 'Plant',
          description: '',
          revision: 1,
          created_at: '2026-09-08T00:00:00Z',
          updated_at: '2026-09-08T00:00:00Z',
          deleted_at: null,
        },
      ],
      total: 1,
      offset: 0,
      limit: 20,
    })
  })

  it('should read actual sources and gate editing by source capabilities, not device permissions', async () => {
    renderSources()
    expect(await screen.findByRole('heading', { name: 'Plant readings' })).toBeInTheDocument()
    expect(screen.getByText(/common.enterprise.sources.notTested/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.enterprise.sources.create' })).toBeEnabled()
  })

  it.each([
    { can_manage: false, write_enabled: true, reason_code: null },
    { can_manage: true, write_enabled: false, reason_code: 'encryption_key_missing' },
  ] as const)('should retain read access when writes are unavailable: %o', async (capabilities) => {
    api.capabilities.mockResolvedValue(capabilities)
    renderSources()
    await screen.findByRole('heading', { name: 'Plant readings' })
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.sources.create' }),
    ).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.sources.details' }))
    expect(await screen.findByLabelText('common.enterprise.sources.host')).toHaveValue(
      'db.internal',
    )
    expect(
      screen.queryByRole('button', { name: 'common.operation.confirm' }),
    ).not.toBeInTheDocument()
  })

  it('should submit a database draft only after explicit read-only confirmation', async () => {
    const { client } = renderSources()
    const user = await fillDatabase()
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('common.enterprise.sources.invalid')
    expect(api.create).not.toHaveBeenCalled()
    await user.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce())
    expect(api.create.mock.calls[0]?.[0]).toMatchObject({
      body: {
        device_ids: ['device-1'],
        device_parameter: 'device',
        read_only_confirmed: true,
        connection: { username: 'reader', password: 'test-only-password', port: 5432 },
      },
      headers: { Origin: window.location.origin, 'idempotency-key': expect.any(String) },
    })
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data),
      ),
    ).not.toContain('test-only-password')
  })

  it.each([false, true])(
    'should save the selected lifecycle state of a disabled source: %s',
    async (enable) => {
      api.get.mockResolvedValue({ ...source(), enabled: false })
      api.list.mockResolvedValue({
        items: [{ ...source(), enabled: false }],
        total: 1,
        offset: 0,
        limit: 20,
      })
      renderSources()
      expect(await screen.findByText('common.modelProvider.selector.disabled')).toBeInTheDocument()
      await userEvent.click(
        screen.getByRole('button', { name: 'common.enterprise.sources.details' }),
      )
      const checkbox = await screen.findByRole('checkbox', {
        name: 'common.enterprise.schedule.enabled',
      })
      expect(checkbox).not.toBeChecked()
      if (enable) await userEvent.click(checkbox)
      await userEvent.click(
        screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
      )
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      await waitFor(() => expect(api.update).toHaveBeenCalledOnce())
      expect(api.update.mock.calls[0]?.[0]).toMatchObject({ body: { enabled: enable } })
    },
  )

  it('should fetch the latest source before editing and use that fixed revision with blank credential retention', async () => {
    api.get.mockResolvedValue({ ...source(), revision: 7 })
    renderSources()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.details' }),
    )
    await screen.findByLabelText('common.enterprise.sources.host')
    expect(screen.getByLabelText('common.enterprise.sources.username')).toHaveValue('')
    expect(screen.getByLabelText('common.enterprise.sources.password')).toHaveValue('')
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledOnce())
    expect(api.update.mock.calls[0]?.[0]).toMatchObject({
      params: { source_id: 'source-1' },
      headers: { 'if-match': '"7"' },
      body: { connection: { username: null, password: null } },
    })
  })

  it.each([new Error('response lost'), new ORPCError('CONFLICT')])(
    'should retain the exact uncertain create attempt across closing and reopening: %s',
    async (error) => {
      api.create.mockRejectedValueOnce(error)
      renderSources()
      const user = await fillDatabase()
      await user.click(
        screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
      )
      await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.sources.createUncertain',
      )
      await user.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
      await user.click(screen.getByRole('button', { name: 'common.enterprise.sources.create' }))
      expect(screen.getByLabelText('common.enterprise.sources.name')).toBeDisabled()
      await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
      expect(api.create.mock.calls[1]?.[0]).toEqual(api.create.mock.calls[0]?.[0])
    },
  )

  it('should isolate an open source form when the workspace changes', async () => {
    const { store } = renderSources()
    await fillDatabase()
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-2',
      display_name: 'User',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    api.list.mockResolvedValue({ items: [], offset: 0, limit: 20, total: 0 })
    act(() => {
      store.set(workspaceAtom, { id: 'workspace-2', name: 'Other' })
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('common.enterprise.sources.empty')).toBeInTheDocument()
    expect(api.create).not.toHaveBeenCalled()
  })

  it('should retain an uncertain create attempt through a capabilities refresh failure', async () => {
    api.create.mockRejectedValueOnce(new Error('response lost'))
    const { client } = renderSources()
    const user = await fillDatabase()
    await user.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
    api.capabilities.mockRejectedValueOnce(new Error('temporarily unavailable'))
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ['enterprise-business', 'workspace-1', 'user-1', 'source-capabilities'],
      })
    })
    const failure = await screen.findByRole('alert')
    await user.click(within(failure).getByRole('button', { name: 'common.operation.retry' }))
    await user.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.create' }),
    )
    expect(screen.getByLabelText('common.enterprise.sources.name')).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
    expect(api.create.mock.calls[1]?.[0]).toEqual(api.create.mock.calls[0]?.[0])
  })

  it('should allow correction after a definitive create validation rejection', async () => {
    api.create.mockRejectedValueOnce(new ORPCError('VALIDATION_ERROR', { status: 422 }))
    renderSources()
    const user = await fillDatabase()
    await user.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    const field = screen.getByLabelText('common.enterprise.sources.name')
    expect(field).toBeEnabled()
    await user.clear(field)
    await user.type(field, 'Corrected source')
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledTimes(2))
    expect(api.create.mock.calls[1]?.[0].body.name).toBe('Corrected source')
    expect(api.create.mock.calls[1]?.[0].headers['idempotency-key']).not.toBe(
      api.create.mock.calls[0]?.[0].headers['idempotency-key'],
    )
  })

  it('should require reopening an edit conflict to load a new CAS revision', async () => {
    api.update.mockRejectedValueOnce(new ORPCError('CONFLICT'))
    renderSources()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.details' }),
    )
    await screen.findByLabelText('common.enterprise.sources.host')
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.editConflict',
    )
    expect(screen.getByRole('button', { name: 'common.operation.confirm' })).toBeDisabled()
    api.get.mockResolvedValue({ ...source(), revision: 9 })
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.sources.details' }))
    await screen.findByLabelText('common.enterprise.sources.host')
    expect(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    ).not.toBeChecked()
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(2))
    expect(api.update.mock.calls[1]?.[0].headers['if-match']).toBe('"9"')
  })

  it('should create HTTP headers and additional parameters through labeled controls', async () => {
    const user = userEvent.setup()
    renderSources()
    await user.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.create' }),
    )
    await user.click(screen.getByRole('combobox', { name: 'common.enterprise.sources.type' }))
    await user.click(screen.getByRole('option', { name: 'common.enterprise.sources.http' }))
    await user.type(screen.getByLabelText('common.enterprise.sources.name'), 'HTTP readings')
    await user.type(
      screen.getByLabelText('common.enterprise.sources.url'),
      'https://api.internal/readings',
    )
    await user.click(screen.getByRole('combobox', { name: 'common.enterprise.sources.headerMode' }))
    await user.click(
      screen.getByRole('option', { name: 'common.enterprise.sources.replaceHeaders' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sources.addHeader' }))
    await user.type(screen.getByLabelText('common.enterprise.sources.headerName'), 'Authorization')
    await user.type(
      screen.getByLabelText('common.enterprise.sources.headerValue'),
      'test-only-header',
    )
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sources.addParameter' }))
    await user.type(screen.getByLabelText('common.enterprise.sources.inputKey'), 'batch')
    await user.type(screen.getByLabelText('common.enterprise.sources.parameterName'), 'batch_id')
    await user.click(await screen.findByRole('checkbox', { name: '0001 · Pump' }))
    await user.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledOnce())
    expect(api.create.mock.calls[0]?.[0].body).toMatchObject({
      connection: {
        kind: 'http',
        method: 'GET',
        headers: [{ name: 'Authorization', value: 'test-only-header' }],
      },
      parameters: [
        { input_key: 'batch', parameter_name: 'batch_id', kind: 'string', nullable: false },
      ],
    })
  })

  it('should preserve an uncertain edit through a background list refresh failure', async () => {
    api.update.mockRejectedValueOnce(new Error('response lost'))
    const { client } = renderSources()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.details' }),
    )
    await screen.findByLabelText('common.enterprise.sources.host')
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
    api.list.mockRejectedValueOnce(new Error('temporarily unavailable'))
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ['enterprise-business', 'workspace-1', 'user-1', 'sources'],
      })
    })
    const failure = await screen.findByRole('alert')
    await userEvent.click(within(failure).getByRole('button', { name: 'common.operation.retry' }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.details' }),
    )
    expect(await screen.findByLabelText('common.enterprise.sources.name')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(2))
    expect(api.update.mock.calls[1]?.[0]).toEqual(api.update.mock.calls[0]?.[0])
  })

  it('should retain the submitted attempt when a create response belongs to another workspace', async () => {
    api.create.mockResolvedValue({ ...source(), workspace_id: 'workspace-other' })
    renderSources()
    const user = await fillDatabase()
    await user.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.getByLabelText('common.enterprise.sources.name')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'common.operation.confirm' })).toBeDisabled()
    expect(api.create).toHaveBeenCalledOnce()
  })

  it('should retain the edit attempt when its response names another source', async () => {
    api.update.mockResolvedValue({ ...source(), source_id: 'other-source' })
    renderSources()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.sources.details' }),
    )
    await screen.findByLabelText('common.enterprise.sources.host')
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'common.enterprise.sources.readOnlyConfirmation' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.getByRole('button', { name: 'common.operation.confirm' })).toBeDisabled()
  })

  it('should show a retryable source error instead of an empty list', async () => {
    api.list.mockRejectedValueOnce(new ORPCError('INTERNAL_SERVER_ERROR'))
    renderSources()
    const failure = await screen.findByRole('alert')
    expect(screen.queryByText('common.enterprise.sources.empty')).not.toBeInTheDocument()
    await userEvent.click(within(failure).getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByRole('heading', { name: 'Plant readings' })).toBeInTheDocument()
  })

  it('should read source pagination from the URL and request the next real page', async () => {
    api.list.mockResolvedValue({ items: [source()], total: 60, offset: 20, limit: 20 })
    const { onUrlUpdate } = renderSources('?page=2')
    await screen.findByRole('heading', { name: 'Plant readings' })
    expect(api.list).toHaveBeenCalledWith({ query: { offset: 20, limit: 20 } }, expect.anything())
    await userEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await waitFor(() =>
      expect(api.list).toHaveBeenCalledWith(
        { query: { offset: 40, limit: 20 } },
        expect.anything(),
      ),
    )
    expect(onUrlUpdate).toHaveBeenCalled()
  })
})
