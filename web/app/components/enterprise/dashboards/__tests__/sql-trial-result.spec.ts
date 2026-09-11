import { zSqlDraftInspection, zSqlTrialResult } from '@enterprise/business-contracts/zod'
import { formatTrialCell, inspectTrialResult } from '../sql-trial-result'

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

describe('SQL trial display boundary', () => {
  it('should accept a matching capture without changing precision', () => {
    expect(inspectTrialResult(result, inspection, 'count')).toEqual(result)
  })

  it.each([
    { workspace_id: 'other' },
    { dashboard_id: 'other' },
    { draft_id: 'other' },
    { slot_id: 'other' },
    { dashboard_revision: 4 },
    { design_identity: 'd'.repeat(64) },
    { source: { ...result.source, workspace_id: 'other' } },
    { source: { ...result.source, source_id: 'other' } },
    { source: { ...result.source, revision: 's2' } },
    { row_count: 2 },
    { columns: ['count', 'count'] },
    { columns: ['missing'] },
    { rows: [[]] },
    { status: 'published' },
    { semantic_status: 'approved' },
    { draft_hash: 'invalid' },
    { captured_at: 'invalid' },
    { rows: [[{ kind: 'integer', value: 18446744073709551616 }]] },
  ])('should reject mismatched or malformed result %j', (change) => {
    expect(inspectTrialResult({ ...result, ...change }, inspection, 'count')).toBeNull()
  })

  it('should reject results when the inspected draft requires regeneration', () => {
    expect(
      inspectTrialResult(result, { ...inspection, requires_regeneration: true }, 'count'),
    ).toBeNull()
  })

  it('should reject results when the current dashboard revision differs', () => {
    expect(
      inspectTrialResult(result, { ...inspection, current_dashboard_revision: 4 }, 'count'),
    ).toBeNull()
  })

  it('should reject a slot absent from the inspected proposals', () => {
    expect(inspectTrialResult({ ...result, slot_id: 'other' }, inspection, 'other')).toBeNull()
  })

  it('should allow empty results with mapped column metadata', () => {
    const empty = { ...result, rows: [], row_count: 0 }
    expect(inspectTrialResult(empty, inspection, 'count')).toEqual(empty)
  })
})

describe('SQL trial cell text', () => {
  it('should preserve exact scalar values and distinguish null, empty, zero and false', () => {
    expect([
      formatTrialCell({ kind: 'integer', value: '18446744073709551616' }),
      formatTrialCell({ kind: 'decimal', value: '0.0000000000000000001' }),
      formatTrialCell({ kind: 'boolean', value: false }),
      formatTrialCell({ kind: 'null', value: null }),
      formatTrialCell({ kind: 'string', value: '' }),
      formatTrialCell({ kind: 'integer', value: '0' }),
      formatTrialCell({ kind: 'date', value: '2026-09-11' }),
      formatTrialCell({ kind: 'datetime', value: '2026-09-11T01:02:03' }),
      formatTrialCell({ kind: 'string', value: '<script>alert(1)</script>' }),
    ]).toEqual([
      '18446744073709551616',
      '0.0000000000000000001',
      'false',
      'null',
      '""',
      '0',
      '2026-09-11',
      '2026-09-11T01:02:03',
      '<script>alert(1)</script>',
    ])
  })
})
