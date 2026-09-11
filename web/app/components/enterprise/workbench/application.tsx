'use client'

import type { ComponentProps } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { userProfileIdAtom } from '@/context/account-state'
import {
  currentWorkspaceIdAtom,
  isCurrentWorkspaceDatasetOperatorAtom,
} from '@/context/workspace-state'
import Link from '@/next/link'
import { consoleQuery } from '@/service/client'
import { ResourceSection } from '../resource-section'
import { restoreWorkbenchInputFiles } from './input-file-defaults'
import { decodeWorkbenchInputs } from './input-schema'
import { WorkbenchLauncher } from './launcher'

type Configuration = Pick<
  ComponentProps<typeof WorkbenchLauncher>,
  'fields' | 'uploadConfig' | 'configurationKey' | 'title' | 'initialFiles'
>

export function WorkbenchApplication({ installedAppId }: { installedAppId: string }) {
  const actor = useAtomValue(userProfileIdAtom)
  const workspace = useAtomValue(currentWorkspaceIdAtom)
  const restricted = useAtomValue(isCurrentWorkspaceDatasetOperatorAtom)
  const { t } = useTranslation('common')
  if (restricted) return <p role="alert">{t(($) => $['enterprise.devices.accessDenied'])}</p>
  return (
    <Application
      key={JSON.stringify([workspace, actor, installedAppId])}
      installedAppId={installedAppId}
      actor={actor}
      workspace={workspace}
    />
  )
}

function Application({
  installedAppId,
  actor,
  workspace,
}: {
  installedAppId: string
  actor: string
  workspace: string
}) {
  const { t } = useTranslation('common')
  const scopeKey = ['enterprise-workbench-config', workspace, actor, installedAppId]
  const [configuration, setConfiguration] = useState<Configuration | null>(null)
  const installed = useQuery(
    consoleQuery.installedApps.get.queryOptions({
      input: {},
      queryKey: [...scopeKey, 'installed'],
      enabled: !!actor && !!workspace && !configuration,
      retry: false,
    }),
  )
  const app = installed.data?.installed_apps.find((entry) => entry.id === installedAppId)
  const chat = !!app && ['chat', 'agent-chat', 'advanced-chat'].includes(app.app.mode ?? '')
  const parameters = useQuery(
    consoleQuery.installedApps.byInstalledAppId.parameters.get.queryOptions({
      input: { params: { installed_app_id: installedAppId } },
      queryKey: [...scopeKey, 'parameters'],
      enabled: chat && !configuration,
      retry: false,
    }),
  )
  const fields = useMemo(() => {
    if (!parameters.data) return null
    try {
      return decodeWorkbenchInputs(parameters.data.user_input_form)
    } catch {
      return null
    }
  }, [parameters.data])
  const initialFiles = useMemo(() => {
    if (!fields) return null
    try {
      return restoreWorkbenchInputFiles(fields)
    } catch {
      return null
    }
  }, [fields])
  const needsUpload =
    fields?.some(
      (field) => !field.hidden && (field.type === 'file' || field.type === 'file-list'),
    ) ?? false
  const upload = useQuery(
    consoleQuery.files.upload.get.queryOptions({
      input: {},
      queryKey: [...scopeKey, 'upload'],
      enabled: needsUpload && !configuration,
      retry: false,
    }),
  )
  const pending =
    installed.isPending || (chat && parameters.isPending) || (needsUpload && upload.isPending)
  const failed =
    installed.isError ||
    (!installed.isPending && !chat) ||
    parameters.isError ||
    (!!parameters.data && !fields) ||
    (!!fields && !initialFiles) ||
    (needsUpload && upload.isError)
  // Adopt one editing configuration, not a live mirror of query cache updates. Native
  // authorization/preflight still runs for every server action; scope changes remount us.
  if (!configuration && !pending && !failed && app && fields && initialFiles && parameters.data) {
    setConfiguration({
      fields,
      initialFiles,
      uploadConfig: upload.data,
      configurationKey: JSON.stringify(parameters.data),
      title: app.app.name ?? '',
    })
  }

  return (
    <main className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-8">
        <nav className="flex gap-4">
          <Link
            href={`/enterprise/workbench/${encodeURIComponent(installedAppId)}/history`}
            className="system-sm-medium text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['promptEditor.history.item.title'])}
          </Link>
          <Link
            href="/enterprise/workbench"
            className="system-sm-medium text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['enterprise.ai'])}
          </Link>
          {(app || configuration) && (
            <Link
              href={`/installed/${encodeURIComponent(installedAppId)}`}
              className="system-sm-medium text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
            >
              {t(($) => $['enterprise.workbench.openNative'])}
            </Link>
          )}
        </nav>
        <ResourceSection
          title={t(($) => $['operation.params'])}
          pending={!configuration && pending}
          failed={!configuration && failed}
          empty={false}
          retry={() => {
            void installed.refetch()
            if (chat) void parameters.refetch()
            if (needsUpload) void upload.refetch()
          }}
        >
          {configuration && (
            <WorkbenchLauncher
              scope={{ workspace_id: workspace, actor_id: actor, installed_app_id: installedAppId }}
              configurationKey={configuration.configurationKey}
              title={configuration.title || t(($) => $['menus.appDetail'])}
              fields={configuration.fields}
              initialFiles={configuration.initialFiles}
              uploadConfig={configuration.uploadConfig}
              labels={{
                submit: t(($) => $['operation.confirm']),
                required: t(($) => $['enterprise.workbench.requiredInput']),
                invalid: t(($) => $['enterprise.workbench.invalidInput']),
                pending: t(($) => $.loading),
                configMissing: t(($) => $['enterprise.loadError']),
                error: t(($) => $['enterprise.loadError']),
              }}
            />
          )}
        </ResourceSection>
      </div>
    </main>
  )
}
