'use client'

import type { SqlDraftInspection } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useMutation } from '@tanstack/react-query'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { inspectTrialResult } from './sql-trial-result'
import { SqlTrialTable } from './sql-trial-table'

export function SqlTrial({
  inspection,
  slotId,
  onRecorded,
}: {
  inspection: SqlDraftInspection
  slotId: string
  onRecorded?: () => void
}) {
  const { t } = useTranslation('common')
  const submittedRef = useRef(false)
  const draft = inspection.draft
  const trial = useMutation(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.trial.post.mutationOptions(
      {
        retry: false,
        onSuccess: (result) => {
          if (inspectTrialResult(result, inspection, slotId)) onRecorded?.()
        },
        onSettled: () => {
          submittedRef.current = false
        },
      },
    ),
  )
  const disabled =
    inspection.requires_regeneration ||
    inspection.current_dashboard_revision !== draft.dashboard_revision
  const result =
    trial.isSuccess && !disabled ? inspectTrialResult(trial.data, inspection, slotId) : null
  return (
    <div className="space-y-3" aria-busy={trial.isPending}>
      <Button
        variant="secondary"
        disabled={disabled || trial.isPending}
        onClick={() => {
          if (submittedRef.current || disabled) return
          submittedRef.current = true
          trial.mutate({
            params: { dashboard_id: draft.dashboard_id, draft_id: draft.draft_id },
            body: {
              expected_revision: draft.dashboard_revision,
              expected_design_identity: draft.design_identity,
              slot_id: slotId,
            },
            headers: { Origin: window.location.origin },
          })
        }}
      >
        {t(($) => $['enterprise.sqlTrial.run'])}
      </Button>
      {trial.isPending && (
        <div className="h-24 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none" />
      )}
      {trial.isError && <p role="alert">{t(($) => $['enterprise.sqlTrial.error'])}</p>}
      {trial.isSuccess && !result && !disabled && (
        <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
      )}
      {result && <SqlTrialTable key={trial.submittedAt} result={result} />}
    </div>
  )
}
