'use client'

import type { SourceDraftWritable, SourceView } from '@enterprise/business-contracts/types'
import type { ReactNode } from 'react'
import { Button } from '@langgenius/dify-ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '@langgenius/dify-ui/dialog'
import { Form } from '@langgenius/dify-ui/form'
import { ORPCError } from '@orpc/client'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { Toggle } from './controls'
import { readSourceForm, SourceFormError } from './form-values'
import { SourceFields } from './source-fields'

type SourceAttempt = { body: SourceDraftWritable; key: string; revision: number | null }

export function SourceEditor({
  sourceId,
  scope,
  canEdit,
}: {
  sourceId?: string
  scope: readonly string[]
  canEdit: boolean
}) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [attempt, setAttempt] = useState<SourceAttempt | null>(null)
  const [invalid, setInvalid] = useState<SourceFormError['code'] | null>(null)
  const [responseMismatch, setResponseMismatch] = useState(false)
  const detail = useMutation({
    mutationFn: async () => {
      if (!sourceId) throw new Error('missing_source')
      const source = await client.fetchQuery(
        consoleQuery.business.sources.bySourceId.get.queryOptions({
          input: { params: { source_id: sourceId } },
          queryKey: [...scope, 'sources', 'detail', sourceId],
          staleTime: 0,
          retry: false,
        }),
      )
      if (source.workspace_id !== scope[1] || source.source_id !== sourceId)
        throw new Error('source_scope_mismatch')
      return source
    },
    retry: false,
  })
  const create = useMutation({
    ...consoleQuery.business.sources.post.mutationOptions(),
    retry: false,
    gcTime: 0,
  })
  const update = useMutation({
    ...consoleQuery.business.sources.bySourceId.put.mutationOptions(),
    retry: false,
    gcTime: 0,
  })
  const busy = create.isPending || update.isPending
  const error = sourceId ? update.error : create.error
  const conflict = Boolean(sourceId) && error instanceof ORPCError && error.status === 409
  const uncertain = attempt !== null && Boolean(error)

  function close() {
    if (busy) return
    if (sourceId && conflict) {
      setAttempt(null)
      update.reset()
      detail.reset()
    }
    setOpen(false)
  }
  function submit(data: FormData, snapshot?: SourceView) {
    if (busy || !canEdit || conflict || responseMismatch) return
    let submission = attempt
    if (!submission) {
      try {
        submission = {
          body: readSourceForm(data, snapshot),
          key: crypto.randomUUID(),
          revision: snapshot?.revision ?? null,
        }
        setInvalid(null)
        setAttempt(submission)
      } catch (error) {
        setInvalid(error instanceof SourceFormError ? error.code : 'invalid')
        return
      }
    }
    const onSuccess = (source: SourceView) => {
      if (source.workspace_id !== scope[1] || (sourceId && source.source_id !== sourceId)) {
        setResponseMismatch(true)
        return
      }
      setAttempt(null)
      setOpen(false)
      create.reset()
      update.reset()
      void client.invalidateQueries({ queryKey: [...scope, 'sources'] })
    }
    const onError = (error: Error) => {
      if (error instanceof ORPCError && error.status === 422) setAttempt(null)
    }
    if (sourceId && submission.revision !== null)
      update.mutate(
        {
          params: { source_id: sourceId },
          body: submission.body,
          headers: { Origin: window.location.origin, 'if-match': `"${submission.revision}"` },
        },
        { onSuccess, onError },
      )
    else if (!sourceId)
      create.mutate(
        {
          body: submission.body,
          headers: { Origin: window.location.origin, 'idempotency-key': submission.key },
        },
        { onSuccess, onError },
      )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          close()
          return
        }
        if (busy) return
        setInvalid(null)
        if (!attempt) {
          create.reset()
          update.reset()
          if (sourceId) detail.mutate()
        }
        setOpen(true)
      }}
    >
      {(sourceId || canEdit) && (
        <DialogTrigger render={<Button variant={sourceId ? 'secondary' : 'primary'} />}>
          {t(($) => $[sourceId ? 'enterprise.sources.details' : 'enterprise.sources.create'])}
        </DialogTrigger>
      )}
      <DialogContent className="max-h-[90dvh] w-full max-w-3xl overflow-y-auto">
        <DialogTitle className="text-xl font-semibold text-text-primary">
          {t(($) => $[sourceId ? 'enterprise.sources.details' : 'enterprise.sources.create'])}
        </DialogTitle>
        <DialogDescription className="my-4 system-sm-regular text-text-tertiary">
          {t(($) => $['enterprise.sources.subtitle'])}
        </DialogDescription>
        {sourceId && detail.isPending ? (
          <div
            aria-busy
            className="h-32 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
          />
        ) : sourceId && (detail.isError || !detail.data) ? (
          <BusinessFailure retry={() => detail.mutate()} />
        ) : (
          <SourceForm
            initial={attempt?.body ?? detail.data}
            snapshot={detail.data}
            scope={scope}
            locked={busy || attempt !== null || !canEdit}
            confirmed={attempt !== null}
            onSubmit={submit}
          >
            {invalid && (
              <p role="alert" className="system-sm-regular text-text-destructive">
                {t(($) => $[`enterprise.sources.${invalid}`])}
              </p>
            )}
            {responseMismatch && (
              <p role="alert" className="system-sm-regular text-text-destructive">
                {t(($) => $['enterprise.sources.responseMismatch'])}
              </p>
            )}
            {error && (
              <p role="alert" className="system-sm-regular text-text-destructive">
                {t(
                  ($) =>
                    $[
                      sourceId && conflict
                        ? 'enterprise.sources.editConflict'
                        : uncertain
                          ? sourceId
                            ? 'enterprise.sources.editUncertain'
                            : 'enterprise.sources.createUncertain'
                          : 'enterprise.devices.writeError'
                    ],
                )}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" disabled={busy} onClick={close}>
                {t(($) => $['operation.cancel'])}
              </Button>
              {canEdit && (
                <Button
                  type="submit"
                  variant="primary"
                  loading={busy}
                  disabled={busy || conflict || responseMismatch}
                >
                  {t(($) => $['operation.confirm'])}
                </Button>
              )}
            </div>
          </SourceForm>
        )}
      </DialogContent>
    </Dialog>
  )
}

function SourceForm({
  initial,
  snapshot,
  scope,
  locked,
  confirmed,
  onSubmit,
  children,
}: {
  initial?: SourceView | SourceDraftWritable
  snapshot?: SourceView
  scope: readonly string[]
  locked: boolean
  confirmed: boolean
  onSubmit: (data: FormData, snapshot?: SourceView) => void
  children: ReactNode
}) {
  // Defaults belong to this dialog session, not a changing cache or mutation attempt.
  const [defaults] = useState(initial)
  const [wasConfirmed] = useState(confirmed)
  const { t } = useTranslation('common')
  return (
    <Form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit(new FormData(event.currentTarget), snapshot)
      }}
    >
      <fieldset disabled={locked} className="min-w-0 space-y-5">
        <SourceFields initial={defaults} scope={scope} disabled={locked} />
        <Toggle
          name="read_only_confirmed"
          label={t(($) => $['enterprise.sources.readOnlyConfirmation'])}
          defaultChecked={wasConfirmed}
          disabled={locked}
        />
      </fieldset>
      {children}
    </Form>
  )
}
