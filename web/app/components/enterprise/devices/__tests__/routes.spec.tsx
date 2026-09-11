import EnterpriseDevicePage from '@/app/(commonLayout)/enterprise/devices/[deviceId]/page'
import EnterpriseDevicesPage from '@/app/(commonLayout)/enterprise/devices/page'

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

describe('Device route rollout gates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    feature.enabled = false
  })

  it('should block both direct device routes while the feature is disabled', async () => {
    expect(() => EnterpriseDevicesPage()).toThrow('NOT_FOUND')
    await expect(
      EnterpriseDevicePage({ params: Promise.resolve({ deviceId: '0001' }) }),
    ).rejects.toThrow('NOT_FOUND')
  })

  it('should preserve the explicit device identity when the feature is enabled', async () => {
    feature.enabled = true
    expect(EnterpriseDevicesPage()).toBeTruthy()
    const page = await EnterpriseDevicePage({ params: Promise.resolve({ deviceId: '0001' }) })
    expect(page.props.deviceId).toBe('0001')
  })
})
