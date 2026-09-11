import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { DashboardsPage } from '../index'
import { loadDashboardRenderer } from '../renderer-loader'

vi.mock('../renderer-loader', () => ({ loadDashboardRenderer: vi.fn() }))

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  access: vi.fn<Client['me']['get']>(),
  list: vi.fn<Client['dashboards']['get']>(),
  detail: vi.fn<Client['dashboards']['byDashboardId']['get']>(),
  refresh: vi.fn<Client['dashboards']['byDashboardId']['refresh']['post']>(),
  sources: vi.fn<Client['sources']['get']>(),
  generate: vi.fn<Client['dashboards']['byDashboardId']['sqlProposals']['post']>(),
  bindings: vi.fn<Client['dashboards']['byDashboardId']['bindings']['get']>(),
  saveBindings: vi.fn<Client['dashboards']['byDashboardId']['bindings']['put']>(),
  queries: vi.fn<Client['dashboardQueries']['get']>(),
  templates: vi.fn<Client['dashboardTemplates']['get']>(),
  create: vi.fn<Client['dashboards']['post']>(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      me: { get: api.access },
      sources: { get: api.sources },
      dashboards: {
        get: api.list,
        post: api.create,
        byDashboardId: {
          get: api.detail,
          refresh: { post: api.refresh },
          sqlProposals: { post: api.generate },
          bindings: { get: api.bindings, put: api.saveBindings },
        },
      },
      dashboardQueries: { get: api.queries },
      dashboardTemplates: { get: api.templates },
    }),
  },
}))
vi.mock('@/context/workspace-state', async () => ({
  currentWorkspaceAtom: (await import('jotai')).atom({ id: 'w', name: 'Plant' }),
}))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('actor'),
}))

function renderPage(searchParams = '') {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <NuqsTestingAdapter hasMemory searchParams={searchParams}>
        <DashboardsPage />
      </NuqsTestingAdapter>
    </QueryClientProvider>,
  )
}

describe('Dashboard management', () => {
  it('reloads an open dashboard after changing its bindings', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const current = {
      id: 'screen',
      name: 'Factory',
      revision: 3,
      template_id: 'equipment',
      design_identity: 'a'.repeat(64),
      renderer_build_id: 'b'.repeat(64),
      status: 'empty' as const,
      current: null,
      last_attempt_id: null,
      failures: [],
    }
    const next = { ...current, revision: 4 }
    api.detail.mockResolvedValueOnce(current).mockResolvedValue(next)
    const bindingView = {
      dashboard_id: 'screen',
      revision: 3,
      design_identity: 'a'.repeat(64),
      slots: [],
      bindings: [
        {
          slot_id: 'old',
          query_ref: 'old',
          query_revision: 'v1',
          field_map: { value: 'reading' },
          parameters: {},
        },
      ],
    }
    // An unknown removed slot is retained by the editor; use a real declared slot for explicit removal.
    const slots = [
      {
        slot_id: 'old',
        columns: [
          { name: 'value', kind: 'decimal' as const, required: true, nullable: false, unit: null },
        ],
        required: false,
        row_limit: 1000,
      },
    ]
    api.bindings.mockResolvedValue({ ...bindingView, slots })
    api.queries.mockResolvedValue({ items: [] })
    api.saveBindings.mockResolvedValue({ ...bindingView, slots, revision: 4, bindings: [] })
    const runtime = { update: vi.fn(), dispose: vi.fn() }
    vi.mocked(loadDashboardRenderer).mockResolvedValue(runtime)
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.view Factory' }))
    await waitFor(() => expect(loadDashboardRenderer).toHaveBeenCalledOnce())
    await user.click(screen.getByRole('button', { name: 'common.operation.edit Factory' }))
    await user.click(await screen.findByRole('button', { name: 'common.operation.remove old' }))
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await waitFor(() => expect(api.detail).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(runtime.update).toHaveBeenCalledWith(next))
    expect(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.open' }),
    ).toBeInTheDocument()
  })

  it('reconciles an uncertain refresh by reading state instead of automatically rerunning queries', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const current = {
      id: 'screen',
      name: 'Factory',
      revision: 3,
      template_id: 'equipment',
      design_identity: 'a'.repeat(64),
      renderer_build_id: 'b'.repeat(64),
      status: 'empty' as const,
      current: null,
      last_attempt_id: null,
      failures: [],
    }
    api.detail.mockResolvedValueOnce(current).mockResolvedValue({ ...current, revision: 4 })
    api.refresh.mockRejectedValueOnce(new Error('response lost'))
    vi.mocked(loadDashboardRenderer).mockResolvedValue({ update: vi.fn(), dispose: vi.fn() })
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.view Factory' }))
    const refresh = await screen.findByRole('button', {
      name: 'common.enterprise.provisioning.refresh Factory',
    })
    await user.click(refresh)
    await screen.findByRole('alert')
    expect(refresh).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    await waitFor(() => expect(refresh).toBeEnabled())
    expect(api.detail).toHaveBeenCalledTimes(2)
    expect(api.refresh).toHaveBeenCalledOnce()
  })

  it('executes a dashboard refresh with its current revision and updates the mounted canvas', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const current = {
      id: 'screen',
      name: 'Factory',
      revision: 3,
      template_id: 'equipment',
      design_identity: 'a'.repeat(64),
      renderer_build_id: 'b'.repeat(64),
      status: 'empty' as const,
      current: null,
      last_attempt_id: null,
      failures: [],
    }
    const updated = { ...current, revision: 4 }
    api.detail.mockResolvedValue(current)
    api.refresh.mockResolvedValue(updated)
    const runtime = { update: vi.fn(), dispose: vi.fn() }
    vi.mocked(loadDashboardRenderer).mockResolvedValue(runtime)
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.view Factory' }))
    await waitFor(() => expect(loadDashboardRenderer).toHaveBeenCalledOnce())
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.provisioning.refresh Factory' }),
    )
    await waitFor(() => expect(runtime.update).toHaveBeenCalledWith(updated))
    expect(api.refresh.mock.calls[0]?.[0]).toMatchObject({
      params: { dashboard_id: 'screen' },
      body: { expected_revision: 3 },
    })
  })

  it('lets a read-only member view and close a dashboard without querying bindings', async () => {
    const user = userEvent.setup()
    api.access.mockResolvedValue({
      actor_id: 'actor',
      workspace_id: 'w',
      display_name: 'Reader',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const data = {
      id: 'screen',
      name: 'Factory',
      revision: 3,
      template_id: 'equipment',
      design_identity: 'a'.repeat(64),
      renderer_build_id: 'b'.repeat(64),
      status: 'empty' as const,
      current: null,
      last_attempt_id: null,
      failures: [],
    }
    api.detail.mockResolvedValue(data)
    const runtime = { update: vi.fn(), dispose: vi.fn() }
    vi.mocked(loadDashboardRenderer).mockResolvedValue(runtime)
    renderPage('?sqlDraft=draft-1')
    await user.click(await screen.findByRole('button', { name: 'common.operation.view Factory' }))
    await waitFor(() => expect(loadDashboardRenderer).toHaveBeenCalledOnce())
    expect(vi.mocked(loadDashboardRenderer).mock.calls[0]?.[1]).toEqual(data)
    expect(api.bindings).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('region', { name: 'common.enterprise.sqlGeneration.saved' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.sqlGeneration.open' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.operation.edit Factory' }),
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'common.operation.close' }))
    await waitFor(() => expect(runtime.dispose).toHaveBeenCalledOnce())
  })

  it('loads the selected dashboard detail and rejects a different returned identity', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    api.detail.mockResolvedValue({
      id: 'other',
      name: 'Other',
      revision: 1,
      template_id: 'equipment',
      design_identity: 'a'.repeat(64),
      renderer_build_id: 'b'.repeat(64),
      status: 'empty',
      current: null,
      last_attempt_id: null,
      failures: [],
    })
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.view Factory' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(api.detail.mock.calls[0]?.[0]).toEqual({ params: { dashboard_id: 'screen' } })
  })

  it.each([false, true])(
    'reloads binding conflict before unlocking save (catalog failure: %s)',
    async (catalogFails) => {
      const user = userEvent.setup()
      api.list.mockResolvedValue({
        items: [
          { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
        ],
        next_cursor: null,
      })
      const original = {
        dashboard_id: 'screen',
        revision: 3,
        design_identity: 'a'.repeat(64),
        slots: [],
        bindings: [],
      }
      const current = { ...original, revision: 4 }
      api.bindings.mockResolvedValueOnce(original).mockResolvedValue(current)
      api.queries.mockResolvedValueOnce({ items: [] })
      if (catalogFails) api.queries.mockRejectedValue(new Error('catalog unavailable'))
      else api.queries.mockResolvedValue({ items: [] })
      api.saveBindings
        .mockRejectedValueOnce(new ORPCError('CONFLICT', { status: 409 }))
        .mockResolvedValue(current)
      renderPage()
      await user.click(await screen.findByRole('button', { name: 'common.operation.edit Factory' }))
      await user.click(await screen.findByRole('button', { name: 'common.operation.save' }))
      const change = await screen.findByRole('button', { name: 'common.operation.change' })
      expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeDisabled()
      await user.click(change)
      await waitFor(() => expect(api.queries).toHaveBeenCalledTimes(2))
      if (catalogFails) {
        expect(await screen.findByRole('alert')).toBeInTheDocument()
        expect(
          screen.queryByRole('button', { name: 'common.operation.save' }),
        ).not.toBeInTheDocument()
        expect(api.saveBindings).toHaveBeenCalledOnce()
        api.queries.mockResolvedValue({ items: [] })
        await user.click(screen.getByRole('button', { name: 'common.operation.retry' }))
      }
      const save = await screen.findByRole('button', { name: 'common.operation.save' })
      await waitFor(() => expect(save).toBeEnabled())
      await user.click(save)
      await waitFor(() => expect(api.saveBindings).toHaveBeenCalledTimes(2))
      expect(api.saveBindings.mock.calls[1]?.[0].body.expected_revision).toBe(4)
    },
  )

  it('blocks editing when a save receipt contains unsubmitted bindings', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const bindingView = {
      dashboard_id: 'screen',
      revision: 3,
      design_identity: 'a'.repeat(64),
      slots: [],
      bindings: [],
    }
    api.bindings.mockResolvedValue(bindingView)
    api.queries.mockResolvedValue({ items: [] })
    api.saveBindings.mockResolvedValue({
      ...bindingView,
      bindings: [
        {
          slot_id: 'unexpected',
          query_ref: 'other',
          query_revision: 'v1',
          field_map: { value: 'reading' },
        },
      ],
    })
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.edit Factory' }))
    await user.click(await screen.findByRole('button', { name: 'common.operation.save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.queryByRole('button', { name: 'common.operation.save' })).not.toBeInTheDocument()
    expect(api.list).toHaveBeenCalledOnce()
  })

  it('retries an uncertain binding save with the identical command', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const bindingView = {
      dashboard_id: 'screen',
      revision: 3,
      design_identity: 'a'.repeat(64),
      slots: [],
      bindings: [],
    }
    api.bindings.mockResolvedValue(bindingView)
    api.queries.mockResolvedValue({ items: [] })
    api.saveBindings
      .mockRejectedValueOnce(new Error('network interrupted'))
      .mockResolvedValueOnce(bindingView)
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.edit Factory' }))
    await user.click(await screen.findByRole('button', { name: 'common.operation.save' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    await waitFor(() => expect(api.saveBindings).toHaveBeenCalledTimes(2))
    expect(api.saveBindings.mock.calls[1]?.[0]).toEqual(api.saveBindings.mock.calls[0]?.[0])
  })

  it('opens bindings from a dashboard and saves using its loaded version', async () => {
    const user = userEvent.setup()
    api.list.mockResolvedValue({
      items: [
        { id: 'screen', name: 'Factory', revision: 3, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const bindingView = {
      dashboard_id: 'screen',
      revision: 3,
      design_identity: 'a'.repeat(64),
      slots: [],
      bindings: [],
    }
    api.bindings.mockResolvedValue(bindingView)
    api.queries.mockResolvedValue({ items: [] })
    api.saveBindings.mockResolvedValue(bindingView)
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.edit Factory' }))
    await user.click(await screen.findByRole('button', { name: 'common.operation.save' }))
    await waitFor(() => expect(api.saveBindings).toHaveBeenCalledOnce())
    expect(api.saveBindings.mock.calls[0]?.[0]).toMatchObject({
      params: { dashboard_id: 'screen' },
      body: { expected_revision: 3, expected_design_identity: 'a'.repeat(64), bindings: [] },
    })
  })

  beforeEach(() => {
    vi.clearAllMocks()
    api.access.mockResolvedValue({
      actor_id: 'actor',
      workspace_id: 'w',
      display_name: 'User',
      permissions: { read: true, manage: true, run: true, review: false },
    })
    api.list.mockResolvedValue({ items: [], next_cursor: null })
    api.templates.mockResolvedValue({
      items: [
        {
          template_id: 'equipment',
          template_revision: 1,
          design_identity: 'a'.repeat(64),
          renderer_build_id: 'b'.repeat(64),
          slots: [],
        },
      ],
    })
    api.create.mockResolvedValue({
      id: 'created-dashboard',
      name: 'created-dashboard',
      revision: 1,
      template_id: 'equipment',
      design_identity: 'a'.repeat(64),
      renderer_build_id: 'b'.repeat(64),
      status: 'empty',
      current: null,
      last_attempt_id: null,
      failures: [],
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('creates from a pinned template and refreshes the list', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(
      await screen.findByRole('button', { name: 'common.operation.create equipment' }),
    )
    await screen.findByText('created-dashboard')
    expect(api.create.mock.calls[0]?.[0].body).toEqual({
      template_id: 'equipment',
      expected_design_identity: 'a'.repeat(64),
      name: 'equipment',
    })
    expect(api.create.mock.calls[0]?.[0].headers['idempotency-key']).toBeTruthy()
    await waitFor(() => expect(api.list.mock.calls.length).toBeGreaterThan(1))
  })

  it('submits the user-entered dashboard name instead of changing the template title', async () => {
    const user = userEvent.setup()
    renderPage()
    const name = await screen.findByLabelText('common.account.name')
    await user.clear(name)
    await user.type(name, '一车间设备预警')
    await user.click(screen.getByRole('button', { name: 'common.operation.create equipment' }))
    await screen.findByText('created-dashboard')
    expect(api.create.mock.calls[0]?.[0].body.name).toBe('一车间设备预警')
    expect(api.create.mock.calls[0]?.[0].body.template_id).toBe('equipment')
  })

  it('retries uncertain creation with the original key and payload', async () => {
    api.create.mockRejectedValueOnce(new Error('network interrupted'))
    const user = userEvent.setup()
    renderPage()
    await user.click(
      await screen.findByRole('button', { name: 'common.operation.create equipment' }),
    )
    await user.click(await screen.findByRole('button', { name: 'common.operation.retry' }))
    await screen.findByText('created-dashboard')
    expect(api.create.mock.calls[1]?.[0]).toEqual(api.create.mock.calls[0]?.[0])
  })

  it('creates when randomUUID is absent but cryptographic random values are available', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    const user = userEvent.setup()
    renderPage()
    await user.click(
      await screen.findByRole('button', { name: 'common.operation.create equipment' }),
    )
    await screen.findByText('created-dashboard')
    expect(api.create.mock.calls[0]?.[0].headers['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('reloads a changed catalog before allowing a new creation request', async () => {
    api.create.mockRejectedValueOnce(new ORPCError('CONFLICT', { status: 409 }))
    const user = userEvent.setup()
    renderPage()
    await user.click(
      await screen.findByRole('button', { name: 'common.operation.create equipment' }),
    )
    api.templates.mockResolvedValue({
      items: [
        {
          template_id: 'equipment',
          template_revision: 2,
          design_identity: 'c'.repeat(64),
          renderer_build_id: 'd'.repeat(64),
          slots: [],
        },
      ],
    })
    await user.click(await screen.findByRole('button', { name: 'common.operation.change' }))
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'common.operation.create equipment' }),
      ).toBeEnabled(),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.create equipment' }))
    await screen.findByText('created-dashboard')
    expect(api.create.mock.calls[1]?.[0].body.expected_design_identity).toBe('c'.repeat(64))
    expect(api.create.mock.calls[1]?.[0].headers['idempotency-key']).not.toBe(
      api.create.mock.calls[0]?.[0].headers['idempotency-key'],
    )
  })

  it('does not unlock template selection if conflict reconciliation fails', async () => {
    api.create.mockRejectedValueOnce(new ORPCError('CONFLICT', { status: 409 }))
    const user = userEvent.setup()
    renderPage()
    await user.click(
      await screen.findByRole('button', { name: 'common.operation.create equipment' }),
    )
    api.templates.mockRejectedValue(new Error('catalog unavailable'))
    await user.click(await screen.findByRole('button', { name: 'common.operation.change' }))
    await waitFor(() => expect(api.templates.mock.calls.length).toBeGreaterThan(1))
    expect(api.create).toHaveBeenCalledOnce()
    expect(
      screen.queryByRole('button', { name: 'common.operation.create equipment' }),
    ).not.toBeInTheDocument()
  })

  it('read-only members see dashboards but no creation controls', async () => {
    api.access.mockResolvedValue({
      actor_id: 'actor',
      workspace_id: 'w',
      display_name: 'User',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    api.list.mockResolvedValue({
      items: [
        {
          id: 'dashboard-1',
          name: 'dashboard-1',
          revision: 1,
          template_id: 'equipment',
          status: 'empty',
        },
      ],
      next_cursor: null,
    })
    renderPage()
    await screen.findByText('dashboard-1')
    expect(
      screen.queryByRole('button', { name: /common.operation.create/ }),
    ).not.toBeInTheDocument()
    expect(api.create).not.toHaveBeenCalled()
  })

  it('shows the imported name and local preview for a known template revision', async () => {
    api.templates.mockResolvedValue({
      items: [
        {
          template_id: 'equipment-digital-ops',
          template_revision: 1,
          design_identity: 'a'.repeat(64),
          renderer_build_id: 'b'.repeat(64),
          slots: [],
        },
      ],
    })
    renderPage()
    const preview = await screen.findByRole('img', { name: '设备数字运维大屏' })
    expect(preview.getAttribute('src')).toMatch(
      /\/enterprise\/dashboard-templates\/template-19-[a-f0-9]{64}\.jpg$/,
    )
    expect(
      screen.getByRole('button', { name: 'common.operation.create 设备数字运维大屏' }),
    ).toBeEnabled()
  })

  it('does not present a stale preview for an unknown template revision', async () => {
    api.templates.mockResolvedValue({
      items: [
        {
          template_id: 'equipment-digital-ops',
          template_revision: 2,
          design_identity: 'a'.repeat(64),
          renderer_build_id: 'b'.repeat(64),
          slots: [],
        },
      ],
    })
    renderPage()
    await screen.findByRole('button', { name: 'common.operation.create equipment-digital-ops' })
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('loads the next cursor without mixing previous results', async () => {
    api.list.mockResolvedValueOnce({
      items: [
        { id: 'first', name: 'first', revision: 1, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: 'first',
    })
    api.list.mockResolvedValue({
      items: [
        { id: 'second', name: 'second', revision: 1, template_id: 'equipment', status: 'empty' },
      ],
      next_cursor: null,
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'common.operation.more' }))
    await screen.findByText('second')
    expect(screen.queryByText('first')).not.toBeInTheDocument()
    expect(api.list.mock.calls.at(-1)?.[0].query).toEqual({ after: 'first', limit: 20 })
  })

  it('denied access never loads templates or dashboards', async () => {
    api.access.mockRejectedValue(new Error('unauthenticated'))
    renderPage()
    await screen.findByRole('alert')
    expect(api.list).not.toHaveBeenCalled()
    expect(api.templates).not.toHaveBeenCalled()
  })
})
