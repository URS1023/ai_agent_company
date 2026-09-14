'use client'

import { useAtomValue } from 'jotai'
import { useTranslation } from 'react-i18next'
import { userProfileIdAtom } from '@/context/account-state'
import {
  currentWorkspaceIdAtom,
  isCurrentWorkspaceDatasetOperatorAtom,
} from '@/context/workspace-state'
import Link from '@/next/link'
import { InstalledApplications } from '../application-lists'
import { OfficeDocuments } from './documents'

function WorkbenchResources() {
  const actor = useAtomValue(userProfileIdAtom)
  const workspace = useAtomValue(currentWorkspaceIdAtom)
  return (
    <>
      <InstalledApplications workbench />
      <OfficeDocuments actor={actor} workspace={workspace} />
    </>
  )
}

/** Native app entry remains available while enterprise parameter/branch setup is integrated. */
export function WorkbenchLanding() {
  const { t } = useTranslation('common')
  const datasetOperator = useAtomValue(isCurrentWorkspaceDatasetOperatorAtom)
  return (
    <main className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <div className="mx-auto max-w-5xl space-y-8 px-4 py-8 sm:px-8">
        <header className="space-y-4">
          <Link
            href="/enterprise"
            className="system-sm-medium text-text-tertiary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['enterprise.title'])}
          </Link>
          <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
            {t(($) => $['enterprise.ai'])}
          </h1>
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.notConnected'])}
          </p>
        </header>
        {datasetOperator ? (
          <p role="alert" className="text-text-secondary">
            {t(($) => $['enterprise.devices.accessDenied'])}
          </p>
        ) : (
          <WorkbenchResources />
        )}
      </div>
    </main>
  )
}
