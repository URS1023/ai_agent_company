import type {
  GraphDefinition,
  GraphNode,
  InputVar,
  VariableOption,
  VarType,
} from "./types";

export const editableNodeTypes = new Set(["start", "end", "if-else"]);
const editableInputs = new Set([
  "text-input",
  "paragraph",
  "select",
  "number",
  "checkbox",
]);
/** 先检查整个定义；遇到尚未提取的配置只读保留，不能通过保存丢失原图。 */
export function editingBlockers(definition: GraphDefinition): string[] {
  const problems: string[] = [];
  if (
    definition.format !== "graph" ||
    !Array.isArray(definition.graph?.nodes) ||
    !Array.isArray(definition.graph?.edges)
  )
    return ["图定义无法识别"];
  for (const node of definition.graph.nodes) {
    const label = node.data.title || "节点";
    if (!editableNodeTypes.has(node.data.type))
      problems.push(`${label}的属性编辑尚未接入`);
    if (
      node.parentId ||
      node.parentNode ||
      node.data.isInIteration ||
      node.data.isInLoop
    )
      problems.push(`${label}属于容器，当前保留只读`);
    if (
      node.data.type === "start" &&
      (node.data.variables ?? []).some(
        (v: InputVar) => !editableInputs.has(v.type),
      )
    )
      problems.push(`${label}包含暂未接入的字段配置`);
    if (
      node.data.type === "if-else" &&
      (node.data.cases ?? []).some((c: any) =>
        c.conditions?.some((item: any) => item.sub_variable_condition),
      )
    )
      problems.push(`${label}包含文件子条件，当前保留只读`);
  }
  return [...new Set(problems)];
}
export function cloneDefinition(value: GraphDefinition): GraphDefinition {
  return structuredClone(value);
}

export function beforeNodeIds(
  nodes: GraphNode[],
  edges: GraphDefinition["graph"]["edges"],
  nodeId: string,
): Set<string> {
  const found = new Set<string>(),
    queue = [nodeId];
  while (queue.length) {
    const current = queue.shift()!;
    for (const edge of edges) {
      if (
        edge.target !== current ||
        found.has(edge.source) ||
        edge.source === nodeId
      )
        continue;
      if (!nodes.some((node) => node.id === edge.source)) continue;
      found.add(edge.source);
      queue.push(edge.source);
    }
  }
  return found;
}
export function availableVariables(
  definition: GraphDefinition,
  nodeId: string,
): VariableOption[] {
  const ids = beforeNodeIds(
    definition.graph.nodes,
    definition.graph.edges,
    nodeId,
  );
  const result: VariableOption[] = [];
  for (const node of definition.graph.nodes.filter((node) =>
    ids.has(node.id),
  )) {
    if (node.data.type !== "start") continue;
    for (const variable of (node.data.variables ?? []) as InputVar[]) {
      const type: VarType =
        variable.type === "number"
          ? "number"
          : variable.type === "checkbox"
            ? "boolean"
            : variable.type === "json_object"
              ? "object"
              : "string";
      result.push({
        selector: [node.id, variable.variable],
        label: `${node.data.title || "开始"} / ${variable.label || variable.variable}`,
        type,
      });
    }
  }
  return result;
}

/** 原变量重命名只调整明确的选择器及Dify模板引用；其他未知节点元数据原样保留。 */
export function renameReferences(
  definition: GraphDefinition,
  nodeId: string,
  before: string,
  after: string,
): GraphDefinition {
  const copy = cloneDefinition(definition);
  const visit = (value: any): void => {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (
        ["value_selector", "variable_selector", "selector"].includes(key) &&
        Array.isArray(child) &&
        child[0] === nodeId &&
        child[1] === before
      )
        child[1] = after;
      else if (
        typeof child === "string" &&
        child.includes(`{{#${nodeId}.${before}#}}`)
      )
        value[key] = child.replaceAll(
          `{{#${nodeId}.${before}#}}`,
          `{{#${nodeId}.${after}#}}`,
        );
      else visit(child);
    }
  };
  visit(copy.graph.nodes);
  return copy;
}
export function isVariableUsed(
  definition: GraphDefinition,
  nodeId: string,
  variable: string,
): boolean {
  return (
    JSON.stringify(
      renameReferences(definition, nodeId, variable, "__reference_probe__"),
    ) !== JSON.stringify(definition)
  );
}
export function removeBranch(
  definition: GraphDefinition,
  nodeId: string,
  handleId: string,
): GraphDefinition {
  const copy = cloneDefinition(definition);
  copy.graph.edges = copy.graph.edges.filter(
    (edge) => !(edge.source === nodeId && edge.sourceHandle === handleId),
  );
  return copy;
}
export function validateDraft(
  definition: GraphDefinition,
): Record<string, string> {
  const errors: Record<string, string> = {},
    { nodes, edges } = definition.graph;
  const ids = new Set(nodes.map((node) => node.id));
  if (ids.size !== nodes.length) errors.graph = "节点编号重复";
  if (
    !nodes.some(
      (node) =>
        node.id === definition.root_node_id && node.data.type === "start",
    )
  )
    errors.graph = "请选择有效的开始节点";
  if (nodes.filter((node) => node.data.type === "start").length !== 1)
    errors.graph = "只能保留一个开始节点";
  if (edges.some((edge) => !ids.has(edge.source) || !ids.has(edge.target)))
    errors.graph = "请删除未连接到节点的连线";
  for (const node of nodes) {
    const key = `nodes.${node.id}`;
    if (!node.data.title?.trim()) errors[`${key}.title`] = "请输入节点名称";
    const options = availableVariables(definition, node.id);
    if (node.data.type === "end") {
      const outputs = node.data.outputs ?? [];
      if (!outputs.length) errors[key] = "请添加输出变量";
      if (
        new Set(outputs.map((output: any) => output.variable)).size !==
        outputs.length
      )
        errors[key] = "输出变量名称不能重复";
      for (const output of outputs)
        if (
          !output.variable?.trim() ||
          !options.some(
            (option) =>
              JSON.stringify(option.selector) ===
              JSON.stringify(output.value_selector),
          )
        )
          errors[key] = "请选择已连接的输出变量";
    }
    if (node.data.type === "if-else") {
      if (!node.data.cases?.length) errors[key] = "请添加判断条件";
      for (const group of node.data.cases ?? []) {
        if (!group.conditions?.length) errors[key] = "请添加判断条件";
        for (const condition of group.conditions ?? []) {
          if (
            !options.some(
              (option) =>
                JSON.stringify(option.selector) ===
                JSON.stringify(condition.variable_selector),
            )
          )
            errors[key] = "请选择已连接的条件变量";
          const noValue = [
            "empty",
            "not empty",
            "is null",
            "is not null",
            "exists",
            "not exists",
          ].includes(condition.comparison_operator);
          if (
            !condition.comparison_operator ||
            (!noValue &&
              (condition.value === undefined ||
                condition.value === null ||
                condition.value === ""))
          )
            errors[key] = "请补全判断条件";
        }
      }
    }
  }
  return errors;
}
