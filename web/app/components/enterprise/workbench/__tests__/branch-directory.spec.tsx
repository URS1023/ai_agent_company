import type { BranchContext } from '@enterprise/business-contracts/types'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WorkbenchBranchDirectory } from '../branch-directory'

const api = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('@/service/client', () => ({
  consoleQuery: {
    business: {
      workbench: {
        apps: {
          byInstalledAppId: {
            branches: {
              get: {
                queryOptions: (options: { input: unknown }) => ({
                  ...options,
                  queryFn: () => api.read(options.input),
                }),
              },
            },
          },
        },
      },
    },
  },
}))
const scope = {
  workspace_id: 'workspace',
  actor_id: 'actor',
  installed_app_id: '00000000-0000-4000-8000-000000000001',
}
function branch(id: string): BranchContext {
  return {
    scope: { ...scope, branch_id: id },
    state: 'ready',
    revision: 1,
    conversation_id: null,
    head_message_id: null,
    inflight_client_message_id: null,
    origin: null,
  }
}
function mount() {
  const select = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <WorkbenchBranchDirectory scope={scope} title="Conversations" onSelect={select} />
    </QueryClientProvider>,
  )
  return { ...view, select, client }
}
beforeEach(() => {
  vi.clearAllMocks()
})

// Exercise real query ownership, section states and controls; only the network is substituted.
describe('workbench branch directory', () => {
  it('should paginate and select the scoped branch without generating a message', async () => {
    api.read
      .mockResolvedValueOnce({
        items: Array.from({ length: 50 }, (_, i) => branch(`branch-${i}`)),
        next_after: 'branch-49',
      })
      .mockResolvedValue({ items: [branch('last')], next_after: null })
    const { select } = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'branch-0' }))
    expect(select).toHaveBeenCalledWith(branch('branch-0'))
    fireEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    expect(await screen.findByRole('button', { name: 'last' })).toBeInTheDocument()
    expect(api.read).toHaveBeenLastCalledWith({
      params: { installed_app_id: scope.installed_app_id },
      query: { limit: 50, after: 'branch-49' },
    })
    expect(screen.getByRole('button', { name: 'common.pagination.next' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'common.pagination.previous' }))
    expect(await screen.findByRole('button', { name: 'branch-0' })).toBeInTheDocument()
  })
  it('should show a loading section then an empty result', async () => {
    let resolve!: (value: unknown) => void
    api.read.mockReturnValue(
      new Promise((done) => {
        resolve = done
      }),
    )
    mount()
    expect(screen.getByRole('region', { name: 'Conversations' })).toHaveAttribute(
      'aria-busy',
      'true',
    )
    resolve({ items: [] })
    expect(await screen.findByText('common.noData')).toBeInTheDocument()
  })
  it('should allow an explicit retry after failure without automatic retries', async () => {
    api.read
      .mockRejectedValueOnce(new Error('private details'))
      .mockResolvedValue({ items: [branch('restored')] })
    mount()
    expect(await screen.findByRole('alert')).not.toHaveTextContent('private details')
    expect(api.read).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByRole('button', { name: 'restored' })).toBeInTheDocument()
  })
  it.each([
    {
      items: [
        { ...branch('foreign'), scope: { ...scope, branch_id: 'foreign', actor_id: 'other' } },
      ],
    },
    { items: [branch('duplicate'), branch('duplicate')] },
    { items: [branch('bad-cursor')], next_after: 'elsewhere' },
    { items: 'malformed' },
    { items: Array.from({ length: 51 }, (_, i) => branch(`overflow-${i}`)) },
    {
      items: [
        { ...branch('foreign'), scope: { ...scope, branch_id: 'foreign', workspace_id: 'other' } },
      ],
    },
    {
      items: [
        {
          ...branch('foreign'),
          scope: {
            ...scope,
            branch_id: 'foreign',
            installed_app_id: '00000000-0000-4000-8000-000000000099',
          },
        },
      ],
    },
  ])('should hide invalid or foreign directory data: %j', async (response) => {
    api.read.mockResolvedValue(response)
    const { select } = mount()
    await screen.findByRole('alert')
    expect(select).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'foreign' })).not.toBeInTheDocument()
  })
  it('should keep previous-page navigation available after a failed next page', async () => {
    const first = {
      items: Array.from({ length: 50 }, (_, i) => branch(`row-${i}`)),
      next_after: 'row-49',
    }
    api.read
      .mockResolvedValueOnce(first)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue(first)
    mount()
    await screen.findByRole('button', { name: 'row-0' })
    fireEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: 'row-0' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'common.pagination.previous' }))
    expect(await screen.findByRole('button', { name: 'row-0' })).toBeInTheDocument()
  })
  it('should reject a next page which repeats its input cursor', async () => {
    api.read
      .mockResolvedValueOnce({
        items: Array.from({ length: 50 }, (_, i) => branch(`row-${i}`)),
        next_after: 'row-49',
      })
      .mockResolvedValue({ items: [branch('row-49')] })
    mount()
    await screen.findByRole('button', { name: 'row-0' })
    fireEvent.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: 'row-49' })).not.toBeInTheDocument()
  })
  it('should discard the old page and cache scope when the account changes', async () => {
    api.read
      .mockResolvedValueOnce({ items: [branch('old-account')] })
      .mockResolvedValue({ items: [] })
    const { rerender, client, select } = mount()
    await screen.findByRole('button', { name: 'old-account' })
    rerender(
      <QueryClientProvider client={client}>
        <WorkbenchBranchDirectory
          scope={{ ...scope, actor_id: 'new-account' }}
          title="Conversations"
          onSelect={select}
        />
      </QueryClientProvider>,
    )
    expect(screen.queryByRole('button', { name: 'old-account' })).not.toBeInTheDocument()
    await waitFor(() => expect(api.read).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('common.noData')).toBeInTheDocument()
  })
})
