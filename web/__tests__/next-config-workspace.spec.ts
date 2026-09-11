import { resolve } from 'node:path'
import config from '../next.config'

vi.mock('@next/mdx', () => ({ default: () => (value: object) => value }))
vi.mock('code-inspector-plugin', () => ({ codeInspectorPlugin: () => ({}) }))
vi.mock('../env', () => ({ env: { NEXT_PUBLIC_BASE_PATH: '' } }))

describe('Next worktree build root', () => {
  it('should resolve the owning workspace instead of inferring a parent checkout', () => {
    expect(config.turbopack?.root).toBe(resolve(import.meta.dirname, '../..'))
  })
})
