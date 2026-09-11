import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { buildDefaultWorkflow } from './default-workflows.mjs'

test('both scenarios use an editable native workflow and the current DSL format', () => {
  const version = readFileSync(
    new URL('../../api/constants/dsl_version.py', import.meta.url),
    'utf8',
  ).match(/CURRENT_APP_DSL_VERSION = "([^"]+)"/)[1]
  for (const scenario of ['alert', 'quality']) {
    const dsl = buildDefaultWorkflow(scenario)
    assert.equal(dsl.version, version)
    assert.equal(dsl.kind, 'app')
    assert.equal(dsl.app.mode, 'workflow')
    assert.equal(dsl.workflow.graph.nodes.length, 3)
    assert.deepEqual(
      dsl.workflow.graph.nodes.map((node) => node.data.type),
      ['start', 'tool', 'end'],
    )
    assert.deepEqual(
      dsl.workflow.graph.edges.map((edge) => [edge.source, edge.target]),
      [
        ['start', 'assessment'],
        ['assessment', 'end'],
      ],
    )
  }
  assert.notEqual(buildDefaultWorkflow('alert').app.name, buildDefaultWorkflow('quality').app.name)
})

test('assessment accepts no untrusted identity and the end returns the full tool text unchanged', () => {
  const dsl = buildDefaultWorkflow('alert')
  const [start, assessment, end] = dsl.workflow.graph.nodes
  assert.deepEqual(start.data.variables, [])
  assert.equal(
    assessment.data.provider_id,
    'enterprise/enterprise_device_assessment/enterprise_device',
  )
  assert.equal(assessment.data.tool_name, 'evaluate_device')
  assert.deepEqual(assessment.data.tool_parameters, {})
  assert.deepEqual(assessment.data.tool_configurations, {})
  assert.equal(assessment.data.credential_id, undefined)
  assert.deepEqual(end.data.outputs, [
    { variable: 'result', value_type: 'string', value_selector: ['assessment', 'text'] },
  ])
  assert.deepEqual(dsl.workflow.environment_variables, [])
})

test('a selected credential is explicit, validated and never inferred from a default', () => {
  const credentialId = '6be6b6aa-09d4-4d83-9902-465775cf4d5b'
  assert.equal(
    buildDefaultWorkflow('quality', { credentialId }).workflow.graph.nodes[1].data.credential_id,
    credentialId,
  )
  for (const invalid of [
    'secret-token',
    '',
    '00000000-0000-0000-0000-000000000000',
    '../credential',
  ]) {
    assert.throws(() => buildDefaultWorkflow('alert', { credentialId: invalid }), /credential/)
  }
  assert.throws(() => buildDefaultWorkflow('unknown'), /scenario/)
})

test('each invocation owns its graph and does not mutate other scenario templates', () => {
  const first = buildDefaultWorkflow('alert')
  first.workflow.graph.nodes[1].data.tool_parameters = { injected: 'value' }
  assert.deepEqual(buildDefaultWorkflow('alert').workflow.graph.nodes[1].data.tool_parameters, {})
})

test('distributed YAML assets exactly match the current generator without embedded credentials', () => {
  for (const scenario of ['alert', 'quality']) {
    const bytes = readFileSync(new URL(`./default-${scenario}.yml`, import.meta.url), 'utf8')
    assert.equal(bytes, `${JSON.stringify(buildDefaultWorkflow(scenario), null, 2)}\n`)
  }
})

test('Python runtime templates match the same reviewed native workflow generator', () => {
  for (const scenario of ['alert', 'quality']) {
    const bytes = readFileSync(
      new URL(
        `../api/src/enterprise_platform/workflow_templates/default-${scenario}.yml`,
        import.meta.url,
      ),
      'utf8',
    )
    assert.equal(bytes, `${JSON.stringify(buildDefaultWorkflow(scenario), null, 2)}\n`)
  }
})
