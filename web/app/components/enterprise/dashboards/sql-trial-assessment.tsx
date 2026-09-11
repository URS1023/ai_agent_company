'use client'

import type { SqlTrialEvidence } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'

type Props = { evidence: SqlTrialEvidence; scope: readonly string[] }

export function SqlTrialAssessment({ evidence, scope }: Props) {
  const { t } = useTranslation('common')
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3">
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {t(($) => $['enterprise.sqlAssessment.open'])}
      </Button>
      {open && <AssessmentResult key={evidence.trial_id} evidence={evidence} scope={scope} />}
    </section>
  )
}

function AssessmentResult({ evidence, scope }: Props) {
  const { t } = useTranslation('common')
  const result = evidence.result
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.assessment.get.queryOptions(
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
          'sql-assessment',
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
  const assessment = query.data
  if (
    !assessment ||
    assessment.trial_id !== evidence.trial_id ||
    assessment.slot_id !== result.slot_id ||
    assessment.semantic_status !== 'not_reviewed' ||
    assessment.unit_status !== 'not_reviewed' ||
    (assessment.status === 'incompatible') !== assessment.issues.length > 0 ||
    (assessment.status === 'sample_compatible' &&
      (result.row_count === 0 || assessment.issues_truncated)) ||
    assessment.issues.some(
      (issue) => issue.row_index != null && issue.row_index >= result.row_count,
    )
  )
    return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  return (
    <div className="space-y-3">
      <p className="system-sm-semibold text-text-primary">
        {t(($) => $[`enterprise.sqlAssessment.${assessment.status}`])}
      </p>
      <p className="system-sm-regular text-text-secondary">
        {t(($) => $['enterprise.sqlAssessment.notice'])}
      </p>
      <ul className="space-y-2">
        {assessment.issues.map((issue) => (
          <li
            key={`${issue.code}:${issue.field}:${issue.row_index}`}
            className="flex flex-wrap gap-2"
          >
            <span>{t(($) => $[`enterprise.sqlAssessment.${issue.code}`])}</span>
            {issue.field && <code>{issue.field}</code>}
            {issue.row_index != null && <span>#{issue.row_index + 1}</span>}
          </li>
        ))}
      </ul>
      {assessment.issues_truncated && (
        <p role="status">{t(($) => $['enterprise.sqlAssessment.truncated'])}</p>
      )}
    </div>
  )
}
