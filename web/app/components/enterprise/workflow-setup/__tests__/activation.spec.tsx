import type { contract } from '@enterprise/business-contracts/orpc'
import type { ContractRouterClient } from '@orpc/contract'
import { zActivationView } from '@enterprise/business-contracts/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ActivationSection } from '../activation'
import fixture from './fixtures/activation.json'

type Client = ContractRouterClient<typeof contract>
const api = vi.hoisted(() => ({
  find: vi.fn<Client['workflowEnrollments']['byEnrollmentId']['activation']['get']>(),
  start: vi.fn<Client['workflowEnrollments']['byEnrollmentId']['activation']['post']>(),
  specifications:
    vi.fn<Client['workflowEnrollments']['byEnrollmentId']['activationSpecifications']['get']>(),
  revoke: vi.fn<Client['workflowActivations']['byActivationId']['revoke']['post']>(),
}))
vi.mock('@/service/client', async () => ({
  consoleQuery: {
    business: (await import('@orpc/tanstack-query')).createTanstackQueryUtils({
      workflowEnrollments: {
        byEnrollmentId: {
          activation: { get: api.find, post: api.start },
          activationSpecifications: { get: api.specifications },
        },
      },
      workflowActivations: { byActivationId: { revoke: { post: api.revoke } } },
    }),
  },
}))
const value = zActivationView.parse(fixture)
function mount(disabled = false) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ActivationSection enrollment={value.enrollment} disabled={disabled} />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  vi.resetAllMocks()
  api.specifications.mockResolvedValue({ items: ['spec-1'] })
})

it('recovers active registration without creating again', async () => {
  api.find.mockResolvedValue(value)
  mount()
  expect(await screen.findByText('common.enterprise.activation.active')).toBeInTheDocument()
  expect(api.start).not.toHaveBeenCalled()
})

it('keeps revoked registration terminal', async () => {
  api.find.mockResolvedValue({ ...value, active: false, revision: 2 })
  mount()
  expect(await screen.findByText('common.enterprise.activation.revoked')).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: 'common.enterprise.activation.activate' }),
  ).not.toBeInTheDocument()
})

it('does not repeat an ambiguous revocation', async () => {
  api.find.mockResolvedValue(value)
  api.revoke.mockRejectedValue(new Error('lost'))
  mount()
  const button = await screen.findByRole('button', { name: 'common.enterprise.activation.revoke' })
  await userEvent.click(button)
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(button).toBeDisabled()
  expect(api.revoke).toHaveBeenCalledTimes(1)
})

it('blocks records from a different enrollment', async () => {
  api.find.mockResolvedValue({ ...value, enrollment: { ...value.enrollment, id: 'other' } })
  mount()
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(api.start).not.toHaveBeenCalled()
  expect(api.revoke).not.toHaveBeenCalled()
})

it('requires choosing a server version before explicit activation', async () => {
  api.find.mockResolvedValue(null)
  api.start.mockImplementation(async () => {
    api.find.mockResolvedValue(value)
    return value
  })
  mount()
  const activate = await screen.findByRole('button', {
    name: 'common.enterprise.activation.activate',
  })
  expect(activate).toBeDisabled()
  await userEvent.click(screen.getByRole('combobox'))
  await userEvent.click(await screen.findByRole('option', { name: 'spec-1' }))
  await userEvent.click(activate)
  expect(await screen.findByText('common.enterprise.activation.active')).toBeInTheDocument()
  expect(api.start).toHaveBeenCalledTimes(1)
  expect(api.start.mock.calls[0]?.[0].body).toEqual({ specification_revision: 'spec-1' })
})
