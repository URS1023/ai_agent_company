import assert from 'node:assert/strict'
import test from 'node:test'

import { buildInsertionEdges, insertionShapeError } from '../../apps/analytics/src/utils/workflowGraphEditing.js'

function edge(id, source, target, port = 'out') {
  return { id, source, target, port, sourceSide: 'right', targetSide: 'left' }
}

function baseGraph() {
  return {
    nodes: [
      { id: 'source', kind: 'classifier' },
      { id: 'target', kind: 'end' },
    ],
    edges: [edge('branch-pass', 'source', 'target', 'case:pass')],
  }
}

test('inserting an ordinary node preserves a branch port and uses out for the new edge', () => {
  const result = buildInsertionEdges(baseGraph(), { edgeId: 'branch-pass' }, [{ id: 'new', kind: 'text' }], (() => {
    let index = 0
    return () => `new-edge-${++index}`
  })())

  assert.equal(result.error, '')
  assert.deepEqual(result.edges, [
    edge('branch-pass', 'source', 'new', 'case:pass'),
    edge('new-edge-1', 'new', 'target', 'out'),
  ])
})

test('branch and terminal nodes are never silently chained through out', () => {
  const graph = {
    nodes: [{ id: 'source', kind: 'text' }, { id: 'target', kind: 'end' }],
    edges: [edge('main', 'source', 'target')],
  }
  const makeId = (() => { let index = 0; return () => `edge-${++index}` })()

  assert.match(
    buildInsertionEdges(graph, { edgeId: 'main' }, [{ id: 'branch', kind: 'condition' }], makeId).error,
    /条件分支/,
  )
  assert.match(
    buildInsertionEdges(graph, { edgeId: 'main' }, [{ id: 'terminal', kind: 'end' }], makeId).error,
    /结束或回复/,
  )
  assert.match(
    insertionShapeError({ source: graph.nodes[0], target: graph.nodes[1], nodes: [{ id: 'text', kind: 'text' }, { id: 'branch', kind: 'classifier' }] }),
    /条件分支/,
  )
})

test('adding after a branch keeps the selected case port on the source edge', () => {
  const graph = baseGraph()
  const result = buildInsertionEdges(graph, {
    direction: 'after', nodeId: 'source', side: 'right', port: 'case:pass',
  }, [{ id: 'new', kind: 'text' }], () => 'after-edge')

  assert.equal(result.error, '')
  assert.deepEqual(result.edges.at(-1), edge('after-edge', 'source', 'new', 'case:pass'))
})

test('a terminal source is rejected before adding a successor', () => {
  const graph = {
    nodes: [{ id: 'terminal', kind: 'reply' }],
    edges: [],
  }
  const result = buildInsertionEdges(graph, { direction: 'after', nodeId: 'terminal', side: 'right' }, [{ id: 'new', kind: 'text' }], () => 'unused')
  assert.match(result.error, /结束或回复节点不能继续连接/)
})
