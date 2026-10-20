/** 行权限编辑协议；草稿ID和控件类型不进入后端，显式值保持类型且不静默舍入。 */
export type PolicyScalar = string | number | boolean;
export type PolicyOperator =
  | "eq"
  | "ne"
  | "in"
  | "not_in"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "is_null"
  | "not_null";
export type PolicyValueSource =
  "literal" | "current_user_id" | "current_workspace_id";
export type PolicySubjectType = "all" | "role" | "user";
export interface PolicyColumn {
  name: string;
  type: string;
}
export interface PolicyTable {
  name: string;
  columns: PolicyColumn[];
}
export interface PolicySubject {
  id: string;
  name: string;
}
export interface PolicySubjects {
  roles: PolicySubject[];
  users: PolicySubject[];
}
export interface PolicyCondition {
  field: string;
  operator: PolicyOperator;
  value_source: PolicyValueSource;
  value?: PolicyScalar | PolicyScalar[];
}
export interface PolicyRule {
  subject_type: PolicySubjectType;
  subject_ids: string[];
  conditions: PolicyCondition[];
}
export interface RowPolicy {
  id: string;
  datasource_id: string;
  table_name: string;
  revision: number;
  mode: "all" | "rules";
  rules: PolicyRule[];
}
export interface ConditionDraft {
  key: string;
  field: string;
  operator: PolicyOperator;
  value_source: PolicyValueSource;
  value_kind: "text" | "number" | "boolean";
  value_text: string;
  values_text: string[];
  value_boolean: boolean | null;
  values_boolean: boolean[];
}
export interface RuleDraft {
  key: string;
  subject_type: PolicySubjectType;
  subject_ids: string[];
  conditions: ConditionDraft[];
}
export interface RowPolicyDraft {
  mode: "all" | "rules";
  rules: RuleDraft[];
}
export const policyOperators: { value: PolicyOperator; label: string }[] = [
  { value: "eq", label: "等于" },
  { value: "ne", label: "不等于" },
  { value: "in", label: "属于" },
  { value: "not_in", label: "不属于" },
  { value: "gt", label: "大于" },
  { value: "gte", label: "大于等于" },
  { value: "lt", label: "小于" },
  { value: "lte", label: "小于等于" },
  { value: "is_null", label: "为空" },
  { value: "not_null", label: "不为空" },
];
let draftSequence = 0;
function draftKey() {
  return `row-policy-draft-${++draftSequence}`;
}
export function isSetOperator(operator: PolicyOperator) {
  return operator === "in" || operator === "not_in";
}
export function hasLiteralValue(condition: ConditionDraft) {
  return (
    condition.value_source === "literal" &&
    !["is_null", "not_null"].includes(condition.operator)
  );
}
export function operatorsFor(condition: ConditionDraft) {
  return policyOperators.filter(({ value }) =>
    condition.value_source !== "literal"
      ? ["eq", "ne", "in", "not_in"].includes(value)
      : condition.value_kind !== "boolean" ||
        !["gt", "gte", "lt", "lte"].includes(value),
  );
}
export function newCondition(column?: PolicyColumn): ConditionDraft {
  return {
    key: draftKey(),
    field: column?.name ?? "",
    operator: "eq",
    value_source: "literal",
    value_kind: /bool/i.test(column?.type ?? "")
      ? "boolean"
      : /int|decimal|numeric|real|float|double|number/i.test(column?.type ?? "")
        ? "number"
        : "text",
    value_text: "",
    values_text: [],
    value_boolean: null,
    values_boolean: [],
  };
}
export function newRule(column?: PolicyColumn): RuleDraft {
  return {
    key: draftKey(),
    subject_type: "all",
    subject_ids: [],
    conditions: [newCondition(column)],
  };
}
export function conditionDraft(
  value: PolicyCondition,
  columns: PolicyColumn[],
): ConditionDraft {
  const draft = newCondition(
    columns.find((column) => column.name === value.field),
  );
  Object.assign(draft, {
    field: value.field,
    operator: value.operator,
    value_source: value.value_source,
  });
  const values = Array.isArray(value.value)
    ? value.value
    : value.value === undefined
      ? []
      : [value.value];
  if (values.length) {
    draft.value_kind =
      typeof values[0] === "boolean"
        ? "boolean"
        : typeof values[0] === "number"
          ? "number"
          : "text";
    draft.value_text = String(values[0]);
    draft.values_text = values.map(String);
    if (typeof values[0] === "boolean") draft.value_boolean = values[0];
    draft.values_boolean = values.filter(
      (item): item is boolean => typeof item === "boolean",
    );
  }
  return draft;
}
export function policyDraft(
  value: RowPolicy | undefined,
  columns: PolicyColumn[],
): RowPolicyDraft {
  return {
    mode: value?.mode ?? "all",
    rules: (value?.rules ?? []).map((rule) => ({
      key: draftKey(),
      subject_type: rule.subject_type,
      subject_ids: [...rule.subject_ids],
      conditions: rule.conditions.map((condition) =>
        conditionDraft(condition, columns),
      ),
    })),
  };
}
function canonicalNumber(value: string) {
  const [base = "", exponent = "0"] = value.toLowerCase().split("e");
  const negative = base.startsWith("-");
  const unsigned = base.replace(/^[+-]/, "");
  let digits = unsigned.replace(".", "").replace(/^0+/, "");
  let power = Number(exponent) - (unsigned.split(".")[1]?.length ?? 0);
  if (!digits) return "0";
  while (digits.endsWith("0")) {
    digits = digits.slice(0, -1);
    power++;
  }
  return `${negative ? "-" : ""}${digits}e${power}`;
}
function numericValue(text: string): number {
  const trimmed = text.trim();
  const number = Number(trimmed);
  if (
    !/^[+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(trimmed) ||
    !Number.isFinite(number)
  )
    throw new Error("请输入有效数字");
  if (canonicalNumber(trimmed) !== canonicalNumber(String(number)))
    throw new Error("数字精度超出范围，请选择文本类型保留原值");
  return number;
}
export function literalValue(
  condition: ConditionDraft,
): PolicyScalar | PolicyScalar[] {
  if (condition.value_kind === "boolean") {
    if (isSetOperator(condition.operator)) {
      if (!condition.values_boolean.length) throw new Error("请选择匹配值");
      return [...condition.values_boolean];
    }
    if (condition.value_boolean === null) throw new Error("请选择匹配值");
    return condition.value_boolean;
  }
  const values = isSetOperator(condition.operator)
    ? condition.values_text
    : [condition.value_text];
  if (!values.length || values.length > 100)
    throw new Error("请填写1至100个匹配值");
  const parsed = values.map((value) => {
    if (condition.value_kind === "number") return numericValue(value);
    // 空文本是合法的精确匹配值，不能自动trim或改成null。
    return value;
  });
  return isSetOperator(condition.operator) ? parsed : parsed[0]!;
}
export function policyPayload(draft: RowPolicyDraft): {
  mode: "all" | "rules";
  rules: PolicyRule[];
} {
  return {
    mode: draft.mode,
    rules:
      draft.mode === "all"
        ? []
        : draft.rules.map((rule) => ({
            subject_type: rule.subject_type,
            subject_ids:
              rule.subject_type === "all" ? [] : [...rule.subject_ids],
            conditions: rule.conditions.map((condition) => ({
              field: condition.field,
              operator: condition.operator,
              value_source: ["is_null", "not_null"].includes(condition.operator)
                ? "literal"
                : condition.value_source,
              ...(hasLiteralValue(condition)
                ? { value: literalValue(condition) }
                : {}),
            })),
          })),
  };
}
export function policyErrors(
  draft: RowPolicyDraft,
  columns: PolicyColumn[],
  subjects: PolicySubjects,
) {
  const errors: Record<string, string> = {};
  if (draft.mode === "all") return errors;
  if (draft.rules.length > 50) errors.rules = "最多添加50条访问规则";
  draft.rules.forEach((rule, index) => {
    const prefix = `rules.${index}`;
    const options =
      rule.subject_type === "role" ? subjects.roles : subjects.users;
    if (
      rule.subject_type !== "all" &&
      (!rule.subject_ids.length ||
        rule.subject_ids.length > 100 ||
        rule.subject_ids.some((id) => !options.some((item) => item.id === id)))
    )
      errors[`${prefix}.subject_ids`] = "请选择当前工作区内的适用对象";
    if (!rule.conditions.length || rule.conditions.length > 20)
      errors[`${prefix}.conditions`] = "请添加1至20个条件";
    rule.conditions.forEach((condition, conditionIndex) => {
      const path = `${prefix}.conditions.${conditionIndex}`;
      if (!columns.some((column) => column.name === condition.field))
        errors[`${path}.field`] = "请选择当前表的字段";
      if (
        !operatorsFor(condition).some(
          (item) => item.value === condition.operator,
        )
      )
        errors[`${path}.operator`] = "请选择适用的比较方式";
      if (hasLiteralValue(condition)) {
        try {
          literalValue(condition);
        } catch (error) {
          errors[`${path}.value`] = (error as Error).message;
        }
      }
    });
  });
  return errors;
}
export function policyFingerprint(draft: RowPolicyDraft) {
  // 草稿可能暂未有效；比较控件状态而非提前转成数值，避免编辑过程中抛错。
  return JSON.stringify(
    { mode: draft.mode, rules: draft.mode === "all" ? [] : draft.rules },
    (key, value) => (key === "key" ? undefined : value),
  );
}
