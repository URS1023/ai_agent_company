import WorkbenchHistoryPage from '@/app/(commonLayout)/enterprise/workbench/[installedAppId]/history/page'
const flags = vi.hoisted(() => ({ enabled: true }))
vi.mock('@/env', () => ({
  env: {
    get NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL() {
      return flags.enabled
    },
  },
}))
vi.mock('@/next/navigation', () => ({
  notFound: () => {
    throw new Error('NOT_FOUND')
  },
}))
beforeEach(() => {
  vi.clearAllMocks()
  flags.enabled = true
})
describe('workbench history route', () => {
  it.each(['bad/id', '00000000-0000-0000-0000-000000000000'])(
    'should reject an invalid installation %s',
    async (installedAppId) => {
      await expect(
        WorkbenchHistoryPage({ params: Promise.resolve({ installedAppId }) }),
      ).rejects.toThrow('NOT_FOUND')
    },
  )
  it('should withhold the route when the enterprise portal is disabled', async () => {
    flags.enabled = false
    await expect(
      WorkbenchHistoryPage({
        params: Promise.resolve({ installedAppId: '00000000-0000-4000-8000-000000000001' }),
      }),
    ).rejects.toThrow('NOT_FOUND')
  })
  it('should pass the validated installation to the history surface', async () => {
    const installedAppId = '00000000-0000-4000-8000-000000000001'
    const element = await WorkbenchHistoryPage({ params: Promise.resolve({ installedAppId }) })
    expect(element.props.installedAppId).toBe(installedAppId)
  })
})
