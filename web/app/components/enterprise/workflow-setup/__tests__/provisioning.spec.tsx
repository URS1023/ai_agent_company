import type { contract } from '@enterprise/business-contracts/orpc'
import type { SetupView } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import type { ProvisioningSafety } from '../provisioning'
import { zEnrollmentView } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProvisioningSection } from '../provisioning'
import enrollmentFixture from './fixtures/enrollment.json'

type Client = ContractRouterClient<typeof contract>
type ProvisioningView = Awaited<
  ReturnType<Client['workflowProvisioning']['byProvisioningId']['get']>
>
const api = vi.hoisted(() => ({
  list: vi.fn<Client['workflowSetups']['bySetupId']['provisioning']['get']>(),
  profiles: vi.fn<Client['workflowSetups']['bySetupId']['provisioningProfiles']['get']>(),
  start: vi.fn<Client['workflowSetups']['bySetupId']['provisioning']['post']>(),
  get: vi.fn<Client['workflowProvisioning']['byProvisioningId']['get']>(),
  advance: vi.fn<Client['workflowProvisioning']['byProvisioningId']['advance']['post']>(),
  enrollment: vi.fn<Client['workflowProvisioning']['byProvisioningId']['enrollment']['get']>(),
  startEnrollment:
    vi.fn<Client['workflowProvisioning']['byProvisioningId']['enrollment']['post']>(),
  advanceEnrollment: vi.fn<Client['workflowEnrollments']['byEnrollmentId']['advance']['post']>(),
}))
vi.mock('@/service/client', async () => {
  const business = {
    workflowSetups: {
      bySetupId: {
        provisioning: { get: api.list, post: api.start },
        provisioningProfiles: { get: api.profiles },
      },
    },
    workflowProvisioning: {
      byProvisioningId: {
        get: api.get,
        advance: { post: api.advance },
        enrollment: { get: api.enrollment, post: api.startEnrollment },
      },
    },
    workflowEnrollments: { byEnrollmentId: { advance: { post: api.advanceEnrollment } } },
  }
  return {
    consoleClient: { business },
    consoleQuery: {
      business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils(business),
    },
  }
})
const setup: SetupView = {
  id: 'setup',
  workspace_id: 'ws',
  app_id: 'app',
  device_id: 'device',
  scenario: 'alert',
  source_id: 'source',
  source_revision: 's1',
  read_id: 'read',
  read_revision: 'r1',
  expected_source_revision: 1,
  expected_binding_revision: null,
  revision: 3,
  state: 'draft_ready',
  name: 'Draft',
  created_at: '2026-09-10T00:00:00Z',
  updated_at: '2026-09-10T00:00:00Z',
}
function record(changes: Partial<ProvisioningView> = {}): ProvisioningView {
  return {
    id: 'operation',
    workspace_id: 'ws',
    actor_id: 'actor',
    app_id: 'app',
    device_id: 'device',
    scenario: 'alert',
    source_id: 'source',
    source_revision: 's1',
    read_id: 'read',
    read_revision: 'r1',
    expected_source_revision: 1,
    expected_binding_revision: null,
    setup_id: 'setup',
    setup_revision: 3,
    config_ref: 'profile',
    config_revision: 1,
    revision: 1,
    state: 'in_progress',
    phases: [],
    created_at: setup.created_at,
    updated_at: setup.updated_at,
    ...changes,
  }
}
function mount(safety: ProvisioningSafety = {}, linkedSetup: SetupView = setup) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <ProvisioningSection
        setup={linkedSetup}
        scope={['enterprise-business', linkedSetup.workspace_id, 'actor']}
        disabled={false}
        safety={safety}
        onSafetyChange={vi.fn()}
      />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  api.profiles.mockResolvedValue([
    { config_ref: 'profile', config_revision: 1, display_name: 'Production' },
  ])
  api.list.mockResolvedValue({ items: [], offset: 0, limit: 20, total: 0 })
  api.enrollment.mockResolvedValue(null)
})

it('renders actual enrollment controls after all four publication phases succeeded', async () => {
  const published = zEnrollmentView.parse(enrollmentFixture).provisioning
  const linkedSetup: SetupView = {
    ...setup,
    ...published,
    id: published.setup_id,
    revision: published.setup_revision,
    state: 'draft_ready',
  }
  api.list.mockResolvedValue({ items: [published], offset: 0, limit: 20, total: 1 })
  api.get.mockResolvedValue(published)
  mount({ operationId: published.id }, linkedSetup)
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.enrollment.start' }),
  ).toBeEnabled()
  expect(api.startEnrollment).not.toHaveBeenCalled()
})
it('shows operator profile labels without asking for configuration references', async () => {
  mount()
  expect(await screen.findByRole('combobox')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('combobox'))
  expect(await screen.findByRole('option', { name: 'Production · 1' })).toBeInTheDocument()
})
it('recovers an operation from persisted history and advances only explicitly', async () => {
  const view = record()
  api.list.mockResolvedValue({ items: [view], offset: 0, limit: 20, total: 1 })
  api.get.mockResolvedValue(view)
  mount()
  await userEvent.click(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.inspect' }),
  )
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.continue' }),
  ).toBeEnabled()
  expect(api.advance).not.toHaveBeenCalled()
})
it.each(['uncertain', 'rejected', 'published_pending_enrollment'] as const)(
  'blocks advancing %s',
  async (state) => {
    const view = record({ state })
    api.list.mockResolvedValue({ items: [view], offset: 0, limit: 20, total: 1 })
    api.get.mockResolvedValue(view)
    mount()
    await userEvent.click(
      await screen.findByRole('button', { name: 'common.enterprise.provisioning.inspect' }),
    )
    expect(
      await screen.findByRole('button', { name: 'common.enterprise.provisioning.continue' }),
    ).toBeDisabled()
  },
)

it.each(['succeeded', 'claimed'] as const)(
  'reconciles a lost advance only when persisted phase is %s',
  async (state) => {
    const view = record({
      revision: 3,
      phases: [
        {
          command: { kind: 'read_draft', operation_id: 'phase', app_id: 'app' },
          state,
          revision: 2,
          created_at: setup.created_at,
          updated_at: setup.updated_at,
          receipt:
            state === 'succeeded'
              ? {
                  kind: 'read_draft',
                  workspace_id: 'ws',
                  app_id: 'app',
                  draft_id: 'draft',
                  draft_hash: 'a'.repeat(64),
                }
              : null,
        },
      ],
    })
    api.list.mockResolvedValue({ items: [view], offset: 0, limit: 20, total: 1 })
    api.get.mockResolvedValue(view)
    mount({
      blocked: true,
      operationId: view.id,
      pendingAdvance: {
        id: view.id,
        revision: 1,
        configRef: view.config_ref,
        configRevision: view.config_revision,
      },
    })
    const button = await screen.findByRole('button', {
      name: 'common.enterprise.provisioning.continue',
    })
    if (state === 'succeeded') expect(button).toBeEnabled()
    else expect(button).toBeDisabled()
    expect(api.advance).not.toHaveBeenCalled()
  },
)

it('sends one frozen start command and does not retry a lost response', async () => {
  api.start.mockRejectedValue(new Error('lost'))
  mount()
  await userEvent.click(await screen.findByRole('combobox'))
  await userEvent.click(await screen.findByRole('option', { name: 'Production · 1' }))
  await userEvent.click(
    screen.getByRole('button', { name: 'common.enterprise.provisioning.start' }),
  )
  expect(await screen.findByText('common.enterprise.provisioning.review')).toBeInTheDocument()
  expect(api.start).toHaveBeenCalledTimes(1)
  expect(api.start).toHaveBeenCalledWith(
    expect.objectContaining({
      params: { setup_id: 'setup' },
      body: { config_ref: 'profile', config_revision: 1 },
      headers: expect.objectContaining({ 'idempotency-key': expect.any(String) }),
    }),
    expect.anything(),
  )
  expect(
    screen.getByRole('button', { name: 'common.enterprise.provisioning.start' }),
  ).toBeDisabled()
})
it('rejects history from another actor before inspection', async () => {
  api.list.mockResolvedValue({
    items: [record({ actor_id: 'other' })],
    offset: 0,
    limit: 20,
    total: 1,
  })
  mount()
  expect(await screen.findByText('common.enterprise.provisioning.unavailable')).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: 'common.enterprise.provisioning.inspect' }),
  ).not.toBeInTheDocument()
})
it('uses server pagination for persisted operation recovery', async () => {
  api.list
    .mockResolvedValueOnce({ items: [record()], offset: 0, limit: 20, total: 21 })
    .mockResolvedValueOnce({ items: [record({ id: 'older' })], offset: 20, limit: 20, total: 21 })
  mount()
  await userEvent.click(
    await screen.findByRole('button', { name: 'common.enterprise.setup.moreHistory' }),
  )
  expect(api.list).toHaveBeenLastCalledWith({
    params: { setup_id: 'setup' },
    query: { offset: 20, limit: 20 },
  })
})
it('keeps an unchanged operation blocked after a lost advance', async () => {
  api.get.mockResolvedValue(record())
  mount({
    blocked: true,
    operationId: 'operation',
    pendingAdvance: { id: 'operation', revision: 1, configRef: 'profile', configRevision: 1 },
  })
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.continue' }),
  ).toBeDisabled()
  expect(api.advance).not.toHaveBeenCalled()
})

it('allows explicit use of inspected persisted operation after a lost start without resending start', async () => {
  api.list.mockResolvedValue({ items: [record()], offset: 0, limit: 20, total: 1 })
  api.get.mockResolvedValue(record())
  mount({
    blocked: true,
    attempt: { key: 'lost-key', body: { config_ref: 'profile', config_revision: 1 } },
  })
  await userEvent.click(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.inspect' }),
  )
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.continue' }),
  ).toBeEnabled()
  expect(api.start).not.toHaveBeenCalled()
})
