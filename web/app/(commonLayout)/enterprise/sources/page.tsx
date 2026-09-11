import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { SourcesPage } from '@/app/components/enterprise/sources'
import { notFound } from '@/next/navigation'

export default function EnterpriseSourcesPage() {
  if (!isEnterprisePortalEnabled()) notFound()
  return <SourcesPage />
}
