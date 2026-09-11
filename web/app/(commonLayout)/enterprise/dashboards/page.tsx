import { DashboardsPage } from '@/app/components/enterprise/dashboards'
import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { notFound } from '@/next/navigation'

export default function EnterpriseDashboardsPage() {
  if (!isEnterprisePortalEnabled()) notFound()
  return <DashboardsPage />
}
