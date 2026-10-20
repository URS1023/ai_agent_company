/** 图输入只来自顶层开始节点；局部schema表单不解释节点或执行任意表达式。 */
export interface SimpleSchema {
  type: "string" | "number" | "integer" | "boolean" | "object" | "array";
  title?: string;
  properties?: Record<string, SimpleSchema>;
  required?: string[];
  items?: SimpleSchema;
  enum?: (string | number | boolean | null)[];
  default?: unknown;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minItems?: number;
  maxItems?: number;
  additionalProperties?: boolean;
  multiline?: boolean;
}
export interface GraphField {
  key: string;
  label: string;
  required: boolean;
  schema: SimpleSchema;
  default?: unknown;
  issue?: string;
}
export interface GraphDefinition {
  format: "graph";
  root_node_id: string;
  graph: {
    nodes: { id: string; data: Record<string, any> }[];
    edges: unknown[];
  };
  [key: string]: unknown;
}
const safeKeys = new Set([
  "$schema",
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
  "exclusiveMinimum",
  "exclusiveMaximum",
  "minItems",
  "maxItems",
  "additionalProperties",
]);
const own = (value: object, key: string) =>
  Object.prototype.hasOwnProperty.call(value, key);
const object = (value: unknown): value is Record<string, any> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);
const copy = (value: unknown) =>
  value === undefined ? undefined : JSON.parse(JSON.stringify(value));
function schemaIssue(value: unknown, depth = 0): string {
  if (
    !object(value) ||
    depth > 5 ||
    Object.keys(value).some((key) => !safeKeys.has(key))
  )
    return "该输入结构暂未支持，请联系流程维护者调整字段定义";
  if (
    !["string", "number", "integer", "boolean", "object", "array"].includes(
      value.type,
    )
  )
    return "该字段需要明确的输入类型";
  if (
    value.enum &&
    (!Array.isArray(value.enum) ||
      !value.enum.length ||
      value.enum.some(
        (item: unknown) =>
          item === null ||
          !["string", "number", "boolean"].includes(typeof item),
      ))
  )
    return "该字段的选项暂不支持";
  if (value.type === "object") {
    if (
      (value.properties !== undefined && !object(value.properties)) ||
      (value.additionalProperties !== undefined &&
        typeof value.additionalProperties !== "boolean")
    )
      return "该对象的字段结构暂不支持";
    const properties = Object.values(value.properties ?? {});
    if (
      Array.isArray(value.required) &&
      value.required.some((key: string) => !own(value.properties ?? {}, key))
    )
      return "必填子字段缺少可填写定义，请联系流程维护者";
    if (
      properties.length > 100 ||
      (value.required !== undefined &&
        (!Array.isArray(value.required) ||
          value.required.some((key: unknown) => typeof key !== "string")))
    )
      return "该对象的字段结构暂不支持";
    for (const child of properties) {
      const issue = schemaIssue(child, depth + 1);
      if (issue) return issue;
    }
  }
  if (value.type === "array") {
    if ((value.minItems ?? 0) > 100) return "该数组超过当前表单可填写范围";
    const issue = schemaIssue(value.items, depth + 1);
    if (issue) return issue;
  }
  return "";
}
export function graphFields(definition: GraphDefinition): GraphField[] {
  const root = definition.graph?.nodes?.find(
    (node) => node.id === definition.root_node_id && node.data.type === "start",
  );
  if (!root || !Array.isArray(root.data.variables ?? []))
    throw new Error("未找到可填写的开始节点");
  return (root.data.variables ?? []).map((field: Record<string, any>) => {
    let schema: SimpleSchema | undefined,
      issue = "";
    if (field.type === "json_object") {
      if (!field.json_schema)
        issue = "该对象尚未定义可填写的子字段，请联系流程维护者";
      try {
        schema =
          typeof field.json_schema === "string"
            ? JSON.parse(field.json_schema)
            : field.json_schema;
      } catch {
        issue = "输入结构无法读取，请联系流程维护者";
      }
      schema ??= { type: "object", properties: {} };
      issue ||= schemaIssue(schema);
      if (schema.type !== "object")
        issue = "该输入需要对象结构，当前表单无法填写此定义";
    } else {
      schema = {
        type:
          field.type === "number"
            ? "number"
            : field.type === "checkbox"
              ? "boolean"
              : "string",
      };
      if (field.type === "select") schema.enum = field.options;
      if (field.type === "paragraph") schema.multiline = true;
      if (field.max_length) schema.maxLength = field.max_length;
      if (
        !["text-input", "paragraph", "number", "select", "checkbox"].includes(
          field.type,
        )
      )
        issue = "该输入类型暂未支持";
    }
    return {
      key: field.variable,
      label: field.label || field.variable,
      required: field.required === true,
      schema,
      default: field.default,
      issue,
    };
  });
}
export function initialSchemaValue(schema: SimpleSchema): unknown {
  if (schema.default !== undefined) return copy(schema.default);
  if (schema.type === "object")
    return Object.fromEntries(
      Object.entries(schema.properties ?? {}).flatMap(([key, child]) =>
        child.default !== undefined ? [[key, copy(child.default)]] : [],
      ),
    );
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  return undefined;
}
export function initialGraphInputs(fields: GraphField[]) {
  return Object.fromEntries(
    fields.flatMap((field) => {
      const value =
        field.default !== undefined
          ? copy(field.default)
          : field.required || field.schema.type === "boolean"
            ? initialSchemaValue(field.schema)
            : undefined;
      return value === undefined ? [] : [[field.key, value]];
    }),
  );
}
function validate(
  schema: SimpleSchema,
  value: unknown,
  path: string,
  required: boolean,
  errors: Record<string, string>,
) {
  if (value === undefined) {
    if (required) errors[path] = "请填写此项";
    return;
  }
  if (schema.enum && !schema.enum.some((item) => Object.is(item, value))) {
    errors[path] = "请选择有效选项";
    return;
  }
  if (schema.type === "object") {
    if (!object(value)) {
      errors[path] = "请填写对象字段";
      return;
    }
    const properties = schema.properties ?? {};
    if (
      schema.additionalProperties === false &&
      Object.keys(value).some((key) => !own(properties, key))
    )
      errors[path] = "内容包含未定义字段";
    for (const [key, child] of Object.entries(properties))
      validate(
        child,
        own(value, key) ? value[key] : undefined,
        `${path}.${key}`,
        schema.required?.includes(key) ?? false,
        errors,
      );
    return;
  }
  if (schema.type === "array") {
    if (!Array.isArray(value)) {
      errors[path] = "请填写列表项目";
      return;
    }
    if (value.length < (schema.minItems ?? 0))
      errors[path] = `至少填写${schema.minItems}项`;
    if (value.length > Math.min(schema.maxItems ?? 100, 100))
      errors[path] = "列表项目超出可填写范围";
    value.forEach((item, index) =>
      validate(schema.items!, item, `${path}.${index}`, true, errors),
    );
    return;
  }
  if (schema.type === "boolean") {
    if (typeof value !== "boolean") errors[path] = "请选择是或否";
    return;
  }
  if (schema.type === "string") {
    if (typeof value !== "string") errors[path] = "请填写文本";
    else if (value.length < (schema.minLength ?? 0))
      errors[path] = `至少填写${schema.minLength}个字`;
    else if (value.length > (schema.maxLength ?? 40000))
      errors[path] = "内容超出长度限制";
    return;
  }
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    (schema.type === "integer" && !Number.isInteger(value))
  ) {
    errors[path] = "请填写有效数值";
    return;
  }
  if (Number.isInteger(value) && !Number.isSafeInteger(value)) {
    errors[path] = "整数超出可安全填写范围";
    return;
  }
  if (
    (schema.minimum !== undefined && value < schema.minimum) ||
    (schema.maximum !== undefined && value > schema.maximum) ||
    (schema.exclusiveMinimum !== undefined &&
      value <= schema.exclusiveMinimum) ||
    (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum)
  )
    errors[path] = "数值超出允许范围";
}
export function validateGraphInputs(
  fields: GraphField[],
  values: Record<string, unknown>,
) {
  const errors: Record<string, string> = {},
    inputs: Record<string, unknown> = {};
  for (const field of fields) {
    const path = `inputs.${field.key}`,
      value = own(values, field.key) ? values[field.key] : undefined;
    if (field.issue) {
      errors[path] = field.issue;
      continue;
    }
    if (value === null && !field.required) {
      Object.defineProperty(inputs, field.key, {
        value: null,
        enumerable: true,
        configurable: true,
        writable: true,
      });
      continue;
    }
    if (
      field.required &&
      (value === undefined || value === null || value === "")
    ) {
      errors[path] = `请填写${field.label}`;
      continue;
    }
    validate(field.schema, value, path, field.required, errors);
    if (value !== undefined)
      Object.defineProperty(inputs, field.key, {
        value: copy(value),
        enumerable: true,
        configurable: true,
        writable: true,
      });
  }
  return { inputs, errors };
}
