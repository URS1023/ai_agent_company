'use client'

import type { EnrollmentView, ProvisioningView } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isEqual } from 'es-toolkit/predicate'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { ActivationSection } from './activation'

export function EnrollmentSection({
  provisioning,
  disabled,
}: {
  provisioning: ProvisioningView
  disabled: boolean
}) {
  const { t } = useTranslation('common')
  const cache = useQueryClient()
  const [pending, setPending] = useState<{ id: string; revision: number } | null>(null)
  const [invalid, setInvalid] = useState(false)
  const endpoint = consoleQuery.business.workflowProvisioning.byProvisioningId.enrollment
  const options = endpoint.get.queryOptions({
    input: { params: { provisioning_id: provisioning.id } },
    queryKey: [
      'enterprise-business',
      provisioning.workspace_id,
      provisioning.actor_id,
      'enrollment',
      provisioning.id,
    ],
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const query = useQuery(options)
  const start = useMutation(endpoint.post.mutationOptions({ retry: false }))
  const advance = useMutation(
    consoleQuery.business.workflowEnrollments.byEnrollmentId.advance.post.mutationOptions({
      retry: false,
    }),
  )
  const view = query.data
  const matches = (value: EnrollmentView) => isEqual(value.provisioning, provisioning)
  const mismatch = invalid || (!!view && !matches(view))
  const pendingUnconfirmed =
    !!pending && (!view || view.id !== pending.id || view.revision <= pending.revision)
  const busy = start.isPending || advance.isPending || query.isFetching
  const unavailable = disabled || mismatch || query.isError || busy || pendingUnconfirmed
  const canAdvance =
    !unavailable && !!view && (view.state === 'pending_verification' || view.state === 'verified')
  const phaseState =
    view?.state === 'rejected'
      ? 'rejected'
      : view?.state === 'uncertain'
        ? 'uncertain'
        : view?.state === 'token_stored'
          ? 'succeeded'
          : view?.state === 'verification_claimed' || view?.state === 'token_claimed'
            ? 'claimed'
            : 'queued'

  function accept(result: EnrollmentView) {
    if (!matches(result) || (view && (result.id !== view.id || result.revision <= view.revision))) {
      setInvalid(true)
      return
    }
    cache.setQueryData(options.queryKey, result)
  }

  return (
    <section
      className="space-y-3 rounded-lg border border-divider-regular p-3"
      aria-label={t(($) => $['enterprise.enrollment.title'])}
    >
      <h4 className="system-sm-semibold">{t(($) => $['enterprise.enrollment.title'])}</h4>
      {query.isPending && (
        <div
          className="h-8 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
          aria-busy="true"
        />
      )}
      {(query.isError || mismatch || pendingUnconfirmed || start.isError || advance.isError) && (
        <p role="alert">{t(($) => $['enterprise.provisioning.review'])}</p>
      )}
      {view && (
        <div aria-live="polite">
          <p>
            {!view.publication
              ? t(($) => $['enterprise.enrollment.verify'])
              : t(($) => $['enterprise.enrollment.credential'])}
          </p>
          <p>{t(($) => $[`enterprise.provisioning.state.${phaseState}`])}</p>
          {view.state === 'token_stored' && (
            <p role="status">{t(($) => $['enterprise.enrollment.saved'])}</p>
          )}
          <Button
            disabled={!canAdvance}
            onClick={() => {
              if (!canAdvance) return
              setPending({ id: view.id, revision: view.revision })
              advance.mutate(
                {
                  params: { enrollment_id: view.id },
                  body: { expected_revision: view.revision },
                  headers: { Origin: window.location.origin },
                },
                {
                  onSuccess: accept,
                  onSettled: () => {
                    void query.refetch()
                  },
                },
              )
            }}
          >
            {t(($) => $['enterprise.provisioning.continue'])}
          </Button>
        </div>
      )}
      {query.isSuccess && view === null && (
        <Button
          disabled={unavailable}
          onClick={() => {
            start.mutate(
              {
                params: { provisioning_id: provisioning.id },
                body: {},
                headers: { Origin: window.location.origin },
              },
              {
                onSuccess: accept,
                onSettled: () => {
                  void query.refetch()
                },
              },
            )
          }}
        >
          {t(($) => $['enterprise.enrollment.start'])}
        </Button>
      )}
      {view?.state === 'token_stored' && !mismatch && (
        <ActivationSection key={view.id} enrollment={view} disabled={unavailable} />
      )}
      <Button
        variant="secondary"
        disabled={busy}
        onClick={() => {
          void query.refetch()
        }}
      >
        {t(($) => $['enterprise.provisioning.refresh'])}
      </Button>
    </section>
  )
}
