'use client'

import { createParser, useQueryState } from 'nuqs'

export const MAX_APPS_PAGE = 99999

const parseAsAppsPage = createParser<number>({
  parse: (value) => {
    if (!/^[1-9]\d{0,4}$/.test(value)) return null
    return Number(value)
  },
  serialize: (value) => value.toString(),
})
  .withDefault(1)
  .withOptions({ history: 'push', shallow: true, clearOnDefault: true })

export function useAppsPage() {
  const [page, setPage] = useQueryState('appsPage', parseAsAppsPage)
  return [page, setPage] as const
}
