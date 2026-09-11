'use client'

import { Button } from '@langgenius/dify-ui/button'
import { Checkbox } from '@langgenius/dify-ui/checkbox'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from '../devices/access'
import { TextField } from './controls'

export function DevicePicker({
  scope,
  initial,
  disabled,
}: {
  scope: readonly string[]
  initial: readonly string[]
  disabled: boolean
}) {
  const { t } = useTranslation('common')
  const [selected, setSelected] = useState(() => new Set(initial))
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const query = { offset: (page - 1) * 20, limit: 20, q }
  const devices = useQuery(
    consoleQuery.business.devices.get.queryOptions({
      input: { query },
      queryKey: [...scope, 'devices', query],
      retry: false,
    }),
  )
  return (
    <section
      aria-label={t(($) => $['enterprise.sources.selectDevices'])}
      className="space-y-3 rounded-xl border border-divider-subtle p-4"
    >
      {[...selected].map((id) => (
        <input key={id} type="hidden" name="device_ids" value={id} />
      ))}
      <TextField
        label={t(($) => $['operation.search'])}
        value={q}
        maxLength={200}
        disabled={disabled}
        onValueChange={(value) => {
          setQ(value)
          setPage(1)
        }}
      />
      <p className="system-xs-regular text-text-tertiary">
        {t(($) => $['enterprise.sources.selectedDevices'], { count: selected.size })}
      </p>
      {devices.isPending ? (
        <div
          aria-busy
          className="h-16 animate-pulse rounded-lg bg-background-section motion-reduce:animate-none"
        />
      ) : devices.isError ||
        devices.data.items.some((device) => device.workspace_id !== scope[1]) ? (
        <BusinessFailure
          retry={() => {
            void devices.refetch()
          }}
        />
      ) : devices.data.items.length === 0 ? (
        <p className="text-text-tertiary">{t(($) => $.noData)}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {devices.data.items.map((device) => (
            <label
              key={device.id}
              className="flex min-w-0 items-center gap-2 rounded-lg bg-background-section p-3 text-text-secondary"
            >
              <Checkbox
                checked={selected.has(device.id)}
                disabled={disabled}
                onCheckedChange={(checked) =>
                  setSelected((current) => {
                    const next = new Set(current)
                    if (checked) next.add(device.id)
                    else next.delete(device.id)
                    return next
                  })
                }
              />
              <span className="min-w-0 break-words">
                {device.device_code} · {device.name}
              </span>
            </label>
          ))}
        </div>
      )}
      <div className="flex justify-between gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={page === 1 || disabled || devices.isFetching}
          onClick={() => setPage(page - 1)}
        >
          {t(($) => $['pagination.previous'])}
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={
            disabled ||
            devices.isFetching ||
            devices.isError ||
            !devices.data ||
            query.offset + 20 >= devices.data.total
          }
          onClick={() => setPage(page + 1)}
        >
          {t(($) => $['pagination.next'])}
        </Button>
      </div>
    </section>
  )
}
