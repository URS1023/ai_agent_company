import type {
  GraphDefinition,
  GraphNode,
  InputVar,
  VariableOption,
  VarType,
} from "./types";

/** 连通性仅取当前输入句柄的上游；不把另一分组或未连线节点列作候选。 */
export function beforeNodeIds(
  nodes: GraphNode[],
  edges: GraphDefinition["graph"]["edges"],
  nodeId: string,
  targetHandle?: string,
): Set<string> {
  const found = new Set<string>(),
    queue = [nodeId];
  while (queue.length) {
    const current = queue.shift()!;
    for (const edge of edges) {
      if (
        edge.target !== current ||
        found.has(edge.source) ||
        edge.source === nodeId ||
        (current === nodeId &&
          targetHandle !== undefined &&
          (edge.targetHandle ?? "target") !== targetHandle)
      )
        continue;
      if (!nodes.some((node) => node.id === edge.source)) continue;
      found.add(edge.source);
      queue.push(edge.source);
    }
  }
  return found;
}

const supported = new Set([
  "string",
  "number",
  "integer",
  "boolean",
  "object",
  "array[string]",
  "array[number]",
  "array[boolean]",
  "array[object]",
]);
/** 只展开简单本地对象属性，数组下标与远程/复杂schema不能变成变量选择器。 */
export function simpleInputSchema(
  value: unknown,
  depth = 0,
): Record<string, any> | null {
  let schema = value;
  if (typeof schema === "string") {
    try {
      schema = JSON.parse(schema);
    } catch {
      return null;
    }
  }
  if (
    !schema ||
    typeof schema !== "object" ||
    Array.isArray(schema) ||
    depth > 5
  )
    return null;
  const data = schema as Record<string, any>;
  const keys = new Set([
    "type",
    "title",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "default",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "additionalProperties",
    "$schema",
  ]);
  if (
    Object.keys(data).some((key) => !keys.has(key)) ||
    !["object", "array", "string", "number", "integer", "boolean"].includes(
      data.type,
    )
  )
    return null;
  if (data.type === "object") {
    if (
      !data.properties ||
      typeof data.properties !== "object" ||
      Array.isArray(data.properties)
    )
      return null;
    if (
      Object.values(data.properties).some(
        (child) => !simpleInputSchema(child, depth + 1),
      )
    )
      return null;
  }
  if (data.type === "array" && !simpleInputSchema(data.items, depth + 1))
    return null;
  return data;
}

export function availableVariables(
  definition: GraphDefinition,
  nodeId: string,
  targetHandle?: string,
): VariableOption[] {
  const ids = beforeNodeIds(
    definition.graph.nodes,
    definition.graph.edges,
    nodeId,
    targetHandle,
  );
  const result: VariableOption[] = [];
  const addSchema = (
    node: GraphNode,
    schema: Record<string, any>,
    path: string[],
    label: string,
  ) => {
    const type =
      schema.type === "array"
        ? `array[${schema.items.type === "integer" ? "number" : schema.items.type}]`
        : schema.type === "integer"
          ? "number"
          : schema.type;
    if (supported.has(type))
      result.push({
        selector: [node.id, ...path],
        label,
        type: type as VarType,
      });
    if (schema.type === "object")
      for (const [key, child] of Object.entries(schema.properties ?? {}))
        addSchema(
          node,
          child as Record<string, any>,
          [...path, key],
          `${label} / ${(child as any).title || key}`,
        );
  };
  for (const node of definition.graph.nodes.filter((node) =>
    ids.has(node.id),
  )) {
    const title = node.data.title || "节点";
    if (node.data.type === "start") {
      for (const field of (node.data.variables ?? []) as InputVar[]) {
        const label = `${title} / ${field.label || field.variable}`;
        if (field.type === "json_object") {
          const schema = simpleInputSchema(field.json_schema);
          if (schema?.type === "object")
            addSchema(node, schema, [field.variable], label);
          else
            result.push({
              selector: [node.id, field.variable],
              label,
              type: "object",
            });
        } else
          result.push({
            selector: [node.id, field.variable],
            label,
            type:
              field.type === "number"
                ? "number"
                : field.type === "checkbox"
                  ? "boolean"
                  : "string",
          });
      }
    }
    if (["variable-aggregator", "variable-assigner"].includes(node.data.type)) {
      const settings = node.data.advanced_settings;
      if (settings?.group_enabled)
        for (const group of settings.groups ?? [])
          result.push({
            selector: [node.id, group.group_name, "output"],
            label: `${title} / ${group.group_name} / output`,
            type: group.output_type,
          });
      else
        result.push({
          selector: [node.id, "output"],
          label: `${title} / output`,
          type: node.data.output_type,
        });
    }
  }
  return result;
}
