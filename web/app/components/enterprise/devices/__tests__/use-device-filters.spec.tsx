import { act, waitFor } from '@testing-library/react'
import { renderHookWithNuqs } from '@/test/nuqs-testing'
import { useDeviceFilters } from '../use-device-filters'

describe('Device URL filters', () => {
  beforeEach(() => vi.clearAllMocks())

  it('should read page and literal leading-zero search values from the URL', () => {
    const { result } = renderHookWithNuqs(useDeviceFilters, {
      searchParams: '?page=3&q=0001&department=Plant%20A',
    })
    expect(result.current.filters).toEqual({ page: 3, q: '0001', department: 'Plant A' })
  })

  it.each(['-1', '0', '1.5', 'Infinity', '100000'])(
    'should default invalid page %s to one',
    (page) => {
      const { result } = renderHookWithNuqs(useDeviceFilters, { searchParams: `?page=${page}` })
      expect(result.current.filters.page).toBe(1)
    },
  )

  it('should reset pagination and preserve literal filter text when searching', async () => {
    const { result, onUrlUpdate } = renderHookWithNuqs(useDeviceFilters, {
      searchParams: '?page=3',
    })
    await act(async () => {
      await result.current.search('0001/%', 'Plant A')
    })
    await waitFor(() => expect(onUrlUpdate).toHaveBeenCalled())
    const update = onUrlUpdate.mock.calls.at(-1)?.[0]
    expect(update?.searchParams.get('page')).toBeNull()
    expect(update?.searchParams.get('q')).toBe('0001/%')
    expect(update?.searchParams.get('department')).toBe('Plant A')
    expect(update?.options.history).toBe('push')
  })
})
