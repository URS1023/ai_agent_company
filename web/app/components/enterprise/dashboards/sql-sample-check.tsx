'use client'

import type { SqlTrialEvidence } from '@enterprise/business-contracts/types'
import { zSqlPolicySampleCheck } from '@enterprise/business-contracts/zod'
import { Button } from '@langgenius/dify-ui/button'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'

type Props = { evidence: SqlTrialEvidence; scope: readonly string[] }

export function SqlSampleCheck({ evidence, scope }: Props) {
  const { t } = useTranslation('common')
  const [open, setOpen] = useState(false)
  return (
    <section className="space-y-3">
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {t(($) => $['enterprise.sqlSampleCheck.open'])}
      </Button>
      {open && <SampleCheckResult key={evidence.trial_id} evidence={evidence} scope={scope} />}
    </section>
  )
}

function SampleCheckResult({ evidence, scope }: Props) {
  const { t } = useTranslation('common')
  const result = evidence.result
  const query = useQuery(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.sampleCheck.automatic.get.queryOptions(
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
          'sql-sample-check',
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
  const parsed = zSqlPolicySampleCheck.safeParse(query.data)
  const assessment = parsed.success ? parsed.data.result : undefined
  if (
    !parsed.success ||
    !assessment ||
    assessment.trial_id !== evidence.trial_id ||
    assessment.slot_id !== result.slot_id ||
    (assessment.status === 'sample_constraints_failed') !== assessment.issues.length > 0 ||
    (assessment.status === 'sample_constraints_passed' && assessment.issues_truncated) ||
    assessment.issues.some(
      (issue) => issue.row_index != null && issue.row_index >= result.row_count,
    )
  )
    return <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
  return (
    <div className="space-y-3">
      <p className="system-sm-semibold text-text-primary">
        {t(($) => $[`enterprise.sqlSampleCheck.${assessment.status}`])}
      </p>
      <p className="system-sm-regular text-text-secondary">
        {t(($) => $['enterprise.sqlSampleCheck.notice'])}
      </p>
      <p className="flex flex-wrap gap-2">
        <span>{t(($) => $['enterprise.sqlSampleCheck.policy'])}</span>
        <code>{parsed.data.policy_id}</code>
        <code>{parsed.data.policy_revision}</code>
      </p>
      <ul className="space-y-2">
        {assessment.issues.map((issue) => (
          <li
            key={`${issue.code}:${issue.field}:${issue.row_index}`}
            className="flex flex-wrap gap-2"
          >
            <span>{t(($) => $[`enterprise.sqlSampleCheck.${issue.code}`])}</span>
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
