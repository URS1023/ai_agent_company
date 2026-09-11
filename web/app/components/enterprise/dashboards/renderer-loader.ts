import type { DashboardView } from '@enterprise/business-contracts/types'
import build from './renderer-build.json'

export type DashboardRenderer = {
  update: (view: DashboardView) => void
  dispose: () => void
}

type Importer = (url: string) => Promise<unknown>

function isRenderer(value: unknown): value is DashboardRenderer {
  return (
    typeof value === 'object' &&
    value !== null &&
    'update' in value &&
    typeof value.update === 'function' &&
    'dispose' in value &&
    typeof value.dispose === 'function'
  )
}

export async function loadDashboardRenderer(
  element: HTMLElement,
  initial: DashboardView,
  basePath: string,
  importer: Importer = (url) => import(/* webpackIgnore: true */ /* @vite-ignore */ url),
  signal?: AbortSignal,
): Promise<DashboardRenderer> {
  if (signal?.aborted) throw new Error('dashboard_renderer_aborted')
  if (initial.renderer_build_id !== build.rendererBuildId)
    throw new Error('dashboard_renderer_unavailable')
  if (
    basePath &&
    (!basePath.startsWith('/') || basePath.startsWith('//') || /[?#\\]/.test(basePath))
  )
    throw new Error('dashboard_renderer_base_path_invalid')
  const prefix = `${basePath.replace(/\/$/, '')}/enterprise/renderers`
  const links: HTMLLinkElement[] = []
  const cleanups: (() => void)[] = []
  const clear = () => {
    cleanups.forEach((cleanup) => cleanup())
    links.forEach((link) => link.remove())
  }
  let abort = () => {}
  const aborted = new Promise<never>((_resolve, reject) => {
    abort = () => reject(new Error('dashboard_renderer_aborted'))
    signal?.addEventListener('abort', abort, { once: true })
  })
  try {
    await Promise.race([
      aborted,
      Promise.all(
        build.styles.map(
          (path) =>
            new Promise<void>((resolve, reject) => {
              const link = document.createElement('link')
              links.push(link)
              link.rel = 'stylesheet'
              link.href = `${prefix}${path}`
              link.dataset.dashboardRenderer = build.rendererBuildId
              let timer: ReturnType<typeof setTimeout>
              const finish = (error?: Error) => {
                clearTimeout(timer)
                link.onload = null
                link.onerror = null
                if (error) reject(error)
                else resolve()
              }
              timer = setTimeout(() => finish(new Error('dashboard_style_timeout')), 15_000)
              cleanups.push(() => {
                clearTimeout(timer)
                link.onload = null
                link.onerror = null
              })
              link.onload = () => finish()
              link.onerror = () => finish(new Error('dashboard_style_failed'))
              document.head.append(link)
            }),
        ),
      ),
    ])
    if (signal?.aborted) throw new Error('dashboard_renderer_aborted')
    const module = await Promise.race([aborted, importer(`${prefix}${build.entry}`)])
    if (signal?.aborted) throw new Error('dashboard_renderer_aborted')
    if (
      typeof module !== 'object' ||
      module === null ||
      !('mountDashboardView' in module) ||
      typeof module.mountDashboardView !== 'function'
    )
      throw new Error('dashboard_renderer_invalid')
    const instance: unknown = module.mountDashboardView(element, initial, {
      id: initial.id,
      template_id: initial.template_id,
      design_identity: initial.design_identity,
      renderer_build_id: initial.renderer_build_id,
    })
    if (!isRenderer(instance)) throw new Error('dashboard_renderer_invalid')
    let disposed = false
    return {
      update(view) {
        if (disposed) throw new Error('dashboard_disposed')
        instance.update(view)
      },
      dispose() {
        if (disposed) return
        disposed = true
        try {
          instance.dispose()
        } finally {
          clear()
        }
      },
    }
  } catch (error) {
    clear()
    throw error
  } finally {
    signal?.removeEventListener('abort', abort)
  }
}
