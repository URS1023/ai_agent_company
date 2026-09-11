import type { ReactNode } from 'react'
import { Button } from '@langgenius/dify-ui/button'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'

export function ResourceSection({
  title,
  pending,
  failed,
  empty,
  retry,
  children,
}: {
  title: string
  pending: boolean
  failed: boolean
  empty: boolean
  retry: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const { t } = useTranslation('common')

  return (
    <section aria-labelledby={titleId} aria-busy={pending} className="min-w-0 space-y-4">
      <h2 id={titleId} className="text-xl font-semibold text-text-primary">
        {title}
      </h2>
      {pending ? (
        <div aria-hidden className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <div
              key={index}
              className="h-24 animate-pulse rounded-xl border border-divider-subtle bg-background-section motion-reduce:animate-none"
            />
          ))}
        </div>
      ) : failed ? (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-divider-subtle bg-background-section p-5"
        >
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.loadError'])}
          </p>
          <Button variant="secondary" onClick={retry}>
            {t(($) => $['operation.retry'])}
          </Button>
        </div>
      ) : empty ? (
        <div className="rounded-xl border border-dashed border-divider-regular p-8 text-center system-sm-regular text-text-tertiary">
          {t(($) => $.noData)}
        </div>
      ) : (
        children
      )}
    </section>
  )
}
