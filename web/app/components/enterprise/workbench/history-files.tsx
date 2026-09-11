import type { MessageFile } from '@dify/contracts/api/console/installed-apps/types.gen'
import { Button } from '@langgenius/dify-ui/button'
import {
  Dialog,
  DialogCloseButton,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from '@langgenius/dify-ui/dialog'
import { useTranslation } from 'react-i18next'
import FileTypeIcon from '@/app/components/base/file-uploader/file-type-icon'
import { formatFileSize } from '@/utils/format'
import { WorkbenchHistoryMedia } from './history-media'

function attachmentHref(value: string | null | undefined): string | undefined {
  if (
    !value ||
    /[\s\\]/.test(value) ||
    Array.from(value).some(
      (character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    return undefined
  if (!/^https?:\/\//i.test(value) && !/^\/(?!\/)/.test(value)) return undefined
  try {
    const parsed = new URL(value, 'https://attachment.invalid')
    if (parsed.username || parsed.password) return undefined
    return value
  } catch {
    return undefined
  }
}

// Streaming files have no filename/size/MIME yet; keep their real ID as the label.
type AttachmentMetadata = Pick<MessageFile, 'id' | 'type'> & Partial<MessageFile>

export function WorkbenchHistoryFiles({ files }: { files: readonly AttachmentMetadata[] }) {
  const { t } = useTranslation('common')
  if (!files.length) return null
  return (
    <ul className="flex flex-wrap gap-2">
      {files.map((file) => {
        const name = file.filename || file.id
        const href = attachmentHref(file.url)
        const kind =
          file.type === 'image' || file.type === 'audio' || file.type === 'video' ? file.type : null
        const mime = file.mime_type?.trim().toLowerCase()
        const previewable = href && kind && mime && mime.startsWith(`${kind}/`)
        const content = (
          <>
            <span aria-hidden="true">
              <FileTypeIcon type={kind ?? 'document'} />
            </span>
            <span className="min-w-0 break-words">{name}</span>
            {file.size != null && Number.isFinite(file.size) && file.size >= 0 && (
              <span className="shrink-0 text-text-tertiary">{formatFileSize(file.size)}</span>
            )}
          </>
        )
        const className =
          'flex items-center gap-2 rounded-lg border border-divider-subtle px-3 py-2 system-sm-regular text-text-primary'
        return (
          <li key={file.id} className="max-w-full min-w-0">
            {previewable ? (
              <Dialog>
                <DialogTrigger
                  render={
                    <Button
                      variant="secondary"
                      aria-label={name}
                      className="h-auto max-w-full whitespace-normal"
                    />
                  }
                >
                  {content}
                </DialogTrigger>
                <DialogContent className="w-full max-w-3xl">
                  <DialogTitle className="mb-4 pr-8 system-md-semibold break-words">
                    {name}
                  </DialogTitle>
                  <DialogCloseButton aria-label={t(($) => $['operation.close'])} />
                  <WorkbenchHistoryMedia
                    key={href}
                    kind={kind}
                    mime={mime}
                    url={href}
                    name={name}
                  />
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-4 inline-block text-text-accent outline-hidden focus-visible:ring-2 focus-visible:ring-state-accent-solid"
                  >
                    {t(($) => $['operation.openInNewTab'])}
                  </a>
                </DialogContent>
              </Dialog>
            ) : href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className={`${className} outline-hidden focus-visible:ring-2 focus-visible:ring-state-accent-solid`}
              >
                {content}
              </a>
            ) : (
              <span className={className}>{content}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
