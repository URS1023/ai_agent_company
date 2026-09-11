import type { SqlDraftInspection, SqlTrialEvidence } from '@enterprise/business-contracts/types'
import { zSqlDraftPreview } from '@enterprise/business-contracts/zod'
import { inspectTrialResult } from './sql-trial-result'

export function inspectDraftPreview(
  value: unknown,
  inspection: SqlDraftInspection,
  selected: readonly SqlTrialEvidence[],
) {
  const parsed = zSqlDraftPreview.safeParse(value)
  if (!parsed.success || selected.length === 0 || selected.length > 100) return null
  const preview = parsed.data
  const evidence = new Map(selected.map((item) => [item.trial_id, item]))
  const selectedSlots = new Set(selected.map((item) => item.result.slot_id))
  if (
    evidence.size !== selected.length ||
    selectedSlots.size !== selected.length ||
    preview.items.length !== selected.length
  )
    return null
  const first = preview.items[0]
  const seen = new Set<string>()
  for (const item of preview.items) {
    const stored = evidence.get(item.trial_id)
    if (!stored || seen.has(item.trial_id)) return null
    seen.add(item.trial_id)
    const result = inspectTrialResult(stored.result, inspection, item.slot.slot_id)
    const proposal = inspection.draft.proposals.slots.find(
      (slot) => slot.slot_id === item.slot.slot_id,
    )
    if (
      !result ||
      !proposal ||
      item.workspace_id !== result.workspace_id ||
      item.dashboard_id !== result.dashboard_id ||
      item.draft_id !== result.draft_id ||
      item.dashboard_revision !== result.dashboard_revision ||
      item.design_identity !== result.design_identity ||
      item.template_id !== first?.template_id ||
      item.renderer_build_id !== first?.renderer_build_id ||
      item.slot.rows.length !== result.row_count
    )
      return null
    const fields = Object.entries(proposal.field_map)
    if (
      fields.length === 0 ||
      fields.some(([, column]) => typeof column !== 'string' || !result.columns.includes(column))
    )
      return null
    if (
      item.slot.rows.some(
        (row, index) =>
          Object.keys(row).length !== fields.length ||
          fields.some(([field, column]) => {
            if (typeof column !== 'string') return true
            const original = result.rows[index]?.[result.columns.indexOf(column)]
            return (
              !original ||
              row[field]?.kind !== original.kind ||
              row[field]?.value !== original.value
            )
          }),
      )
    )
      return null
  }
  const missing = preview.missing_required_slots
  if (
    new Set(missing).size !== missing.length ||
    missing.some((slot) => !slot.trim() || selectedSlots.has(slot))
  )
    return null
  return preview
}
