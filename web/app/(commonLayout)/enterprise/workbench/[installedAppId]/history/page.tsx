import { z } from 'zod'
import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { WorkbenchHistory } from '@/app/components/enterprise/workbench/history-page'
import { notFound } from '@/next/navigation'

export default async function WorkbenchHistoryPage({
  params,
}: {
  params: Promise<{ installedAppId: string }>
}) {
  if (!isEnterprisePortalEnabled()) notFound()
  const { installedAppId } = await params
  if (
    !z.uuid().safeParse(installedAppId).success ||
    installedAppId === '00000000-0000-0000-0000-000000000000'
  )
    notFound()
  return <WorkbenchHistory installedAppId={installedAppId} />
}
