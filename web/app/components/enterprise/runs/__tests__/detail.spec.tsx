import type { contract } from '@enterprise/business-contracts/orpc'
import type { RunView } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider } from 'jotai'
import { RunDetail } from '../detail'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  access: vi.fn<Client['me']['get']>(),
  run: vi.fn<Client['runs']['byRunId']['get']>(),
  events: vi.fn<Client['runs']['byRunId']['events']['get']>(),
}))
vi.mock('@/service/client', async () => {
  const { createTanstackQueryUtils } = await import('@orpc/tanstack-query')
  return {
    consoleQuery: {
      business: createTanstackQueryUtils({
        me: { get: api.access },
        runs: { byRunId: { get: api.run, events: { get: api.events } } },
      }),
    },
  }
})
vi.mock('@/context/workspace-state', async () => ({
  currentWorkspaceAtom: (await import('jotai')).atom({ id: 'workspace-1', name: 'Plant' }),
}))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('user-1'),
}))

function fixture(patch: Partial<RunView> = {}): RunView {
  return {
    id: 'run-1',
    device_id: 'device-1',
    scenario: 'quality',
    binding_revision: 2,
    specification_revision: 'standard-v1',
    status: 'succeeded',
    reason_code: null,
    has_input_snapshot: true,
    input_snapshot_digest: 'a'.repeat(64),
    parameters: { batch: '0001' },
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:01:00Z',
    result: {
      scenario: 'quality',
      conclusion: 'failed',
      complete: false,
      evidence: { raw_value: '85.0000000000000001' },
    },
    ...patch,
  }
}
function page() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = createStore()
  const tree = (runId: string) => (
    <QueryClientProvider client={client}>
      <Provider store={store}>
        <RunDetail runId={runId} />
      </Provider>
    </QueryClientProvider>
  )
  const view = render(tree('run-1'))
  return { client, store, rerender: (id: string) => view.rerender(tree(id)) }
}

describe('Run detail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    api.run.mockResolvedValue(fixture())
    api.events.mockResolvedValue([])
  })

  it('separates successful execution from failed quality and incomplete evidence', async () => {
    page()
    expect(await screen.findByText('common.enterprise.devices.state.succeeded')).toBeInTheDocument()
    expect(screen.getByText('common.enterprise.runs.verdict.failed')).toBeInTheDocument()
    expect(screen.getByText('common.enterprise.runs.incomplete')).toBeInTheDocument()
    expect(screen.getByText('standard-v1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'common.enterprise.devices.title' })).toHaveAttribute(
      'href',
      '/enterprise/devices/device-1',
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.runs.evidence' }))
    expect(screen.getByText(/85\.0000000000000001/)).toBeInTheDocument()
  })

  it('does not invent a business verdict or snapshot for a queued run', async () => {
    api.run.mockResolvedValue(
      fixture({
        status: 'queued',
        result: null,
        has_input_snapshot: false,
        input_snapshot_digest: null,
      }),
    )
    page()
    expect(await screen.findByText('common.enterprise.runs.noConclusion')).toBeInTheDocument()
    expect(screen.getByText('common.enterprise.runs.notCaptured')).toBeInTheDocument()
    expect(screen.queryByText('common.enterprise.runs.verdict.passed')).not.toBeInTheDocument()
  })

  it('shows a transport error rather than a normal or empty result and retries explicitly', async () => {
    api.run.mockRejectedValueOnce(new ORPCError('NOT_FOUND')).mockResolvedValueOnce(fixture())
    page()
    await screen.findByRole('alert')
    expect(api.events).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByText('standard-v1')).toBeInTheDocument()
    expect(api.run).toHaveBeenCalledTimes(2)
  })

  it('rejects a mismatched response before showing data or requesting history', async () => {
    api.run.mockResolvedValue(fixture({ id: 'other-run' }))
    page()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.accessDenied',
    )
    expect(screen.queryByText('standard-v1')).not.toBeInTheDocument()
    expect(api.events).not.toHaveBeenCalled()
  })

  it('shows and pages real scoped events without exposing their arbitrary data', async () => {
    api.events.mockResolvedValue(
      Array.from({ length: 21 }, (_, index) => ({
        sequence: index + 1,
        workspace_id: 'workspace-1',
        resource_id: 'run-1',
        run_id: 'run-1',
        actor_id: 'user-1',
        action: `record-${index}`,
        data: { hidden: 'do-not-render-audit-payload' },
        created_at: '2026-09-08T00:00:00Z',
      })),
    )
    page()
    await screen.findByText('record-0')
    expect(screen.queryByText('do-not-render-audit-payload')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await screen.findByText('record-20')
    expect(api.events).toHaveBeenCalledTimes(1)
    expect(api.events.mock.calls[0]?.[0]).toEqual({
      params: { run_id: 'run-1' },
      query: { after_sequence: 0 },
    })
    await userEvent.click(screen.getByRole('button', { name: 'common.pagination.previous' }))
    expect(await screen.findByText('record-0')).toBeInTheDocument()
  })

  it('does not render events from a different workspace or run', async () => {
    api.events.mockResolvedValue([
      {
        sequence: 1,
        workspace_id: 'foreign',
        resource_id: 'other',
        run_id: 'other',
        actor_id: 'other',
        action: 'hidden-foreign-action',
        data: {},
        created_at: '2026-09-08T00:00:00Z',
      },
    ])
    page()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.accessDenied',
    )
    expect(screen.queryByText('hidden-foreign-action')).not.toBeInTheDocument()
  })

  it('isolates late responses when the selected run changes', async () => {
    let finish: ((value: RunView) => void) | undefined
    api.run
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve
          }),
      )
      .mockResolvedValueOnce(fixture({ id: 'run-2', specification_revision: 'new-spec' }))
    const view = page()
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    view.rerender('run-2')
    await screen.findByText('new-spec')
    finish?.(fixture())
    await waitFor(() => expect(screen.queryByText('standard-v1')).not.toBeInTheDocument())
    expect(api.events.mock.calls.every(([input]) => input.params.run_id === 'run-2')).toBe(true)
  })

  it('renders evidence as text rather than HTML', async () => {
    api.run.mockResolvedValue(
      fixture({
        result: {
          scenario: 'quality',
          conclusion: 'passed',
          complete: true,
          evidence: { note: '<img src=x onerror=alert(1)>' },
        },
      }),
    )
    page()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.runs.evidence' }),
    )
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('should limit only the preview when evidence exceeds the display budget', async () => {
    const evidence = { note: 'x'.repeat(70000), end: 'full-report-tail' }
    api.run.mockResolvedValue(
      fixture({
        result: { scenario: 'quality', conclusion: 'passed', complete: true, evidence },
      }),
    )
    page()

    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.runs.evidence' }),
    )

    expect(screen.getByText('common.enterprise.runs.previewLimited')).toBeInTheDocument()
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === 'PRE' &&
          element.textContent === JSON.stringify(evidence, null, 2).slice(0, 65536),
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(/full-report-tail/)).not.toBeInTheDocument()
  })

  it('should download the full report and release its URL after initiating the download', async () => {
    const run = fixture({ parameters: { long: 'x'.repeat(70000), end: 'full-report-tail' } })
    api.run.mockResolvedValue(run)
    const createURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:run-report')
    const revokeURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    page()
    const button = await screen.findByRole('button', { name: 'common.enterprise.runs.download' })

    try {
      await userEvent.click(button)

      const blob = createURL.mock.calls[0]?.[0]
      expect(blob).toBeInstanceOf(Blob)
      if (!(blob instanceof Blob)) throw new Error('Expected report Blob')
      expect(blob.type).toBe('application/json;charset=utf-8')
      expect(JSON.parse(await blob.text())).toEqual(run)
      expect(anchorClick).toHaveBeenCalledTimes(1)
      expect(anchorClick.mock.contexts[0]).toHaveAttribute('download', 'run-run-1.json')
      expect(anchorClick.mock.contexts[0]).toHaveAttribute('href', 'blob:run-report')
      await waitFor(() => expect(revokeURL).toHaveBeenCalledWith('blob:run-report'), {
        timeout: 2000,
      })
    } finally {
      createURL.mockRestore()
      revokeURL.mockRestore()
      anchorClick.mockRestore()
    }
  })

  it('should refresh both execution and its history without reloading unrelated account queries', async () => {
    page()
    await waitFor(() => expect(api.events).toHaveBeenCalledTimes(1))

    await userEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))

    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(api.events).toHaveBeenCalledTimes(2))
    expect(api.access).toHaveBeenCalledTimes(1)
  })
})
