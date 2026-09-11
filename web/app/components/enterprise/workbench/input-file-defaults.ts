import type { WorkbenchInputField } from './input-schema'
import type { FileEntity } from '@/app/components/base/file-uploader/types'
import { zFileType } from '@dify/contracts/api/console/installed-apps/zod.gen'
import { z } from 'zod'
import { InputVarType } from '@/app/components/workflow/types'
import { prepareWorkbenchFiles } from './input-files'
import { WorkbenchInputConfigurationError } from './input-schema'

const metadata = z.object({
  filename: z.string().min(1),
  size: z.number().int().nonnegative(),
  mime_type: z.string().min(1),
  related_id: z.uuid().nullish(),
  upload_file_id: z.uuid().nullish(),
  transfer_method: z.enum(['local_file', 'remote_url']),
  type: zFileType,
  url: z.string().nullish(),
  remote_url: z.string().nullish(),
})

/** Existing native file metadata, not proof of current access. Native preflight checks it again. */
export function restoreWorkbenchInputFiles(
  fields: readonly WorkbenchInputField[],
): Record<string, FileEntity[]> {
  try {
    const entries: [string, FileEntity[]][] = []
    for (const field of fields) {
      if (field.type !== InputVarType.singleFile && field.type !== InputVarType.multiFiles) continue
      const value = field.initialValue
      if (value === undefined || value === null || value === '') continue
      const values =
        field.type === InputVarType.singleFile ? [value] : z.array(z.unknown()).parse(value)
      const files = values.map((entry): FileEntity => {
        const data = metadata.parse(entry)
        const id = data.upload_file_id || data.related_id
        if (!id) throw new WorkbenchInputConfigurationError()
        return {
          id,
          uploadedId: id,
          name: data.filename,
          size: data.size,
          type: data.mime_type,
          supportFileType: data.type,
          transferMethod: data.transfer_method,
          progress: 100,
          ...(data.transfer_method === 'remote_url'
            ? { url: data.remote_url || data.url || '' }
            : {}),
        }
      })
      if (prepareWorkbenchFiles(files).status !== 'ready')
        throw new WorkbenchInputConfigurationError()
      entries.push([field.variable, files])
    }
    return Object.fromEntries(entries)
  } catch {
    throw new WorkbenchInputConfigurationError()
  }
}
