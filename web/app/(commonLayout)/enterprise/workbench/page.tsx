import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { WorkbenchLanding } from '@/app/components/enterprise/workbench/landing'
import { notFound } from '@/next/navigation'

export default function WorkbenchPage() {
  if (!isEnterprisePortalEnabled()) notFound()
  return <WorkbenchLanding />
}
