'use client'

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export type HistoryMediaKind = 'image' | 'audio' | 'video'

/** Mounted only inside an opened preview; closing unmounts playback and error state. */
export function WorkbenchHistoryMedia({
  kind,
  mime,
  url,
  name,
}: {
  kind: HistoryMediaKind
  mime: string
  url: string
  name: string
}) {
  const { t } = useTranslation('common')
  const [failed, setFailed] = useState(false)
  if (failed) return <p role="alert">{t(($) => $['fileUploader.uploadFromComputerReadError'])}</p>
  const onError = () => setFailed(true)
  if (kind === 'image') {
    // Signed local/remote attachments are not public Next image-optimizer assets.
    return (
      <img
        src={url}
        alt={name}
        aria-label={name}
        onError={onError}
        referrerPolicy="no-referrer"
        className="max-h-full max-w-full object-contain"
      />
    )
  }
  const Media = kind === 'audio' ? 'audio' : 'video'
  return (
    <Media
      aria-label={name}
      controls
      preload="metadata"
      onError={onError}
      className="max-h-full w-full"
    >
      <source src={url} type={mime} onError={onError} />
    </Media>
  )
}
