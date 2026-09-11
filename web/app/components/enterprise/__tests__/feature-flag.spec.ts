describe('Enterprise portal runtime transport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.resetModules()
    vi.doUnmock('@/utils/client')
    vi.stubEnv('NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL', undefined)
    document.body.removeAttribute('data-enable-enterprise-portal')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.doUnmock('@/utils/client')
    document.body.removeAttribute('data-enable-enterprise-portal')
  })

  it('should default to disabled in the browser', async () => {
    const { isEnterprisePortalEnabled } = await import('../feature-flag')
    expect(isEnterprisePortalEnabled()).toBe(false)
  })

  it('should read the explicitly enabled browser runtime dataset', async () => {
    document.body.setAttribute('data-enable-enterprise-portal', 'true')
    const { isEnterprisePortalEnabled } = await import('../feature-flag')
    expect(isEnterprisePortalEnabled()).toBe(true)
  })

  it('should emit the server flag through the existing runtime dataset map', async () => {
    vi.stubEnv('NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL', 'true')
    vi.doMock('@/utils/client', () => ({ isClient: false, isServer: true }))
    const { env, getDatasetMap } = await import('@/env')
    expect(env.NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL).toBe(true)
    expect(getDatasetMap()['data-enable-enterprise-portal']).toBe(true)
  })
})
