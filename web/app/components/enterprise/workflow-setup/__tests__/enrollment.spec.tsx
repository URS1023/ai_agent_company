import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zEnrollmentView } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EnrollmentSection } from '../enrollment'
import activationFixture from './fixtures/activation.json'
import fixture from './fixtures/enrollment.json'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  find: vi.fn<Client['workflowProvisioning']['byProvisioningId']['enrollment']['get']>(),
  start: vi.fn<Client['workflowProvisioning']['byProvisioningId']['enrollment']['post']>(),
  advance: vi.fn<Client['workflowEnrollments']['byEnrollmentId']['advance']['post']>(),
  activation: vi.fn<Client['workflowEnrollments']['byEnrollmentId']['activation']['get']>(),
  activate: vi.fn<Client['workflowEnrollments']['byEnrollmentId']['activation']['post']>(),
  specifications:
    vi.fn<Client['workflowEnrollments']['byEnrollmentId']['activationSpecifications']['get']>(),
  revoke: vi.fn<Client['workflowActivations']['byActivationId']['revoke']['post']>(),
}))
vi.mock('@/service/client', async () => {
  const business = {
    workflowProvisioning: { byProvisioningId: { enrollment: { get: api.find, post: api.start } } },
    workflowEnrollments: {
      byEnrollmentId: {
        advance: { post: api.advance },
        activation: { get: api.activation, post: api.activate },
        activationSpecifications: { get: api.specifications },
      },
    },
    workflowActivations: { byActivationId: { revoke: { post: api.revoke } } },
  }
  return {
    consoleQuery: {
      business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils(business),
    },
  }
})
const view = zEnrollmentView.parse(fixture)
function mount(disabled = false, provisioning = view.provisioning) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <EnrollmentSection provisioning={provisioning} disabled={disabled} />
    </QueryClientProvider>,
  )
}
beforeEach(() => vi.resetAllMocks())

it('mounts activation only after matching token storage and preserves disabled permission', async () => {
  const completed = zEnrollmentView.parse(activationFixture.enrollment)
  api.find.mockResolvedValue(completed)
  api.activation.mockResolvedValue(null)
  api.specifications.mockResolvedValue({ items: ['spec-1'] })
  mount(true, completed.provisioning)
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.activation.activate' }),
  ).toBeDisabled()
  expect(api.activation).toHaveBeenCalledTimes(1)
  expect(api.activate).not.toHaveBeenCalled()
})

it('does not mount activation for a mismatched completed record', async () => {
  const completed = zEnrollmentView.parse(activationFixture.enrollment)
  api.find.mockResolvedValue({
    ...completed,
    provisioning: { ...completed.provisioning, actor_id: 'other' },
  })
  mount(false, completed.provisioning)
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(api.activation).not.toHaveBeenCalled()
})

it('queries existing enrollment without creating on mount', async () => {
  api.find.mockResolvedValue(null)
  mount()
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.enrollment.start' }),
  ).toBeEnabled()
  expect(api.start).not.toHaveBeenCalled()
})

it('blocks a claimed state recovered after reload', async () => {
  api.find.mockResolvedValue({ ...view, state: 'token_claimed', revision: 4 })
  mount()
  expect(
    await screen.findByRole('button', { name: 'common.enterprise.provisioning.continue' }),
  ).toBeDisabled()
  expect(api.advance).not.toHaveBeenCalled()
})

it('keeps a rejected publication check labelled as verification rather than credential storage', async () => {
  api.find.mockResolvedValue({
    ...view,
    state: 'rejected',
    reason_code: 'native_publication_unconfirmed',
    revision: 3,
  })
  mount()
  expect(await screen.findByText('common.enterprise.enrollment.verify')).toBeInTheDocument()
  expect(screen.queryByText('common.enterprise.enrollment.credential')).not.toBeInTheDocument()
})

it('blocks another workspace snapshot', async () => {
  api.find.mockResolvedValue({
    ...view,
    provisioning: { ...view.provisioning, workspace_id: 'other' },
  })
  mount()
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(
    screen.getByRole('button', { name: 'common.enterprise.provisioning.continue' }),
  ).toBeDisabled()
})

it('advances only explicitly and never retries a lost response', async () => {
  api.find.mockResolvedValue(view)
  api.advance.mockRejectedValue(new Error('lost response'))
  mount()
  const button = await screen.findByRole('button', {
    name: 'common.enterprise.provisioning.continue',
  })
  expect(api.advance).not.toHaveBeenCalled()
  await userEvent.click(button)
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(button).toBeDisabled()
  expect(api.advance).toHaveBeenCalledTimes(1)
})
