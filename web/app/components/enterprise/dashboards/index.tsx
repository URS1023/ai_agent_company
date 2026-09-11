'use client'

import type { BusinessAccess } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Field, FieldControl, FieldLabel } from '@langgenius/dify-ui/field'
import { Form } from '@langgenius/dify-ui/form'
import { ORPCError } from '@orpc/client'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { parseAsString, useQueryState } from 'nuqs'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { v4 as uuidv4 } from 'uuid'
import { env } from '@/env'
import Link from '@/next/link'
import { consoleQuery } from '@/service/client'
import { BusinessFailure, BusinessGate } from '../devices/access'
import { DashboardBindings } from './bindings'
import { DashboardDetail } from './detail'
import templatePreviews from './template-previews.json'

function DashboardCreation({ scope }: { scope: readonly string[] }) {
  const { t } = useTranslation('common')
  const queryClient = useQueryClient()
  const submittedRef = useRef(false)
  const templates = useQuery(
    consoleQuery.business.dashboardTemplates.get.queryOptions({
      queryKey: [...scope, 'dashboard-templates'],
      retry: false,
    }),
  )
  const create = useMutation(
    consoleQuery.business.dashboards.post.mutationOptions({
      retry: false,
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: [...scope, 'dashboards'] })
      },
      onSettled: () => {
        submittedRef.current = false
      },
    }),
  )
  const rejected = create.error instanceof ORPCError && [409, 422].includes(create.error.status)
  return (
    <section className="space-y-4" aria-busy={templates.isFetching || create.isPending}>
      <h2 className="system-md-semibold text-text-primary">{t(($) => $['operation.create'])}</h2>
      {templates.isPending && (
        <div className="h-20 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none" />
      )}
      {templates.isError && (
        <BusinessFailure
          retry={() => {
            void templates.refetch()
          }}
        />
      )}
      {create.isError && (
        <BusinessFailure
          mutation
          retry={() => {
            if (submittedRef.current || !create.variables) return
            submittedRef.current = true
            create.mutate(create.variables)
          }}
        />
      )}
      {rejected && (
        <Button
          variant="secondary"
          disabled={templates.isFetching}
          onClick={async () => {
            if (submittedRef.current) return
            submittedRef.current = true
            try {
              const current = await templates.refetch()
              if (current.isSuccess) create.reset()
            } finally {
              submittedRef.current = false
            }
          }}
        >
          {t(($) => $['operation.change'])}
        </Button>
      )}
      {create.isSuccess && (
        <p role="status" className="system-sm-regular text-text-secondary">
          {t(($) => $['operation.added'])} · <span>{create.data.id}</span>
        </p>
      )}
      {templates.isSuccess && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {templates.data.items.map((template) => {
            const preview = templatePreviews.find(
              (item) =>
                item.id === template.template_id &&
                item.templateRevision === template.template_revision,
            )
            return (
              <article
                key={template.template_id}
                className="flex flex-col items-start gap-4 overflow-hidden rounded-xl border border-divider-subtle bg-background-default p-4"
              >
                {preview && (
                  <img
                    src={`${env.NEXT_PUBLIC_BASE_PATH}${preview.src}`}
                    alt={preview.name}
                    width={640}
                    height={360}
                    loading="lazy"
                    decoding="async"
                    className="aspect-video w-full rounded-lg object-contain"
                  />
                )}
                <h3 className="min-w-0 system-sm-medium break-words text-text-primary">
                  {preview?.name ?? template.template_id}
                </h3>
                <Form
                  key={template.design_identity}
                  className="w-full space-y-3"
                  onSubmit={(event) => {
                    event.preventDefault()
                    if (submittedRef.current || create.isError || templates.isFetching) return
                    const name = String(new FormData(event.currentTarget).get('name') ?? '').trim()
                    if (!name) return
                    submittedRef.current = true
                    create.mutate({
                      body: {
                        template_id: template.template_id,
                        expected_design_identity: template.design_identity,
                        name,
                      },
                      headers: { 'idempotency-key': uuidv4(), Origin: window.location.origin },
                    })
                  }}
                >
                  <Field name="name">
                    <FieldLabel>{t(($) => $['account.name'])}</FieldLabel>
                    <FieldControl
                      defaultValue={preview?.name ?? template.template_id}
                      required
                      maxLength={200}
                      pattern=".*\S.*"
                      disabled={create.isPending || create.isError}
                    />
                  </Field>
                  <Button
                    type="submit"
                    variant="secondary"
                    disabled={
                      create.isPending ||
                      create.isError ||
                      templates.isFetching ||
                      templates.isError
                    }
                    aria-label={`${t(($) => $['operation.create'])} ${preview?.name ?? template.template_id}`}
                  >
                    {t(($) => $['operation.create'])}
                  </Button>
                </Form>
              </article>
            )
          })}
          {!templates.data.items.length && (
            <p className="text-text-tertiary">{t(($) => $.noData)}</p>
          )}
        </div>
      )}
    </section>
  )
}

function DashboardManagement({
  access,
  scope,
}: {
  access: BusinessAccess
  scope: readonly string[]
}) {
  const { t } = useTranslation('common')
  const [after, setAfter] = useQueryState('after', parseAsString)
  const [editing, setEditing] = useQueryState('bindings', parseAsString)
  const [viewing, setViewing] = useQueryState('view', parseAsString)
  const query = { ...(after ? { after } : {}), limit: 20 }
  const dashboards = useQuery(
    consoleQuery.business.dashboards.get.queryOptions({
      input: { query },
      queryKey: [...scope, 'dashboards', query],
      retry: false,
    }),
  )
  return (
    <main className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-8 lg:px-12">
      <header className="space-y-3">
        <Link
          href="/enterprise"
          className="system-sm-medium text-text-secondary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
        >
          {t(($) => $['enterprise.title'])}
        </Link>
        <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
          {t(($) => $['enterprise.dashboards'])}
        </h1>
      </header>
      {access.permissions.manage && <DashboardCreation scope={scope} />}
      {viewing && (
        <DashboardDetail
          key={`view:${viewing}`}
          dashboardId={viewing}
          canRun={access.permissions.run}
          canManage={access.permissions.manage}
          workspaceId={access.workspace_id}
          scope={scope}
          onClose={() => {
            void setViewing(null)
          }}
        />
      )}
      {access.permissions.manage && editing && (
        <DashboardBindings key={`bindings:${editing}`} dashboardId={editing} scope={scope} />
      )}
      <section className="space-y-4" aria-busy={dashboards.isFetching}>
        <div className="flex justify-end">
          <Button
            variant="secondary"
            disabled={dashboards.isFetching}
            onClick={() => {
              void dashboards.refetch()
            }}
          >
            {t(($) => $['enterprise.provisioning.refresh'])}
          </Button>
        </div>
        {dashboards.isPending && (
          <div className="h-32 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none" />
        )}
        {dashboards.isError && (
          <BusinessFailure
            retry={() => {
              void dashboards.refetch()
            }}
          />
        )}
        {dashboards.isSuccess && (
          <div className="grid gap-3 sm:grid-cols-2">
            {dashboards.data.items.map((item) => (
              <article
                key={item.id}
                className="space-y-2 rounded-xl border border-divider-subtle bg-background-default p-5"
              >
                <h2 className="system-sm-medium break-all text-text-primary">
                  {item.name || item.id}
                </h2>
                <p className="system-sm-regular text-text-secondary">{item.template_id}</p>
                <Button
                  variant="secondary"
                  aria-label={`${t(($) => $['operation.view'])} ${item.name || item.id}`}
                  onClick={() => {
                    void setViewing(item.id)
                  }}
                >
                  {t(($) => $['operation.view'])}
                </Button>
                {access.permissions.manage && (
                  <Button
                    variant="secondary"
                    aria-label={`${t(($) => $['operation.edit'])} ${item.name || item.id}`}
                    onClick={() => {
                      void setEditing(item.id)
                    }}
                  >
                    {t(($) => $['operation.edit'])}
                  </Button>
                )}
              </article>
            ))}
            {!dashboards.data.items.length && (
              <p className="text-text-tertiary">{t(($) => $.noData)}</p>
            )}
          </div>
        )}
        <div className="flex gap-2">
          {after && (
            <Button
              variant="secondary"
              onClick={() => {
                void setAfter(null)
              }}
            >
              {t(($) => $['operation.back'])}
            </Button>
          )}
          {dashboards.isSuccess && dashboards.data.next_cursor && (
            <Button
              variant="secondary"
              disabled={dashboards.isFetching}
              onClick={() => {
                void setAfter(dashboards.data.next_cursor)
              }}
            >
              {t(($) => $['operation.more'])}
            </Button>
          )}
        </div>
      </section>
    </main>
  )
}

export function DashboardsPage() {
  return (
    <BusinessGate>
      {(access, scope) => <DashboardManagement access={access} scope={scope} />}
    </BusinessGate>
  )
}
