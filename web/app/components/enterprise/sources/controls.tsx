import type { FieldControlProps } from '@langgenius/dify-ui/field'
import { Checkbox } from '@langgenius/dify-ui/checkbox'
import { Field, FieldControl, FieldLabel } from '@langgenius/dify-ui/field'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@langgenius/dify-ui/select'

export function TextField({
  label,
  multiline = false,
  ...props
}: FieldControlProps & { label: string; multiline?: boolean }) {
  return (
    <Field name={props.name}>
      <FieldLabel>{label}</FieldLabel>
      <FieldControl
        {...props}
        render={multiline ? <textarea rows={4} /> : undefined}
        className={multiline ? 'min-h-24 py-2 font-mono' : undefined}
      />
    </Field>
  )
}

export function Choice<Value extends string>({
  label,
  name,
  values,
  defaultValue,
  value,
  onChange,
  disabled,
}: {
  label: string
  name: string
  values: readonly { value: Value; label: string }[]
  defaultValue?: Value
  value?: Value
  onChange?: (value: Value) => void
  disabled?: boolean
}) {
  return (
    <Field name={name}>
      <FieldLabel>{label}</FieldLabel>
      <Select<Value>
        name={name}
        defaultValue={defaultValue}
        value={value}
        onValueChange={(next) => {
          if (next !== null) onChange?.(next)
        }}
        disabled={disabled}
        items={values}
      >
        <SelectTrigger>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {values.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  )
}

export function Toggle({
  label,
  name,
  defaultChecked = false,
  disabled = false,
}: {
  label: string
  name: string
  defaultChecked?: boolean
  disabled?: boolean
}) {
  return (
    <label className="flex items-start gap-2 system-sm-regular text-text-secondary">
      <Checkbox name={name} defaultChecked={defaultChecked} disabled={disabled} />
      <span>{label}</span>
    </label>
  )
}
