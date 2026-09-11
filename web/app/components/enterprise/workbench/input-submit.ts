import type { JsonObject } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { WorkbenchInputField } from './input-schema'
import type { FileEntity } from '@/app/components/base/file-uploader/types'
import { InputVarType } from '@/app/components/workflow/types'
import { prepareWorkbenchFiles } from './input-files'
import { parseWorkbenchJsonInput } from './input-json'

type InputError = 'required' | 'invalid' | 'pending'
type PreparedInputs =
  | { status: 'ready'; inputs: JsonObject }
  | { status: 'invalid'; errors: Record<string, InputError> }

/** UI submission gate only; the native server still validates effective app configuration. */
export function prepareWorkbenchInputs(
  fields: readonly WorkbenchInputField[],
  values: Readonly<JsonObject>,
  selections: Readonly<Record<string, readonly FileEntity[]>>,
): PreparedInputs {
  const entries: [string, unknown][] = []
  const errors: [string, InputError][] = []
  for (const field of fields) {
    const key = field.variable
    let value = Object.hasOwn(values, key) ? values[key] : undefined
    if (field.type === InputVarType.singleFile || field.type === InputVarType.multiFiles) {
      if (
        !Object.hasOwn(selections, key) &&
        value !== undefined &&
        value !== null &&
        value !== ''
      ) {
        errors.push([key, 'invalid'])
        continue
      }
      const selected = Object.hasOwn(selections, key) ? (selections[key] ?? []) : []
      const prepared = prepareWorkbenchFiles(selected)
      if (prepared.status !== 'ready') {
        errors.push([key, prepared.status])
        continue
      }
      const max = field.type === InputVarType.singleFile ? 1 : field.definition.max_length
      if (typeof max === 'number' && selected.length > max) {
        errors.push([key, 'invalid'])
        continue
      }
      if (field.required && !selected.length) {
        errors.push([key, 'required'])
        continue
      }
      value = field.type === InputVarType.singleFile ? prepared.files[0] : prepared.files
    } else if (field.type === InputVarType.jsonObject && typeof value === 'string') {
      const parsed = parseWorkbenchJsonInput(value)
      if (parsed.status === 'invalid') {
        errors.push([key, 'invalid'])
        continue
      }
      value = parsed.status === 'valid' ? parsed.value : undefined
    }
    if (value === undefined || value === null || value === '') {
      if (field.required) errors.push([key, 'required'])
      else if (value !== undefined) entries.push([key, value])
      continue
    }
    let valid = true
    switch (field.type) {
      case InputVarType.textInput:
      case InputVarType.paragraph:
        valid =
          typeof value === 'string' &&
          (!field.definition.max_length ||
            (typeof field.definition.max_length === 'number' &&
              Array.from(value).length <= field.definition.max_length))
        break
      case InputVarType.select:
        valid =
          typeof value === 'string' &&
          Array.isArray(field.definition.options) &&
          field.definition.options.includes(value)
        break
      case InputVarType.number:
        valid =
          typeof value === 'number'
            ? Number.isFinite(value)
            : typeof value === 'string' &&
              /^[+-]?(?:\d+|(?:\d+\.\d*|\d*\.\d+)(?:e[+-]?\d+)?)$/i.test(value.trim()) &&
              Number.isFinite(Number(value))
        break
      case InputVarType.checkbox:
        valid = typeof value === 'boolean'
        break
      case InputVarType.jsonObject:
        valid = typeof value === 'object' && !Array.isArray(value)
        break
    }
    if (!valid) errors.push([key, 'invalid'])
    else entries.push([key, structuredClone(value)])
  }
  return errors.length
    ? { status: 'invalid', errors: Object.fromEntries(errors) }
    : { status: 'ready', inputs: Object.fromEntries(entries) }
}
