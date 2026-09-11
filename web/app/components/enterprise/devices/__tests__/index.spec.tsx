import type { contract } from '@enterprise/business-contracts/orpc'
import type { Device, PageDevice } from '@enterprise/business-contracts/types'
import type { ContractRouterClient } from '@orpc/contract'
import { ORPCError } from '@orpc/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider } from 'jotai'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { DevicesPage } from '../index'

type Client = ContractRouterClient<typeof contract>
const testWorkspaceAtom = await vi.hoisted(async () =>
  (await import('jotai')).atom({ id: 'workspace-1', name: 'Plant' }),
)
const api = vi.hoisted(() => ({
  access: vi.fn<Client['me']['get']>(),
  list: vi.fn<Client['devices']['get']>(),
  create: vi.fn<Client['devices']['post']>(),
  update: vi.fn<Client['devices']['byDeviceId']['put']>(),
  remove: vi.fn<Client['devices']['byDeviceId']['delete']>(),
}))
vi.mock('@/service/client', async () => {
  const { createTanstackQueryUtils } = await import('@orpc/tanstack-query')
  return {
    consoleQuery: {
      business: createTanstackQueryUtils({
        me: { get: api.access },
        devices: {
          get: api.list,
          post: api.create,
          byDeviceId: { put: api.update, delete: api.remove },
        },
      }),
    },
  }
})
vi.mock('@/context/workspace-state', () => ({
  currentWorkspaceAtom: testWorkspaceAtom,
}))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('user-1'),
}))

function device() {
  return {
    id: 'device-1',
    workspace_id: 'workspace-1',
    device_code: '0001',
    name: 'Pump',
    department: 'A',
    description: '',
    deleted_at: null,
    revision: 3,
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  } satisfies Device
}
function renderPage(searchParams = '') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const onUrlUpdate = vi.fn()
  const store = createStore()
  render(
    <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate} hasMemory>
      <QueryClientProvider client={client}>
        <Provider store={store}>
          <DevicesPage />
        </Provider>
      </QueryClientProvider>
    </NuqsTestingAdapter>,
  )
  return { client, store, onUrlUpdate }
}

describe('DevicesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: true, run: true, review: false },
    })
    api.list.mockResolvedValue({ items: [device()], total: 21, offset: 0, limit: 20 })
    api.create.mockResolvedValue(device())
    api.update.mockResolvedValue({ ...device(), revision: 4 })
    api.remove.mockResolvedValue(undefined)
  })

  it('should request filtered server pagination and link the device to its detail', async () => {
    renderPage('?page=2&q=0001&department=A')
    expect(await screen.findByRole('link', { name: /Pump/ })).toHaveAttribute(
      'href',
      '/enterprise/devices/device-1',
    )
    expect(api.list.mock.calls[0]?.[0]).toEqual({
      query: { offset: 20, limit: 20, q: '0001', department: 'A' },
    })
  })

  it('should clear the page when new search filters are submitted', async () => {
    const { onUrlUpdate } = renderPage('?page=2')
    await screen.findByRole('link', { name: /Pump/ })
    await userEvent.type(screen.getByRole('textbox', { name: 'common.operation.search' }), '0001')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.search' }))
    await waitFor(() => expect(onUrlUpdate).toHaveBeenCalled())
    expect(onUrlUpdate.mock.calls.at(-1)?.[0].searchParams.get('page')).toBeNull()
  })

  it('should not request devices when the server workspace differs from the current workspace', async () => {
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'other',
      display_name: 'User',
      permissions: { read: true, manage: true, run: true, review: true },
    })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.accessDenied',
    )
    expect(api.list).not.toHaveBeenCalled()
  })

  it('should hide write actions for a read-only member', async () => {
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    renderPage()
    await screen.findByRole('link', { name: /Pump/ })
    expect(
      screen.queryByRole('button', { name: 'common.operation.create' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'common.operation.delete' }),
    ).not.toBeInTheDocument()
  })

  it('should confirm deletion with the observed revision rather than deleting immediately', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.delete' }))
    expect(api.remove).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.confirm' }))
    await waitFor(() => expect(api.remove).toHaveBeenCalled())
    expect(api.remove.mock.calls[0]?.[0]).toEqual({
      params: { device_id: 'device-1' },
      headers: { Origin: window.location.origin, 'if-match': '3' },
    })
  })

  it('should show recoverable error state without fictional devices', async () => {
    api.list.mockRejectedValueOnce(new Error('unavailable'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.loadError',
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByRole('link', { name: /Pump/ })).toBeInTheDocument()
  })

  it('should show an explicit empty state for an empty server page', async () => {
    api.list.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 } satisfies PageDevice)
    renderPage()
    expect(await screen.findByText('common.enterprise.devices.empty')).toBeInTheDocument()
  })

  it('should preserve the revision observed when editing despite a background refresh', async () => {
    const { client } = renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.edit' }))
    api.list.mockResolvedValue({
      items: [{ ...device(), revision: 4, name: 'External update' }],
      total: 1,
      offset: 0,
      limit: 20,
    })
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['enterprise-business'] })
    })
    await screen.findByText('External update')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await waitFor(() => expect(api.update).toHaveBeenCalled())
    expect(api.update.mock.calls[0]?.[0].headers['if-match']).toBe('3')
    expect(api.update.mock.calls[0]?.[0].body.name).toBe('Pump')
  })

  it('should submit newly entered leading-zero codes through the real form boundary', async () => {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.create' }))
    await userEvent.type(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.code' }),
      '0002',
    )
    await userEvent.type(screen.getByRole('textbox', { name: 'common.account.name' }), 'Valve')
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await waitFor(() => expect(api.create).toHaveBeenCalled())
    expect(api.create.mock.calls[0]?.[0].body).toEqual({
      device_code: '0002',
      name: 'Valve',
      department: '',
      description: '',
    })
  })

  it('should stop displaying the previous workspace list during an identity switch', async () => {
    const { store } = renderPage()
    await screen.findByRole('link', { name: /Pump/ })
    act(() => store.set(testWorkspaceAtom, { ...store.get(testWorkspaceAtom), id: 'workspace-2' }))
    expect(screen.queryByRole('link', { name: /Pump/ })).not.toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'common.enterprise.devices.accessDenied',
    )
    expect(api.list).toHaveBeenCalledTimes(1)
  })

  it('should close mounted write dialogs when server permissions are revoked', async () => {
    const { client } = renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'common.operation.edit' }))
    api.access.mockResolvedValue({
      actor_id: 'user-1',
      workspace_id: 'workspace-1',
      display_name: 'User',
      permissions: { read: true, manage: false, run: false, review: false },
    })
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['enterprise-business'] })
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.update).not.toHaveBeenCalled()
  })

  it.each(['edit', 'delete'] as const)(
    'should refresh and reopen %s with a new revision after conflict',
    async (operation) => {
      const mutation = operation === 'edit' ? api.update : api.remove
      mutation.mockRejectedValueOnce(new ORPCError('CONFLICT'))
      renderPage()
      await userEvent.click(
        await screen.findByRole('button', { name: `common.operation.${operation}` }),
      )
      await userEvent.click(
        screen.getByRole('button', {
          name: operation === 'edit' ? 'common.operation.save' : 'common.operation.confirm',
        }),
      )
      const error = await screen.findByRole('alert')
      expect(error).toHaveTextContent('common.enterprise.devices.reopenHint')
      api.list.mockResolvedValue({
        items: [{ ...device(), revision: 4 }],
        total: 1,
        offset: 0,
        limit: 20,
      })
      await userEvent.click(within(error).getByRole('button', { name: 'common.operation.close' }))
      await waitFor(() =>
        expect(
          screen.queryByRole(operation === 'edit' ? 'dialog' : 'alertdialog'),
        ).not.toBeInTheDocument(),
      )
      await userEvent.click(screen.getByRole('button', { name: `common.operation.${operation}` }))
      await userEvent.click(
        screen.getByRole('button', {
          name: operation === 'edit' ? 'common.operation.save' : 'common.operation.confirm',
        }),
      )
      await waitFor(() => expect(mutation).toHaveBeenCalledTimes(2))
      expect(mutation.mock.calls[1]?.[0].headers['if-match']).toBe('4')
    },
  )
})
