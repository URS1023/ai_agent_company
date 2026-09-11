import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { EnterprisePortal } from '@/app/components/enterprise/portal'
import { notFound } from '@/next/navigation'

export default function EnterprisePage() {
  if (!isEnterprisePortalEnabled()) notFound()

  return <EnterprisePortal />
}
