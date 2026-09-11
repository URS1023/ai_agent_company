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

test('persistence CI applies every production migration in order and verifies the final schema', async () => {
  const workflow = await readFile(
    new URL('../../.github/workflows/enterprise-foundation.yml', import.meta.url),
    'utf8',
  )
  const job = workflow.replaceAll('\r\n', '\n').split('  persistence-integration:')[1]
  assert.ok(job)
  const modules = [
    'migrate',
    'migrate_sources',
    'migrate_workflow_setups',
    'migrate_workflow_credentials',
    'migrate_workflow_provisioning',
    'migrate_workflow_enrollment',
    'migrate_workflow_activation',
    'migrate_schedules',
    'migrate_dashboards',
    'migrate_sql_drafts',
    'migrate_sql_trials',
    'migrate_chat_messages',
    'migrate_chat_branches',
  ]
  let previous = -1
  for (const module of modules) {
    const command = `python -m enterprise_platform.persistence.${module}\n`
    const index = job.indexOf(command)
    assert.ok(index > previous, `Missing or out-of-order production migration: ${module}`)
    assert.match(
      job.slice(index + command.length),
      /^\s+--apply --url-env ENTERPRISE_TEST_DATABASE_URL --expected-database enterprise_test/,
    )
    previous = index
  }
  const finalCheck = job.indexOf('require_schema(inspect(connection), metadata)')
  assert.ok(finalCheck > previous, 'Final reflected schema must include migration 0013')
  assert.match(
    job,
    /from enterprise_platform.persistence.workbench_branches import BranchContextBase/,
  )
  assert.match(job, /table.to_metadata\(metadata\)/)
  assert.doesNotMatch(job, /continue-on-error|if: always\(\)/)
})
