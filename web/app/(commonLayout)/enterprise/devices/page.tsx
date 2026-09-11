import { DevicesPage } from '@/app/components/enterprise/devices'
import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { notFound } from '@/next/navigation'

export default function EnterpriseDevicesPage() {
  if (!isEnterprisePortalEnabled()) notFound()
  return <DevicesPage />
}
