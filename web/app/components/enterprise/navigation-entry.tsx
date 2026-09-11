'use client'

import { useTranslation } from 'react-i18next'
import MainNavLink from '@/app/components/main-nav/components/nav-link'
import { isEnterprisePortalEnabled } from './feature-flag'

export function EnterpriseNavigationEntry({ pathname }: { pathname: string }) {
  const { t } = useTranslation('common')
  if (!isEnterprisePortalEnabled()) return null

  return (
    <MainNavLink
      pathname={pathname}
      item={{
        href: '/enterprise',
        label: t(($) => $['enterprise.title']),
        active: (path) => path === '/enterprise' || path.startsWith('/enterprise/'),
        icon: 'i-ri-building-line',
        activeIcon: 'i-ri-building-fill',
      }}
    />
  )
}
