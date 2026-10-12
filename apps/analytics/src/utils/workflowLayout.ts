/**
 * workflowLayout —— 工作流画布的自动排布与重叠检测（纯函数，只改坐标不改结构）。
 *
 * 位置：WorkflowEditor/WorkflowSubgraphEditor 的"自动排布"、打开图时的可读性检查。
 * 依赖：`api/workflows` 的 FlowGraph 类型。
 * 关键约束：
 * - 尺寸常量（NODE_WIDTH/NODE_HEIGHT/COLUMN_STEP/ROW_STEP）必须与 DifyWorkflowNode 的实际卡片尺寸匹配，
 *   否则排布后仍会互相遮挡；
 * - 输入图可能含环或悬挂边：遍历必须做环检测并忽略两端节点不存在的边，不能假设是有向无环图；
 * - 返回值是新图，不得就地修改入参（调用方通常直接把它写回草稿）。
 */
import type { FlowGraph } from '../api/workflows'

const NODE_WIDTH = 240
const NODE_HEIGHT = 108
const COLUMN_STEP = 320
const ROW_STEP = 190

/**
 * 检测是否存在互相遮挡的节点（用于提示用户执行自动排布）。
 * 判定用卡片宽高 + 少量容差，任意一对节点重叠即返回 true。
 */

/** Detect layouts where cards cover one another and leave no visible wire. */
export function hasOverlappingWorkflowNodes(graph: FlowGraph) {
  for (let index = 0; index < graph.nodes.length; index += 1) {
    const left = graph.nodes[index]!
    for (let other = index + 1; other < graph.nodes.length; other += 1) {
      const right = graph.nodes[other]!
      if (Math.abs(left.position.x - right.position.x) < NODE_WIDTH + 8
        && Math.abs(left.position.y - right.position.y) < NODE_HEIGHT + 4) return true
    }
  }
  return false
}

/**
 * 按依赖层级重排节点坐标（同 Dify 的 240px 卡片网格）。
 * @param graph 工作流图
 * @returns 新的 FlowGraph（节点顺序与边不变，只改 position）
 */

/** Lay a graph out by dependency level with the same 240px cards used by Dify. */
export function arrangeWorkflowGraph(graph: FlowGraph): FlowGraph {
  const nodesById = new Map(graph.nodes.map(node => [node.id, node]))
  const incoming = new Map(graph.nodes.map(node => [node.id, 0]))
  const outgoing = new Map(graph.nodes.map(node => [node.id, [] as string[]]))
  for (const edge of graph.edges) {
    if (!nodesById.has(edge.source) || !nodesById.has(edge.target)) continue
    incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1)
    outgoing.get(edge.source)!.push(edge.target)
  }
  const rank = (id: string) => {
    const node = nodesById.get(id)!
    return [node.position.x, node.position.y, graph.nodes.indexOf(node)]
  }
  const queue = graph.nodes.filter(node => !incoming.get(node.id)).map(node => node.id)
    .sort((left, right) => {
      const a = rank(left), b = rank(right)
      return a[0]! - b[0]! || a[1]! - b[1]! || a[2]! - b[2]!
    })
  const level = new Map(graph.nodes.map(node => [node.id, 0]))
  const visited = new Set<string>()
  for (let index = 0; index < queue.length; index += 1) {
    const source = queue[index]!
    visited.add(source)
    for (const target of outgoing.get(source) || []) {
      level.set(target, Math.max(level.get(target) || 0, (level.get(source) || 0) + 1))
      incoming.set(target, (incoming.get(target) || 0) - 1)
      if (!incoming.get(target)) queue.push(target)
    }
  }
  // Invalid drafts can contain a cycle. Keep every card reachable in the
  // editor so the user can remove the bad edge instead of stacking cards.
  let fallbackLevel = Math.max(0, ...level.values()) + 1
  for (const node of graph.nodes) {
    if (!visited.has(node.id)) level.set(node.id, fallbackLevel++)
  }
  const rows = new Map<number, number>()
  const nodes = graph.nodes.map(node => {
    const column = level.get(node.id) || 0
    const row = rows.get(column) || 0
    rows.set(column, row + 1)
    return { ...node, position: { x: 80 + column * COLUMN_STEP, y: 120 + row * ROW_STEP } }
  })
  const edges = graph.edges.map(edge => ({ ...edge, sourceSide: 'right' as const, targetSide: 'left' as const }))
  return { nodes, edges }
}

export function ensureReadableWorkflowLayout(graph: FlowGraph) {
  return hasOverlappingWorkflowNodes(graph) ? arrangeWorkflowGraph(graph) : graph
}
