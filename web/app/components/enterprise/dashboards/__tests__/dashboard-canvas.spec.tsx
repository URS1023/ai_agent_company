import type { DashboardView } from '@enterprise/business-contracts/types'
import type { DashboardRenderer } from '../renderer-loader'
import { act, render, screen, waitFor } from '@testing-library/react'
import { DashboardCanvas } from '../dashboard-canvas'
import { loadDashboardRenderer } from '../renderer-loader'

vi.mock('../renderer-loader', () => ({ loadDashboardRenderer: vi.fn() }))
const load = vi.mocked(loadDashboardRenderer)
const view: DashboardView = {
  id: 'screen',
  name: 'Screen',
  revision: 1,
  template_id: 'template',
  design_identity: 'a',
  renderer_build_id: 'b',
  status: 'empty',
  current: null,
  last_attempt_id: null,
  failures: [],
}

describe('Dashboard canvas lifecycle', () => {
  beforeEach(() => vi.clearAllMocks())

  it('should apply the newest received revision when loading finishes', async () => {
    let finish!: (runtime: DashboardRenderer) => void
    load.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const runtime = { update: vi.fn(), dispose: vi.fn() }
    const mounted = render(<DashboardCanvas view={view} />)
    const newer = { ...view, revision: 3 }
    mounted.rerender(<DashboardCanvas view={newer} />)
    mounted.rerender(<DashboardCanvas view={{ ...view, revision: 2 }} />)
    await act(async () => {
      finish(runtime)
    })
    expect(runtime.update).toHaveBeenCalledExactlyOnceWith(newer)
  })

  it('should replace the renderer when dashboard identity changes', async () => {
    const first = { update: vi.fn(), dispose: vi.fn() }
    const second = { update: vi.fn(), dispose: vi.fn() }
    load.mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    const mounted = render(<DashboardCanvas view={view} />)
    await act(async () => {})
    mounted.rerender(<DashboardCanvas view={{ ...view, id: 'other-screen' }} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    expect(first.dispose).toHaveBeenCalledOnce()
    expect(first.update).not.toHaveBeenCalled()
  })

  it('should update newer data without remounting and dispose on unmount', async () => {
    const runtime = { update: vi.fn(), dispose: vi.fn() }
    load.mockResolvedValue(runtime)
    const mounted = render(<DashboardCanvas view={view} />)
    await waitFor(() => expect(load).toHaveBeenCalledOnce())
    const newer = { ...view, revision: 2 }
    mounted.rerender(<DashboardCanvas view={newer} />)
    await waitFor(() => expect(runtime.update).toHaveBeenCalledWith(newer))
    mounted.rerender(<DashboardCanvas view={view} />)
    expect(runtime.update).toHaveBeenCalledOnce()
    mounted.unmount()
    expect(runtime.dispose).toHaveBeenCalledOnce()
    expect(load.mock.calls[0]?.[4]?.aborted).toBe(true)
  })

  it('should dispose a late handle after unmount', async () => {
    let finish!: (runtime: DashboardRenderer) => void
    load.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const runtime = { update: vi.fn(), dispose: vi.fn() }
    const mounted = render(<DashboardCanvas view={view} />)
    mounted.unmount()
    await act(async () => {
      finish(runtime)
    })
    expect(runtime.dispose).toHaveBeenCalledOnce()
    expect(runtime.update).not.toHaveBeenCalled()
  })

  it('should report loading failure without fabricating dashboard data', async () => {
    load.mockRejectedValue(new Error('module unavailable'))
    render(<DashboardCanvas view={view} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('common.api.actionFailed')
  })
})
