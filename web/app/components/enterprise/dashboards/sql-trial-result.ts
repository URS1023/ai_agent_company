import type { SqlDraftInspection, SqlTrialCell } from '@enterprise/business-contracts/types'
import { zSqlTrialResult } from '@enterprise/business-contracts/zod'

export function inspectTrialResult(value: unknown, inspection: SqlDraftInspection, slotId: string) {
  const parsed = zSqlTrialResult.safeParse(value)
  if (!parsed.success || inspection.requires_regeneration) return null
  const result = parsed.data
  const draft = inspection.draft
  const slot = draft.proposals.slots.find((candidate) => candidate.slot_id === slotId)
  if (
    !slot ||
    inspection.current_dashboard_revision !== draft.dashboard_revision ||
    result.workspace_id !== draft.workspace_id ||
    result.dashboard_id !== draft.dashboard_id ||
    result.draft_id !== draft.draft_id ||
    result.slot_id !== slotId ||
    result.dashboard_revision !== draft.dashboard_revision ||
    result.design_identity !== draft.design_identity ||
    result.source.workspace_id !== draft.workspace_id ||
    result.source.workspace_id !== draft.source.workspace_id ||
    result.source.source_id !== draft.source.source_id ||
    result.source.revision !== draft.source.revision ||
    result.row_count !== result.rows.length ||
    new Set(result.columns).size !== result.columns.length ||
    result.rows.some((row) => row.length !== result.columns.length) ||
    Object.values(slot.field_map).some(
      (column) => column === undefined || !result.columns.includes(column),
    )
  )
    return null
  return result
}

export function formatTrialCell(cell: SqlTrialCell): string {
  if (cell.kind === 'null') return 'null'
  if (cell.kind === 'boolean') return cell.value ? 'true' : 'false'
  if (cell.kind === 'string' && cell.value === '') return '""'
  return cell.value
}
