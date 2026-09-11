'use client'

import type { SqlTrialEvidence } from '@enterprise/business-contracts/types'
import { zSqlTrialPreview } from '@enterprise/business-contracts/zod'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { DashboardCanvas } from './dashboard-canvas'

type Props = {
  evidence: SqlTrialEvidence
  scope: readonly string[]
  fieldMap: Readonly<Record<string, string | undefined>>
}

export function SqlTrialPreview({ evidence, scope, fieldMap }: Props) {
  const { t } = useTranslation('common')
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3">
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {t(($) => $['enterprise.sqlPreview.open'])}
      </Button>
      {open && (
        <PreviewResult
          key={evidence.trial_id}
          evidence={evidence}
          scope={scope}
          fieldMap={fieldMap}
        />
      )}
    </section>
  )
}

function PreviewResult({ evidence, scope, fieldMap }: Props) {
  const { t } = useTranslation('common')
  const result = evidence.result
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.preview.get.queryOptions(
      {
        input: {
          params: {
            dashboard_id: result.dashboard_id,
            draft_id: result.draft_id,
            trial_id: evidence.trial_id,
          },
        },
        queryKey: [
          ...scope,
          'sql-trial-preview',
          result.workspace_id,
          result.dashboard_id,
          result.draft_id,
          evidence.trial_id,
        ],
        retry: false,
        refetchOnMount: 'always',
        refetchOnWindowFocus: false,
      },
    ),
  )
  if (query.isPending || query.isFetching)
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
  const parsed = zSqlTrialPreview.safeParse(query.data)
  if (!parsed.success)
    return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  const preview = parsed.data
  const fields = Object.entries(fieldMap)
  if (
    preview.workspace_id !== result.workspace_id ||
    preview.dashboard_id !== result.dashboard_id ||
    preview.draft_id !== result.draft_id ||
    preview.trial_id !== evidence.trial_id ||
    preview.dashboard_revision !== result.dashboard_revision ||
    preview.design_identity !== result.design_identity ||
    preview.slot.slot_id !== result.slot_id ||
    preview.slot.rows.length !== result.row_count ||
    fields.length === 0 ||
    fields.some(([, column]) => typeof column !== 'string' || !result.columns.includes(column)) ||
    preview.slot.rows.some(
      (row, index) =>
        Object.keys(row).length !== fields.length ||
        fields.some(([field, column]) => {
          if (typeof column !== 'string') return true
          const original = result.rows[index]?.[result.columns.indexOf(column)]
          return (
            !original || row[field]?.kind !== original.kind || row[field]?.value !== original.value
          )
        }),
    )
  )
    return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  // This view exists only inside this preview canvas; it is never written to the dashboard cache.
  const batchId = `preview-${preview.trial_id}`
  return (
    <div className="space-y-3">
      <p role="status" className="system-sm-regular text-text-secondary">
        {t(($) => $['enterprise.sqlPreview.notice'])}
      </p>
      <DashboardCanvas
        key={query.dataUpdatedAt}
        view={{
          id: preview.dashboard_id,
          revision: preview.dashboard_revision,
          template_id: preview.template_id,
          design_identity: preview.design_identity,
          renderer_build_id: preview.renderer_build_id,
          status: 'ready',
          current: { batch_id: batchId, slots: [preview.slot] },
          last_attempt_id: batchId,
          failures: [],
        }}
      />
    </div>
  )
}
