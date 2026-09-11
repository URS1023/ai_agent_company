import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zSqlTrialEvidence } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlSampleCheck } from '../sql-sample-check'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  get: vi.fn<
    Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trials']['byTrialId']['sampleCheck']['automatic']['get']
  >(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      dashboards: {
        byDashboardId: {
          sqlProposals: {
            byDraftId: { trials: { byTrialId: { sampleCheck: { automatic: { get: api.get } } } } },
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

const checked = {
  policy_id: 'device-count',
  policy_revision: 'v1',
  policy_hash: 'd'.repeat(64),
  result: {
    trial_id: 'trial',
    slot_id: 'count',
    status: 'sample_constraints_passed' as const,
    semantic_status: 'not_reviewed' as const,
    issues: [],
    issues_truncated: false,
  },
}
function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SqlSampleCheck evidence={evidence} scope={['w', 'actor']} />
    </QueryClientProvider>,
  )
}
async function open() {
  await userEvent
    .setup()
    .click(screen.getByRole('button', { name: 'common.enterprise.sqlSampleCheck.open' }))
}
beforeEach(() => {
  vi.resetAllMocks()
  api.get.mockResolvedValue(checked)
})
describe('Automatic sample checks', () => {
  it('should select the policy on the server without sending policy IDs', async () => {
    mount()
    expect(api.get).not.toHaveBeenCalled()
    await open()
    expect(
      await screen.findByText('common.enterprise.sqlSampleCheck.sample_constraints_passed'),
    ).toBeVisible()
    expect(screen.getByText('device-count')).toBeVisible()
    expect(screen.getByText('v1')).toBeVisible()
    expect(screen.getByText('common.enterprise.sqlSampleCheck.notice')).toBeVisible()
    expect(api.get.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft', trial_id: 'trial' },
    })
  })
  it('should show exact issue fields and one-based row positions as text', async () => {
    api.get.mockResolvedValue({
      ...checked,
      result: {
        ...checked.result,
        status: 'sample_constraints_failed',
        issues: [{ code: 'numeric_out_of_range', field: '<img src=x>', row_index: 0 }],
        issues_truncated: true,
      },
    })
    mount()
    await open()
    expect(
      await screen.findByText('common.enterprise.sqlSampleCheck.numeric_out_of_range'),
    ).toBeVisible()
    expect(screen.getByText('<img src=x>')).toBeVisible()
    expect(screen.getByText('#1')).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
  it.each(['trial', 'slot', 'issues', 'row', 'hash'])(
    'should reject mismatched %s responses',
    async (scenario) => {
      const value = { ...checked, result: { ...checked.result } }
      if (scenario === 'trial') value.result.trial_id = 'other'
      if (scenario === 'slot') value.result.slot_id = 'other'
      if (scenario === 'hash') value.policy_hash = 'invalid'
      if (scenario === 'issues' || scenario === 'row') {
        api.get.mockResolvedValue({
          ...value,
          result: {
            ...value.result,
            status: scenario === 'row' ? 'sample_constraints_failed' : 'sample_constraints_passed',
            issues: [
              {
                code: 'numeric_out_of_range',
                field: 'value',
                row_index: scenario === 'row' ? 99 : 0,
              },
            ],
          },
        })
      } else api.get.mockResolvedValue(value)
      mount()
      await open()
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.sources.responseMismatch',
      )
    },
  )
  it('should hide cached success when reopening fails and never expose server details', async () => {
    mount()
    await open()
    await screen.findByText('common.enterprise.sqlSampleCheck.sample_constraints_passed')
    await open()
    api.get.mockRejectedValue(new Error('private rule contents'))
    await open()
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(
      screen.queryByText('common.enterprise.sqlSampleCheck.sample_constraints_passed'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('private rule contents')).not.toBeInTheDocument()
  })
})
