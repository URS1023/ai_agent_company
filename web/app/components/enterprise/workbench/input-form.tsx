'use client'

import type { UploadConfig } from '@dify/contracts/api/console/files/types.gen'
import type { JsonObject } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { WorkbenchInputField } from './input-schema'
import type { FileEntity } from '@/app/components/base/file-uploader/types'
import { zJsonValue } from '@dify/contracts/api/console/installed-apps/zod.gen'
import { Button } from '@langgenius/dify-ui/button'
import { useState } from 'react'
import { InputVarType } from '@/app/components/workflow/types'
import { WorkbenchFileInput } from './file-input'
import { workbenchInputDefaults } from './input-schema'
import { prepareWorkbenchInputs } from './input-submit'
import { WorkbenchJsonInput } from './json-input'
import { WorkbenchScalarInput } from './scalar-input'

type Props = Readonly<{
  scopeKey: string
  fields: readonly WorkbenchInputField[]
  uploadConfig?: UploadConfig
  initialFiles?: Readonly<Record<string, FileEntity[]>>
  busy?: boolean
  labels: Readonly<{
    submit: string
    required: string
    invalid: string
    pending: string
    configMissing: string
  }>
  onSubmit: (inputs: JsonObject) => void
}>

/** Owner supplies localized labels and a scope key including account/app/config identity. */
export function WorkbenchInputForm(props: Props) {
  return <InputForm key={props.scopeKey} {...props} />
}

function InputForm({ fields, uploadConfig, initialFiles, busy, labels, onSubmit }: Props) {
  const [values, setValues] = useState<JsonObject>(() => {
    const defaults = workbenchInputDefaults(fields)
    return Object.fromEntries(
      fields.flatMap((field) => {
        const value = Object.hasOwn(defaults, field.variable) ? defaults[field.variable] : undefined
        if (value === undefined) return []
        return [
          [
            field.variable,
            field.type === InputVarType.jsonObject && value !== null && typeof value !== 'string'
              ? JSON.stringify(value, null, 2)
              : value,
          ],
        ]
      }),
    )
  })
  const [selections, setSelections] = useState<Readonly<Record<string, FileEntity[]>>>(
    () => initialFiles ?? {},
  )
  const [attempted, setAttempted] = useState(false)
  const prepared = prepareWorkbenchInputs(fields, values, selections)
  const missingConfig =
    !uploadConfig &&
    fields.some(
      (field) =>
        !field.hidden &&
        (field.type === InputVarType.singleFile || field.type === InputVarType.multiFiles),
    )
  const setValue = (key: string, value: string | boolean) =>
    setValues((previous) => ({ ...previous, [key]: value }))
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (busy || missingConfig) return
        setAttempted(true)
        if (prepared.status === 'ready') onSubmit(prepared.inputs)
      }}
    >
      {fields
        .filter((field) => !field.hidden)
        .map((field) => {
          const value = Object.hasOwn(values, field.variable) ? values[field.variable] : undefined
          if (field.type === InputVarType.singleFile || field.type === InputVarType.multiFiles)
            return uploadConfig ? (
              <WorkbenchFileInput
                key={field.variable}
                field={field}
                uploadConfig={uploadConfig}
                initialFiles={initialFiles?.[field.variable]}
                disabled={busy}
                onChange={(files) =>
                  setSelections((previous) => ({ ...previous, [field.variable]: files }))
                }
              />
            ) : null
          if (field.type === InputVarType.jsonObject)
            return (
              <WorkbenchJsonInput
                key={field.variable}
                label={field.label}
                text={typeof value === 'string' ? value : ''}
                required={field.required}
                disabled={busy}
                onChange={(text) => setValue(field.variable, text)}
              />
            )
          return (
            <WorkbenchScalarInput
              key={field.variable}
              field={field}
              value={value === undefined ? undefined : zJsonValue.parse(value)}
              disabled={busy}
              onChange={(next) => setValue(field.variable, next)}
            />
          )
        })}
      {missingConfig && (
        <p role="alert" className="system-sm-regular text-text-destructive">
          {labels.configMissing}
        </p>
      )}
      {attempted && prepared.status === 'invalid' && (
        <ul role="alert" className="system-sm-regular text-text-destructive">
          {fields.flatMap((field) => {
            const error = Object.hasOwn(prepared.errors, field.variable)
              ? prepared.errors[field.variable]
              : undefined
            return error
              ? [
                  <li key={field.variable}>
                    {field.label}: {labels[error]}
                  </li>,
                ]
              : []
          })}
        </ul>
      )}
      <Button type="submit" variant="primary" disabled={busy || missingConfig}>
        {labels.submit}
      </Button>
    </form>
  )
}
