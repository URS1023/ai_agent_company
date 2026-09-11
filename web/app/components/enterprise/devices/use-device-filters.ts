'use client'

import { createParser, parseAsString, useQueryStates } from 'nuqs'

const pageParser = createParser({
  parse: (value: string) => (/^[1-9]\d{0,4}$/.test(value) ? Number(value) : null),
  serialize: (value: number) => String(value),
}).withDefault(1)

export function useDeviceFilters() {
  const [filters, setFilters] = useQueryStates(
    {
      page: pageParser,
      q: parseAsString.withDefault(''),
      department: parseAsString.withDefault(''),
    },
    { history: 'push', shallow: true, clearOnDefault: true },
  )

  return {
    filters,
    setPage: (page: number) => setFilters({ page }),
    search: (q: string, department: string) =>
      setFilters({ page: 1, q: q.trim(), department: department.trim() }),
  }
}
