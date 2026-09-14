'use client'

import type { OfficeCreateRequest } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleClient } from '@/service/client'
import { fetchOfficeDocument } from '@/service/enterprise-business/office-download'
import { downloadBlob } from '@/utils/download'
import { answerDocument } from './answer-document'

type Props = { answer: string; actor: string; workspace: string; messageId: string }

export function SaveAnswerDocument(props: Props) {
  return (
    <SaveAnswer
      key={JSON.stringify([props.actor, props.workspace, props.messageId, props.answer])}
      {...props}
    />
  )
}

function SaveAnswer({ answer, actor, workspace, messageId }: Props) {
  const { t } = useTranslation('common')
  const attemptRef = useRef<OfficeCreateRequest | null>(null)
  const pendingRef = useRef(false)
  const downloadRef = useRef<AbortController | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadFailed, setDownloadFailed] = useState(false)
  useEffect(() => () => downloadRef.current?.abort(), [])
  const [invalid, setInvalid] = useState(false)
  const save = useMutation({
    retry: false,
    gcTime: 0,
    mutationFn: async (body: OfficeCreateRequest) => {
      const result = await consoleClient.business.office.files.post({
        body,
        headers: { Origin: window.location.origin },
      })
      const unit = result.units[0]
      const content = unit?.content[0]
      if (
        result.file_id !== body.file_id ||
        result.revision !== '1' ||
        result.kind !== 'document' ||
        result.template_id !== body.template_id ||
        result.template_revision !== body.template_revision ||
        result.source_snapshot_ids.length !== 0 ||
        result.units.length !== 1 ||
        unit?.unit_id !== body.units[0]?.unit_id ||
        unit?.kind !== 'paragraph' ||
        unit.content.length !== 1 ||
        !content ||
        !('text' in content) ||
        content.text !== answer
      )
        throw new Error('Saved document does not match the requested answer')
      return result
    },
  })
  async function submit() {
    if (pendingRef.current || save.isSuccess || !actor || !workspace || !messageId) return
    pendingRef.current = true
    try {
      attemptRef.current ??= answerDocument(answer, { actor, workspace, messageId })
      await save.mutateAsync(attemptRef.current)
    } catch {
      setInvalid(true)
    } finally {
      pendingRef.current = false
    }
  }
  async function download() {
    if (!save.isSuccess || !save.data || downloadRef.current) return
    const controller = new AbortController()
    downloadRef.current = controller
    setDownloading(true)
    setDownloadFailed(false)
    try {
      const result = await fetchOfficeDocument(
        {
          path: { file_id: save.data.file_id },
          query: { expected_revision: save.data.revision },
        },
        { signal: controller.signal },
      )
      controller.signal.throwIfAborted()
      downloadBlob({ data: result.blob, fileName: result.filename })
    } catch {
      if (!controller.signal.aborted) setDownloadFailed(true)
    } finally {
      if (!controller.signal.aborted) {
        downloadRef.current = null
        setDownloading(false)
      }
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        disabled={
          !actor || !workspace || !messageId || !answer.trim() || save.isPending || save.isSuccess
        }
        onClick={() => void submit()}
      >
        {save.isError ? t(($) => $['operation.retry']) : `${t(($) => $['operation.save'])} DOCX`}
      </Button>
      {save.isSuccess ? (
        <>
          <span role="status">{t(($) => $['api.saved'])}</span>
          <Button variant="secondary" disabled={downloading} onClick={() => void download()}>
            {t(($) => $['operation.download'])}
          </Button>
          {downloadFailed && <span role="alert">{t(($) => $.error)}</span>}
        </>
      ) : (
        invalid && <span role="alert">{t(($) => $.error)}</span>
      )}
    </div>
  )
}
