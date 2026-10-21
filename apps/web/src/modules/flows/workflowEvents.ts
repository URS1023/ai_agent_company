import type { WorkflowEvent } from "./useWorkflowEvents";
const nodeLabels: Record<string, string> = {
  start: "开始",
  end: "结束",
  answer: "输出",
  "if-else": "条件分支",
  "variable-aggregator": "变量聚合",
  "variable-assigner": "变量聚合",
  assigner: "变量赋值",
  "list-operator": "列表处理",
  iteration: "迭代",
  "iteration-start": "迭代开始",
  loop: "循环",
  "loop-start": "循环开始",
  "loop-end": "循环结束",
};
export function eventLabel(type: string) {
  if (/Failed|Aborted|Exception/.test(type)) return "执行失败";
  if (/Succeeded|Completed|Finished/.test(type)) return "执行完成";
  if (/Started/.test(type)) return "开始执行";
  if (/Paused|HumanInput/.test(type)) return "等待处理";
  if (/Iteration|Loop/.test(type)) return "循环进度";
  return "进度更新";
}
/** 同一节点在循环内可执行多次，用执行ID计次，绝不把节点ID当一次执行ID。 */
export function eventRows(
  events: WorkflowEvent[],
  nodes: { id: string; data: Record<string, any> }[],
) {
  const executions = new Map<string, string[]>();
  return events.map((event) => {
    const data = event.data ?? {},
      nodeId = typeof data.node_id === "string" ? data.node_id : "";
    const executionId = nodeId
      ? String(data.id ?? data.node_execution_id ?? "")
      : "";
    let attempt: number | undefined;
    if (nodeId && executionId) {
      const known = executions.get(nodeId) ?? [];
      if (!known.includes(executionId)) known.push(executionId);
      executions.set(nodeId, known);
      attempt = known.indexOf(executionId) + 1;
    }
    const node = nodes.find((item) => item.id === nodeId);
    return {
      ...event,
      executionId,
      attempt,
      label: eventLabel(event.event_type),
      nodeName:
        data.node_title ||
        node?.data.title ||
        nodeLabels[node?.data.type] ||
        (nodeId ? "节点" : "流程"),
      facts: Object.fromEntries(
        ["inputs", "outputs", "error", "error_message", "process_data"]
          .filter(
            (key) =>
              data[key] !== undefined &&
              !(
                data[key] &&
                typeof data[key] === "object" &&
                !Array.isArray(data[key]) &&
                Object.keys(data[key]).length === 0
              ),
          )
          .map((key) => [key, data[key]]),
      ),
    };
  });
}
