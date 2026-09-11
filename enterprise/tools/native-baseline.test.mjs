import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  findChangedOriginalKeys,
  findProtectedChanges,
  isProtectedPath,
  isPreservedMainNavLayout,
} from './native-baseline.mjs'

describe('native Dify baseline preservation', () => {
  it('accepts the navigation shell relocation only with identical content and no reserved layout left', () => {
    assert.equal(isPreservedMainNavLayout('native\n', 'native\r\n', false), true)
    assert.equal(isPreservedMainNavLayout('native\n', 'changed\n', false), false)
    assert.equal(isPreservedMainNavLayout('native\n', 'native\n', true), false)
    assert.equal(isPreservedMainNavLayout('native\n', undefined, false), false)
    assert.equal(isPreservedMainNavLayout('', '', false), false)
  })
  it('protects native execution, routes, permissions and license', () => {
    for (const path of [
      'api/core/workflow/node_factory.py',
      'api/controllers/console/auth/login.py',
      'web/app/(commonLayout)/page.tsx',
      'web/app/components/workflow/index.tsx',
      'web/app/components/main-nav/routes.ts',
      'web/service/client.ts',
      'LICENSE',
      'docker/docker-compose.yaml',
    ])
      assert.equal(isProtectedPath(path), true, path)
  })

  it('allows only the explicit additive integration seams', () => {
    for (const path of [
      'enterprise/api/src/enterprise_platform/domain/rules.py',
      'web/app/(commonLayout)/enterprise/page.tsx',
      'web/app/components/enterprise/portal.tsx',
      'web/app/components/main-nav/index.tsx',
      'web/env.ts',
      'web/i18n/en-US/common.json',
    ])
      assert.equal(isProtectedPath(path), false, path)
  })

  it('does not allow lookalike prefixes or arbitrary locale edits', () => {
    assert.equal(isProtectedPath('web/app/components/enterprise-old/index.tsx'), true)
    assert.equal(isProtectedPath('web/i18n/en-US/workflow.json'), true)
  })

  it('reports modifications and deletions of baseline files, but permits new files', () => {
    const baseline = new Set(['api/core.py', 'web/app/page.tsx', 'web/env.ts'])
    assert.deepEqual(
      findProtectedChanges(
        ['api/core.py', 'web/app/page.tsx', 'web/env.ts', 'api/new-extension.py'],
        baseline,
      ),
      ['api/core.py', 'web/app/page.tsx'],
    )
  })

  it('allows additive translations but catches deletion and modification of native keys', () => {
    const original = { menu: { studio: 'Studio' }, enabled: true }
    assert.deepEqual(
      findChangedOriginalKeys(original, { ...original, enterprise: { title: 'Enterprise' } }),
      [],
    )
    assert.deepEqual(findChangedOriginalKeys(original, { menu: { studio: 'Changed' } }), [
      'menu.studio',
      'enabled',
    ])
    assert.deepEqual(findChangedOriginalKeys(original, {}), ['menu', 'enabled'])
  })

  it('preserves empty objects and rejects type changes at every original key', () => {
    assert.deepEqual(findChangedOriginalKeys({ native: {} }, {}), ['native'])
    assert.deepEqual(findChangedOriginalKeys({ native: {} }, { native: [] }), ['native'])
    assert.deepEqual(findChangedOriginalKeys({ native: {} }, { native: null }), ['native'])
    assert.deepEqual(findChangedOriginalKeys({}, []), ['$'])
    assert.deepEqual(findChangedOriginalKeys({ native: {} }, { native: { added: 'ok' } }), [])
  })
})
