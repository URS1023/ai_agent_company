import EnterpriseRunPage from '@/app/(commonLayout)/enterprise/runs/[runId]/page'

const flag = vi.hoisted(() => ({ enabled: false }))
vi.mock('@/env', () => ({
  env: {
    get NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL() {
      return flag.enabled
    },
  },
}))
vi.mock('@/next/navigation', () => ({
  notFound: () => {
    throw new Error('NOT_FOUND')
  },
}))

it('keeps the direct run route closed by default', async () => {
  flag.enabled = false
  await expect(EnterpriseRunPage({ params: Promise.resolve({ runId: 'run-1' }) })).rejects.toThrow(
    'NOT_FOUND',
  )
})

it('passes the explicit run identity to the protected detail page', async () => {
  flag.enabled = true
  const page = await EnterpriseRunPage({ params: Promise.resolve({ runId: 'run-0001' }) })
  expect(page.props.runId).toBe('run-0001')
})
