/** 数据准备表单只组装白名单DTO；计算、精度与来源授权仍由服务端判断。 */
export const dataTypes = [
  { value: "text", label: "文本" },
  { value: "decimal", label: "小数" },
  { value: "integer", label: "整数" },
  { value: "boolean", label: "布尔值" },
  { value: "date", label: "日期" },
  { value: "datetime", label: "日期时间" },
] as const;
export type DataType = (typeof dataTypes)[number]["value"];
export type Scalar = string | number | boolean | null;
export type Fields = Record<string, string>;
export const operationTypes = [
  { value: "select", label: "选择字段" },
  { value: "drop_null", label: "去除空值" },
  { value: "deduplicate", label: "删除重复记录" },
  { value: "filter", label: "筛选记录" },
  { value: "rename", label: "重命名字段" },
  { value: "cast", label: "转换类型" },
  { value: "derive", label: "计算字段" },
  { value: "aggregate", label: "分组汇总" },
] as const;
export const comparisons = [
  { value: "eq", label: "等于" },
  { value: "ne", label: "不等于" },
  { value: "in", label: "属于" },
  { value: "not_in", label: "不属于" },
  { value: "gt", label: "大于" },
  { value: "gte", label: "大于等于" },
  { value: "lt", label: "小于" },
  { value: "lte", label: "小于等于" },
  { value: "contains", label: "包含文字" },
  { value: "is_null", label: "为空" },
  { value: "not_null", label: "不为空" },
];
export const aggregations = [
  { value: "sum", label: "求和" },
  { value: "avg", label: "平均值" },
  { value: "min", label: "最小值" },
  { value: "max", label: "最大值" },
  { value: "count", label: "计数" },
  { value: "count_distinct", label: "去重计数" },
];
export const expressionOperations = [
  { value: "add", label: "相加" },
  { value: "subtract", label: "相减" },
  { value: "multiply", label: "相乘" },
  { value: "divide", label: "相除" },
  { value: "coalesce", label: "首个非空值" },
  { value: "concat", label: "拼接文本" },
  { value: "round", label: "四舍五入" },
];
export const qualityTypes = [
  { value: "required", label: "必填" },
  { value: "unique", label: "唯一" },
  { value: "range", label: "数值范围" },
  { value: "enum", label: "允许值" },
  { value: "type", label: "数据类型" },
  { value: "length", label: "文本长度" },
  { value: "compare", label: "字段比较" },
];
export interface AggregateDraft {
  name: string;
  function: string;
  field: string;
  data_type: "decimal" | "text";
  unit_field: string;
}
export interface ExpressionDraft {
  kind: "expression" | "field" | "literal" | "null";
  field: string;
  value: Scalar;
  literal_type: DataType;
  op: string;
  args: ExpressionDraft[];
  digits: number;
}
export interface FilterDraft {
  key: string;
  field: string;
  operator: string;
  data_type: DataType;
  value: Scalar | Scalar[];
}
export interface OperationDraft {
  key: string;
  type: (typeof operationTypes)[number]["value"];
  field: string;
  fields: string[];
  output_field: string;
  data_type: DataType;
  on_error: "fail" | "null";
  match: "all" | "any";
  conditions: FilterDraft[];
  expression: ExpressionDraft;
  group_by: string[];
  aggregations: AggregateDraft[];
}
export interface QualityDraft {
  key: string;
  name: string;
  type: (typeof qualityTypes)[number]["value"];
  field: string;
  data_type: DataType;
  allow_missing: boolean;
  minimum: string;
  maximum: string;
  values: Scalar[];
  min_length: number | undefined;
  max_length: number | undefined;
  other_field: string;
  operator: string;
}
export interface MetricDraft {
  version: 1;
  kind: "aggregate" | "ratio";
  group_by: string[];
  aggregation: AggregateDraft;
  numerator: AggregateDraft;
  denominator: AggregateDraft;
  scale: string;
  unit: string;
}
export function newAggregate(name = ""): AggregateDraft {
  return {
    name,
    function: "sum",
    field: "",
    data_type: "decimal",
    unit_field: "",
  };
}
export function newExpression(
  kind: ExpressionDraft["kind"] = "expression",
): ExpressionDraft {
  return {
    kind,
    field: "",
    value: "",
    literal_type: "text",
    op: "add",
    args:
      kind === "expression"
        ? [newExpression("field"), newExpression("field")]
        : [],
    digits: 2,
  };
}
export function newFilter(): FilterDraft {
  return {
    key: crypto.randomUUID(),
    field: "",
    operator: "eq",
    data_type: "text",
    value: "",
  };
}
export function newOperation(): OperationDraft {
  return {
    key: crypto.randomUUID(),
    type: "drop_null",
    field: "",
    fields: [],
    output_field: "",
    data_type: "text",
    on_error: "fail",
    match: "all",
    conditions: [newFilter()],
    expression: newExpression(),
    group_by: [],
    aggregations: [newAggregate()],
  };
}
export function newQualityRule(): QualityDraft {
  return {
    key: crypto.randomUUID(),
    name: "",
    type: "required",
    field: "",
    data_type: "text",
    allow_missing: true,
    minimum: "",
    maximum: "",
    values: [],
    min_length: undefined,
    max_length: undefined,
    other_field: "",
    operator: "eq",
  };
}
export function metricDraft(definition: Record<string, any> = {}): MetricDraft {
  const aggregate = (value: Record<string, any> = {}, name: string) => ({
    ...newAggregate(name),
    ...value,
  });
  return {
    version: 1,
    kind: definition.kind === "ratio" ? "ratio" : "aggregate",
    group_by: [...(definition.group_by ?? [])],
    aggregation: aggregate(definition.aggregation, "指标值"),
    numerator: aggregate(definition.numerator, "分子"),
    denominator: aggregate(definition.denominator, "分母"),
    scale: String(definition.scale ?? "1"),
    unit: definition.unit ?? "",
  };
}
export function queryColumns(query?: Record<string, any>): string[] {
  const columns = query?.columns ?? query?.result?.columns ?? [];
  return columns
    .map((x: unknown) =>
      typeof x === "string" ? x : (x as { name?: string }).name,
    )
    .filter((x: unknown): x is string => typeof x === "string");
}
export function queryLabel(query: Record<string, any>): string {
  return query.name || query.sql || "未命名查询";
}
/** 只推导字段名，不提前执行用户运算或补造数据。 */
export function operationColumns(
  columns: string[],
  steps: OperationDraft[],
): string[] {
  return steps.reduce(
    (current, step) => {
      if (step.type === "select")
        return step.fields.filter((x) => current.includes(x));
      if (step.type === "rename")
        return current.map((x) =>
          x === step.field && step.output_field ? step.output_field : x,
        );
      if (["cast", "derive"].includes(step.type) && step.output_field)
        return [...new Set([...current, step.output_field])];
      if (step.type === "aggregate")
        return [
          ...step.group_by,
          ...step.aggregations.map((x) => x.name).filter(Boolean),
        ];
      return current;
    },
    [...columns],
  );
}
export class PreparationError extends Error {
  constructor(public fields: Fields) {
    super("请检查标记的字段");
  }
}
const decimalPattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
export function validDecimal(value: string): boolean {
  return decimalPattern.test(value);
}
function fail(fields: Fields): never {
  throw new PreparationError(fields);
}
function requireField(
  field: string,
  columns: string[],
  path: string,
  errors: Fields,
  allowCustom = false,
) {
  if (!field || (!allowCustom && !columns.includes(field)))
    errors[path] = "请选择有效字段";
}
function aggregatePayload(
  draft: AggregateDraft,
  columns: string[],
  path: string,
  errors: Fields,
  allowCustom = false,
) {
  if (!draft.name.trim()) errors[`${path}.name`] = "请输入结果字段名";
  if (draft.function !== "count" || draft.field)
    requireField(draft.field, columns, `${path}.field`, errors, allowCustom);
  if (draft.unit_field)
    requireField(
      draft.unit_field,
      columns,
      `${path}.unit_field`,
      errors,
      allowCustom,
    );
  return {
    name: draft.name.trim(),
    function: draft.function,
    ...(draft.field ? { field: draft.field } : {}),
    data_type: draft.data_type,
    ...(draft.unit_field ? { unit_field: draft.unit_field } : {}),
  };
}
function scalarValid(value: Scalar, type: DataType): boolean {
  if (type === "decimal")
    return typeof value === "string" && validDecimal(value);
  if (type === "integer")
    return typeof value === "string" && /^[+-]?\d+$/.test(value);
  if (type === "boolean") return typeof value === "boolean";
  if (type === "date")
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  if (type === "datetime")
    return (
      typeof value === "string" &&
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    );
  return typeof value === "string";
}
export function expressionNodeCount(node: ExpressionDraft): number {
  return (
    1 +
    (node.kind === "expression"
      ? node.args.reduce((sum, x) => sum + expressionNodeCount(x), 0)
      : 0)
  );
}
function expressionPayload(
  node: ExpressionDraft,
  columns: string[],
  path: string,
  errors: Fields,
  depth = 1,
): Record<string, any> {
  if (depth > 8) errors[path] = "最多嵌套8层";
  if (node.kind === "field") {
    requireField(node.field, columns, `${path}.field`, errors);
    return { field: node.field };
  }
  if (node.kind === "null") return { value: null };
  if (node.kind === "literal") {
    if (!scalarValid(node.value, node.literal_type))
      errors[`${path}.value`] = "请输入有效值";
    return { value: node.value };
  }
  if (node.args.length < 1 || node.args.length > 10)
    errors[`${path}.args`] = "请保留1至10个参数";
  if (
    ["add", "subtract", "multiply", "divide"].includes(node.op) &&
    node.args.length !== 2
  )
    errors[`${path}.args`] = "请填写两个计算项";
  if (node.op === "round" && node.args.length !== 1)
    errors[`${path}.args`] = "请填写一个计算项";
  return {
    op: node.op,
    args: node.args.map((x, i) =>
      expressionPayload(x, columns, `${path}.args.${i}`, errors, depth + 1),
    ),
    ...(node.op === "round" ? { digits: node.digits } : {}),
  };
}
export function processingPayload(
  name: string,
  queryId: string,
  steps: OperationDraft[],
  initialColumns: string[],
) {
  const errors: Fields = {};
  if (!name.trim()) errors.name = "请输入任务名称";
  if (!queryId) errors.query_id = "请选择数据查询";
  if (steps.length < 1 || steps.length > 20)
    errors.operations = "请保留1至20个步骤";
  const operations = steps.map((step, index) => {
    const path = `operations.${index}`;
    const columns = operationColumns(initialColumns, steps.slice(0, index));
    if (["select", "deduplicate"].includes(step.type)) {
      if (!step.fields.length && step.type === "select")
        errors[`${path}.fields`] = "请选择字段";
      step.fields.forEach((x) =>
        requireField(x, columns, `${path}.fields`, errors),
      );
      return {
        type: step.type,
        ...(step.fields.length ? { fields: [...step.fields] } : {}),
      };
    }
    if (step.type === "drop_null") {
      requireField(step.field, columns, `${path}.field`, errors);
      return { type: step.type, field: step.field };
    }
    if (step.type === "rename" || step.type === "cast") {
      requireField(step.field, columns, `${path}.field`, errors);
      const output = step.output_field.trim();
      if (!output && step.type === "rename")
        errors[`${path}.output_field`] = "请输入新字段名";
      if (output !== step.field && columns.includes(output))
        errors[`${path}.output_field`] = "该字段名已存在";
      return {
        type: step.type,
        field: step.field,
        ...(output ? { output_field: output } : {}),
        ...(step.type === "cast"
          ? { data_type: step.data_type, on_error: step.on_error }
          : {}),
      };
    }
    if (step.type === "derive") {
      if (!step.output_field.trim())
        errors[`${path}.output_field`] = "请输入结果字段名";
      if (columns.includes(step.output_field.trim()))
        errors[`${path}.output_field`] = "该字段名已存在";
      if (expressionNodeCount(step.expression) > 100)
        errors[`${path}.expression`] = "计算项超过100个";
      return {
        type: step.type,
        output_field: step.output_field.trim(),
        expression: expressionPayload(
          step.expression,
          columns,
          `${path}.expression`,
          errors,
        ),
        on_error: step.on_error,
      };
    }
    if (step.type === "aggregate") {
      step.group_by.forEach((x) =>
        requireField(x, columns, `${path}.group_by`, errors),
      );
      if (step.group_by.length > 10)
        errors[`${path}.group_by`] = "最多选择10个分组字段";
      const values = step.aggregations.map((x, i) =>
        aggregatePayload(x, columns, `${path}.aggregations.${i}`, errors),
      );
      if (!values.length || values.length > 20)
        errors[`${path}.aggregations`] = "请保留1至20个汇总项";
      const names = [...step.group_by];
      values.forEach((x, i) => {
        if (names.includes(x.name))
          errors[`${path}.aggregations.${i}.name`] = "结果字段名不能重复";
        names.push(x.name);
      });
      return {
        type: step.type,
        group_by: [...step.group_by],
        aggregations: values,
      };
    }
    if (!step.conditions.length)
      errors[`${path}.conditions`] = "请添加筛选条件";
    const conditions = step.conditions.map((condition, i) => {
      const p = `${path}.conditions.${i}`;
      requireField(condition.field, columns, `${p}.field`, errors);
      const noValue = ["is_null", "not_null"].includes(condition.operator);
      const many = ["in", "not_in"].includes(condition.operator);
      if (!noValue) {
        const values = many ? condition.value : [condition.value];
        if (
          !Array.isArray(values) ||
          !values.length ||
          values.some(
            (x) =>
              Array.isArray(x) ||
              !scalarValid(x as Scalar, condition.data_type),
          )
        )
          errors[`${p}.value`] = "请输入有效比较值";
      }
      if (condition.operator === "contains" && condition.data_type !== "text")
        errors[`${p}.operator`] = "包含文字只支持文本";
      return {
        field: condition.field,
        operator: condition.operator,
        data_type: condition.data_type,
        ...(!noValue ? { value: condition.value } : {}),
      };
    });
    return { type: step.type, match: step.match, conditions };
  });
  if (Object.keys(errors).length) fail(errors);
  return { name: name.trim(), query_id: queryId, operations };
}
export function qualityPayload(
  queryId: string,
  required: string[],
  unique: string[],
  rules: QualityDraft[],
  columns: string[],
) {
  const errors: Fields = {};
  if (!queryId) errors.query_id = "请选择数据查询";
  const result = rules.map((rule, i) => {
    const p = `rules.${i}`;
    requireField(rule.field, columns, `${p}.field`, errors);
    const base = {
      ...(rule.name.trim() ? { name: rule.name.trim() } : {}),
      type: rule.type,
      field: rule.field,
      data_type: rule.data_type,
      allow_missing: rule.allow_missing,
    };
    if (rule.type === "range") {
      if (!rule.minimum && !rule.maximum)
        errors[`${p}.minimum`] = "请至少填写一个边界";
      for (const key of ["minimum", "maximum"] as const)
        if (rule[key] && !validDecimal(rule[key]))
          errors[`${p}.${key}`] = "请输入有效数值";
      return {
        ...base,
        ...(rule.minimum ? { minimum: rule.minimum } : {}),
        ...(rule.maximum ? { maximum: rule.maximum } : {}),
      };
    }
    if (rule.type === "enum") {
      if (
        !rule.values.length ||
        rule.values.some((x) => !scalarValid(x, rule.data_type))
      )
        errors[`${p}.values`] = "请填写有效的允许值";
      return { ...base, values: rule.values };
    }
    if (rule.type === "length") {
      if (rule.min_length === undefined && rule.max_length === undefined)
        errors[`${p}.min_length`] = "请至少填写一个长度";
      return {
        ...base,
        ...(rule.min_length !== undefined
          ? { min_length: rule.min_length }
          : {}),
        ...(rule.max_length !== undefined
          ? { max_length: rule.max_length }
          : {}),
      };
    }
    if (rule.type === "compare") {
      requireField(rule.other_field, columns, `${p}.other_field`, errors);
      return {
        ...base,
        other_field: rule.other_field,
        operator: rule.operator,
      };
    }
    return base;
  });
  required.forEach((x) => requireField(x, columns, "required_fields", errors));
  unique.forEach((x) => requireField(x, columns, "unique_fields", errors));
  if (!required.length && !unique.length && !rules.length)
    errors.rules = "请添加检查规则";
  if (rules.length > 50) errors.rules = "最多添加50条规则";
  if (Object.keys(errors).length) fail(errors);
  return {
    query_id: queryId,
    required_fields: [...required],
    unique_fields: [...unique],
    rules: result,
  };
}
export function metricPayload(draft: MetricDraft, columns: string[] = []) {
  const errors: Fields = {};
  const definition: Record<string, any> = {
    version: 1,
    kind: draft.kind,
    group_by: [...draft.group_by],
    unit: draft.unit,
  };
  if (draft.kind === "aggregate")
    definition.aggregation = aggregatePayload(
      draft.aggregation,
      columns,
      "definition.aggregation",
      errors,
      true,
    );
  else {
    definition.numerator = aggregatePayload(
      draft.numerator,
      columns,
      "definition.numerator",
      errors,
      true,
    );
    definition.denominator = aggregatePayload(
      draft.denominator,
      columns,
      "definition.denominator",
      errors,
      true,
    );
    if (!validDecimal(draft.scale))
      errors["definition.scale"] = "请输入有效倍数";
    definition.scale = draft.scale;
  }
  if (draft.group_by.length > 10)
    errors["definition.group_by"] = "最多选择10个分组字段";
  if (Object.keys(errors).length) fail(errors);
  return definition;
}
/** 服务端路径只用于定位，不将operations.0等实现路径写到页面上。 */
export function preparationErrors(fields: Fields, message: string): Fields {
  const mapped = Object.fromEntries(
    Object.entries(fields).filter(
      ([key]) => !["path", "row_number"].includes(key),
    ),
  );
  if (fields.path)
    mapped[fields.path] =
      `${fields.row_number ? `第${fields.row_number}行：` : ""}${message}`;
  return mapped;
}
export const outcomeLabels: Record<string, string> = {
  passed: "通过",
  failed: "未通过",
  warning: "需检查",
  incomplete: "数据不完整",
  no_data: "无数据",
  zero_denominator: "分母为零",
  missing_denominator: "缺少分母",
  ready: "已计算",
  computed: "已计算",
  succeeded: "已完成",
};
export function displayScalar(value: unknown): string {
  return value === null || value === undefined
    ? "—"
    : typeof value === "boolean"
      ? value
        ? "是"
        : "否"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
}
