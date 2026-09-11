'use client'

import type { BusinessAccess } from '@enterprise/business-contracts/types'
import { Button } from '@langgenius/dify-ui/button'
import { Field, FieldControl, FieldLabel } from '@langgenius/dify-ui/field'
import { Form } from '@langgenius/dify-ui/form'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import Link from '@/next/link'
import { consoleQuery } from '@/service/client'
import { BusinessFailure, BusinessGate } from './access'
import { DeleteDevice, DeviceEditor } from './device-actions'
import { useDeviceFilters } from './use-device-filters'

export function DevicesPage() {
  return (
    <div className="h-full min-w-0 flex-1 overflow-y-auto bg-background-body">
      <BusinessGate>{(access, scope) => <DeviceList access={access} scope={scope} />}</BusinessGate>
    </div>
  )
}

function DeviceList({ access, scope }: { access: BusinessAccess; scope: readonly string[] }) {
  const { t } = useTranslation('common')
  const { filters, setPage, search } = useDeviceFilters()
  const query = {
    offset: (filters.page - 1) * 20,
    limit: 20,
    q: filters.q,
    department: filters.department,
  }
  const devices = useQuery(
    consoleQuery.business.devices.get.queryOptions({
      input: { query },
      queryKey: [...scope, 'devices', query],
      retry: false,
    }),
  )
  return (
    <main className="mx-auto max-w-7xl space-y-8 px-4 py-8 sm:px-8 lg:px-12">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-3">
          <Link
            href="/enterprise"
            className="system-sm-medium text-text-tertiary focus-visible:ring-2 focus-visible:ring-state-accent-solid"
          >
            {t(($) => $['enterprise.title'])}
          </Link>
          <h1 className="text-3xl font-semibold tracking-tight text-text-primary">
            {t(($) => $['enterprise.devices.title'])}
          </h1>
          <p className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.devices.subtitle'])}
          </p>
        </div>
        {access.permissions.manage && <DeviceEditor scope={scope} />}
      </header>
      <Form
        key={JSON.stringify([filters.q, filters.department])}
        className="flex flex-wrap items-end gap-3 rounded-xl border border-divider-subtle bg-background-default p-4"
        onSubmit={(event) => {
          event.preventDefault()
          const values = new FormData(event.currentTarget)
          void search(String(values.get('q') ?? ''), String(values.get('department') ?? ''))
        }}
      >
        <Field name="q" className="min-w-48 flex-1">
          <FieldLabel>{t(($) => $['operation.search'])}</FieldLabel>
          <FieldControl defaultValue={filters.q} maxLength={200} />
        </Field>
        <Field name="department" className="min-w-48">
          <FieldLabel>{t(($) => $['enterprise.devices.department'])}</FieldLabel>
          <FieldControl defaultValue={filters.department} maxLength={200} />
        </Field>
        <Button type="submit" variant="secondary">
          {t(($) => $['operation.search'])}
        </Button>
      </Form>
      <section aria-busy={devices.isPending} aria-label={t(($) => $['enterprise.devices.title'])}>
        {devices.isPending ? (
          <div aria-hidden className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-20 animate-pulse rounded-xl bg-background-section motion-reduce:animate-none"
              />
            ))}
          </div>
        ) : devices.isError ? (
          <BusinessFailure
            retry={() => {
              void devices.refetch()
            }}
          />
        ) : devices.data.items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-divider-regular p-12 text-center text-text-tertiary">
            {t(($) => $['enterprise.devices.empty'])}
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-divider-subtle bg-background-default">
            {devices.data.items.map((device) => (
              <article
                key={device.id}
                className="flex flex-wrap items-center justify-between gap-4 border-b border-divider-subtle p-5 last:border-b-0"
              >
                <Link
                  href={`/enterprise/devices/${encodeURIComponent(device.id)}`}
                  className="min-w-0 flex-1 rounded-lg focus-visible:ring-2 focus-visible:ring-state-accent-solid"
                >
                  <span className="mb-1 block font-mono text-xs break-words text-text-accent">
                    {device.device_code}
                  </span>
                  <h2 className="truncate system-md-semibold text-text-primary">{device.name}</h2>
                  {device.department && (
                    <p className="mt-1 system-xs-regular break-words text-text-tertiary">
                      {device.department}
                    </p>
                  )}
                </Link>
                {access.permissions.manage && (
                  <div className="flex gap-2">
                    <DeviceEditor device={device} scope={scope} />
                    <DeleteDevice device={device} scope={scope} />
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
      <nav
        aria-label={t(($) => $['pagination.pageNumber'])}
        className="flex items-center justify-between gap-3"
      >
        <Button
          variant="secondary"
          disabled={filters.page <= 1 || devices.isPending}
          onClick={() => {
            void setPage(filters.page - 1)
          }}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <span className="font-mono system-sm-regular text-text-secondary">{filters.page}</span>
        <Button
          variant="secondary"
          disabled={
            !devices.data ||
            devices.isError ||
            query.offset + query.limit >= devices.data.total ||
            filters.page >= 99999
          }
          onClick={() => {
            void setPage(filters.page + 1)
          }}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </nav>
    </main>
  )
}
