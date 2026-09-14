import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkbenchPage from '@/app/(commonLayout)/enterprise/workbench/page'
import { WorkbenchLanding } from '../landing'
const feature = vi.hoisted(() => ({ enabled: false, datasetOperator: false }))
const directory = vi.hoisted(() => vi.fn())
vi.mock('@/context/account-state', async () => {
  const { atom } = await import('jotai')
  return { userProfileIdAtom: atom('actor') }
})
vi.mock('@/context/workspace-state', async () => {
  const { atom } = await import('jotai')
  return {
    isCurrentWorkspaceDatasetOperatorAtom: atom(() => feature.datasetOperator),
    currentWorkspaceIdAtom: atom('workspace'),
  }
})
vi.mock('@/service/client', () => ({
  consoleQuery: {
    business: {
      office: {
        files: {
          get: {
            queryOptions: (options: { input: unknown }) => ({
              ...options,
              queryFn: () => directory(options.input),
            }),
          },
        },
      },
    },
  },
}))
vi.mock('@/service/enterprise-business/office-download', () => ({ fetchOfficeDocument: vi.fn() }))
vi.mock('@/env', () => ({
  env: {
    get NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL() {
      return feature.enabled
    },
  },
}))
vi.mock('@/next/navigation', () => ({
  notFound: () => {
    throw new Error('NOT_FOUND')
  },
}))
vi.mock('../../application-lists', () => ({
  InstalledApplications: () => <div>installed-applications</div>,
}))
describe('Workbench entry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    feature.enabled = false
    feature.datasetOperator = false
    directory.mockResolvedValue({ items: [], next_offset: null })
  })
  it('should protect direct navigation with the enterprise rollout flag', () => {
    expect(() => WorkbenchPage()).toThrow('NOT_FOUND')
  })
  it('should render the native application chooser without inventing a conversation', () => {
    feature.enabled = true
    renderEntry(<WorkbenchPage />)
    expect(screen.getByRole('heading', { name: 'common.enterprise.ai' })).toBeInTheDocument()
    expect(screen.getByText('installed-applications')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })
  it('should retain a navigation path to the enterprise portal', () => {
    renderEntry(<WorkbenchLanding />)
    expect(screen.getByRole('link', { name: 'common.enterprise.title' })).toHaveAttribute(
      'href',
      '/enterprise',
    )
  })
  it('should show authorized Office files alongside the native application chooser', async () => {
    directory.mockResolvedValue({
      items: [
        {
          file_id: '00000000-0000-4000-8000-000000000001',
          revision: '1',
          kind: 'document',
          template_id: 'document-default',
          template_revision: '1',
        },
      ],
      next_offset: null,
    })
    renderEntry(<WorkbenchLanding />)
    expect(
      await screen.findByRole('button', { name: 'common.operation.download' }),
    ).toBeInTheDocument()
    expect(screen.getByText('installed-applications')).toBeInTheDocument()
  })
})

it('should preserve the portal dataset-operator restriction on direct entry', () => {
  directory.mockClear()
  feature.datasetOperator = true
  renderEntry(<WorkbenchLanding />)
  expect(screen.queryByText('installed-applications')).not.toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('common.enterprise.devices.accessDenied')
  expect(directory).not.toHaveBeenCalled()
})

function renderEntry(node: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <Provider store={createStore()}>
      <QueryClientProvider client={client}>{node}</QueryClientProvider>
    </Provider>,
  )
}
