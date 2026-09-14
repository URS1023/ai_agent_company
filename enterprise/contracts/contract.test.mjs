import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
// eslint-disable-next-line test/no-import-node-test -- Standalone generation checks do not load the web test runtime.
import test from 'node:test'
// Node's type stripping needs the extension; packages use normal workspace resolution.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith('./') && specifier.endsWith('.gen'))
      return nextResolve(`${specifier}.ts`, context)
    return nextResolve(specifier, context)
  },
})

const { contract } = await import('@enterprise/business-contracts/orpc')
const validators = await import('@enterprise/business-contracts/zod')
const document = JSON.parse(
  readFileSync(new URL('./business-openapi.json', import.meta.url), 'utf8'),
)

test('saved trial evidence is available only through scoped GET', () => {
  assert.ok(contract.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.get)
  const route =
    document.paths[
      '/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}'
    ]
  assert.ok(route.get)
  assert.equal(route.post, undefined)
  assert.equal(route.get.requestBody, undefined)
  assert.deepEqual(Object.keys(validators.zSqlTrialEvidence.shape).sort(), [
    'actor_id',
    'recorded_at',
    'result',
    'schema_version',
    'trial_id',
  ])
})

test('saved trial assessment reports sample compatibility without semantic approval', () => {
  assert.ok(
    contract.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.assessment.get,
  )
  const input = { trial_id: 'trial-1', slot_id: 'count', status: 'sample_compatible', issues: [] }
  const result = validators.zSqlTrialAssessment.parse(input)
  assert.equal(result.semantic_status, 'not_reviewed')
  assert.equal(result.unit_status, 'not_reviewed')
  assert.equal(
    validators.zSqlTrialAssessment.safeParse({ ...input, status: 'approved' }).success,
    false,
  )
  assert.equal(
    validators.zSqlTrialAssessment.safeParse({
      ...input,
      issues: Array(101).fill({ code: 'null_not_allowed' }),
    }).success,
    false,
  )
})

test('trial history lists bounded metadata with a cursor and no result rows', () => {
  assert.ok(contract.dashboards.byDashboardId.sqlProposals.byDraftId.trials.get)
  const route =
    document.paths['/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials']
  assert.equal(route.post, undefined)
  assert.equal(
    route.get.parameters.find((parameter) => parameter.name === 'limit').schema.maximum,
    100,
  )
  assert.deepEqual(Object.keys(validators.zSqlTrialSummary.shape).sort(), [
    'actor_id',
    'dashboard_id',
    'draft_id',
    'recorded_at',
    'trial_id',
    'workspace_id',
  ])
  assert.deepEqual(validators.zSqlTrialPage.parse({ items: [], next_cursor: null }), {
    items: [],
    next_cursor: null,
  })
})

test('SQL trials expose scoped commands and lossless unreviewed results', () => {
  assert.ok(contract.dashboards.byDashboardId.sqlProposals.byDraftId.trial.post)
  assert.deepEqual(Object.keys(validators.zSqlTrialCommand.shape).sort(), [
    'expected_design_identity',
    'expected_revision',
    'slot_id',
  ])
  const result = {
    workspace_id: 'w',
    dashboard_id: 'screen',
    draft_id: 'draft',
    slot_id: 'count',
    dashboard_revision: 1,
    design_identity: 'a'.repeat(64),
    source: { workspace_id: 'w', source_id: 'source', revision: 'v1' },
    draft_hash: 'b'.repeat(64),
    read_fingerprint: 'c'.repeat(64),
    captured_at: '2026-09-11T00:00:00Z',
    columns: ['count'],
    rows: [[{ kind: 'integer', value: '18446744073709551616' }]],
    row_count: 1,
    status: 'trial_only',
    semantic_status: 'not_reviewed',
  }
  assert.equal(validators.zSqlTrialResult.parse(result).rows[0][0].value, '18446744073709551616')
  assert.equal(
    validators.zSqlTrialResult.safeParse({ ...result, status: 'approved' }).success,
    false,
  )
  assert.equal(
    validators.zSqlTrialResult.safeParse({ ...result, semantic_status: 'verified' }).success,
    false,
  )
  const route =
    document.paths['/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trial']
  assert.ok(route.post)
  assert.equal(route.get, undefined)
  assert.ok(
    route.post.parameters.some((parameter) => parameter.name === 'Origin' && parameter.required),
  )
})

test('SQL generation exposes a prompt command and review-only draft, not execution inputs', () => {
  assert.ok(contract.dashboards.byDashboardId.sqlProposals.post)
  assert.deepEqual(Object.keys(validators.zGenerateSqlCommand.shape).sort(), [
    'expected_design_identity',
    'expected_revision',
    'prompt',
    'source_id',
  ])
  assert.deepEqual(Object.keys(validators.zSqlSlotProposal.shape).sort(), [
    'field_map',
    'metric_definition',
    'slot_id',
    'sql',
    'time_definition',
  ])
  const command = {
    expected_design_identity: 'a'.repeat(64),
    expected_revision: 1,
    prompt: 'Count inspections',
    source_id: 'source-1',
  }
  assert.equal(validators.zGenerateSqlCommand.safeParse(command).success, true)
  assert.equal(
    validators.zGenerateSqlCommand.safeParse({ ...command, expected_revision: true }).success,
    false,
  )
  assert.deepEqual(validators.zSqlGenerationReceipt.shape.status.safeParse('draft').success, true)
  assert.equal(validators.zSqlGenerationReceipt.shape.status.safeParse('published').success, false)
  assert.equal(document.components.schemas.GenerateSqlCommand.additionalProperties, false)
})

test('query discovery exposes only management selection metadata', () => {
  assert.ok(contract.dashboardQueries.get)
  assert.deepEqual(Object.keys(validators.zDashboardQueryChoice.shape).sort(), [
    'columns',
    'device_id',
    'parameters',
    'query_ref',
    'query_revision',
  ])
  assert.deepEqual(Object.keys(validators.zDashboardQueryParameter.shape).sort(), [
    'input_key',
    'kind',
    'nullable',
  ])
  assert.equal(validators.zDashboardQueryCatalog.safeParse({ items: [] }).success, true)
})

test('dashboard list is a bounded metadata-only generated contract', () => {
  assert.ok(contract.dashboards.get)
  assert.deepEqual(Object.keys(validators.zDashboardSummary.shape).sort(), [
    'id',
    'name',
    'revision',
    'status',
    'template_id',
  ])
  const route = document.paths['/enterprise/api/v1/dashboards'].get
  assert.equal(route.parameters.find((item) => item.name === 'limit').schema.maximum, 100)
  assert.equal(validators.zDashboardPage.safeParse({ items: [], next_cursor: null }).success, true)
})

test('template discovery exposes identity and slot contracts, not visual documents', () => {
  assert.ok(contract.dashboardTemplates.get)
  assert.deepEqual(Object.keys(validators.zDashboardTemplateView.shape).sort(), [
    'design_identity',
    'renderer_build_id',
    'slots',
    'template_id',
    'template_revision',
  ])
  assert.equal(validators.zDashboardTemplateCatalog.safeParse({ items: [] }).success, true)
})

test('dashboard creation pins a template and requires a retry key', () => {
  assert.ok(contract.dashboards.post)
  assert.deepEqual(Object.keys(validators.zDashboardCreateCommand.shape).sort(), [
    'expected_design_identity',
    'name',
    'template_id',
  ])
  assert.ok(
    document.paths['/enterprise/api/v1/dashboards'].post.parameters.some(
      (item) => item.name === 'idempotency-key' && item.required,
    ),
  )
})

test('dashboard binding contract allows versioned references without SQL or visual edits', () => {
  assert.ok(contract.dashboards.byDashboardId.bindings.get)
  assert.ok(contract.dashboards.byDashboardId.bindings.put)
  assert.deepEqual(Object.keys(validators.zDashboardBindingItem.shape).sort(), [
    'field_map',
    'parameters',
    'query_ref',
    'query_revision',
    'slot_id',
  ])
  const command = { expected_revision: 1, expected_design_identity: 'a'.repeat(64), bindings: [] }
  assert.equal(validators.zDashboardBindingWrite.safeParse(command).success, true)
  assert.equal(
    validators.zDashboardBindingWrite.safeParse({ ...command, expected_revision: true }).success,
    false,
  )
  assert.equal('visual_json' in validators.zDashboardBindingView.shape, false)
})

test('dashboard contracts retain precision and expose no arbitrary SQL or design writes', () => {
  assert.ok(document.paths['/enterprise/api/v1/dashboards/{dashboard_id}'].get)
  assert.ok(document.paths['/enterprise/api/v1/dashboards/{dashboard_id}/refresh'].post)
  assert.deepEqual(Object.keys(validators.zDashboardRefreshCommand.shape), ['expected_revision'])
  assert.equal(
    validators.zDashboardInteger.safeParse({ kind: 'integer', value: '9007199254740993' }).success,
    true,
  )
  assert.equal(validators.zDashboardInteger.safeParse({ kind: 'integer', value: 1 }).success, false)
  assert.equal(
    validators.zDashboardDecimal.safeParse({ kind: 'decimal', value: '98.2500' }).success,
    true,
  )
  for (const field of [
    'bindings',
    'parameters',
    'visual_json',
    'source_id',
    'creation_request_hash',
  ])
    assert.equal(field in validators.zDashboardView.shape, false)
})

test('schedule management contracts expose configuration only, not worker execution', () => {
  const inputKeys = Object.keys(validators.zCreateSchedule.shape)
  for (const key of ['workspace_id', 'binding_id', 'id', 'enabled', 'token', 'secret_ref'])
    assert.equal(inputKeys.includes(key), false)
  assert.equal(
    validators.zScheduleSwitch.safeParse({ expected_revision: 1, enabled: false }).success,
    true,
  )
  assert.equal(
    validators.zScheduleSwitch.safeParse({ expected_revision: true, enabled: false }).success,
    false,
  )
  assert.equal(
    validators.zScheduleSwitch.safeParse({ expected_revision: 1, enabled: 'false' }).success,
    false,
  )
  assert.ok(document.paths['/enterprise/api/v1/schedules/{schedule_id}'].get)
  const lookup = document.paths['/enterprise/api/v1/devices/{device_id}/{scenario}/schedule'].get
  assert.ok(lookup)
  assert.ok(
    lookup.responses['200'].content['application/json'].schema.anyOf.some(
      (schema) => schema.type === 'null',
    ),
  )
  assert.ok(contract.devices.byDeviceId.byScenario.schedule.get)
  assert.ok(contract.devices.byDeviceId.byScenario.schedule.actors.get)
  assert.deepEqual(Object.keys(validators.zScheduleActorChoice.shape).sort(), [
    'actor_id',
    'display_name',
  ])
  const actors = document.paths['/enterprise/api/v1/devices/{device_id}/{scenario}/schedule/actors']
  assert.deepEqual(Object.keys(actors), ['get'])
  assert.ok(document.paths['/enterprise/api/v1/schedules/{schedule_id}/state'].post)
  assert.equal(document.paths['/enterprise/api/v1/schedules/poll'], undefined)
  assert.equal(document.paths['/enterprise/api/v1/schedules/{schedule_id}/tick'], undefined)
})

test('every exported HTTP operation has a real generated oRPC procedure', () => {
  const actual = []
  function visit(router) {
    for (const value of Object.values(router)) {
      if (value['~orpc']) {
        const { method, path } = value['~orpc'].route
        actual.push(`${method} ${path}`)
      } else {
        visit(value)
      }
    }
  }
  visit(contract)
  const expected = Object.entries(document.paths).flatMap(([path, operations]) =>
    Object.keys(operations).map((method) => `${method.toUpperCase()} ${path}`),
  )
  assert.deepEqual(actual.sort(), expected.sort())
  assert.ok(contract.devices.byDeviceId.bindings.byScenario.runs.post)
  assert.ok(contract.runs.byRunId.events.get)
})

test('generated input schemas keep leading-zero device codes and mandatory concurrency headers', () => {
  const result = validators.zDeviceCreate.parse({ device_code: '0001', name: 'Pump' })
  assert.equal(result.device_code, '0001')
  const headers = validators.zUpdateDeviceEnterpriseApiV1DevicesDeviceIdPutHeaders
  assert.equal(headers.safeParse({ Origin: 'https://portal.example' }).success, false)
  assert.equal(headers.safeParse({ 'if-match': '"1"' }).success, false)
  assert.equal(
    headers.safeParse({ 'if-match': '"1"', Origin: 'https://portal.example' }).success,
    true,
  )
  assert.equal(
    headers.safeParse({ 'if-match': '*', Origin: 'https://portal.example' }).success,
    false,
  )
})

test('generated public run projection has no internal nonce, credential or raw snapshot fields', () => {
  const properties = Object.keys(validators.zRunView.shape)
  for (const name of ['dispatch_nonce', 'secret_ref', 'input_snapshot', 'spec'])
    assert.equal(properties.includes(name), false)
  assert.ok(properties.includes('input_snapshot_digest'))
  assert.deepEqual(validators.zErrorResponse.parse({ code: 'invalid_input' }), {
    code: 'invalid_input',
  })
})

test('run events use an array and sequence cursor, never a device-list page envelope', () => {
  const query = validators.zEventsEnterpriseApiV1RunsRunIdEventsGetQuery
  assert.deepEqual(Object.keys(query.shape), ['after_sequence'])
  assert.deepEqual(query.parse({}), { after_sequence: 0 })
  assert.equal(query.safeParse({ after_sequence: -1 }).success, false)

  const events = [
    {
      sequence: 21,
      workspace_id: 'workspace-1',
      resource_id: 'run-1',
      run_id: 'run-1',
      actor_id: 'actor-1',
      action: 'run.completed',
      data: {},
      created_at: '2026-09-08T00:00:00Z',
    },
  ]
  const response = validators.zEventsEnterpriseApiV1RunsRunIdEventsGetResponse
  assert.deepEqual(response.parse(events), events)
  assert.deepEqual(response.parse([]), [])
  assert.equal(response.safeParse({ items: events, offset: 0, limit: 20, total: 1 }).success, false)
  const operation = document.paths['/enterprise/api/v1/runs/{run_id}/events'].get
  assert.equal(operation.responses['200'].content['application/json'].schema.type, 'array')
})

test('run reports accept pending results and an omitted optional evidence object', () => {
  const run = {
    id: 'run-1',
    device_id: 'device-1',
    scenario: 'alert',
    binding_revision: 1,
    specification_revision: 'spec-1',
    status: 'queued',
    result: null,
    reason_code: null,
    has_input_snapshot: false,
    input_snapshot_digest: null,
    parameters: {},
    created_at: '2026-09-08T00:00:00Z',
    updated_at: '2026-09-08T00:00:00Z',
  }
  assert.deepEqual(validators.zRunView.parse(run), run)
  const completed = {
    ...run,
    status: 'succeeded',
    result: { scenario: 'alert', conclusion: 'normal', complete: true },
  }
  assert.deepEqual(validators.zRunView.parse(completed), completed)
})

test('generated identity projection and device search retain the real server schema', () => {
  assert.equal(contract.me.get['~orpc'].route.path, '/enterprise/api/v1/me')
  const access = {
    actor_id: 'actor-1',
    workspace_id: 'workspace-1',
    display_name: 'Viewer',
    permissions: { read: true, manage: false, run: false, review: false },
  }
  assert.deepEqual(validators.zCurrentAccessEnterpriseApiV1MeGetResponse.parse(access), access)
  assert.equal(
    validators.zBusinessAccess.safeParse({ ...access, workspace_id: undefined }).success,
    false,
  )
  const query = validators.zListDevicesEnterpriseApiV1DevicesGetQuery
  assert.deepEqual(query.parse({ q: '0001', department: 'Operations' }), {
    offset: 0,
    limit: 50,
    q: '0001',
    department: 'Operations',
  })
  for (const field of ['q', 'department']) {
    assert.equal(query.safeParse({ [field]: 'x'.repeat(200) }).success, true)
    assert.equal(query.safeParse({ [field]: 'x'.repeat(201) }).success, false)
    assert.equal(query.safeParse({ [field]: null }).success, true)
  }
})

test('generated files retain generator provenance instead of hand-written DTOs', () => {
  for (const filename of ['types.gen.ts', 'zod.gen.ts', 'orpc.gen.ts']) {
    const source = readFileSync(new URL(`./generated/${filename}`, import.meta.url), 'utf8')
    assert.match(source, /^\/\/ This file is auto-generated by @hey-api\/openapi-ts/)
    assert.doesNotMatch(source, /fixture-private|dispatch_nonce/)
  }
})

test('provisioning commands expose versioned references, not server secrets or phase commands', () => {
  assert.deepEqual(Object.keys(validators.zProvisioningStartRequest.shape).sort(), [
    'config_ref',
    'config_revision',
  ])
  assert.deepEqual(Object.keys(validators.zProvisioningAdvanceRequest.shape), ['expected_revision'])
  for (const revision of [0, -1, true, '1', 1.5]) {
    assert.equal(
      validators.zProvisioningAdvanceRequest.safeParse({ expected_revision: revision }).success,
      false,
    )
    assert.equal(
      validators.zProvisioningStartRequest.safeParse({
        config_ref: 'default',
        config_revision: revision,
      }).success,
      false,
    )
  }
  assert.equal(
    validators.zProvisioningAdvanceRequest.safeParse({ expected_revision: 1 }).success,
    true,
  )
  for (const field of [
    'claim_nonce',
    'request_key',
    'request_hash',
    'master_secret',
    'signing_secret',
    'authorization',
    'cookie_header',
  ])
    assert.equal(Object.hasOwn(validators.zProvisioningView.shape, field), false)
  assert.ok(contract.workflowSetups.bySetupId.provisioning.post)
  assert.ok(contract.workflowProvisioning.byProvisioningId.get)
  assert.ok(contract.workflowProvisioning.byProvisioningId.advance.post)
  assert.equal(validators.zProvisioningState.safeParse('ready').success, false)
  assert.equal(
    validators.zProvisioningState.safeParse('published_pending_enrollment').success,
    true,
  )
})

test('sample checking selects a configured policy and never returns semantic approval', () => {
  const path =
    '/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/sample-check'
  assert.deepEqual(Object.keys(document.paths[path]), ['get'])
  const query = document.paths[path].get.parameters.filter((parameter) => parameter.in === 'query')
  assert.deepEqual(query.map((parameter) => parameter.name).sort(), [
    'policy_id',
    'policy_revision',
  ])
  assert.ok(query.every((parameter) => parameter.required))
  const value = {
    policy_id: 'device-count',
    policy_revision: 'v1',
    policy_hash: 'a'.repeat(64),
    result: {
      trial_id: 'trial-1',
      slot_id: 'a',
      status: 'sample_constraints_passed',
      semantic_status: 'not_reviewed',
      issues: [],
      issues_truncated: false,
    },
  }
  assert.equal(validators.zSqlPolicySampleCheck.safeParse(value).success, true)
  assert.equal(
    validators.zSqlPolicySampleCheck.safeParse({
      ...value,
      result: { ...value.result, semantic_status: 'approved' },
    }).success,
    false,
  )
  assert.ok(
    contract.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.sampleCheck.get,
  )
})

test('automatic sample checking requires no policy identifiers or request body', () => {
  const path =
    '/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/sample-check/automatic'
  assert.deepEqual(Object.keys(document.paths[path]), ['get'])
  assert.equal(document.paths[path].get.requestBody, undefined)
  assert.equal(
    document.paths[path].get.parameters.some((parameter) => parameter.in === 'query'),
    false,
  )
  assert.ok(
    contract.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.sampleCheck.automatic
      .get,
  )
})

test('saved trial preview preserves tagged data without publishing or accepting a design', () => {
  const path =
    '/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/trials/{trial_id}/preview'
  assert.deepEqual(Object.keys(document.paths[path]), ['get'])
  assert.equal(document.paths[path].get.requestBody, undefined)
  assert.ok(contract.dashboards.byDashboardId.sqlProposals.byDraftId.trials.byTrialId.preview.get)
  const value = {
    workspace_id: 'w',
    dashboard_id: 'd',
    draft_id: 'draft',
    trial_id: 'trial',
    dashboard_revision: 1,
    template_id: 'template',
    design_identity: 'a'.repeat(64),
    renderer_build_id: 'renderer',
    status: 'preview_only',
    semantic_status: 'not_reviewed',
    unit_status: 'not_reviewed',
    sample_status: 'sample_compatible',
    slot: {
      slot_id: 'count',
      rows: [{ value: { kind: 'integer', value: '18446744073709551616' } }],
    },
  }
  assert.equal(validators.zSqlTrialPreview.safeParse(value).success, true)
  assert.equal(
    validators.zSqlTrialPreview.safeParse({ ...value, status: 'published' }).success,
    false,
  )
  assert.equal(
    validators.zSqlTrialPreview.safeParse({
      ...value,
      slot: { slot_id: 'count', rows: [{ value: { kind: 'integer', value: 1 } }] },
    }).success,
    false,
  )
  assert.equal(Object.keys(validators.zSqlTrialPreview.shape).includes('visual_json'), false)
})

test('multi-slot preview selects bounded stored trial IDs, never SQL or data rows', () => {
  const path = '/enterprise/api/v1/dashboards/{dashboard_id}/sql-proposals/{draft_id}/preview'
  assert.deepEqual(Object.keys(document.paths[path]), ['post'])
  assert.ok(contract.dashboards.byDashboardId.sqlProposals.byDraftId.preview.post)
  assert.deepEqual(Object.keys(validators.zSqlDraftPreviewSelection.shape), ['trial_ids'])
  assert.equal(
    validators.zSqlDraftPreviewSelection.safeParse({ trial_ids: ['trial-1', 'trial-2'] }).success,
    true,
  )
  assert.equal(validators.zSqlDraftPreviewSelection.safeParse({ trial_ids: [] }).success, false)
  assert.equal(
    validators.zSqlDraftPreviewSelection.safeParse({
      trial_ids: Array.from({ length: 101 }, (_, i) => `trial-${i}`),
    }).success,
    false,
  )
  assert.equal(
    document.components.schemas.SqlDraftPreview.properties.snapshot_status.const,
    'independent_trials',
  )
})

test('profile discovery exposes labels only and history remains server-paginated', () => {
  assert.deepEqual(Object.keys(validators.zPluginProfileChoice.shape).sort(), [
    'config_ref',
    'config_revision',
    'display_name',
  ])
  assert.equal(
    validators.zPluginProfileChoice.safeParse({
      config_ref: 'default',
      config_revision: 1,
      display_name: 'Factory',
    }).success,
    true,
  )
  assert.equal(
    validators.zPluginProfileChoice.safeParse({
      config_ref: 'default',
      config_revision: true,
      display_name: 'Factory',
    }).success,
    false,
  )
  assert.ok(contract.workflowSetups.bySetupId.provisioning.get)
  assert.ok(contract.workflowSetups.bySetupId.provisioningProfiles.get)
  const history = document.paths['/enterprise/api/v1/workflow-setups/{setup_id}/provisioning'].get
  assert.equal(
    history.parameters.find((parameter) => parameter.name === 'limit').schema.maximum,
    100,
  )
  assert.equal(
    history.parameters.find((parameter) => parameter.name === 'offset').schema.minimum,
    0,
  )
})

test('workflow setup command selects a source instead of exposing native IDs or credentials', () => {
  assert.deepEqual(Object.keys(validators.zSetupRequest.shape).sort(), [
    'expected_binding_revision',
    'expected_source_revision',
    'source_id',
  ])
  assert.equal(
    validators.zSetupRequest.safeParse({ source_id: 'source-1', expected_source_revision: 1 })
      .success,
    true,
  )
  assert.equal(
    validators.zSetupRequest.safeParse({ source_id: 'source-1', expected_source_revision: true })
      .success,
    false,
  )
  for (const name of [
    'import_nonce',
    'cookie_header',
    'authorization',
    'secret_ref',
    'api_key',
    'request_hash',
  ])
    assert.equal(Object.keys(validators.zSetupView.shape).includes(name), false)
  const commands = contract.devices.byDeviceId.bindings.byScenario.workflowSetups
  assert.ok(commands.get && commands.post && contract.workflowSetups.bySetupId.get)
  const creation =
    document.paths['/enterprise/api/v1/devices/{device_id}/bindings/{scenario}/workflow-setups']
      .post
  assert.ok(
    creation.parameters.some(
      (parameter) => parameter.name === 'idempotency-key' && parameter.required,
    ),
  )
  assert.ok(
    creation.parameters.some((parameter) => parameter.name === 'Origin' && parameter.required),
  )
})

test('workbench branches expose scoped creation and reads without client identity fields', () => {
  const branches = contract.workbench.apps.byInstalledAppId.branches
  assert.ok(branches.post)
  assert.ok(branches.byBranchId.get)
  assert.deepEqual(Object.keys(validators.zRootBranchRequest.shape), ['branch_id'])
  const request = document.components.schemas.RootBranchRequest
  assert.equal(request.additionalProperties, false)
  assert.deepEqual(request.required, ['branch_id'])
  const context = document.components.schemas.BranchContext
  assert.ok(context.properties.inflight_client_message_id)
  assert.ok(context.properties.head_message_id)
  assert.ok(context.properties.revision)
})

test('workbench branch directory exposes a bounded read without caller identity or mutation body', () => {
  assert.ok(contract.workbench.apps.byInstalledAppId.branches.get)
  const operation =
    document.paths['/enterprise/api/v1/workbench/apps/{installed_app_id}/branches'].get
  assert.equal(operation.requestBody, undefined)
  const query = operation.parameters.filter((item) => item.in === 'query')
  assert.deepEqual(query.map((item) => item.name).sort(), ['after', 'limit'])
  const limit = query.find((item) => item.name === 'limit').schema
  assert.equal(limit.minimum, 1)
  assert.equal(limit.maximum, 100)
  assert.equal(limit.default, 50)
  assert.deepEqual(Object.keys(validators.zBranchPage.shape).sort(), ['items', 'next_after'])
})

test('workbench sends retain an explicit SSE response and bounded client request schema', () => {
  const route = '/enterprise/api/v1/workbench/apps/{installed_app_id}/branches/{branch_id}/messages'
  const operation = document.paths[route].post
  assert.ok(contract.workbench.apps.byInstalledAppId.branches.byBranchId.messages.post)
  assert.deepEqual(Object.keys(operation.responses['200'].content), ['text/event-stream'])
  assert.deepEqual(Object.keys(validators.zChatSendRequest.shape).sort(), [
    'client_message_id',
    'payload',
  ])
  assert.equal(document.components.schemas.ChatSendRequest.additionalProperties, false)
  for (const status of ['401', '403', '409', '422', '503']) assert.ok(operation.responses[status])
})

test('workbench ledger reads expose the same bounded public state as SSE without replay commands', () => {
  const messages = contract.workbench.apps.byInstalledAppId.branches.byBranchId.messages
  assert.ok(messages.byClientMessageId.get)
  const route =
    document.paths[
      '/enterprise/api/v1/workbench/apps/{installed_app_id}/branches/{branch_id}/messages/{client_message_id}'
    ]
  assert.equal(route.post, undefined)
  assert.equal(route.get.requestBody, undefined)
  assert.deepEqual(Object.keys(validators.zWorkbenchSendState.shape).sort(), [
    'client_message_id',
    'conversation_id',
    'message_id',
    'outcome',
    'revision',
    'status',
    'task_id',
  ])
  const value = {
    client_message_id: '00000000-0000-4000-8000-000000000002',
    status: 'accepted',
    revision: 3,
    conversation_id: '00000000-0000-4000-8000-000000000003',
    message_id: '00000000-0000-4000-8000-000000000004',
    task_id: 'task',
    outcome: null,
  }
  assert.equal(validators.zWorkbenchSendState.parse(value).outcome, null)
  assert.equal(
    validators.zWorkbenchSendState.safeParse({ ...value, status: 'completed' }).success,
    false,
  )
})

test('Office read projection retains exact numeric tags and string revisions in JavaScript', () => {
  assert.ok(contract.office.files.byFileId.get)
  const value = JSON.parse(
    JSON.stringify({
      file_id: '00000000-0000-4000-8000-000000000001',
      revision: '9007199254740993',
      kind: 'document',
      template_id: 'document-default',
      template_revision: '9007199254740993',
      source_snapshot_ids: [],
      units: [
        {
          unit_id: '00000000-0000-4000-8000-000000000002',
          kind: 'table',
          content: [
            {
              table_id: 'values',
              headers: ['code', 'large', 'exact', 'missing'],
              rows: [
                [
                  '0007',
                  { integer: '9007199254740993' },
                  { decimal: '1.2300000000000000001' },
                  null,
                ],
              ],
            },
          ],
        },
      ],
    }),
  )
  assert.deepEqual(validators.zOfficeFileView.parse(value), value)
  assert.equal(
    validators.zOfficeIntegerView.safeParse({ integer: 9007199254740992 }).success,
    false,
  )
  assert.equal(validators.zOfficeFileView.safeParse({ ...value, revision: 1 }).success, false)
})

test('Office edits carry retry identity and exact tagged values without binding overrides', () => {
  assert.ok(contract.office.files.byFileId.edits.post)
  const value = {
    request_id: '00000000-0000-4000-8000-000000000001',
    expected_revision: '1',
    replacements: [
      {
        unit_id: '00000000-0000-4000-8000-000000000002',
        content: [
          { table_id: 'values', headers: ['exact'], rows: [[{ integer: '9007199254740993' }]] },
        ],
      },
    ],
  }
  assert.deepEqual(validators.zOfficeEditRequest.parse(value), value)
  assert.equal(
    validators.zOfficeEditRequest.safeParse({ ...value, expected_revision: 1 }).success,
    false,
  )
  assert.deepEqual(validators.zOfficeEditRequest.parse({ ...value, template_id: 'other' }), value)
  assert.equal(
    validators.zOfficeTableInput.safeParse({
      table_id: 'values',
      headers: ['value'],
      rows: [[1.5]],
    }).success,
    false,
  )
})

test('Office directory exposes bounded metadata and supports empty continuing pages', () => {
  assert.ok(contract.office.files.get)
  assert.deepEqual(validators.zOfficeDirectoryPage.parse({ items: [], next_offset: 50 }), {
    items: [],
    next_offset: 50,
  })
  assert.deepEqual(Object.keys(validators.zOfficeFileSummary.shape).sort(), [
    'file_id',
    'kind',
    'revision',
    'template_id',
    'template_revision',
  ])
  const route = document.paths['/enterprise/api/v1/office/files'].get
  assert.equal(route.requestBody, undefined)
  const offset = route.parameters.find((parameter) => parameter.name === 'offset')
  assert.equal(offset.schema.minimum, 0)
  assert.equal(offset.schema.maximum, 2147483647)
})
