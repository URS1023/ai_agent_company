import type { BranchContext } from '@enterprise/business-contracts/types'
import { confirmWorkbenchRoot } from '../launch'

const scope = {
  workspace_id: 'workspace',
  actor_id: 'actor',
  installed_app_id: '00000000-0000-4000-8000-000000000001',
  branch_id: 'root-1',
}
const branch: BranchContext = {
  scope,
  state: 'ready',
  revision: 1,
  conversation_id: null,
  head_message_id: null,
  inflight_client_message_id: null,
  origin: null,
}
describe('workbench root launch confirmation', () => {
  it('should accept only the requested ready empty root and detach its response', () => {
    expect(confirmWorkbenchRoot(branch, scope)).toEqual(branch)
    expect(confirmWorkbenchRoot(branch, scope)).not.toBe(branch)
  })
  it.each(['workspace_id', 'actor_id', 'installed_app_id', 'branch_id'] as const)(
    'should reject a foreign %s',
    (key) => {
      expect(() =>
        confirmWorkbenchRoot({ ...branch, scope: { ...scope, [key]: 'foreign' } }, scope),
      ).toThrow('Workbench launch unavailable')
    },
  )
  it.each([
    { state: 'archived' },
    { state: 'preparing' },
    { conversation_id: '00000000-0000-4000-8000-000000000002' },
    { inflight_client_message_id: '00000000-0000-4000-8000-000000000003' },
  ])('should not launch a used or non-ready root as an empty conversation: %j', (extra) => {
    expect(() => confirmWorkbenchRoot({ ...branch, ...extra }, scope)).toThrow(
      'Workbench launch unavailable',
    )
  })
})
