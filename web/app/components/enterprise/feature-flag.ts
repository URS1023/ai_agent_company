import { env } from '@/env'

export function isEnterprisePortalEnabled() {
  return env.NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL
}
