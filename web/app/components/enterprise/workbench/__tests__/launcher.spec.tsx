import type { MessageScope } from '@enterprise/business-contracts/types'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkbenchLauncher } from '../launcher'

const { createRoot } = vi.hoisted(() => ({ createRoot: vi.fn() }))
vi.mock('@/service/client', () => ({
  consoleClient: {
    business: { workbench: { apps: { byInstalledAppId: { branches: { post: createRoot } } } } },
  },
}))
vi.mock('../session', () => ({
  WorkbenchSession: ({ title }: { title: string }) => <h2>{title}</h2>,
}))
const scope = {
  workspace_id: 'workspace',
  actor_id: 'actor',
  installed_app_id: '00000000-0000-4000-8000-000000000001',
}
const labels = {
  submit: 'Launch',
  required: 'Required',
  invalid: 'Invalid',
  pending: 'Uploading',
  configMissing: 'Configuration missing',
  error: 'Launch failed',
}
function mount() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
    >
      <WorkbenchLauncher
        scope={scope}
        configurationKey="v1"
        title="Device assistant"
        fields={[]}
        labels={labels}
      />
    </QueryClientProvider>,
  )
}
beforeEach(() => vi.clearAllMocks())
describe('workbench root launcher', () => {
  it('should create one root on explicit submission and then mount the session', async () => {
    createRoot.mockImplementation(async ({ body }: { body: { branch_id: string } }) => ({
      scope: { ...scope, branch_id: body.branch_id },
      state: 'ready',
      revision: 1,
    }))
    mount()
    expect(createRoot).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }))
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }))
    await screen.findByRole('heading', { name: 'Device assistant' })
    expect(createRoot).toHaveBeenCalledTimes(1)
    expect(createRoot).toHaveBeenCalledWith({
      params: { installed_app_id: scope.installed_app_id },
      headers: { Origin: window.location.origin },
      body: { branch_id: expect.any(String) },
    })
  })
  it('should retain the root ID after an uncertain request and reject a foreign response', async () => {
    createRoot.mockRejectedValueOnce(new Error('private server error'))
    createRoot.mockImplementationOnce(async ({ body }: { body: { branch_id: string } }) => ({
      scope: { ...scope, actor_id: 'foreign', branch_id: body.branch_id } satisfies MessageScope,
      state: 'ready',
    }))
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }))
    await screen.findByRole('alert')
    expect(screen.getByRole('alert')).toHaveTextContent('Launch failed')
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }))
    await waitFor(() => expect(createRoot).toHaveBeenCalledTimes(2))
    expect(createRoot.mock.calls[0]?.[0]).toEqual(createRoot.mock.calls[1]?.[0])
    expect(screen.queryByRole('heading', { name: 'Device assistant' })).not.toBeInTheDocument()
  })
})
