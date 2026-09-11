'use client'

import type { BusinessAccess } from '@enterprise/business-contracts/types'
import type { ReactNode } from 'react'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import { userProfileIdAtom } from '@/context/account-state'
import { currentWorkspaceAtom } from '@/context/workspace-state'
import { consoleQuery } from '@/service/client'

export function BusinessGate({
  children,
}: {
  children: (access: BusinessAccess, scope: readonly string[]) => ReactNode
}) {
  const { t } = useTranslation('common')
  const workspace = useAtomValue(currentWorkspaceAtom)
  const accountId = useAtomValue(userProfileIdAtom)
  const scope = ['enterprise-business', workspace.id, accountId]
  const access = useQuery(
    consoleQuery.business.me.get.queryOptions({
      queryKey: [...scope, 'me'],
      enabled: Boolean(workspace.id && accountId),
      retry: false,
    }),
  )
  if (access.isPending)
    return (
      <div
        aria-busy="true"
        className="m-8 h-40 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
      />
    )
  if (access.isError)
    return (
      <BusinessFailure
        retry={() => {
          void access.refetch()
        }}
      />
    )
  if (
    access.data.workspace_id !== workspace.id ||
    access.data.actor_id !== accountId ||
    !access.data.permissions.read
  )
    return (
      <p role="alert" className="p-8 text-text-secondary">
        {t(($) => $['enterprise.devices.accessDenied'])}
      </p>
    )
  return <div key={`${workspace.id}:${accountId}`}>{children(access.data, scope)}</div>
}

export function BusinessFailure({
  retry,
  mutation = false,
  reopen = false,
  message,
}: {
  retry: () => void
  mutation?: boolean
  reopen?: boolean
  message?: string
}) {
  const { t } = useTranslation('common')
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-divider-subtle bg-background-section p-4"
    >
      <p className="system-sm-regular text-text-secondary">
        {message ??
          t(($) => $[mutation ? 'enterprise.devices.writeError' : 'enterprise.devices.loadError'])}
        {reopen && (
          <span className="mt-2 block">{t(($) => $['enterprise.devices.reopenHint'])}</span>
        )}
      </p>
      <Button type="button" variant="secondary" onClick={retry}>
        {t(($) => $[reopen ? 'operation.close' : 'operation.retry'])}
      </Button>
    </div>
  )
}
