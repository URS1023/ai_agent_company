import type { SqlProposals } from '@enterprise/business-contracts/types'
import type { ReactNode } from 'react'
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from '@langgenius/dify-ui/collapsible'
import { useTranslation } from 'react-i18next'

export function SqlProposalCards({
  proposals,
  renderTrial,
}: {
  proposals: SqlProposals
  renderTrial?: (slotId: string) => ReactNode
}) {
  const { t } = useTranslation('common')
  return (
    <div className="space-y-3">
      {proposals.slots.map((slot) => (
        <article key={slot.slot_id} className="space-y-2 rounded-lg bg-background-section p-4">
          <h3 className="system-sm-semibold text-text-primary">{slot.slot_id}</h3>
          <dl className="system-sm-regular text-text-secondary">
            <dt>{t(($) => $['enterprise.sqlGeneration.metric'])}</dt>
            <dd>{slot.metric_definition}</dd>
            <dt>{t(($) => $['enterprise.sqlGeneration.time'])}</dt>
            <dd>{slot.time_definition}</dd>
          </dl>
          <Collapsible>
            <CollapsibleTrigger>
              {t(($) => $['enterprise.sqlGeneration.advanced'])}
            </CollapsibleTrigger>
            <CollapsiblePanel>
              <div className="space-y-3 py-3">
                <pre className="overflow-auto break-words whitespace-pre-wrap text-text-primary">
                  <code>{slot.sql}</code>
                </pre>
                <dl className="system-sm-regular text-text-secondary">
                  <dt>{t(($) => $['enterprise.sqlGeneration.mapping'])}</dt>
                  <dd>
                    <pre className="overflow-auto break-words whitespace-pre-wrap">
                      {JSON.stringify(slot.field_map, null, 2)}
                    </pre>
                  </dd>
                </dl>
              </div>
            </CollapsiblePanel>
          </Collapsible>
          {renderTrial?.(slot.slot_id)}
        </article>
      ))}
    </div>
  )
}
