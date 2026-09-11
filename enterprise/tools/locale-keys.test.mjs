import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import test from 'node:test'

test('enterprise translation leaves never collide with a namespace in any locale', async () => {
  const root = new URL('../../web/i18n/', import.meta.url)
  const directories = await readdir(root, { withFileTypes: true })
  const conflicts = []
  let checked = 0
  for (const directory of directories.filter((entry) => entry.isDirectory())) {
    const values = JSON.parse(
      await readFile(new URL(`${directory.name}/common.json`, root), 'utf8'),
    )
    const keys = Object.keys(values).filter((key) => key.startsWith('enterprise.'))
    const leaves = new Set(keys)
    for (const key of keys) {
      const parts = key.split('.')
      for (let length = 1; length < parts.length; length += 1) {
        const prefix = parts.slice(0, length).join('.')
        if (leaves.has(prefix)) conflicts.push(`${directory.name}: ${prefix} / ${key}`)
      }
    }
    assert.ok(keys.length > 0, `${directory.name} has enterprise translations`)
    checked += 1
  }
  assert.ok(checked > 0)
  assert.deepEqual(conflicts, [])
})
