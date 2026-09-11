'use client'

import { Textarea } from '@langgenius/dify-ui/textarea'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { parseWorkbenchJsonInput } from './input-json'

type Props = Readonly<{
  label: string
  text: string
  required?: boolean
  disabled?: boolean
  onChange: (text: string) => void
}>

/** Keeps the draft as text: the enclosing form uses the same parser before submission. */
export function WorkbenchJsonInput({ label, text, required, disabled, onChange }: Props) {
  const id = useId()
  const { t } = useTranslation('workflow')
  const parsed = parseWorkbenchJsonInput(text)
  const error =
    parsed.status === 'invalid'
      ? t(($) => $['errorMsg.invalidJson'], { field: label })
      : parsed.status === 'empty' && required
        ? t(($) => $['errorMsg.fieldRequired'], { field: label })
        : undefined
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="system-sm-medium text-text-secondary">
        {label}
      </label>
      <Textarea
        id={id}
        value={text}
        onValueChange={(next) => onChange(next)}
        required={required}
        disabled={disabled}
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-error` : undefined}
        spellCheck={false}
        rows={6}
        className="font-mono"
      />
      {error && (
        <p id={`${id}-error`} role="alert" className="system-xs-regular text-text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
