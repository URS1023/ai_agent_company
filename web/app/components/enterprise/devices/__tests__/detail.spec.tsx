import type { contract } from '@enterprise/business-contracts/orpc'
import type { Binding, RunView, Scenario } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider } from 'jotai'
import { DeviceDetail } from '../detail'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  access: vi.fn<Client['me']['get']>(),
  get: vi.fn<Client['devices']['byDeviceId']['get']>(),
  binding: vi.fn<Client['devices']['byDeviceId']['bindings']['byScenario']['get']>(),
  runs: vi.fn<Client['devices']['byDeviceId']['bindings']['byScenario']['runs']['get']>(),
  enqueue: vi.fn<Client['devices']['byDeviceId']['bindings']['byScenario']['runs']['post']>(),
  lookup: vi.fn<Client['runRequests']['lookup']['get']>(),
  schedule: vi.fn<Client['devices']['byDeviceId']['byScenario']['schedule']['get']>(),
  scheduleState: vi.fn<Client['schedules']['byScheduleId']['state']['post']>(),
  scheduleActors:
    vi.fn<Client['devices']['byDeviceId']['byScenario']['schedule']['actors']['get']>(),
  scheduleCreate: vi.fn<Client['devices']['byDeviceId']['byScenario']['schedule']['post']>(),
}))
vi.mock('@/service/client', async () => {
  const { createTanstackQueryUtils } = await import('@orpc/tanstack-query')
  return {
    consoleQuery: {
      business: createTanstackQueryUtils({
        me: { get: api.access },
        runRequests: { lookup: { get: api.lookup } },
        schedules: { byScheduleId: { state: { post: api.scheduleState } } },
        devices: {
          byDeviceId: {
            get: api.get,
            byScenario: {
              schedule: {
                get: api.schedule,
                post: api.scheduleCreate,
                actors: { get: api.scheduleActors },
              },
            },
            bindings: {
              byScenario: { get: api.binding, runs: { get: api.runs, post: api.enqueue } },
            },
          },
        },
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
vi.mock('@/next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))

function binding(scenario: Scenario = 'alert') {
  return {
    id: `binding-${scenario}`,
    workspace_id: 'workspace-1',
    device_id: 'device-1',
    scenario,
    revision: scenario === 'alert' ? 2 : 3,
    app_id: scenario === 'alert' ? 'workflow-app' : 'quality-app',
    workflow_id: '00000000-0000-4000-8000-000000000001',
    specification_revision: scenario === 'alert' ? 'spec-1' : 'quality-spec-1',
    source_id: scenario === 'alert' ? 'measurements' : 'quality-measurements',
    source_revision: 'source-1',
    read_id: 'read',
    read_revision: 'read-1',
    secret_ref: 'private-secret-reference',
    manifest: {},
    active_run_id: null,
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  } satisfies Binding
}
function queued(scenario: Scenario = 'alert'): RunView {
  return {
    id: 'run-1',
    device_id: 'device-1',
    scenario,
    binding_revision: binding(scenario).revision,
    specification_revision: binding(scenario).specification_revision,
    parameters: {},
    has_input_snapshot: false,
    input_snapshot_digest: null,
    reason_code: null,
    result: null,
    status: 'queued',
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  }
}
function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const store = createStore()
  const renderTree = (deviceId: string) => (
    <QueryClientProvider client={client}>
      <Provider store={store}>
        <DeviceDetail deviceId={deviceId} />
      </Provider>
    </QueryClientProvider>
  )
  const rendered = render(renderTree('device-1'))
  return { client, rerender: (deviceId: string) => rendered.rerender(renderTree(deviceId)) }
}

describe('Device detail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.schedule.mockResolvedValue(null)
    api.scheduleActors.mockResolvedValue([])
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: false, run: true, review: false },
    })
    api.get.mockResolvedValue({
      id: 'device-1',
      workspace_id: 'workspace-1',
      device_code: '0001',
      name: 'Pump',
      department: '',
      description: '',
      deleted_at: null,
      revision: 1,
      created_at: '2026-09-08T00:00:00Z',
      updated_at: '2026-09-08T00:00:00Z',
    })
    api.binding.mockImplementation(async ({ params }) => binding(params.scenario))
    api.runs.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 5 })
    api.enqueue.mockImplementation(async ({ params, body }) => ({
      ...queued(params.scenario),
      parameters: body.parameters ?? {},
    }))
  })

  it('should show the bound source and native workflow without exposing its secret reference', async () => {
    renderDetail()
    expect(
      await screen.findByRole('link', { name: 'common.enterprise.devices.binding' }),
    ).toHaveAttribute('href', '/app/workflow-app/workflow')
    expect(screen.getByText('0001')).toBeInTheDocument()
    expect(screen.getByText('measurements')).toBeInTheDocument()
    expect(screen.queryByText('private-secret-reference')).not.toBeInTheDocument()
  })

  it('should load schedule management within both device scenario panels', async () => {
    renderDetail()
    await screen.findByRole('region', { name: 'common.enterprise.schedule.title' })
    await waitFor(() => expect(api.schedule).toHaveBeenCalledTimes(2))
    expect(api.schedule.mock.calls.map(([input]) => input.params)).toEqual(
      expect.arrayContaining([
        { device_id: 'device-1', scenario: 'alert' },
        { device_id: 'device-1', scenario: 'quality' },
      ]),
    )
  })

  it('should offer the matching editable template when each scenario has no binding', async () => {
    api.binding.mockRejectedValue(new ORPCError('NOT_FOUND'))
    renderDetail()
    expect(
      await screen.findByRole('link', { name: 'common.enterprise.devices.downloadTemplate' }),
    ).toHaveAttribute('href', '/enterprise/workflow-templates/alert')
    expect(
      within(screen.getByRole('tabpanel')).getByText('common.enterprise.devices.templateSetup'),
    ).toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )

    expect(
      await screen.findByRole('link', { name: 'common.enterprise.devices.downloadTemplate' }),
    ).toHaveAttribute('href', '/enterprise/workflow-templates/quality')
    expect(api.enqueue).not.toHaveBeenCalled()
  })

  it('should load and queue the quality scenario when selected', async () => {
    api.enqueue.mockResolvedValue(queued('quality'))
    renderDetail()

    await userEvent.click(
      await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )

    expect(
      await screen.findByRole('link', { name: 'common.enterprise.devices.qualityBinding' }),
    ).toHaveAttribute('href', '/app/quality-app/workflow')
    expect(api.binding).toHaveBeenCalledWith(
      { params: { device_id: 'device-1', scenario: 'quality' } },
      expect.anything(),
    )
    expect(
      screen.getAllByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))

    await waitFor(() => expect(api.enqueue).toHaveBeenCalledOnce())
    expect(api.enqueue.mock.calls[0]?.[0]).toMatchObject({
      params: { device_id: 'device-1', scenario: 'quality' },
      body: { expected_binding_revision: 3, parameters: {} },
    })
  })

  it('should link only the selected scenario runs to their details', async () => {
    api.runs.mockImplementation(async ({ params }) => ({
      items: [{ ...queued(params.scenario), id: `run-${params.scenario}` }],
      offset: 0,
      limit: 5,
      total: 1,
    }))
    renderDetail()
    expect(await screen.findByRole('link', { name: 'run-alert' })).toHaveAttribute(
      'href',
      '/enterprise/runs/run-alert',
    )

    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )

    expect(await screen.findByRole('link', { name: 'run-quality' })).toHaveAttribute(
      'href',
      '/enterprise/runs/run-quality',
    )
    expect(screen.queryByRole('link', { name: 'run-alert' })).not.toBeInTheDocument()
    expect(api.runs).toHaveBeenCalledWith(
      { params: { device_id: 'device-1', scenario: 'quality' }, query: { offset: 0, limit: 5 } },
      expect.anything(),
    )
  })

  // A scenario owns both its server queries and its unresolved queue attempt.
  it('should preserve an alert attempt while independently queueing quality', async () => {
    api.enqueue.mockRejectedValueOnce(new Error('response lost'))
    renderDetail()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.cancel' }))

    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    expect(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
    ).not.toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.enqueue.mock.calls[1]?.[0].params.scenario).toBe('quality')
    expect(api.enqueue.mock.calls[1]?.[0].headers['idempotency-key']).not.toBe(
      api.enqueue.mock.calls[0]?.[0].headers['idempotency-key'],
    )

    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.alert' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    expect(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
    ).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(3))
    expect(api.enqueue.mock.calls[2]?.[0]).toEqual(api.enqueue.mock.calls[0]?.[0])
  })

  it('should preserve a hidden alert attempt through a failed binding refresh', async () => {
    let alertUnavailable = false
    api.binding.mockImplementation(async ({ params }) => {
      if (params.scenario === 'alert' && alertUnavailable)
        throw new ORPCError('INTERNAL_SERVER_ERROR')
      return binding(params.scenario)
    })
    api.enqueue.mockRejectedValueOnce(new Error('response lost'))
    const { client } = renderDetail()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
    alertUnavailable = true

    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }))
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await act(async () => {
      await client.invalidateQueries({
        queryKey: ['enterprise-business', 'workspace-1', 'user-1', 'binding', 'device-1', 'alert'],
      })
    })
    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.alert' }),
    )
    const failure = await screen.findByRole('alert')
    alertUnavailable = false
    await userEvent.click(within(failure).getByRole('button', { name: 'common.operation.retry' }))
    const trigger = await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' })
    await waitFor(() => expect(trigger).not.toBeDisabled())
    await userEvent.click(trigger)
    expect(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
    ).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(3))
    expect(api.enqueue.mock.calls[2]?.[0]).toEqual(api.enqueue.mock.calls[0]?.[0])
  })

  it.each([
    ['get', 'enqueue'],
    ['access', 'enqueue'],
    ['get', 'lookup'],
    ['access', 'lookup'],
  ] as const)(
    'should not refetch unrelated %s state after quality %s',
    async (parentQuery, action) => {
      api.enqueue.mockRejectedValueOnce(new Error('alert response lost'))
      if (action === 'lookup') api.enqueue.mockRejectedValueOnce(new Error('quality response lost'))
      api.lookup.mockResolvedValue(queued('quality'))
      renderDetail()
      await userEvent.click(
        await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
      )
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      await screen.findByRole('alert')
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
      api[parentQuery].mockRejectedValue(new ORPCError('INTERNAL_SERVER_ERROR'))

      await userEvent.click(
        screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
      )
      await userEvent.click(
        screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }),
      )
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      if (action === 'lookup') {
        await screen.findByRole('alert')
        await userEvent.click(
          screen.getByRole('button', { name: 'common.enterprise.devices.checkRun' }),
        )
        await screen.findByText('common.enterprise.devices.runRecovered')
        await userEvent.click(screen.getByRole('button', { name: 'common.operation.close' }))
      }
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

      expect(api[parentQuery]).toHaveBeenCalledOnce()
      expect(
        api.binding.mock.calls.filter(([input]) => input.params.scenario === 'quality'),
      ).toHaveLength(2)
      expect(
        api.runs.mock.calls.filter(([input]) => input.params.scenario === 'quality'),
      ).toHaveLength(2)
      expect(
        api.binding.mock.calls.filter(([input]) => input.params.scenario === 'alert'),
      ).toHaveLength(1)
      expect(
        api.runs.mock.calls.filter(([input]) => input.params.scenario === 'alert'),
      ).toHaveLength(1)
      await userEvent.click(
        screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.alert' }),
      )
      await userEvent.click(
        screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }),
      )
      expect(
        screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' }),
      ).toBeDisabled()
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(3))
      expect(api.enqueue.mock.calls[2]?.[0]).toEqual(api.enqueue.mock.calls[0]?.[0])
    },
  )

  it('should support keyboard scenario selection with one accessible panel', async () => {
    renderDetail()
    const alert = await screen.findByRole('tab', {
      name: 'common.enterprise.devices.scenario.alert',
    })
    await userEvent.click(alert)
    await userEvent.keyboard('{ArrowRight}')
    const quality = screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.quality' })
    expect(quality).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(quality).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(
      'common.enterprise.devices.scenario.quality',
    )
  })

  it('should keep a missing quality binding separate from the configured alert binding', async () => {
    api.binding.mockImplementation(async ({ params }) => {
      if (params.scenario === 'quality') throw new ORPCError('NOT_FOUND')
      return binding(params.scenario)
    })
    renderDetail()
    await userEvent.click(
      await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    expect(
      await within(screen.getByRole('tabpanel')).findByText(
        'common.enterprise.devices.bindingMissing',
      ),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).not.toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('tab', { name: 'common.enterprise.devices.scenario.alert' }),
    )
    expect(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).toBeInTheDocument()
  })

  it('should retry only the failed quality binding', async () => {
    let qualityFailed = true
    api.binding.mockImplementation(async ({ params }) => {
      if (params.scenario === 'quality' && qualityFailed)
        throw new ORPCError('INTERNAL_SERVER_ERROR')
      return binding(params.scenario)
    })
    renderDetail()
    await userEvent.click(
      await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    const failure = await screen.findByRole('alert')
    expect(failure).toHaveTextContent('common.enterprise.devices.loadError')
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).not.toBeInTheDocument()
    qualityFailed = false
    await userEvent.click(within(failure).getByRole('button', { name: 'common.operation.retry' }))
    expect(
      await screen.findByRole('link', { name: 'common.enterprise.devices.qualityBinding' }),
    ).toBeInTheDocument()
    expect(
      api.binding.mock.calls.filter(([input]) => input.params.scenario === 'alert'),
    ).toHaveLength(1)
    expect(
      api.binding.mock.calls.filter(([input]) => input.params.scenario === 'quality'),
    ).toHaveLength(2)
  })

  it('should withhold quality queue controls when run permission is absent', async () => {
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'Reader',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    renderDetail()
    await userEvent.click(
      await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    expect(
      await screen.findByRole('link', { name: 'common.enterprise.devices.qualityBinding' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).not.toBeInTheDocument()
    expect(api.enqueue).not.toHaveBeenCalled()
  })

  it('should show quality binding loading without using the alert binding', async () => {
    let resolve: ((value: Binding) => void) | undefined
    const pending = new Promise<Binding>((finish) => {
      resolve = finish
    })
    api.binding.mockImplementation(async ({ params }) =>
      params.scenario === 'quality' ? pending : binding('alert'),
    )
    renderDetail()
    await userEvent.click(
      await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    expect(
      screen.getByRole('region', { name: 'common.enterprise.devices.qualityBinding' }),
    ).toHaveAttribute('aria-busy', 'true')
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).not.toBeInTheDocument()
    resolve?.(binding('quality'))
    expect(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('region', { name: 'common.enterprise.devices.qualityBinding' }),
    ).toHaveAttribute('aria-busy', 'false')
  })

  it('should distinguish failed quality runs from an empty successful refresh', async () => {
    let unavailable = true
    api.runs.mockImplementation(async ({ params }) => {
      if (params.scenario === 'quality' && unavailable) throw new ORPCError('INTERNAL_SERVER_ERROR')
      return { items: [], offset: 0, limit: 5, total: 0 }
    })
    renderDetail()
    await userEvent.click(
      await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
    )
    const failure = await screen.findByRole('alert')
    expect(
      within(screen.getByRole('tabpanel')).queryByText('common.noData'),
    ).not.toBeInTheDocument()
    unavailable = false
    await userEvent.click(within(failure).getByRole('button', { name: 'common.operation.retry' }))
    expect(
      await within(screen.getByRole('tabpanel')).findByText('common.noData'),
    ).toBeInTheDocument()
    expect(api.runs.mock.calls.filter(([input]) => input.params.scenario === 'alert')).toHaveLength(
      1,
    )
  })

  it.each(['workspace', 'device', 'scenario'] as const)(
    'should reject a quality binding with a mismatched %s',
    async (field) => {
      api.binding.mockImplementation(async ({ params }) => {
        if (params.scenario === 'alert') return binding('alert')
        const response = binding('quality')
        if (field === 'workspace') response.workspace_id = 'other-workspace'
        if (field === 'device') response.device_id = 'other-device'
        if (field === 'scenario') response.scenario = 'alert'
        return response
      })
      renderDetail()
      await userEvent.click(
        await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
      )
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.devices.loadError',
      )
      expect(
        screen.queryByRole('button', { name: 'common.enterprise.devices.enqueue' }),
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('link', { name: 'common.enterprise.devices.qualityBinding' }),
      ).not.toBeInTheDocument()
    },
  )

  it.each(['device', 'scenario'] as const)(
    'should reject quality run records with a mismatched %s',
    async (field) => {
      api.runs.mockImplementation(async ({ params }) => ({
        items:
          params.scenario === 'alert'
            ? []
            : [
                {
                  ...queued(field === 'scenario' ? 'alert' : 'quality'),
                  device_id: field === 'device' ? 'other-device' : 'device-1',
                  id: 'wrong-run',
                },
              ],
        offset: 0,
        limit: 5,
        total: params.scenario === 'alert' ? 0 : 1,
      }))
      renderDetail()
      await userEvent.click(
        await screen.findByRole('tab', { name: 'common.enterprise.devices.scenario.quality' }),
      )
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'common.enterprise.devices.loadError',
      )
      expect(screen.queryByRole('link', { name: 'wrong-run' })).not.toBeInTheDocument()
    },
  )

  it('should distinguish an absent binding from a failed service', async () => {
    api.binding.mockRejectedValue(new ORPCError('NOT_FOUND'))
    renderDetail()
    const panel = await screen.findByRole('tabpanel')
    expect(
      await within(panel).findByText('common.enterprise.devices.bindingMissing'),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    ).not.toBeInTheDocument()
  })

  it('should reuse the exact request key and parameters when a queue request is retried', async () => {
    api.enqueue.mockRejectedValueOnce(new Error('response lost'))
    renderDetail()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(2))
    expect(api.enqueue.mock.calls[1]?.[0]).toEqual(api.enqueue.mock.calls[0]?.[0])
    expect(api.enqueue.mock.calls[0]?.[0].body).toEqual({
      expected_binding_revision: 2,
      parameters: {},
    })
  })

  it('should reject array parameters before enqueueing', async () => {
    renderDetail()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    const field = screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' })
    await userEvent.clear(field)
    await userEvent.type(field, '[1]')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.invalidParameters',
    )
    expect(api.enqueue).not.toHaveBeenCalled()
  })

  it('should let a user correct parameters after a definitive validation rejection', async () => {
    api.enqueue.mockRejectedValueOnce(new ORPCError('VALIDATION_ERROR', { status: 422 }))
    renderDetail()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await screen.findByRole('alert')
    const field = screen.getByRole('textbox', { name: 'common.enterprise.devices.parameters' })
    expect(field).not.toBeDisabled()
    await userEvent.clear(field)
    await userEvent.type(field, '{{"batch":"0001"}')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(2))
    expect(api.enqueue.mock.calls[1]?.[0].body.parameters).toEqual({ batch: '0001' })
    expect(api.enqueue.mock.calls[1]?.[0].headers['idempotency-key']).not.toBe(
      api.enqueue.mock.calls[0]?.[0].headers['idempotency-key'],
    )
  })

  it.each(['alert', 'quality'] as const)(
    'should isolate retained %s attempts when navigating to another cached device',
    async (scenario) => {
      api.enqueue.mockRejectedValueOnce(new Error('response lost'))
      const { client, rerender } = renderDetail()
      await userEvent.click(
        await screen.findByRole('tab', { name: `common.enterprise.devices.scenario.${scenario}` }),
      )
      await userEvent.click(
        await screen.findByRole('button', { name: 'common.enterprise.devices.enqueue' }),
      )
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      await screen.findByRole('alert')
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.cancel' }))
      const second = {
        id: 'device-2',
        workspace_id: 'workspace-1',
        device_code: '0002',
        name: 'Valve',
        department: '',
        description: '',
        deleted_at: null,
        revision: 1,
        created_at: '2026-09-08T00:00:00Z',
        updated_at: '2026-09-08T00:00:00Z',
      }
      const secondBinding = { ...binding(scenario), id: 'binding-2', device_id: 'device-2' }
      api.get.mockResolvedValue(second)
      api.binding.mockResolvedValue(secondBinding)
      client.setQueryData(
        ['enterprise-business', 'workspace-1', 'user-1', 'device', 'device-2'],
        second,
      )
      client.setQueryData(
        ['enterprise-business', 'workspace-1', 'user-1', 'binding', 'device-2', scenario],
        secondBinding,
      )
      rerender('device-2')
      await screen.findByRole('heading', { name: 'Valve' })
      await userEvent.click(
        screen.getByRole('tab', { name: `common.enterprise.devices.scenario.${scenario}` }),
      )
      await userEvent.click(
        screen.getByRole('button', { name: 'common.enterprise.devices.enqueue' }),
      )
      await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
      await waitFor(() => expect(api.enqueue).toHaveBeenCalledTimes(2))
      expect(api.enqueue.mock.calls[1]?.[0].params.device_id).toBe('device-2')
      expect(api.enqueue.mock.calls[1]?.[0].params.scenario).toBe(scenario)
    },
  )
})
