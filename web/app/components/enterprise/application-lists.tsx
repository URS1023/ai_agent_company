'use client'

import type { App } from '@/types/app'
import type { ResourceMaintainerPermissionOptions } from '@/utils/permission'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import { buildInstalledAppPath } from '@/app/components/explore/installed-app/routes'
import { userProfileIdAtom } from '@/context/account-state'
import { workspacePermissionKeysAtom } from '@/context/permission-state'
import { systemFeaturesQueryOptions } from '@/features/system-features/client'
import Link from '@/next/link'
import { consoleQuery } from '@/service/client'
import { normalizeAppPagination } from '@/service/use-apps'
import { getRedirectionPath } from '@/utils/app-redirection'
import { getAppACLCapabilities, hasOnlyAppPreviewPermission } from '@/utils/permission'
import { ResourceSection } from './resource-section'
import { MAX_APPS_PAGE, useAppsPage } from './use-apps-page'

const resourceCardClassName =
  'flex min-w-0 items-center gap-3 rounded-xl border border-divider-subtle bg-background-default p-4'
const resourceLinkClassName = `${resourceCardClassName} transition-colors hover:border-divider-regular hover:bg-background-section focus-visible:ring-2 focus-visible:ring-state-accent-solid focus-visible:outline-hidden motion-reduce:transition-none`

function WorkspaceApplication({
  app,
  permissionOptions,
}: {
  app: App
  permissionOptions: ResourceMaintainerPermissionOptions
}) {
  const { t } = useTranslation('common')
  const options = { ...permissionOptions, resourceMaintainer: app.maintainer ?? app.created_by }
  const capabilities = getAppACLCapabilities(app.permission_keys, options)
  const canOpenConsole =
    capabilities.canAccessLayout ||
    capabilities.canMonitor ||
    capabilities.canAccessLogAndAnnotation ||
    capabilities.canAccessConfig
  const content = (
    <>
      <span aria-hidden className="i-ri-apps-2-line h-5 w-5 shrink-0 text-text-accent" />
      <div className="min-w-0 flex-1">
        <p className="truncate system-sm-semibold text-text-primary" title={app.name}>
          {app.name}
        </p>
        {app.description && (
          <p className="mt-1 line-clamp-2 system-xs-regular text-text-tertiary">
            {app.description}
          </p>
        )}
        {!canOpenConsole && hasOnlyAppPreviewPermission(app.permission_keys) && (
          <p className="mt-1 system-xs-regular text-text-tertiary">
            {t(($) => $['enterprise.previewOnly'])}
          </p>
        )}
      </div>
      {canOpenConsole && (
        <span
          aria-hidden
          className="i-ri-arrow-right-up-line h-4 w-4 shrink-0 text-text-tertiary"
        />
      )}
    </>
  )

  return canOpenConsole ? (
    <Link href={getRedirectionPath(app, options)} className={resourceLinkClassName}>
      {content}
    </Link>
  ) : (
    <div className={resourceCardClassName}>{content}</div>
  )
}

export function WorkspaceApplications() {
  const { t } = useTranslation('common')
  const [page, setPage] = useAppsPage()
  const currentUserId = useAtomValue(userProfileIdAtom)
  const workspacePermissionKeys = useAtomValue(workspacePermissionKeysAtom)
  const { data: systemFeatures } = useQuery(systemFeaturesQueryOptions())
  const apps = useQuery(
    consoleQuery.apps.get.queryOptions({
      input: { query: { page, limit: 12, sort_by: 'last_modified' } },
      select: normalizeAppPagination,
    }),
  )

  return (
    <div className="space-y-4">
      <ResourceSection
        title={t(($) => $['enterprise.apps'])}
        pending={apps.isPending}
        failed={apps.isError}
        empty={apps.data?.data.length === 0}
        retry={() => {
          void apps.refetch()
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {apps.data?.data.map((app) => (
            <WorkspaceApplication
              key={app.id}
              app={app}
              permissionOptions={{
                currentUserId,
                workspacePermissionKeys,
                isRbacEnabled: systemFeatures?.rbac_enabled,
              }}
            />
          ))}
        </div>
      </ResourceSection>
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="secondary"
          size="small"
          disabled={page === 1 || apps.isFetching}
          onClick={() => {
            void setPage((value) => value - 1)
          }}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <span
          aria-label={t(($) => $['pagination.pageNumber'])}
          className="min-w-8 text-center system-sm-medium text-text-secondary"
        >
          {page}
        </span>
        <Button
          variant="secondary"
          size="small"
          disabled={
            page === MAX_APPS_PAGE || !apps.data?.has_more || apps.isFetching || apps.isError
          }
          onClick={() => {
            void setPage((value) => value + 1)
          }}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </div>
    </div>
  )
}

export function InstalledApplications({ workbench = false }: { workbench?: boolean } = {}) {
  const { t } = useTranslation('common')
  const installed = useQuery(consoleQuery.installedApps.get.queryOptions({ input: {} }))

  return (
    <ResourceSection
      title={t(($) => $['enterprise.installed'])}
      pending={installed.isPending}
      failed={installed.isError}
      empty={installed.data?.installed_apps.length === 0}
      retry={() => {
        void installed.refetch()
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {installed.data?.installed_apps.map((installation) => (
          <div key={installation.id} className="space-y-2">
            <Link href={buildInstalledAppPath(installation.id)} className={resourceLinkClassName}>
              <span aria-hidden className="i-ri-chat-3-line h-5 w-5 shrink-0 text-text-accent" />
              <span
                className="min-w-0 flex-1 truncate system-sm-semibold text-text-primary"
                title={installation.app.name ?? undefined}
              >
                {installation.app.name || t(($) => $['menus.appDetail'])}
              </span>
              <span
                aria-hidden
                className="i-ri-arrow-right-up-line h-4 w-4 shrink-0 text-text-tertiary"
              />
            </Link>
            {workbench &&
              ['chat', 'agent-chat', 'advanced-chat'].includes(installation.app.mode ?? '') && (
                <Link
                  href={`/enterprise/workbench/${encodeURIComponent(installation.id)}`}
                  className="inline-flex system-sm-medium text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
                >
                  {t(($) => $['enterprise.ai'])}
                </Link>
              )}
          </div>
        ))}
      </div>
    </ResourceSection>
  )
}
