/**
 * workflowEdgeState —— 工作流连线的"执行态"推导与配色映射。
 *
 * 位置：WorkflowCanvas 组装连线数据、DifyWorkflowEdge 渲染颜色与分支标签时使用。
 * 输入：端口（out / true / false / error / case:<id>）、源节点运行状态、目标节点运行状态。
 * 关键约束：
 * - 状态判定必须镜像后端引擎的 active_parent_ids 语义：只有"目标节点已完成"不足以判定连线被走过，
 *   还要看源节点是否真的走了该端口（例如 condition 走了 false 分支时 true 分支不能显示为已执行）；
 * - 颜色映射 workflowEdgeColors 是连线颜色的唯一来源，新增状态必须同时补齐键与样式；
 * - count 仅在行分支（rowBranches）模式下有意义，非 true/false 端口必须返回 null，
 *   前端据此决定是否显示"命中 N 条"，不要把 null 当 0。
 */

/** Mirror engine.active_parent_ids: a completed destination alone is not a taken edge. */
export function workflowEdgeState(port, source, target) {
  const value = source?.output;
  const rowBranch = value?.rowBranches === true;
  const branchRows = rowBranch ? value[port + 'Rows']?.data : undefined;
  const count = rowBranch && ['true', 'false'].includes(port) && Array.isArray(branchRows)
    ? branchRows.length : null;
  let active = false;
  if (source?.status === 'completed') {
    active = port === 'out' || (port.startsWith('case:') && value?.category === port.slice(5)) || (['true', 'false'].includes(port) && (rowBranch
      ? (count !== null && count > 0) || (port === 'false' && value.totalCount === 0)
      : value?.matched === (port === 'true')));
  }
  if (source?.handled === true) {
    active ||= (port === 'error' && source.errorStrategy === 'fail-branch')
      || (port === 'out' && source.errorStrategy === 'default-value');
  }
  const status = active && ['completed', 'running', 'waiting', 'failed'].includes(target?.status)
    ? target.status : 'idle';
  return { status, count };
}

/**
 * 连线颜色映射（执行态 → 十六进制色值），是画布连线配色的唯一来源。
 * 键必须与 workflowEdgeState 返回的 status 取值一一对应。
 */
export const workflowEdgeColors = {
  idle: '#aeb8c7', completed: '#17b26a', running: '#085afc', waiting: '#f79009', failed: '#f04438',
};

/**
 * 按绘制层级重排连线：SVG 同级元素按数组顺序绘制，
 * 未执行(0) → 已执行(1) → 选中(2)，保证激活的连线与选中态压在其他连线之上。
 * @param {Array} edges Vue Flow 的 edge 数组
 * @returns {Array} 新数组（不修改入参顺序）
 */

/** SVG siblings paint in array order. Keep inactive paths beneath taken paths. */
export function orderWorkflowEdges(edges) {
  const layer = edge => edge.selected ? 2 : edge.data?.execution?.status && edge.data.execution.status !== 'idle' ? 1 : 0;
  return [...edges].sort((a, b) => layer(a) - layer(b));
}
