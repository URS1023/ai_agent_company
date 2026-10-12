/*
 * 中文说明（工作流连线状态的纯前端单元测试）：
 * 验证行为：orderWorkflowEdges 与 workflowEdgeState 依据运行状态（completed 等）推导连线状态与顺序。
 * 运行方式：node --test tests/ui/workflow_edge_state.mjs，依赖
 *   apps/analytics/src/utils/workflowEdgeState.js。
 * 边界：纯函数测试，不连后端。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { orderWorkflowEdges, workflowEdgeState } from '../../apps/analytics/src/utils/workflowEdgeState.js';

const completed = { status: 'completed' };
const rows = (yes, no) => ({ ...completed, output: {
  rowBranches: true, totalCount: yes + no, matched: yes > 0,
  trueRows: { data: Array.from({ length: yes }, (_, id) => ({ id })) },
  falseRows: { data: Array.from({ length: no }, (_, id) => ({ id })) },
} });

test('two condition edges into the same completed merge highlight only the selected port', () => {
  const source = { ...completed, output: { matched: true } };
  assert.equal(workflowEdgeState('true', source, completed).status, 'completed');
  assert.equal(workflowEdgeState('false', source, completed).status, 'idle');
  assert.equal(workflowEdgeState('false', { ...completed, output: { matched: false } }, completed).status, 'completed');
});

test('batch branches use actual per-port records, not the aggregate matched flag', () => {
  assert.deepEqual(workflowEdgeState('true', rows(12, 0), completed), { status: 'completed', count: 12 });
  assert.deepEqual(workflowEdgeState('false', rows(12, 0), completed), { status: 'idle', count: 0 });
  assert.deepEqual(workflowEdgeState('true', rows(2, 3), completed), { status: 'completed', count: 2 });
  assert.deepEqual(workflowEdgeState('false', rows(2, 3), completed), { status: 'completed', count: 3 });
  assert.equal(workflowEdgeState('true', rows(0, 0), completed).status, 'idle');
  assert.equal(workflowEdgeState('false', rows(0, 0), completed).status, 'completed');
});

test('unexecuted, pending and missing states cannot color incoming or outgoing edges', () => {
  for (const status of ['pending', 'running', 'waiting', 'skipped', 'cancelled', 'failed']) {
    assert.equal(workflowEdgeState('out', { status }, completed).status, 'idle', status);
  }
  for (const status of ['pending', 'skipped', 'cancelled']) {
    assert.equal(workflowEdgeState('out', completed, { status }).status, 'idle', status);
  }
  assert.equal(workflowEdgeState('out', undefined, undefined).status, 'idle');
  assert.equal(workflowEdgeState('true', completed, completed).status, 'idle');
});

test('only the taken incoming edge reflects downstream running, waiting or failure', () => {
  for (const status of ['running', 'waiting', 'failed']) {
    assert.equal(workflowEdgeState('true', rows(1, 0), { status }).status, status);
    assert.equal(workflowEdgeState('false', rows(1, 0), { status }).status, 'idle');
  }
});

test('error handling activates its configured fallback, never both outputs', () => {
  const failBranch = { status: 'failed', handled: true, errorStrategy: 'fail-branch' };
  assert.equal(workflowEdgeState('error', failBranch, completed).status, 'completed');
  assert.equal(workflowEdgeState('out', failBranch, completed).status, 'idle');
  assert.equal(workflowEdgeState('error', completed, completed).status, 'idle');
  const defaultValue = { ...failBranch, errorStrategy: 'default-value' };
  assert.equal(workflowEdgeState('error', defaultValue, completed).status, 'idle');
  assert.equal(workflowEdgeState('out', defaultValue, completed).status, 'completed');
});

test('all completed output edges paint above skipped outputs at a shared merge', () => {
  const edges = ['completed', 'idle', 'completed', 'idle'].map((status, i) => ({
    id: `output-${i}`, data: { execution: { status } }, selected: false,
  }));
  const ordered = orderWorkflowEdges(edges);
  assert.deepEqual(ordered.map(e => e.id), ['output-1', 'output-3', 'output-0', 'output-2']);
  assert.deepEqual(edges.map(e => e.id), ['output-0', 'output-1', 'output-2', 'output-3'], 'display order must not mutate the graph');
  const selected = orderWorkflowEdges(ordered.map(e => ({ ...e, selected: e.id === 'output-1' })));
  assert.equal(selected.at(-1).id, 'output-1', 'a deliberately selected edge remains inspectable');
  assert.deepEqual(orderWorkflowEdges(selected.map(e => ({ ...e, selected: false }))).slice(-2).map(e => e.id), ['output-0', 'output-2']);
});
