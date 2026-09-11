import type { contract } from '@enterprise/business-contracts/orpc'
import type { IntervalSchedule } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DeviceSchedule } from '../schedule'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  get: vi.fn<Client['devices']['byDeviceId']['byScenario']['schedule']['get']>(),
  state: vi.fn<Client['schedules']['byScheduleId']['state']['post']>(),
  actors: vi.fn<Client['devices']['byDeviceId']['byScenario']['schedule']['actors']['get']>(),
  create: vi.fn<Client['devices']['byDeviceId']['byScenario']['schedule']['post']>(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      devices: {
        byDeviceId: {
          byScenario: { schedule: { get: api.get, post: api.create, actors: { get: api.actors } } },
        },
      },
      schedules: { byScheduleId: { state: { post: api.state } } },
    }),
  },
}))

const value = {
  id: 'schedule-1',
  workspace_id: 'workspace-1',
  device_id: 'device-1',
  scenario: 'alert',
  revision: 3,
  binding_id: 'binding-1',
  binding_revision: 2,
  service_actor_id: 'worker',
  anchor_at: '2026-09-11T00:00:00Z',
  next_due_at: '2026-09-11T00:10:00Z',
  interval_seconds: 60,
  window_seconds: 300,
  grace_seconds: 5,
  missed_policy: 'skip',
  enabled: true,
} satisfies IntervalSchedule
function mount(manage = true, bindingRevision?: number) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <DeviceSchedule
        deviceId="device-1"
        scenario="alert"
        bindingRevision={bindingRevision}
        scope={['enterprise-business', 'workspace-1', 'user-1']}
        access={{
          actor_id: 'user-1',
          workspace_id: 'workspace-1',
          display_name: 'User',
          permissions: { read: true, manage, run: false, review: false },
        }}
      />
    </QueryClientProvider>,
  )
}

// Server state, not a local toggle, controls every displayed result.
describe('device schedule management', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.get.mockResolvedValue(value)
    api.state.mockResolvedValue({ ...value, enabled: false, revision: 4 })
    api.actors.mockResolvedValue([{ actor_id: 'worker', display_name: 'Factory worker' }])
    api.create.mockResolvedValue({ ...value, enabled: false, revision: 1 })
  })
  it('should display the server state for a read-only member without mutation controls', async () => {
    mount(false)
    expect(await screen.findByText('common.enterprise.schedule.enabled')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.schedule.pause' }),
    ).not.toBeInTheDocument()
    expect(api.get.mock.calls[0]![0].params).toEqual({ device_id: 'device-1', scenario: 'alert' })
  })
  it('should show an empty result without fabricating a schedule', async () => {
    api.get.mockResolvedValue(null)
    mount()
    expect(await screen.findByText('common.enterprise.schedule.empty')).toBeInTheDocument()
    expect(api.state).not.toHaveBeenCalled()
  })

  it('should create from current server choices and recover the paused record by lookup', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    mount(true, 2)
    const start = await screen.findByLabelText('common.enterprise.schedule.anchor')
    fireEvent.change(start, { target: { value: '2026-09-11T08:30' } })
    api.get.mockResolvedValue({ ...value, revision: 1, enabled: false })
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await screen.findByText('common.enterprise.schedule.paused')
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(api.create.mock.calls[0]![0]).toEqual({
      params: { device_id: 'device-1', scenario: 'alert' },
      headers: { Origin: window.location.origin },
      body: {
        service_actor_id: 'worker',
        expected_binding_revision: 2,
        anchor_at: new Date('2026-09-11T08:30').toISOString(),
        interval_seconds: 60,
        window_seconds: 300,
        grace_seconds: 10,
        missed_policy: 'skip',
      },
    })
    expect(api.state).not.toHaveBeenCalled()
  })

  it('should keep an uncertain creation locked after an empty refresh and recover without another POST', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    api.create.mockRejectedValue(new Error('private connection'))
    mount(true, 2)
    fireEvent.change(await screen.findByLabelText('common.enterprise.schedule.anchor'), {
      target: { value: '2026-09-11T08:30' },
    })
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'common.enterprise.provisioning.refresh' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeDisabled()
    api.get.mockResolvedValue({ ...value, enabled: false, revision: 1 })
    await user.click(screen.getByRole('button', { name: 'common.enterprise.provisioning.refresh' }))
    await screen.findByText('common.enterprise.schedule.paused')
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('private connection')).not.toBeInTheDocument()
  })

  it('should not discover execution accounts for a read-only member', async () => {
    api.get.mockResolvedValue(null)
    mount(false, 2)
    await screen.findByText('common.enterprise.schedule.empty')
    expect(api.actors).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'common.operation.save' })).not.toBeInTheDocument()
  })

  it('should explicitly reopen a rejected validation request only after refreshing server state', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    api.create.mockRejectedValueOnce(new ORPCError('UNPROCESSABLE_CONTENT', { status: 422 }))
    mount(true, 2)
    fireEvent.change(await screen.findByLabelText('common.enterprise.schedule.anchor'), {
      target: { value: '2026-09-11T08:30' },
    })
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await user.click(await screen.findByRole('button', { name: 'common.operation.edit' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeEnabled(),
    )
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(api.actors.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(api.create).toHaveBeenCalledTimes(1)
    fireEvent.change(screen.getByLabelText('common.enterprise.schedule.interval'), {
      target: { value: '120' },
    })
    api.get.mockResolvedValue({ ...value, enabled: false, revision: 1 })
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await screen.findByText('common.enterprise.schedule.paused')
    expect(api.create).toHaveBeenCalledTimes(2)
    expect(api.create.mock.calls[1]![0].body.interval_seconds).toBe(120)
  })

  it.each([409, 503])(
    'should not offer an edit-and-resubmit shortcut for status %s',
    async (status) => {
      const user = userEvent.setup()
      api.get.mockResolvedValue(null)
      api.create.mockRejectedValue(new ORPCError('ERROR', { status }))
      mount(true, 2)
      fireEvent.change(await screen.findByLabelText('common.enterprise.schedule.anchor'), {
        target: { value: '2026-09-11T08:30' },
      })
      await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
      await screen.findByRole('alert')
      expect(
        screen.queryByRole('button', { name: 'common.operation.edit' }),
      ).not.toBeInTheDocument()
      expect(api.create).toHaveBeenCalledTimes(1)
    },
  )

  it('should keep validation retry locked if its recovery read fails', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    api.create.mockRejectedValue(new ORPCError('UNPROCESSABLE_CONTENT', { status: 422 }))
    mount(true, 2)
    fireEvent.change(await screen.findByLabelText('common.enterprise.schedule.anchor'), {
      target: { value: '2026-09-11T08:30' },
    })
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    const edit = await screen.findByRole('button', { name: 'common.operation.edit' })
    api.get.mockRejectedValue(new Error('offline'))
    await user.click(edit)
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'common.operation.save' }),
      ).not.toBeInTheDocument(),
    )
    expect(api.create).toHaveBeenCalledTimes(1)
  })

  it('should explain missing eligible actors without enabling creation', async () => {
    api.get.mockResolvedValue(null)
    api.actors.mockResolvedValue([])
    mount(true, 2)
    await screen.findByText('common.enterprise.schedule.noActors')
    expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeDisabled()
    expect(api.create).not.toHaveBeenCalled()
  })

  it('should show an actor lookup failure rather than a usable creation form', async () => {
    api.get.mockResolvedValue(null)
    api.actors.mockRejectedValue(new Error('private worker credentials'))
    mount(true, 2)
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: 'common.operation.save' })).not.toBeInTheDocument()
    expect(screen.queryByText('private worker credentials')).not.toBeInTheDocument()
    expect(api.create).not.toHaveBeenCalled()
  })
  it('should pause the exact revision and reload the persisted state', async () => {
    const user = userEvent.setup()
    mount()
    const pause = await screen.findByRole('button', { name: 'common.enterprise.schedule.pause' })
    api.get.mockResolvedValue({ ...value, enabled: false, revision: 4 })
    await user.click(pause)
    await screen.findByText('common.enterprise.schedule.paused')
    expect(api.state.mock.calls[0]![0]).toEqual({
      params: { schedule_id: 'schedule-1' },
      headers: { Origin: window.location.origin },
      body: { expected_revision: 3, enabled: false },
    })
    expect(api.state).toHaveBeenCalledTimes(1)
  })
  it('should refresh rather than replay an uncertain mutation', async () => {
    api.state.mockRejectedValue(new Error('private error'))
    const user = userEvent.setup()
    mount()
    await user.click(
      await screen.findByRole('button', { name: 'common.enterprise.schedule.pause' }),
    )
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: 'common.enterprise.schedule.pause' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'common.enterprise.provisioning.refresh' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
    expect(api.state).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('private error')).not.toBeInTheDocument()
  })

  it('should resume a paused schedule using its current revision', async () => {
    api.get.mockResolvedValue({ ...value, enabled: false })
    const user = userEvent.setup()
    mount()
    await user.click(
      await screen.findByRole('button', { name: 'common.enterprise.schedule.resume' }),
    )
    expect(api.state.mock.calls[0]![0].body).toEqual({ expected_revision: 3, enabled: true })
  })

  it('should show a read failure rather than an empty configuration on service errors', async () => {
    api.get.mockRejectedValue(new Error('private connection details'))
    mount()
    await screen.findByRole('alert')
    expect(screen.queryByText('common.enterprise.schedule.empty')).not.toBeInTheDocument()
    expect(screen.queryByText('private connection details')).not.toBeInTheDocument()
    expect(api.state).not.toHaveBeenCalled()
  })
  it.each([{ workspace_id: 'other' }, { device_id: 'other' }, { scenario: 'quality' as const }])(
    'should reject mismatched server scope %j',
    async (patch) => {
      api.get.mockResolvedValue({ ...value, ...patch })
      mount()
      await screen.findByRole('alert')
      expect(
        screen.queryByRole('button', { name: 'common.enterprise.schedule.pause' }),
      ).not.toBeInTheDocument()
    },
  )
})
