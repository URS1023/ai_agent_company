'use client'

import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { createParser, useQueryState } from 'nuqs'
import { useTranslation } from 'react-i18next'
import Link from '@/next/link'
import { consoleQuery } from '@/service/client'
import { BusinessFailure, BusinessGate } from '../devices/access'
import { SourceEditor } from './source-editor'

const pageParser = createParser({
  parse: (value: string) => (/^[1-9]\d{0,4}$/.test(value) ? Number(value) : null),
  serialize: String,
}).withDefault(1)

export function SourcesPage() {
  return (
    <div className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <BusinessGate>{(_access, scope) => <SourceList scope={scope} />}</BusinessGate>
    </div>
  )
}

function SourceList({ scope }: { scope: readonly string[] }) {
  const { t } = useTranslation('common')
  const [page, setPage] = useQueryState(
    'page',
    pageParser.withOptions({ history: 'push', shallow: true, clearOnDefault: true }),
  )
  const capabilities = useQuery(
    consoleQuery.business.sources.capabilities.get.queryOptions({
      queryKey: [...scope, 'source-capabilities'],
      retry: false,
    }),
  )
  const query = { offset: (page - 1) * 20, limit: 20 }
  const sources = useQuery(
    consoleQuery.business.sources.get.queryOptions({
      input: { query },
      queryKey: [...scope, 'sources', 'list', query],
      retry: false,
    }),
  )
  const canEdit =
    !capabilities.isError &&
    !capabilities.isFetching &&
    capabilities.data?.can_manage === true &&
    capabilities.data.write_enabled
  const scopeMismatch =
    sources.data?.items.some((source) => source.workspace_id !== scope[1]) ?? false
  return (
    <main className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-8 lg:px-12">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-3">
          <Link
            href="/enterprise"
            className="system-sm-medium text-text-tertiary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['enterprise.title'])}
          </Link>
          <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
            {t(($) => $['enterprise.sources.title'])}
          </h1>
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.sources.subtitle'])}
          </p>
        </div>
        <SourceEditor scope={scope} canEdit={canEdit} />
      </header>
      {capabilities.isPending ? (
        <div
          aria-busy
          className="h-10 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
        />
      ) : capabilities.isError ? (
        <BusinessFailure
          retry={() => {
            void capabilities.refetch()
          }}
        />
      ) : (
        !canEdit && (
          <p
            role="status"
            className="rounded-xl bg-background-section p-4 system-sm-regular text-text-secondary"
          >
            {t(
              ($) =>
                $[
                  !capabilities.data.can_manage
                    ? 'enterprise.sources.readOnly'
                    : 'enterprise.sources.disabled'
                ],
            )}
          </p>
        )
      )}
      <section aria-label={t(($) => $['enterprise.sources.title'])} aria-busy={sources.isPending}>
        {sources.isPending ? (
          <div
            aria-hidden
            className="h-40 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
          />
        ) : sources.isError || scopeMismatch ? (
          <BusinessFailure
            retry={() => {
              void sources.refetch()
            }}
          />
        ) : sources.data.items.length === 0 ? (
          <p className="rounded-xl border border-dashed border-divider-regular p-12 text-center text-text-tertiary">
            {t(($) => $['enterprise.sources.empty'])}
          </p>
        ) : null}
        <div className="grid gap-4 lg:grid-cols-2">
          {!scopeMismatch &&
            sources.data?.items.map((source) => (
              <article
                key={source.source_id}
                className="min-w-0 space-y-5 rounded-xl border border-divider-subtle bg-background-default p-6"
              >
                <div className="flex items-start justify-between gap-3">
                  <h2 className="min-w-0 text-xl font-semibold break-words text-text-primary">
                    {source.name}
                  </h2>
                  <span className="rounded-md bg-background-section px-2 py-1 font-mono system-xs-regular text-text-secondary">
                    {source.connection.kind === 'db' ? source.connection.dialect : 'HTTP'}
                  </span>
                </div>
                <p className="system-sm-regular text-text-tertiary">
                  {t(($) => $['enterprise.sources.notTested'])} ·{' '}
                  {t(
                    ($) =>
                      $[
                        source.connection.credentials_configured
                          ? 'enterprise.sources.credentialsConfigured'
                          : 'enterprise.sources.credentialsMissing'
                      ],
                  )}
                </p>
                <SourceEditor
                  sourceId={source.source_id}
                  scope={scope}
                  canEdit={canEdit && !sources.isError && !sources.isFetching}
                />
              </article>
            ))}
        </div>
      </section>
      <nav
        aria-label={t(($) => $['pagination.pageNumber'])}
        className="flex items-center justify-between gap-3"
      >
        <Button
          variant="secondary"
          disabled={page <= 1 || sources.isFetching}
          onClick={() => {
            void setPage(page - 1)
          }}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <span className="font-mono text-text-tertiary">{page}</span>
        <Button
          variant="secondary"
          disabled={
            sources.isFetching ||
            sources.isError ||
            !sources.data ||
            query.offset + 20 >= sources.data.total ||
            page >= 99999
          }
          onClick={() => {
            void setPage(page + 1)
          }}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </nav>
    </main>
  )
}
