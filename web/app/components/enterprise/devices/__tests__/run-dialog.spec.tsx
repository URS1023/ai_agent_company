import type { contract } from '@enterprise/business-contracts/orpc'
import type { Binding, RunView } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider } from 'jotai'
import { BusinessGate } from '../access'
import { QueueRun } from '../run-dialog'

type Client = ContractRouterClient<typeof contract>
const workspaceAtom = await vi.hoisted(async () =>
  (await import('jotai')).atom({ id: 'workspace-1', name: 'Plant' }),
)
const api = vi.hoisted(() => ({
  access: vi.fn<Client['me']['get']>(),
  enqueue: vi.fn<Client['devices']['byDeviceId']['bindings']['byScenario']['runs']['post']>(),
  lookup: vi.fn<Client['runRequests']['lookup']['get']>(),
}))
vi.mock('@/service/client', async () => {
  const { createTanstackQueryUtils } = await import('@orpc/tanstack-query')
  return {
    consoleQuery: {
      business: createTanstackQueryUtils({
        me: { get: api.access },
        devices: { byDeviceId: { bindings: { byScenario: { runs: { post: api.enqueue } } } } },
        runRequests: { lookup: { get: api.lookup } },
      }),
    },
  }
})
vi.mock('@/context/workspace-state', () => ({ currentWorkspaceAtom: workspaceAtom }))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('user-1'),
}))

function binding(): Binding {
  return {
    id: 'binding',
    workspace_id: 'workspace-1',
    device_id: 'device-1',
    scenario: 'alert',
    revision: 2,
    app_id: 'app',
    workflow_id: '00000000-0000-4000-8000-000000000001',
    specification_revision: 'spec-1',
    source_id: 'source',
    source_revision: 's1',
    read_id: 'read',
    read_revision: 'r1',
    secret_ref: 'private-ref',
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  }
}
function recorded(overrides: Partial<RunView> = {}): RunView {
  return {
    id: 'original-run',
    device_id: 'device-1',
    scenario: 'alert',
    binding_revision: 2,
    specification_revision: 'spec-1',
    parameters: {},
    status: 'dispatched',
    result: null,
    reason_code: null,
    has_input_snapshot: false,
    input_snapshot_digest: null,
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
    ...overrides,
  }
}
function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const store = createStore()
  const tree = (current: Binding | undefined, disabled = false) => (
    <QueryClientProvider client={client}>
      <Provider store={store}>
        <BusinessGate>
          {(access, scope) =>
            access.permissions.run && (
              <QueueRun binding={current} scope={scope} disabled={disabled} />
            )
          }
        </BusinessGate>
      </Provider>
    </QueryClientProvider>
  )
  const rendered = render(tree(binding()))
  return {
    client,
    store,
    rerender: (current: Binding | undefined, disabled = false) =>
      rendered.rerender(tree(current, disabled)),
  }
}
async function loseResponse(parameters = {}) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }))
  if (Object.keys(parameters).length) {
    const field = screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' })
    await user.clear(field)
    await user.click(field)
    await user.paste(JSON.stringify(parameters))
  }
  await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
  await screen.findByRole('alert')
  return user
}

// Real query providers and overlay primitives; only the generated client boundary is mocked.
describe('QueueRun authoritative recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: false, run: true, review: false },
    })
    api.enqueue.mockRejectedValue(new Error('response lost'))
    api.lookup.mockResolvedValue(recorded())
  })

  it('should offer no lookup before any request has been submitted', async () => {
    renderDialog()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).not.toBeInTheDocument()
    expect(api.lookup).not.toHaveBeenCalled()
  })

  it('should retain an attempt while an open dialog has no ready binding', async () => {
    const { rerender } = renderDialog()
    const user = await loseResponse({ batch: '0001' })
    rerender(undefined, true)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.operation.confirm' })).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).toBeDisabled()
    expect(screen.getByText('common.enterprise.devices.loadError')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    expect(api.enqueue).toHaveBeenCalledOnce()
    expect(api.lookup).not.toHaveBeenCalled()

    rerender({ ...binding(), revision: 3, specification_revision: 'spec-2' })
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(2))
    expect(api.enqueue.mock.calls[1]?.[0]).toEqual(api.enqueue.mock.calls[0]?.[0])
  })

  it('should block opening a queue dialog while the binding is disabled', async () => {
    const { rerender } = renderDialog()
    await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' })
    rerender(binding(), true)
    expect(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.enqueue).not.toHaveBeenCalled()
  })

  it('should restore a matching recorded run without another POST', async () => {
    renderDialog()
    const user = await loseResponse()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'common.enterprise.devices.runRecovered',
    )
    expect(screen.getByText('original-run')).toBeInTheDocument()
    expect(screen.getByText('common.enterprise.devices.state.dispatched')).toBeInTheDocument()
    expect(api.lookup.mock.calls[0]?.[0]).toEqual({
      query: { request_key: api.enqueue.mock.calls[0]?.[0].headers['idempotency-key'] },
    })
    expect(api.enqueue).toHaveBeenCalledTimes(1)
    expect(
      screen.queryByRole('button', { name: 'common.operation.confirm' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).not.toBeInTheDocument()
  })

  it('should show the restored revision rather than the newly edited binding revision', async () => {
    const { rerender } = renderDialog()
    const user = await loseResponse()
    rerender({ ...binding(), revision: 3, specification_revision: 'spec-2' })
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    await screen.findByText('common.enterprise.devices.runRecovered')
    expect(screen.getByText(/common.enterprise.devices.revision/)).toHaveTextContent('2 · spec-1')
    expect(api.enqueue).toHaveBeenCalledTimes(1)
  })

  it('should clear the recovered attempt before the user opens a deliberate new request', async () => {
    api.lookup.mockResolvedValue(recorded({ parameters: { batch: '0001' } }))
    renderDialog()
    const user = await loseResponse({ batch: '0001' })
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    await screen.findByText('common.enterprise.devices.runRecovered')
    await user.click(screen.getByRole('button', { name: 'common.operation.close' }))
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    const field = screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' })
    expect(field).not.toBeDisabled()
    expect(field).toHaveValue('{}')
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).not.toBeInTheDocument()
    expect(api.enqueue).toHaveBeenCalledTimes(1)
  })

  it('should verify with a fresh server read rather than reuse a cached result', async () => {
    const { client } = renderDialog()
    const user = await loseResponse()
    client.setQueryData(
      [
        'enterprise-business',
        'workspace-1',
        'user-1',
        'run-request',
        api.enqueue.mock.calls[0]?.[0].headers['idempotency-key'],
      ],
      recorded({ status: 'succeeded' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    await screen.findByText('common.enterprise.devices.runRecovered')
    expect(api.lookup).toHaveBeenCalledTimes(1)
    expect(screen.getByText('common.enterprise.devices.state.dispatched')).toBeInTheDocument()
  })

  it.each([
    ['not found', new ORPCError('NOT_FOUND'), 'common.enterprise.devices.runNotFound'],
    ['network failure', new Error('lookup offline'), 'common.enterprise.devices.runLookupFailed'],
    [
      'server failure',
      new ORPCError('INTERNAL_SERVER_ERROR'),
      'common.enterprise.devices.runLookupFailed',
    ],
  ])('should retain the exact original attempt after %s', async (_case, error, message) => {
    api.lookup.mockRejectedValue(error)
    const { rerender } = renderDialog()
    const user = await loseResponse({ batch: '0001' })
    rerender({ ...binding(), revision: 3, specification_revision: 'spec-2' })
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    expect(await screen.findByText(String(message))).toBeInTheDocument()
    expect(api.enqueue).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    expect(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
    ).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(2))
    expect(api.enqueue.mock.calls[1]?.[0]).toEqual(api.enqueue.mock.calls[0]?.[0])
  })

  it.each([
    { device_id: 'another-device' },
    { scenario: 'quality' },
    { binding_revision: 3 },
    { parameters: { batch: 'changed' } },
    { specification_revision: 'changed-spec' },
  ] satisfies Partial<RunView>[])(
    'should block submission when the recorded run mismatches %j',
    async (changes) => {
      api.lookup.mockResolvedValue(recorded(changes))
      renderDialog()
      const user = await loseResponse()
      await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.devices.runMismatch',
      )
      expect(screen.getByRole('button', { name: 'common.operation.confirm' })).toBeDisabled()
      expect(
        screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
      ).toBeDisabled()
      expect(screen.queryByText('common.enterprise.devices.runRecovered')).not.toBeInTheDocument()
      expect(api.enqueue).toHaveBeenCalledTimes(1)
    },
  )

  it('should compare parameter objects independently of property order', async () => {
    api.lookup.mockResolvedValue(recorded({ parameters: { b: { value: '0001' }, a: [1, true] } }))
    renderDialog()
    const user = await loseResponse({ a: [1, true], b: { value: '0001' } })
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'common.enterprise.devices.runRecovered',
    )
    expect(api.enqueue).toHaveBeenCalledTimes(1)
  })

  it('should retain the correction path after a definitive 422', async () => {
    api.enqueue.mockRejectedValueOnce(new ORPCError('VALIDATION_ERROR', { status: 422 }))
    renderDialog()
    await loseResponse({ typo: '0001' })
    expect(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
    ).not.toBeDisabled()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).not.toBeInTheDocument()
    expect(api.lookup).not.toHaveBeenCalled()
  })

  it('should prevent duplicate POSTs or closing while verification is pending', async () => {
    let finish: (run: RunView) => void = () => {
      throw new Error('lookup has not started')
    }
    api.lookup.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    renderDialog()
    const user = await loseResponse()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    expect(screen.getByRole('button', { name: 'common.operation.confirm' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'common.operation.cancel' })).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await act(() => finish(recorded()))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'common.enterprise.devices.runRecovered',
    )
    expect(api.enqueue).toHaveBeenCalledTimes(1)
  })

  it('should read the server again after a previous lookup found no run', async () => {
    api.lookup.mockRejectedValueOnce(new ORPCError('NOT_FOUND'))
    renderDialog()
    const user = await loseResponse()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    await screen.findByText('common.enterprise.devices.runNotFound')
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      'common.enterprise.devices.runRecovered',
    )
    expect(api.lookup).toHaveBeenCalledTimes(2)
    expect(api.enqueue).toHaveBeenCalledTimes(1)
  })

  it('should discard the old dialog on workspace switch and not display its late lookup result', async () => {
    let finish: (run: RunView) => void = () => {
      throw new Error('lookup has not started')
    }
    api.lookup.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const { client, store, rerender } = renderDialog()
    const user = await loseResponse()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }))
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-2',
      display_name: 'User',
      permissions: { read: true, manage: false, run: true, review: false },
    })
    await act(() => store.set(workspaceAtom, { id: 'workspace-2', name: 'Other plant' }))
    rerender({ ...binding(), workspace_id: 'workspace-2', device_id: 'other-device' })
    await user.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    await act(() => finish(recorded()))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.queryByText('original-run')).not.toBeInTheDocument()
    expect(
      dialog.queryByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).not.toBeInTheDocument()
    expect(
      dialog.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
    ).not.toBeDisabled()
    expect(
      client.getQueryData([
        'enterprise-business',
        'workspace-2',
        'user-1',
        'run-request',
        api.enqueue.mock.calls[0]?.[0].headers['idempotency-key'],
      ]),
    ).toBeUndefined()
    await user.click(dialog.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(2))
    expect(api.enqueue.mock.calls[1]?.[0].params.device_id).toBe('other-device')
    expect(api.enqueue.mock.calls[1]?.[0].headers['idempotency-key']).not.toBe(
      api.enqueue.mock.calls[0]?.[0].headers['idempotency-key'],
    )
  })
})
