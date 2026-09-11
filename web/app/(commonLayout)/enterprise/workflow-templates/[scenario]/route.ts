import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { buildDefaultWorkflow } from '../../../../../../enterprise/workflows/default-workflows.mjs'

// These assets contain no workspace state or credentials and perform no mutations.
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ scenario: string }> },
) {
  const { scenario } = await params
  if (!isEnterprisePortalEnabled() || (scenario !== 'alert' && scenario !== 'quality'))
    return new Response(null, { status: 404 })
  return new Response(`${JSON.stringify(buildDefaultWorkflow(scenario), null, 2)}\n`, {
    headers: {
      'Content-Type': 'application/yaml; charset=utf-8',
      'Content-Disposition': `attachment; filename="default-${scenario}.yml"`,
      'Cache-Control': 'private, no-store',
      'X-Content-Type-Options': 'nosniff',
    },
  })
}
