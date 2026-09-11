'use client'

import type { RunView } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from '@langgenius/dify-ui/collapsible'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Link from '@/next/link'
import { consoleQuery } from '@/service/client'
import { BusinessFailure, BusinessGate } from '../devices/access'

const previewLimit = 65536

function JsonPreview({ title, value }: { title: string; value: unknown }) {
  const { t } = useTranslation('common')
  const text = JSON.stringify(value, null, 2)
  if (text === undefined) return null
  return (
    <Collapsible className="rounded-xl border border-divider-subtle bg-background-default p-3">
      <CollapsibleTrigger>{title}</CollapsibleTrigger>
      <CollapsiblePanel>
        {text.length > previewLimit && (
          <p className="px-3 pt-3 system-xs-regular text-text-warning">
            {t(($) => $['enterprise.runs.previewLimited'])}
          </p>
        )}
        <pre className="max-h-96 overflow-auto p-3 font-mono text-xs break-all whitespace-pre-wrap text-text-secondary">
          {text.slice(0, previewLimit)}
        </pre>
      </CollapsiblePanel>
    </Collapsible>
  )
}

function download(run: RunView) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(run, null, 2)], { type: 'application/json;charset=utf-8' }),
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `run-${run.id.replace(/[^\w-]/g, '_').slice(0, 128)}.json`
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function RunDetail({ runId }: { runId: string }) {
  return (
    <div className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <BusinessGate>
        {(access, scope) => (
          <RunContent key={runId} runId={runId} workspaceId={access.workspace_id} scope={scope} />
        )}
      </BusinessGate>
    </div>
  )
}

function RunContent({
  runId,
  workspaceId,
  scope,
}: {
  runId: string
  workspaceId: string
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const query = useQuery(
    consoleQuery.business.runs.byRunId.get.queryOptions({
      input: { params: { run_id: runId } },
      queryKey: [...scope, 'run', runId],
      retry: false,
    }),
  )
  if (query.isPending)
    return (
      <div
        aria-busy="true"
        className="m-8 h-48 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
      />
    )
  if (query.isError)
    return (
      <div className="p-8">
        <BusinessFailure
          retry={() => {
            void query.refetch()
          }}
        />
      </div>
    )
  const run = query.data
  const result = run.result
  if (run.id !== runId || (run.result !== null && run.result.scenario !== run.scenario))
    return <p role="alert">{t(($) => $['enterprise.devices.accessDenied'])}</p>
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-8 lg:px-12">
      <Link
        href={`/enterprise/devices/${encodeURIComponent(run.device_id)}`}
        className="system-sm-medium text-text-tertiary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
      >
        {t(($) => $['enterprise.devices.title'])}
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <p className="system-xs-medium text-text-accent">
            {t(($) => $[run.scenario === 'alert' ? 'enterprise.alerts' : 'enterprise.quality'])}
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
            {t(($) => $['enterprise.runs.title'])}
          </h1>
          <p className="font-mono text-xs break-all text-text-tertiary">{run.id}</p>
          <time dateTime={run.created_at} className="block system-xs-regular text-text-tertiary">
            {new Date(run.created_at).toLocaleString()}
          </time>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            disabled={query.isFetching}
            onClick={() => {
              void query.refetch()
              void client.invalidateQueries({ queryKey: [...scope, 'run-events', runId] })
            }}
          >
            {t(($) => $['operation.retry'])}
          </Button>
          <Button variant="secondary" onClick={() => download(run)}>
            {t(($) => $['enterprise.runs.download'])}
          </Button>
        </div>
      </header>
      <div className="grid gap-4 md:grid-cols-2">
        <section className="space-y-4 rounded-xl border border-divider-subtle bg-background-default p-6">
          <h2 className="system-sm-medium text-text-secondary">
            {t(($) => $['enterprise.runs.execution'])}
          </h2>
          <p className="text-xl font-semibold text-text-primary">
            {t(($) => $[`enterprise.devices.state.${run.status}`])}
          </p>
          {run.reason_code && (
            <p className="system-xs-regular text-text-tertiary">
              {t(($) => $['enterprise.runs.reason'])}: <code>{run.reason_code}</code>
            </p>
          )}
        </section>
        <section className="space-y-4 rounded-xl border border-divider-subtle bg-background-default p-6">
          <h2 className="system-sm-medium text-text-secondary">
            {t(($) => $['enterprise.runs.conclusion'])}
          </h2>
          <p className="text-xl font-semibold text-text-primary">
            {result
              ? t(($) => $[`enterprise.runs.verdict.${result.conclusion}`])
              : t(($) => $['enterprise.runs.noConclusion'])}
          </p>
          {run.result && (
            <p className="system-xs-regular text-text-secondary">
              {t(
                ($) =>
                  $[
                    run.result?.complete ? 'enterprise.runs.complete' : 'enterprise.runs.incomplete'
                  ],
              )}
            </p>
          )}
        </section>
      </div>
      <dl className="grid gap-4 rounded-xl border border-divider-subtle p-5 system-sm-regular sm:grid-cols-2">
        <div>
          <dt className="text-text-tertiary">{t(($) => $['enterprise.devices.revision'])}</dt>
          <dd className="mt-2 font-mono text-text-primary">
            <span>{run.binding_revision}</span> · <span>{run.specification_revision}</span>
          </dd>
        </div>
        <div>
          <dt className="text-text-tertiary">{t(($) => $['enterprise.runs.snapshot'])}</dt>
          <dd className="mt-2 font-mono text-xs break-all text-text-secondary">
            {run.has_input_snapshot
              ? run.input_snapshot_digest
              : t(($) => $['enterprise.runs.notCaptured'])}
          </dd>
        </div>
      </dl>
      {run.result && (
        <JsonPreview title={t(($) => $['enterprise.runs.evidence'])} value={run.result.evidence} />
      )}
      <JsonPreview title={t(($) => $['enterprise.runs.parameters'])} value={run.parameters} />
      <RunEvents runId={runId} workspaceId={workspaceId} scope={scope} />
    </main>
  )
}

function RunEvents({
  runId,
  workspaceId,
  scope,
}: {
  runId: string
  workspaceId: string
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const [page, setPage] = useState(0)
  const offset = page * 20
  const query = useQuery(
    consoleQuery.business.runs.byRunId.events.get.queryOptions({
      input: { params: { run_id: runId }, query: { after_sequence: 0 } },
      queryKey: [...scope, 'run-events', runId],
      retry: false,
    }),
  )
  const mismatched = query.data?.some(
    (event) => event.workspace_id !== workspaceId || event.run_id !== runId,
  )
  return (
    <section className="space-y-4" aria-busy={query.isPending}>
      <h2 className="text-xl font-semibold text-text-primary">
        {t(($) => $['enterprise.runs.events'])}
      </h2>
      {query.isPending ? (
        <div className="h-20 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none" />
      ) : query.isError ? (
        <BusinessFailure
          retry={() => {
            void query.refetch()
          }}
        />
      ) : mismatched ? (
        <p role="alert">{t(($) => $['enterprise.devices.accessDenied'])}</p>
      ) : query.data.length === 0 ? (
        <p className="text-text-tertiary">{t(($) => $.noData)}</p>
      ) : (
        <ol className="divide-y divide-divider-subtle rounded-xl border border-divider-subtle bg-background-default">
          {query.data.slice(offset, offset + 20).map((event) => (
            <li key={event.sequence} className="flex flex-wrap justify-between gap-3 p-4">
              <span className="font-mono system-sm-regular text-text-secondary">
                {event.action}
              </span>
              <time dateTime={event.created_at} className="system-xs-regular text-text-tertiary">
                {new Date(event.created_at).toLocaleString()}
              </time>
            </li>
          ))}
        </ol>
      )}
      <div className="flex justify-end gap-2">
        <Button
          variant="secondary"
          disabled={page === 0 || query.isFetching}
          onClick={() => setPage(page - 1)}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <Button
          variant="secondary"
          disabled={
            !query.data ||
            query.isError ||
            mismatched ||
            query.isFetching ||
            offset + 20 >= query.data.length ||
            page >= 99999
          }
          onClick={() => setPage(page + 1)}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </div>
    </section>
  )
}
