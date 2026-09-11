'use client'

import { Button } from '@langgenius/dify-ui/button'
import { ORPCError } from '@orpc/client'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { BindingEditor } from './binding-editor'
import { bindingReceiptMatches } from './binding-form'

export function DashboardBindings({
  dashboardId,
  scope,
}: {
  dashboardId: string
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const submittedRef = useRef(false)
  const [mismatch, setMismatch] = useState(false)
  const [epoch, setEpoch] = useState(0)
  const key = [...scope, 'dashboard-bindings', dashboardId]
  const bindings = useQuery(
    consoleQuery.business.dashboards.byDashboardId.bindings.get.queryOptions({
      input: { params: { dashboard_id: dashboardId } },
      queryKey: key,
      retry: false,
      refetchOnWindowFocus: false,
    }),
  )
  const queries = useQuery(
    consoleQuery.business.dashboardQueries.get.queryOptions({
      queryKey: [...scope, 'dashboard-queries'],
      retry: false,
      refetchOnWindowFocus: false,
    }),
  )
  const save = useMutation(
    consoleQuery.business.dashboards.byDashboardId.bindings.put.mutationOptions({
      retry: false,
      onSuccess: async (receipt, request) => {
        if (!bindings.data || !bindingReceiptMatches(bindings.data, request.body, receipt)) {
          setMismatch(true)
          return
        }
        client.setQueryData(key, receipt)
        await Promise.all([
          client.invalidateQueries({ queryKey: [...scope, 'dashboards'] }),
          client.invalidateQueries({ queryKey: [...scope, 'dashboard-detail', dashboardId] }),
        ])
      },
      onSettled: () => {
        submittedRef.current = false
      },
    }),
  )
  const reload = async () => {
    if (submittedRef.current) return
    submittedRef.current = true
    try {
      const [current, choices] = await Promise.all([bindings.refetch(), queries.refetch()])
      if (current.isSuccess && choices.isSuccess && current.data.dashboard_id === dashboardId) {
        save.reset()
        setMismatch(false)
        setEpoch((value) => value + 1)
      }
    } finally {
      submittedRef.current = false
    }
  }
  if (bindings.isPending || queries.isPending)
    return (
      <div
        aria-busy="true"
        className="h-32 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
      />
    )
  if (bindings.isError || queries.isError)
    return (
      <BusinessFailure
        retry={() => {
          void reload()
        }}
      />
    )
  if (bindings.data.dashboard_id !== dashboardId || mismatch)
    return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  const rejected = save.error instanceof ORPCError && [409, 422].includes(save.error.status)
  return (
    <section className="space-y-4" aria-busy={save.isPending}>
      {save.isError && (
        <BusinessFailure
          mutation
          retry={() => {
            if (submittedRef.current || !save.variables) return
            submittedRef.current = true
            save.mutate(save.variables)
          }}
        />
      )}
      {rejected && (
        <Button
          variant="secondary"
          disabled={bindings.isFetching || queries.isFetching}
          onClick={() => {
            void reload()
          }}
        >
          {t(($) => $['operation.change'])}
        </Button>
      )}
      <BindingEditor
        key={`${dashboardId}:${bindings.data.revision}:${epoch}`}
        view={bindings.data}
        choices={queries.data.items}
        disabled={save.isPending || save.isError || bindings.isFetching || queries.isFetching}
        onSave={(body) => {
          if (submittedRef.current || save.isError) return
          submittedRef.current = true
          save.mutate({
            params: { dashboard_id: dashboardId },
            body,
            headers: { Origin: window.location.origin },
          })
        }}
      />
    </section>
  )
}
