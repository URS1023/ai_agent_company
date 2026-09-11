// @vitest-environment-options {"settings":{"disableCSSFileLoading":true,"handleDisabledFileLoadingAsSuccess":true}}
import type { DashboardView } from '@enterprise/business-contracts/types'
import build from '../renderer-build.json'
import { loadDashboardRenderer } from '../renderer-loader'

const view: DashboardView = {
  id: 'screen',
  name: 'Screen',
  revision: 1,
  template_id: 'equipment-digital-ops',
  design_identity: 'a'.repeat(64),
  renderer_build_id: build.rendererBuildId,
  status: 'empty',
  current: null,
  last_attempt_id: null,
  failures: [],
}

describe('Dashboard renderer loading', () => {
  it('should reject an already cancelled load before creating resources', async () => {
    const controller = new AbortController()
    controller.abort()
    const importer = vi.fn()
    await expect(
      loadDashboardRenderer(document.createElement('div'), view, '', importer, controller.signal),
    ).rejects.toThrow('dashboard_renderer_aborted')
    expect(importer).not.toHaveBeenCalled()
    expect(document.head.querySelector('link[data-dashboard-renderer]')).toBeNull()
  })

  it('should remove styles on cancellation and never mount a late module', async () => {
    const controller = new AbortController()
    const mount = vi.fn()
    let resolveModule!: (module: unknown) => void
    const importer = vi.fn(
      () =>
        new Promise<unknown>((resolve) => {
          resolveModule = resolve
        }),
    )
    const pending = loadDashboardRenderer(
      document.createElement('div'),
      view,
      '',
      importer,
      controller.signal,
    )
    const rejection = expect(pending).rejects.toThrow('dashboard_renderer_aborted')
    await vi.waitFor(() => expect(importer).toHaveBeenCalledOnce())
    controller.abort()
    await rejection
    resolveModule({ mountDashboardView: mount })
    await Promise.resolve()
    expect(mount).not.toHaveBeenCalled()
    expect(document.head.querySelector('link[data-dashboard-renderer]')).toBeNull()
  })

  afterEach(() =>
    document.head
      .querySelectorAll('link[data-dashboard-renderer]')
      .forEach((item) => item.remove()),
  )

  it('should reject an unavailable build before loading any script or style', async () => {
    const importer = vi.fn()
    await expect(
      loadDashboardRenderer(
        document.createElement('div'),
        { ...view, renderer_build_id: 'other' },
        '',
        importer,
      ),
    ).rejects.toThrow('dashboard_renderer_unavailable')
    expect(importer).not.toHaveBeenCalled()
    expect(document.head.querySelector('link[data-dashboard-renderer]')).toBeNull()
  })

  it('should await styles, use the configured base path and dispose both runtime and styles', async () => {
    const dispose = vi.fn()
    const update = vi.fn()
    const mount = vi.fn().mockReturnValue({ dispose, update })
    const importer = vi.fn().mockResolvedValue({ mountDashboardView: mount })
    const element = document.createElement('div')
    const pending = loadDashboardRenderer(element, view, '/portal', importer)
    const link = document.head.querySelector<HTMLLinkElement>('link[data-dashboard-renderer]')!
    expect(link.getAttribute('href')).toBe(`/portal/enterprise/renderers${build.styles[0]}`)
    expect(mount).not.toHaveBeenCalled()
    link.dispatchEvent(new Event('load'))
    const renderer = await pending
    expect(importer).toHaveBeenCalledWith(`/portal/enterprise/renderers${build.entry}`)
    expect(mount).toHaveBeenCalledWith(element, view, {
      id: view.id,
      template_id: view.template_id,
      design_identity: view.design_identity,
      renderer_build_id: view.renderer_build_id,
    })
    renderer.dispose()
    renderer.dispose()
    expect(dispose).toHaveBeenCalledOnce()
    expect(link.isConnected).toBe(false)
  })

  it('should remove style links when loading fails without mounting', async () => {
    const importer = vi.fn().mockRejectedValue(new Error('renderer_import_failed'))
    const pending = loadDashboardRenderer(document.createElement('div'), view, '', importer)
    const rejection = expect(pending).rejects.toThrow('renderer_import_failed')
    document.head.querySelector('link[data-dashboard-renderer]')!.dispatchEvent(new Event('error'))
    await rejection
    expect(importer).toHaveBeenCalledOnce()
    expect(document.head.querySelector('link[data-dashboard-renderer]')).toBeNull()
  })
})
