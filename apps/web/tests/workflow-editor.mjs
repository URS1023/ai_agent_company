/** 图编辑宿主纯验收：Mock传输与React挂载边界，核CAS、失权清理和卸载取消。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { createRenderer, h, nextTick, reactive } from "vue";
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const asUrl = (code) =>
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
const vueUrl = import.meta.resolve("vue");
const requests = [],
  islands = [];
let answer = async () => structuredClone(fixture);
class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
globalThis.__workflowEditorTest = {
  ApiError,
  async request(path, options = {}) {
    requests.push({ path, ...options });
    return answer(path, options);
  },
  ElMessageBox: {
    async confirm() {
      return true;
    },
  },
  mountWorkflowEditor(host, props) {
    const island = { host, props, updates: 0, unmounted: false };
    islands.push(island);
    return {
      update(value) {
        island.props = value;
        island.updates++;
      },
      unmount() {
        island.unmounted = true;
      },
    };
  },
};
const apiUrl = asUrl(
  "export const {ApiError,request}=globalThis.__workflowEditorTest;",
);
const dialogUrl = asUrl(
  "export const {ElMessageBox}=globalThis.__workflowEditorTest;",
);
const islandUrl = asUrl(
  "export const {mountWorkflowEditor,createInitialDefinition}=globalThis.__workflowEditorTest;",
);
const sourceRoot = new URL("../src/modules/flows/", import.meta.url);
function transpile(source, filename) {
  const result = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.deepEqual(result.diagnostics ?? [], []));
  return result.outputText
    .replaceAll('"vue"', JSON.stringify(vueUrl))
    .replaceAll("'vue'", JSON.stringify(vueUrl));
}
globalThis.__workflowEditorTest.createInitialDefinition = (
  await import(
    asUrl(
      transpile(
        await readFile(
          new URL("../../../packages/dify-workflow/public.ts", import.meta.url),
          "utf8",
        ),
        "public.ts",
      ),
    )
  )
).createInitialDefinition;
const requestSource = transpile(
  await readFile(new URL("useWorkflowRequests.ts", sourceRoot), "utf8"),
  "useWorkflowRequests.ts",
).replaceAll('"../../api/client"', JSON.stringify(apiUrl));
const requestUrl = asUrl(requestSource);
const source = await readFile(
  new URL("GraphWorkflowEditor.vue", sourceRoot),
  "utf8",
);
const parsed = parse(source, { filename: "GraphWorkflowEditor.vue" });
check(() => assert.deepEqual(parsed.errors, []));
const script = compileScript(parsed.descriptor, { id: "graph-editor-test" });
const template = compileTemplate({
  source: parsed.descriptor.template.content,
  filename: "GraphWorkflowEditor.vue",
  id: "graph-editor-test",
  compilerOptions: { bindingMetadata: script.bindings },
});
check(() => assert.deepEqual(template.errors, []));
const compiled = transpile(script.content, "GraphWorkflowEditor.ts")
  .replaceAll('"../../api/client"', JSON.stringify(apiUrl))
  .replaceAll('"element-plus"', JSON.stringify(dialogUrl))
  .replaceAll('"./useWorkflowRequests"', JSON.stringify(requestUrl))
  .replaceAll('"@dify-workflow"', JSON.stringify(islandUrl));
const Component = (await import(asUrl(compiled))).default;
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
const fixture = {
  id: "flow-a",
  name: "设备温度分流",
  description: "原说明",
  revision: 7,
  definition: {
    format: "graph",
    engine: "graphon",
    engine_version: "0.8.0",
    root_node_id: "start",
    retained: { value: false },
    graph: { nodes: [], edges: [], viewport: { x: 1, y: 2, zoom: 1 } },
  },
};
async function settle() {
  for (let index = 0; index < 8; index++) {
    await Promise.resolve();
    await nextTick();
  }
}
async function mount(canWrite = true, workflowId = "flow-a") {
  let state;
  const events = [];
  const app = renderer.createApp({
    setup() {
      state = Component.setup(reactive({ workflowId, canWrite }), {
        expose() {},
        emit: (...args) => events.push(args),
      });
      return () => h("div");
    },
  });
  app.mount({ children: [] });
  await settle();
  return { state, events, stop: () => app.unmount() };
}
const view = await mount();
check(() => assert.equal(requests.length, 1));
check(() => assert.equal(requests[0].path, "/workflows/flow-a"));
check(() => assert.equal(islands.length, 0));
view.state.host.value = {};
await settle();
check(() => assert.equal(islands.length, 1));
check(() => assert.equal(islands[0].props.readOnly, false));
check(() =>
  assert.deepEqual(islands[0].props.definition.retained, { value: false }),
);
const changed = structuredClone(fixture.definition);
changed.graph.viewport.x = 30;
answer = async () => ({
  ...structuredClone(fixture),
  revision: 8,
  definition: changed,
});
await islands[0].props.onSave(changed);
await settle();
const save = requests.find((item) => item.method === "PUT"),
  payload = JSON.parse(save.body);
check(() => assert.equal(payload.revision, 7));
check(() => assert.equal(payload.name, fixture.name));
check(() => assert.equal(payload.description, fixture.description));
check(() => assert.deepEqual(payload.definition, changed));
check(() => assert.equal(view.state.detail.value.revision, 8));
check(() =>
  assert.equal(view.events.filter((event) => event[0] === "saved").length, 1),
);
view.state.name.value = "新名称";
await settle();
check(() => assert.equal(islands[0].props.externalDirty, true));
answer = async () => {
  throw new ApiError(409, "版本已变化，请重新读取");
};
await islands[0].props.onSave(changed);
await settle();
check(() => assert.match(view.state.error.value, /版本已变化/));
check(() => assert.equal(view.state.detail.value.revision, 8));
check(() => assert.equal(view.state.name.value, "新名称"));
check(() =>
  assert.equal(view.events.filter((event) => event[0] === "saved").length, 1),
);
answer = async () => {
  throw new ApiError(403, "访问权限已撤销");
};
await islands[0].props.onSave(changed);
await settle();
check(() => assert.equal(view.state.detail.value, undefined));
check(() => assert.equal(islands[0].unmounted, true));
check(() => assert.equal(view.events.at(-1)[0], "denied"));
const requestCount = requests.length;
await view.state.save(changed);
check(() => assert.equal(requests.length, requestCount));
view.stop();
answer = async () => structuredClone(fixture);
const readonly = await mount(false);
readonly.state.host.value = {};
await settle();
check(() => assert.equal(islands.at(-1).props.readOnly, true));
const beforeReadonly = requests.length;
await readonly.state.save(changed);
check(() => assert.equal(requests.length, beforeReadonly));
readonly.stop();
check(() => assert.equal(islands.at(-1).unmounted, true));
const beforeCreate = requests.length;
const created = await mount(true, "");
check(() => assert.equal(requests.length, beforeCreate));
check(() =>
  assert.equal(created.state.detail.value.definition.graph.nodes.length, 2),
);
check(() =>
  assert.equal(
    created.state.detail.value.definition.graph.nodes[0].data.type,
    "start",
  ),
);
check(() =>
  assert.equal(
    created.state.detail.value.definition.graph.nodes[1].data.type,
    "end",
  ),
);
created.state.host.value = {};
await settle();
await created.state.save(created.state.detail.value.definition);
check(() => assert.equal(requests.length, beforeCreate));
check(() => assert.match(created.state.error.value, /名称/));
created.state.name.value = "新建可视化流程";
await settle();
answer = async (_path, options) => ({
  ...fixture,
  id: "created-flow",
  name: "新建可视化流程",
  definition: JSON.parse(options.body).definition,
});
await islands.at(-1).props.onSave(created.state.detail.value.definition);
await settle();
const creation = requests.at(-1),
  creationPayload = JSON.parse(creation.body);
check(() => assert.equal(creation.path, "/workflows"));
check(() => assert.equal(creation.method, "POST"));
check(() => assert.equal(Object.hasOwn(creationPayload, "revision"), false));
check(() => assert.equal(creationPayload.name, "新建可视化流程"));
check(() => assert.equal(created.events.at(-1)[1], "created-flow"));
check(() => assert.equal(created.state.detail.value.id, "created-flow"));
await created.state.save(created.state.detail.value.definition);
await settle();
check(() => assert.equal(requests.at(-1).path, "/workflows/created-flow"));
check(() => assert.equal(requests.at(-1).method, "PUT"));
created.stop();
let resolve, signal;
answer = async (_path, options) => {
  signal = options.signal;
  return new Promise((complete) => {
    resolve = complete;
  });
};
const late = await mount();
late.stop();
check(() => assert.equal(signal.aborted, true));
resolve(structuredClone(fixture));
await settle();
check(() => assert.equal(late.state.detail.value, undefined));
check(() => assert.equal(late.events.length, 0));
check(() =>
  assert.equal(requests.filter((item) => item.method === "POST").length, 1),
);
delete globalThis.__workflowEditorTest;
console.log(
  JSON.stringify({
    passed: true,
    checks,
    actual_http: 0,
    browser: 0,
    database: 0,
    model: 0,
  }),
);
