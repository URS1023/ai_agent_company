'use client'

import { Button } from '@langgenius/dify-ui/button'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { DashboardCanvas } from './dashboard-canvas'
import { SqlGeneration } from './sql-generation'

export function DashboardDetail({
  dashboardId,
  scope,
  onClose,
  canRun,
  canManage,
  workspaceId,
}: {
  dashboardId: string
  scope: readonly string[]
  onClose: () => void
  canRun: boolean
  canManage: boolean
  workspaceId: string
}) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const submittedRef = useRef(false)
  const [mismatch, setMismatch] = useState(false)
  const key = [...scope, 'dashboard-detail', dashboardId]
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.get.queryOptions({
      input: { params: { dashboard_id: dashboardId } },
      queryKey: key,
      retry: false,
      refetchOnWindowFocus: false,
    }),
  )
  const refresh = useMutation(
    consoleQuery.business.dashboards.byDashboardId.refresh.post.mutationOptions({
      retry: false,
      onSuccess: async (receipt, request) => {
        const previous = query.data
        if (
          !previous ||
          receipt.id !== dashboardId ||
          receipt.design_identity !== previous.design_identity ||
          receipt.template_id !== previous.template_id ||
          receipt.renderer_build_id !== previous.renderer_build_id ||
          receipt.revision <= request.body.expected_revision
        ) {
          setMismatch(true)
          return
        }
        client.setQueryData(key, receipt)
        await client.invalidateQueries({ queryKey: [...scope, 'dashboards'] })
      },
      onSettled: () => {
        submittedRef.current = false
      },
    }),
  )
  return (
    <section className="space-y-4" aria-busy={query.isFetching}>
      <div className="flex items-center justify-between gap-4">
        <h2 className="system-md-semibold text-text-primary">
          {query.data?.id === dashboardId ? query.data.name || dashboardId : dashboardId}
        </h2>
        <Button variant="secondary" onClick={onClose}>
          {t(($) => $['operation.close'])}
        </Button>
        {canRun && query.data?.id === dashboardId && (
          <Button
            variant="secondary"
            aria-label={`${t(($) => $['enterprise.provisioning.refresh'])} ${query.data.name || dashboardId}`}
            disabled={
              refresh.isPending || refresh.isError || query.isFetching || query.isError || mismatch
            }
            onClick={() => {
              if (submittedRef.current || !query.data || refresh.isError || mismatch) return
              submittedRef.current = true
              refresh.mutate({
                params: { dashboard_id: dashboardId },
                body: { expected_revision: query.data.revision },
                headers: { Origin: window.location.origin },
              })
            }}
          >
            {t(($) => $['enterprise.provisioning.refresh'])}
          </Button>
        )}
      </div>
      {refresh.isError && (
        <BusinessFailure
          mutation
          retry={async () => {
            if (submittedRef.current) return
            const current = await query.refetch()
            if (current.isSuccess && current.data.id === dashboardId) refresh.reset()
          }}
        />
      )}
      {mismatch && <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>}
      {query.isPending && (
        <div className="aspect-video animate-pulse rounded-xl bg-background-section motion-reduce:animate-none" />
      )}
      {query.isError && (
        <BusinessFailure
          retry={() => {
            void query.refetch()
          }}
        />
      )}
      {query.isSuccess &&
        !mismatch &&
        (query.data.id === dashboardId ? (
          <>
            <DashboardCanvas view={query.data} />
            {canManage && (
              <SqlGeneration
                key={JSON.stringify([
                  scope,
                  query.data.id,
                  query.data.revision,
                  query.data.design_identity,
                ])}
                view={query.data}
                scope={scope}
                workspaceId={workspaceId}
              />
            )}
          </>
        ) : (
          <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
        ))}
    </section>
  )
}
