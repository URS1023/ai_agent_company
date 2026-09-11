import { z } from 'zod'
import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { WorkbenchApplication } from '@/app/components/enterprise/workbench/application'
import { notFound } from '@/next/navigation'

export default async function WorkbenchApplicationPage({
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
  return <WorkbenchApplication installedAppId={installedAppId} />
}
