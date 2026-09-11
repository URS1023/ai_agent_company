import EnterpriseSourcesPage from '@/app/(commonLayout)/enterprise/sources/page'

const feature = vi.hoisted(() => ({ enabled: false }))
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

describe('Sources route rollout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    feature.enabled = false
  })

  it('should block the direct source route when the rollout flag is disabled', () => {
    expect(() => EnterpriseSourcesPage()).toThrow('NOT_FOUND')
  })

  it('should render the source entry when explicitly enabled', () => {
    feature.enabled = true
    expect(EnterpriseSourcesPage()).toBeTruthy()
  })
})
