/** 独立真实组件验收：仅about:blank/本地Blob，无平台服务、账号、模型或数据库。 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(
  new URL("../../../apps/web/package.json", import.meta.url),
);
const { chromium } = require("@playwright/test");
const source = await readFile(
  new URL("../dist/index.js", import.meta.url),
  "utf8",
);
const resultDirectory = new URL(
  "../../../apps/web/test-results/openwebui-component/",
  import.meta.url,
);
await mkdir(resultDirectory, { recursive: true });
const receipt = new URL("acceptance.json", resultDirectory);
const state = {
  status: "running",
  stage: "launch",
  requests: 0,
  models: 0,
  business_queries: 0,
  browsers: 0,
  pages: 0,
  errors: [],
};
let server,
  browser,
  receiptCreated = false;
const started = Date.now();
// 独立watchdog不依赖页面微任务；页面饥饿时也能强停本脚本持有的BrowserServer。
let rejectDeadline;
const deadline = new Promise((_, reject) => {
  rejectDeadline = reject;
});
const timer = setTimeout(() => {
  state.stage += ":deadline";
  rejectDeadline(new Error("component_deadline_exceeded"));
  void server?.kill();
}, 45000);
try {
  await writeFile(receipt, JSON.stringify(state, null, 2), { flag: "wx" });
  receiptCreated = true;
  await Promise.race([
    deadline,
    (async () => {
      server = await chromium.launchServer({
        executablePath:
          process.env.E2E_CHROME_PATH ||
          "C:/Program Files/Google/Chrome/Application/chrome.exe",
        headless: true,
        timeout: 10000,
      });
      state.browsers++;
      browser = await chromium.connect(server.wsEndpoint(), { timeout: 10000 });
      const context = await browser.newContext({
        viewport: { width: 1280, height: 900 },
        serviceWorkers: "block",
      });
      await context.route("**/*", (route) => {
        state.requests++;
        state.errors.push("unexpected_request");
        return route.abort();
      });
      const page = await context.newPage();
      state.pages++;
      page.setDefaultTimeout(5000);
      page.on("pageerror", (error) => state.errors.push(error.message));
      await page.setContent(
        '<html lang="zh-CN"><body style="margin:0"><main id="host" style="height:860px"></main></body></html>',
      );
      state.stage = "mount";
      await page.evaluate(async (code) => {
        // 模拟业务回调仅记录事件；任何传输调用都是测试失败。
        globalThis.fetch = async () => {
          throw new Error("fixture_network_forbidden");
        };
        globalThis.WebSocket = class {
          constructor() {
            throw new Error("fixture_socket_forbidden");
          }
        };
        globalThis.EventSource = class {
          constructor() {
            throw new Error("fixture_events_forbidden");
          }
        };
        const url = URL.createObjectURL(
          new Blob([code], { type: "text/javascript" }),
        );
        const library = await import(url);
        URL.revokeObjectURL(url);
        const snapshot = {
          conversationId: "pure-component",
          displayName: "组件验收",
          modelId: "fixture-model",
          models: [{ id: "fixture-model", name: "本地组件模型" }],
          canWrite: true,
          busy: false,
          status: "ready",
          draft: "",
          files: [],
          attachmentIds: [],
          messageAttachments: {},
          error: "",
          canStop: false,
          uncertain: false,
          history: {
            currentId: "assistant",
            messages: {
              user: {
                id: "user",
                parentId: null,
                role: "user",
                content: "本地组件验收",
                childrenIds: ["assistant"],
              },
              assistant: {
                id: "assistant",
                parentId: "user",
                role: "assistant",
                content: "组件初始内容",
                done: true,
                model: "fixture-model",
                childrenIds: [],
              },
            },
          },
        };
        const calls = [];
        const actions = Object.fromEntries(
          [
            "draft",
            "model",
            "send",
            "stop",
            "verify",
            "branch",
            "upload",
            "attach",
            "refreshFile",
            "reconcileFile",
            "download",
            "resolve",
          ].map((name) => [
            name,
            (value) =>
              calls.push({
                name,
                value: typeof value === "string" ? value : undefined,
              }),
          ]),
        );
        globalThis.__fixture = {
          snapshot,
          actions,
          calls,
          host: document.querySelector("#host"),
          handle: library.mountNativeChat(document.querySelector("#host"), {
            snapshot,
            actions,
          }),
        };
      }, source);
      await page
        .getByLabel("输入消息", { exact: true })
        .waitFor({ state: "visible" });
      assert.equal(
        await page.getByLabel("对话模型").inputValue(),
        "fixture-model",
      );
      assert.equal(
        await page
          .getByLabel("对话内容")
          .getByText("组件初始内容", { exact: true })
          .count(),
        1,
      );
      state.stage = "updates";
      await page.evaluate(async () => {
        const fixture = globalThis.__fixture;
        // 每次props替换后必须让浏览器真实frame前进，旧afterUpdate微任务循环会在此饿死。
        for (let index = 1; index <= 4; index++) {
          const snapshot = structuredClone(fixture.snapshot);
          snapshot.history.messages.assistant.content = `组件更新${index}`;
          fixture.snapshot = snapshot;
          fixture.handle.update({ snapshot, actions: fixture.actions });
          await new Promise((resolve) => requestAnimationFrame(resolve));
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      });
      await page
        .getByLabel("对话内容")
        .getByText("组件更新4", { exact: true })
        .waitFor();
      await page.getByLabel("输入消息", { exact: true }).fill("验证输入事件");
      assert.equal(
        await page.evaluate(() =>
          globalThis.__fixture.calls.some(
            (row) => row.name === "draft" && row.value === "验证输入事件",
          ),
        ),
        true,
      );
      state.stage = "screenshot";
      await page.screenshot({
        path: fileURLToPath(new URL("component.png", resultDirectory)),
        animations: "disabled",
        timeout: 5000,
      });
      state.stage = "unmount";
      assert.equal(
        await page.evaluate(async () => {
          const fixture = globalThis.__fixture;
          fixture.handle.unmount();
          await new Promise((resolve) => setTimeout(resolve, 0));
          return fixture.host.shadowRoot.querySelector("textarea") === null;
        }),
        true,
      );
      assert.deepEqual(state.errors, []);
      assert.equal(state.requests, 0);
      state.status = "passed";
    })(),
  ]);
} catch (error) {
  state.status = "failed";
  state.error = error instanceof Error ? error.message : String(error);
} finally {
  clearTimeout(timer);
  // kill只针对本脚本持有的server，不枚举或关闭用户Chrome。
  await server?.kill().catch(() => undefined);
  state.elapsed_ms = Date.now() - started;
  if (receiptCreated) await writeFile(receipt, JSON.stringify(state, null, 2));
}
console.log(JSON.stringify(state));
process.exitCode = state.status === "passed" ? 0 : 1;
