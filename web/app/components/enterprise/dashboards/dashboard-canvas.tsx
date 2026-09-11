'use client'

import type { DashboardView } from '@enterprise/business-contracts/types'
import type { DashboardRenderer } from './renderer-loader'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { env } from '@/env'
import { loadDashboardRenderer } from './renderer-loader'

function MountedCanvas({ view }: { view: DashboardView }) {
  const { t } = useTranslation('common')
  const containerRef = useRef<HTMLDivElement>(null)
  const handleRef = useRef<DashboardRenderer | null>(null)
  const latestRef = useRef(view)
  const revisionRef = useRef(view.revision)
  const [initial] = useState(view)
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading')

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const controller = new AbortController()
    let handle: DashboardRenderer | undefined
    void loadDashboardRenderer(
      element,
      initial,
      env.NEXT_PUBLIC_BASE_PATH ?? '',
      undefined,
      controller.signal,
    )
      .then((runtime) => {
        if (controller.signal.aborted) {
          runtime.dispose()
          return
        }
        handle = runtime
        handleRef.current = runtime
        if (latestRef.current.revision > initial.revision) {
          runtime.update(latestRef.current)
          revisionRef.current = latestRef.current.revision
        }
        setStatus('ready')
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus('failed')
      })
    return () => {
      controller.abort()
      handleRef.current = null
      handle?.dispose()
    }
  }, [initial])

  useEffect(() => {
    if (view.revision <= latestRef.current.revision) return
    latestRef.current = view
    const runtime = handleRef.current
    if (!runtime || view.revision <= revisionRef.current) return
    void Promise.resolve()
      .then(() => {
        if (handleRef.current !== runtime || view.revision <= revisionRef.current) return
        runtime.update(view)
        revisionRef.current = view.revision
      })
      .catch(() => {
        if (handleRef.current === runtime) setStatus('failed')
      })
  }, [view])

  return (
    <section
      aria-label={t(($) => $['enterprise.dashboards'])}
      aria-busy={status === 'loading'}
      className="relative aspect-video w-full overflow-hidden rounded-xl bg-background-section"
    >
      {status === 'loading' && (
        <div className="absolute inset-0 animate-pulse bg-background-section motion-reduce:animate-none" />
      )}
      {status === 'failed' && (
        <p
          role="alert"
          className="absolute inset-x-0 top-0 z-10 bg-background-default p-4 text-text-warning"
        >
          {t(($) => $['api.actionFailed'])}
        </p>
      )}
      <div ref={containerRef} className="h-full w-full" />
    </section>
  )
}

export function DashboardCanvas({ view }: { view: DashboardView }) {
  return (
    <MountedCanvas
      key={JSON.stringify([
        view.id,
        view.template_id,
        view.design_identity,
        view.renderer_build_id,
      ])}
      view={view}
    />
  )
}
