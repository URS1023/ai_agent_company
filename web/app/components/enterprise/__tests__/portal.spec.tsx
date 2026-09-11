import type { AppPagination, GetAppsData } from '@dify/contracts/api/console/apps/types.gen'
import type { InstalledAppListResponse } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { UrlUpdateEvent } from 'nuqs/adapters/testing'
import type { AppListResponse } from '@/models/app'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createStore, Provider } from 'jotai'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { InstalledApplications } from '../application-lists'
import { EnterprisePortal } from '../portal'

type AppsInput = { query: NonNullable<GetAppsData['query']> }
const { loadApps, loadInstalled } = vi.hoisted(() => ({
  loadApps: vi.fn<(input: AppsInput) => Promise<AppPagination>>(),
  loadInstalled: vi.fn<() => Promise<InstalledAppListResponse>>(),
}))
const membership = vi.hoisted(() => ({ datasetOperator: false, permissions: new Array<string>() }))

vi.mock('@/context/workspace-state', async () => {
  const { atom } = await import('jotai')
  return {
    currentWorkspaceAtom: atom({ id: 'workspace-1', name: 'Production workspace' }),
    isCurrentWorkspaceDatasetOperatorAtom: atom(() => membership.datasetOperator),
  }
})
vi.mock('@/context/account-state', async () => {
  const { atom } = await import('jotai')
  return { userProfileIdAtom: atom('user-1') }
})
vi.mock('@/context/permission-state', async () => {
  const { atom } = await import('jotai')
  return { workspacePermissionKeysAtom: atom(() => membership.permissions) }
})
vi.mock('@/features/system-features/client', () => ({
  systemFeaturesQueryOptions: () => ({
    queryKey: ['system-features'],
    queryFn: async () => ({ rbac_enabled: true }),
  }),
}))
vi.mock('@/service/client', () => ({
  consoleQuery: {
    apps: {
      get: {
        queryOptions: (options: {
          input: AppsInput
          select?: (response: AppPagination) => AppListResponse
        }) => ({
          queryKey: ['console', 'apps', options.input],
          queryFn: () => loadApps(options.input),
          select: options.select,
        }),
      },
    },
    installedApps: {
      get: {
        queryOptions: () => ({
          queryKey: ['console', 'installed-apps'],
          queryFn: () => loadInstalled(),
        }),
      },
    },
  },
}))

function applications(page = 1): AppPagination {
  return {
    data: [
      {
        id: `workflow-${page}`,
        name: `Inspection ${page}`,
        mode: 'workflow',
        icon_url: null,
        permission_keys: ['app.acl.view_layout'],
      },
    ],
    page,
    limit: 12,
    total: 13,
    has_more: page === 1,
  }
}

function renderPortal(searchParams = '') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onUrlUpdate = vi.fn<(event: UrlUpdateEvent) => void>()
  const rendered = render(
    <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate} hasMemory>
      <QueryClientProvider client={client}>
        <Provider store={createStore()}>
          <EnterprisePortal />
        </Provider>
      </QueryClientProvider>
    </NuqsTestingAdapter>,
  )
  return { ...rendered, onUrlUpdate }
}

describe('EnterprisePortal', () => {
  it('should offer workbench launch alongside the unchanged native installed-app link', async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <InstalledApplications workbench />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('link', { name: 'common.enterprise.ai' })).toHaveAttribute(
      'href',
      '/enterprise/workbench/installed-42',
    )
    expect(screen.getByRole('link', { name: /Operations chat/ })).toHaveAttribute(
      'href',
      '/installed/installed-42',
    )
  })
  beforeEach(() => {
    vi.clearAllMocks()
    membership.datasetOperator = false
    membership.permissions = []
    loadApps.mockImplementation(async ({ query }) => applications(query.page))
    loadInstalled.mockResolvedValue({
      installed_apps: [
        {
          id: 'installed-42',
          app_owner_tenant_id: 'workspace-1',
          editable: true,
          is_pinned: false,
          uninstallable: false,
          app: { id: 'app-99', name: 'Operations chat', mode: 'chat', icon_url: null },
        },
      ],
    })
  })

  // Existing resources remain in their native Dify surfaces.
  it('should show real workspace resources and use the installed ID when opening an app', async () => {
    renderPortal()
    expect(screen.getByRole('heading', { name: 'common.enterprise.title' })).toBeInTheDocument()
    expect(screen.getByText('Production workspace')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /common.enterprise.ai/ })).toHaveAttribute(
      'href',
      '/enterprise/workbench',
    )
    expect(await screen.findByRole('link', { name: /Inspection 1/ })).toHaveAttribute(
      'href',
      '/app/workflow-1/workflow',
    )
    expect(await screen.findByRole('link', { name: /Operations chat/ })).toHaveAttribute(
      'href',
      '/installed/installed-42',
    )
    expect(screen.getByRole('link', { name: 'common.menus.apps' })).toHaveAttribute('href', '/apps')
    expect(screen.getByRole('link', { name: /common.enterprise.dashboards/ })).toHaveAttribute(
      'href',
      '/enterprise/dashboards',
    )
    expect(screen.getByRole('link', { name: 'common.menus.datasets' })).toHaveAttribute(
      'href',
      '/datasets',
    )
    expect(screen.getAllByText('common.enterprise.notConnected')).toHaveLength(1)
    expect(screen.getByRole('link', { name: /common.enterprise.alerts/ })).toHaveAttribute(
      'href',
      '/enterprise/devices',
    )
    expect(screen.getByRole('link', { name: /common.enterprise.quality/ })).toHaveAttribute(
      'href',
      '/enterprise/devices',
    )
    expect(screen.getByRole('link', { name: 'common.enterprise.sources.title' })).toHaveAttribute(
      'href',
      '/enterprise/sources',
    )
  })

  it('should request the next page and preserve previous navigation when more apps exist', async () => {
    const user = userEvent.setup()
    renderPortal()
    await screen.findByRole('link', { name: /Inspection 1/ })
    expect(screen.getByRole('button', { name: 'common.pagination.previous' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    expect(await screen.findByRole('link', { name: /Inspection 2/ })).toHaveAttribute(
      'href',
      '/app/workflow-2/workflow',
    )
    expect(loadApps).toHaveBeenLastCalledWith({
      query: { page: 2, limit: 12, sort_by: 'last_modified' },
    })
    expect(screen.getByRole('button', { name: 'common.pagination.next' })).toBeDisabled()
  })

  it('should start at page one when the enterprise page parameter is absent', async () => {
    renderPortal('?page=7')
    await screen.findByRole('link', { name: /Inspection 1/ })
    expect(loadApps).toHaveBeenLastCalledWith({
      query: { page: 1, limit: 12, sort_by: 'last_modified' },
    })
  })

  it.each(['', '0', '-1', '1.5', '2tail', '1e2', '+2', '0002', '100000', '999999999999999999999'])(
    'should use page one for invalid appsPage=%s',
    async (value) => {
      renderPortal(`?appsPage=${encodeURIComponent(value)}`)
      await screen.findByRole('link', { name: /Inspection 1/ })
      expect(loadApps).toHaveBeenLastCalledWith({
        query: { page: 1, limit: 12, sort_by: 'last_modified' },
      })
    },
  )

  it.each([2, 99999])('should restore the shared appsPage=%s from the URL', async (page) => {
    renderPortal(`?appsPage=${page}`)
    await screen.findByRole('link', { name: new RegExp(`Inspection ${page}`) })
    expect(loadApps).toHaveBeenLastCalledWith({
      query: { page, limit: 12, sort_by: 'last_modified' },
    })
  })

  it('should push page changes into the URL while keeping unrelated parameters', async () => {
    const user = userEvent.setup()
    const { onUrlUpdate } = renderPortal('?tab=retained')
    await screen.findByRole('link', { name: /Inspection 1/ })
    await user.click(screen.getByRole('button', { name: 'common.pagination.next' }))
    await screen.findByRole('link', { name: /Inspection 2/ })
    await waitFor(() =>
      expect(onUrlUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          searchParams: new URLSearchParams('tab=retained&appsPage=2'),
          options: expect.objectContaining({ history: 'push', shallow: true }),
        }),
      ),
    )
  })

  it('should clear only appsPage when returning to the default page', async () => {
    const user = userEvent.setup()
    const { onUrlUpdate } = renderPortal('?appsPage=2&tab=retained')
    await screen.findByRole('link', { name: /Inspection 2/ })
    await user.click(screen.getByRole('button', { name: 'common.pagination.previous' }))
    await screen.findByRole('link', { name: /Inspection 1/ })
    await waitFor(() =>
      expect(onUrlUpdate).toHaveBeenCalledWith(
        expect.objectContaining({
          searchParams: new URLSearchParams('tab=retained'),
        }),
      ),
    )
  })

  it('should stop at the maximum supported page even when the response has more data', async () => {
    loadApps.mockResolvedValue({ ...applications(99999), has_more: true })
    renderPortal('?appsPage=99999')
    await screen.findByRole('link', { name: /Inspection 99999/ })
    expect(screen.getByRole('button', { name: 'common.pagination.next' })).toBeDisabled()
  })

  it('should keep preview-only apps out of the editor and honor monitor permissions', async () => {
    loadApps.mockResolvedValue({
      ...applications(),
      has_more: false,
      data: [
        {
          id: 'preview',
          name: 'Preview only app',
          mode: 'workflow',
          icon_url: null,
          permission_keys: ['app.acl.preview'],
        },
        {
          id: 'monitor',
          name: 'Monitor app',
          mode: 'workflow',
          icon_url: null,
          permission_keys: ['app.acl.monitor'],
        },
      ],
    })
    renderPortal()
    await screen.findByText('Preview only app')
    expect(screen.queryByRole('link', { name: /Preview only app/ })).not.toBeInTheDocument()
    expect(screen.getByText('common.enterprise.previewOnly')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Monitor app/ })).toHaveAttribute(
      'href',
      '/app/monitor/overview',
    )
  })

  // Failed and empty responses never turn into demonstration app state.
  it('should expose a retryable error instead of an empty successful result', async () => {
    loadApps.mockRejectedValueOnce(new Error('offline'))
    const user = userEvent.setup()
    renderPortal()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('common.enterprise.loadError')
    await user.click(within(alert).getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByRole('link', { name: /Inspection 1/ })).toBeInTheDocument()
  })

  it('should render honest empty states when both lists have no accessible apps', async () => {
    loadApps.mockResolvedValue({ data: [], page: 1, limit: 12, total: 0, has_more: false })
    loadInstalled.mockResolvedValue({ installed_apps: [] })
    renderPortal()
    await waitFor(() => expect(screen.getAllByText('common.noData')).toHaveLength(2))
    expect(screen.queryByRole('link', { name: /Inspection/ })).not.toBeInTheDocument()
  })

  it('should keep section loading states visible while requests are pending', () => {
    loadApps.mockReturnValue(new Promise(() => {}))
    loadInstalled.mockReturnValue(new Promise(() => {}))
    renderPortal()
    expect(screen.getByRole('region', { name: 'common.enterprise.apps' })).toHaveAttribute(
      'aria-busy',
      'true',
    )
    expect(screen.getByRole('region', { name: 'common.enterprise.installed' })).toHaveAttribute(
      'aria-busy',
      'true',
    )
  })

  it('should retain dataset-operator restrictions without requesting application lists', () => {
    membership.datasetOperator = true
    renderPortal()
    expect(screen.queryByRole('link', { name: 'common.menus.apps' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'common.menus.datasets' })).toHaveAttribute(
      'href',
      '/datasets',
    )
    expect(loadApps).not.toHaveBeenCalled()
    expect(loadInstalled).not.toHaveBeenCalled()
  })

  it('should reuse native maintainer, logs, and RBAC access-config routes without linking ungranted apps', async () => {
    membership.permissions = ['app.create_and_management']
    loadApps.mockResolvedValue({
      ...applications(),
      has_more: false,
      data: [
        {
          id: 'maintained',
          name: 'Maintained app',
          mode: 'chat',
          icon_url: null,
          maintainer: 'user-1',
        },
        {
          id: 'logs',
          name: 'Logs app',
          mode: 'workflow',
          icon_url: null,
          permission_keys: ['app.acl.log_and_annotation'],
        },
        {
          id: 'acl',
          name: 'Access app',
          mode: 'workflow',
          icon_url: null,
          permission_keys: ['app.acl.access_config'],
        },
        {
          id: 'ungranted',
          name: 'Ungranted app',
          mode: 'workflow',
          icon_url: null,
          maintainer: 'another-user',
        },
      ],
    })
    renderPortal()
    expect(await screen.findByRole('link', { name: /Maintained app/ })).toHaveAttribute(
      'href',
      '/app/maintained/configuration',
    )
    expect(screen.getByRole('link', { name: /Logs app/ })).toHaveAttribute('href', '/app/logs/logs')
    expect(await screen.findByRole('link', { name: /Access app/ })).toHaveAttribute(
      'href',
      '/app/acl/access-config',
    )
    expect(screen.queryByRole('link', { name: /Ungranted app/ })).not.toBeInTheDocument()
  })

  it('should retry installed-app failures without hiding the successful workspace list', async () => {
    loadInstalled.mockRejectedValueOnce(new Error('offline'))
    const user = userEvent.setup()
    renderPortal()
    expect(await screen.findByRole('link', { name: /Inspection 1/ })).toBeInTheDocument()
    const installed = screen.getByRole('region', { name: 'common.enterprise.installed' })
    const alert = await within(installed).findByRole('alert')
    await user.click(within(alert).getByRole('button', { name: 'common.operation.retry' }))
    expect(await screen.findByRole('link', { name: /Operations chat/ })).toHaveAttribute(
      'href',
      '/installed/installed-42',
    )
  })
})
