/** 纯协议/预算测试；不加载Playwright、不打开浏览器或网络。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { blockedRequestMetadata, CookieRefreshGate, fixedDraft, fixedRunBody, RequestBudget, runBodyHash, workflowSse } from './dify-wsl.mjs';

const flow = { language: 'python3', marker: 'PY_NATIVE_OK_mock', code: 'def main():\n return {"value":42}\n',
  edited_code: 'def main():\n return {"value":42}\n\n# NATIVE_EDITOR_VERIFIED',
  graph: { nodes: [{}, {}, { data: { outputs: [{ value_selector: ['code', 'result'], value_type: 'string', variable: 'result' }] } }] } };

function draft() {
  return { graph: { nodes: [
    { id: 'start', data: { type: 'start', variables: [] } },
    { id: 'code', data: { type: 'code', variables: [], code_language: flow.language, code: flow.edited_code } },
    { id: 'end', data: { type: 'end', outputs: flow.graph.nodes[2].data.outputs } },
  ], edges: [{ source: 'start', target: 'code' }, { source: 'code', target: 'end' }] },
  features: { file_upload: { enabled: false } } };
}

test('仅空输入/无文件，持久化摘要与Python规范JSON一致', () => {
  assert.ok(fixedRunBody({ inputs: {}, files: [] }));
  assert.ok(fixedRunBody({ inputs: {} }));
  assert.equal(fixedRunBody({ inputs: { sql: 'select' } }), false);
  assert.equal(fixedRunBody({ inputs: {}, files: [{ url: 'http://private' }] }), false);
  assert.equal(fixedRunBody({ inputs: {}, query: 'model' }), false);
  assert.equal(runBodyHash(), createHash('sha256').update('{"files":[],"inputs":{}}').digest('hex'));
});

test('原生自动保存仅允许原代码/编辑注释，拒绝新增模型和外部输入', () => {
  assert.ok(fixedDraft(draft(), flow));
  const unsafe = draft();
  unsafe.graph.nodes[1].data.code = 'import requests';
  assert.equal(fixedDraft(unsafe, flow), false);
  const model = draft();
  model.graph.nodes.push({ id: 'llm', data: { type: 'llm' } });
  assert.equal(fixedDraft(model, flow), false);
  const upload = draft();
  upload.features.file_upload.enabled = true;
  assert.equal(fixedDraft(upload, flow), false);
});

function sse(status = 'succeeded', tokens = 0, value = 42) {
  return [
    { event: 'workflow_started', task_id: 'task-1', workflow_run_id: 'run-1', data: { id: 'run-1' } },
    { event: 'node_finished', data: { node_id: 'code', node_type: 'code', status: 'succeeded' } },
    { event: 'workflow_finished', workflow_run_id: 'run-1', data: { id: 'run-1', status,
      total_tokens: tokens, outputs: { result: flow.marker, value } } },
  ].map(event => 'data: ' + JSON.stringify(event)).join('\n\n') + '\n\n';
}

test('必须原生SSE完整终态/确切结果/代码节点，不把HTTP200算通过', () => {
  assert.equal(workflowSse(sse(), flow).native_code_node, true);
  assert.throws(() => workflowSse(sse('failed'), flow), /outputs_not_verified/);
  assert.throws(() => workflowSse(sse('succeeded', 1), flow), /outputs_not_verified/);
  assert.throws(() => workflowSse(sse('succeeded', 0, 41), flow), /outputs_not_verified/);
  assert.throws(() => workflowSse('data: null\n\n', flow));
  assert.throws(() => workflowSse('data: {"event":"workflow_started"}\n\n', flow), /lifecycle_incomplete/);
});

test('共享业务预算不重置，静态/业务独立有上限且只本机origin', () => {
  const budget = new RequestBudget({ origin: 'http://127.0.0.1:13001', max_business: 120, max_static: 400,
    business_used: 119, deadline_unix_ms: Date.now() + 5000 });
  assert.equal(budget.take('https://external.invalid/api', 'fetch'), 'external');
  assert.equal(budget.take('http://127.0.0.1:13001/_next/static/code.js', 'script'), 'static');
  assert.equal(budget.take('http://127.0.0.1:13001/console/api/profile', 'fetch'), 'business');
  assert.throws(() => budget.take('http://127.0.0.1:13001/console/api/profile', 'fetch'), /budget_exceeded/);
  budget.stopAt = performance.now() - 1;
  assert.throws(() => budget.take('http://127.0.0.1:13001/console/api/profile', 'fetch'), /total_deadline/);
});

test('拦截诊断仅有method/path，不泄露query和凭据', () => {
  const metadata = blockedRequestMetadata('POST', 'http://user:password@127.0.0.1:13001/console/api/refresh-token?token=never-log-me');
  assert.deepEqual(metadata, { method: 'POST', path: '/console/api/refresh-token' });
  assert.equal(JSON.stringify(metadata).includes('password'), false);
  assert.equal(JSON.stringify(metadata).includes('never-log-me'), false);
  assert.deepEqual(blockedRequestMetadata('POST\nInjected', 'not-a-url-with-private-text'), { method: 'UNKNOWN', path: '/invalid-url' });
});

test('原生refresh只允许同源准确路径空body，两个浏览器内请求后拒绝循环', () => {
  const url = 'http://127.0.0.1:13001/console/api/refresh-token';
  const gate = new CookieRefreshGate();
  assert.equal(gate.allow('POST', url, null), true);
  assert.equal(gate.allow('POST', url, '{}'), true);
  assert.equal(gate.requests, 2);
  assert.throws(() => gate.allow('POST', url, ''), /cookie_refresh_budget_exceeded/);
});

test('refresh不扩大其他写接口、外部URL或带body-token的权限', () => {
  const url = 'http://127.0.0.1:13001/console/api/refresh-token';
  for (const [method, target, body] of [
    ['POST', 'http://127.0.0.1:18080/console/api/refresh-token', null],
    ['POST', 'https://external.invalid/console/api/refresh-token', null],
    ['POST', url + '?token=private', null], ['POST', url + '/other', null],
    ['POST', 'http://127.0.0.1:13001/signin', null], ['POST', url, '{"refresh_token":"private"}'],
    ['POST', url, '[]'], ['POST', url, 'null'], ['POST', url, '""'], ['POST', url, 'invalid json'],
    ['PUT', url, null], ['GET', url, null],
  ]) {
    const gate = new CookieRefreshGate();
    assert.equal(gate.allow(method, target, body), false);
    assert.equal(gate.requests, 0);
  }
});
