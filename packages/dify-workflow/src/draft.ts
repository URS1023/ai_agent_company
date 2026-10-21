import type { GraphDefinition, InputVar } from "./types";
import { availableVariables, simpleInputSchema } from "./variables";
export { availableVariables, beforeNodeIds } from "./variables";

export const editableNodeTypes = new Set([
  "start",
  "end",
  "if-else",
  "variable-aggregator",
  "variable-assigner",
]);
const editableInputs = new Set([
  "text-input",
  "paragraph",
  "select",
  "number",
  "checkbox",
]);
/** 畸形或尚未接入的分组结构保留为只读，不把原数据补成另一份可保存配置。 */
export function readableAggregation(data: Record<string, any>): boolean {
  const selectors = (value: unknown) =>
    Array.isArray(value) &&
    value.every(
      (item) =>
        Array.isArray(item) &&
        item.length >= 2 &&
        item.every((part) => typeof part === "string"),
    );
  if (typeof data.output_type !== "string" || !selectors(data.variables))
    return false;
  if (data.advanced_settings == null) return true;
  const settings = data.advanced_settings;
  return (
    typeof settings.group_enabled === "boolean" &&
    Array.isArray(settings.groups) &&
    settings.groups.every(
      (group: any) =>
        group &&
        typeof group.groupId === "string" &&
        !!group.groupId &&
        typeof group.group_name === "string" &&
        typeof group.output_type === "string" &&
        selectors(group.variables),
    )
  );
}
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
      ["variable-aggregator", "variable-assigner"].includes(node.data.type) &&
      !readableAggregation(node.data)
    )
      problems.push(`${label}的分组配置暂未支持编辑`);
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
        (v: InputVar) =>
          !editableInputs.has(v.type) &&
          !(
            v.type === "json_object" &&
            simpleInputSchema(v.json_schema)?.type === "object"
          ),
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

/** 原变量重命名只调整明确的选择器及Dify模板引用；其他未知节点元数据原样保留。 */
export function renameReferences(
  definition: GraphDefinition,
  nodeId: string,
  before: string,
  after: string,
): GraphDefinition {
  return renameSelectorReferences(
    definition,
    [nodeId, before],
    [nodeId, after],
  );
}

/** 变量与聚合分组均按完整选择器重命名；保留嵌套尾段和未涉及的元数据。 */
export function renameSelectorReferences(
  definition: GraphDefinition,
  before: string[],
  after: string[],
): GraphDefinition {
  const copy = cloneDefinition(definition);
  const replace = (selector: any) => {
    if (
      Array.isArray(selector) &&
      before.every((part, index) => selector[index] === part)
    )
      selector.splice(0, before.length, ...after);
  };
  const visit = (value: any): void => {
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      if (
        [
          "value_selector",
          "variable_selector",
          "selector",
          "iterator_selector",
          "output_selector",
        ].includes(key) &&
        Array.isArray(child)
      )
        replace(child);
      else if (
        key === "variables" &&
        Array.isArray(child) &&
        child.every((item) => Array.isArray(item))
      )
        child.forEach(replace);
      else if (
        typeof child === "string" &&
        child.includes(`{{#${before.join(".")}#}}`)
      )
        value[key] = child.replaceAll(
          `{{#${before.join(".")}#}}`,
          `{{#${after.join(".")}#}}`,
        );
      else visit(child);
    }
  };
  visit(copy.graph.nodes);
  return copy;
}
export function isSelectorUsed(
  definition: GraphDefinition,
  selector: string[],
): boolean {
  return (
    JSON.stringify(
      renameSelectorReferences(definition, selector, ["__reference_probe__"]),
    ) !== JSON.stringify(definition)
  );
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
    if (["variable-aggregator", "variable-assigner"].includes(node.data.type)) {
      const grouped = !!node.data.advanced_settings?.group_enabled;
      const groups = grouped
        ? (node.data.advanced_settings.groups ?? [])
        : [
            {
              variables: node.data.variables,
              output_type: node.data.output_type,
            },
          ];
      if (!groups.length) errors[key] = "请添加聚合分组";
      if (
        grouped &&
        new Set(groups.map((group: any) => group.group_name)).size !==
          groups.length
      )
        errors[key] = "分组名称不能重复";
      if (
        grouped &&
        new Set(groups.map((group: any) => group.groupId)).size !==
          groups.length
      )
        errors[key] = "请检查分组连接";
      for (const group of groups) {
        if (!Array.isArray(group.variables) || !group.variables.length) {
          errors[key] = "请选择聚合变量";
          continue;
        }
        if (
          grouped &&
          (!group.groupId ||
            !/^[A-Za-z_][A-Za-z0-9_]{0,29}$/.test(group.group_name) ||
            group.output_type === "any")
        )
          errors[key] = "请检查分组名称和变量类型";
        const candidates = availableVariables(
          definition,
          node.id,
          grouped ? group.groupId : undefined,
        );
        if (
          new Set(
            group.variables.map((selector: string[]) =>
              JSON.stringify(selector),
            ),
          ).size !== group.variables.length
        )
          errors[key] = "聚合变量不能重复";
        for (const selector of group.variables) {
          const option = candidates.find(
            (option) =>
              JSON.stringify(option.selector) === JSON.stringify(selector),
          );
          if (
            !option ||
            (group.output_type !== "any" &&
              option.type !== "any" &&
              option.type !== group.output_type)
          )
            errors[key] = "请选择已连接且类型匹配的聚合变量";
        }
      }
    }
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
