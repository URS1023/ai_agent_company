/** 纯消息协议回归：本地字节流、真实源算法与来源SHA，不发HTTP或模型请求。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(
  new URL("../../../apps/web/package.json", import.meta.url),
);
const { build } = require("esbuild");
const root = new URL("../", import.meta.url);
const bundled = await build({
  stdin: {
    contents:
      "export * from './src/reducer'; export * from './src/messages'; export * from './src/events';",
    resolveDir: fileURLToPath(root),
    loader: "ts",
  },
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
  target: "es2022",
  logLevel: "silent",
});
const {
  reduceNativeFrame,
  currentMessages,
  deepestChild,
  siblings,
  messageError,
  readNativeEvents,
} = await import(
  `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString("base64")}`
);
let checks = 0;
const check = (callback) => {
  callback();
  checks++;
};
const history = {
  currentId: "a",
  messages: {
    u: {
      id: "u",
      parentId: null,
      role: "user",
      content: "温度",
      childrenIds: ["a", "b"],
    },
    a: {
      id: "a",
      parentId: "u",
      role: "assistant",
      content: "",
      childrenIds: [],
    },
    b: {
      id: "b",
      parentId: "u",
      role: "assistant",
      content: "另一分支",
      done: true,
      childrenIds: [],
    },
  },
};
const merged = reduceNativeFrame(history, "fresh-assistant", {
  sequence: 1,
  event_type: "chat:messages",
  data: {
    currentId: "fresh-assistant",
    messages: {
      fresh: {
        id: "fresh",
        role: "user",
        parentId: null,
        childrenIds: ["fresh-assistant"],
        content: "新根分支",
        files: [{ id: "private-native", url: "http://private.invalid" }],
        meta: { secret: true },
      },
      "fresh-assistant": {
        id: "fresh-assistant",
        role: "assistant",
        parentId: "fresh",
        childrenIds: [],
        content: "",
      },
    },
  },
});
check(() => assert.equal(merged.messages.u.content, "温度"));
check(() => assert.equal(merged.messages.fresh.content, "新根分支"));
check(() => assert.equal(merged.currentId, "fresh-assistant"));
check(() => assert.equal(merged.messages.fresh.files, undefined));
check(() => assert.equal(merged.messages.fresh.meta, undefined));
check(() => assert.equal(history.messages.fresh, undefined));
check(() =>
  assert.equal(
    messageError({ detail: "Endpoint is unavailable" }),
    "Endpoint is unavailable",
  ),
);
check(() =>
  assert.equal(messageError({ content: "原生服务未连接" }), "原生服务未连接"),
);
check(() =>
  assert.equal(messageError({ unknown: "内部值" }), "本次回答未完成"),
);
check(() =>
  assert.deepEqual(
    currentMessages(history).map((row) => row.id),
    ["u", "a"],
  ),
);
check(() =>
  assert.deepEqual(siblings(history, history.messages.a), ["a", "b"]),
);
check(() => assert.equal(deepestChild(history, "u"), "b"));
check(() =>
  assert.deepEqual(currentMessages({ currentId: null, messages: {} }), []),
);
const cycle = structuredClone(history);
cycle.messages.u.parentId = "a";
cycle.messages.b.childrenIds = ["u"];
check(() => assert.equal(currentMessages(cycle).length, 2));
check(() => assert.equal(deepestChild(cycle, "u"), "u"));
let result = reduceNativeFrame(history, "a", {
  sequence: 1,
  event_type: "chat:completion",
  data: { choices: [{ delta: { content: "实测" } }] },
});
result = reduceNativeFrame(result, "a", {
  sequence: 2,
  event_type: "chat:completion",
  data: { choices: [{ delta: { content: "72°C" } }] },
});
check(() => assert.equal(result.messages.a.content, "实测72°C"));
check(() => assert.equal(history.messages.a.content, ""));
result = reduceNativeFrame(result, "a", {
  sequence: 3,
  event_type: "replace",
  data: { content: "准确终态" },
});
check(() => assert.equal(result.messages.a.content, "准确终态"));
result = reduceNativeFrame(result, "a", {
  sequence: 4,
  event_type: "chat:completion",
  data: { done: true, usage: { total_tokens: 12 } },
});
check(() => assert.equal(result.messages.a.done, true));
check(() => assert.equal(result.messages.a.usage.total_tokens, 12));
result = reduceNativeFrame(history, "a", {
  sequence: 1,
  event_type: "chat:completion",
  data: {
    output: [
      {
        id: "o",
        type: "message",
        role: "assistant",
        content: [{ type: "output_text", text: "原输出" }],
      },
    ],
  },
});
check(() => assert.equal(result.messages.a.content, "原输出"));
check(() => assert.equal(result.messages.a.output[0].id, "o"));
check(() =>
  assert.deepEqual(
    reduceNativeFrame(history, "missing", {
      sequence: 1,
      event_type: "replace",
      data: { content: "丢弃" },
    }),
    history,
  ),
);
const encode = (text) => new TextEncoder().encode(text);
const stream = (chunks) =>
  new ReadableStream({
    start(controller) {
      for (const part of chunks) controller.enqueue(part);
      controller.close();
    },
  });
const text =
  'id: 1\ndata: {"sequence":1,"event_type":"message","data":{"content":"中文"}}\n\n: heartbeat\n\nid: 2\ndata: {"sequence":2,"event_type":"state","data":{"status":"succeeded"}}\n\n';
const bytes = encode(text),
  frames = [];
// 故意切开UTF-8多字节，验证原SSE parser不乱码、不按传输块冒充消息。
for await (const frame of readNativeEvents(
  stream([...bytes].map((value) => Uint8Array.of(value))),
))
  frames.push(frame);
check(() => assert.equal(frames.length, 2));
check(() => assert.equal(frames[0].data.content, "中文"));
check(() => assert.equal(frames[1].data.status, "succeeded"));
for (const kind of ["access_revoked", "reconcile_required"]) {
  const received = [];
  for await (const frame of readNativeEvents(
    stream([
      encode(
        `event: ${kind}\ndata: {}\n\ndata: {"sequence":99,"event_type":"message","data":{"content":"不应读取"}}\n\n`,
      ),
    ]),
  ))
    received.push(frame);
  check(() => assert.equal(received.length, 1));
  check(() => assert.equal(received[0].event_type, kind));
}
await assert.rejects(async () => {
  for await (const _ of readNativeEvents(
    stream([encode('data: {"sequence":0,"event_type":"message"}\n\n')]),
  )) {
  }
}, /无法识别/);
checks++;
await assert.rejects(async () => {
  for await (const _ of readNativeEvents(
    stream([encode("data: broken\n\n")]),
  )) {
  }
});
checks++;
const manifest = JSON.parse(
  await readFile(new URL("SOURCE_MANIFEST.json", root), "utf8"),
);
check(() => assert.equal(manifest.version, "0.12.3"));
const license = await readFile(new URL(manifest.license, root), "utf8");
check(() => assert.ok(license.includes("Open WebUI")));
for (const entry of manifest.files) {
  // 完整clone不依赖原仓库：仅在本机锁定来源仍存在时核原SHA，缺少来源不跳过包内检查。
  const original = await readFile(
    new URL(
      `../../.local/native_windows/sources/openwebui/${entry.source}`,
      root,
    ),
  ).catch((error) => (error.code === "ENOENT" ? null : Promise.reject(error)));
  if (original)
    check(() =>
      assert.equal(
        createHash("sha256").update(original).digest("hex"),
        entry.sha256,
      ),
    );
  if (entry.target)
    check(() => assert.ok(entry.target.startsWith("src/reused/")));
}
for (const name of [
  "StructuredOutputRenderer",
  "ConsecutiveDetailsGroup",
  "Collapsible",
]) {
  const source = await readFile(
    new URL(`src/reused/${name}.svelte`, root),
    "utf8",
  );
  check(() =>
    assert.equal(/\$lib|\$app|localStorage|fetch\(/.test(source), false),
  );
}
console.log(`Open WebUI消息/来源纯检查通过：${checks}项；0HTTP/模型/数据库`);
