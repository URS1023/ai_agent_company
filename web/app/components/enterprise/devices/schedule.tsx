'use client'

import type { BusinessAccess, Scenario } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { ORPCError } from '@orpc/client'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from './access'
import { ScheduleForm } from './schedule-form'

export function DeviceSchedule({
  deviceId,
  scenario,
  access,
  scope,
  bindingRevision,
}: {
  deviceId: string
  scenario: Scenario
  access: BusinessAccess
  scope: readonly string[]
  bindingRevision?: number
}) {
  const { t } = useTranslation('common')
  const schedule = useQuery(
    consoleQuery.business.devices.byDeviceId.byScenario.schedule.get.queryOptions({
      input: { params: { device_id: deviceId, scenario } },
      queryKey: [...scope, 'schedule', deviceId, scenario],
      retry: false,
    }),
  )
  const change = useMutation(
    consoleQuery.business.schedules.byScheduleId.state.post.mutationOptions({
      retry: false,
      onSuccess: async () => {
        await schedule.refetch()
      },
    }),
  )
  const submittedRef = useRef(false)
  const create = useMutation(
    consoleQuery.business.devices.byDeviceId.byScenario.schedule.post.mutationOptions({
      retry: false,
      onSuccess: async () => {
        await schedule.refetch()
      },
    }),
  )
  const canCreate = access.permissions.manage && bindingRevision !== undefined
  const creationRevision = create.variables?.body.expected_binding_revision ?? bindingRevision
  const validationRejected = create.error instanceof ORPCError && create.error.status === 422
  const actors = useQuery(
    consoleQuery.business.devices.byDeviceId.byScenario.schedule.actors.get.queryOptions({
      input: { params: { device_id: deviceId, scenario } },
      queryKey: [...scope, 'schedule-actors', deviceId, scenario],
      enabled: canCreate && schedule.isSuccess && schedule.data === null && create.isIdle,
      retry: false,
    }),
  )
  const refresh = async () => {
    const result = await schedule.refetch()
    if (result.isSuccess) change.reset()
    if (result.isSuccess && result.data === null && canCreate && create.isIdle)
      await actors.refetch()
  }
  const editRejected = async () => {
    if (!validationRejected || !canCreate || schedule.isFetching || actors.isFetching) return
    const current = await schedule.refetch()
    if (!current.isSuccess || current.data !== null) return
    const candidates = await actors.refetch()
    if (!candidates.isSuccess) return
    submittedRef.current = false
    create.reset()
  }
  const value = schedule.data
  const mismatched =
    value &&
    (value.workspace_id !== access.workspace_id ||
      value.device_id !== deviceId ||
      value.scenario !== scenario)
  return (
    <section
      aria-label={t(($) => $['enterprise.schedule.title'])}
      aria-busy={schedule.isFetching || change.isPending || create.isPending}
      className="space-y-3 rounded-xl border border-divider-subtle bg-background-default p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="system-md-semibold text-text-primary">
          {t(($) => $['enterprise.schedule.title'])}
        </h3>
        <Button
          variant="secondary"
          disabled={schedule.isFetching || change.isPending || create.isPending}
          onClick={() => {
            void refresh()
          }}
        >
          {t(($) => $['enterprise.provisioning.refresh'])}
        </Button>
      </div>
      {schedule.isPending ? (
        <div
          aria-hidden
          className="h-20 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
        />
      ) : schedule.isError || mismatched ? (
        <BusinessFailure
          retry={() => {
            void refresh()
          }}
        />
      ) : !value ? (
        <>
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.schedule.empty'])}
          </p>
          {canCreate &&
            creationRevision !== undefined &&
            (actors.isPending ? (
              <div
                aria-hidden
                className="h-32 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
              />
            ) : actors.isError ? (
              <BusinessFailure
                retry={() => {
                  void actors.refetch()
                }}
              />
            ) : (
              <>
                {!create.isIdle && !create.isPending && (
                  <p role="alert" className="system-sm-regular text-text-secondary">
                    {t(
                      ($) =>
                        $[
                          validationRejected
                            ? 'enterprise.schedule.invalid'
                            : 'enterprise.devices.writeError'
                        ],
                    )}
                  </p>
                )}
                {validationRejected && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={schedule.isFetching || actors.isFetching}
                    onClick={() => {
                      void editRejected()
                    }}
                  >
                    {t(($) => $['operation.edit'])}
                  </Button>
                )}
                <p className="system-sm-regular text-text-secondary">
                  {t(
                    ($) =>
                      $[
                        actors.data.length
                          ? 'enterprise.schedule.pausedHint'
                          : 'enterprise.schedule.noActors'
                      ],
                  )}
                </p>
                <ScheduleForm
                  key={creationRevision}
                  actors={actors.data}
                  bindingRevision={creationRevision}
                  pending={!create.isIdle || schedule.isFetching || actors.isFetching}
                  onSave={(body) => {
                    if (
                      submittedRef.current ||
                      !create.isIdle ||
                      schedule.isFetching ||
                      actors.isFetching
                    )
                      return
                    submittedRef.current = true
                    create.mutate({
                      params: { device_id: deviceId, scenario },
                      headers: { Origin: window.location.origin },
                      body,
                    })
                  }}
                />
              </>
            ))}
        </>
      ) : (
        <>
          <p role="status" className="system-sm-medium text-text-primary">
            {t(
              ($) =>
                $[value.enabled ? 'enterprise.schedule.enabled' : 'enterprise.schedule.paused'],
            )}
          </p>
          <dl className="system-sm-regular text-text-secondary">
            <dt>{t(($) => $['enterprise.schedule.next'])}</dt>
            <dd>
              <time dateTime={value.next_due_at}>
                {new Date(value.next_due_at).toLocaleString()}
              </time>
            </dd>
          </dl>
          {change.isError && (
            <p role="alert" className="system-sm-regular text-text-secondary">
              {t(($) => $['enterprise.devices.writeError'])}
            </p>
          )}
          {access.permissions.manage && (
            <Button
              variant="secondary"
              disabled={schedule.isFetching || change.isPending || change.isError}
              onClick={() =>
                change.mutate({
                  params: { schedule_id: value.id },
                  headers: { Origin: window.location.origin },
                  body: { expected_revision: value.revision, enabled: !value.enabled },
                })
              }
            >
              {t(
                ($) =>
                  $[value.enabled ? 'enterprise.schedule.pause' : 'enterprise.schedule.resume'],
              )}
            </Button>
          )}
        </>
      )}
    </section>
  )
}
