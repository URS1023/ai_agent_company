import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zSqlDraftInspection, zSqlTrialResult } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlTrial } from '../sql-trial'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  trial:
    vi.fn<Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trial']['post']>(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      dashboards: {
        byDashboardId: { sqlProposals: { byDraftId: { trial: { post: api.trial } } } },
      },
    }),
  },
}))
const inspection = zSqlDraftInspection.parse({
  current_dashboard_revision: 3,
  requires_regeneration: false,
  draft: {
    actor_id: 'actor',
    workspace_id: 'w',
    dashboard_id: 'screen',
    draft_id: 'draft',
    dashboard_revision: 3,
    design_identity: 'a'.repeat(64),
    schema_revision: 'schema1',
    source: { workspace_id: 'w', source_id: 'source', revision: 's1' },
    prompt: 'Count inspections',
    created_at: '2026-09-11T00:00:00Z',
    proposals: {
      slots: [
        {
          slot_id: 'count',
          sql: 'SELECT count FROM inspections',
          field_map: { value: 'count' },
          metric_definition: 'Inspection count',
          time_definition: 'Current shift',
        },
      ],
    },
  },
})
const result = zSqlTrialResult.parse({
  workspace_id: 'w',
  dashboard_id: 'screen',
  draft_id: 'draft',
  slot_id: 'count',
  dashboard_revision: 3,
  design_identity: 'a'.repeat(64),
  source: inspection.draft.source,
  draft_hash: 'b'.repeat(64),
  read_fingerprint: 'c'.repeat(64),
  captured_at: '2026-09-11T00:00:00Z',
  columns: ['count'],
  rows: Array.from({ length: 21 }, (_, i) => [{ kind: 'integer', value: `${i}` }]),
  row_count: 21,
})
function mount(value = inspection) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SqlTrial inspection={value} slotId="count" />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  api.trial.mockResolvedValue(result)
})
describe('Saved SQL slot trial', () => {
  it('should notify the history owner only after a valid trial succeeds', async () => {
    const recorded = vi.fn()
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SqlTrial inspection={inspection} slotId="count" onRecorded={recorded} />
      </QueryClientProvider>,
    )
    expect(recorded).not.toHaveBeenCalled()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' }))
    await screen.findByRole('table')
    expect(recorded).toHaveBeenCalledTimes(1)
    api.trial.mockResolvedValue({ ...result, workspace_id: 'other' })
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' }))
    await screen.findByRole('alert')
    expect(recorded).toHaveBeenCalledTimes(1)
  })
  it('should display exact large values and render source markup as inert text', async () => {
    api.trial.mockResolvedValue({
      ...result,
      row_count: 2,
      rows: [
        [{ kind: 'integer', value: '18446744073709551616' }],
        [{ kind: 'string', value: '<img src=x onerror=alert(1)>' }],
      ],
    })
    mount()
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' }))
    expect(await screen.findByRole('cell', { name: '18446744073709551616' })).toBeVisible()
    expect(screen.getByRole('cell', { name: '<img src=x onerror=alert(1)>' })).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
  it('should execute only on demand with saved identity and page locally', async () => {
    const user = userEvent.setup()
    mount()
    expect(api.trial).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' }))
    expect(await screen.findByRole('table')).toHaveAccessibleName(
      'common.enterprise.sqlTrial.notice',
    )
    expect(api.trial.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft' },
      body: { expected_revision: 3, expected_design_identity: 'a'.repeat(64), slot_id: 'count' },
      headers: { Origin: window.location.origin },
    })
    expect(screen.getAllByRole('row')).toHaveLength(21)
    await user.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    expect(screen.getAllByRole('row')).toHaveLength(2)
    expect(screen.getByRole('cell', { name: '20' })).toBeVisible()
    expect(api.trial).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'common.pagination.previous' }))
    expect(screen.getByRole('cell', { name: '0' })).toBeVisible()
  })
  it('should disable execution for stale drafts', () => {
    mount({ ...inspection, requires_regeneration: true })
    expect(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' })).toBeDisabled()
    expect(api.trial).not.toHaveBeenCalled()
  })
  it('should discard foreign results rather than display a table', async () => {
    api.trial.mockResolvedValue({ ...result, workspace_id: 'other' })
    mount()
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
  it('should hide previous results during rerun and after an opaque failure without retrying', async () => {
    const user = userEvent.setup()
    mount()
    const run = screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' })
    await user.click(run)
    await screen.findByRole('table')
    let reject: (error: Error) => void = () => {
      throw new Error('not initialized')
    }
    api.trial.mockReturnValue(
      new Promise((_resolve, rejectPromise) => {
        reject = rejectPromise
      }),
    )
    await user.dblClick(run)
    expect(api.trial).toHaveBeenCalledTimes(2)
    expect(run).toBeDisabled()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    reject(new Error('private connection details'))
    expect(await screen.findByRole('alert')).toHaveTextContent('common.enterprise.sqlTrial.error')
    expect(screen.queryByText('private connection details')).not.toBeInTheDocument()
    await waitFor(() => expect(run).toBeEnabled())
    expect(api.trial).toHaveBeenCalledTimes(2)
  })
  it('should show an empty result without creating a fake row', async () => {
    api.trial.mockResolvedValue({ ...result, rows: [], row_count: 0 })
    mount()
    await userEvent
      .setup()
      .click(screen.getByRole('button', { name: 'common.enterprise.sqlTrial.run' }))
    expect(await screen.findByText('common.noData')).toBeVisible()
    expect(screen.getAllByRole('row')).toHaveLength(1)
  })
})
