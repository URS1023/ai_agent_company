import {
  zSqlDraftInspection,
  zSqlDraftPreview,
  zSqlTrialResult,
} from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlDraftPreview } from '../sql-draft-preview'

const api = vi.hoisted(() => ({ post: vi.fn(), load: vi.fn(), dispose: vi.fn() }))
vi.mock('../renderer-loader', () => ({ loadDashboardRenderer: api.load }))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      dashboards: {
        byDashboardId: { sqlProposals: { byDraftId: { preview: { post: api.post } } } },
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
  rows: [[{ kind: 'integer', value: '18446744073709551616' }]],
  row_count: 1,
  status: 'trial_only',
  semantic_status: 'not_reviewed',
})

const selected = [
  {
    schema_version: 1 as const,
    trial_id: 'trial-1',
    actor_id: 'actor',
    recorded_at: '2026-09-11T00:00:01Z',
    result,
  },
]
const item = {
  workspace_id: 'w',
  dashboard_id: 'screen',
  draft_id: 'draft',
  trial_id: 'trial-1',
  dashboard_revision: 3,
  template_id: 'template',
  renderer_build_id: 'renderer',
  design_identity: result.design_identity,
  status: 'preview_only',
  semantic_status: 'not_reviewed',
  unit_status: 'not_reviewed',
  sample_status: 'sample_compatible',
  slot: { slot_id: 'count', rows: [{ value: { kind: 'integer', value: '18446744073709551616' } }] },
}
const preview = {
  status: 'preview_only',
  snapshot_status: 'independent_trials',
  items: [item],
  missing_required_slots: ['other'],
}

function mount(records = selected, currentInspection = inspection) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SqlDraftPreview inspection={currentInspection} selected={records} />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  api.post.mockResolvedValue(zSqlDraftPreview.parse(preview))
  api.load.mockResolvedValue({ update: vi.fn(), dispose: api.dispose })
})
async function run() {
  await userEvent
    .setup()
    .click(screen.getByRole('button', { name: 'common.enterprise.sqlPreview.groupRun' }))
}
describe('Combined trial preview', () => {
  it('should fill both slots losslessly when two trials are selected', async () => {
    const second = {
      ...selected[0]!,
      trial_id: 'trial-2',
      result: {
        ...result,
        slot_id: 'quality',
        rows: [[{ kind: 'decimal' as const, value: '98.2500' }]],
      },
    }
    const secondItem = {
      ...item,
      trial_id: second.trial_id,
      slot: { slot_id: 'quality', rows: [{ value: { kind: 'decimal', value: '98.2500' } }] },
    }
    const twoSlotInspection = zSqlDraftInspection.parse({
      ...inspection,
      draft: {
        ...inspection.draft,
        proposals: {
          slots: [
            ...inspection.draft.proposals.slots,
            { ...inspection.draft.proposals.slots[0], slot_id: 'quality' },
          ],
        },
      },
    })
    api.post.mockResolvedValue(
      zSqlDraftPreview.parse({
        ...preview,
        items: [item, secondItem],
        missing_required_slots: [],
      }),
    )
    mount([...selected, second], twoSlotInspection)

    await run()

    await vi.waitFor(() => expect(api.load).toHaveBeenCalledOnce())
    expect(api.post.mock.calls[0]?.[0].body).toEqual({ trial_ids: ['trial-1', 'trial-2'] })
    expect(api.load.mock.calls[0]?.[1].current.slots).toEqual([item.slot, secondItem.slot])
    expect(screen.queryByText('common.enterprise.sqlPreview.missing')).not.toBeInTheDocument()
  })
  it('should send only selected IDs and display missing slots with a preview notice', async () => {
    mount()
    expect(api.post).not.toHaveBeenCalled()
    await run()
    await vi.waitFor(() => expect(api.load).toHaveBeenCalledOnce())
    expect(api.post.mock.calls[0]?.[0]).toEqual({
      params: { dashboard_id: 'screen', draft_id: 'draft' },
      body: { trial_ids: ['trial-1'] },
      headers: { Origin: window.location.origin },
    })
    expect(screen.getByText('other')).toBeVisible()
    expect(screen.getByText('common.enterprise.sqlPreview.groupNotice')).toBeVisible()
    expect(api.load.mock.calls[0]?.[1].current.slots).toEqual([item.slot])
  })
  it('should not enable a preview with no selected trials', () => {
    mount([])
    expect(screen.getByRole('button')).toBeDisabled()
    expect(api.post).not.toHaveBeenCalled()
  })
  it('should reject unexpected result selections before rendering', async () => {
    api.post.mockResolvedValue({ ...preview, items: [{ ...item, trial_id: 'foreign' }] })
    mount()
    await run()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.sources.responseMismatch',
    )
    expect(api.load).not.toHaveBeenCalled()
  })
  it('should dispose old results and show opaque errors when a new preview fails', async () => {
    mount()
    await run()
    await vi.waitFor(() => expect(api.load).toHaveBeenCalledOnce())
    api.post.mockRejectedValue(new Error('private details'))
    await run()
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(api.dispose).toHaveBeenCalledOnce()
    expect(screen.queryByText('private details')).not.toBeInTheDocument()
  })
})
