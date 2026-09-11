import type { FileEntity } from '@/app/components/base/file-uploader/types'
import type { VisionFile } from '@/types/app'
import { zFileType } from '@dify/contracts/api/console/installed-apps/zod.gen'
import { z } from 'zod'

type PreparedFiles = { status: 'pending' | 'invalid' } | { status: 'ready'; files: VisionFile[] }
const uploadedId = z.uuid().refine((id) => id !== '00000000-0000-0000-0000-000000000000')

/** Upload UI state is not a request: never silently discard a pending/failed selection.
 * Native preflight still checks actual file ownership, constraints and remote resolution.
 */
export function prepareWorkbenchFiles(selected: readonly FileEntity[]): PreparedFiles {
  if (
    selected.some(
      (file) => !Number.isFinite(file.progress) || file.progress < 0 || file.progress > 100,
    )
  )
    return { status: 'invalid' }
  if (selected.some((file) => file.progress < 100)) return { status: 'pending' }
  const files: VisionFile[] = []
  for (const file of selected) {
    const type = zFileType.safeParse(file.supportFileType)
    const id = uploadedId.safeParse(file.uploadedId)
    if (!type.success || !id.success) return { status: 'invalid' }
    if (file.transferMethod !== 'local_file' && file.transferMethod !== 'remote_url')
      return { status: 'invalid' }
    let url = ''
    if (file.transferMethod === 'remote_url') {
      try {
        const parsed = new URL(file.url ?? '')
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
          return { status: 'invalid' }
        url = file.url ?? ''
      } catch {
        return { status: 'invalid' }
      }
    }
    files.push({
      type: type.data,
      transfer_method: file.transferMethod,
      upload_file_id: id.data,
      url,
    })
  }
  return { status: 'ready', files }
}
