import test from 'node:test';
import assert from 'node:assert/strict';
import { workflowEdgeState } from '../../apps/analytics/src/utils/workflowEdgeState.js';

test('classification edges only turn green for the selected category, including fallback', () => {
  const destination = { status: 'completed' };
  const source = { status: 'completed', output: { category: 'equipment', matched: true } };
  assert.equal(workflowEdgeState('case:equipment', source, destination).status, 'completed');
  assert.equal(workflowEdgeState('case:finance', source, destination).status, 'idle');
  source.output = { category: 'other', matched: false };
  assert.equal(workflowEdgeState('case:other', source, destination).status, 'completed');
  assert.equal(workflowEdgeState('case:equipment', source, destination).status, 'idle');
  source.status = 'failed';
  assert.equal(workflowEdgeState('case:other', source, destination).status, 'idle');
});
