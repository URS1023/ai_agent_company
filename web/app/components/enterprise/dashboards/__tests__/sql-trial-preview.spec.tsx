import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zSqlTrialEvidence } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import build from '../renderer-build.json'
import { SqlTrialPreview } from '../sql-trial-preview'

const renderer = vi.hoisted(() => ({ load: vi.fn(), dispose: vi.fn() }))
vi.mock('../renderer-loader', () => ({ loadDashboardRenderer: renderer.load }))

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  get: vi.fn<
    Client['dashboards']['byDashboardId']['sqlProposals']['byDraftId']['trials']['byTrialId']['preview']['get']
  >(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      dashboards: {
        byDashboardId: {
          sqlProposals: {
            byDraftId: { trials: { byTrialId: { preview: { get: api.get } } } },
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

const preview = {
  workspace_id: 'w',
  dashboard_id: 'screen',
  draft_id: 'draft',
  trial_id: 'trial',
  dashboard_revision: 1,
  template_id: 'template',
  design_identity: 'a'.repeat(64),
  renderer_build_id: build.rendererBuildId,
  status: 'preview_only' as const,
  semantic_status: 'not_reviewed' as const,
  unit_status: 'not_reviewed' as const,
  sample_status: 'sample_compatible' as const,
  slot: { slot_id: 'count', rows: [{ value: { kind: 'integer' as const, value: '1' } }] },
}
function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SqlTrialPreview evidence={evidence} fieldMap={{ value: 'n' }} scope={['w', 'actor']} />
    </QueryClientProvider>,
  )
}
async function open() {
  await userEvent
    .setup()
    .click(screen.getByRole('button', { name: 'common.enterprise.sqlPreview.open' }))
}
beforeEach(() => {
  vi.resetAllMocks()
  api.get.mockResolvedValue(preview)
  renderer.load.mockResolvedValue({ update: vi.fn(), dispose: renderer.dispose })
})
describe('Saved trial preview', () => {
  it('should request on demand and render only the trial slot without publishing', async () => {
    mount()
    expect(api.get).not.toHaveBeenCalled()
    await open()
    await vi.waitFor(() => expect(renderer.load).toHaveBeenCalledOnce())
    expect(api.get.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft', trial_id: 'trial' },
    })
    expect(renderer.load.mock.calls[0]?.[1]).toMatchObject({
      id: 'screen',
      template_id: 'template',
      current: { slots: [preview.slot] },
    })
    expect(screen.getByText('common.enterprise.sqlPreview.notice')).toBeVisible()
    await open()
    expect(renderer.dispose).toHaveBeenCalledOnce()
  })
  it.each(['workspace_id', 'dashboard_id', 'draft_id', 'trial_id', 'design_identity'] as const)(
    'should reject mismatched %s before mounting',
    async (field) => {
      api.get.mockResolvedValue({ ...preview, [field]: 'other' })
      mount()
      await open()
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.sources.responseMismatch',
      )
      expect(renderer.load).not.toHaveBeenCalled()
    },
  )
  it('should reject changed mapped values instead of displaying unrelated data', async () => {
    api.get.mockResolvedValue({
      ...preview,
      slot: { ...preview.slot, rows: [{ value: { kind: 'integer', value: '999' } }] },
    })
    mount()
    await open()
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(renderer.load).not.toHaveBeenCalled()
  })
  it('should discard a previous canvas when reopened access fails', async () => {
    mount()
    await open()
    await vi.waitFor(() => expect(renderer.load).toHaveBeenCalledOnce())
    await open()
    api.get.mockRejectedValue(new Error('private details'))
    await open()
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(renderer.load).toHaveBeenCalledOnce()
    expect(screen.queryByText('private details')).not.toBeInTheDocument()
  })
})
