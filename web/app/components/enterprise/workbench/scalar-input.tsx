'use client'

import type { JsonValue } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { WorkbenchInputField } from './input-schema'
import { Checkbox } from '@langgenius/dify-ui/checkbox'
import { Input } from '@langgenius/dify-ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectTrigger,
} from '@langgenius/dify-ui/select'
import { Textarea } from '@langgenius/dify-ui/textarea'
import { useId } from 'react'
import { InputVarType } from '@/app/components/workflow/types'
import { WorkbenchInputConfigurationError } from './input-schema'

type Props = Readonly<{
  field: WorkbenchInputField
  value: JsonValue | undefined
  disabled?: boolean
  onChange: (value: string | boolean) => void
}>

export function WorkbenchScalarInput({ field, value, disabled, onChange }: Props) {
  const id = useId()
  if (field.hidden) return null
  const labelId = `${id}-label`
  const text = typeof value === 'string' || typeof value === 'number' ? String(value) : ''
  const limit = field.definition.max_length
  const maxLength = typeof limit === 'number' ? limit : undefined
  const shared = { id, 'aria-labelledby': labelId, disabled }
  let control
  switch (field.type) {
    case InputVarType.checkbox:
      // Native "required" means present: false is a valid value, not a required checkmark.
      control = (
        <Checkbox {...shared} checked={value === true} onCheckedChange={(next) => onChange(next)} />
      )
      break
    case InputVarType.textInput:
    case InputVarType.number:
      control = (
        <Input
          {...shared}
          type={field.type === InputVarType.number ? 'number' : 'text'}
          step={field.type === InputVarType.number ? 'any' : undefined}
          required={field.required}
          maxLength={maxLength}
          value={text}
          onChange={(event) => onChange(event.target.value)}
        />
      )
      break
    case InputVarType.paragraph:
      control = (
        <Textarea
          {...shared}
          required={field.required}
          maxLength={maxLength}
          value={text}
          onValueChange={(next) => onChange(next)}
        />
      )
      break
    case InputVarType.select: {
      const options = field.definition.options
      if (
        !Array.isArray(options) ||
        !options.every((option): option is string => typeof option === 'string')
      )
        throw new WorkbenchInputConfigurationError()
      control = (
        <Select
          value={value === undefined || value === null ? null : text}
          disabled={disabled}
          onValueChange={(next) => {
            if (next !== null) onChange(next)
          }}
        >
          <SelectTrigger {...shared} aria-required={field.required} className="w-full">
            {text || field.label}
          </SelectTrigger>
          <SelectContent>
            {Array.from(new Set(options)).map((option) => (
              <SelectItem key={option} value={option}>
                <SelectItemText>{option}</SelectItemText>
                <SelectItemIndicator />
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
      break
    }
    default:
      throw new WorkbenchInputConfigurationError()
  }
  return (
    <div className="space-y-1">
      <label id={labelId} htmlFor={id} className="system-sm-medium text-text-secondary">
        {field.label}
      </label>
      {control}
    </div>
  )
}
