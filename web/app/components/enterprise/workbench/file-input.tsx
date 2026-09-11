'use client'

import type { UploadConfig } from '@dify/contracts/api/console/files/types.gen'
import type { WorkbenchInputField } from './input-schema'
import type { FileEntity } from '@/app/components/base/file-uploader/types'
import { zFileType } from '@dify/contracts/api/console/installed-apps/zod.gen'
import { useCallback, useId, useLayoutEffect, useRef } from 'react'
import { z } from 'zod'
import FileUploaderInAttachmentWrapper from '@/app/components/base/file-uploader/file-uploader-in-attachment'
import { InputVarType } from '@/app/components/workflow/types'
import { WorkbenchInputConfigurationError } from './input-schema'

const constraints = z.object({
  allowed_file_types: z.array(zFileType).optional(),
  allowed_file_extensions: z.array(z.string()).optional(),
  allowed_file_upload_methods: z.array(z.enum(['local_file', 'remote_url'])).optional(),
  max_length: z.int().positive().nullable().optional(),
})
type Props = Readonly<{
  field: WorkbenchInputField
  uploadConfig: UploadConfig
  initialFiles?: FileEntity[]
  disabled?: boolean
  onChange: (files: FileEntity[]) => void
}>

/** Native uploader owns its initial selection; remount the enclosing form to reset scope. */
export function WorkbenchFileInput({
  field,
  uploadConfig,
  initialFiles,
  disabled,
  onChange,
}: Props) {
  const labelId = useId()
  const changeRef = useRef(onChange)
  useLayoutEffect(() => {
    changeRef.current = onChange
  }, [onChange])
  const handleChange = useCallback((files: FileEntity[]) => changeRef.current(files), [])
  if (field.hidden) return null
  if (field.type !== InputVarType.singleFile && field.type !== InputVarType.multiFiles)
    throw new WorkbenchInputConfigurationError()
  const parsed = constraints.safeParse(field.definition)
  if (!parsed.success) throw new WorkbenchInputConfigurationError()
  return (
    <div role="group" aria-labelledby={labelId} className="space-y-1">
      <div id={labelId} className="system-sm-medium text-text-secondary">
        {field.label}
      </div>
      <FileUploaderInAttachmentWrapper
        value={initialFiles}
        onChange={handleChange}
        isDisabled={disabled}
        fileConfig={{
          enabled: true,
          allowed_file_types: parsed.data.allowed_file_types,
          allowed_file_extensions: parsed.data.allowed_file_extensions,
          allowed_file_upload_methods: parsed.data.allowed_file_upload_methods,
          number_limits:
            field.type === InputVarType.singleFile
              ? 1
              : (parsed.data.max_length ?? uploadConfig.workflow_file_upload_limit),
          fileUploadConfig: uploadConfig,
        }}
      />
    </div>
  )
}
