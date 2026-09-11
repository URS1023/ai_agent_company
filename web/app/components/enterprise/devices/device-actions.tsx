'use client'

import type { Device } from '@enterprise/business-contracts/types'
import {
  AlertDialog,
  AlertDialogActions,
  AlertDialogCancelButton,
  AlertDialogConfirmButton,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@langgenius/dify-ui/alert-dialog'
import { Button } from '@langgenius/dify-ui/button'
import {
  Dialog,
  DialogCloseButton,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from '@langgenius/dify-ui/dialog'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { consoleQuery } from '@/service/client'
import { BusinessFailure } from './access'
import { DeviceForm } from './device-form'

export function DeviceEditor({ device, scope }: { device?: Device; scope: readonly string[] }) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [snapshot, setSnapshot] = useState(device)
  const create = useMutation(consoleQuery.business.devices.post.mutationOptions())
  const update = useMutation(consoleQuery.business.devices.byDeviceId.put.mutationOptions())
  const pending = create.isPending || update.isPending
  const done = () => {
    void client.invalidateQueries({ queryKey: scope })
    setOpen(false)
  }
  const label = t(($) => $[device ? 'operation.edit' : 'operation.create'])
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return
        if (next) {
          setSnapshot(device)
          create.reset()
          update.reset()
        }
        setOpen(next)
      }}
    >
      <DialogTrigger render={<Button variant={device ? 'secondary' : 'primary'} />}>
        {label}
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="mb-6 text-xl font-semibold text-text-primary">{label}</DialogTitle>
        <DialogCloseButton aria-label={t(($) => $['operation.close'])} disabled={pending} />
        {(create.isError || update.isError) && (
          <div className="mb-4">
            <BusinessFailure
              mutation
              reopen
              retry={() => {
                void client.invalidateQueries({ queryKey: scope }).then(() => setOpen(false))
              }}
            />
          </div>
        )}
        <DeviceForm
          initial={snapshot}
          pending={pending}
          onCancel={() => setOpen(false)}
          onSave={(body) => {
            if (pending) return
            if (snapshot)
              update.mutate(
                {
                  body,
                  params: { device_id: snapshot.id },
                  headers: {
                    Origin: window.location.origin,
                    'if-match': String(snapshot.revision),
                  },
                },
                { onSuccess: done },
              )
            else
              create.mutate(
                { body, headers: { Origin: window.location.origin } },
                { onSuccess: done },
              )
          }}
        />
      </DialogContent>
    </Dialog>
  )
}

export function DeleteDevice({
  device,
  scope,
  onDeleted,
}: {
  device: Device
  scope: readonly string[]
  onDeleted?: () => void
}) {
  const { t } = useTranslation('common')
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [snapshot, setSnapshot] = useState(device)
  const deletion = useMutation(consoleQuery.business.devices.byDeviceId.delete.mutationOptions())
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (deletion.isPending) return
        if (next) {
          setSnapshot(device)
          deletion.reset()
        }
        setOpen(next)
      }}
    >
      <AlertDialogTrigger render={<Button variant="secondary" tone="destructive" />}>
        {t(($) => $['operation.delete'])}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <div className="space-y-3 p-6 pb-0">
          <AlertDialogTitle className="text-xl font-semibold text-text-primary">
            {t(($) => $['operation.deleteConfirmTitle'])}
          </AlertDialogTitle>
          <p className="font-mono text-text-primary">
            {snapshot.device_code} · {snapshot.name}
          </p>
          <AlertDialogDescription className="system-sm-regular text-text-secondary">
            {t(($) => $['enterprise.devices.deleteHint'])}
          </AlertDialogDescription>
          {deletion.isError && (
            <BusinessFailure
              mutation
              reopen
              retry={() => {
                void client.invalidateQueries({ queryKey: scope }).then(() => setOpen(false))
              }}
            />
          )}
        </div>
        <AlertDialogActions>
          <AlertDialogCancelButton disabled={deletion.isPending}>
            {t(($) => $['operation.cancel'])}
          </AlertDialogCancelButton>
          <AlertDialogConfirmButton
            loading={deletion.isPending}
            onClick={() => {
              if (deletion.isPending) return
              deletion.mutate(
                {
                  params: { device_id: snapshot.id },
                  headers: {
                    Origin: window.location.origin,
                    'if-match': String(snapshot.revision),
                  },
                },
                {
                  onSuccess: () => {
                    void client.invalidateQueries({ queryKey: scope })
                    setOpen(false)
                    onDeleted?.()
                  },
                },
              )
            }}
          >
            {t(($) => $['operation.confirm'])}
          </AlertDialogConfirmButton>
        </AlertDialogActions>
      </AlertDialogContent>
    </AlertDialog>
  )
}
