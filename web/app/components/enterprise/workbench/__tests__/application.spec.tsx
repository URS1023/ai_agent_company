import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import WorkbenchApplicationPage from '@/app/(commonLayout)/enterprise/workbench/[installedAppId]/page'
import { WorkbenchApplication } from '../application'

vi.mock('@/env', () => ({ env: { NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL: true } }))
vi.mock('@/next/navigation', () => ({
  notFound: () => {
    throw new Error('NOT_FOUND')
  },
}))

const state = vi.hoisted(() => ({
  restricted: false,
  installed: vi.fn(),
  parameters: vi.fn(),
  upload: vi.fn(),
}))
vi.mock('@/context/account-state', async () => ({
  userProfileIdAtom: (await import('jotai')).atom('actor'),
}))
vi.mock('@/context/workspace-state', async () => {
  const { atom } = await import('jotai')
  return {
    currentWorkspaceIdAtom: atom('workspace'),
    isCurrentWorkspaceDatasetOperatorAtom: atom(() => state.restricted),
  }
})
vi.mock('@/service/client', () => ({
  consoleQuery: {
    installedApps: {
      get: { queryOptions: (options: object) => ({ ...options, queryFn: state.installed }) },
      byInstalledAppId: {
        parameters: {
          get: { queryOptions: (options: object) => ({ ...options, queryFn: state.parameters }) },
        },
      },
    },
    files: {
      upload: {
        get: { queryOptions: (options: object) => ({ ...options, queryFn: state.upload }) },
      },
    },
  },
}))
vi.mock('../launcher', () => ({
  WorkbenchLauncher: ({ title }: { title: string }) => <h2>{title}</h2>,
}))
const appId = '00000000-0000-4000-8000-000000000001'
function mount() {
  return render(
    <Provider store={createStore()}>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <WorkbenchApplication installedAppId={appId} />
      </QueryClientProvider>
    </Provider>,
  )
}
beforeEach(() => {
  vi.clearAllMocks()
  state.restricted = false
  state.installed.mockResolvedValue({
    installed_apps: [{ id: appId, app: { mode: 'chat', name: 'Device assistant' } }],
  })
  state.parameters.mockResolvedValue({ user_input_form: [] })
  state.upload.mockResolvedValue({})
})
describe('workbench application configuration', () => {
  it('should withhold launch when a file default lacks native metadata', async () => {
    state.parameters.mockResolvedValue({
      user_input_form: [
        { file: { variable: 'report', label: 'Report', default: { upload_file_id: appId } } },
      ],
    })
    mount()
    await screen.findByRole('alert')
    expect(screen.queryByRole('heading', { name: 'Device assistant' })).not.toBeInTheDocument()
  })
  it('should withhold launch when a required upload configuration request fails', async () => {
    state.parameters.mockResolvedValue({
      user_input_form: [{ file: { variable: 'report', label: 'Report' } }],
    })
    state.upload.mockRejectedValue(new Error('private upload configuration failure'))
    mount()
    await screen.findByRole('alert')
    expect(state.upload).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('heading', { name: 'Device assistant' })).not.toBeInTheDocument()
  })
  it('should preserve native access without treating a workflow app as a chat app', async () => {
    state.installed.mockResolvedValue({
      installed_apps: [{ id: appId, app: { mode: 'workflow', name: 'Workflow' } }],
    })
    mount()
    await screen.findByRole('alert')
    expect(state.parameters).not.toHaveBeenCalled()
    expect(
      screen.getByRole('link', { name: 'common.enterprise.workbench.openNative' }),
    ).toHaveAttribute('href', `/installed/${appId}`)
  })
  it('should reject malformed app identifiers at the direct route', async () => {
    await expect(
      WorkbenchApplicationPage({ params: Promise.resolve({ installedAppId: '../invalid' }) }),
    ).rejects.toThrow('NOT_FOUND')
  })
  it('should load real installed app metadata and parameters before mounting its launcher', async () => {
    mount()
    await screen.findByRole('heading', { name: 'Device assistant' })
    expect(state.installed).toHaveBeenCalledTimes(1)
    expect(state.parameters).toHaveBeenCalledTimes(1)
    expect(state.upload).not.toHaveBeenCalled()
    expect(
      screen.getByRole('link', { name: 'common.promptEditor.history.item.title' }),
    ).toHaveAttribute('href', `/enterprise/workbench/${appId}/history`)
    expect(
      screen.getByRole('link', { name: 'common.enterprise.workbench.openNative' }),
    ).toHaveAttribute('href', `/installed/${appId}`)
  })
  it('should withhold parameter requests and launcher for an unlisted installation', async () => {
    state.installed.mockResolvedValue({ installed_apps: [] })
    mount()
    await screen.findByRole('alert')
    expect(state.parameters).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Device assistant' })).not.toBeInTheDocument()
  })
  it('should deny dataset operators without requesting application data', () => {
    state.restricted = true
    mount()
    expect(screen.getByRole('alert')).toHaveTextContent('common.enterprise.devices.accessDenied')
    expect(state.installed).not.toHaveBeenCalled()
  })
  it('should fail visibly when parameters are malformed instead of mounting an empty form', async () => {
    state.parameters.mockResolvedValue({ user_input_form: [{ unknown: {} }] })
    mount()
    await screen.findByRole('alert')
    expect(screen.queryByRole('heading', { name: 'Device assistant' })).not.toBeInTheDocument()
  })
})
