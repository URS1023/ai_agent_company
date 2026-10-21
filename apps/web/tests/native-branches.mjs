/** 分支编辑纯回归：真实消息树协议、Mock预约和SSE，不连接平台/原生服务/模型。 */
import assert from "node:assert/strict";
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
globalThis.__nativeBranchTest = { ApiError };
const result = await build({
  stdin: {
    contents:
      "export * from './useNativeTurn'; export * from './nativeBranchDraft';",
    resolveDir: fileURLToPath(new URL("../src/modules/chat", import.meta.url)),
    loader: "ts",
  },
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
  target: "es2022",
  logLevel: "silent",
  plugins: [
    {
      name: "branch-boundary",
      setup(plugin) {
        plugin.onResolve({ filter: /^vue$/ }, () => ({
          path: import.meta.resolve("vue"),
          external: true,
        }));
        plugin.onResolve({ filter: /api\/client$/ }, () => ({
          path: "api",
          namespace: "mock",
        }));
        plugin.onLoad({ filter: /.*/, namespace: "mock" }, () => ({
          contents: "export const {ApiError}=globalThis.__nativeBranchTest;",
        }));
        plugin.onResolve({ filter: /^@openwebui-chat$/ }, () => ({
          path: "library",
          namespace: "pure",
        }));
        plugin.onLoad({ filter: /.*/, namespace: "pure" }, () => ({
          contents:
            "export * from './src/reducer';export * from './src/events';",
          resolveDir: fileURLToPath(
            new URL("../../../packages/openwebui-chat", import.meta.url),
          ),
          loader: "ts",
        }));
      },
    },
  ],
});
const { useNativeTurn, editPayload, editedUserSaved } = await import(
  `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`
);
let reply = async () => {
  throw new Error("未配置Mock");
};
const transport = {
  active: () => true,
  async send(path, options = {}) {
    requests.push({ path, ...options });
    return reply(path, options);
  },
};
const tick = async () => {
  for (let i = 0; i < 8; i++)
    await new Promise((resolve) => setImmediate(resolve));
};
const sourceHistory = () => ({
  currentId: "old-assistant",
  messages: {
    "old-user": {
      id: "old-user",
      role: "user",
      parentId: null,
      childrenIds: ["old-assistant"],
      content: "原始提问",
    },
    "old-assistant": {
      id: "old-assistant",
      role: "assistant",
      parentId: "old-user",
      childrenIds: [],
      content: "",
      done: true,
      error: { detail: "旧失败" },
      model: "real-model",
    },
  },
});
const newHistory = (content = "修改后的提问") => ({
  currentId: "new-assistant",
  messages: {
    ...sourceHistory().messages,
    "new-user": {
      id: "new-user",
      role: "user",
      parentId: null,
      childrenIds: ["new-assistant"],
      content,
    },
    "new-assistant": {
      id: "new-assistant",
      role: "assistant",
      parentId: "new-user",
      childrenIds: [],
      content: "本次回答",
      done: true,
      model: "real-model",
    },
  },
});
const file = (id, status = "ready") => ({
  id,
  name: `${id}.txt`,
  content_type: "text/plain",
  size: 6,
  status,
  revision: 1,
});
const makeTurn = (kind, key, status = "unknown") => ({
  id: "branch-turn",
  conversation_id: "chat",
  identity_id: "identity",
  model_id: "real-model",
  status,
  user_message_id: kind === "regenerate" ? "old-user" : "new-user",
  assistant_message_id: "new-assistant",
  parent_message_id: null,
  idempotency_key: key,
  revision: 1,
  intent_kind: kind,
  source_message_id: kind === "regenerate" ? "old-assistant" : "old-user",
});
function fixture() {
  const errors = [],
    context = {
      detail: shallowRef({
        id: "chat",
        status: "ready",
        identity_id: "identity",
        revision: 4,
        active_turn_id: null,
      }),
      history: shallowRef(sourceHistory()),
      draft: shallowRef("普通草稿"),
      modelId: shallowRef("real-model"),
      attachmentIds: shallowRef(["ordinary-draft-file"]),
      canWrite: () => true,
      fail: (error) => errors.push(error),
      refresh: async () => {},
      files: () => [
        file("old-file"),
        file("replacement-file"),
        file("not-ready", "processing"),
      ],
      attachments: () => ({ "old-user": [file("old-file")] }),
    };
  const scope = effectScope();
  let turn;
  scope.run(() => {
    turn = useNativeTurn(transport, context);
  });
  return {
    context,
    turn,
    errors,
    stop() {
      turn.clear();
      scope.stop();
    },
  };
}
const draft = {
  sourceMessageId: "old-user",
  content: " 保留空格\n",
  attachmentMode: "inherit",
  attachmentIds: ["ordinary-draft-file"],
  inheritedAttachments: [file("old-file")],
};
check(() =>
  assert.deepEqual(editPayload(draft, []).payload, { content: " 保留空格\n" }),
);
check(() =>
  assert.deepEqual(
    editPayload({ ...draft, attachmentMode: "remove" }, []).payload
      .attachment_ids,
    [],
  ),
);
check(() =>
  assert.ok(
    editPayload({ ...draft, attachmentMode: "replace", attachmentIds: [] }, [])
      .errors.attachment_ids,
  ),
);
check(() =>
  assert.ok(
    editPayload(
      { ...draft, attachmentMode: "replace", attachmentIds: ["wrong"] },
      [file("wrong", "processing")],
    ).errors.attachment_ids,
  ),
);
check(() =>
  assert.deepEqual(
    editPayload(
      {
        ...draft,
        attachmentMode: "replace",
        attachmentIds: ["ready", "ready"],
      },
      [file("ready")],
    ).payload.attachment_ids,
    ["ready"],
  ),
);
check(() =>
  assert.ok(editPayload({ ...draft, content: "   " }, []).errors.content),
);
check(() =>
  assert.ok(
    editPayload({ ...draft, content: "字".repeat(40001) }, []).errors.content,
  ),
);
check(() =>
  assert.equal(
    editedUserSaved(
      newHistory(),
      makeTurn("edit_send", "key"),
      "old-user",
      "修改后的提问",
    ),
    true,
  ),
);
check(() =>
  assert.equal(
    editedUserSaved(
      sourceHistory(),
      makeTurn("edit_send", "key"),
      "old-user",
      "修改后的提问",
    ),
    false,
  ),
);
check(() =>
  assert.equal(
    editedUserSaved(
      newHistory("不准确"),
      makeTurn("edit_send", "key"),
      "old-user",
      "修改后的提问",
    ),
    false,
  ),
);
const wrongParent = newHistory();
wrongParent.messages["new-user"].parentId = "unrelated";
check(() =>
  assert.equal(
    editedUserSaved(
      wrongParent,
      makeTurn("edit_send", "key"),
      "old-user",
      "修改后的提问",
    ),
    false,
  ),
);
const missingLink = newHistory();
missingLink.messages["new-user"].childrenIds = [];
check(() =>
  assert.equal(
    editedUserSaved(
      missingLink,
      makeTurn("edit_send", "key"),
      "old-user",
      "修改后的提问",
    ),
    false,
  ),
);
// failed但done的准确助手允许显式再生成，body不得夹带新content、parent或附件。
requests.length = 0;
let sample = fixture();
reply = async (_, options) =>
  makeTurn("regenerate", options.headers["Idempotency-Key"]);
await sample.turn.regenerate("old-user");
check(() => assert.equal(requests.length, 0));
await sample.turn.regenerate("old-assistant");
check(() => assert.equal(requests.length, 1));
check(() =>
  assert.ok(requests[0].path.endsWith("/messages/old-assistant/regenerations")),
);
check(() =>
  assert.deepEqual(JSON.parse(requests[0].body), {
    revision: 4,
    model_id: "real-model",
  }),
);
check(() => assert.equal(sample.turn.uncertain.value, true));
check(() => assert.equal(sample.context.draft.value, "普通草稿"));
check(() =>
  assert.deepEqual(sample.context.attachmentIds.value, ["ordinary-draft-file"]),
);
await sample.turn.regenerate("old-assistant");
check(() => assert.equal(requests.length, 1));
sample.stop();
// 编辑附件默认继承本条source，不从普通composer附件选择覆盖。
requests.length = 0;
sample = fixture();
sample.turn.beginEdit("old-user");
check(() =>
  assert.deepEqual(
    sample.turn.editing.value.inheritedAttachments.map((row) => row.id),
    ["old-file"],
  ),
);
check(() => assert.deepEqual(sample.turn.editing.value.attachmentIds, []));
sample.turn.updateEdit({ content: "修改后的提问" });
reply = async () => {
  throw new ApiError(408, "timeout", "本次保存未确认");
};
await sample.turn.editAndSend();
const sent = requests[0],
  key = sent.headers["Idempotency-Key"];
check(() => assert.ok(sent.path.endsWith("/messages/old-user/edit-and-send")));
check(() =>
  assert.deepEqual(JSON.parse(sent.body), {
    revision: 4,
    model_id: "real-model",
    content: "修改后的提问",
  }),
);
check(() => assert.equal(sample.turn.editing.value.content, "修改后的提问"));
check(() =>
  assert.equal(
    sample.context.history.value.messages["old-user"].content,
    "原始提问",
  ),
);
sample.turn.cancelEdit();
sample.turn.updateEdit({ content: "不得覆盖未知意图" });
await sample.turn.editAndSend();
check(() => assert.equal(requests.length, 1));
check(() => assert.equal(sample.turn.editing.value.content, "修改后的提问"));
reply = async (path) =>
  path.includes("?idempotency_key=")
    ? { items: [makeTurn("edit_send", key)] }
    : makeTurn("edit_send", key);
sample.context.refresh = async () => {
  sample.context.history.value = newHistory("未匹配正文");
};
await sample.turn.verify();
check(() => assert.ok(requests[1].path.endsWith(encodeURIComponent(key))));
check(() => assert.ok(sample.turn.editing.value));
sample.context.refresh = async () => {
  sample.context.history.value = newHistory();
};
await sample.turn.verify();
check(() => assert.equal(sample.turn.editing.value, undefined));
check(() => assert.equal(sample.turn.uncertain.value, true));
check(() =>
  assert.equal(
    requests.filter((row) => row.path.endsWith("/edit-and-send")).length,
    1,
  ),
);
sample.stop();
// 替换的空/非ready集合不出网，移除明确发[]；202不当作保存成功。
requests.length = 0;
sample = fixture();
sample.turn.beginEdit("old-user");
sample.turn.updateEdit({ attachmentMode: "replace", attachmentIds: [] });
await sample.turn.editAndSend();
check(() => assert.equal(requests.length, 0));
check(() => assert.ok(sample.turn.editing.value.errors.attachment_ids));
sample.turn.updateEdit({ attachmentMode: "remove", content: "无附件提问" });
reply = async (_, options) =>
  makeTurn("edit_send", options.headers["Idempotency-Key"]);
await sample.turn.editAndSend();
check(() => assert.deepEqual(JSON.parse(requests[0].body).attachment_ids, []));
check(() => assert.ok(sample.turn.editing.value));
sample.stop();
requests.length = 0;
sample = fixture();
sample.turn.beginEdit("old-user");
sample.turn.updateEdit({
  attachmentMode: "replace",
  attachmentIds: ["replacement-file"],
});
await sample.turn.editAndSend();
check(() =>
  assert.deepEqual(JSON.parse(requests[0].body).attachment_ids, [
    "replacement-file",
  ]),
);
sample.stop();
// 原chat:messages提交后收编辑框，原分支仍保留，模型随后失败不会恢复重复编辑文本。
requests.length = 0;
sample = fixture();
sample.turn.beginEdit("old-user");
sample.turn.updateEdit({ content: "修改后的提问" });
let acceptedKey = "",
  sourceReads = 0;
reply = async (path, options) => {
  if (options.method === "POST") {
    acceptedKey = options.headers["Idempotency-Key"];
    return makeTurn("edit_send", acceptedKey, "running");
  }
  return makeTurn("edit_send", acceptedKey, "failed");
};
const encode = (sequence, event_type, data) =>
  `id: ${sequence}\ndata: ${JSON.stringify({ sequence, event_type, data })}\n\n`;
globalThis.fetch = async () =>
  new Response(
    new TextEncoder().encode(
      encode(1, "chat:messages", newHistory()) +
        encode(2, "state", { status: "failed" }),
    ),
    { headers: { "Content-Type": "text/event-stream" } },
  );
sample.context.refreshSources = async () => {
  sourceReads++;
};
sample.context.refresh = async () => {};
await sample.turn.editAndSend();
await tick();
check(() => assert.equal(sample.turn.editing.value, undefined));
check(() =>
  assert.equal(
    sample.context.history.value.messages["old-user"].content,
    "原始提问",
  ),
);
check(() =>
  assert.equal(
    sample.context.history.value.messages["new-user"].content,
    "修改后的提问",
  ),
);
check(() =>
  assert.equal(sample.context.history.value.currentId, "new-assistant"),
);
check(() => assert.equal(sourceReads, 1));
check(() => assert.equal(sample.turn.current.value.status, "failed"));
sample.stop();
console.log(`原生聊天分支纯检查通过：${checks}项；0HTTP/模型/数据库`);
