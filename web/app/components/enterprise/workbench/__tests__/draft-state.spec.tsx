import type { ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { useWorkbenchDraft } from '../draft-state'

const scope = {
  workspace_id: 'workspace',
  actor_id: 'actor',
  installed_app_id: '00000000-0000-4000-8000-000000000001',
  branch_id: 'branch',
}
function setup() {
  const store = createStore()
  return ({ children }: { children: ReactNode }) => <Provider store={store}>{children}</Provider>
}
describe('workbench in-memory drafts', () => {
  it('should request an unload warning only while an active draft exists and remove it on unmount', () => {
    const view = renderHook(() => useWorkbenchDraft(scope), { wrapper: setup() })
    const unload = () => {
      const event = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(event)
      return event.defaultPrevented
    }
    expect(unload()).toBe(false)
    act(() => view.result.current[1]('unsent'))
    expect(unload()).toBe(true)
    act(() => view.result.current[1](''))
    expect(unload()).toBe(false)
    act(() => view.result.current[1]('unsent again'))
    view.unmount()
    expect(unload()).toBe(false)
  })
  it('should restore a draft after the same branch remounts', () => {
    const wrapper = setup()
    const first = renderHook(() => useWorkbenchDraft(scope), { wrapper })
    act(() => first.result.current[1]('unsent'))
    first.unmount()
    const second = renderHook(() => useWorkbenchDraft(scope), { wrapper })
    expect(second.result.current[0]).toBe('unsent')
  })
  it.each(['actor_id', 'workspace_id', 'installed_app_id', 'branch_id'] as const)(
    'should isolate drafts by %s',
    (key) => {
      const wrapper = setup()
      const first = renderHook(() => useWorkbenchDraft(scope), { wrapper })
      act(() => first.result.current[1]('private draft'))
      const second = renderHook(
        () =>
          useWorkbenchDraft({
            ...scope,
            [key]: key === 'installed_app_id' ? '00000000-0000-4000-8000-000000000002' : 'other',
          }),
        { wrapper },
      )
      expect(second.result.current[0]).toBe('')
    },
  )
  it('should apply acknowledgement clearing against the latest draft, not a stale value', () => {
    const { result } = renderHook(() => useWorkbenchDraft(scope), { wrapper: setup() })
    act(() => result.current[1]('sent'))
    act(() => result.current[1]('new draft'))
    act(() => result.current[1]((current) => (current === 'sent' ? '' : current)))
    expect(result.current[0]).toBe('new draft')
    act(() => result.current[1](''))
    expect(result.current[0]).toBe('')
  })
})
