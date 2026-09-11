'use client'

import type { MessageScope } from '@enterprise/business-contracts/types'
import type { SetStateAction } from 'react'
import { atom, useAtom } from 'jotai'
import { useEffect, useMemo } from 'react'

// Ephemeral only: drafts must not silently persist in browser storage or cross identity scopes.
const draftsAtom = atom<ReadonlyMap<string, string>>(new Map())

export function useWorkbenchDraft(scope: MessageScope) {
  const key = JSON.stringify([
    scope.workspace_id,
    scope.actor_id,
    scope.installed_app_id,
    scope.branch_id,
  ])
  const scopedAtom = useMemo(
    () =>
      atom(
        (get) => get(draftsAtom).get(key) ?? '',
        (get, set, update: SetStateAction<string>) => {
          const drafts = get(draftsAtom)
          const current = drafts.get(key) ?? ''
          const next = typeof update === 'function' ? update(current) : update
          if (next === current) return
          const changed = new Map(drafts)
          if (next.length) changed.set(key, next)
          else changed.delete(key)
          set(draftsAtom, changed)
        },
      ),
    [key],
  )
  const [draft, setDraft] = useAtom(scopedAtom)
  const hasDraft = draft.length > 0
  useEffect(() => {
    if (!hasDraft) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [hasDraft])
  return [draft, setDraft] as const
}
