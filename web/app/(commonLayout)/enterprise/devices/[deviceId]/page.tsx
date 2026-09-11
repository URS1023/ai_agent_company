import { DeviceDetail } from '@/app/components/enterprise/devices/detail'
import { isEnterprisePortalEnabled } from '@/app/components/enterprise/feature-flag'
import { notFound } from '@/next/navigation'

export default async function EnterpriseDevicePage({
  params,
}: {
  params: Promise<{ deviceId: string }>
}) {
  if (!isEnterprisePortalEnabled()) notFound()
  const { deviceId } = await params
  return <DeviceDetail deviceId={deviceId} />
}
