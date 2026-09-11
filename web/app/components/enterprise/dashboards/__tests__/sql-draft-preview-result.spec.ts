import { zSqlDraftInspection, zSqlTrialResult } from '@enterprise/business-contracts/zod'
import { inspectDraftPreview } from '../sql-draft-preview-result'

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
describe('Selected draft preview boundary', () => {
  it('should preserve exact selected data and missing-slot metadata', () => {
    expect(inspectDraftPreview(preview, inspection, selected)).toEqual(preview)
  })
  it.each([
    { items: [] },
    { items: [item, item] },
    { items: [{ ...item, trial_id: 'foreign' }] },
    { items: [{ ...item, draft_id: 'other' }] },
    { items: [{ ...item, dashboard_revision: 4 }] },
    { items: [{ ...item, slot: { ...item.slot, rows: [] } }] },
    {
      items: [
        { ...item, slot: { ...item.slot, rows: [{ value: { kind: 'integer', value: '2' } }] } },
      ],
    },
    {
      items: [
        {
          ...item,
          slot: {
            ...item.slot,
            rows: [
              {
                value: { kind: 'integer', value: '18446744073709551616' },
                extra: { kind: 'null', value: null },
              },
            ],
          },
        },
      ],
    },
    { missing_required_slots: ['count'] },
    { missing_required_slots: ['other', 'other'] },
    { snapshot_status: 'consistent_snapshot' },
  ])('should reject mixed, omitted, duplicated or altered results %j', (change) => {
    expect(inspectDraftPreview({ ...preview, ...change }, inspection, selected)).toBeNull()
  })
  it('should reject stale draft context', () => {
    expect(
      inspectDraftPreview(preview, { ...inspection, requires_regeneration: true }, selected),
    ).toBeNull()
  })
  it('should reject duplicate selections and foreign evidence', () => {
    expect(inspectDraftPreview(preview, inspection, [...selected, ...selected])).toBeNull()
    expect(
      inspectDraftPreview(preview, inspection, [
        { ...selected[0]!, result: { ...result, workspace_id: 'other' } },
      ]),
    ).toBeNull()
  })
  it('should accept multiple selected slots but require one shared renderer and template', () => {
    const draft = {
      ...inspection,
      draft: {
        ...inspection.draft,
        proposals: {
          slots: [
            ...inspection.draft.proposals.slots,
            { ...inspection.draft.proposals.slots[0]!, slot_id: 'second' },
          ],
        },
      },
    }
    const evidence = [
      ...selected,
      { ...selected[0]!, trial_id: 'trial-2', result: { ...result, slot_id: 'second' } },
    ]
    const second = { ...item, trial_id: 'trial-2', slot: { ...item.slot, slot_id: 'second' } }
    const combined = { ...preview, items: [second, item] }
    expect(inspectDraftPreview(combined, draft, evidence)).toEqual(combined)
    expect(
      inspectDraftPreview(
        { ...combined, items: [item, { ...second, template_id: 'other' }] },
        draft,
        evidence,
      ),
    ).toBeNull()
    expect(
      inspectDraftPreview(
        { ...combined, items: [item, { ...second, renderer_build_id: 'other' }] },
        draft,
        evidence,
      ),
    ).toBeNull()
  })
})
