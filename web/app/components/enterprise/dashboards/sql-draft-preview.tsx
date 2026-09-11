'use client'

import type { SqlDraftInspection, SqlTrialEvidence } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useMutation } from '@tanstack/react-query'
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { DashboardCanvas } from './dashboard-canvas'
import { inspectDraftPreview } from './sql-draft-preview-result'
import { inspectTrialResult } from './sql-trial-result'

type Props = { inspection: SqlDraftInspection; selected: readonly SqlTrialEvidence[] }

export function SqlDraftPreview(props: Props) {
  return (
    <SelectedPreview
      key={JSON.stringify([
        props.inspection.draft.workspace_id,
        props.inspection.draft.draft_id,
        props.selected.map((item) => item.trial_id),
      ])}
      {...props}
    />
  )
}

function SelectedPreview({ inspection, selected }: Props) {
  const { t } = useTranslation('common')
  const submittedRef = useRef(false)
  const request = useMutation(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.preview.post.mutationOptions(
      {
        retry: false,
        onSettled: () => {
          submittedRef.current = false
        },
      },
    ),
  )
  const draft = inspection.draft
  const disabled =
    selected.length === 0 ||
    selected.length > 100 ||
    selected.some((item) => !inspectTrialResult(item.result, inspection, item.result.slot_id))
  const preview =
    request.isSuccess && !disabled ? inspectDraftPreview(request.data, inspection, selected) : null
  const first = preview?.items[0]
  const batchId = `preview-${request.submittedAt}`
  return (
    <section className="space-y-3" aria-busy={request.isPending}>
      <Button
        variant="secondary"
        disabled={disabled || request.isPending}
        onClick={() => {
          if (submittedRef.current || disabled) return
          submittedRef.current = true
          request.mutate({
            params: { dashboard_id: draft.dashboard_id, draft_id: draft.draft_id },
            body: { trial_ids: selected.map((item) => item.trial_id) },
            headers: { Origin: window.location.origin },
          })
        }}
      >
        {t(($) => $['enterprise.sqlPreview.groupRun'])}
      </Button>
      {request.isPending && (
        <div className="h-24 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none" />
      )}
      {request.isError && <p role="alert">{t(($) => $['api.actionFailed'])}</p>}
      {request.isSuccess && !preview && !disabled && (
        <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
      )}
      {preview && first && (
        <>
          <p role="status">{t(($) => $['enterprise.sqlPreview.groupNotice'])}</p>
          {preview.missing_required_slots.length > 0 && (
            <div>
              <p>{t(($) => $['enterprise.sqlPreview.missing'])}</p>
              <ul>
                {preview.missing_required_slots.map((slot) => (
                  <li key={slot}>
                    <code>{slot}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <DashboardCanvas
            key={request.submittedAt}
            view={{
              id: first.dashboard_id,
              revision: first.dashboard_revision,
              template_id: first.template_id,
              design_identity: first.design_identity,
              renderer_build_id: first.renderer_build_id,
              status: 'ready',
              current: { batch_id: batchId, slots: preview.items.map((item) => item.slot) },
              last_attempt_id: batchId,
              failures: [],
            }}
          />
        </>
      )}
    </section>
  )
}
