'use client'

import type { Binding, JsonObject, RunView } from '@enterprise/business-contracts/types'
import { zJsonObject } from '@enterprise/business-contracts/zod'
import { Button } from '@langgenius/dify-ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '@langgenius/dify-ui/dialog'
import { Field, FieldControl, FieldLabel } from '@langgenius/dify-ui/field'
import { Form } from '@langgenius/dify-ui/form'
import { ORPCError } from '@orpc/client'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { isEqual } from 'es-toolkit/predicate'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'

type QueueAttempt = {
  key: string
  parameters: JsonObject
  binding: Binding
}

function matchesAttempt(run: RunView, attempt: QueueAttempt) {
  return (
    run.device_id === attempt.binding.device_id &&
    run.scenario === attempt.binding.scenario &&
    run.binding_revision === attempt.binding.revision &&
    run.specification_revision === attempt.binding.specification_revision &&
    isEqual(run.parameters, attempt.parameters)
  )
}

export function QueueRun({
  binding,
  scope,
  disabled = false,
}: {
  binding?: Binding
  scope: readonly string[]
  disabled?: boolean
}) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [initialParameters, setInitialParameters] = useState('{}')
  const [invalid, setInvalid] = useState(false)
  const [attempt, setAttempt] = useState<QueueAttempt | null>(null)
  const [recoveredRun, setRecoveredRun] = useState<RunView | null>(null)
  const queue = useMutation(
    consoleQuery.business.devices.byDeviceId.bindings.byScenario.runs.post.mutationOptions(),
  )
  const lookup = useMutation({
    mutationFn: (submission: QueueAttempt) =>
      client.fetchQuery(
        consoleQuery.business.runRequests.lookup.get.queryOptions({
          input: { query: { request_key: submission.key } },
          queryKey: [...scope, 'run-request', submission.key],
          staleTime: 0,
          retry: false,
        }),
      ),
    retry: false,
  })
  const busy = queue.isPending || lookup.isPending
  const unavailable = disabled || !binding
  const mismatched =
    attempt !== null && lookup.data !== undefined && !matchesAttempt(lookup.data, attempt)

  function refreshScenario(target: Pick<Binding, 'device_id' | 'scenario'>) {
    // Queueing does not change device definitions or account access; avoid unmounting other attempts.
    for (const resource of ['binding', 'runs']) {
      void client.invalidateQueries({
        queryKey: [...scope, resource, target.device_id, target.scenario],
      })
    }
  }

  function checkRun() {
    if (!attempt || busy || unavailable) return
    queue.reset()
    lookup.mutate(attempt, {
      onSuccess: (run, submission) => {
        if (!matchesAttempt(run, submission)) return
        setRecoveredRun(run)
        setAttempt(null)
        refreshScenario(submission.binding)
      },
    })
  }
  const currentBinding = attempt?.binding ?? binding
  if (!currentBinding) return null
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy || (next && unavailable)) return
        if (next) {
          setInitialParameters(attempt ? JSON.stringify(attempt.parameters) : '{}')
          setRecoveredRun(null)
          if (!attempt) lookup.reset()
        }
        setOpen(next)
      }}
    >
      <DialogTrigger disabled={unavailable} render={<Button variant="primary" />}>
        {t(($) => $['enterprise.devices.enqueue'])}
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="text-xl font-semibold text-text-primary">
          {t(($) => $['enterprise.devices.enqueue'])}
        </DialogTitle>
        <DialogDescription className="my-4 system-sm-regular text-text-secondary">
          {t(($) => $['enterprise.devices.runHint'])}
        </DialogDescription>
        <p className="mb-5 font-mono text-xs text-text-tertiary">
          {t(($) => $['enterprise.devices.revision'])}:{' '}
          {recoveredRun?.binding_revision ?? currentBinding.revision} ·{' '}
          {recoveredRun?.specification_revision ?? currentBinding.specification_revision}
        </p>
        {unavailable && (
          <p role="alert" className="mb-4 system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.devices.loadError'])}
          </p>
        )}
        {recoveredRun ? (
          <div className="space-y-4">
            <div
              role="status"
              className="space-y-3 rounded-xl border border-divider-subtle bg-background-section p-4"
            >
              <p className="system-sm-medium text-text-primary">
                {t(($) => $['enterprise.devices.runRecovered'])}
              </p>
              <p className="font-mono text-xs break-all text-text-secondary">{recoveredRun.id}</p>
              <p className="system-sm-regular text-text-secondary">
                {t(($) => $[`enterprise.devices.state.${recoveredRun.status}`])}
              </p>
            </div>
            <div className="flex justify-end">
              <Button type="button" variant="primary" onClick={() => setOpen(false)}>
                {t(($) => $['operation.close'])}
              </Button>
            </div>
          </div>
        ) : (
          <Form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              if (busy || unavailable || mismatched || !binding) return
              let submission = attempt
              if (!submission) {
                try {
                  const parameters = zJsonObject.parse(
                    JSON.parse(String(new FormData(event.currentTarget).get('parameters'))),
                  )
                  submission = { key: crypto.randomUUID(), parameters, binding }
                  setAttempt(submission)
                  setInvalid(false)
                } catch {
                  setInvalid(true)
                  return
                }
              }
              lookup.reset()
              queue.mutate(
                {
                  params: {
                    device_id: submission.binding.device_id,
                    scenario: submission.binding.scenario,
                  },
                  headers: { Origin: window.location.origin, 'idempotency-key': submission.key },
                  body: {
                    expected_binding_revision: submission.binding.revision,
                    parameters: submission.parameters,
                  },
                },
                {
                  onError: (error) => {
                    // Validation rejected the request before enqueue; uncertain responses retain the exact attempt.
                    if (error instanceof ORPCError && error.status === 422) setAttempt(null)
                  },
                  onSuccess: (_run, command) => {
                    refreshScenario(command.params)
                    setOpen(false)
                    setAttempt(null)
                    queue.reset()
                  },
                },
              )
            }}
          >
            <Field name="parameters">
              <FieldLabel>{t(($) => $['enterprise.devices.parameters'])}</FieldLabel>
              <FieldControl
                defaultValue={initialParameters}
                maxLength={16384}
                disabled={busy || unavailable || attempt !== null}
                className="font-mono"
              />
            </Field>
            {invalid && (
              <p role="alert" className="text-text-destructive">
                {t(($) => $['enterprise.devices.invalidParameters'])}
              </p>
            )}
            {queue.isError && (
              <p role="alert" className="system-sm-regular text-text-destructive">
                {t(
                  ($) =>
                    $[
                      queue.error instanceof ORPCError && queue.error.status === 422
                        ? 'enterprise.devices.writeError'
                        : 'enterprise.devices.runUncertainHint'
                    ],
                )}
              </p>
            )}
            {lookup.isError && (
              <p role="alert" className="system-sm-regular text-text-secondary">
                {t(
                  ($) =>
                    $[
                      lookup.error instanceof ORPCError && lookup.error.status === 404
                        ? 'enterprise.devices.runNotFound'
                        : 'enterprise.devices.runLookupFailed'
                    ],
                )}
              </p>
            )}
            {mismatched && (
              <p role="alert" className="system-sm-regular text-text-destructive">
                {t(($) => $['enterprise.devices.runMismatch'])}
              </p>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              {attempt && (
                <Button
                  type="button"
                  variant="secondary"
                  loading={lookup.isPending}
                  disabled={busy || unavailable}
                  onClick={checkRun}
                >
                  {t(($) => $['enterprise.devices.checkRun'])}
                </Button>
              )}
              <Button
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => setOpen(false)}
              >
                {t(($) => $['operation.cancel'])}
              </Button>
              <Button
                type="submit"
                variant="primary"
                loading={queue.isPending}
                disabled={busy || unavailable || mismatched}
              >
                {t(($) => $['operation.confirm'])}
              </Button>
            </div>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  )
}
