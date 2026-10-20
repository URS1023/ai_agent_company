/** 会话归属与流竞态纯回归；只用内存存储和Mock，不发HTTP、不启浏览器。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
import { effectScope, shallowRef, nextTick, watch } from "vue";
import { parse, compileScript, compileTemplate } from "@vue/compiler-sfc";
import { parseLocalUiInput, redactUiMessage } from "./helpers/localUiInput.mjs";
let checks = 0;
// 此处仅使用无效测试占位串，绝不读取真实凭据。
check(() =>
  assert.equal(
    redactUiMessage("fixture: placeholder-only", {
      username: "fixture",
      password: "placeholder-only",
    }),
    "[已隐藏]: [已隐藏]",
  ),
);
function check(action) {
  action();
  checks++;
}
const source = new URL("../src/modules/chat/", import.meta.url);
function moduleUrl(text) {
  return `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`;
}
function js(text, fileName) {
  const result = ts.transpileModule(text, {
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
const vueUrl = import.meta.resolve("vue");
const storeUrl = moduleUrl(
  js(await readFile(new URL("chatStore.ts", source), "utf8"), "chatStore.ts"),
);
const store = await import(storeUrl);
const scopedSource = js(
  await readFile(new URL("useScopedChatSessions.ts", source), "utf8"),
  "useScopedChatSessions.ts",
)
  .replaceAll('"vue"', JSON.stringify(vueUrl))
  .replaceAll('"./chatStore"', JSON.stringify(storeUrl));
const { useScopedChatSessions } = await import(moduleUrl(scopedSource));
function memoryStore() {
  const values = new Map();
  return {
    values,
    fail: false,
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      if (this.fail) throw new Error("存储已满");
      values.set(key, value);
    },
  };
}
const ownerA = { userId: "person-a", workspaceId: "factory-a" };
const ownerB = { userId: "person-b", workspaceId: "factory-a" };
const otherFactory = { userId: "person-a", workspaceId: "factory-b" };
const storage = memoryStore();
const fixture = {
  id: "a-session",
  title: "轴承温度",
  updated: 1,
  turns: [
    { role: "user", content: "检查润滑情况" },
    { role: "assistant", content: "现场检查记录" },
  ],
};
storage.values.set(
  "qknow.chat.sessions",
  JSON.stringify([{ ...fixture, title: "未归属旧记录" }]),
);
check(() => assert.deepEqual(store.loadSessions(ownerA, storage), []));
store.saveSessions(ownerA, [fixture], storage);
check(() => assert.deepEqual(store.loadSessions(ownerA, storage), [fixture]));
check(() => assert.deepEqual(store.loadSessions(ownerB, storage), []));
check(() => assert.deepEqual(store.loadSessions(otherFactory, storage), []));
check(() =>
  assert.notEqual(
    store.chatScopeKey({ userId: "a:b", workspaceId: "c" }),
    store.chatScopeKey({ userId: "a", workspaceId: "b:c" }),
  ),
);
check(() => assert.ok(storage.values.has("qknow.chat.sessions")));
const mismatched = memoryStore();
mismatched.values.set(
  store.chatScopeKey(ownerB),
  storage.values.get(store.chatScopeKey(ownerA)),
);
check(() =>
  assert.throws(() => store.loadSessions(ownerB, mismatched), /无法读取/),
);
const invalid = memoryStore();
invalid.values.set(
  store.chatScopeKey(ownerA),
  JSON.stringify({
    version: 2,
    user_id: ownerA.userId,
    workspace_id: ownerA.workspaceId,
    sessions: [
      { ...fixture, turns: [{ role: "system", content: "注入内容" }] },
    ],
  }),
);
check(() =>
  assert.throws(() => store.loadSessions(ownerA, invalid), /无法读取/),
);
check(() => assert.equal(store.searchSessions([fixture], "润滑").length, 1));
check(() => assert.equal(store.searchSessions([fixture], "不存在").length, 0));
const scope = effectScope();
const owner = shallowRef(ownerA);
let changes = 0;
const records = scope.run(() =>
  useScopedChatSessions(
    () => owner.value,
    () => changes++,
    storage,
  ),
);
check(() => assert.equal(records.sessions.value[0].title, "轴承温度"));
const oldTicket = records.capture();
owner.value = ownerB;
check(() => assert.equal(records.sessions.value.length, 0));
check(() => assert.equal(records.activeId.value, ""));
check(() => assert.equal(records.isCurrent(oldTicket), false));
const bSession = records.create();
bSession.turns.push({ role: "user", content: "B的记录" });
records.touch(bSession.id);
owner.value = otherFactory;
check(() => assert.equal(records.sessions.value.length, 0));
owner.value = ownerA;
check(() => assert.equal(records.sessions.value[0].title, "轴承温度"));
check(() => assert.equal(records.touch(fixture.id, oldTicket), false));
const restored = records.capture();
owner.value = null;
check(() => assert.equal(records.sessions.value.length, 0));
check(() => assert.equal(records.active.value, undefined));
check(() => assert.equal(records.isCurrent(restored), false));
owner.value = ownerB;
check(() => assert.equal(records.sessions.value[0].title, "B的记录"));
storage.fail = true;
check(() => assert.equal(records.remove(bSession.id), false));
check(() => assert.equal(records.sessions.value.length, 1));
storage.fail = false;
check(() => assert.equal(records.remove(bSession.id), true));
check(() => assert.deepEqual(store.loadSessions(ownerB, storage), []));
check(() =>
  assert.equal(store.loadSessions(ownerA, storage)[0].id, fixture.id),
);
owner.value = ownerA;
records.clearSession(fixture.id);
check(() => assert.equal(records.active.value.turns.length, 0));
check(() =>
  assert.equal(store.loadSessions(ownerA, storage)[0].id, fixture.id),
);
check(() => assert.equal(changes >= 7, true));
const disposedTicket = records.capture();
scope.stop();
check(() => assert.equal(records.sessions.value.length, 0));
check(() => assert.equal(records.isCurrent(disposedTicket), false));

const fullStorage = memoryStore();
store.saveSessions(
  ownerA,
  Array.from({ length: 50 }, (_, i) => ({ ...fixture, id: `full-${i}` })),
  fullStorage,
);
const fullScope = effectScope();
const full = fullScope.run(() =>
  useScopedChatSessions(
    () => ownerA,
    () => {},
    fullStorage,
  ),
);
check(() => assert.equal(full.create(), undefined));
check(() => assert.equal(full.sessions.value.length, 50));
check(() =>
  assert.equal(store.loadSessions(ownerA, fullStorage).at(-1).id, "full-49"),
);
fullScope.stop();

// 延迟响应可任意到达；原请求结束不得清掉新请求的状态或写入新归属。
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
};
const calls = [];
const profiles = [];
let deferProfiles = false;
globalThis.__scopedChatMock = {
  api: {
    async get(path) {
      if (!deferProfiles) return { model: "纯测试模型" };
      const wait = deferred();
      profiles.push(wait);
      return wait.promise;
    },
  },
  stream(path, body, onChunk, signal) {
    const wait = deferred();
    calls.push({ path, body, onChunk, signal, ...wait });
    return wait.promise;
  },
};
const mockUrl = moduleUrl(
  "export const {api,stream}=globalThis.__scopedChatMock;",
);
const sseUrl = moduleUrl(
  js(
    await readFile(new URL("../src/api/sse.ts", import.meta.url), "utf8"),
    "sse.ts",
  ),
);
let conversationSource = js(
  await readFile(new URL("useChatConversation.ts", source), "utf8"),
  "useChatConversation.ts",
)
  .replaceAll('"vue"', JSON.stringify(vueUrl))
  .replaceAll('"../../api/client"', JSON.stringify(mockUrl))
  .replaceAll('"../../api/sse"', JSON.stringify(sseUrl));
const { useChatConversation } = await import(moduleUrl(conversationSource));
async function settle() {
  for (let i = 0; i < 4; i++) {
    await Promise.resolve();
    await nextTick();
  }
}
const testScope = effectScope();
const currentOwner = shallowRef(ownerA),
  chatPage = shallowRef(true);
const chatStorage = memoryStore();
let invalidate = () => {};
const chatSessions = testScope.run(() =>
  useScopedChatSessions(
    () => currentOwner.value,
    () => invalidate(),
    chatStorage,
  ),
);
let scrollWait;
const conversation = testScope.run(() =>
  useChatConversation(
    chatSessions,
    () => chatPage.value,
    async () => {
      if (scrollWait) await scrollWait.promise;
    },
  ),
);
invalidate = conversation.clear;
await settle();
chatSessions.reload();
await settle();
check(() => assert.equal(conversation.profileReady.value, true));
conversation.draft.value = "A的问题";
const runA = conversation.send();
await settle();
check(() => assert.equal(calls.length, 1));
const oldCall = calls[0];
oldCall.onChunk('data: {"choices":[{"delta":{"content":"A的回答"}}]}\n');
await settle();
check(() => assert.equal(chatSessions.turns.value.at(-1).content, "A的回答"));
currentOwner.value = ownerB;
await settle();
check(() => assert.equal(oldCall.signal.aborted, true));
check(() => assert.equal(chatSessions.turns.value.length, 0));
check(() => assert.equal(conversation.draft.value, ""));
conversation.draft.value = "B的问题";
const runB = conversation.send();
await settle();
check(() => assert.equal(calls.length, 2));
oldCall.onChunk('data: {"choices":[{"delta":{"content":"迟到的A"}}]}\n');
oldCall.resolve();
await runA;
check(() => assert.equal(conversation.sending.value, true));
check(() =>
  assert.equal(
    chatSessions.turns.value.some((turn) => turn.content.includes("迟到的A")),
    false,
  ),
);
const bCall = calls[1];
bCall.onChunk('data: {"choices":[{"delta":{"content":"B的"}}]}\n');
bCall.onChunk('data: {"choices":[{"delta":{"content":"回答"}}]}');
conversation.stop();
check(() => assert.equal(bCall.signal.aborted, true));
check(() =>
  assert.equal(
    store.loadSessions(ownerB, chatStorage)[0].turns.at(-1).content,
    "B的回答",
  ),
);
bCall.resolve();
await runB;
check(() => assert.equal(conversation.sending.value, false));

// 切换发生在滚动等待阶段时，尚未派发的模型请求必须取消。
scrollWait = deferred();
conversation.draft.value = "不得派发";
const beforeDispatch = conversation.send();
currentOwner.value = otherFactory;
scrollWait.resolve();
await beforeDispatch;
await settle();
check(() => assert.equal(calls.length, 2));
scrollWait = undefined;

deferProfiles = true;
currentOwner.value = ownerA;
currentOwner.value = ownerB;
check(() => assert.equal(profiles.length, 2));
profiles[0].resolve({ model: "过期模型" });
await settle();
check(() => assert.equal(conversation.profileReady.value, false));
profiles[1].resolve({ model: "当前模型" });
await settle();
check(() => assert.equal(conversation.modelName.value, "当前模型"));
chatPage.value = false;
await settle();
check(() => assert.equal(conversation.profileReady.value, false));
check(() => assert.equal(conversation.modelName.value, ""));
testScope.stop();
delete globalThis.__scopedChatMock;

// 只编译页面与历史组件，不运行其API或路由生命周期。
for (const name of ["ChatWorkspace.vue", "ChatHistory.vue"]) {
  const parsed = parse(await readFile(new URL(name, source), "utf8"), {
    filename: name,
  });
  check(() => assert.deepEqual(parsed.errors, []));
  const script = compileScript(parsed.descriptor, { id: name });
  js(script.content, name + ".ts");
  const template = compileTemplate({
    source: parsed.descriptor.template.content,
    filename: name,
    id: name,
    compilerOptions: { bindingMetadata: script.bindings },
  });
  check(() => assert.deepEqual(template.errors, []));
}
check(() =>
  assert.equal(
    parseLocalUiInput(
      JSON.stringify({
        base_url: "http://127.0.0.1:18080",
        username: "fixture",
        password: "placeholder-only",
      }),
    ).base_url,
    "http://127.0.0.1:18080",
  ),
);
for (const base_url of [
  "https://example.invalid",
  "http://192.168.1.2:8080",
  "http://127.0.0.1:8080/?token=x",
])
  check(() =>
    assert.throws(
      () =>
        parseLocalUiInput(
          JSON.stringify({
            base_url,
            username: "fixture",
            password: "placeholder-only",
          }),
        ),
      /本机HTTP/,
    ),
  );
for (const file of ["../_smoke.mjs", "../_shots.mjs"]) {
  const content = await readFile(new URL(file, import.meta.url), "utf8");
  const parsed = ts.createSourceFile(
    file,
    content,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.JS,
  );
  let literalFills = 0;
  function visit(node) {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      node.expression.name.text === "fill" &&
      node.arguments.some((arg) => ts.isStringLiteralLike(arg))
    )
      literalFills++;
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  check(() => assert.equal(literalFills, 0));
  check(() => assert.ok(content.includes("readLocalUiInput")));
}
process.stdout.write(
  JSON.stringify({
    passed: true,
    checks,
    browser_sessions: 0,
    http_requests: 0,
    sql_queries: 0,
    model_calls: 0,
  }) + "\n",
);
