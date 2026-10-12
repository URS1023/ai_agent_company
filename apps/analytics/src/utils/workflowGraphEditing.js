/**
 * workflowGraphEditing —— 在连线上/节点前后插入节点时的断线重连计算（纯函数）。
 *
 * 位置：WorkflowEditor / WorkflowSubgraphEditor 打开节点浮层并确认新增时调用。
 * 关键约束：
 * - 端口归属规则：分支端口（true/false/case:<id>/error）属于原边的源节点，
 *   新插入的普通节点一律走 `out`；因此条件分支/分类器与终止节点不能"被自动串接"——
 *   它们没有通用 out 出边，必须由用户手动连接具体分支，否则会生成后端不接受的图；
 * - 返回 { edges, error }：error 非空表示拒绝插入（调用方只提示、不改图），不允许"尽力而为"地改图；
 * - 本模块不产生副作用，节点 id 由调用方通过 makeEdgeId 注入，便于测试与前端临时 id 策略统一。
 */

/**
 * Pure edge construction for palette insertion in the workflow canvas.
 * Branch ports belong to the original source edge; every newly inserted
 * ordinary node emits through `out`. Branching and terminal nodes cannot be
 * silently chained through an unavailable `out` port.
 */
const TERMINAL_KINDS = new Set(['end', 'reply'])
const BRANCH_KINDS = new Set(['condition', 'classifier'])

function opposite(side) {
  return side === 'left' ? 'right' : side === 'right' ? 'left' : side === 'top' ? 'bottom' : 'top'
}

function nonLinear(kind) {
  return TERMINAL_KINDS.has(kind) || BRANCH_KINDS.has(kind)
}

/**
 * 校验插入链的形状是否可自动连线。
 * @returns 中文错误文案；空串表示可以插入
 */
export function insertionShapeError({ source, target, nodes }) {
  if (!nodes?.length) return '没有选择要添加的节点'
  if (source && TERMINAL_KINDS.has(source.kind)) return '结束或回复节点不能继续连接后续节点'
  const last = nodes[nodes.length - 1]
  for (let index = 0; index < nodes.length - 1; index += 1) {
    if (TERMINAL_KINDS.has(nodes[index].kind)) return '结束或回复节点必须是插入链的最后一个节点'
    if (BRANCH_KINDS.has(nodes[index].kind)) return '条件分支或分类器不能自动串接普通出边，请先添加分支后续节点'
  }
  if (target && nonLinear(last.kind)) {
    return last.kind === 'condition' || last.kind === 'classifier'
      ? '条件分支或分类器没有通用 out 出边，不能自动连接原目标；请先保存后手动连接具体分支'
      : '结束或回复节点不能连接原目标；请把终止节点放在插入链末尾'
  }
  return ''
}

/**
 * 计算插入节点后的边集合（在已有连线上插入时会断开原边并重连到新链两端）。
 * @returns {{ edges: Array, error: string }} error 非空时 edges 原样返回（调用方不得落图）
 */
export function buildInsertionEdges(graph, request, nodes, makeEdgeId) {
  const edge = request?.edgeId ? graph.edges.find(item => item.id === request.edgeId) : undefined
  if (request?.edgeId && !edge) return { edges: graph.edges, error: '插入位置的连线已经不存在，请重新选择。' }
  const source = edge
    ? graph.nodes.find(item => item.id === edge.source)
    : request?.direction === 'after' ? graph.nodes.find(item => item.id === request.nodeId) : undefined
  const target = edge
    ? graph.nodes.find(item => item.id === edge.target)
    : request?.direction === 'before' ? graph.nodes.find(item => item.id === request.nodeId) : undefined
  if ((edge && (!source || !target)) || (request?.direction === 'after' && !source) || (request?.direction === 'before' && !target)) {
    return { edges: graph.edges, error: '插入位置的节点已经不存在，请重新选择。' }
  }
  const error = insertionShapeError({ source, target, nodes })
  if (error) return { edges: graph.edges, error }

  const chainSide = request?.direction === 'before' ? opposite(request.side || 'left') : request?.side || edge?.sourceSide || 'right'
  const chainEdges = nodes.slice(0, -1).map((node, index) => ({
    id: makeEdgeId(), source: node.id, target: nodes[index + 1].id, port: 'out',
    sourceSide: chainSide, targetSide: opposite(chainSide),
  }))
  let edges = [...graph.edges, ...chainEdges]
  if (edge && source && target) {
    const sourceSide = edge.sourceSide || 'right'
    const targetSide = edge.targetSide || 'left'
    edges = [
      ...graph.edges.filter(item => item.id !== edge.id),
      { ...edge, target: nodes[0].id, targetSide: opposite(sourceSide) },
      ...chainEdges,
      { id: makeEdgeId(), source: nodes[nodes.length - 1].id, target: edge.target, port: 'out', sourceSide: opposite(targetSide), targetSide },
    ]
  } else if (request?.direction === 'after' && source && request.side) {
    edges.push({ id: makeEdgeId(), source: source.id, target: nodes[0].id, port: request.port || 'out', sourceSide: request.side, targetSide: opposite(request.side) })
  } else if (request?.direction === 'before' && target && request.side) {
    edges.push({ id: makeEdgeId(), source: nodes[nodes.length - 1].id, target: target.id, port: 'out', sourceSide: opposite(request.side), targetSide: request.side })
  }
  return { edges, error: '' }
}
