'use client'

import type { MessageScope } from '@enterprise/business-contracts/types'
import { zMessageScope } from '@enterprise/business-contracts/zod'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { createParser, useQueryState } from 'nuqs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { userProfileIdAtom } from '@/context/account-state'
import {
  currentWorkspaceIdAtom,
  isCurrentWorkspaceDatasetOperatorAtom,
} from '@/context/workspace-state'
import Link from '@/next/link'
import { ResourceSection } from '../resource-section'
import { WorkbenchBranchDirectory } from './branch-directory'
import { loadWorkbenchResume } from './resume'
import { WorkbenchSession } from './session'

const branchParser = createParser({
  parse: (value: string) => {
    const parsed = zMessageScope.shape.branch_id.safeParse(value)
    return parsed.success ? parsed.data : null
  },
  serialize: (value: string) => value,
}).withOptions({ history: 'push', shallow: true })

export function WorkbenchHistory({ installedAppId }: { installedAppId: string }) {
  const actor = useAtomValue(userProfileIdAtom)
  const workspace = useAtomValue(currentWorkspaceIdAtom)
  const restricted = useAtomValue(isCurrentWorkspaceDatasetOperatorAtom)
  const { t } = useTranslation('common')
  if (restricted) return <p role="alert">{t(($) => $['enterprise.devices.accessDenied'])}</p>
  const scope = { actor_id: actor, workspace_id: workspace, installed_app_id: installedAppId }
  return <History key={JSON.stringify(scope)} scope={scope} />
}

function History({ scope }: { scope: Omit<MessageScope, 'branch_id'> }) {
  const [branchId, setBranchId] = useQueryState('branch', branchParser)
  const selected = branchId === null ? null : { ...scope, branch_id: branchId }
  const { t } = useTranslation('common')
  const title = t(($) => $['promptEditor.history.item.title'])
  return (
    <main className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-8">
        <nav className="flex gap-4">
          <Link
            href={`/enterprise/workbench/${encodeURIComponent(scope.installed_app_id)}`}
            className="system-sm-medium text-text-accent focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['enterprise.ai'])}
          </Link>
          {selected && (
            <Button
              variant="secondary"
              onClick={() => {
                void setBranchId(null)
              }}
            >
              {t(($) => $['operation.back'])}
            </Button>
          )}
        </nav>
        {selected ? (
          <Resume key={JSON.stringify(selected)} scope={selected} title={title} />
        ) : (
          <WorkbenchBranchDirectory
            scope={scope}
            title={title}
            onSelect={(branch) => {
              void setBranchId(branch.scope.branch_id)
            }}
          />
        )}
      </div>
    </main>
  )
}

function Resume({ scope, title }: { scope: MessageScope; title: string }) {
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof loadWorkbenchResume>> | null>(
    null,
  )
  const query = useQuery({
    queryKey: ['enterprise-workbench-resume', scope],
    queryFn: ({ signal }) => loadWorkbenchResume(scope, signal),
    enabled: !snapshot,
    retry: false,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
  if (!snapshot && query.data && !query.isFetching && !query.isError) setSnapshot(query.data)
  return (
    <ResourceSection
      title={title}
      pending={!snapshot && (query.isPending || query.isFetching)}
      failed={!snapshot && query.isError}
      empty={false}
      retry={() => {
        void query.refetch()
      }}
    >
      {snapshot && <WorkbenchSession {...snapshot} title={title} />}
    </ResourceSection>
  )
}
