/** 分页、历史授权和流程幂等纯回归：Mock只在内存，不发HTTP/数据库/模型请求。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { effectScope, shallowRef, nextTick } from "vue";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
let checks = 0;
function check(action) {
  action();
  checks++;
}
function moduleUrl(source) {
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
function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}
async function settle() {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
const vueUrl = import.meta.resolve("vue");
const helperUrl = moduleUrl(
  transpile(
    await readFile(
      new URL("../src/modules/data/dataPreparation.ts", import.meta.url),
      "utf8",
    ),
    "dataPreparation.ts",
  ),
);
class MockApiError extends Error {
  constructor(status, message, fields = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}
const state = {
  gets: [],
  posts: [],
  get: async () => ({ items: [], total: 0 }),
  post: async () => ({}),
};
globalThis.__historyPureTest = {
  ApiError: MockApiError,
  api: {
    async get(path) {
      state.gets.push(path);
      return state.get(path);
    },
  },
  async request(path, options) {
    if (options.method !== "POST") {
      state.gets.push(path);
      return state.get(path);
    }
    state.posts.push({ path, options });
    return state.post(path, options);
  },
};
const mockUrl = moduleUrl(
  "export const {api,request,ApiError}=globalThis.__historyPureTest;",
);
async function loadModule(path) {
  let source = transpile(
    await readFile(new URL(path, import.meta.url), "utf8"),
    path,
  );
  source = source
    .replaceAll('"vue"', JSON.stringify(vueUrl))
    .replaceAll('"../../api/client"', JSON.stringify(mockUrl))
    .replaceAll('"./dataPreparation"', JSON.stringify(helperUrl));
  return import(moduleUrl(source));
}
const { usePreparationQueries } = await loadModule(
  "../src/modules/data/usePreparationQueries.ts",
);
const queryScope = effectScope();
const queryA = {
  id: "query-a",
  revision: 1,
  name: "设备A",
  columns: ["line", "value"],
  rows: [{ line: "A", value: "9007199254740993123.0001" }],
};
state.get = async (path) =>
  path.startsWith("/queries/query-")
    ? structuredClone(queryA)
    : { items: [queryA], total: 250 };
const catalog = queryScope.run(() => usePreparationQueries());
await catalog.load();
check(() => assert.ok(state.gets.at(-1).includes("limit=100&offset=0")));
catalog.queryId.value = queryA.id;
await settle();
check(() => assert.deepEqual(catalog.columns.value, ["line", "value"]));
state.get = async () => ({
  items: [{ id: "page-two", revision: 1, name: "设备B" }],
  total: 250,
});
await catalog.changePage(2);
check(() => assert.ok(state.gets.at(-1).includes("offset=100")));
check(() => assert.deepEqual(catalog.columns.value, ["line", "value"]));
check(() => assert.equal(catalog.options.value[0].id, queryA.id));
await catalog.search("泵&线A");
check(() =>
  assert.ok(state.gets.at(-1).includes("q=" + encodeURIComponent("泵&线A"))),
);
check(() => assert.ok(state.gets.at(-1).endsWith("offset=0")));
const slow = deferred();
state.get = async (path) =>
  path.includes("q=old")
    ? slow.promise
    : { items: [{ id: "new", revision: 1 }], total: 1 };
const older = catalog.search("old");
await catalog.search("new");
slow.resolve({ items: [{ id: "old", revision: 1 }], total: 99 });
await older;
check(() => assert.equal(catalog.queries.value[0].id, "new"));
check(() => assert.equal(catalog.total.value, 1));
const oldDetail = deferred();
state.get = async (path) =>
  path.endsWith("/slow")
    ? oldDetail.promise
    : { id: "latest", revision: 1, columns: ["latest_field"] };
catalog.queryId.value = "slow";
catalog.queryId.value = "latest";
await settle();
oldDetail.resolve({ id: "slow", revision: 1, columns: ["wrong_field"] });
await settle();
check(() => assert.deepEqual(catalog.columns.value, ["latest_field"]));
state.get = async () => {
  throw new MockApiError(403, "当前无权读取");
};
catalog.queryId.value = "denied";
await settle();
check(() => assert.equal(catalog.queryId.value, ""));
check(() => assert.deepEqual(catalog.columns.value, []));
check(() => assert.equal(catalog.selecting.value, false));
check(() => assert.equal(catalog.error.value, "当前无权读取"));
await assert.rejects(catalog.load());
const failedCount = state.gets.length;
await catalog.changePage(1);
check(() => assert.equal(state.gets.length, failedCount));
queryScope.stop();

const { usePreparationHistory } = await loadModule(
  "../src/modules/data/usePreparationHistory.ts",
);
const historyScope = effectScope();
const job = {
  id: "job-one",
  name: "产量汇总",
  revision: 1,
  status: "succeeded",
  operations: [{ type: "metric", metric_id: "metric&one" }],
  result: { columns: ["value"], rows: [{ value: null }], row_count: 1 },
};
state.get = async (path) =>
  path.startsWith("/data-processing-jobs?")
    ? { items: [job], total: 43 }
    : structuredClone(job);
const history = historyScope.run(() => usePreparationHistory());
await history.open("metric&one");
check(() => assert.ok(state.gets.at(-1).endsWith("metric_id=metric%26one")));
check(() => assert.equal(history.visible.value, true));
await history.changePage(2);
check(() => assert.ok(state.gets.at(-1).includes("offset=20")));
history.query.value = "日报";
await history.search();
check(() => assert.ok(state.gets.at(-1).includes(encodeURIComponent("日报"))));
check(() => assert.equal(history.page.value, 1));
await history.read(job.id);
check(() => assert.equal(history.detail.value.result.rows[0].value, null));
const lateJob = deferred();
state.get = async () => lateJob.promise;
const read = history.read(job.id);
history.close();
lateJob.resolve(job);
await read;
check(() => assert.equal(history.detail.value, null));
check(() => assert.deepEqual(history.items.value, []));
state.get = async () => ({ items: [job], total: 1 });
await history.open();
state.get = async () => {
  throw new MockApiError(403, "来源访问已撤销");
};
await history.read(job.id);
check(() => assert.equal(history.detail.value, null));
check(() => assert.equal(history.total.value, 0));
check(() => assert.deepEqual(history.items.value, []));
check(() => assert.equal(history.error.value, "来源访问已撤销"));
const requestsBefore = state.gets.length;
await history.changePage(1);
check(() => assert.equal(state.gets.length, requestsBefore));
history.detail.value = job;
history.items.value = [job];
history.deny("导出权限已撤销");
check(() => assert.equal(history.detail.value, null));
check(() => assert.deepEqual(history.items.value, []));
check(() => assert.equal(history.error.value, "导出权限已撤销"));
historyScope.stop();

const { useWorkflowRunIntent } = await loadModule(
  "../src/modules/flows/useWorkflowRunIntent.ts",
);
const workflowId = shallowRef("flow-one");
const runScope = effectScope();
const workflow = runScope.run(() =>
  useWorkflowRunIntent(() => workflowId.value),
);
const pendingPost = deferred();
state.post = async () => pendingPost.promise;
const starting = workflow.start("首次输入");
const afterFirst = state.posts.length;
check(() => assert.equal(workflow.running.value, true));
const doubleClick = await workflow.start("重复点击");
check(() => assert.equal(doubleClick, false));
check(() => assert.equal(state.posts.length, afterFirst));
const firstKey = state.posts.at(-1).options.headers["Idempotency-Key"];
check(() => assert.ok(firstKey));
check(() =>
  assert.deepEqual(JSON.parse(state.posts.at(-1).options.body), {
    input: "首次输入",
  }),
);
pendingPost.resolve({
  id: "run-one",
  status: "unknown",
  steps: [],
  input: "首次输入",
  output: "",
  error: "需核对",
});
await starting;
check(() => assert.equal(workflow.uncertain.value, true));
const changedInput = await workflow.start("换了输入");
check(() => assert.equal(changedInput, false));
check(() => assert.equal(workflow.current.value.key, firstKey));
state.get = async (path) =>
  path.includes("idempotency_key=")
    ? { items: [{ id: "run-one" }], total: 1 }
    : {
        id: "run-one",
        status: "succeeded",
        steps: [],
        output: "真实返回",
        input: "首次输入",
      };
await workflow.verify();
check(() =>
  assert.ok(state.gets.at(-2).includes(encodeURIComponent(firstKey))),
);
check(() => assert.equal(state.posts.length, afterFirst));
check(() => assert.equal(workflow.currentRun.value.output, "真实返回"));
check(() => assert.equal(workflow.uncertain.value, false));
state.post = async () => {
  throw new MockApiError(408, "请求超时");
};
await workflow.start("独立新运行");
const secondKey = workflow.current.value.key;
check(() => assert.notEqual(secondKey, firstKey));
check(() => assert.equal(workflow.current.value.status, "unknown"));
check(() => assert.equal(workflow.currentRun.value, null));
const automaticRetry = await workflow.start("不能自动重发");
check(() => assert.equal(automaticRetry, false));
state.get = async () => ({ items: [], total: 0 });
await workflow.verify();
check(() => assert.equal(workflow.current.value.key, secondKey));
check(() => assert.equal(workflow.current.value.error, "尚未找到本次运行记录"));
state.post = async () => ({
  id: "explicit-new",
  status: "failed",
  input: "确认后新运行",
  steps: [],
  output: "",
  error: "原始失败",
});
await workflow.start("确认后新运行", true);
check(() => assert.notEqual(workflow.current.value.key, secondKey));
check(() => assert.equal(workflow.current.value.status, "failed"));
check(() => assert.equal(workflow.currentRun.value.error, "原始失败"));
const pendingDispose = deferred();
state.post = async () => pendingDispose.promise;
const last = workflow.start("销毁中");
const signal = state.posts.at(-1).options.signal;
runScope.stop();
check(() => assert.equal(signal.aborted, true));
pendingDispose.resolve({
  id: "late",
  status: "succeeded",
  steps: [],
  input: "销毁中",
  output: "不得回写",
});
await last;
check(() => assert.equal(workflow.currentRun.value, null));

for (const relative of [
  "data/PreparationQuerySelect.vue",
  "data/PreparationHistory.vue",
  "data/ProcessingWorkspace.vue",
  "data/BusinessMetricWorkspace.vue",
  "data/BusinessMetricList.vue",
  "data/PreparationResult.vue",
  "flows/WorkflowWorkspace.vue",
]) {
  const filename = relative.split("/").at(-1);
  const source = await readFile(
    new URL("../src/modules/" + relative, import.meta.url),
    "utf8",
  );
  const parsed = parse(source, { filename });
  check(() => assert.deepEqual(parsed.errors, []));
  const script = compileScript(parsed.descriptor, { id: filename });
  transpile(script.content, filename + ".ts");
  const template = compileTemplate({
    source: parsed.descriptor.template.content,
    id: filename,
    filename,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
}
// 实际结果组件：拒绝导出时清上层缓存，异步下载仍使用点击时的任务名。
globalThis.__historyPureTest.useOperation = () => ({
  busy: shallowRef(false),
  async run(action) {
    try {
      return await action();
    } catch {}
  },
});
const operationUrl = moduleUrl(
  "export const useOperation=globalThis.__historyPureTest.useOperation; export default {};",
);
const resultParsed = parse(
  await readFile(
    new URL("../src/modules/data/PreparationResult.vue", import.meta.url),
    "utf8",
  ),
  { filename: "PreparationResult.vue" },
);
let resultSource = transpile(
  compileScript(resultParsed.descriptor, { id: "result-download" }).content,
  "PreparationResult.vue.ts",
);
resultSource = resultSource
  .replaceAll('"vue"', JSON.stringify(vueUrl))
  .replaceAll("'vue'", JSON.stringify(vueUrl))
  .replaceAll('"../../api/client"', JSON.stringify(mockUrl))
  .replaceAll('"../../composables/useOperation"', JSON.stringify(operationUrl))
  .replaceAll(
    '"../../components/common/ResultTable.vue"',
    JSON.stringify(operationUrl),
  )
  .replaceAll('"./dataPreparation"', JSON.stringify(helperUrl));
const resultComponent = (await import(moduleUrl(resultSource))).default;
const props = {
  job: { id: "export-a", name: "原任务", result: { rows: [], columns: [] } },
  metric: false,
};
const events = [];
const resultControls = resultComponent.setup(props, {
  expose() {},
  emit: (...event) => events.push(event),
});
globalThis.__historyPureTest.api.postBlob = async () => {
  throw new MockApiError(403, "导出权限已撤销");
};
await resultControls.download();
check(() =>
  assert.deepEqual(events, [["permission-denied", "导出权限已撤销"]]),
);
const pendingExport = deferred();
let exportPath;
globalThis.__historyPureTest.api.postBlob = async (path) => {
  exportPath = path;
  return pendingExport.promise;
};
const savedDocument = globalThis.document,
  savedWindow = globalThis.window;
const originalCreate = URL.createObjectURL,
  originalRevoke = URL.revokeObjectURL;
const link = { click() {} };
try {
  globalThis.document = { createElement: () => link };
  globalThis.window = {
    setTimeout(callback) {
      callback();
    },
  };
  URL.createObjectURL = () => "blob:pure-test";
  URL.revokeObjectURL = () => {};
  const downloading = resultControls.download();
  props.job = {
    id: "export-b",
    name: "新任务",
    result: { rows: [], columns: [] },
  };
  pendingExport.resolve(new Blob(["value\n9007199254740993123.0001\n"]));
  await downloading;
  check(() =>
    assert.equal(exportPath, "/data-processing-jobs/export-a/exports"),
  );
  check(() => assert.equal(link.download, "原任务.csv"));
} finally {
  globalThis.document = savedDocument;
  globalThis.window = savedWindow;
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
}
delete globalThis.__historyPureTest;
process.stdout.write(
  JSON.stringify({
    passed: true,
    checks,
    sfc_count: 7,
    http_requests: 0,
    sql_queries: 0,
    model_calls: 0,
  }) + "\n",
);
