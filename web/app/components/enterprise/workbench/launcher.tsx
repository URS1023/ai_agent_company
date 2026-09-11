'use client'

import type { BranchContext, JsonObject, MessageScope } from '@enterprise/business-contracts/types'
import type { ComponentProps } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { v4 as uuidv4 } from 'uuid'
import { consoleClient } from '@/service/client'
import { WorkbenchInputForm } from './input-form'
import { confirmWorkbenchRoot } from './launch'
import { WorkbenchSession } from './session'

type FormProps = ComponentProps<typeof WorkbenchInputForm>
type Props = Pick<FormProps, 'fields' | 'uploadConfig' | 'initialFiles'> & {
  scope: Omit<MessageScope, 'branch_id'>
  configurationKey: string
  title: string
  labels: FormProps['labels'] & { error: string }
}

export function WorkbenchLauncher(props: Props) {
  const key = JSON.stringify([props.scope, props.configurationKey])
  return <Launcher key={key} {...props} />
}

function Launcher({ scope, configurationKey, title, labels, ...form }: Props) {
  const [branchId] = useState(() => uuidv4())
  const [launched, setLaunched] = useState<{ branch: BranchContext; inputs: JsonObject } | null>(
    null,
  )
  const reservedRef = useRef(false)
  const create = useMutation({
    retry: false,
    mutationFn: async (inputs: JsonObject) => {
      try {
        const response = await consoleClient.business.workbench.apps.byInstalledAppId.branches.post(
          {
            params: { installed_app_id: scope.installed_app_id },
            headers: { Origin: window.location.origin },
            body: { branch_id: branchId },
          },
        )
        return { branch: confirmWorkbenchRoot(response, { ...scope, branch_id: branchId }), inputs }
      } finally {
        reservedRef.current = false
      }
    },
    onSuccess: setLaunched,
  })
  if (launched)
    return <WorkbenchSession title={title} branch={launched.branch} inputs={launched.inputs} />
  return (
    <div className="space-y-4">
      <WorkbenchInputForm
        {...form}
        scopeKey={configurationKey}
        labels={labels}
        busy={create.isPending}
        onSubmit={(inputs) => {
          if (reservedRef.current || create.isPending) return
          reservedRef.current = true
          create.mutate(structuredClone(inputs))
        }}
      />
      {create.isError && (
        <p role="alert" className="system-sm-regular text-text-destructive">
          {labels.error}
        </p>
      )}
    </div>
  )
}
