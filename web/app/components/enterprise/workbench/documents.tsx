'use client'

import type { OfficeFileSummary } from '@enterprise/business-contracts/types'
import { zOfficeDirectoryPage } from '@enterprise/business-contracts/zod'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { fetchOfficeDocument } from '@/service/enterprise-business/office-download'
import { downloadBlob } from '@/utils/download'
import { ResourceSection } from '../resource-section'

type Scope = { actor: string; workspace: string }

export function OfficeDocuments(scope: Scope) {
  return <Documents key={JSON.stringify(scope)} {...scope} />
}

function Documents({ actor, workspace }: Scope) {
  const { t } = useTranslation('common')
  const [offsets, setOffsets] = useState<number[]>([0])
  const offset = offsets.at(-1) ?? 0
  const [downloading, setDownloading] = useState<string | null>(null)
  const [downloadFailed, setDownloadFailed] = useState(false)
  const activeDownloadRef = useRef<AbortController | null>(null)
  useEffect(() => () => activeDownloadRef.current?.abort(), [])

  const page = useQuery(
    consoleQuery.business.office.files.get.queryOptions({
      input: { query: { offset } },
      queryKey: ['enterprise-office-directory', workspace, actor, offset],
      enabled: !!actor && !!workspace,
      retry: false,
      gcTime: 0,
      select: (response) => {
        const value = zOfficeDirectoryPage.parse(response)
        const next = value.next_offset
        if (
          value.items.length > 50 ||
          new Set(value.items.map((item) => item.file_id)).size !== value.items.length ||
          (next !== null &&
            (!Number.isInteger(next) || next <= offset || next > offset + 50 || next > 2147483647))
        )
          throw new Error('Office directory unavailable')
        return value
      },
    }),
  )

  async function download(file: OfficeFileSummary) {
    if (activeDownloadRef.current || file.kind !== 'document') return
    const controller = new AbortController()
    activeDownloadRef.current = controller
    setDownloading(file.file_id)
    setDownloadFailed(false)
    try {
      const result = await fetchOfficeDocument(
        {
          path: { file_id: file.file_id },
          query: { expected_revision: file.revision },
        },
        { signal: controller.signal },
      )
      controller.signal.throwIfAborted()
      downloadBlob({ data: result.blob, fileName: result.filename })
    } catch {
      if (!controller.signal.aborted) setDownloadFailed(true)
    } finally {
      if (!controller.signal.aborted) {
        activeDownloadRef.current = null
        setDownloading(null)
      }
    }
  }

  return (
    <div className="space-y-4">
      <ResourceSection
        title={t(($) => $['datasetMenus.documents'])}
        pending={page.isPending}
        failed={page.isError}
        empty={page.data?.items.length === 0}
        retry={() => {
          void page.refetch()
        }}
      >
        <ul className="space-y-3">
          {page.data?.items.map((file) => (
            <li
              key={file.file_id}
              className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-divider-subtle bg-background-section p-4"
            >
              <div className="min-w-0 space-y-1">
                <p className="font-mono text-sm break-all text-text-primary">{file.file_id}</p>
                <p className="system-sm-regular text-text-secondary">{file.template_id}</p>
                <p className="system-xs-regular text-text-tertiary">
                  {t(($) => $['about.version'], { version: file.revision })}
                </p>
              </div>
              {file.kind === 'document' && (
                <Button
                  variant="secondary"
                  disabled={downloading !== null}
                  onClick={() => {
                    void download(file)
                  }}
                >
                  {downloading === file.file_id
                    ? t(($) => $['operation.downloading'])
                    : t(($) => $['operation.download'])}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </ResourceSection>
      {downloadFailed && (
        <p role="alert" className="system-sm-regular text-text-secondary">
          {t(($) => $['operation.downloadFailed'])}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          variant="secondary"
          disabled={offsets.length === 1 || page.isFetching}
          onClick={() => setOffsets((previous) => previous.slice(0, -1))}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <Button
          variant="secondary"
          disabled={page.isError || page.isFetching || page.data?.next_offset == null}
          onClick={() => {
            const next = page.data?.next_offset
            if (next != null && !page.isError && !page.isFetching)
              setOffsets((previous) => [...previous, next])
          }}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </div>
    </div>
  )
}
