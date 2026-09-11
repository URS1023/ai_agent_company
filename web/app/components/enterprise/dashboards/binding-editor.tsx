'use client'

import type {
  DashboardBindingView,
  DashboardBindingWrite,
  DashboardQueryChoice,
} from '@enterprise/business-contracts/types'
import type { BindingDraft } from './binding-form'
import { Button } from '@langgenius/dify-ui/button'
import { Field, FieldControl, FieldLabel } from '@langgenius/dify-ui/field'
import { Form } from '@langgenius/dify-ui/form'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@langgenius/dify-ui/select'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { buildBindingWrite, suggestFieldMap } from './binding-form'

export function BindingEditor({
  view,
  choices,
  onSave,
  disabled = false,
}: {
  view: DashboardBindingView
  choices: readonly DashboardQueryChoice[]
  onSave: (command: DashboardBindingWrite) => void
  disabled?: boolean
}) {
  const { t } = useTranslation('common')
  const [drafts, setDrafts] = useState<BindingDraft[]>(() =>
    view.bindings.map((binding) => ({
      slotId: binding.slot_id,
      queryRef: binding.query_ref,
      queryRevision: binding.query_revision,
      fieldMap: { ...binding.field_map },
      parameters: Object.fromEntries(
        Object.entries(binding.parameters ?? {}).map(([name, value]) => [
          name,
          {
            text:
              value === null
                ? ''
                : typeof value === 'object'
                  ? JSON.stringify(value)
                  : String(value),
            isNull: value === null,
          },
        ]),
      ),
    })),
  )
  const [invalid, setInvalid] = useState(false)
  const update = (draft: BindingDraft) =>
    setDrafts((current) => [...current.filter((item) => item.slotId !== draft.slotId), draft])
  return (
    <Form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (disabled) return
        let command: DashboardBindingWrite
        try {
          command = buildBindingWrite(view, choices, drafts)
        } catch {
          setInvalid(true)
          return
        }
        setInvalid(false)
        onSave(command)
      }}
    >
      {invalid && (
        <p role="alert" className="text-text-warning">
          {t(($) => $['api.actionFailed'])}
        </p>
      )}
      {view.slots.map((slot) => {
        const draft = drafts.find((item) => item.slotId === slot.slot_id)
        const selected = choices.find(
          (item) =>
            item.query_ref === draft?.queryRef && item.query_revision === draft.queryRevision,
        )
        return (
          <fieldset
            key={slot.slot_id}
            disabled={disabled}
            className="space-y-3 rounded-xl border border-divider-regular p-4"
          >
            <legend className="system-sm-semibold text-text-primary">{slot.slot_id}</legend>
            <div className="flex flex-wrap gap-2">
              {choices.map((query) => (
                <Button
                  key={JSON.stringify([query.query_ref, query.query_revision])}
                  type="button"
                  variant="secondary"
                  disabled={disabled}
                  aria-pressed={selected === query}
                  onClick={() =>
                    selected !== query &&
                    update({
                      slotId: slot.slot_id,
                      queryRef: query.query_ref,
                      queryRevision: query.query_revision,
                      fieldMap: suggestFieldMap(slot, query.columns),
                      parameters: Object.fromEntries(
                        query.parameters.map((parameter) => [
                          parameter.input_key,
                          { text: '', isNull: false },
                        ]),
                      ),
                    })
                  }
                >
                  {query.query_ref} · {query.query_revision} · {query.device_id}
                </Button>
              ))}
            </div>
            {draft && (
              <>
                <p className="system-xs-regular text-text-secondary">
                  {draft.queryRef} · {draft.queryRevision}
                </p>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={disabled}
                  aria-label={`${t(($) => $['operation.remove'])} ${slot.slot_id}`}
                  onClick={() =>
                    setDrafts((current) => current.filter((item) => item.slotId !== slot.slot_id))
                  }
                >
                  {t(($) => $['operation.remove'])}
                </Button>
              </>
            )}
            {draft && selected && (
              <>
                {slot.columns.map((column) => (
                  <Field key={column.name}>
                    <FieldLabel>{column.name}</FieldLabel>
                    <Select
                      value={draft.fieldMap[column.name] ?? null}
                      disabled={disabled}
                      onValueChange={(value) => {
                        const fieldMap = { ...draft.fieldMap }
                        if (value) fieldMap[column.name] = value
                        else delete fieldMap[column.name]
                        update({ ...draft, fieldMap })
                      }}
                    >
                      <SelectTrigger aria-label={column.name}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {selected.columns
                          .filter(
                            (source) =>
                              source.kind === column.kind &&
                              (source.unit ?? null) === (column.unit ?? null),
                          )
                          .map((source) => (
                            <SelectItem key={source.name} value={source.name}>
                              {source.name}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </Field>
                ))}
                {selected.parameters.map((parameter) => {
                  const input = draft.parameters[parameter.input_key] ?? { text: '', isNull: false }
                  return (
                    <Field key={parameter.input_key}>
                      <FieldLabel>{parameter.input_key}</FieldLabel>
                      <FieldControl
                        value={input.text}
                        disabled={disabled || input.isNull}
                        onChange={(event) =>
                          update({
                            ...draft,
                            parameters: {
                              ...draft.parameters,
                              [parameter.input_key]: { ...input, text: event.target.value },
                            },
                          })
                        }
                      />
                      {parameter.nullable && (
                        <Button
                          type="button"
                          variant="secondary"
                          disabled={disabled}
                          aria-pressed={input.isNull}
                          onClick={() =>
                            update({
                              ...draft,
                              parameters: {
                                ...draft.parameters,
                                [parameter.input_key]: { ...input, isNull: !input.isNull },
                              },
                            })
                          }
                        >
                          {t(($) => $['enterprise.sources.nullable'])}
                        </Button>
                      )}
                    </Field>
                  )
                })}
              </>
            )}
          </fieldset>
        )
      })}
      <Button type="submit" disabled={disabled}>
        {t(($) => $['operation.save'])}
      </Button>
    </Form>
  )
}
