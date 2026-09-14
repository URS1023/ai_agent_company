import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import test from 'node:test'
import { validateReviews, matchesReview } from './reviewed-integrations.mjs'

const digest = (value) => createHash('sha256').update(value.replace(/\r\n/g, '\n')).digest('hex')
const entry = {
  path: 'web/service/client.ts',
  baselineSha256: digest('original\n'),
  reviewedSha256: digest('additive reviewed version\n'),
  reason: 'Add a business namespace while retaining the native link.',
  verification: ['Native client regression suite and additive routing tests.'],
}

test('limits native copy-control repairs to exact reviewed component bytes', () => {
  for (const path of [
    'web/app/components/base/copy-icon/index.tsx',
    'web/app/components/base/markdown-blocks/code-block.tsx',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
  }
  assert.throws(() =>
    validateReviews(
      {
        baseline: 'commit',
        entries: [{ ...entry, path: 'web/app/components/base/action-button/index.tsx' }],
      },
      'commit',
    ),
  )
})

test('limits native chat repair reviews to exact schema and test file bytes', () => {
  for (const path of [
    'api/controllers/console/explore/completion.py',
    'packages/contracts/generated/api/console/installed-apps/types.gen.ts',
    'packages/contracts/generated/api/console/installed-apps/zod.gen.ts',
    'web/app/components/base/chat/chat-with-history/__tests__/chat-wrapper.spec.tsx',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'changed runtime behavior\n', reviews), false)
  }
  for (const path of [
    'api/controllers/console/explore/wraps.py',
    'api/controllers/console/app/completion.py',
    'web/app/components/base/chat/chat-with-history/chat-wrapper.tsx',
  ]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
  }
})

test('permits only exact reviewed navigation-shell import updates', () => {
  for (const path of [
    'web/app/(commonLayout)/layout.tsx',
    'web/app/components/main-nav/__tests__/layout.spec.tsx',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed logic\n', reviews), false)
  }
  assert.throws(() =>
    validateReviews(
      {
        baseline: 'commit',
        entries: [{ ...entry, path: 'web/app/(commonLayout)/hydration-boundary.tsx' }],
      },
      'commit',
    ),
  )
})

test('permits exact reviewed Webpack configuration but not adjacent environment or proxy files', () => {
  const path = 'web/next.config.ts'
  const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
  assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
  assert.equal(matchesReview(path, 'original\n', 'unreviewed headers\n', reviews), false)
  for (const adjacent of ['web/env.ts', 'web/proxy.ts']) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path: adjacent }] }, 'commit'),
    )
  }
})

test('accepts only the exact reviewed integration bytes and baseline', () => {
  const reviews = validateReviews({ baseline: 'commit', entries: [entry] }, 'commit')
  assert.equal(
    matchesReview(entry.path, 'original\n', 'additive reviewed version\n', reviews),
    true,
  )
  assert.equal(matchesReview(entry.path, 'original\n', 'unreviewed change\n', reviews), false)
  assert.equal(
    matchesReview(entry.path, 'different base\n', 'additive reviewed version\n', reviews),
    false,
  )
})

test('normalizes checkout line endings but preserves every other byte', () => {
  const reviews = validateReviews({ baseline: 'commit', entries: [entry] }, 'commit')
  assert.equal(
    matchesReview(entry.path, 'original\r\n', 'additive reviewed version\r\n', reviews),
    true,
  )
  assert.equal(
    matchesReview(entry.path, 'original\n', 'additive reviewed version \n', reviews),
    false,
  )
})

test('permits exact reviewed managed-import bytes without opening adjacent auth or app controllers', () => {
  const path = 'api/controllers/console/app/app_import.py'
  const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
  assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
  assert.equal(
    matchesReview(path, 'original\n', 'changed auth or import behavior\n', reviews),
    false,
  )
  for (const adjacent of [
    'api/controllers/console/wraps.py',
    'api/controllers/console/app/wraps.py',
    'api/controllers/console/app/app.py',
    'api/services/app_dsl_service.py',
  ]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path: adjacent }] }, 'commit'),
    )
  }
})

test('never permits reviews to exempt native auth, permissions or license files', () => {
  for (const path of [
    'LICENSE',
    'api/controllers/console/auth/login.py',
    'web/service/base.ts',
    'web/env.ts',
  ]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
  }
})

test('permits exact reviewed publication seams without exempting credential or permission services', () => {
  for (const path of [
    'api/controllers/console/app/workflow.py',
    'api/services/workflow_service.py',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed publish\n', reviews), false)
  }
  for (const path of [
    'api/controllers/console/app/wraps.py',
    'api/services/tools/builtin_tools_manage_service.py',
    'api/services/agent/workflow_publish_service.py',
    'api/models/workflow.py',
  ]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
  }
})

test('permits exact provider scope controller bytes but protects credential storage and permissions', () => {
  const path = 'api/controllers/console/workspace/tool_providers.py'
  const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
  assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
  assert.equal(matchesReview(path, 'original\n', 'unreviewed credentials\n', reviews), false)
  for (const adjacent of [
    'api/services/tools/builtin_tools_manage_service.py',
    'api/models/tools.py',
    'api/controllers/console/wraps.py',
  ]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path: adjacent }] }, 'commit'),
    )
  }
})

test('rejects stale baselines, duplicate paths, missing evidence and malformed hashes', () => {
  assert.throws(() => validateReviews({ baseline: 'old', entries: [entry] }, 'commit'))
  assert.throws(() => validateReviews({ baseline: 'commit', entries: [entry, entry] }, 'commit'))
  for (const patch of [{ reviewedSha256: '*' }, { verification: [] }, { reason: '' }]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, ...patch }] }, 'commit'),
    )
  }
})

test('unreviewed files remain protected even when another file is approved', () => {
  const reviews = validateReviews({ baseline: 'commit', entries: [entry] }, 'commit')
  assert.equal(
    matchesReview('pnpm-lock.yaml', 'original\n', 'additive reviewed version\n', reviews),
    false,
  )
})

test('permits only three exact native managed-execution seams, not their directories', () => {
  for (const path of [
    'api/configs/feature/__init__.py',
    'api/core/workflow/node_factory.py',
    'api/core/workflow/node_runtime.py',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
  }
  for (const path of [
    'api/configs/__init__.py',
    'api/core/workflow/graph_engine.py',
    'api/core/tools/tool_manager.py',
    'api/core/tools/plugin_tool/tool.py',
  ]) {
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
  }
})

test('permits exact reviewed app-token scope seam but not token models or caches', () => {
  const path = 'api/controllers/console/apikey.py'
  const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
  assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
  assert.equal(matchesReview(path, 'original\n', 'unreviewed issuance\n', reviews), false)
  for (const adjacent of [
    'api/models/model.py',
    'api/services/api_token_service.py',
    'api/controllers/console/wraps.py',
  ])
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path: adjacent }] }, 'commit'),
    )
})

test('permits only exact reviewed native message terminal metadata integration', () => {
  const path = 'api/core/app/task_pipeline/easy_ui_based_generate_task_pipeline.py'
  const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
  assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
  assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
})

test('permits exact error terminal integration bytes without directory exemptions', () => {
  for (const path of [
    'api/core/app/task_pipeline/based_generate_task_pipeline.py',
    'api/core/app/apps/chat/generate_response_converter.py',
    'api/core/app/apps/agent_chat/generate_response_converter.py',
    'api/core/app/apps/advanced_chat/generate_response_converter.py',
    'api/tests/unit_tests/core/app/task_pipeline/test_based_generate_task_pipeline.py',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
  }
})

test('limits Web runtime fixes to the reviewed plugin and roster test bytes', () => {
  for (const path of [
    'web/plugins/vite/next-static-image-test.ts',
    'web/app/components/base/chat/chat/answer/__tests__/agent-roster-response-content.spec.tsx',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
  }
  for (const path of ['web/plugins/vite/utils.ts', 'web/app/components/base/markdown/index.tsx'])
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
})

test('limits re-enabled regression reviews to exact test bytes', () => {
  for (const path of [
    'web/app/components/base/mermaid/__tests__/index.spec.tsx',
    'web/app/components/workflow/nodes/parameter-extractor/components/extract-parameter/__tests__/list.spec.tsx',
  ]) {
    const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
    assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
    assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
  }
  for (const path of [
    'web/app/components/base/mermaid/index.tsx',
    'web/app/components/workflow/nodes/parameter-extractor/components/extract-parameter/list.tsx',
  ])
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
})

test('limits chat-history lazy-chunk review to its exact regression test', () => {
  const path = 'web/app/components/workflow/panel/chat-record/__tests__/index.spec.tsx'
  const reviews = validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit')
  assert.equal(matchesReview(path, 'original\n', 'additive reviewed version\n', reviews), true)
  assert.equal(matchesReview(path, 'original\n', 'unreviewed change\n', reviews), false)
  for (const path of [
    'web/app/components/workflow/panel/chat-record/index.tsx',
    'web/app/components/base/markdown/streamdown-wrapper.tsx',
  ])
    assert.throws(() =>
      validateReviews({ baseline: 'commit', entries: [{ ...entry, path }] }, 'commit'),
    )
})
