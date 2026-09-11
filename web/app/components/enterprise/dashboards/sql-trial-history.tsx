'use client'

import type { SqlDraftInspection, SqlTrialEvidence } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { SqlDraftPreview } from './sql-draft-preview'
import { SqlSampleCheck } from './sql-sample-check'
import { SqlTrialAssessment } from './sql-trial-assessment'
import { SqlTrialPreview } from './sql-trial-preview'
import { inspectTrialResult } from './sql-trial-result'
import { SqlTrialTable } from './sql-trial-table'

type Props = { inspection: SqlDraftInspection; scope: readonly string[] }

export function SqlTrialHistory({ inspection, scope }: Props) {
  const { t } = useTranslation('common')
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3">
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {t(($) => $['enterprise.sqlTrial.history'])}
      </Button>
      {open && (
        <HistoryList
          key={`${inspection.draft.workspace_id}:${inspection.draft.draft_id}`}
          inspection={inspection}
          scope={scope}
        />
      )}
    </section>
  )
}

function HistoryList({ inspection, scope }: Props) {
  const { t } = useTranslation('common')
  const [cursors, setCursors] = useState<string[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [previewTrials, setPreviewTrials] = useState<SqlTrialEvidence[]>([])
  const draft = inspection.draft
  const after = cursors.at(-1)
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.trials.get.queryOptions({
      input: {
        params: { dashboard_id: draft.dashboard_id, draft_id: draft.draft_id },
        query: { after, limit: 20 },
      },
      queryKey: [
        ...scope,
        'sql-trials',
        draft.workspace_id,
        draft.dashboard_id,
        draft.draft_id,
        after ?? null,
      ],
      retry: false,
      refetchOnMount: 'always',
      refetchOnWindowFocus: false,
    }),
  )
  const items = query.data?.items ?? []
  const next = query.data?.next_cursor
  const mismatch =
    items.length > 20 ||
    new Set(items.map((item) => item.trial_id)).size !== items.length ||
    items.some(
      (item) =>
        item.workspace_id !== draft.workspace_id ||
        item.dashboard_id !== draft.dashboard_id ||
        item.draft_id !== draft.draft_id ||
        item.trial_id === after,
    ) ||
    Boolean(next && (next !== items.at(-1)?.trial_id || cursors.includes(next)))
  if (query.isFetching || query.isPending)
    return (
      <div className="h-24 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none" />
    )
  if (query.isError)
    return (
      <BusinessFailure
        retry={() => {
          void query.refetch()
        }}
      />
    )
  if (mismatch) return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  return (
    <div className="space-y-3">
      {selected ? (
        <>
          <Button variant="secondary" onClick={() => setSelected(null)}>
            {t(($) => $['operation.back'])}
          </Button>
          <HistoryDetail
            key={selected}
            inspection={inspection}
            scope={scope}
            trialId={selected}
            onSelectPreview={(evidence) =>
              setPreviewTrials((current) => {
                const remaining = current.filter(
                  (item) => item.result.slot_id !== evidence.result.slot_id,
                )
                return remaining.length >= 100 ? current : [...remaining, evidence]
              })
            }
          />
        </>
      ) : (
        <>
          {items.length === 0 && <p>{t(($) => $.noData)}</p>}
          <ul className="space-y-2">
            {items.map((item) => (
              <li key={item.trial_id} className="flex flex-wrap items-center gap-3">
                <Button variant="secondary" onClick={() => setSelected(item.trial_id)}>
                  {item.trial_id}
                </Button>
                <span>{item.actor_id}</span>
                <time dateTime={item.recorded_at}>{item.recorded_at}</time>
              </li>
            ))}
          </ul>
          <div className="flex gap-3">
            <Button
              variant="secondary"
              disabled={cursors.length === 0}
              onClick={() => setCursors((value) => value.slice(0, -1))}
            >
              {t(($) => $['pagination.previous'])}
            </Button>
            <Button
              variant="secondary"
              disabled={!next}
              onClick={() => {
                if (next) setCursors((value) => [...value, next])
              }}
            >
              {t(($) => $['pagination.next'])}
            </Button>
          </div>
        </>
      )}
      {previewTrials.length > 0 && (
        <div className="space-y-3">
          <ul className="space-y-2">
            {previewTrials.map((item) => (
              <li key={item.trial_id} className="flex flex-wrap items-center gap-3">
                <code>{item.result.slot_id}</code>
                <span>{item.trial_id}</span>
                <Button
                  variant="secondary"
                  onClick={() =>
                    setPreviewTrials((current) =>
                      current.filter((value) => value.trial_id !== item.trial_id),
                    )
                  }
                >
                  {t(($) => $['enterprise.sqlPreview.groupRemove'])}
                </Button>
              </li>
            ))}
          </ul>
          <SqlDraftPreview inspection={inspection} selected={previewTrials} />
        </div>
      )}
    </div>
  )
}

function HistoryDetail({
  inspection,
  scope,
  trialId,
  onSelectPreview,
}: Props & { trialId: string; onSelectPreview: (evidence: SqlTrialEvidence) => void }) {
  const { t } = useTranslation('common')
  const draft = inspection.draft
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.get.queryOptions(
      {
        input: {
          params: { dashboard_id: draft.dashboard_id, draft_id: draft.draft_id, trial_id: trialId },
        },
        queryKey: [
          ...scope,
          'sql-trial',
          draft.workspace_id,
          draft.dashboard_id,
          draft.draft_id,
          trialId,
        ],
        retry: false,
        refetchOnMount: 'always',
        refetchOnWindowFocus: false,
      },
    ),
  )
  if (query.isFetching || query.isPending)
    return (
      <div className="h-24 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none" />
    )
  if (query.isError)
    return (
      <BusinessFailure
        retry={() => {
          void query.refetch()
        }}
      />
    )
  const evidence = query.data
  const result =
    evidence?.trial_id === trialId
      ? inspectTrialResult(evidence.result, inspection, evidence.result.slot_id)
      : null
  if (!result || !evidence)
    return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  return (
    <div className="space-y-3">
      <SqlTrialAssessment evidence={evidence} scope={scope} />
      <SqlSampleCheck evidence={evidence} scope={scope} />
      <Button variant="secondary" onClick={() => onSelectPreview(evidence)}>
        {t(($) => $['enterprise.sqlPreview.groupAdd'])}
      </Button>
      <SqlTrialPreview
        evidence={evidence}
        scope={scope}
        fieldMap={
          draft.proposals.slots.find((slot) => slot.slot_id === result.slot_id)?.field_map ?? {}
        }
      />
      <SqlTrialTable key={trialId} result={result} />
    </div>
  )
}
