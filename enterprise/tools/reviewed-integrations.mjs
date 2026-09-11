import { createHash } from 'node:crypto'

// Exact reviewed bytes, not whole-file exemptions. Auth/permission/license code
// cannot be approved through this mechanism. Evidence text is never executed.
const reviewablePaths = new Set([
  'web/plugins/vite/next-static-image-test.ts',
  'web/app/components/base/chat/chat/answer/__tests__/agent-roster-response-content.spec.tsx',
  'web/app/components/base/copy-icon/index.tsx',
  'web/app/components/base/markdown-blocks/code-block.tsx',
  'api/core/app/task_pipeline/based_generate_task_pipeline.py',
  'api/core/app/apps/chat/generate_response_converter.py',
  'api/core/app/apps/agent_chat/generate_response_converter.py',
  'api/core/app/apps/advanced_chat/generate_response_converter.py',
  'api/tests/unit_tests/core/app/task_pipeline/test_based_generate_task_pipeline.py',
  'web/service/client.ts',
  'web/package.json',
  'web/next.config.ts',
  'web/app/(commonLayout)/layout.tsx',
  'web/app/components/main-nav/__tests__/layout.spec.tsx',
  'pnpm-workspace.yaml',
  'pnpm-lock.yaml',
  'api/configs/feature/__init__.py',
  'api/core/workflow/node_factory.py',
  'api/core/workflow/node_runtime.py',
  'api/controllers/console/app/app_import.py',
  'api/controllers/console/app/workflow.py',
  'api/services/workflow_service.py',
  'api/controllers/console/workspace/tool_providers.py',
  'api/controllers/console/apikey.py',
  'api/controllers/console/explore/completion.py',
  'api/core/app/task_pipeline/easy_ui_based_generate_task_pipeline.py',
  'packages/contracts/generated/api/console/installed-apps/types.gen.ts',
  'packages/contracts/generated/api/console/installed-apps/zod.gen.ts',
  'web/app/components/base/chat/chat-with-history/__tests__/chat-wrapper.spec.tsx',
])

function digest(text) {
  return createHash('sha256').update(text.replace(/\r\n/g, '\n')).digest('hex')
}

export function validateReviews(document, baseline) {
  if (!document || document.baseline !== baseline || !Array.isArray(document.entries))
    throw new Error('Integration review manifest does not match the fixed baseline.')
  const reviews = new Map()
  for (const entry of document.entries) {
    if (
      !entry ||
      !reviewablePaths.has(entry.path) ||
      reviews.has(entry.path) ||
      !/^[0-9a-f]{64}$/.test(entry.baselineSha256 ?? '') ||
      !/^[0-9a-f]{64}$/.test(entry.reviewedSha256 ?? '') ||
      typeof entry.reason !== 'string' ||
      !entry.reason.trim() ||
      !Array.isArray(entry.verification) ||
      !entry.verification.length ||
      entry.verification.some((value) => typeof value !== 'string' || !value.trim())
    )
      throw new Error('Invalid or out-of-scope integration review entry.')
    reviews.set(entry.path, entry)
  }
  return reviews
}

export function matchesReview(path, original, current, reviews) {
  const review = reviews.get(path)
  return (
    !!review &&
    review.baselineSha256 === digest(original) &&
    review.reviewedSha256 === digest(current)
  )
}
