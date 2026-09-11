import type { ReactElement } from 'react'
import { render, screen } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkbenchPage from '@/app/(commonLayout)/enterprise/workbench/page'
import { WorkbenchLanding } from '../landing'
const feature = vi.hoisted(() => ({ enabled: false, datasetOperator: false }))
vi.mock('@/context/workspace-state', async () => {
  const { atom } = await import('jotai')
  return { isCurrentWorkspaceDatasetOperatorAtom: atom(() => feature.datasetOperator) }
})
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
})

it('should preserve the portal dataset-operator restriction on direct entry', () => {
  feature.datasetOperator = true
  renderEntry(<WorkbenchLanding />)
  expect(screen.queryByText('installed-applications')).not.toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('common.enterprise.devices.accessDenied')
})

function renderEntry(node: ReactElement) {
  return render(<Provider store={createStore()}>{node}</Provider>)
}
