import EnterprisePage from '@/app/(commonLayout)/enterprise/page'

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
vi.mock('../portal', () => ({ EnterprisePortal: () => null }))

describe('EnterprisePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    feature.enabled = false
  })

  // The rollout switch also protects direct navigation, not just the sidebar.
  it('should return not found when directly visited while disabled', () => {
    expect(() => EnterprisePage()).toThrow('NOT_FOUND')
  })

  it('should preserve the enterprise route when explicitly enabled', () => {
    feature.enabled = true
    expect(EnterprisePage()).toBeTruthy()
  })
})
