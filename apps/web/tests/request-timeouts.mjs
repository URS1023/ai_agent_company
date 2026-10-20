/** 共享请求等待时间纯检查：模拟fetch与定时器，实际网络调用为零。 */
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
let checks = 0;
const check = (action) => {
  action();
  checks++;
};
const source = await readFile(
  new URL("../src/api/client.ts", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;
const { request, ApiError, setCsrf } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);
const previousWindow = globalThis.window,
  previousFetch = globalThis.fetch;
const timers = [],
  cleared = [],
  calls = [];
globalThis.window = {
  setTimeout(action, delay) {
    timers.push({ action, delay });
    return timers.length;
  },
  clearTimeout(id) {
    cleared.push(id);
  },
};
let respond = async () =>
  new Response(JSON.stringify({ ok: true }), {
    headers: { "content-type": "application/json" },
  });
globalThis.fetch = async (url, options) => {
  calls.push({ url, options });
  return respond(url, options);
};
try {
  await request("/fixture");
  check(() => assert.equal(timers.at(-1).delay, 30000));
  check(() => assert.equal(calls.at(-1).url, "/api/v1/fixture"));
  await request("/fixture", { timeoutMs: 100000, asBlob: true });
  check(() => assert.equal(timers.at(-1).delay, 100000));
  check(() => assert.equal("timeoutMs" in calls.at(-1).options, false));
  check(() => assert.equal("asBlob" in calls.at(-1).options, false));
  await request("/fixture", { timeoutMs: 120000 });
  check(() => assert.equal(timers.at(-1).delay, 120000));
  const count = calls.length,
    timerCount = timers.length;
  for (const timeoutMs of [0, -1, 1.5, Infinity, NaN, "100", 120001, null]) {
    await assert.rejects(
      request("/fixture", { timeoutMs }),
      (failure) =>
        failure instanceof ApiError && failure.code === "invalid_timeout",
    );
    checks++;
  }
  check(() => assert.equal(calls.length, count));
  check(() => assert.equal(timers.length, timerCount));
  setCsrf("mock-csrf");
  const body = new FormData();
  body.append("file", new File(["中文"], "片段.txt"));
  await request("/fixture", { method: "POST", body, timeoutMs: 100000 });
  check(() =>
    assert.equal(calls.at(-1).options.headers.get("X-CSRF-Token"), "mock-csrf"),
  );
  check(() =>
    assert.equal(calls.at(-1).options.headers.has("Content-Type"), false),
  );
  check(() => assert.equal(calls.at(-1).options.credentials, "same-origin"));
  await request("/fixture", { method: "POST", body: "{}" });
  check(() =>
    assert.equal(
      calls.at(-1).options.headers.get("Content-Type"),
      "application/json",
    ),
  );
  const external = new AbortController();
  await request("/fixture", { signal: external.signal });
  const combined = calls.at(-1).options.signal;
  external.abort();
  check(() => assert.equal(combined.aborted, true));
  respond = async () =>
    new Response(new Uint8Array([1, 2, 3]), {
      headers: { "content-type": "application/octet-stream" },
    });
  const blob = await request("/fixture", { asBlob: true });
  const bytes = [...new Uint8Array(await blob.arrayBuffer())];
  check(() => assert.deepEqual(bytes, [1, 2, 3]));
  respond = async () =>
    new Response(
      JSON.stringify({
        error: {
          code: "permission_denied",
          message: "无权限",
          fields: { file: "不可访问" },
        },
      }),
      { status: 403, headers: { "content-type": "application/json" } },
    );
  await assert.rejects(
    request("/fixture"),
    (failure) =>
      failure instanceof ApiError &&
      failure.status === 403 &&
      failure.fields.file === "不可访问",
  );
  checks++;
  respond = async () => {
    throw new Error("模拟断线");
  };
  const beforeNetwork = calls.length;
  await assert.rejects(
    request("/fixture"),
    (failure) => failure instanceof ApiError && failure.status === 0,
  );
  checks++;
  check(() => assert.equal(calls.length, beforeNetwork + 1));
  respond = async () => {
    timers.at(-1).action();
    throw new DOMException("模拟截止", "AbortError");
  };
  await assert.rejects(
    request("/fixture", { timeoutMs: 100000 }),
    (failure) => failure instanceof ApiError && failure.status === 408,
  );
  checks++;
  check(() => assert.equal(cleared.length, timers.length));
  setCsrf("");
} finally {
  globalThis.window = previousWindow;
  globalThis.fetch = previousFetch;
}
console.log(
  JSON.stringify({ passed: true, assertions: checks, actual_http: 0 }),
);
