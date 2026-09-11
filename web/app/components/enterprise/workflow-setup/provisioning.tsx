'use client'

import type {
  PluginProfileChoice,
  ProvisioningStartRequest,
  ProvisioningView,
  SetupView,
} from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@langgenius/dify-ui/select'
import { skipToken, useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleClient, consoleQuery } from '@/service/client'
import { EnrollmentSection } from './enrollment'

export type ProvisioningSafety = {
  attempt?: { key: string; body: ProvisioningStartRequest }
  blocked?: boolean
  mismatched?: boolean
  pendingAdvance?: { id: string; revision: number; configRef: string; configRevision: number }
  operationId?: string
}
const phases = ['read_draft', 'prepare_credential', 'bind_credential', 'publish'] as const

export function ProvisioningSection({
  setup,
  scope,
  disabled,
  safety,
  onSafetyChange,
}: {
  setup: SetupView
  scope: readonly string[]
  disabled: boolean
  safety: ProvisioningSafety
  onSafetyChange: (value: ProvisioningSafety) => void
}) {
  const { t } = useTranslation('common')
  const [selection, setSelection] = useState<PluginProfileChoice | null>(null)
  const [localSafety, setLocalSafety] = useState(safety)
  const currentSafety = { ...safety, ...localSafety }
  function remember(value: ProvisioningSafety) {
    setLocalSafety(value)
    onSafetyChange(value)
  }
  const endpoint = consoleQuery.business.workflowSetups.bySetupId
  const params = { setup_id: setup.id }
  const profiles = useQuery(
    endpoint.provisioningProfiles.get.queryOptions({
      input: { params },
      queryKey: [...scope, 'provisioning-profiles', setup.id],
      retry: false,
    }),
  )
  const history = useInfiniteQuery({
    queryKey: [...scope, 'provisioning-history', params],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      consoleClient.business.workflowSetups.bySetupId.provisioning.get({
        params,
        query: { offset: pageParam, limit: 20 },
      }),
    getNextPageParam: (page) =>
      page.offset + page.limit < page.total ? page.offset + page.limit : undefined,
    retry: false,
  })
  const detail = useQuery(
    consoleQuery.business.workflowProvisioning.byProvisioningId.get.queryOptions({
      input: currentSafety.operationId
        ? { params: { provisioning_id: currentSafety.operationId } }
        : skipToken,
      queryKey: [...scope, 'provisioning', currentSafety.operationId],
      retry: false,
    }),
  )
  const start = useMutation(endpoint.provisioning.post.mutationOptions({ retry: false }))
  const advance = useMutation(
    consoleQuery.business.workflowProvisioning.byProvisioningId.advance.post.mutationOptions({
      retry: false,
    }),
  )
  function matches(view: ProvisioningView) {
    return (
      view.workspace_id === scope[1] &&
      view.actor_id === scope[2] &&
      view.setup_id === setup.id &&
      view.setup_revision === setup.revision &&
      view.app_id === setup.app_id &&
      view.device_id === setup.device_id &&
      view.scenario === setup.scenario &&
      view.source_id === setup.source_id &&
      view.source_revision === setup.source_revision &&
      view.read_id === setup.read_id &&
      view.read_revision === setup.read_revision &&
      view.expected_source_revision === setup.expected_source_revision &&
      (view.expected_binding_revision ?? null) === (setup.expected_binding_revision ?? null)
    )
  }
  const records = history.data?.pages.flatMap((page) => page.items) ?? []
  const mismatch =
    records.some((view) => !matches(view)) ||
    !!(detail.data && (!matches(detail.data) || detail.data.id !== currentSafety.operationId))
  const failed = history.isError || profiles.isError || detail.isError || mismatch
  const view = !mismatch ? detail.data : undefined
  const selected = profiles.data?.find(
    (profile) =>
      profile.config_ref === selection?.config_ref &&
      profile.config_revision === selection.config_revision,
  )
  const busy = start.isPending || advance.isPending
  const canStart =
    !disabled &&
    !busy &&
    !failed &&
    !currentSafety.attempt &&
    !currentSafety.blocked &&
    !!selected &&
    history.isSuccess &&
    !history.isFetching &&
    !history.hasNextPage &&
    records.length === 0 &&
    !profiles.isFetching
  const last = view?.phases?.at(-1)
  const pending = currentSafety.pendingAdvance
  const recovered =
    !!view &&
    !failed &&
    !detail.isFetching &&
    !currentSafety.mismatched &&
    (pending
      ? view.id === pending.id &&
        view.revision > pending.revision &&
        view.config_ref === pending.configRef &&
        view.config_revision === pending.configRevision &&
        !!last &&
        ['succeeded', 'rejected', 'uncertain'].includes(last.state)
      : !!currentSafety.operationId)
  const awaitingReview = currentSafety.blocked && !recovered
  const canAdvance =
    !disabled &&
    history.isSuccess &&
    !history.isFetching &&
    profiles.isSuccess &&
    !profiles.isFetching &&
    !busy &&
    !failed &&
    !awaitingReview &&
    !detail.isFetching &&
    view?.state === 'in_progress' &&
    (!last || last.state === 'succeeded' || last.state === 'queued')
  function refresh() {
    void history.refetch()
    void profiles.refetch()
    if (currentSafety.operationId) void detail.refetch()
  }
  return (
    <section
      className="space-y-3 rounded-xl border border-divider-subtle p-4"
      aria-label={t(($) => $['enterprise.provisioning.title'])}
    >
      <h3 className="system-sm-semibold text-text-primary">
        {t(($) => $['enterprise.provisioning.title'])}
      </h3>
      <p className="system-xs-regular text-text-secondary">
        {t(($) => $['enterprise.provisioning.remaining'])}
      </p>
      {history.isPending || profiles.isPending ? (
        <div
          aria-busy="true"
          className="h-16 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
        />
      ) : (
        <>
          {failed && <p role="alert">{t(($) => $['enterprise.provisioning.unavailable'])}</p>}
          <Select<string>
            value={
              selected ? JSON.stringify([selected.config_ref, selected.config_revision]) : null
            }
            disabled={busy || !!currentSafety.attempt || !!currentSafety.blocked}
            onValueChange={(value) =>
              setSelection(
                profiles.data?.find(
                  (profile) =>
                    JSON.stringify([profile.config_ref, profile.config_revision]) === value,
                ) ?? null,
              )
            }
          >
            <SelectTrigger aria-label={t(($) => $['enterprise.provisioning.profile'])}>
              <SelectValue placeholder={t(($) => $['enterprise.provisioning.profile'])}>
                {selected && `${selected.display_name} · ${selected.config_revision}`}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {profiles.data?.map((profile) => (
                <SelectItem
                  key={JSON.stringify([profile.config_ref, profile.config_revision])}
                  value={JSON.stringify([profile.config_ref, profile.config_revision])}
                >
                  {profile.display_name} · {profile.config_revision}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {profiles.data?.length === 0 && (
            <p>{t(($) => $['enterprise.provisioning.unavailable'])}</p>
          )}
          <Button
            disabled={!canStart}
            onClick={() => {
              if (!canStart || !selected) return
              const attempt = {
                key: crypto.randomUUID(),
                body: {
                  config_ref: selected.config_ref,
                  config_revision: selected.config_revision,
                },
              }
              remember({ attempt })
              start.mutate(
                {
                  params,
                  body: attempt.body,
                  headers: { Origin: window.location.origin, 'idempotency-key': attempt.key },
                },
                {
                  onSuccess: (result) => {
                    if (
                      !matches(result) ||
                      result.config_ref !== attempt.body.config_ref ||
                      result.config_revision !== attempt.body.config_revision
                    ) {
                      remember({ attempt, blocked: true, mismatched: true })
                      return
                    }
                    remember({ attempt, operationId: result.id })
                    refresh()
                  },
                  onError: () => {
                    remember({ attempt, blocked: true })
                    refresh()
                  },
                },
              )
            }}
          >
            {t(($) => $['enterprise.provisioning.start'])}
          </Button>
        </>
      )}
      {(awaitingReview || (!recovered && (start.isError || advance.isError))) && (
        <p role="alert">{t(($) => $['enterprise.provisioning.review'])}</p>
      )}
      <Button variant="secondary" disabled={busy || history.isFetching} onClick={refresh}>
        {t(($) => $['enterprise.provisioning.refresh'])}
      </Button>
      <ol className="space-y-2">
        {!mismatch &&
          records.map((record) => (
            <li key={record.id} className="flex items-center justify-between gap-2">
              <span className="system-xs-regular text-text-secondary">
                {record.config_ref} · {record.config_revision} ·{' '}
                {new Date(record.created_at).toLocaleString()}
              </span>
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => remember({ ...currentSafety, operationId: record.id })}
              >
                {t(($) => $['enterprise.provisioning.inspect'])}
              </Button>
            </li>
          ))}
      </ol>
      {history.hasNextPage && (
        <Button
          variant="secondary"
          disabled={history.isFetching}
          onClick={() => {
            void history.fetchNextPage()
          }}
        >
          {t(($) => $['enterprise.setup.moreHistory'])}
        </Button>
      )}
      {view && (
        <div className="space-y-3" aria-live="polite">
          <ol className="space-y-2">
            {phases.map((phase, index) => (
              <li
                key={phase}
                className="flex justify-between gap-2 rounded-lg bg-background-section p-2"
              >
                <span>{t(($) => $[`enterprise.provisioning.phase.${phase}`])}</span>
                <span>
                  {t(
                    ($) =>
                      $[`enterprise.provisioning.state.${view.phases?.[index]?.state ?? 'queued'}`],
                  )}
                </span>
              </li>
            ))}
          </ol>
          {view.state === 'published_pending_enrollment' && (
            <p role="status">{t(($) => $['enterprise.provisioning.published'])}</p>
          )}
          {view.state === 'published_pending_enrollment' &&
            view.phases?.length === 4 &&
            view.phases.every((phase) => phase.state === 'succeeded') && (
              <EnrollmentSection
                key={view.id}
                provisioning={view}
                disabled={disabled || mismatch || awaitingReview || busy}
              />
            )}
          <Button
            disabled={!canAdvance}
            onClick={() => {
              if (!canAdvance) return
              remember({
                ...currentSafety,
                blocked: true,
                pendingAdvance: {
                  id: view.id,
                  revision: view.revision,
                  configRef: view.config_ref,
                  configRevision: view.config_revision,
                },
              })
              advance.mutate(
                {
                  params: { provisioning_id: view.id },
                  body: { expected_revision: view.revision },
                  headers: { Origin: window.location.origin },
                },
                {
                  onSuccess: (result) => {
                    if (
                      !matches(result) ||
                      result.id !== view.id ||
                      result.config_ref !== view.config_ref ||
                      result.config_revision !== view.config_revision ||
                      result.revision <= view.revision
                    ) {
                      remember({ ...currentSafety, blocked: true, mismatched: true })
                      refresh()
                      return
                    }
                    remember({ ...currentSafety, blocked: false, pendingAdvance: undefined })
                    refresh()
                  },
                  onError: refresh,
                },
              )
            }}
          >
            {t(($) => $['enterprise.provisioning.continue'])}
          </Button>
        </div>
      )}
    </section>
  )
}
