'use client'

import type {
  Scenario,
  SetupRequest,
  SetupView,
  SourceView,
} from '@enterprise/business-contracts/types'
import type { ProvisioningSafety } from './provisioning'
import { Button } from '@langgenius/dify-ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '@langgenius/dify-ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@langgenius/dify-ui/select'
import { ORPCError } from '@orpc/client'
import { skipToken, useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Link from '@/next/link'
import { consoleClient, consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { ProvisioningSection } from './provisioning'

type Attempt = { key: string; body: SetupRequest; source: SourceView }

type Props = {
  deviceId: string
  scenario: Scenario
  scope: readonly string[]
  expectedBindingRevision: number | null
  disabled: boolean
}
export function WorkflowSetup(props: Props) {
  return (
    <SetupController
      key={JSON.stringify([...props.scope, props.deviceId, props.scenario])}
      {...props}
    />
  )
}
function SetupController({ deviceId, scenario, scope, expectedBindingRevision, disabled }: Props) {
  const { t } = useTranslation('common')
  const [provisioningSafety, setProvisioningSafety] = useState<Record<string, ProvisioningSafety>>(
    {},
  )
  const [mismatched, setMismatched] = useState(false)
  const [open, setOpen] = useState(false)
  const [sourcePage, setSourcePage] = useState(0)
  const [selected, setSelected] = useState<SourceView | null>(null)
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [current, setCurrent] = useState<SetupView | null>(null)
  const [inspectId, setInspectId] = useState<string | null>(null)
  const params = { device_id: deviceId, scenario }
  const endpoint = consoleQuery.business.devices.byDeviceId.bindings.byScenario.workflowSetups
  const history = useInfiniteQuery({
    queryKey: [...scope, 'workflow-setups', params],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      consoleClient.business.devices.byDeviceId.bindings.byScenario.workflowSetups.get({
        params,
        query: { offset: pageParam, limit: 20 },
      }),
    getNextPageParam: (last) =>
      last.offset + last.limit < last.total ? last.offset + last.limit : undefined,
    enabled: open,
    retry: false,
  })
  const sources = useQuery(
    consoleQuery.business.sources.get.queryOptions({
      input: { query: { offset: sourcePage * 20, limit: 20 } },
      queryKey: [...scope, 'sources', 'setup', sourcePage],
      enabled: open,
      retry: false,
    }),
  )
  const detail = useQuery(
    consoleQuery.business.workflowSetups.bySetupId.get.queryOptions({
      input: inspectId ? { params: { setup_id: inspectId } } : skipToken,
      queryKey: [...scope, 'workflow-setup', inspectId],
      enabled: open && inspectId !== null,
      retry: false,
      refetchInterval: (query) =>
        query.state.data && ['queued', 'importing'].includes(query.state.data.state) ? 4000 : false,
    }),
  )
  function matches(record: SetupView) {
    return (
      record.workspace_id === scope[1] &&
      record.device_id === deviceId &&
      record.scenario === scenario
    )
  }
  const create = useMutation(endpoint.post.mutationOptions({ retry: false }))
  const records = history.data?.pages.flatMap((page) => page.items) ?? []
  const invalidHistory = records.some((record) => !matches(record))
  const invalidSources =
    sources.data?.items.some((source) => source.workspace_id !== scope[1]) ?? false
  const eligible = invalidSources
    ? []
    : (sources.data?.items.filter(
        (source) => source.enabled !== false && source.device_ids.includes(deviceId),
      ) ?? [])
  const selectedSource = eligible.find(
    (source) =>
      source.source_id === selected?.source_id &&
      source.revision === selected.revision &&
      source.source_revision === selected.source_revision &&
      source.read_id === selected.read_id &&
      source.read_revision === selected.read_revision,
  )
  const displayed =
    detail.data && matches(detail.data) && detail.data.id === inspectId ? detail.data : current
  const unresolved = records.some((record) => !['draft_ready', 'failed'].includes(record.state))
  const canCreate =
    !disabled &&
    !create.isPending &&
    !attempt &&
    !unresolved &&
    !history.isFetching &&
    !history.isError &&
    history.isSuccess &&
    !history.hasNextPage &&
    !invalidHistory &&
    !sources.isError &&
    !sources.isFetching &&
    !invalidSources &&
    selectedSource !== undefined
  function submit(submission: Attempt) {
    if (disabled || mismatched || create.isPending) return
    setAttempt(submission)
    create.mutate(
      {
        params,
        body: submission.body,
        headers: { Origin: window.location.origin, 'idempotency-key': submission.key },
      },
      {
        onSuccess: (record) => {
          if (
            !matches(record) ||
            record.source_id !== submission.body.source_id ||
            record.expected_source_revision !== submission.body.expected_source_revision ||
            record.expected_binding_revision !== submission.body.expected_binding_revision ||
            record.source_revision !== submission.source.source_revision ||
            record.read_id !== submission.source.read_id ||
            record.read_revision !== submission.source.read_revision
          ) {
            setMismatched(true)
            return
          }
          setCurrent(record)
          setInspectId(record.id)
          if (['draft_ready', 'failed'].includes(record.state)) setAttempt(null)
          void history.refetch()
        },
        onError: () => {
          void history.refetch()
        },
      },
    )
  }
  function renderRecord(record: SetupView) {
    return (
      <div className="space-y-2 rounded-lg border border-divider-subtle p-3">
        <div className="flex items-center justify-between gap-3">
          <span className="system-sm-medium text-text-primary">{record.name}</span>
          <span className="rounded-md bg-background-section px-2 py-1 system-xs-medium text-text-secondary">
            {t(($) => $[`enterprise.setup.state.${record.state}`])}
          </span>
        </div>
        <p className="font-mono system-xs-regular text-text-tertiary">
          {t(($) => $['enterprise.devices.revision'])}: {record.expected_source_revision} ·{' '}
          {record.source_revision} · {record.read_revision}
        </p>
        <time className="system-xs-regular text-text-tertiary" dateTime={record.created_at}>
          {new Date(record.created_at).toLocaleString()}
        </time>
        <div className="flex gap-3">
          <Button
            variant="secondary"
            onClick={() => {
              setInspectId(record.id)
              void history.refetch()
            }}
          >
            {t(($) => $['enterprise.setup.inspect'])}
          </Button>
          {record.state === 'draft_ready' && record.app_id && (
            <Link
              href={`/app/${encodeURIComponent(record.app_id)}/workflow`}
              className="self-center rounded-md system-sm-medium text-text-accent outline-hidden focus-visible:ring-2 focus-visible:ring-state-accent-solid"
            >
              {t(($) => $['enterprise.setup.openDraft'])}
            </Link>
          )}
        </div>
      </div>
    )
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="secondary" />}>
        {t(($) => $['enterprise.setup.title'])}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogTitle>{t(($) => $['enterprise.setup.title'])}</DialogTitle>
        <DialogDescription className="my-3 system-sm-regular text-text-secondary">
          {t(($) => $['enterprise.setup.remaining'])}
        </DialogDescription>
        <div className="space-y-5">
          {disabled && (
            <p role="alert" className="system-sm-regular text-text-secondary">
              {t(($) => $['enterprise.devices.loadError'])}
            </p>
          )}
          <section className="space-y-3" aria-label={t(($) => $['enterprise.devices.source'])}>
            {sources.isPending ? (
              <div
                aria-busy="true"
                className="h-16 animate-pulse rounded-lg bg-background-section"
              />
            ) : sources.isError || invalidSources ? (
              <BusinessFailure
                retry={() => {
                  void sources.refetch()
                }}
              />
            ) : (
              <>
                <Select<string>
                  value={attempt?.source.source_id ?? selectedSource?.source_id ?? null}
                  onValueChange={(value) =>
                    setSelected(eligible.find((source) => source.source_id === value) ?? null)
                  }
                  disabled={!!attempt || create.isPending}
                >
                  <SelectTrigger aria-label={t(($) => $['enterprise.devices.source'])}>
                    <SelectValue placeholder={t(($) => $['enterprise.devices.source'])}>
                      {attempt?.source.name ?? selectedSource?.name}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {eligible.map((source) => (
                      <SelectItem key={source.source_id} value={source.source_id}>
                        {source.name} · {source.revision}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {eligible.length === 0 && (
                  <p className="system-sm-regular text-text-tertiary">
                    {t(($) => $['enterprise.setup.noSources'])}
                  </p>
                )}
                <div className="flex justify-between gap-2">
                  <Button
                    variant="secondary"
                    disabled={sourcePage === 0 || sources.isFetching}
                    onClick={() => setSourcePage((page) => page - 1)}
                  >
                    {t(($) => $['operation.back'])}
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={
                      !sources.data ||
                      (sourcePage + 1) * 20 >= sources.data.total ||
                      sources.isFetching
                    }
                    onClick={() => setSourcePage((page) => page + 1)}
                  >
                    {t(($) => $['pagination.next'])}
                  </Button>
                </div>
              </>
            )}
            <Button
              variant="primary"
              disabled={!canCreate}
              onClick={() => {
                if (canCreate && selectedSource)
                  submit({
                    key: crypto.randomUUID(),
                    source: selectedSource,
                    body: {
                      source_id: selectedSource.source_id,
                      expected_source_revision: selectedSource.revision,
                      expected_binding_revision: expectedBindingRevision,
                    },
                  })
              }}
            >
              {t(($) => $['enterprise.setup.create'])}
            </Button>
            {(attempt || unresolved || history.hasNextPage) && (
              <p role="status" className="system-sm-regular text-text-secondary">
                {t(($) => $['enterprise.setup.reviewHistory'])}
              </p>
            )}
            {attempt && !create.isPending && (
              <Button
                variant="secondary"
                disabled={disabled || mismatched}
                onClick={() => submit(attempt)}
              >
                {t(($) => $['enterprise.setup.retryAttempt'])}
              </Button>
            )}
            {mismatched && <p role="alert">{t(($) => $['enterprise.devices.accessDenied'])}</p>}
            {create.isError && (
              <p role="alert" className="system-sm-regular text-text-secondary">
                {t(($) => $['enterprise.setup.state.uncertain'])}
              </p>
            )}
          </section>
          {displayed && matches(displayed) && renderRecord(displayed)}
          {displayed && matches(displayed) && displayed.state === 'draft_ready' && (
            <ProvisioningSection
              key={displayed.id}
              setup={displayed}
              scope={scope}
              disabled={
                disabled ||
                detail.isError ||
                detail.isFetching ||
                history.isError ||
                invalidHistory ||
                !!(detail.data && (!matches(detail.data) || detail.data.id !== inspectId))
              }
              safety={provisioningSafety[displayed.id] ?? {}}
              onSafetyChange={(value) =>
                setProvisioningSafety((previous) => ({ ...previous, [displayed.id]: value }))
              }
            />
          )}
          {detail.isError ||
          (detail.data && (!matches(detail.data) || detail.data.id !== inspectId)) ? (
            <BusinessFailure
              retry={() => {
                void detail.refetch()
              }}
            />
          ) : null}
          <section className="space-y-3" aria-label={t(($) => $['enterprise.setup.history'])}>
            <div className="flex items-center justify-between">
              <h3 className="system-sm-semibold text-text-primary">
                {t(($) => $['enterprise.setup.history'])}
              </h3>
              <Button
                variant="secondary"
                disabled={history.isFetching}
                onClick={() => {
                  void history.refetch()
                  if (inspectId) void detail.refetch()
                }}
              >
                {t(($) => $['operation.retry'])}
              </Button>
            </div>
            {history.isPending ? (
              <div
                aria-busy="true"
                className="h-24 animate-pulse rounded-lg bg-background-section"
              />
            ) : history.isError || invalidHistory ? (
              <BusinessFailure
                message={
                  history.error instanceof ORPCError && history.error.status === 503
                    ? t(($) => $['enterprise.setup.unavailable'])
                    : undefined
                }
                retry={() => {
                  void history.refetch()
                }}
              />
            ) : records.length === 0 ? (
              <p className="system-sm-regular text-text-tertiary">{t(($) => $.noData)}</p>
            ) : (
              <ol className="space-y-2">
                {records
                  .filter((record) => record.id !== displayed?.id)
                  .map((record) => (
                    <li key={record.id}>{renderRecord(record)}</li>
                  ))}
              </ol>
            )}
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
          </section>
          <div className="flex justify-end">
            <Button variant="secondary" onClick={() => setOpen(false)}>
              {t(($) => $['operation.close'])}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
