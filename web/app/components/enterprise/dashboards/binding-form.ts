import type {
  DashboardBindingItem,
  DashboardBindingView,
  DashboardBindingWrite,
  DashboardQueryChoice,
  DashboardQueryParameter,
  QueryColumn,
  SlotContract,
} from '@enterprise/business-contracts/types'
import { zDashboardBindingWrite } from '@enterprise/business-contracts/zod'
import isEqual from 'fast-deep-equal'

export function suggestFieldMap(
  slot: SlotContract,
  columns: readonly QueryColumn[],
): Record<string, string> {
  return Object.fromEntries(
    slot.columns.flatMap((column) => {
      const compatible = columns.filter(
        (source) => source.kind === column.kind && (source.unit ?? null) === (column.unit ?? null),
      )
      const exact = compatible.filter((source) => source.name === column.name)
      const candidate =
        exact.length === 1 ? exact[0] : compatible.length === 1 ? compatible[0] : undefined
      return candidate ? [[column.name, candidate.name]] : []
    }),
  )
}

function normalizedBindings(items: readonly DashboardBindingItem[]) {
  return items
    .map((item) => ({ ...item, parameters: item.parameters ?? {} }))
    .sort((left, right) =>
      left.slot_id < right.slot_id ? -1 : left.slot_id > right.slot_id ? 1 : 0,
    )
}

export function bindingReceiptMatches(
  previous: DashboardBindingView,
  command: DashboardBindingWrite,
  receipt: DashboardBindingView,
): boolean {
  const expected = normalizedBindings(command.bindings)
  const changed = !isEqual(normalizedBindings(previous.bindings), expected)
  return (
    previous.revision === command.expected_revision &&
    previous.design_identity === command.expected_design_identity &&
    receipt.dashboard_id === previous.dashboard_id &&
    receipt.design_identity === command.expected_design_identity &&
    receipt.revision === command.expected_revision + (changed ? 1 : 0) &&
    isEqual(receipt.slots, previous.slots) &&
    isEqual(normalizedBindings(receipt.bindings), expected)
  )
}

export type BindingDraft = {
  slotId: string
  queryRef: string
  queryRevision: string
  fieldMap: Record<string, string>
  parameters: Record<string, { text: string; isNull: boolean }>
}

export function buildBindingWrite(
  view: DashboardBindingView,
  choices: readonly DashboardQueryChoice[],
  drafts: readonly BindingDraft[],
): DashboardBindingWrite {
  const seen = new Set<string>()
  const bindings = drafts.map((draft) => {
    const slot = view.slots.find((item) => item.slot_id === draft.slotId)
    const query = choices.find(
      (item) => item.query_ref === draft.queryRef && item.query_revision === draft.queryRevision,
    )
    if (!slot || !query || seen.has(draft.slotId)) throw new Error('binding_selection_invalid')
    seen.add(draft.slotId)
    if (
      Object.keys(draft.fieldMap).some(
        (name) => !slot.columns.some((column) => column.name === name),
      )
    )
      throw new Error('binding_mapping_invalid')
    for (const column of slot.columns) {
      const sourceName = draft.fieldMap[column.name]
      if (!sourceName && column.required === false) continue
      const source = query.columns.find((item) => item.name === sourceName)
      if (!source || source.kind !== column.kind || (source.unit ?? null) !== (column.unit ?? null))
        throw new Error('binding_mapping_invalid')
    }
    if (
      Object.keys(draft.parameters).length !== query.parameters.length ||
      query.parameters.some((item) => !Object.hasOwn(draft.parameters, item.input_key))
    )
      throw new Error('binding_parameters_invalid')
    return {
      slot_id: slot.slot_id,
      query_ref: query.query_ref,
      query_revision: query.query_revision,
      field_map: { ...draft.fieldMap },
      parameters: Object.fromEntries(
        query.parameters.map((parameter) => {
          const input = draft.parameters[parameter.input_key]!
          return [parameter.input_key, parseBindingParameter(parameter, input.text, input.isNull)]
        }),
      ),
    }
  })
  return zDashboardBindingWrite.parse({
    expected_revision: view.revision,
    expected_design_identity: view.design_identity,
    bindings,
  })
}

function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000-')) return false
  const date = new Date(`${value}T00:00:00Z`)
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
}

export function parseBindingParameter(
  parameter: DashboardQueryParameter,
  text: string,
  isNull = false,
): string | number | boolean | null {
  if (isNull) {
    if (parameter.nullable) return null
    throw new Error('binding_parameter_null_not_allowed')
  }
  switch (parameter.kind) {
    case 'string':
      return text
    case 'boolean':
      if (text === 'true') return true
      if (text === 'false') return false
      break
    case 'integer': {
      const value = Number(text)
      if (/^-?\d+$/.test(text) && Number.isSafeInteger(value)) return value
      break
    }
    case 'decimal':
      // Decimal text must never pass through Number: source precision is significant.
      if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return text
      break
    case 'date':
      if (validDate(text)) return text
      break
    case 'datetime':
      if (
        validDate(text.slice(0, 10)) &&
        /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,6})?)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(
          text,
        )
      )
        return text
  }
  throw new Error('binding_parameter_type_mismatch')
}
