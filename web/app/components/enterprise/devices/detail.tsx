'use client'

import type { BusinessAccess, Scenario } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Tabs, TabsList, TabsPanel, TabsTab } from '@langgenius/dify-ui/tabs'
import { ORPCError } from '@orpc/client'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import Link from '@/next/link'
import { useRouter } from '@/next/navigation'
import { consoleQuery } from '@/service/client'
import { WorkflowSetup } from '../workflow-setup'
import { BusinessFailure, BusinessGate } from './access'
import { DeleteDevice, DeviceEditor } from './device-actions'
import { QueueRun } from './run-dialog'
import { DeviceSchedule } from './schedule'

const scenarios = ['alert', 'quality'] as const satisfies readonly Scenario[]

export function DeviceDetail({ deviceId }: { deviceId: string }) {
  return (
    <div className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <BusinessGate>
        {(access, scope) => (
          <DetailContent key={deviceId} deviceId={deviceId} access={access} scope={scope} />
        )}
      </BusinessGate>
    </div>
  )
}

function DetailContent({
  deviceId,
  access,
  scope,
}: {
  deviceId: string
  access: BusinessAccess
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const router = useRouter()
  const device = useQuery(
    consoleQuery.business.devices.byDeviceId.get.queryOptions({
      input: { params: { device_id: deviceId } },
      queryKey: [...scope, 'device', deviceId],
      retry: false,
    }),
  )
  if (device.isPending)
    return (
      <div
        aria-busy="true"
        className="m-8 h-48 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
      />
    )
  if (device.isError)
    return (
      <div className="p-8">
        <BusinessFailure
          retry={() => {
            void device.refetch()
          }}
        />
      </div>
    )
  if (device.data.workspace_id !== access.workspace_id)
    return <p role="alert">{t(($) => $['enterprise.devices.accessDenied'])}</p>
  return (
    <main className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-8 lg:px-12">
      <Link
        href="/enterprise/devices"
        className="system-sm-medium text-text-tertiary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
      >
        {t(($) => $['enterprise.devices.title'])}
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-3">
          <span className="font-mono break-words text-text-accent">{device.data.device_code}</span>
          <h1 className="text-3xl font-semibold tracking-tight break-words text-text-primary">
            {device.data.name}
          </h1>
          <p className="break-words text-text-secondary">{device.data.department}</p>
          <p className="max-w-2xl system-sm-regular break-words whitespace-pre-wrap text-text-tertiary">
            {device.data.description}
          </p>
        </div>
        {access.permissions.manage && (
          <div className="flex gap-2">
            <DeviceEditor device={device.data} scope={scope} />
            <DeleteDevice
              device={device.data}
              scope={scope}
              onDeleted={() => router.push('/enterprise/devices')}
            />
          </div>
        )}
      </header>
      <Tabs defaultValue="alert">
        <TabsList aria-label={t(($) => $['enterprise.devices.scenarioLabel'])}>
          {scenarios.map((scenario) => (
            <TabsTab key={scenario} value={scenario}>
              {t(($) => $[`enterprise.devices.scenario.${scenario}`])}
            </TabsTab>
          ))}
        </TabsList>
        {scenarios.map((scenario) => (
          <TabsPanel key={scenario} value={scenario} keepMounted className="space-y-8 pt-6">
            <ScenarioWorkflow
              deviceId={deviceId}
              scenario={scenario}
              access={access}
              scope={scope}
            />
            <RecentRuns deviceId={deviceId} scenario={scenario} scope={scope} />
          </TabsPanel>
        ))}
      </Tabs>
    </main>
  )
}

function ScenarioWorkflow({
  deviceId,
  scenario,
  access,
  scope,
}: {
  deviceId: string
  scenario: Scenario
  access: BusinessAccess
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const binding = useQuery(
    consoleQuery.business.devices.byDeviceId.bindings.byScenario.get.queryOptions({
      input: { params: { device_id: deviceId, scenario } },
      queryKey: [...scope, 'binding', deviceId, scenario],
      retry: false,
    }),
  )
  const missing = binding.error instanceof ORPCError && binding.error.status === 404
  const matchingBinding =
    binding.data?.workspace_id === access.workspace_id &&
    binding.data.device_id === deviceId &&
    binding.data.scenario === scenario
      ? binding.data
      : undefined
  const label = t(
    ($) =>
      $[scenario === 'alert' ? 'enterprise.devices.binding' : 'enterprise.devices.qualityBinding'],
  )
  return (
    <section
      aria-label={label}
      className="space-y-5 rounded-xl border border-divider-subtle bg-background-default p-6"
      aria-busy={binding.isPending}
    >
      <h2 className="text-xl font-semibold text-text-primary">{label}</h2>
      {binding.isPending ? (
        <div
          aria-hidden
          className="h-24 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
        />
      ) : missing ? (
        <div className="space-y-3">
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.devices.bindingMissing'])}
          </p>
          <Link
            href={`/enterprise/workflow-templates/${scenario}`}
            download={`default-${scenario}.yml`}
            prefetch={false}
            className="inline-flex rounded-lg system-sm-medium text-text-accent outline-hidden focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['enterprise.devices.downloadTemplate'])}
          </Link>
          <p className="system-xs-regular text-text-tertiary">
            {t(($) => $['enterprise.devices.templateSetup'])}
          </p>
        </div>
      ) : binding.isError || !matchingBinding ? (
        <BusinessFailure
          retry={() => {
            void binding.refetch()
          }}
        />
      ) : (
        <>
          <dl className="grid gap-4 system-sm-regular sm:grid-cols-2">
            <div>
              <dt className="text-text-tertiary">{t(($) => $['enterprise.devices.source'])}</dt>
              <dd className="mt-1 font-mono break-words text-text-primary">
                {matchingBinding.source_id}
              </dd>
            </div>
            <div>
              <dt className="text-text-tertiary">{t(($) => $['enterprise.devices.revision'])}</dt>
              <dd className="mt-1 font-mono break-words text-text-primary">
                {matchingBinding.revision} · {matchingBinding.specification_revision}
              </dd>
            </div>
          </dl>
          <div className="flex flex-wrap items-center gap-4">
            <Link
              href={`/app/${encodeURIComponent(matchingBinding.app_id)}/workflow`}
              className="rounded-lg system-sm-medium text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
            >
              {label}
              <span
                aria-hidden
                className="ms-1 i-ri-arrow-right-up-line inline-block h-4 w-4 align-middle"
              />
            </Link>
          </div>
        </>
      )}
      {access.permissions.manage && (
        <WorkflowSetup
          deviceId={deviceId}
          scenario={scenario}
          scope={scope}
          expectedBindingRevision={matchingBinding?.revision ?? null}
          disabled={binding.isFetching || (!missing && (binding.isError || !matchingBinding))}
        />
      )}
      {access.permissions.run && (
        <QueueRun
          binding={matchingBinding}
          scope={scope}
          disabled={binding.isFetching || binding.isError || !matchingBinding}
        />
      )}
      <DeviceSchedule
        deviceId={deviceId}
        scenario={scenario}
        access={access}
        scope={scope}
        bindingRevision={
          !binding.isFetching && !binding.isError ? matchingBinding?.revision : undefined
        }
      />
    </section>
  )
}

function RecentRuns({
  deviceId,
  scenario,
  scope,
}: {
  deviceId: string
  scenario: Scenario
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const runs = useQuery(
    consoleQuery.business.devices.byDeviceId.bindings.byScenario.runs.get.queryOptions({
      input: { params: { device_id: deviceId, scenario }, query: { offset: 0, limit: 5 } },
      queryKey: [...scope, 'runs', deviceId, scenario],
      retry: false,
    }),
  )
  return (
    <section className="space-y-4" aria-busy={runs.isPending}>
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-xl font-semibold text-text-primary">
          {t(($) => $['enterprise.devices.recentRuns'])}
        </h2>
        <Button
          variant="secondary"
          disabled={runs.isFetching}
          onClick={() => {
            void runs.refetch()
          }}
        >
          {t(($) => $['operation.retry'])}
        </Button>
      </div>
      {runs.isPending ? (
        <div
          aria-hidden
          className="h-24 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
        />
      ) : runs.isError ||
        runs.data.items.some((run) => run.device_id !== deviceId || run.scenario !== scenario) ? (
        <BusinessFailure
          retry={() => {
            void runs.refetch()
          }}
        />
      ) : runs.data.items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-divider-regular p-8 text-text-tertiary">
          {t(($) => $.noData)}
        </p>
      ) : (
        <ol className="divide-y divide-divider-subtle rounded-xl border border-divider-subtle bg-background-default">
          {runs.data.items.map((run) => (
            <li key={run.id} className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <Link
                  href={`/enterprise/runs/${encodeURIComponent(run.id)}`}
                  className="font-mono text-xs break-all text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
                >
                  {run.id}
                </Link>
                <time
                  dateTime={run.created_at}
                  className="mt-1 block system-xs-regular text-text-tertiary"
                >
                  {new Date(run.created_at).toLocaleString()}
                </time>
              </div>
              <span className="rounded-lg bg-background-section px-3 py-1 system-xs-medium text-text-primary">
                {t(($) => $[`enterprise.devices.state.${run.status}`])}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
