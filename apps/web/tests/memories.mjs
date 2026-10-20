/** 个人记忆纯回归：内存状态与Mock请求，不访问服务、数据库或模型。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { effectScope, nextTick, ref } from "vue";

let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const dataUrl = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const directory = new URL("../src/modules/chat/", import.meta.url);
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
  return result.outputText;
}
class ApiError extends Error {
  constructor(status, code, message, fields = {}) {
    super(message);
    Object.assign(this, { status, code, fields });
  }
}
const requests = [];
const record = (
  id = "one",
  revision = 1,
  owner = "alice",
  workspace = "factory",
) => ({
  id,
  revision,
  user_id: owner,
  workspace_id: workspace,
  content: "按产线整理结果",
  type: "user",
  created_at: "2026-10-21T01:00:00Z",
  updated_at: "2026-10-21T02:00:00Z",
});
let respond = async () => ({ items: [record()], total: 41 });
globalThis.__memoryTest = {
  ApiError,
  async request(path, options = {}) {
    requests.push({ path, ...options });
    return respond(path, options);
  },
};
const apiUrl = dataUrl(
  "export const {request,ApiError}=globalThis.__memoryTest;",
);
const logicSource = await readFile(
  new URL("useMemories.ts", directory),
  "utf8",
);
const logic = transpile(logicSource, "useMemories.ts")
  .replaceAll('"vue"', JSON.stringify(import.meta.resolve("vue")))
  .replaceAll('"../../api/client"', JSON.stringify(apiUrl));
const { useMemories } = await import(dataUrl(logic));
const tick = async () => {
  for (let i = 0; i < 5; i++) {
    await Promise.resolve();
    await nextTick();
  }
};
function pending() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const owner = ref({ userId: "alice", workspaceId: "factory" });
const permissions = ref(["m02.read", "m02.write", "m02.delete"]);
const scope = effectScope();
const ui = scope.run(() =>
  useMemories(
    () => owner.value,
    (permission) => permissions.value.includes(permission),
  ),
);
await tick();
check(() => assert.equal(requests.length, 1));
check(() =>
  assert.equal(requests[0].path, "/chat/memories?q=&limit=20&offset=0"),
);
check(() => assert.equal(ui.total.value, 41));
check(() => assert.equal(ui.items.value[0].id, "one"));

ui.query.value = " 温度 #? ";
ui.type.value = "context";
await ui.search();
const searchUrl = new URL(requests.at(-1).path, "http://memory.invalid");
check(() => assert.equal(searchUrl.searchParams.get("q"), "温度 #?"));
check(() => assert.equal(searchUrl.searchParams.get("type"), "context"));
await ui.changePage(2);
check(() =>
  assert.equal(
    new URL(requests.at(-1).path, "http://memory.invalid").searchParams.get(
      "offset",
    ),
    "20",
  ),
);
const oldList = pending();
respond = () => oldList.promise;
const firstLoad = ui.load();
const oldSignal = requests.at(-1).signal;
respond = async () => ({ items: [record("new-search")], total: 1 });
ui.query.value = "新关键词";
await ui.search();
oldList.resolve({ items: [record("old-search")], total: 9 });
await firstLoad;
check(() => assert.equal(oldSignal.aborted, true));
check(() => assert.equal(ui.items.value[0].id, "new-search"));
check(() => assert.equal(ui.total.value, 1));

ui.create();
const beforeEmpty = requests.length;
ui.draft.content = "   ";
check(() => assert.equal(ui.visible.value, true));
await ui.save();
check(() => assert.equal(requests.length, beforeEmpty));
check(() => assert.equal(ui.fields.value.content, "请输入内容"));
ui.draft.content = "字".repeat(6001);
await ui.save();
check(() => assert.equal(requests.length, beforeEmpty));
check(() => assert.match(ui.fields.value.content, /6000/));
ui.draft.content = "  偏好使用中文  ";
ui.draft.type = "context";
respond = async (path, options) =>
  options.method === "POST"
    ? record("created")
    : { items: [record("created")], total: 1 };
const createdSuccessfully = await ui.save();
check(() => assert.equal(createdSuccessfully, true));
const creation = requests.findLast((item) => item.method === "POST");
check(() =>
  assert.deepEqual(JSON.parse(creation.body), {
    content: "偏好使用中文",
    type: "context",
  }),
);
check(() => assert.equal(ui.visible.value, false));
check(() => assert.equal(ui.items.value[0].id, "created"));

respond = async () => record("versioned", 7);
await ui.edit("versioned");
check(() => assert.equal(ui.editing.value.revision, 7));
check(() => assert.equal(ui.draft.content, "按产线整理结果"));
ui.draft.content = "新的偏好";
respond = async (path, options) =>
  options.method === "PATCH"
    ? record("versioned", 8)
    : { items: [record("versioned", 8)], total: 1 };
await ui.save();
check(() =>
  assert.deepEqual(
    JSON.parse(requests.findLast((item) => item.method === "PATCH").body),
    { content: "新的偏好", type: "user", revision: 7 },
  ),
);
respond = async () => record("versioned", 8);
await ui.edit("versioned");
ui.draft.content = "不丢失的修改";
respond = async () => {
  throw new ApiError(409, "revision_conflict", "记录已被更新");
};
await ui.save();
const conflictCount = requests.length;
check(() => assert.equal(ui.conflict.value, true));
check(() => assert.equal(ui.draft.content, "不丢失的修改"));
check(() => assert.equal(ui.visible.value, true));
await ui.save();
check(() => assert.equal(requests.length, conflictCount));
respond = async () => record("versioned", 9);
await ui.edit("versioned");
check(() => assert.equal(ui.conflict.value, false));
check(() => assert.equal(ui.editing.value.revision, 9));
respond = async () => {
  throw new ApiError(422, "validation_failed", "内容错误", {
    content: "请检查内容",
  });
};
await ui.save();
check(() => assert.equal(ui.fields.value.content, "请检查内容"));
check(() => assert.equal(ui.conflict.value, false));
respond = async () => {
  throw new ApiError(0, "network_error", "网络中断");
};
await ui.save();
const uncertainCount = requests.length;
check(() => assert.equal(ui.uncertain.value, true));
check(() => assert.match(ui.editorError.value, /待核对/));
await ui.save();
check(() => assert.equal(requests.length, uncertainCount));
respond = async () => ({ items: [record("saved-unknown", 10)], total: 1 });
await ui.checkList();
check(() => assert.equal(requests.at(-1).method, undefined));
check(() => assert.equal(ui.visible.value, false));
check(() => assert.equal(ui.items.value[0].id, "saved-unknown"));

ui.page.value = 2;
respond = async (path, options) =>
  options.method === "DELETE"
    ? { id: "saved-unknown", deleted: true }
    : { items: [], total: 0 };
await ui.remove(record("saved-unknown", 10));
check(() =>
  assert.equal(
    requests.findLast((item) => item.method === "DELETE").path,
    "/chat/memories/saved-unknown?revision=10",
  ),
);
check(() => assert.equal(ui.page.value, 1));
const oldTicket = ui.capture();
respond = async () => ({ items: [], total: 0 });
owner.value = { userId: "bob", workspaceId: "factory" };
await tick();
const afterSwitch = requests.length;
await ui.remove(record(), oldTicket);
check(() => assert.equal(requests.length, afterSwitch));
check(() => assert.equal(ui.isCurrent(oldTicket), false));

const oldRead = pending();
respond = () => oldRead.promise;
const readPromise = ui.edit("old-owner");
const readSignal = requests.at(-1).signal;
respond = async () => ({
  items: [record("new-workspace", 1, "bob", "other")],
  total: 1,
});
owner.value = { userId: "bob", workspaceId: "other" };
await tick();
oldRead.resolve(record("old-owner"));
await readPromise;
check(() => assert.equal(readSignal.aborted, true));
check(() => assert.equal(ui.visible.value, false));
check(() => assert.equal(ui.editing.value, null));
check(() => assert.equal(ui.items.value[0].id, "new-workspace"));
ui.create();
ui.draft.content = "旧用户内容";
const oldSave = pending();
respond = () => oldSave.promise;
const savePromise = ui.save();
const saveSignal = requests.at(-1).signal;
respond = async () => ({ items: [], total: 0 });
owner.value = { userId: "alice", workspaceId: "factory" };
await tick();
check(() => assert.equal(saveSignal.aborted, true));
check(() => assert.equal(ui.draft.content, ""));
check(() => assert.equal(ui.saving.value, false));
oldSave.resolve(record("old-save"));
const oldSaveSucceeded = await savePromise;
check(() => assert.equal(oldSaveSucceeded, false));
check(() => assert.equal(ui.items.value.length, 0));

permissions.value = ["m02.read"];
await tick();
check(() => assert.equal(ui.canWrite.value, false));
ui.create();
check(() => assert.equal(ui.visible.value, false));
respond = async () => record("readonly");
await ui.edit("readonly");
check(() => assert.equal(ui.visible.value, true));
const readOnlyCount = requests.length;
await ui.save();
await ui.remove(record());
check(() => assert.equal(requests.length, readOnlyCount));
permissions.value = [];
await tick();
check(() => assert.equal(ui.visible.value, false));
check(() => assert.equal(ui.draft.content, ""));
check(() => assert.equal(ui.canRead.value, false));
const deniedCount = requests.length;
await ui.load();
check(() => assert.equal(requests.length, deniedCount));
respond = async () => ({ items: [record()], total: 1 });
permissions.value = ["m02.read", "m02.write", "m02.delete"];
await tick();
respond = async () => {
  throw new ApiError(403, "permission_denied", "权限已撤销");
};
await ui.edit("one");
check(() => assert.equal(ui.items.value.length, 0));
check(() => assert.equal(ui.total.value, 0));
check(() => assert.equal(ui.visible.value, false));
check(() => assert.equal(ui.error.value, "权限已撤销"));

respond = async () => record("not-found");
await ui.edit("not-found");
respond = async () => {
  throw new ApiError(404, "memory_not_found", "记录已删除");
};
await ui.save();
check(() => assert.equal(ui.editing.value, null));
check(() => assert.equal(ui.draft.content, ""));
check(() => assert.equal(ui.saving.value, false));
ui.query.value = "用户关键词";
ui.type.value = "context";
owner.value = null;
await tick();
check(() => assert.equal(ui.query.value, ""));
check(() => assert.equal(ui.type.value, ""));
check(() => assert.equal(ui.items.value.length, 0));
check(() => assert.equal(ui.canRead.value, false));
respond = async () => ({ items: [], total: 0 });
owner.value = { userId: "alice", workspaceId: "factory" };
await tick();
const disposedRead = pending();
respond = () => disposedRead.promise;
const disposedPromise = ui.edit("disposed");
const disposedSignal = requests.at(-1).signal;
scope.stop();
disposedRead.resolve(record("disposed"));
await disposedPromise;
check(() => assert.equal(disposedSignal.aborted, true));
check(() => assert.equal(ui.visible.value, false));
check(() => assert.equal(ui.items.value.length, 0));

let compiled = 0;
for (const filename of [
  "MemoryWorkspace.vue",
  "MemoryList.vue",
  "MemoryEditor.vue",
  "ChatWorkspace.vue",
]) {
  const source = await readFile(new URL(filename, directory), "utf8");
  const { descriptor, errors } = parse(source, { filename });
  check(() => assert.deepEqual(errors, []));
  const script = compileScript(descriptor, { id: filename });
  const template = compileTemplate({
    source: descriptor.template.content,
    filename,
    id: filename,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
  transpile(script.content, filename + ".ts");
  check(() => assert.equal(source.includes("v-html"), false));
  if (filename === "MemoryWorkspace.vue")
    check(() => assert.match(source, /ElMessageBox\.confirm\("删除这条记忆/));
  if (filename === "MemoryEditor.vue") {
    check(() => assert.match(source, /:maxlength="6000"/));
    check(() => assert.match(source, /读取最新/));
    check(() => assert.match(source, /核对列表/));
    check(() => assert.match(source, /:readonly="readonly"/));
  }
  if (filename === "ChatWorkspace.vue")
    check(() =>
      assert.match(source, /<MemoryWorkspace v-if="mode === 'memory'"/),
    );
  compiled++;
}
check(() =>
  assert.equal(
    /localStorage|completions|embeddings|\/answers/.test(logicSource),
    false,
  ),
);
check(() =>
  assert.equal(
    requests.every((item) => item.path.startsWith("/chat/memories")),
    true,
  ),
);
delete globalThis.__memoryTest;
console.log(
  JSON.stringify({
    passed: true,
    assertions: checks,
    compiled_components: compiled,
    actual_http: 0,
    actual_sql: 0,
    model_calls: 0,
  }),
);
