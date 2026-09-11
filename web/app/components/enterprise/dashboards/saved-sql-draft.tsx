'use client'

import { Button } from '@langgenius/dify-ui/button'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { SqlProposalCards } from './sql-proposal-cards'
import { SqlTrial } from './sql-trial'
import { SqlTrialHistory } from './sql-trial-history'

export function SavedSqlDraft({
  draftId,
  dashboardId,
  workspaceId,
  scope,
  onClose,
}: {
  draftId: string
  dashboardId: string
  workspaceId: string
  scope: readonly string[]
  onClose: () => void
}) {
  const { t } = useTranslation('common')
  const queryClient = useQueryClient()
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.get.queryOptions({
      input: { params: { dashboard_id: dashboardId, draft_id: draftId } },
      queryKey: [...scope, 'sql-draft', workspaceId, dashboardId, draftId],
      retry: false,
      refetchOnMount: 'always',
      refetchOnWindowFocus: false,
    }),
  )
  const draft = query.data?.draft
  const mismatch =
    draft &&
    (draft.workspace_id !== workspaceId ||
      draft.dashboard_id !== dashboardId ||
      draft.draft_id !== draftId ||
      draft.source.workspace_id !== workspaceId)
  return (
    <section
      className="space-y-3"
      aria-label={t(($) => $['enterprise.sqlGeneration.saved'])}
      aria-busy={query.isFetching}
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="system-sm-semibold text-text-primary">
          {t(($) => $['enterprise.sqlGeneration.saved'])}
        </h3>
        <Button variant="secondary" onClick={onClose}>
          {t(($) => $['operation.close'])}
        </Button>
      </div>
      {query.isFetching || query.isPending ? (
        <div className="h-32 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none" />
      ) : query.isError ? (
        <BusinessFailure
          retry={() => {
            void query.refetch()
          }}
        />
      ) : mismatch ? (
        <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
      ) : (
        draft && (
          <>
            <p className="system-sm-regular text-text-secondary">
              {t(($) => $['enterprise.sqlGeneration.draftNotice'])}
            </p>
            {query.data.requires_regeneration && (
              <p role="alert" className="system-sm-regular text-text-warning">
                {t(($) => $['enterprise.sqlGeneration.stale'])}
              </p>
            )}
            <dl className="system-sm-regular text-text-secondary">
              <dt>{t(($) => $['enterprise.sqlGeneration.prompt'])}</dt>
              <dd className="break-words whitespace-pre-wrap">{draft.prompt}</dd>
            </dl>
            <SqlProposalCards
              proposals={draft.proposals}
              renderTrial={(slotId) => (
                <SqlTrial
                  key={`${draft.workspace_id}:${draft.draft_id}:${draft.dashboard_revision}:${draft.design_identity}:${slotId}`}
                  inspection={query.data}
                  slotId={slotId}
                  onRecorded={() => {
                    void queryClient.invalidateQueries({
                      queryKey: [...scope, 'sql-trials', workspaceId, dashboardId, draftId],
                    })
                  }}
                />
              )}
            />
            <SqlTrialHistory inspection={query.data} scope={scope} />
          </>
        )
      )}
    </section>
  )
}
