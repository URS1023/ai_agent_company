import type { contract } from '@enterprise/business-contracts/orpc'
import type { DashboardView } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import {
  zSourceView,
  zSqlDraftInspection,
  zSqlGenerationReceipt,
} from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { SqlGeneration } from '../sql-generation'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  sources: vi.fn<Client['sources']['get']>(),
  generate: vi.fn<Client['dashboards']['byDashboardId']['sqlProposals']['post']>(),
  inspect: vi.fn<Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['get']>(),
  trial:
    vi.fn<Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trial']['post']>(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      sources: { get: api.sources },
      dashboards: {
        byDashboardId: {
          sqlProposals: {
            post: api.generate,
            byDraftId: { get: api.inspect, trial: { post: api.trial } },
          },
        },
      },
    }),
  },
}))

const view: DashboardView = {
  id: 'screen',
  name: 'Factory',
  revision: 3,
  template_id: 'equipment',
  design_identity: 'a'.repeat(64),
  renderer_build_id: 'b'.repeat(64),
  status: 'empty',
  current: null,
  last_attempt_id: null,
  failures: [],
}
const source = zSourceView.parse({
  name: 'Plant readings',
  workspace_id: 'w',
  source_id: 'source-1',
  source_revision: 's1',
  read_id: 'r',
  read_revision: 'r1',
  revision: 1,
  device_ids: ['d'],
  device_parameter: 'device',
  device_column: 'device',
  scope_attribute: 'device_code',
  parameters: [],
  connection_status: 'not_tested',
  connection: {
    kind: 'db',
    dialect: 'postgresql',
    host: 'db.test',
    port: 5432,
    database: 'plant',
    tls: true,
    sql: 'SELECT 1',
    allowed_tables: ['measurements'],
    credentials_configured: true,
  },
  created_at: '2026-09-11T00:00:00Z',
  updated_at: '2026-09-11T00:00:00Z',
})
const receipt = zSqlGenerationReceipt.parse({
  draft_id: 'draft-1',
  dashboard_id: view.id,
  dashboard_revision: 3,
  design_identity: view.design_identity,
  source_id: source.source_id,
  source_revision: 's1',
  schema_revision: 'schema1',
  status: 'draft',
  proposals: {
    slots: [
      {
        slot_id: 'a',
        sql: 'SELECT count FROM measurements',
        field_map: { value: 'count' },
        metric_definition: 'Inspection count',
        time_definition: 'Current shift',
      },
    ],
  },
})
const inspection = zSqlDraftInspection.parse({
  draft: {
    ...receipt,
    workspace_id: 'w',
    actor_id: 'actor',
    prompt: 'Count inspections',
    source: { workspace_id: 'w', source_id: 'source-1', revision: 's1' },
    created_at: '2026-09-11T00:00:00Z',
  },
  current_dashboard_revision: 3,
  requires_regeneration: false,
})
function mount(
  searchParams = '',
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  }),
) {
  return render(
    <QueryClientProvider client={client}>
      <NuqsTestingAdapter hasMemory searchParams={searchParams}>
        <SqlGeneration view={view} scope={['enterprise-business', 'w', 'actor']} workspaceId="w" />
      </NuqsTestingAdapter>
    </QueryClientProvider>,
  )
}
async function fill() {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.open' }))
  await user.click(await screen.findByRole('button', { name: 'Plant readings' }))
  await user.type(
    screen.getByRole('textbox', { name: 'common.enterprise.sqlGeneration.prompt' }),
    'Count inspections',
  )
  return user
}
beforeEach(() => {
  vi.resetAllMocks()
  api.sources.mockResolvedValue({ items: [source], total: 1, limit: 20, offset: 0 })
  api.generate.mockResolvedValue(receipt)
  api.inspect.mockResolvedValue(inspection)
})
describe('SQL generation', () => {
  it('should expose an explicit trial action on a saved draft without executing on inspection', async () => {
    mount('?sqlDraft=draft-1')
    expect(
      await screen.findByRole('button', { name: 'common.enterprise.sqlTrial.run' }),
    ).toBeEnabled()
    expect(api.trial).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.history' })).toBeEnabled()
    expect(api.generate).not.toHaveBeenCalled()
  })
  it('should hide cached content during reauthorization and after access is revoked', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    })
    const first = mount('?sqlDraft=draft-1', client)
    expect(await screen.findByText('Count inspections')).toBeInTheDocument()
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }))
    expect(screen.getByText('SELECT count FROM measurements')).toBeVisible()
    first.unmount()
    let rejectRead: (reason: Error) => void = () => {
      throw new Error('Promise not initialized')
    }
    api.inspect.mockReturnValue(
      new Promise<typeof inspection>((_resolve, reject) => {
        rejectRead = reject
      }),
    )

    mount('?sqlDraft=draft-1', client)

    expect(screen.queryByText('Count inspections')).not.toBeInTheDocument()
    expect(screen.queryByText('SELECT count FROM measurements')).not.toBeInTheDocument()
    await waitFor(() => expect(api.inspect).toHaveBeenCalledTimes(2))
    rejectRead(new Error('access revoked'))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.loadError',
    )
    expect(screen.queryByText('Count inspections')).not.toBeInTheDocument()
    expect(api.generate).not.toHaveBeenCalled()
  })

  it('should hide a draft whose source belongs to a different workspace', async () => {
    api.inspect.mockResolvedValue({
      ...inspection,
      draft: {
        ...inspection.draft,
        source: { ...inspection.draft.source, workspace_id: 'foreign' },
      },
    })
    mount('?sqlDraft=draft-1')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.queryByText('Count inspections')).not.toBeInTheDocument()
  })

  it('should close a restored draft without submitting a mutation', async () => {
    mount('?sqlDraft=draft-1')
    expect(await screen.findByText('Count inspections')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'common.operation.close' }))
    await waitFor(() =>
      expect(
        screen.queryByRole('region', { name: 'common.enterprise.sqlGeneration.saved' }),
      ).not.toBeInTheDocument(),
    )
    expect(api.generate).not.toHaveBeenCalled()
  })

  it('should reopen a persisted draft from the URL without generating again', async () => {
    mount('?sqlDraft=draft-1')
    expect(await screen.findByText('Count inspections')).toBeInTheDocument()
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }))
    expect(screen.getByText('SELECT count FROM measurements')).toBeInTheDocument()
    expect(api.inspect.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft-1' },
    })
    expect(api.generate).not.toHaveBeenCalled()
    expect(api.sources).not.toHaveBeenCalled()
  })

  it('should inspect the saved result after generation without a second model request', async () => {
    mount()
    const user = await fill()
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.generate' }),
    )
    await user.click(
      await screen.findByRole('button', { name: 'common.enterprise.sqlGeneration.saved' }),
    )
    const saved = screen.getByRole('region', { name: 'common.enterprise.sqlGeneration.saved' })
    expect(await within(saved).findByText('Count inspections')).toBeInTheDocument()
    expect(api.inspect).toHaveBeenCalledTimes(1)
    expect(api.generate).toHaveBeenCalledTimes(1)
  })

  it('should warn when a saved draft requires regeneration', async () => {
    api.inspect.mockResolvedValue({ ...inspection, requires_regeneration: true })
    mount('?sqlDraft=draft-1')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sqlGeneration.stale',
    )
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }))
    expect(screen.getByText('SELECT count FROM measurements')).toBeInTheDocument()
  })

  it.each(['workspace_id', 'dashboard_id', 'draft_id'] as const)(
    'should hide a draft with a mismatched %s',
    async (field) => {
      api.inspect.mockResolvedValue({
        ...inspection,
        draft: { ...inspection.draft, [field]: 'foreign' },
      })
      mount('?sqlDraft=draft-1')
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.sources.responseMismatch',
      )
      expect(screen.queryByText('Count inspections')).not.toBeInTheDocument()
      expect(screen.queryByText('SELECT count FROM measurements')).not.toBeInTheDocument()
    },
  )

  it('should retry only the read after inspection fails', async () => {
    api.inspect.mockRejectedValueOnce(new Error('private service detail'))
    mount('?sqlDraft=draft-1')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByText('Count inspections')).toBeInTheDocument()
    expect(screen.queryByText('private service detail')).not.toBeInTheDocument()
    expect(api.inspect).toHaveBeenCalledTimes(2)
    expect(api.generate).not.toHaveBeenCalled()
  })

  it('should prevent duplicate submissions while generation is pending', async () => {
    let finish: (value: typeof receipt) => void = () => {
      throw new Error('Promise not initialized')
    }
    const pending = new Promise<typeof receipt>((resolve) => {
      finish = resolve
    })
    api.generate.mockReturnValue(pending)
    mount()
    const user = await fill()
    const button = screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.generate' })
    await user.dblClick(button)
    expect(api.generate).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
    finish(receipt)
    await userEvent
      .setup()
      .click(
        await screen.findByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }),
      )
    expect(await screen.findByText('SELECT count FROM measurements')).toBeInTheDocument()
  })

  it('should clear the previous draft when the prompt changes', async () => {
    mount()
    const user = await fill()
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.generate' }),
    )
    await userEvent
      .setup()
      .click(
        await screen.findByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }),
      )
    expect(await screen.findByText('SELECT count FROM measurements')).toBeInTheDocument()
    await user.type(
      screen.getByRole('textbox', { name: 'common.enterprise.sqlGeneration.prompt' }),
      ' today',
    )
    expect(screen.queryByText('SELECT count FROM measurements')).not.toBeInTheDocument()
    expect(api.generate).toHaveBeenCalledTimes(1)
  })

  it('should send only prompt context and display an unexecuted SQL draft', async () => {
    mount()
    expect(api.sources).not.toHaveBeenCalled()
    const user = await fill()
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.generate' }),
    )
    await userEvent
      .setup()
      .click(
        await screen.findByRole('button', { name: 'common.enterprise.sqlGeneration.advanced' }),
      )
    expect(await screen.findByText('SELECT count FROM measurements')).toBeInTheDocument()
    expect(screen.getByText('Inspection count')).toBeInTheDocument()
    expect(screen.getByText('common.enterprise.sqlGeneration.draftNotice')).toBeInTheDocument()
    expect(api.generate.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen' },
      body: {
        source_id: 'source-1',
        prompt: 'Count inspections',
        expected_revision: 3,
        expected_design_identity: view.design_identity,
      },
      headers: { Origin: window.location.origin },
    })
  })
  it('should not retry a failed model invocation automatically', async () => {
    api.generate.mockRejectedValue(new Error('private driver detail'))
    mount()
    const user = await fill()
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.generate' }),
    )
    expect(await screen.findByText('common.enterprise.sqlGeneration.error')).toBeInTheDocument()
    expect(screen.queryByText('private driver detail')).not.toBeInTheDocument()
    expect(api.generate).toHaveBeenCalledTimes(1)
  })
  it('should reject mismatched draft context', async () => {
    api.generate.mockResolvedValue({ ...receipt, source_revision: 'other' })
    mount()
    const user = await fill()
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.generate' }),
    )
    expect(
      await screen.findByText('common.enterprise.sources.responseMismatch'),
    ).toBeInTheDocument()
    expect(screen.queryByText('SELECT count FROM measurements')).not.toBeInTheDocument()
  })
  it('should reject cross-workspace source lists before generation', async () => {
    api.sources.mockResolvedValue({
      items: [{ ...source, workspace_id: 'other' }],
      total: 1,
      limit: 20,
      offset: 0,
    })
    mount()
    await userEvent.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlGeneration.open' }),
    )
    await waitFor(() =>
      expect(screen.getByText('common.enterprise.sources.responseMismatch')).toBeInTheDocument(),
    )
    expect(screen.queryByRole('button', { name: 'Plant readings' })).not.toBeInTheDocument()
    expect(api.generate).not.toHaveBeenCalled()
  })
})
