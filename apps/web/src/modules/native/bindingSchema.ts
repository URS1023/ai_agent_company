/** 浏览器检查编辑格式与结构，完整JSON Schema语义继续由服务端2020-12校验。 */
export function parseInputSchema(
  source: string,
): Record<string, unknown> | null {
  if (!source.trim()) return null;
  if (new TextEncoder().encode(source).length > 100000)
    throw new Error("输入字段定义超过100KB");
  let value: unknown;
  try {
    value = JSON.parse(source);
  } catch {
    throw new Error("请输入有效的 JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("输入字段定义须为JSON对象");
  const schema = value as Record<string, any>;
  if (schema.type !== undefined && schema.type !== "object")
    throw new Error("流程输入须使用对象类型");
  if (
    schema.properties !== undefined &&
    (!schema.properties ||
      typeof schema.properties !== "object" ||
      Array.isArray(schema.properties))
  )
    throw new Error("properties 须为字段对象");
  if (
    schema.required !== undefined &&
    (!Array.isArray(schema.required) ||
      schema.required.some((item: unknown) => typeof item !== "string") ||
      new Set(schema.required).size !== schema.required.length)
  )
    throw new Error("required 须为不重复的字段名称列表");
  return schema;
}
export function applicationFields(
  form: {
    native_id: string;
    app_mode: string;
    release_id?: string;
    credential_ref?: string;
  },
  schemaText: string,
  editing: boolean,
): Record<string, unknown> {
  const fields: Record<string, unknown> = {
    native_id: form.native_id,
    ...(!editing ? { app_mode: form.app_mode } : {}),
  };
  if (form.app_mode === "workflow") fields.release_id = form.release_id || null;
  if (!["agent_v2", "openwebui_model"].includes(form.app_mode)) {
    fields.credential_ref = form.credential_ref || null;
    fields.input_schema = parseInputSchema(schemaText);
  }
  return fields;
}
