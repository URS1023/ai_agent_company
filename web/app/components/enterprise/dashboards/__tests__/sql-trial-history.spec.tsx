import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zSqlDraftInspection, zSqlTrialEvidence } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlTrialHistory } from '../sql-trial-history'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  preview:
    vi.fn<Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['preview']['post']>(),
  list: vi.fn<
    Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trials']['get']
  >(),
  get: vi.fn<
    Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trials']['byTrialId']['get']
  >(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      dashboards: {
        byDashboardId: {
          sqlProposals: {
            byDraftId: {
              preview: { post: api.preview },
              trials: { get: api.list, byTrialId: { get: api.get } },
            },
          },
        },
      },
    }),
  },
}))
const inspection = zSqlDraftInspection.parse({
  current_dashboard_revision: 1,
  requires_regeneration: false,
  draft: {
    workspace_id: 'w',
    dashboard_id: 'screen',
    draft_id: 'draft',
    actor_id: 'actor',
    dashboard_revision: 1,
    design_identity: 'a'.repeat(64),
    schema_revision: 'schema1',
    prompt: 'Count',
    source: { workspace_id: 'w', source_id: 'source', revision: 's1' },
    created_at: '2026-09-11T00:00:00Z',
    proposals: {
      slots: [
        {
          slot_id: 'count',
          sql: 'SELECT n FROM readings',
          field_map: { value: 'n' },
          metric_definition: 'Count',
          time_definition: 'Current shift',
        },
      ],
    },
  },
})
const evidence = zSqlTrialEvidence.parse({
  trial_id: 'trial-2',
  actor_id: 'actor',
  recorded_at: '2026-09-11T00:00:01Z',
  result: {
    workspace_id: 'w',
    dashboard_id: 'screen',
    draft_id: 'draft',
    slot_id: 'count',
    dashboard_revision: 1,
    design_identity: 'a'.repeat(64),
    source: inspection.draft.source,
    draft_hash: 'b'.repeat(64),
    read_fingerprint: 'c'.repeat(64),
    captured_at: '2026-09-11T00:00:00Z',
    columns: ['n'],
    rows: [[{ kind: 'integer', value: '18446744073709551616' }]],
    row_count: 1,
  },
})
const item = {
  workspace_id: 'w',
  dashboard_id: 'screen',
  draft_id: 'draft',
  trial_id: 'trial-2',
  actor_id: 'actor',
  recorded_at: evidence.recorded_at,
}
function mount() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SqlTrialHistory inspection={inspection} scope={['w', 'actor']} />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  api.list.mockResolvedValue({ items: [item], next_cursor: null })
  api.get.mockResolvedValue(evidence)
})
describe('SQL trial history', () => {
  it('should load metadata only after opening and fetch results only on selection', async () => {
    mount()
    expect(api.list).not.toHaveBeenCalled()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.history' }))
    const entry = await screen.findByRole('button', { name: 'trial-2' })
    expect(api.get).not.toHaveBeenCalled()
    await user.click(entry)
    expect(await screen.findByRole('cell', { name: '18446744073709551616' })).toBeVisible()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.sqlAssessment.open' }),
    ).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlPreview.groupAdd' }))
    expect(
      screen.getByRole('button', { name: 'common.enterprise.sqlPreview.groupRun' }),
    ).toBeEnabled()
    expect(api.preview).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlPreview.groupAdd' }))
    expect(
      screen.getAllByRole('button', { name: 'common.enterprise.sqlPreview.groupRemove' }),
    ).toHaveLength(1)
    await user.click(
      screen.getByRole('button', { name: 'common.enterprise.sqlPreview.groupRemove' }),
    )
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.sqlPreview.groupRun' }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.enterprise.sqlPreview.open' })).toBeEnabled()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.sqlSampleCheck.open' }),
    ).toBeEnabled()
    expect(api.get.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft', trial_id: 'trial-2' },
    })
  })
  it('should paginate metadata with the returned cursor', async () => {
    api.list
      .mockResolvedValueOnce({ items: [item], next_cursor: 'trial-2' })
      .mockResolvedValueOnce({ items: [{ ...item, trial_id: 'trial-1' }], next_cursor: null })
    mount()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.history' }))
    await screen.findByRole('button', { name: 'trial-2' })
    await user.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    expect(await screen.findByRole('button', { name: 'trial-1' })).toBeVisible()
    expect(api.list.mock.calls[1]?.[0].query).toEqual({ after: 'trial-2', limit: 20 })
  })
  it('should hide foreign metadata instead of offering result selection', async () => {
    api.list.mockResolvedValue({ items: [{ ...item, workspace_id: 'other' }], next_cursor: null })
    mount()
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.history' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.queryByRole('button', { name: 'trial-2' })).not.toBeInTheDocument()
  })
  it('should discard a mismatched detail response', async () => {
    api.get.mockResolvedValue({ ...evidence, trial_id: 'other' })
    mount()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.history' }))
    await user.click(await screen.findByRole('button', { name: 'trial-2' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
  it('should reauthorize reopened results and hide old content after denial', async () => {
    mount()
    const user = userEvent.setup()
    const toggle = screen.getByRole('button', { name: 'common.enterprise.sqlTrial.history' })
    await user.click(toggle)
    await user.click(await screen.findByRole('button', { name: 'trial-2' }))
    await screen.findByRole('table')
    await user.click(toggle)
    api.get.mockRejectedValue(new Error('private detail'))
    await user.click(toggle)
    await user.click(await screen.findByRole('button', { name: 'trial-2' }))
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText('private detail')).not.toBeInTheDocument()
  })
})
