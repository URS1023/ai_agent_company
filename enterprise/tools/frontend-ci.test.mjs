import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('enterprise CI executes frontend suites and fresh type validation on a hosted runner', async () => {
  const workflow = await readFile(
    new URL('../../.github/workflows/enterprise-foundation.yml', import.meta.url),
    'utf8',
  )
  const job = workflow.match(/^  frontend:\r?\n([\s\S]*?)(?=^  [a-z][a-z-]*:|$(?![\s\S]))/m)?.[1]
  assert.ok(job, 'enterprise frontend job must exist')
  assert.match(job, /runs-on: ubuntu-latest/)
  assert.match(job, /uses: \.\/\.github\/actions\/setup-web/)
  const previewCommand = 'node --test enterprise/dashboard/viewer/preview.test.mjs'
  assert.ok(job.includes(previewCommand), 'preview tests require installed workspace dependencies')
  assert.ok(job.indexOf('uses: ./.github/actions/setup-web') < job.indexOf(previewCommand))
  assert.ok(!workflow.split('  frontend:', 1)[0].includes(previewCommand))
  assert.match(job, /working-directory: packages\/dev-proxy\s+run: vp pack/)
  assert.match(
    job,
    /vp test run enterprise service\/client\.spec\.ts service\/console-router-loader\.spec\.ts/,
  )
  assert.match(job, /app\/components\/main-nav\/__tests__\/index\.spec\.tsx/)
  assert.match(job, /app\/components\/main-nav\/__tests__\/layout\.spec\.tsx/)
  assert.match(job, /next typegen/)
  assert.match(job, /--noEmit --pretty false --incremental false/)
  assert.ok(job.indexOf('next typegen') < job.indexOf('--noEmit'))
  assert.doesNotMatch(job, /continue-on-error|passWithNoTests/)
  assert.match(workflow, /node --test enterprise\/tools\/frontend-ci\.test\.mjs/)
})
