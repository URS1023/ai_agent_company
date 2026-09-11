'use client'

import type { ActivationView, EnrollmentView } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@langgenius/dify-ui/select'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isEqual } from 'es-toolkit/predicate'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'

export function ActivationSection({
  enrollment,
  disabled,
}: {
  enrollment: EnrollmentView
  disabled: boolean
}) {
  const { t } = useTranslation('common')
  const cache = useQueryClient()
  const [selection, setSelection] = useState<string | null>(null)
  const [pending, setPending] = useState<'start' | 'revoke' | null>(null)
  const [invalid, setInvalid] = useState(false)
  const scope = [
    'enterprise-business',
    enrollment.provisioning.workspace_id,
    enrollment.provisioning.actor_id,
    'activation',
    enrollment.id,
  ]
  const endpoint = consoleQuery.business.workflowEnrollments.byEnrollmentId
  const options = endpoint.activation.get.queryOptions({
    input: { params: { enrollment_id: enrollment.id } },
    queryKey: scope,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  const query = useQuery(options)
  const specifications = useQuery(
    endpoint.activationSpecifications.get.queryOptions({
      input: { params: { enrollment_id: enrollment.id } },
      queryKey: [...scope, 'specifications'],
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    }),
  )
  const start = useMutation(endpoint.activation.post.mutationOptions({ retry: false }))
  const revoke = useMutation(
    consoleQuery.business.workflowActivations.byActivationId.revoke.post.mutationOptions({
      retry: false,
    }),
  )
  const view = query.data
  const matches = (result: ActivationView) => isEqual(result.enrollment, enrollment)
  const mismatch = invalid || (!!view && !matches(view))
  const unconfirmed =
    pending === 'start' ? !view : pending === 'revoke' ? !view || view.active : false
  const blocked =
    disabled ||
    mismatch ||
    query.isError ||
    query.isPending ||
    query.isFetching ||
    start.isPending ||
    revoke.isPending ||
    unconfirmed
  const canStart =
    !blocked &&
    query.data === null &&
    enrollment.state === 'token_stored' &&
    !specifications.isError &&
    !specifications.isFetching &&
    !!selection &&
    specifications.data?.items.includes(selection)

  function accept(result: ActivationView, kind: 'start' | 'revoke') {
    if (
      !matches(result) ||
      (kind === 'start' &&
        (result.binding.specification_revision !== selection ||
          !result.active ||
          result.revision !== 1)) ||
      (kind === 'revoke' &&
        (!view ||
          result.id !== view.id ||
          result.active ||
          result.revision !== view.revision + 1 ||
          !isEqual(result.binding, view.binding)))
    ) {
      setInvalid(true)
      return
    }
    cache.setQueryData(options.queryKey, result)
    setPending(null)
  }

  return (
    <section
      className="space-y-3 rounded-lg border border-divider-regular p-3"
      aria-label={t(($) => $['enterprise.activation.title'])}
    >
      <h4 className="system-sm-semibold">{t(($) => $['enterprise.activation.title'])}</h4>
      {query.isPending && (
        <div
          className="h-8 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
          aria-busy="true"
        />
      )}
      {(mismatch ||
        query.isError ||
        specifications.isError ||
        start.isError ||
        revoke.isError ||
        unconfirmed) && <p role="alert">{t(($) => $['enterprise.provisioning.review'])}</p>}
      {view && !mismatch && (
        <div aria-live="polite">
          <p>
            {view.active
              ? t(($) => $['enterprise.activation.active'])
              : t(($) => $['enterprise.activation.revoked'])}
          </p>
          <p>{view.binding.specification_revision}</p>
          {view.active && (
            <Button
              disabled={blocked}
              onClick={() => {
                if (blocked) return
                setPending('revoke')
                revoke.mutate(
                  {
                    params: { activation_id: view.id },
                    body: { expected_revision: view.revision },
                    headers: { Origin: window.location.origin },
                  },
                  {
                    onSuccess: (result) => accept(result, 'revoke'),
                    onSettled: () => {
                      void query.refetch()
                    },
                  },
                )
              }}
            >
              {t(($) => $['enterprise.activation.revoke'])}
            </Button>
          )}
        </div>
      )}
      {query.isSuccess && view === null && (
        <>
          <Select<string>
            value={selection}
            disabled={blocked || specifications.isFetching}
            onValueChange={setSelection}
          >
            <SelectTrigger aria-label={t(($) => $['enterprise.activation.specification'])}>
              <SelectValue placeholder={t(($) => $['enterprise.activation.specification'])}>
                {selection}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {specifications.data?.items.map((item) => (
                <SelectItem key={item} value={item}>
                  {item}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {specifications.data?.items.length === 0 && (
            <p>{t(($) => $['enterprise.provisioning.unavailable'])}</p>
          )}
          <Button
            disabled={!canStart}
            onClick={() => {
              if (!canStart || !selection) return
              setPending('start')
              start.mutate(
                {
                  params: { enrollment_id: enrollment.id },
                  body: { specification_revision: selection },
                  headers: { Origin: window.location.origin },
                },
                {
                  onSuccess: (result) => accept(result, 'start'),
                  onSettled: () => {
                    void query.refetch()
                  },
                },
              )
            }}
          >
            {t(($) => $['enterprise.activation.activate'])}
          </Button>
        </>
      )}
      <Button
        disabled={query.isFetching || start.isPending || revoke.isPending}
        onClick={() => {
          void query.refetch()
          void specifications.refetch()
        }}
      >
        {t(($) => $['enterprise.provisioning.refresh'])}
      </Button>
    </section>
  )
}
