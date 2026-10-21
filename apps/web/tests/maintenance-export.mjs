/** 维护CSV入口纯回归：真实SFC状态与Blob/DOM替身；不请求API、数据库或模型。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { createRenderer, h, ref, shallowRef, reactive, nextTick } from "vue";
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const sourceRoot = new URL("../src/modules/manufacturing/", import.meta.url);
const url = (code) =>
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
function js(source, filename) {
  const result = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  check(() => assert.equal(result.diagnostics?.length ?? 0, 0));
  return result.outputText;
}
const helperUrl = url(
  js(
    await readFile(new URL("maintenanceExport.ts", sourceRoot), "utf8"),
    "maintenanceExport.ts",
  ),
);
const helpers = await import(helperUrl);
const measurementUrl = url(
  js(
    await readFile(new URL("measurementPresentation.ts", sourceRoot), "utf8"),
    "measurementPresentation.ts",
  ),
);
check(() =>
  assert.equal(
    helpers.maintenanceCsvName("循环/泵:01"),
    "循环_泵_01-分组数据.csv",
  ),
);
check(() =>
  assert.equal(helpers.maintenanceCsvName("  "), "维护分析-分组数据.csv"),
);
check(() =>
  assert.equal(helpers.maintenanceCsvName("设备... "), "设备-分组数据.csv"),
);
check(() =>
  assert.equal(helpers.maintenanceCsvName("A\u0000B"), "A_B-分组数据.csv"),
);
check(() =>
  assert.ok(helpers.maintenanceCsvName("泵".repeat(300)).length < 140),
);
const csv = new Blob(["load_min,baseline_mean\n0,31.0000001\n"], {
  type: "text/csv;charset=utf-8",
});
const created = [],
  clicked = [],
  revoked = [],
  timers = [],
  requests = [],
  operations = [];
let clickFails = false,
  readAllowed = true;
const original = {
  document: globalThis.document,
  window: globalThis.window,
  create: URL.createObjectURL,
  revoke: URL.revokeObjectURL,
};
URL.createObjectURL = (blob) => {
  const value = `blob:test-${created.length}`;
  created.push({ blob, url: value });
  return value;
};
URL.revokeObjectURL = (value) => revoked.push(value);
globalThis.window = {
  setTimeout: (callback) => {
    timers.push(callback);
    return timers.length;
  },
};
globalThis.document = {
  createElement(tag) {
    assert.equal(tag, "a");
    return {
      click() {
        if (clickFails) throw new Error("下载启动失败");
        clicked.push({ href: this.href, filename: this.download });
      },
    };
  },
};
class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
const session = reactive({
  value: { user: { id: "user-one" }, workspace: { id: "workspace-one" } },
  can(permission) {
    assert.ok(["m10.read", "m10.write"].includes(permission));
    return readAllowed;
  },
});
let response = async () => csv;
globalThis.__maintenanceExport = {
  ApiError,
  useSession: () => session,
  api: {
    async postBlob(path, body) {
      requests.push({ path, body });
      return response(path, body);
    },
  },
  useCollection: () => ({
    items: ref([]),
    loading: shallowRef(false),
    async load() {},
  }),
  useOperation() {
    const busy = shallowRef(false),
      error = shallowRef(""),
      fields = ref({});
    const op = {
      busy,
      error,
      fields,
      async run(action, success) {
        if (busy.value) return;
        assert.equal(success, undefined);
        busy.value = true;
        error.value = "";
        try {
          return await action();
        } catch (failure) {
          error.value = failure.message;
        } finally {
          busy.value = false;
        }
      },
    };
    operations.push(op);
    return op;
  },
};
const stub = url(
  "export const {ApiError,api,useSession,useCollection,useOperation}=globalThis.__maintenanceExport;export default {};",
);
const filename = "MaintenanceWorkspace.vue",
  source = await readFile(new URL(filename, sourceRoot), "utf8");
const parsed = parse(source, { filename });
check(() => assert.deepEqual(parsed.errors, []));
const script = compileScript(parsed.descriptor, { id: "maintenance-export" });
const template = compileTemplate({
  source: parsed.descriptor.template.content,
  filename,
  id: "maintenance-export",
  compilerOptions: { bindingMetadata: script.bindings },
});
check(() => assert.deepEqual(template.errors, []));
let compiled = js(script.content, filename + ".ts")
  .replaceAll('"vue"', JSON.stringify(import.meta.resolve("vue")))
  .replaceAll("'vue'", JSON.stringify(import.meta.resolve("vue")))
  .replaceAll('"./maintenanceExport"', JSON.stringify(helperUrl))
  .replaceAll('"./measurementPresentation"', JSON.stringify(measurementUrl));
for (const specifier of [
  "../../api/client",
  "../../stores/session",
  "../../composables/useCollection",
  "../../composables/useOperation",
])
  compiled = compiled.replaceAll(
    JSON.stringify(specifier),
    JSON.stringify(stub),
  );
compiled = compiled.replace(
  /from "[^"\n]+\.vue"/g,
  `from ${JSON.stringify(stub)}`,
);
const Comp = (await import(url(compiled))).default;
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
let state,
  mounted = false;
const app = renderer.createApp({
  setup() {
    state = Comp.setup({}, { expose() {}, emit() {} });
    return () => h("div");
  },
});
try {
  app.mount({ children: [] });
  mounted = true;
  await nextTick();
  await state.exportData();
  check(() => assert.equal(requests.length, 0));
  const record = () => ({
    id: "analysis-one",
    name: "循环/泵:01",
    revision: 2,
    result: { bins: [{ baseline_mean: "31.0000001" }] },
  });
  state.detail.value = record();
  check(() => assert.equal(state.displayBins.value[0].baseline_mean, "31"));
  check(() => assert.equal(state.bins.value[0].baseline_mean, "31.0000001"));
  check(() => assert.equal(state.number(null, "%"), "—"));
  check(() => assert.equal(state.number("0.125678", "%"), "12.57%"));
  readAllowed = false;
  await state.exportData();
  check(() => assert.equal(requests.length, 0));
  readAllowed = true;
  await state.exportData();
  check(() =>
    assert.deepEqual(requests.at(-1), {
      path: "/maintenance-analyses/analysis-one/exports",
      body: {},
    }),
  );
  check(() => assert.equal(created.at(-1).blob, csv));
  check(() => assert.equal(clicked.at(-1).filename, "循环_泵_01-分组数据.csv"));
  check(() => assert.equal(revoked.length, 0));
  timers.splice(0).forEach((callback) => callback());
  check(() => assert.equal(revoked.length, 1));
  let complete;
  response = () =>
    new Promise((resolve) => {
      complete = resolve;
    });
  const pending = state.exportData(),
    before = requests.length;
  check(() => assert.equal(operations[1].busy.value, true));
  await state.exportData();
  check(() => assert.equal(requests.length, before));
  complete(csv);
  await pending;
  check(() => assert.equal(operations[1].busy.value, false));
  response = async () => {
    throw new ApiError(503, "导出服务暂不可用");
  };
  const beforeFailure = clicked.length;
  await state.exportData();
  check(() => assert.equal(clicked.length, beforeFailure));
  check(() => assert.match(operations[1].error.value, /暂不可用/));
  check(() => assert.equal(state.detail.value.id, "analysis-one"));
  for (const status of [403, 404]) {
    state.detail.value = record();
    response = async () => {
      throw new ApiError(status, "当前结果不可访问");
    };
    await state.exportData();
    check(() => assert.equal(state.detail.value, null));
    check(() => assert.equal(clicked.length, beforeFailure));
  }
  for (const change of [
    () => {
      state.detail.value = { ...record(), id: "another-analysis" };
    },
    () => {
      session.value.workspace.id = "workspace-two";
    },
    () => {
      session.value.user.id = "user-two";
    },
    () => {
      readAllowed = false;
    },
  ]) {
    state.detail.value = record();
    session.value.workspace.id = "workspace-one";
    session.value.user.id = "user-one";
    readAllowed = true;
    response = () =>
      new Promise((resolve) => {
        complete = resolve;
      });
    const task = state.exportData();
    change();
    complete(csv);
    await task;
    check(() => assert.equal(clicked.length, beforeFailure));
  }
  check(() => assert.equal(state.detail.value, null));
  readAllowed = true;
  state.detail.value = record();
  response = () =>
    new Promise((resolve) => {
      complete = resolve;
    });
  const late = state.exportData();
  app.unmount();
  mounted = false;
  complete(csv);
  await late;
  check(() => assert.equal(clicked.length, beforeFailure));
  clickFails = true;
  const revokedBefore = revoked.length;
  check(() =>
    assert.throws(
      () => helpers.downloadMaintenanceCsv(csv, "维护分析.csv"),
      /启动失败/,
    ),
  );
  check(() => assert.equal(revoked.length, revokedBefore + 1));
  check(() =>
    assert.ok(requests.every((request) => request.path.endsWith("/exports"))),
  );
} finally {
  if (mounted) app.unmount();
  timers.forEach((callback) => callback());
  URL.createObjectURL = original.create;
  URL.revokeObjectURL = original.revoke;
  if (original.document === undefined) delete globalThis.document;
  else globalThis.document = original.document;
  if (original.window === undefined) delete globalThis.window;
  else globalThis.window = original.window;
  delete globalThis.__maintenanceExport;
}
console.log(
  JSON.stringify({
    passed: true,
    checks,
    http_requests: 0,
    sql_queries: 0,
    model_calls: 0,
    browser: 0,
  }),
);
