'use client'

import type { DashboardView, SourceView } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Field, FieldControl, FieldLabel } from '@langgenius/dify-ui/field'
import { Form } from '@langgenius/dify-ui/form'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useQueryState } from 'nuqs'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { SavedSqlDraft } from './saved-sql-draft'
import { SqlProposalCards } from './sql-proposal-cards'

export function SqlGeneration({
  view,
  scope,
  workspaceId,
}: {
  view: DashboardView
  scope: readonly string[]
  workspaceId: string
}) {
  const { t } = useTranslation('common')
  const [draftId, setDraftId] = useQueryState('sqlDraft')
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<SourceView | null>(null)
  const [prompt, setPrompt] = useState('')
  const [mismatch, setMismatch] = useState(false)
  const submittedRef = useRef(false)
  const sources = useQuery(
    consoleQuery.business.sources.get.queryOptions({
      input: { query: { offset: page * 20, limit: 20 } },
      queryKey: [...scope, 'sources', 'sql-generation', page],
      enabled: open,
      retry: false,
      refetchOnWindowFocus: false,
    }),
  )
  const invalidSources =
    sources.data?.items.some((item) => item.workspace_id !== workspaceId) ?? false
  const eligible = invalidSources
    ? []
    : (sources.data?.items.filter((item) => item.connection.kind === 'db') ?? [])
  const current = eligible.find(
    (item) =>
      item.source_id === selected?.source_id && item.source_revision === selected.source_revision,
  )
  const generation = useMutation(
    consoleQuery.business.dashboards.byDashboardId.sqlProposals.post.mutationOptions({
      retry: false,
      onSuccess: (receipt, request) => {
        if (
          receipt.status !== 'draft' ||
          receipt.dashboard_id !== view.id ||
          receipt.dashboard_revision !== request.body.expected_revision ||
          receipt.design_identity !== request.body.expected_design_identity ||
          receipt.source_id !== request.body.source_id ||
          receipt.source_revision !== selected?.source_revision
        )
          setMismatch(true)
      },
      onSettled: () => {
        submittedRef.current = false
      },
    }),
  )
  const clearDraft = () => {
    generation.reset()
    setMismatch(false)
  }
  return (
    <section className="space-y-4 rounded-xl border border-divider-regular p-4">
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {t(($) => $['enterprise.sqlGeneration.open'])}
      </Button>
      {open && (
        <>
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.sqlGeneration.draftNotice'])}
          </p>
          <Form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              if (
                submittedRef.current ||
                !current ||
                !prompt.trim() ||
                prompt.trim().length > 8000 ||
                sources.isFetching ||
                sources.isError
              )
                return
              submittedRef.current = true
              setMismatch(false)
              generation.mutate({
                params: { dashboard_id: view.id },
                body: {
                  expected_revision: view.revision,
                  expected_design_identity: view.design_identity,
                  source_id: current.source_id,
                  prompt: prompt.trim(),
                },
                headers: { Origin: window.location.origin },
              })
            }}
          >
            <fieldset disabled={generation.isPending} className="space-y-3">
              <legend className="system-sm-semibold text-text-primary">
                {t(($) => $['enterprise.devices.source'])}
              </legend>
              <p className="system-xs-regular text-text-tertiary">
                {t(($) => $['enterprise.sqlGeneration.sourceNotice'])}
              </p>
              {sources.isPending && (
                <div
                  className="h-12 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
                  aria-busy="true"
                />
              )}
              {sources.isError && (
                <BusinessFailure
                  retry={() => {
                    void sources.refetch()
                  }}
                />
              )}
              {invalidSources && (
                <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>
              )}
              <div className="flex flex-wrap gap-2">
                {eligible.map((item) => (
                  <Button
                    type="button"
                    variant="secondary"
                    key={item.source_id}
                    aria-pressed={current?.source_id === item.source_id}
                    onClick={() => {
                      setSelected(item)
                      clearDraft()
                    }}
                  >
                    {item.name}
                  </Button>
                ))}
              </div>
              {sources.isSuccess && eligible.length === 0 && !invalidSources && (
                <p>{t(($) => $['enterprise.setup.noSources'])}</p>
              )}
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  disabled={page === 0 || sources.isFetching}
                  onClick={() => {
                    setPage((value) => value - 1)
                    setSelected(null)
                    clearDraft()
                  }}
                >
                  {t(($) => $['operation.back'])}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={
                    !sources.data || (page + 1) * 20 >= sources.data.total || sources.isFetching
                  }
                  onClick={() => {
                    setPage((value) => value + 1)
                    setSelected(null)
                    clearDraft()
                  }}
                >
                  {t(($) => $['pagination.next'])}
                </Button>
              </div>
              <Field>
                <FieldLabel>{t(($) => $['enterprise.sqlGeneration.prompt'])}</FieldLabel>
                <FieldControl
                  render={<textarea rows={4} />}
                  value={prompt}
                  maxLength={8000}
                  onChange={(event) => {
                    setPrompt(event.target.value)
                    clearDraft()
                  }}
                />
              </Field>
              <Button
                type="submit"
                disabled={
                  !current ||
                  !prompt.trim() ||
                  generation.isPending ||
                  sources.isFetching ||
                  sources.isError
                }
                loading={generation.isPending}
              >
                {t(($) => $['enterprise.sqlGeneration.generate'])}
              </Button>
            </fieldset>
          </Form>
          {generation.isError && (
            <p role="alert" className="system-sm-regular text-text-warning">
              {t(($) => $['enterprise.sqlGeneration.error'])}
            </p>
          )}
          {mismatch && <p role="alert">{t(($) => $['enterprise.sources.responseMismatch'])}</p>}
          {generation.isSuccess && !mismatch && (
            <div className="space-y-3" aria-live="polite">
              <Button
                variant="secondary"
                onClick={() => {
                  void setDraftId(generation.data.draft_id)
                }}
              >
                {t(($) => $['enterprise.sqlGeneration.saved'])}
              </Button>
              {!draftId && <SqlProposalCards proposals={generation.data.proposals} />}
            </div>
          )}
        </>
      )}
      {draftId && (
        <SavedSqlDraft
          draftId={draftId}
          dashboardId={view.id}
          workspaceId={workspaceId}
          scope={scope}
          onClose={() => {
            void setDraftId(null)
          }}
        />
      )}
    </section>
  )
}
