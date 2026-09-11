import { expect, it } from 'vitest'

it('initializes snapshot state for imported and global assertions', () => {
  const runtime = globalThis as typeof globalThis & { expect: typeof expect }
  expect(expect).toBe(runtime.expect)
  expect(1).toMatchInlineSnapshot(`1`)
  runtime.expect(1).toMatchInlineSnapshot(`1`)
})
