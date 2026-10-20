/** 图运行纯验收：内存Vue宿主与Mock账本，不调用实际执行器、服务或模型。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import {
  createRenderer,
  effectScope,
  h,
  nextTick,
  reactive,
  ref,
  shallowRef,
} from "vue";
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const root = new URL("../src/modules/flows/", import.meta.url);
const asUrl = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const vueUrl = import.meta.resolve("vue");
const requests = [];
class ApiError extends Error {
  constructor(status, message, fields = {}) {
    super(message);
    Object.assign(this, { status, fields });
  }
}
let respond = async () => ({ items: [], total: 0 });
globalThis.__graphUiTest = {
  ApiError,
  async request(path, options = {}) {
    requests.push({ path, ...options });
    return respond(path, options);
  },
  ElMessageBox: {
    async confirm() {
      return true;
    },
  },
  useOperation() {
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
        try {
          return await action();
        } catch (failure) {
          if (failure !== "cancel")
            error.value = failure instanceof Error ? failure.message : "失败";
        } finally {
          busy.value = false;
        }
      },
    };
  },
};
const apiUrl = asUrl(
  "export const {ApiError,request}=globalThis.__graphUiTest;",
);
const stubUrl = asUrl(
  "export const {ElMessageBox,useOperation}=globalThis.__graphUiTest; export const Delete={},Plus={},VideoPlay={};export const useSession=()=>({value:null,can:()=>false});export default {};",
);
const cache = new Map();
function transpile(source, filename) {
  const result = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length ?? 0, 0));
  return result.outputText
    .replaceAll('"vue"', JSON.stringify(vueUrl))
    .replaceAll("'vue'", JSON.stringify(vueUrl));
}
async function moduleUrl(filename) {
  if (cache.has(filename)) return cache.get(filename);
  let source = transpile(
    await readFile(new URL(filename, root), "utf8"),
    filename,
  ).replaceAll('"../../api/client"', JSON.stringify(apiUrl));
  const imports = [...source.matchAll(/from "(\.\/[^"\n]+)"/g)].map(
    (match) => match[1],
  );
  for (const path of new Set(imports))
    source = source.replaceAll(
      JSON.stringify(path),
      JSON.stringify(await moduleUrl(path.slice(2) + ".ts")),
    );
  const url = asUrl(source);
  cache.set(filename, url);
  return url;
}
const {
  graphFields,
  initialGraphInputs,
  validateGraphInputs,
  initialSchemaValue,
} = await import(await moduleUrl("graphInputs.ts"));
const schema = {
  type: "object",
  additionalProperties: false,
  required: ["enabled", "amount", "tags"],
  properties: {
    enabled: { type: "boolean" },
    amount: { type: "integer", minimum: 0, maximum: 10 },
    tags: {
      type: "array",
      items: { type: "string", minLength: 1 },
      maxItems: 3,
    },
  },
};
const definition = {
  format: "graph",
  engine: "graphon",
  engine_version: "0.8.0",
  root_node_id: "start",
  variables: [{ name: "not-a-user-field" }],
  graph: {
    nodes: [
      {
        id: "start",
        data: {
          type: "start",
          variables: [
            {
              variable: "count",
              label: "数量",
              type: "number",
              required: true,
              default: 0,
            },
            {
              variable: "enabled",
              label: "启用",
              type: "checkbox",
              required: true,
              default: false,
            },
            {
              variable: "line",
              label: "产线",
              type: "select",
              options: ["甲", "乙"],
              default: "甲",
            },
            {
              variable: "note",
              label: "备注",
              type: "paragraph",
              max_length: 20,
              default: "保持原文",
            },
            {
              variable: "config",
              label: "配置",
              type: "json_object",
              required: true,
              json_schema: schema,
              default: { enabled: false, amount: 0, tags: [] },
            },
          ],
        },
      },
      { id: "end", data: { type: "end", title: "结束" } },
    ],
    edges: [{ source: "start", target: "end" }],
  },
};
const fields = graphFields(definition),
  initial = initialGraphInputs(fields);
check(() => assert.equal(fields.length, 5));
check(() =>
  assert.equal(
    fields.some((item) => item.key === "not-a-user-field"),
    false,
  ),
);
check(() => assert.equal(initial.count, 0));
check(() => assert.equal(initial.enabled, false));
check(() =>
  assert.deepEqual(initial.config, { enabled: false, amount: 0, tags: [] }),
);
check(() => assert.deepEqual(validateGraphInputs(fields, initial).errors, {}));
check(() =>
  assert.deepEqual(validateGraphInputs(fields, initial).inputs.config.tags, []),
);
initial.config.tags.push("新值");
check(() =>
  assert.deepEqual(
    definition.graph.nodes[0].data.variables[4].default.tags,
    [],
  ),
);
const invalid = validateGraphInputs(fields, {
  count: true,
  enabled: "false",
  line: "丙",
  note: "字".repeat(21),
  config: { enabled: false, amount: 0, tags: [""] },
});
for (const key of [
  "inputs.count",
  "inputs.enabled",
  "inputs.line",
  "inputs.note",
  "inputs.config.tags.0",
])
  check(() => assert.ok(invalid.errors[key]));
const nestedInvalid = validateGraphInputs(fields, {
  count: 0,
  enabled: false,
  config: { enabled: false, amount: 1.5, tags: [], extra: true },
});
check(() => assert.ok(nestedInvalid.errors["inputs.config.amount"]));
check(() => assert.ok(nestedInvalid.errors["inputs.config"]));
const missing = validateGraphInputs(fields, {});
check(() => assert.ok(missing.errors["inputs.count"]));
check(() => assert.ok(missing.errors["inputs.enabled"]));
const unsafe = validateGraphInputs(fields, {
  ...initial,
  count: Number.MAX_SAFE_INTEGER + 1,
});
check(() => assert.match(unsafe.errors["inputs.count"], /安全/));
const optionalNull = validateGraphInputs(
  [
    {
      key: "optional",
      label: "可选",
      required: false,
      schema: { type: "string" },
    },
  ],
  { optional: null },
);
check(() => assert.deepEqual(optionalNull.inputs, { optional: null }));
check(() => assert.deepEqual(optionalNull.errors, {}));
const nullDefault = initialSchemaValue({
  type: "object",
  default: { value: 0, flag: false, list: [] },
});
check(() => assert.deepEqual(nullDefault, { value: 0, flag: false, list: [] }));
function withVariable(variable) {
  const copied = structuredClone(definition);
  copied.graph.nodes[0].data.variables = [variable];
  return copied;
}
for (const json_schema of [
  { type: "object", properties: { bad: { $ref: "#/definitions/item" } } },
  { type: "object", oneOf: [] },
  { type: "array", items: { type: "string" } },
  { type: "object", properties: { x: { type: ["null", "string"] } } },
]) {
  const parsed = graphFields(
    withVariable({
      variable: "complex",
      label: "复杂项",
      type: "json_object",
      json_schema,
    }),
  );
  check(() => assert.ok(parsed[0].issue));
  check(() =>
    assert.ok(validateGraphInputs(parsed, {}).errors["inputs.complex"]),
  );
}
const proto = validateGraphInputs(
  [
    {
      key: "__proto__",
      label: "字段",
      required: true,
      schema: { type: "string" },
    },
  ],
  JSON.parse('{"__proto__":"值"}'),
);
check(() =>
  assert.equal(Object.getPrototypeOf(proto.inputs), Object.prototype),
);
check(() =>
  assert.equal(
    Object.prototype.hasOwnProperty.call(proto.inputs, "__proto__"),
    true,
  ),
);
check(() => assert.equal(proto.inputs.__proto__, "值"));

const { useWorkflowRunIntent } = await import(
  await moduleUrl("useWorkflowRunIntent.ts")
);
const runResult = (extra = {}) => ({
  id: "run-one",
  status: "succeeded",
  input: "",
  output: "",
  steps: [
    {
      index: 1,
      type: "graph_result",
      name: "图运行",
      status: "ok",
      output: "",
    },
  ],
  format: "graph",
  inputs: { count: 0, enabled: false },
  outputs: { count: 0, enabled: false, rows: [] },
  started_at: "2026-10-21T06:00:00+00:00",
  definition_snapshot: definition,
  ...extra,
});
let id = "workflow";
const runScope = effectScope(),
  intent = runScope.run(() => useWorkflowRunIntent(() => id));
respond = async () => runResult();
await intent.start({ count: 0, enabled: false, config: { tags: [] } });
const firstPost = requests.at(-1),
  firstKey = firstPost.headers["Idempotency-Key"];
check(() => assert.equal(firstPost.timeoutMs, 100000));
check(() =>
  assert.deepEqual(JSON.parse(firstPost.body), {
    inputs: { count: 0, enabled: false, config: { tags: [] } },
  }),
);
check(() => assert.ok(firstKey));
check(() => assert.equal(intent.currentRun.value.outputs.count, 0));
check(() => assert.equal(intent.currentRun.value.outputs.enabled, false));
respond = async () => {
  throw new ApiError(422, "请检查输入", { "inputs.count": "请输入数量" });
};
await intent.start({ count: "bad" });
check(() =>
  assert.equal(intent.current.value.fields["inputs.count"], "请输入数量"),
);
check(() => assert.equal(intent.current.value.status, "failed"));
respond = async () => {
  throw new ApiError(408, "结果尚未确认");
};
await intent.start({ count: 0 });
const key = intent.current.value.key,
  unknownRequests = requests.length;
await intent.start({ count: 1 });
check(() => assert.equal(requests.length, unknownRequests));
check(() => assert.equal(intent.current.value.key, key));
respond = async (path) =>
  path.includes("idempotency_key=")
    ? { items: [{ id: "run-one" }], total: 1 }
    : runResult();
await intent.verify();
check(() =>
  assert.equal(requests.at(-2).path.includes(encodeURIComponent(key)), true),
);
check(() =>
  assert.equal(
    requests.slice(-2).every((item) => item.method !== "POST"),
    true,
  ),
);
check(() => assert.equal(intent.currentRun.value.outputs.enabled, false));
respond = async () => {
  throw new ApiError(403, "权限已撤销");
};
await intent.verify();
check(() => assert.equal(intent.currentRun.value, null));
check(() => assert.equal(intent.permissionError.value, "权限已撤销"));
runScope.stop();

const { eventRows } = await import(await moduleUrl("workflowEvents.ts"));
function event(
  cursor,
  execution = "execution-a",
  run_id = "run-one",
  event_type = "NodeRunStartedEvent",
) {
  return {
    cursor,
    run_id,
    event_sequence: cursor,
    event_type,
    data: {
      id: execution,
      node_execution_id: "different-extension-id",
      node_id: "same-node",
      node_title: "处理节点",
      outputs: { value: 0, ok: false },
    },
    created_at: "2026-10-21T06:00:00+00:00",
  };
}
const rows = eventRows(
  [
    event(1),
    event(2, "execution-a", "run-one", "NodeRunSucceededEvent"),
    event(3, "execution-b"),
    event(4, "execution-b", "run-one", "NodeRunSucceededEvent"),
  ],
  [],
);
check(() =>
  assert.deepEqual(
    rows.map((item) => item.attempt),
    [1, 1, 2, 2],
  ),
);
check(() => assert.equal(rows[0].executionId, "execution-a"));
check(() => assert.equal(rows[0].facts.outputs.ok, false));
const { useWorkflowEvents } = await import(
  await moduleUrl("useWorkflowEvents.ts")
);
const currentRunId = shallowRef("run-one"),
  denied = [],
  eventScope = effectScope();
respond = async () => ({
  items: [event(2), event(3)],
  next_cursor: 3,
  has_more: true,
});
const events = eventScope.run(() =>
  useWorkflowEvents(
    () => currentRunId.value,
    (message) => denied.push(message),
  ),
);
async function settle() {
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
await settle();
check(() => assert.equal(events.cursor.value, 3));
check(() => assert.equal(events.hasMore.value, true));
const eventCount = requests.length;
await settle();
check(() => assert.equal(requests.length, eventCount));
respond = async () => ({
  items: [event(5, "execution-b")],
  next_cursor: 5,
  has_more: false,
});
await events.load();
check(() => assert.match(requests.at(-1).path, /after=3&limit=100$/));
check(() => assert.equal(events.items.value.length, 3));
check(() => assert.equal(events.hasMore.value, false));
respond = async () => ({ items: [], next_cursor: 5, has_more: true });
await events.load();
check(() => assert.match(events.error.value, /分页/));
respond = async () => {
  throw new ApiError(403, "记录已失权");
};
await events.load();
check(() => assert.equal(events.items.value.length, 0));
check(() => assert.deepEqual(denied, ["记录已失权"]));
function pending() {
  let resolve;
  const promise = new Promise((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
const stale = pending();
respond = () => stale.promise;
const pendingEvents = events.load();
const staleSignal = requests.at(-1).signal;
eventScope.stop();
stale.resolve({ items: [event(6)], next_cursor: 6, has_more: false });
await pendingEvents;
check(() => assert.equal(staleSignal.aborted, true));
check(() => assert.equal(events.items.value.length, 0));

const renderer = createRenderer({
  createElement: (tag) => ({ tag, children: [] }),
  createText: (text) => ({ text }),
  createComment: (text) => ({ text }),
  setElementText(node, text) {
    node.text = text;
  },
  setText(node, text) {
    node.text = text;
  },
  insert(child, parent) {
    parent.children.push(child);
    child.parent = parent;
  },
  remove() {},
  parentNode: (node) => node.parent,
  nextSibling: () => null,
  patchProp() {},
});
const compiled = new Map();
async function component(filename) {
  if (compiled.has(filename)) return compiled.get(filename);
  const source = await readFile(new URL(filename, root), "utf8"),
    parsed = parse(source, { filename });
  check(() => assert.deepEqual(parsed.errors, []));
  const script = compileScript(parsed.descriptor, { id: filename });
  const template = compileTemplate({
    source: parsed.descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
  let code = transpile(script.content, filename + ".ts")
    .replaceAll('"../../api/client"', JSON.stringify(apiUrl))
    .replaceAll('"element-plus"', JSON.stringify(stubUrl))
    .replaceAll('"@element-plus/icons-vue"', JSON.stringify(stubUrl))
    .replaceAll('"../../composables/useOperation"', JSON.stringify(stubUrl))
    .replaceAll('"../../stores/session"', JSON.stringify(stubUrl));
  code = code.replace(
    /from "[^"\n]+\.vue"/g,
    `from ${JSON.stringify(stubUrl)}`,
  );
  for (const path of new Set(
    [...code.matchAll(/from "(\.\/[^"\n]+)"/g)].map((match) => match[1]),
  ))
    code = code.replaceAll(
      JSON.stringify(path),
      JSON.stringify(await moduleUrl(path.slice(2) + ".ts")),
    );
  const result = { source, Comp: (await import(asUrl(code))).default };
  compiled.set(filename, result);
  return result;
}
async function mount(filename, props) {
  const { Comp } = await component(filename);
  let state;
  const emitted = [];
  const app = renderer.createApp({
    setup() {
      state = Comp.setup(props, {
        expose() {},
        emit: (...args) => emitted.push(args),
      });
      return () => h("div");
    },
  });
  app.mount({ children: [] });
  await settle();
  return { state, emitted, stop: () => app.unmount() };
}
const form = await mount(
  "GraphInputForm.vue",
  reactive({ definition, disabled: false, errors: {} }),
);
form.state.submit();
check(() => assert.equal(form.emitted[0][0], "submit"));
check(() => assert.equal(form.emitted[0][1].enabled, false));
check(() => assert.deepEqual(form.emitted[0][1].config.tags, []));
form.state.update("count", true);
const beforeBad = form.emitted.length;
form.state.submit();
check(() => assert.equal(form.emitted.length, beforeBad));
check(() => assert.ok(form.state.localErrors.value["inputs.count"]));
form.stop();
const complexForm = await mount(
  "GraphInputForm.vue",
  reactive({
    definition: withVariable({
      variable: "advanced",
      label: "复杂输入",
      type: "json_object",
      json_schema: { type: "object", oneOf: [] },
    }),
    disabled: false,
    errors: {},
  }),
);
check(() => assert.equal(complexForm.state.unsupported.value, true));
complexForm.state.submit();
check(() => assert.equal(complexForm.emitted.length, 0));
complexForm.stop();
const valueView = await mount(
  "WorkflowJsonValue.vue",
  reactive({ value: false, depth: 0 }),
);
check(() => assert.equal(valueView.state.scalar.value, "false"));
valueView.stop();

respond = async (path) =>
  path === "/workflows"
    ? {
        items: [
          {
            id: "graph-flow",
            name: "原图",
            format: "graph",
            enabled: true,
            step_count: 2,
          },
        ],
        total: 1,
      }
    : { name: "原图", description: "", definition };
const library = await mount(
  "WorkflowLibrary.vue",
  reactive({ canWrite: true, canDelete: true, canKnowledge: false }),
);
const beforeEdit = requests.length;
await library.state.openEdit();
check(() => assert.equal(requests.length, beforeEdit));
check(() => assert.equal(library.state.editorOpen.value, false));
library.state.editingId.value = "graph-flow";
library.state.form.value.name = "覆盖尝试";
await library.state.save();
check(() => assert.equal(requests.length, beforeEdit));
library.state.workflows.value[0].format = undefined;
await library.state.openEdit();
check(() => assert.equal(library.state.editingFormat.value, "graph"));
const beforeSecondSave = requests.length;
await library.state.save();
check(() => assert.equal(requests.length, beforeSecondSave));
library.stop();

respond = async (path, options) =>
  options.method === "POST"
    ? runResult()
    : path.endsWith("/runs")
      ? { items: [runResult()], total: 1 }
      : { definition };
const runner = await mount(
  "GraphWorkflowRunner.vue",
  reactive({ workflowId: "graph-flow", canRun: true }),
);
await runner.state.run({ count: 0, enabled: false });
check(() => assert.equal(runner.state.shown.value.outputs.count, 0));
check(() => assert.equal(runner.state.shown.value.outputs.enabled, false));
respond = async () => {
  throw new ApiError(403, "来源已失权");
};
await runner.state.read(runResult());
check(() => assert.equal(runner.state.definition.value, null));
check(() => assert.equal(runner.state.history.value.length, 0));
check(() => assert.equal(runner.state.shown.value, null));
runner.stop();

for (const filename of [
  "WorkflowWorkspace.vue",
  "WorkflowLibrary.vue",
  "GraphWorkflowRunner.vue",
  "GraphInputForm.vue",
  "SchemaInput.vue",
  "WorkflowJsonValue.vue",
  "WorkflowRunEvents.vue",
]) {
  const { source } = await component(filename);
  check(() => assert.equal(source.includes("v-html"), false));
  if (filename === "WorkflowWorkspace.vue") {
    check(() => assert.match(source, /:key="scope"/));
    check(() => assert.match(source, /user\.id/));
    check(() => assert.match(source, /workspace\.id/));
  }
  if (filename === "SchemaInput.vue") {
    check(() => assert.equal(source.includes("CodeEditor"), false));
    check(() => assert.match(source, /添加一项/));
  }
}
check(() =>
  assert.equal(
    requests.some((item) =>
      /completions|embeddings|datasources|queries/.test(item.path),
    ),
    false,
  ),
);
delete globalThis.__graphUiTest;
console.log(
  JSON.stringify({
    passed: true,
    assertions: checks,
    compiled_components: 7,
    actual_http: 0,
    actual_sql: 0,
    model_calls: 0,
  }),
);
