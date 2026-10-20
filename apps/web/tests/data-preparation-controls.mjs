/** 只在Vue内存中检查DTO、字段演进和操作状态；零HTTP/浏览器/数据库/模型。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { ref, shallowRef, computed } from "vue";
let checks = 0;
function check(callback) {
  callback();
  checks++;
}
function url(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}
function transpile(source, fileName) {
  const result = ts.transpileModule(source, {
    fileName,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length ?? 0, 0));
  return result.outputText;
}
const sourceRoot = new URL("../src/modules/data/", import.meta.url);
const helperUrl = url(
  transpile(
    await readFile(new URL("dataPreparation.ts", sourceRoot), "utf8"),
    "dataPreparation.ts",
  ),
);
const data = await import(helperUrl);
const columns = [
  "line",
  "temperature",
  "actual",
  "expected",
  "unit",
  "date",
  "enabled",
];
function operation(type, patch = {}) {
  return { ...data.newOperation(), type, ...patch };
}
function errorAt(action, path) {
  check(() =>
    assert.throws(
      action,
      (error) =>
        error instanceof data.PreparationError && Boolean(error.fields[path]),
    ),
  );
}
function payload(steps) {
  return data.processingPayload("新本机夹具", "query-one", steps, columns);
}

check(() =>
  assert.deepEqual(
    payload([operation("select", { fields: ["line", "actual"] })]).operations,
    [{ type: "select", fields: ["line", "actual"] }],
  ),
);
check(() =>
  assert.deepEqual(
    payload([operation("drop_null", { field: "actual" })]).operations,
    [{ type: "drop_null", field: "actual" }],
  ),
);
check(() =>
  assert.deepEqual(
    payload([operation("deduplicate", { fields: ["line"] })]).operations,
    [{ type: "deduplicate", fields: ["line"] }],
  ),
);
check(() =>
  assert.deepEqual(payload([operation("deduplicate")]).operations, [
    { type: "deduplicate" },
  ]),
);
errorAt(() => payload([operation("select")]), "operations.0.fields");
errorAt(
  () => payload([operation("drop_null", { field: "removed" })]),
  "operations.0.field",
);
const rename = operation("rename", {
  field: "actual",
  output_field: "production",
});
const cast = operation("cast", {
  field: "production",
  output_field: "count",
  data_type: "integer",
  on_error: "null",
});
check(() =>
  assert.deepEqual(data.operationColumns(columns, [rename, cast]), [
    "line",
    "temperature",
    "production",
    "expected",
    "unit",
    "date",
    "enabled",
    "count",
  ]),
);
check(() =>
  assert.equal(payload([rename, cast]).operations[1].on_error, "null"),
);
errorAt(
  () => payload([rename, operation("drop_null", { field: "actual" })]),
  "operations.1.field",
);
errorAt(
  () => payload([operation("rename", { field: "line", output_field: "unit" })]),
  "operations.0.output_field",
);
const condition = {
  ...data.newFilter(),
  field: "temperature",
  data_type: "decimal",
  value: "90071992547409931234567890.0001",
};
check(() =>
  assert.equal(
    payload([operation("filter", { conditions: [condition] })]).operations[0]
      .conditions[0].value,
    condition.value,
  ),
);
for (const value of [
  "0",
  "-0.00100",
  "1e-20",
  "90071992547409931234567890.0001",
])
  check(() => assert.equal(data.validDecimal(value), true));
for (const value of ["", "NaN", "Infinity", "1;DROP TABLE a", "1.2.3"])
  check(() => assert.equal(data.validDecimal(value), false));
errorAt(
  () =>
    payload([
      operation("filter", { conditions: [{ ...condition, value: "NaN" }] }),
    ]),
  "operations.0.conditions.0.value",
);
check(() =>
  assert.deepEqual(
    payload([
      operation("filter", {
        conditions: [{ ...condition, operator: "in", value: ["0", "1.000"] }],
      }),
    ]).operations[0].conditions[0].value,
    ["0", "1.000"],
  ),
);
check(() =>
  assert.equal(
    "value" in
      payload([
        operation("filter", {
          conditions: [{ ...condition, operator: "is_null", value: "stale" }],
        }),
      ]).operations[0].conditions[0],
    false,
  ),
);
errorAt(
  () =>
    payload([
      operation("filter", {
        conditions: [{ ...condition, operator: "contains" }],
      }),
    ]),
  "operations.0.conditions.0.operator",
);
const expression = data.newExpression();
expression.args[0].field = "actual";
expression.args[1] = {
  ...data.newExpression("literal"),
  literal_type: "decimal",
  value: "0.000000000000000001",
};
const derive = operation("derive", { output_field: "calculated", expression });
check(() =>
  assert.deepEqual(payload([derive]).operations[0].expression, {
    op: "add",
    args: [{ field: "actual" }, { value: "0.000000000000000001" }],
  }),
);
check(() =>
  assert.equal(
    "literal_type" in payload([derive]).operations[0].expression.args[1],
    false,
  ),
);
errorAt(
  () =>
    payload([
      operation("derive", {
        output_field: "v",
        expression: { ...expression, args: expression.args.slice(0, 1) },
      }),
    ]),
  "operations.0.expression.args",
);
const nullExpression = {
  ...data.newExpression(),
  op: "coalesce",
  args: [
    data.newExpression("null"),
    { ...data.newExpression("literal"), literal_type: "boolean", value: false },
  ],
};
check(() =>
  assert.deepEqual(
    payload([
      operation("derive", {
        output_field: "value",
        expression: nullExpression,
      }),
    ]).operations[0].expression,
    { op: "coalesce", args: [{ value: null }, { value: false }] },
  ),
);
const aggregate = {
  ...data.newAggregate("total"),
  function: "sum",
  field: "actual",
  unit_field: "unit",
};
const grouped = operation("aggregate", {
  group_by: ["line"],
  aggregations: [aggregate],
});
check(() =>
  assert.deepEqual(data.operationColumns(columns, [grouped]), [
    "line",
    "total",
  ]),
);
check(() =>
  assert.equal(
    payload([grouped]).operations[0].aggregations[0].unit_field,
    "unit",
  ),
);
errorAt(
  () =>
    payload([
      operation("aggregate", {
        group_by: ["line"],
        aggregations: [{ ...aggregate, name: "line" }],
      }),
    ]),
  "operations.0.aggregations.0.name",
);
check(() =>
  assert.deepEqual(
    payload([
      operation("aggregate", {
        aggregations: [{ ...data.newAggregate("n"), function: "count" }],
      }),
    ]).operations[0].aggregations[0],
    { name: "n", function: "count", data_type: "decimal" },
  ),
);
errorAt(
  () =>
    payload(
      Array.from({ length: 21 }, () =>
        operation("drop_null", { field: "actual" }),
      ),
    ),
  "operations",
);
let deep = { ...data.newExpression("field"), field: "actual" };
for (let i = 0; i < 9; i++)
  deep = { ...data.newExpression(), op: "coalesce", args: [deep] };
check(() => assert.equal(data.expressionNodeCount(deep), 10));
check(() =>
  assert.throws(
    () =>
      payload([operation("derive", { output_field: "v", expression: deep })]),
    data.PreparationError,
  ),
);

for (const type of [
  "required",
  "unique",
  "range",
  "enum",
  "type",
  "length",
  "compare",
]) {
  const rule = {
    ...data.newQualityRule(),
    type,
    field: "actual",
    minimum: "0.000000000000001",
    values: ["A", "B"],
    min_length: 0,
    other_field: "expected",
  };
  const dto = data.qualityPayload("query-one", [], [], [rule], columns)
    .rules[0];
  check(() => assert.equal(dto.type, type));
  check(() => assert.equal("key" in dto, false));
  if (type === "range") check(() => assert.equal(dto.minimum, rule.minimum));
  if (type !== "range") check(() => assert.equal("minimum" in dto, false));
}
check(() =>
  assert.deepEqual(
    data.qualityPayload("query-one", ["actual"], ["line"], [], columns)
      .required_fields,
    ["actual"],
  ),
);
errorAt(() => data.qualityPayload("query-one", [], [], [], columns), "rules");
errorAt(
  () =>
    data.qualityPayload(
      "query-one",
      [],
      [],
      [{ ...data.newQualityRule(), type: "range", field: "actual" }],
      columns,
    ),
  "rules.0.minimum",
);
errorAt(
  () =>
    data.qualityPayload(
      "query-one",
      [],
      [],
      Array.from({ length: 51 }, () => ({
        ...data.newQualityRule(),
        field: "actual",
      })),
      columns,
    ),
  "rules",
);
const metric = data.metricDraft({
  kind: "ratio",
  scale: "100.000",
  unit: "%",
  group_by: ["line"],
});
metric.numerator.field = "actual";
metric.denominator.field = "expected";
const metricDto = data.metricPayload(metric, columns);
check(() => assert.equal(metricDto.scale, "100.000"));
check(() => assert.equal("aggregation" in metricDto, false));
check(() => assert.equal("key" in metricDto.numerator, false));
errorAt(
  () => data.metricPayload({ ...metric, scale: "NaN" }),
  "definition.scale",
);
check(() =>
  assert.deepEqual(
    data.preparationErrors(
      { path: "operations.0.field", row_number: "3" },
      "字段无效",
    ),
    { "operations.0.field": "第3行：字段无效" },
  ),
);
for (const [value, expected] of [
  [null, "—"],
  ["0", "0"],
  ["900719925474099312345.0001", "900719925474099312345.0001"],
  [false, "否"],
])
  check(() => assert.equal(data.displayScalar(value), expected));

// 编译全部新增SFC，并直接触发实际控件更新函数，覆盖props不变与原子更新。
const vueUrl = import.meta.resolve("vue");
const emptyComponentUrl = url("export default {};");
async function sfc(name) {
  const source = await readFile(new URL(name, sourceRoot), "utf8");
  const parsed = parse(source, { filename: name });
  check(() => assert.deepEqual(parsed.errors, []));
  const script = compileScript(parsed.descriptor, { id: name });
  const template = compileTemplate({
    source: parsed.descriptor.template.content,
    filename: name,
    id: name,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
  let compiled = transpile(script.content, name + ".ts");
  compiled = compiled
    .replaceAll('"vue"', JSON.stringify(vueUrl))
    .replaceAll("'vue'", JSON.stringify(vueUrl))
    .replaceAll('"./dataPreparation"', JSON.stringify(helperUrl))
    .replace(
      /from "[^"\n]+\.vue"/g,
      `from ${JSON.stringify(emptyComponentUrl)}`,
    );
  return { source, compiled };
}
for (const name of [
  "DataFieldSelect.vue",
  "DataScalarInput.vue",
  "AggregateDefinitionForm.vue",
  "StructuredExpressionEditor.vue",
  "DataFilterCondition.vue",
  "ProcessingOperationForm.vue",
  "PreparationResult.vue",
  "ProcessingWorkspace.vue",
  "DataQualityRuleForm.vue",
  "QualityCheckResults.vue",
  "DataQualityWorkspace.vue",
  "MetricDefinitionForm.vue",
  "BusinessMetricList.vue",
  "BusinessMetricWorkspace.vue",
])
  await sfc(name);
const expressionComponent = (
  await import(url((await sfc("StructuredExpressionEditor.vue")).compiled))
).default;
const expressionProps = {
  modelValue: structuredClone(expression),
  columns,
  path: "expression",
  errors: {},
  depth: 1,
  root: true,
};
const emitted = [];
const controls = expressionComponent.setup(expressionProps, {
  expose() {},
  emit(event, value) {
    emitted.push([event, value]);
  },
});
controls.changeOperation("round");
check(() => assert.equal(emitted.at(-1)[1].args.length, 1));
check(() => assert.equal(expressionProps.modelValue.op, "add"));
controls.changeLiteralType("boolean");
check(() => assert.equal(emitted.at(-1)[1].literal_type, "boolean"));
check(() => assert.equal(emitted.at(-1)[1].value, false));
const conditionComponent = (
  await import(url((await sfc("DataFilterCondition.vue")).compiled))
).default;
const conditionProps = {
  modelValue: condition,
  columns,
  path: "filter",
  errors: {},
};
const conditionControls = conditionComponent.setup(conditionProps, {
  expose() {},
  emit(event, value) {
    emitted.push([event, value]);
  },
});
conditionControls.update("operator", "in");
check(() => assert.deepEqual(emitted.at(-1)[1].value, []));
check(() => assert.equal(conditionProps.modelValue.value, condition.value));

// 指标会话用Mock保留版本、SQL参数，失败留在编辑态且不触发执行重试。
const state = { requests: [], fail: false, confirmations: [] };
const fixture = {
  id: "metric-one",
  name: "产量",
  revision: 4,
  sql: "SELECT actual FROM source",
  parameters: { period: "本月" },
  datasource_id: "source-one",
  definition: {
    version: 1,
    kind: "aggregate",
    group_by: [],
    aggregation: {
      name: "指标值",
      function: "sum",
      field: "actual",
      data_type: "decimal",
    },
    unit: "件",
  },
};
const apiMock = {
  async get(path) {
    state.requests.push({ method: "GET", path });
    return path.startsWith("/business-metrics/")
      ? structuredClone(fixture)
      : { items: [], total: 0 };
  },
  async patch(path, body) {
    state.requests.push({
      method: "PATCH",
      path,
      body: JSON.parse(JSON.stringify(body)),
    });
    if (state.fail)
      throw Object.assign(new Error("版本已变更"), {
        fields: { path: "definition.aggregation.field" },
      });
    return { ...fixture, ...body, revision: 5 };
  },
  async post(path, body) {
    state.requests.push({
      method: "POST",
      path,
      body: JSON.parse(JSON.stringify(body)),
    });
    return {
      id: "job-one",
      revision: 1,
      result: {
        rows: [
          {
            value: null,
            status: "zero_denominator",
            numerator: "3",
            denominator: "0",
          },
        ],
        columns: ["value", "status", "numerator", "denominator"],
      },
    };
  },
  async delete(path, revision) {
    state.requests.push({ method: "DELETE", path, revision });
  },
};
function operationMock() {
  const busy = shallowRef(false),
    error = shallowRef(""),
    fields = ref({});
  return {
    busy,
    error,
    fields,
    async run(action) {
      if (busy.value) return;
      busy.value = true;
      error.value = "";
      fields.value = {};
      try {
        return await action();
      } catch (failure) {
        error.value = failure.message;
        fields.value = failure.fields ?? {};
      } finally {
        busy.value = false;
      }
    },
  };
}
globalThis.__dataPreparationPureTest = {
  api: apiMock,
  useOperation: operationMock,
  useCollection() {
    return {
      items: ref([]),
      total: shallowRef(0),
      page: shallowRef(1),
      size: shallowRef(20),
      query: shallowRef(""),
      loading: shallowRef(false),
      async load() {
        state.requests.push({ method: "LIST" });
      },
    };
  },
  usePreparationQueries() {
    const queries = ref([]),
      queryId = shallowRef("");
    return {
      queries,
      queryId,
      columns: computed(() => columns),
      async load() {
        state.requests.push({ method: "QUERIES" });
      },
    };
  },
  ElMessageBox: {
    async confirm(message) {
      state.confirmations.push(message);
    },
  },
};
const stubUrl = url(
  "export const {api,useOperation,useCollection,usePreparationQueries,ElMessageBox}=globalThis.__dataPreparationPureTest;",
);
let composable = transpile(
  await readFile(new URL("useBusinessMetrics.ts", sourceRoot), "utf8"),
  "useBusinessMetrics.ts",
);
for (const specifier of [
  "element-plus",
  "../../api/client",
  "../../composables/useCollection",
  "../../composables/useOperation",
  "./usePreparationQueries",
])
  composable = composable.replaceAll(
    JSON.stringify(specifier),
    JSON.stringify(stubUrl),
  );
composable = composable
  .replaceAll('"vue"', JSON.stringify(vueUrl))
  .replaceAll('"./dataPreparation"', JSON.stringify(helperUrl));
const { useBusinessMetrics } = await import(url(composable));
const editor = useBusinessMetrics();
await editor.load(false);
check(() =>
  assert.equal(
    state.requests.some((r) => r.method === "QUERIES"),
    false,
  ),
);
await editor.edit(fixture);
editor.name.value = "新产量";
await editor.save();
const saved = state.requests.find((r) => r.method === "PATCH");
check(() =>
  assert.ok(
    saved,
    JSON.stringify({
      fields: editor.errors.value,
      error: editor.op.error.value,
      selected: editor.selected.value,
      requests: state.requests,
    }),
  ),
);
check(() => assert.equal(saved.body.revision, 4));
check(() => assert.equal(saved.body.sql, fixture.sql));
check(() => assert.deepEqual(saved.body.parameters, fixture.parameters));
check(() => assert.equal("description" in saved.body, false));
check(() => assert.equal(editor.dialog.value, false));
await editor.edit(fixture);
state.fail = true;
await editor.save();
check(() => assert.equal(editor.dialog.value, true));
check(() =>
  assert.equal(
    editor.errors.value["definition.aggregation.field"],
    "版本已变更",
  ),
);
state.fail = false;
editor.startExecution(fixture);
editor.queries.queryId.value = "query-one";
await editor.execute();
const executed = state.requests.find((r) => r.method === "POST");
check(() =>
  assert.deepEqual(executed.body, { query_id: "query-one", revision: 4 }),
);
check(() => assert.equal(editor.result.value.result.rows[0].value, null));
check(() => assert.equal(editor.result.value.result.rows[0].denominator, "0"));
check(() => assert.equal(editor.executing.value, null));
check(() =>
  assert.equal(
    state.requests.some((r) => r.path === "/queries"),
    false,
  ),
);
delete globalThis.__dataPreparationPureTest;
process.stdout.write(
  JSON.stringify({
    passed: true,
    checks,
    sfc_count: 14,
    http_requests: 0,
    sql_queries: 0,
    model_calls: 0,
  }) + "\n",
);
