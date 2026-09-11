import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { env } from '@/env'
import { proxyEnterprise } from '../_proxy'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params
  return proxyEnterprise(request, path, {
    enabled: isEnterprisePortalEnabled(),
    upstream: env.ENTERPRISE_API_URL,
  })
}

export { handle as DELETE, handle as GET, handle as POST, handle as PUT }
