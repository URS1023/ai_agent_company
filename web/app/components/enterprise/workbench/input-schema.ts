/** Presentation adapter for native Parameters.user_input_form, not a second input authority.
 * Preserve raw field constraints and JSON defaults; native input/file checks remain mandatory.
 * Only external data tools are skipped: unknown/malformed editable fields block setup.
 */
import type {
  JsonObject,
  JsonValue,
  Parameters,
} from '@dify/contracts/api/console/installed-apps/types.gen'
import { zJsonObject, zJsonValue } from '@dify/contracts/api/console/installed-apps/zod.gen'
import { z } from 'zod'
import { InputVarType } from '@/app/components/workflow/types'

const supportedType = z.enum([
  InputVarType.textInput,
  InputVarType.paragraph,
  InputVarType.number,
  InputVarType.checkbox,
  InputVarType.select,
  InputVarType.singleFile,
  InputVarType.multiFiles,
  InputVarType.jsonObject,
])
const metadata = z.object({
  variable: z.string().min(1),
  label: z.string(),
  required: z.boolean().optional(),
  hide: z.boolean().optional(),
  options: z.array(z.string()).optional(),
  max_length: z.int().nonnegative().nullable().optional(),
})
export type WorkbenchInputField = Readonly<{
  type: z.infer<typeof supportedType>
  variable: string
  label: string
  required: boolean
  hidden: boolean
  definition: Readonly<JsonObject>
  initialValue?: JsonValue
}>

export class WorkbenchInputConfigurationError extends Error {
  constructor() {
    super('Invalid workbench input configuration')
    this.name = 'WorkbenchInputConfigurationError'
  }
}

export function decodeWorkbenchInputs(forms: Parameters['user_input_form']): WorkbenchInputField[] {
  try {
    const seen = new Set<string>()
    return forms.flatMap((item) => {
      const wrapper = zJsonObject.parse(item)
      const keys = Object.keys(wrapper).filter((key) => key !== 'default')
      if (keys.length !== 1) throw new WorkbenchInputConfigurationError()
      const key = keys[0]
      if (key === 'external_data_tool') return []
      const type = supportedType.parse(key)
      const definition = zJsonObject.parse(structuredClone(wrapper[type]))
      const field = metadata.parse(definition)
      if (seen.has(field.variable)) throw new WorkbenchInputConfigurationError()
      if (type === InputVarType.select && field.options === undefined)
        throw new WorkbenchInputConfigurationError()
      seen.add(field.variable)
      const initialValue = Object.hasOwn(wrapper, 'default') ? wrapper.default : definition.default
      return [
        {
          type,
          variable: field.variable,
          label: field.label || field.variable,
          required: field.required ?? false,
          hidden: field.hide ?? false,
          definition,
          ...(initialValue !== undefined
            ? { initialValue: zJsonValue.parse(structuredClone(initialValue)) }
            : {}),
        },
      ]
    })
  } catch {
    throw new WorkbenchInputConfigurationError()
  }
}

export function workbenchInputDefaults(fields: readonly WorkbenchInputField[]): JsonObject {
  return Object.fromEntries(
    fields.flatMap((field) =>
      field.initialValue === undefined
        ? []
        : [[field.variable, structuredClone(field.initialValue)]],
    ),
  )
}
