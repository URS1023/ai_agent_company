'use client'

import { useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import { buildIntegrationPath } from '@/app/components/integrations/routes'
import {
  currentWorkspaceAtom,
  isCurrentWorkspaceDatasetOperatorAtom,
} from '@/context/workspace-state'
import Link from '@/next/link'
import { InstalledApplications, WorkspaceApplications } from './application-lists'

const businessDirections = [
  { key: 'enterprise.ai', icon: 'i-ri-sparkling-line' },
  { key: 'enterprise.alerts', icon: 'i-ri-alarm-warning-line' },
  { key: 'enterprise.quality', icon: 'i-ri-checkbox-circle-line' },
  { key: 'enterprise.dashboards', icon: 'i-ri-bar-chart-box-line' },
] as const

export function EnterprisePortal() {
  const { t } = useTranslation('common')
  const workspace = useAtomValue(currentWorkspaceAtom)
  const datasetOperator = useAtomValue(isCurrentWorkspaceDatasetOperatorAtom)
  const shortcuts = [
    ...(!datasetOperator
      ? [{ href: '/apps', label: t(($) => $['menus.apps']), icon: 'i-ri-apps-2-line' }]
      : []),
    { href: '/datasets', label: t(($) => $['menus.datasets']), icon: 'i-ri-book-2-line' },
    {
      href: '/enterprise/sources',
      label: t(($) => $['enterprise.sources.title']),
      icon: 'i-ri-database-2-line',
    },
    {
      href: buildIntegrationPath('provider'),
      label: t(($) => $['mainNav.integrations']),
      icon: 'i-ri-plug-line',
    },
  ]

  return (
    <div className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <div className="mx-auto max-w-7xl space-y-10 px-4 py-8 sm:px-8 lg:px-12">
        <header className="space-y-6">
          <div className="flex min-w-0 flex-wrap items-center gap-2 system-xs-medium text-text-tertiary">
            <span aria-hidden className="i-ri-building-line h-4 w-4 shrink-0" />
            <span>{t(($) => $['enterprise.workspace'])}</span>
            <span className="min-w-0 truncate text-text-secondary" title={workspace.name}>
              {workspace.name}
            </span>
          </div>
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
              {t(($) => $['enterprise.title'])}
            </h1>
            <p className="mt-3 max-w-2xl system-md-regular text-text-secondary">
              {t(($) => $['enterprise.apps'])} · {t(($) => $['enterprise.devices.title'])}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {shortcuts.map((shortcut) => (
              <Link
                key={shortcut.href}
                href={shortcut.href}
                className="inline-flex items-center gap-2 rounded-lg border border-divider-subtle bg-background-default px-4 py-2 system-sm-medium text-text-secondary transition-colors hover:bg-background-section focus-visible:ring-2 focus-visible:ring-state-accent-solid focus-visible:outline-hidden motion-reduce:transition-none"
              >
                <span aria-hidden className={`${shortcut.icon} h-4 w-4 shrink-0`} />
                {shortcut.label}
                <span
                  aria-hidden
                  className="i-ri-arrow-right-up-line h-4 w-4 shrink-0 text-text-tertiary"
                />
              </Link>
            ))}
          </div>
        </header>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {businessDirections.map((direction) =>
            direction.key !== 'enterprise.ai' ? (
              <Link
                key={direction.key}
                href={
                  direction.key === 'enterprise.dashboards'
                    ? '/enterprise/dashboards'
                    : '/enterprise/devices'
                }
                className="min-w-0 rounded-xl border border-divider-subtle bg-background-default p-5 transition-colors hover:bg-background-section focus-visible:ring-2 focus-visible:ring-state-accent-solid motion-reduce:transition-none"
              >
                <span
                  aria-hidden
                  className={`${direction.icon} mb-5 block h-6 w-6 text-text-accent`}
                />
                <h2 className="system-md-semibold text-text-primary">
                  {t(($) => $[direction.key])}
                </h2>
                <p className="mt-2 system-xs-regular text-text-tertiary">
                  {t(
                    ($) =>
                      $[
                        direction.key === 'enterprise.dashboards'
                          ? 'operation.view'
                          : 'enterprise.devices.title'
                      ],
                  )}
                </p>
              </Link>
            ) : (
              <Link
                href="/enterprise/workbench"
                key={direction.key}
                className="min-w-0 rounded-xl border border-divider-subtle bg-background-default p-5 transition-colors hover:bg-background-section focus-visible:ring-2 focus-visible:ring-state-accent-solid motion-reduce:transition-none"
              >
                <span
                  aria-hidden
                  className={`${direction.icon} mb-5 block h-6 w-6 text-text-tertiary`}
                />
                <h2 className="system-md-semibold text-text-primary">
                  {t(($) => $[direction.key])}
                </h2>
                <p className="mt-2 inline-flex items-center gap-1.5 system-xs-regular text-text-tertiary">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-text-quaternary" />
                  {t(($) => $['enterprise.notConnected'])}
                </p>
              </Link>
            ),
          )}
        </div>
        {!datasetOperator && (
          <div key={workspace.id} className="space-y-8 border-t border-divider-subtle pt-8">
            <WorkspaceApplications />
            <InstalledApplications />
          </div>
        )}
      </div>
    </div>
  )
}
