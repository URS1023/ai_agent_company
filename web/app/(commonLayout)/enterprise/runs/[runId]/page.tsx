import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { RunDetail } from '@/app/components/enterprise/runs/detail'
import { notFound } from '@/next/navigation'

export default async function EnterpriseRunPage({
  params,
}: {
  params: Promise<{ runId: string }>
}) {
  if (!isEnterprisePortalEnabled()) notFound()
  const { runId } = await params
  return <RunDetail runId={runId} />
}
