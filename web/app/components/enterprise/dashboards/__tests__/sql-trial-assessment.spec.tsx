import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zSqlTrialEvidence } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlTrialAssessment } from '../sql-trial-assessment'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  get: vi.fn<
    Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trials']['byTrialId']['assessment']['get']
  >(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      dashboards: {
        byDashboardId: {
          sqlProposals: {
            byDraftId: { trials: { byTrialId: { assessment: { get: api.get } } } },
          },
        },
      },
    }),
  },
}))
const evidence = zSqlTrialEvidence.parse({
  trial_id: 'trial',
  actor_id: 'actor',
  recorded_at: '2026-09-11T00:00:01Z',
  result: {
    workspace_id: 'w',
    dashboard_id: 'screen',
    draft_id: 'draft',
    slot_id: 'count',
    dashboard_revision: 1,
    design_identity: 'a'.repeat(64),
    source: { workspace_id: 'w', source_id: 'source', revision: 'v1' },
    draft_hash: 'b'.repeat(64),
    read_fingerprint: 'c'.repeat(64),
    captured_at: '2026-09-11T00:00:00Z',
    columns: ['n'],
    rows: [[{ kind: 'integer', value: '1' }]],
    row_count: 1,
  },
})
const compatible = {
  trial_id: 'trial',
  slot_id: 'count',
  status: 'sample_compatible' as const,
  issues: [],
  semantic_status: 'not_reviewed' as const,
  unit_status: 'not_reviewed' as const,
  issues_truncated: false,
}
function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SqlTrialAssessment evidence={evidence} scope={['w', 'actor']} />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  api.get.mockResolvedValue(compatible)
})
async function open() {
  await userEvent
    .setup()
    .click(screen.getByRole('button', { name: 'common.enterprise.sqlAssessment.open' }))
}
describe('Saved trial assessment', () => {
  it('should assess on demand and keep semantic review visibly outstanding', async () => {
    mount()
    expect(api.get).not.toHaveBeenCalled()
    await open()
    expect(
      await screen.findByText('common.enterprise.sqlAssessment.sample_compatible'),
    ).toBeVisible()
    expect(screen.getByText('common.enterprise.sqlAssessment.notice')).toBeVisible()
    expect(api.get.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft', trial_id: 'trial' },
    })
  })
  it('should show localized field issues, one-based rows and truncation', async () => {
    api.get.mockResolvedValue({
      ...compatible,
      status: 'incompatible',
      issues_truncated: true,
      issues: [{ code: 'column_type_mismatch', field: '<img src=x>', row_index: 0 }],
    })
    mount()
    await open()
    expect(
      await screen.findByText('common.enterprise.sqlAssessment.column_type_mismatch'),
    ).toBeVisible()
    expect(screen.getByText('<img src=x>')).toBeVisible()
    expect(screen.getByText('#1')).toBeVisible()
    expect(screen.getByText('common.enterprise.sqlAssessment.truncated')).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
  it('should reject foreign assessment identifiers', async () => {
    api.get.mockResolvedValue({ ...compatible, trial_id: 'other' })
    mount()
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(
      screen.queryByText('common.enterprise.sqlAssessment.sample_compatible'),
    ).not.toBeInTheDocument()
  })
  it('should reject success with issues instead of displaying a compatible status', async () => {
    api.get.mockResolvedValue({
      ...compatible,
      issues: [{ code: 'null_not_allowed', field: 'value', row_index: 0 }],
    })
    mount()
    await open()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
  })
  it('should reauthorize on reopening and hide previously successful assessment on error', async () => {
    mount()
    await open()
    await screen.findByText('common.enterprise.sqlAssessment.sample_compatible')
    await open()
    api.get.mockRejectedValue(new Error('private server details'))
    await open()
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(
      screen.queryByText('common.enterprise.sqlAssessment.sample_compatible'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('private server details')).not.toBeInTheDocument()
  })
})
