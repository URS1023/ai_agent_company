'use client'

import type { DeviceCreate } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Field, FieldControl, FieldDescription, FieldLabel } from '@langgenius/dify-ui/field'
import { Form } from '@langgenius/dify-ui/form'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function DeviceForm({
  initial,
  onSave,
  onCancel,
  pending,
}: {
  initial?: DeviceCreate
  onSave: (value: DeviceCreate) => void
  onCancel: () => void
  pending: boolean
}) {
  const { t } = useTranslation('common')
  const [invalid, setInvalid] = useState(false)

  return (
    <Form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault()
        if (pending) return
        const data = new FormData(event.currentTarget)
        const device_code = String(data.get('device_code') ?? '').trim()
        const name = String(data.get('name') ?? '').trim()
        if (!device_code || !name) {
          setInvalid(true)
          return
        }
        setInvalid(false)
        onSave({
          device_code,
          name,
          department: String(data.get('department') ?? '').trim(),
          description: String(data.get('description') ?? '').trim(),
        })
      }}
    >
      <Field name="device_code">
        <FieldLabel>{t(($) => $['enterprise.devices.code'])}</FieldLabel>
        <FieldControl
          defaultValue={initial?.device_code ?? ''}
          required
          maxLength={128}
          disabled={pending}
          autoComplete="off"
          className="font-mono"
        />
        <FieldDescription>{t(($) => $['enterprise.devices.codeHint'])}</FieldDescription>
      </Field>
      <Field name="name">
        <FieldLabel>{t(($) => $['account.name'])}</FieldLabel>
        <FieldControl
          defaultValue={initial?.name ?? ''}
          required
          maxLength={200}
          disabled={pending}
        />
      </Field>
      <Field name="department">
        <FieldLabel>{t(($) => $['enterprise.devices.department'])}</FieldLabel>
        <FieldControl defaultValue={initial?.department ?? ''} maxLength={200} disabled={pending} />
      </Field>
      <Field name="description">
        <FieldLabel>{t(($) => $['enterprise.devices.description'])}</FieldLabel>
        <FieldControl
          defaultValue={initial?.description ?? ''}
          maxLength={2000}
          disabled={pending}
        />
      </Field>
      {invalid && (
        <p role="alert" className="system-sm-regular text-text-destructive">
          {t(($) => $['enterprise.devices.required'])}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" disabled={pending} onClick={onCancel}>
          {t(($) => $['operation.cancel'])}
        </Button>
        <Button type="submit" variant="primary" loading={pending}>
          {t(($) => $['operation.save'])}
        </Button>
      </div>
    </Form>
  )
}
