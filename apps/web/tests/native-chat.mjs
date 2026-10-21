/** 原生聊天宿主纯回归：Mock平台传输和字节流，不连接原服务、模型或数据库。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { effectScope, shallowRef } from "vue";
let checks = 0;
const check = (callback) => {
  callback();
  checks++;
};
const requests = [];
class ApiError extends Error {
  constructor(status, code, message, fields = {}) {
    super(message);
    Object.assign(this, { status, code, fields });
  }
}
let reply = async () => {
  throw new Error("未配置的Mock请求");
};
globalThis.__nativeChatTest = {
  ApiError,
  request: async (path, options = {}) => {
    requests.push({ path, ...options });
    return reply(path, options);
  },
};
// 任何没有显式配置的fetch立即失败，不能误触真实服务。
let streaming = async () => {
  throw new Error("禁止真实HTTP");
};
globalThis.fetch = (...args) => streaming(...args);
const packageRoot = fileURLToPath(
  new URL("../../../packages/openwebui-chat", import.meta.url),
);
const bundled = await build({
  stdin: {
    contents:
      "export * from './useNativeChat'; export * from './useNativeTurn'; export * from './useNativeFiles'; export * from './useNativeTransport';",
    resolveDir: fileURLToPath(new URL("../src/modules/chat", import.meta.url)),
    loader: "ts",
  },
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  target: "es2022",
  logLevel: "silent",
  plugins: [
    {
      name: "pure-native-boundary",
      setup(plugin) {
        plugin.onResolve({ filter: /^vue$/ }, () => ({
          path: import.meta.resolve("vue"),
          external: true,
        }));
        plugin.onResolve({ filter: /api\/client$/ }, () => ({
          path: "client",
          namespace: "mock",
        }));
        plugin.onLoad({ filter: /.*/, namespace: "mock" }, () => ({
          contents:
            "export const {ApiError,request}=globalThis.__nativeChatTest;",
        }));
        plugin.onResolve({ filter: /^@openwebui-chat$/ }, () => ({
          path: "island",
          namespace: "pure-island",
        }));
        plugin.onLoad({ filter: /.*/, namespace: "pure-island" }, () => ({
          contents:
            "export * from './src/reducer'; export * from './src/events';",
          resolveDir: packageRoot,
          loader: "ts",
        }));
      },
    },
  ],
});
const { useNativeTurn, useNativeChat, useNativeFiles, useNativeTransport } =
  await import(
    `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`
  );
const tick = async () => {
  for (let index = 0; index < 8; index++)
    await new Promise((resolve) => setImmediate(resolve));
};
const empty = () => ({ messages: {}, currentId: null });
const chatFixture = () => ({
  id: "conversation",
  identity_id: "identity",
  integration_id: "engine",
  title: "真实目录",
  status: "ready",
  revision: 3,
  active_turn_id: null,
  history: empty(),
  message_attachments: {},
});
const turnFixture = (status = "running") => ({
  id: "turn",
  conversation_id: "conversation",
  identity_id: "identity",
  model_id: "model",
  status,
  user_message_id: "user-message",
  assistant_message_id: "assistant-message",
  parent_message_id: null,
  idempotency_key: "stable-key",
  revision: 1,
});
const fileFixture = (status = "processing") => ({
  id: "platform-file",
  identity_id: "identity",
  status,
  revision: 1,
  source_file: {
    id: "source",
    name: "说明.txt",
    size: 12,
    content_type: "text/plain",
  },
});
const encode = (text) => new TextEncoder().encode(text);
const response = (text) =>
  new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encode(text));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
const frame = (sequence, event_type, data) =>
  `id: ${sequence}\ndata: ${JSON.stringify({ sequence, event_type, data })}\n\n`;
function turnContext() {
  const errors = [],
    context = {
      detail: shallowRef(chatFixture()),
      history: shallowRef(empty()),
      draft: shallowRef("保留原意图"),
      modelId: shallowRef("model"),
      attachmentIds: shallowRef([]),
      canWrite: () => true,
      fail: (error) => errors.push(error),
      refresh: async () => {
        if (context.detail.value)
          context.detail.value = {
            ...context.detail.value,
            active_turn_id: null,
          };
      },
    };
  return { context, errors };
}
// 超时前后只派发一次，原key只读找回，正文与附件不作为整棵客户端消息树回送。
requests.length = 0;
let scope = effectScope(),
  sample = turnContext(),
  turn;
scope.run(() => {
  turn = useNativeTurn(
    { send: globalThis.__nativeChatTest.request, active: () => true },
    sample.context,
  );
});
reply = async () => {
  throw new ApiError(408, "timeout", "未确认");
};
await turn.send();
const first = requests[0],
  originalKey = first.headers["Idempotency-Key"];
check(() => assert.ok(originalKey));
check(() => assert.equal(turn.uncertain.value, true));
check(() => assert.equal(sample.context.draft.value, "保留原意图"));
await turn.send();
check(() => assert.equal(requests.length, 1));
const posted = JSON.parse(first.body);
check(() =>
  assert.deepEqual(posted, {
    revision: 3,
    model_id: "model",
    content: "保留原意图",
    parent_message_id: null,
    attachment_ids: [],
  }),
);
reply = async (path) =>
  path.includes("?idempotency_key=")
    ? { items: [{ ...turnFixture("succeeded"), idempotency_key: originalKey }] }
    : turnFixture("succeeded");
await turn.verify();
check(() =>
  assert.ok(requests[1].path.endsWith(encodeURIComponent(originalKey))),
);
check(() =>
  assert.equal(
    requests.filter(
      (row) => row.path.endsWith("/turns") && row.method === "POST",
    ).length,
    1,
  ),
);
check(() => assert.equal(turn.current.value.status, "succeeded"));
check(() => assert.equal(turn.uncertain.value, false));
scope.stop();
// 序号重复不重复拼接，缓存从零回放覆盖已存片段；准确终态仍由平台GET确认。
requests.length = 0;
scope = effectScope();
sample = turnContext();
sample.context.history.value = {
  currentId: "assistant-message",
  messages: {
    "assistant-message": {
      id: "assistant-message",
      parentId: "user-message",
      role: "assistant",
      content: "旧片段",
    },
  },
};
scope.run(() => {
  turn = useNativeTurn(
    { send: globalThis.__nativeChatTest.request, active: () => true },
    sample.context,
  );
});
reply = async (path) =>
  path.includes("/turns/") ? turnFixture("succeeded") : turnFixture();
streaming = async () =>
  response(
    frame(1, "message", { content: "新" }) +
      frame(1, "message", { content: "重复" }) +
      frame(2, "message", { content: "正文" }) +
      frame(3, "state", { status: "succeeded" }),
  );
await turn.restore("turn");
await tick();
// restore读到succeeded时不会订阅；明确running后再恢复同一准确记录。
reply = async () => turnFixture();
await turn.restore("turn");
reply = async () => turnFixture("succeeded");
await tick();
check(() =>
  assert.equal(
    sample.context.history.value.messages["assistant-message"].content,
    "新正文",
  ),
);
check(() => assert.equal(turn.current.value.status, "succeeded"));
check(() => assert.equal(turn.uncertain.value, false));
turn.clear();
check(() => assert.equal(turn.current.value, undefined));
scope.stop();
// 持久多轮选择完成assistant作为parent，保留所有旧分支，不支持的user叶子不派发。
requests.length = 0;
scope = effectScope();
sample = turnContext();
sample.context.history.value = {
  currentId: "earlier",
  messages: {
    earlier: {
      id: "earlier",
      parentId: null,
      role: "assistant",
      content: "已完成",
      done: true,
      childrenIds: ["other"],
    },
  },
};
scope.run(() => {
  turn = useNativeTurn(
    { send: globalThis.__nativeChatTest.request, active: () => true },
    sample.context,
  );
});
reply = async () => ({
  ...turnFixture("unknown"),
  parent_message_id: "earlier",
});
await turn.send();
check(() =>
  assert.equal(JSON.parse(requests[0].body).parent_message_id, "earlier"),
);
check(() =>
  assert.ok(
    sample.context.history.value.messages.earlier.childrenIds.includes("other"),
  ),
);
check(() =>
  assert.ok(
    sample.context.history.value.messages.earlier.childrenIds.includes(
      "user-message",
    ),
  ),
);
turn.clear();
sample.context.detail.value.active_turn_id = null;
sample.context.draft.value = "不应发送";
sample.context.history.value.currentId = "user-message";
await turn.send();
check(() => assert.equal(requests.length, 1));
scope.stop();
// 附件只有ready可选；上传不会隐式刷新/重传，显式核对走revision，失权清理选择。
requests.length = 0;
scope = effectScope();
sample = turnContext();
scope.run(() => {
  turn = useNativeTurn(
    { send: globalThis.__nativeChatTest.request, active: () => true },
    sample.context,
  );
});
reply = async (path, options) =>
  options?.method === "POST"
    ? { turn: turnFixture("cancel_requested") }
    : turnFixture("waiting_approval");
await turn.restore("turn");
check(() => assert.equal(turn.canStop.value, true));
streaming = async () => response("event: reconcile_required\ndata: {}\n\n");
await turn.stop();
check(() => assert.equal(turn.current.value.status, "cancel_requested"));
check(() => assert.equal(turn.canStop.value, false));
await turn.stop();
check(() =>
  assert.equal(requests.filter((row) => row.path.endsWith("/stop")).length, 1),
);
await tick();
check(() => assert.equal(turn.uncertain.value, true));
turn.clear();
scope.stop();
// 明确失败响应不把终止Turn仍挂成活跃运行，用户可以修正输入后发起新的意图。
requests.length = 0;
scope = effectScope();
sample = turnContext();
scope.run(() => {
  turn = useNativeTurn(
    { send: globalThis.__nativeChatTest.request, active: () => true },
    sample.context,
  );
});
reply = async () => turnFixture("failed");
await turn.send();
check(() => assert.equal(sample.context.detail.value.active_turn_id, null));
check(() => assert.equal(turn.busy.value, false));
scope.stop();
// 附件只有ready可选；上传不会隐式刷新/重传，显式核对走revision，失权清理选择。
requests.length = 0;
scope = effectScope();
let files;
const fileErrors = [];
scope.run(() => {
  files = useNativeFiles(
    { send: globalThis.__nativeChatTest.request, active: () => true },
    () => "identity",
    () => true,
    (error) => fileErrors.push(error),
  );
});
await files.upload(new File(["bad"], "图片.png"));
check(() => assert.equal(requests.length, 0));
reply = async () => fileFixture();
await files.upload(new File(["说明"], "说明.txt"));
check(() => assert.equal(requests.length, 1));
check(() => assert.equal(requests[0].body instanceof FormData, true));
check(() => assert.ok(requests[0].headers["Idempotency-Key"]));
check(() => assert.deepEqual(files.selected.value, []));
files.attach(["platform-file"]);
check(() => assert.deepEqual(files.selected.value, []));
reply = async () => ({ ...fileFixture("ready"), revision: 2 });
await files.action("platform-file", "refreshes");
files.attach(["platform-file", "platform-file", "not-allowed"]);
check(() => assert.deepEqual(JSON.parse(requests[1].body), { revision: 1 }));
check(() => assert.deepEqual(files.selected.value, ["platform-file"]));
reply = async () => {
  throw new ApiError(403, "source_denied", "已失权");
};
await files.download("platform-file");
check(() => assert.deepEqual(files.files.value, []));
check(() => assert.deepEqual(files.selected.value, []));
scope.stop();
// SSE撤权必须立即清除目录、正文、原件入口，不能等用户刷新。
requests.length = 0;
scope = effectScope();
let pendingChat;
reply = async () => ({
  id: "pending-identity",
  status: "access_pending",
  revision: 1,
});
scope.run(() => {
  pendingChat = useNativeChat(
    () => true,
    () => true,
  );
});
check(() => assert.equal(pendingChat.detail.value, undefined));
const created = await pendingChat.create("engine", "需要授权的对话");
check(() => assert.equal(created, false));
check(() => assert.equal(pendingChat.identityReview.value, "pending-identity"));
check(() =>
  assert.equal(
    requests.some((row) => row.path.endsWith("/conversations")),
    false,
  ),
);
reply = async () => ({ id: "pending-identity", status: "ready", revision: 2 });
await pendingChat.reconcileIdentity();
check(() =>
  assert.equal(
    requests.at(-1).path,
    "/chat/native/identities/pending-identity/reconcile",
  ),
);
check(() => assert.equal(pendingChat.identityReview.value, ""));
scope.stop();
// SSE撤权必须立即清除目录、正文、原件入口，不能等用户刷新。
requests.length = 0;
scope = effectScope();
let chat;
reply = async (path) =>
  path.includes("/models")
    ? { items: [{ id: "model", name: "管理员授权模型" }] }
    : path.includes("/files?")
      ? { items: [fileFixture("ready")] }
      : path.endsWith("/turns")
        ? turnFixture()
        : chatFixture();
scope.run(() => {
  chat = useNativeChat(
    () => true,
    () => true,
  );
});
await chat.open("conversation");
chat.draft.value = "测试撤权";
streaming = async () => response("event: access_revoked\ndata: {}\n\n");
await chat.turn.send();
await tick();
check(() => assert.equal(chat.detail.value, undefined));
check(() => assert.deepEqual(chat.history.value, empty()));
check(() => assert.deepEqual(chat.files.files.value, []));
check(() => assert.deepEqual(chat.messageAttachments.value, {}));
check(() => assert.deepEqual(chat.models.value, []));
check(() => assert.ok(chat.error.value.includes("无权")));
scope.stop();
// 用户/工作区卸载时取消请求；迟到成功不能重新填回上一人的数据。
requests.length = 0;
scope = effectScope();
let finish;
reply = async () =>
  new Promise((resolve) => {
    finish = resolve;
  });
scope.run(() => {
  chat = useNativeChat(
    () => true,
    () => true,
  );
});
const pending = chat.open("conversation");
await tick();
scope.stop();
check(() => assert.equal(requests[0].signal.aborted, true));
finish(chatFixture());
await pending;
check(() => assert.equal(chat.detail.value, undefined));
check(() => assert.deepEqual(chat.history.value, empty()));
for (const filename of [
  "NativeChatIsland.vue",
  "NativeChatWorkspace.vue",
  "ChatWorkspace.vue",
]) {
  const source = await readFile(
    new URL(`../src/modules/chat/${filename}`, import.meta.url),
    "utf8",
  );
  check(() =>
    assert.equal(
      /localStorage\.token|Authorization:|native_user_id|native_chat_id/.test(
        source,
      ),
      false,
    ),
  );
}
const workspaceSource = await readFile(
  new URL("../src/modules/chat/ChatWorkspace.vue", import.meta.url),
  "utf8",
);
check(() => assert.ok(workspaceSource.includes("account.value.user.id")));
console.log(`原生聊天宿主纯检查通过：${checks}项；0HTTP/模型/数据库`);
