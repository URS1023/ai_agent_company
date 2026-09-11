'use client'

import type { BranchContext, MessageScope } from '@enterprise/business-contracts/types'
import { zBranchPage } from '@enterprise/business-contracts/zod'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { ResourceSection } from '../resource-section'

type Props = {
  scope: Omit<MessageScope, 'branch_id'>
  title: string
  onSelect: (branch: BranchContext) => void
}
const PAGE_SIZE = 50

export function WorkbenchBranchDirectory(props: Props) {
  return <Directory key={JSON.stringify(props.scope)} {...props} />
}

function Directory({ scope, title, onSelect }: Props) {
  const { t } = useTranslation('common')
  const [cursors, setCursors] = useState<string[]>([])
  const after = cursors.at(-1)
  const page = useQuery(
    consoleQuery.business.workbench.apps.byInstalledAppId.branches.get.queryOptions({
      input: {
        params: { installed_app_id: scope.installed_app_id },
        query: { limit: PAGE_SIZE, ...(after === undefined ? {} : { after }) },
      },
      queryKey: [
        'enterprise-workbench-branches',
        scope.workspace_id,
        scope.actor_id,
        scope.installed_app_id,
        after ?? null,
      ],
      enabled: !!scope.workspace_id && !!scope.actor_id && !!scope.installed_app_id,
      retry: false,
      select: (response) => {
        const parsed = zBranchPage.safeParse(response)
        if (!parsed.success) throw new Error('Workbench directory unavailable')
        const value = parsed.data
        const ids = new Set<string>()
        for (const branch of value.items) {
          if (
            branch.scope.workspace_id !== scope.workspace_id ||
            branch.scope.actor_id !== scope.actor_id ||
            branch.scope.installed_app_id !== scope.installed_app_id ||
            branch.scope.branch_id === after ||
            ids.has(branch.scope.branch_id)
          )
            throw new Error('Workbench directory unavailable')
          ids.add(branch.scope.branch_id)
        }
        if (
          value.items.length > PAGE_SIZE ||
          (value.next_after != null &&
            (value.items.length !== PAGE_SIZE ||
              value.next_after !== value.items.at(-1)?.scope.branch_id ||
              cursors.includes(value.next_after)))
        )
          throw new Error('Workbench directory unavailable')
        return value
      },
    }),
  )

  return (
    <div className="space-y-4">
      <ResourceSection
        title={title}
        pending={page.isPending}
        failed={page.isError}
        empty={page.data?.items.length === 0}
        retry={() => {
          void page.refetch()
        }}
      >
        <ul className="space-y-2">
          {page.data?.items.map((branch) => (
            <li key={branch.scope.branch_id}>
              <Button variant="secondary" onClick={() => onSelect(branch)}>
                {branch.scope.branch_id}
              </Button>
            </li>
          ))}
        </ul>
      </ResourceSection>
      <div className="flex gap-2">
        <Button
          variant="secondary"
          disabled={cursors.length === 0 || page.isFetching}
          onClick={() => setCursors((previous) => previous.slice(0, -1))}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <Button
          variant="secondary"
          disabled={page.isError || page.isFetching || !page.data?.next_after}
          onClick={() => {
            const next = page.data?.next_after
            if (next && !page.isError && !page.isFetching)
              setCursors((previous) => [...previous, next])
          }}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </div>
    </div>
  )
}
