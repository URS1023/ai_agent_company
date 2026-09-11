'use client'

import type { CreateSchedule, ScheduleActorChoice } from '@enterprise/business-contracts/types'
import { zCreateSchedule } from '@enterprise/business-contracts/zod'
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

export function ScheduleForm({
  actors,
  bindingRevision,
  pending,
  onSave,
}: {
  actors: readonly ScheduleActorChoice[]
  bindingRevision: number
  pending: boolean
  onSave: (payload: CreateSchedule) => void
}) {
  const { t } = useTranslation('common')
  const [invalid, setInvalid] = useState(false)
  const [initialActor] = useState(() => actors[0]?.actor_id ?? null)
  const choices = actors.map((actor) => ({ value: actor.actor_id, label: actor.display_name }))
  const policies = [
    { value: 'skip', label: t(($) => $['enterprise.schedule.skip']) },
    { value: 'coalesce', label: t(($) => $['enterprise.schedule.coalesce']) },
  ]
  return (
    <Form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (pending || !actors.length) return
        const data = new FormData(event.currentTarget)
        const anchor = new Date(String(data.get('anchor_at') ?? ''))
        const result = zCreateSchedule.safeParse({
          expected_binding_revision: bindingRevision,
          service_actor_id: data.get('service_actor_id'),
          anchor_at: Number.isFinite(anchor.getTime()) ? anchor.toISOString() : '',
          interval_seconds: Number(data.get('interval_seconds')),
          window_seconds: Number(data.get('window_seconds')),
          grace_seconds: Number(data.get('grace_seconds')),
          missed_policy: data.get('missed_policy'),
        })
        if (
          !result.success ||
          result.data.grace_seconds >= result.data.interval_seconds ||
          !actors.some((actor) => actor.actor_id === result.data.service_actor_id)
        ) {
          setInvalid(true)
          return
        }
        setInvalid(false)
        onSave(result.data)
      }}
    >
      <Field name="service_actor_id">
        <FieldLabel>{t(($) => $['enterprise.schedule.actor'])}</FieldLabel>
        <Select
          name="service_actor_id"
          items={choices}
          defaultValue={initialActor}
          disabled={pending || !actors.length}
        >
          <SelectTrigger aria-label={t(($) => $['enterprise.schedule.actor'])}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {choices.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field name="anchor_at">
        <FieldLabel>{t(($) => $['enterprise.schedule.anchor'])}</FieldLabel>
        <FieldControl type="datetime-local" required disabled={pending} />
      </Field>
      {(
        [
          ['interval_seconds', 'enterprise.schedule.interval', 60, 1, 86400],
          ['window_seconds', 'enterprise.schedule.window', 300, 1, 604800],
          ['grace_seconds', 'enterprise.schedule.grace', 10, 0, 86399],
        ] as const
      ).map(([name, label, initial, min, max]) => (
        <Field key={name} name={name}>
          <FieldLabel>{t(($) => $[label])}</FieldLabel>
          <FieldControl
            type="number"
            required
            step={1}
            min={min}
            max={max}
            defaultValue={initial}
            disabled={pending}
          />
        </Field>
      ))}
      <Field name="missed_policy">
        <FieldLabel>{t(($) => $['enterprise.schedule.policy'])}</FieldLabel>
        <Select name="missed_policy" items={policies} defaultValue="skip" disabled={pending}>
          <SelectTrigger aria-label={t(($) => $['enterprise.schedule.policy'])}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {policies.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      {invalid && (
        <p role="alert" className="system-sm-regular text-text-destructive">
          {t(($) => $['enterprise.schedule.invalid'])}
        </p>
      )}
      <Button type="submit" variant="primary" disabled={pending || !actors.length}>
        {t(($) => $['operation.save'])}
      </Button>
    </Form>
  )
}
