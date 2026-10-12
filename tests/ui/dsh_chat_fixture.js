"use strict";
// 使用真实聊天页面与静态资源，所有接口由浏览器内存夹具回答，不启动服务。
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const ROOT = path.resolve(__dirname, "../..");

function installFixture(features = {}) {
  localStorage.setItem("agent_token", "offline-fixture");
  localStorage.setItem("agent_workspace_id", "offline-workspace");
  const fixture = window.__chatFixture = {
    calls: [], streams: [], requests: 0,
    capabilities: { engine: "dsh", protocol_version: 2,
      modes: [{ id: "standard", available: true }, { id: "web", available: false, reason: "未配置搜索服务" }, { id: "deep", available: false, reason: "未配置研究服务" }],
      features: { queue: true, steer: false, reasoning: true, questions: false, tasks: true, deliveries: true },
      skills: [{ id: "business-analysis", name: "业务分析", description: "分析已选企业资料", available: true }],
      plugins: [{ id: "business-tools", name: "业务工具", description: "使用已发布业务能力", available: true }],
    },
    detail: { id: "fixture", session_id: "session-fixture", title: "业务分析", messages: [], last_run: null,
      projections: { mode: "standard", model: null, queue: [], tasks: [], deliveries: [], pending_questions: [] }, cursor: 0 },
  };
  Object.assign(fixture.capabilities.features, features);
  const saved = sessionStorage.getItem("dsh-fixture-detail");
  if (saved) fixture.detail = JSON.parse(saved);
  fixture.requests = Number(sessionStorage.getItem("dsh-fixture-requests") || 0);
  fixture.persist = () => { sessionStorage.setItem("dsh-fixture-detail", JSON.stringify(fixture.detail)); sessionStorage.setItem("dsh-fixture-requests", String(fixture.requests)); };
  fixture.emit = (event, data) => {
    fixture.detail.cursor += 1;
    fixture.persist();
    const frame = `id: ${fixture.detail.cursor}\nevent: ${event}\ndata: ${JSON.stringify({ session_id: "session-fixture", ...data })}\n\n`;
    for (const stream of [...fixture.streams]) { try { stream.enqueue(new TextEncoder().encode(frame)); } catch (_) {} }
  };
  fixture.disconnect = () => { fixture.persist(); for (const stream of fixture.streams.splice(0)) stream.close(); };
  fixture.respond = (value, status = 200) => Response.json(value, { status });
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const method = options.method || "GET";
    const body = typeof options.body === "string" ? JSON.parse(options.body) : {};
    fixture.calls.push({ path: url.pathname, method, body });
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) throw Error("夹具阻止未声明请求");
    const route = url.pathname;
    if (route === "/api/auth/me") return fixture.respond({ user: { id: "fixture-user", display_name: "测试用户" }, workspace: { id: "offline-workspace", name: "离线工作区" }, role: "member", permissions: [] });
    if (route === "/api/chat/capabilities") return fixture.respond(fixture.capabilities);
    if (route === "/api/v1/platform/navigation") return fixture.respond({ settings: { "site.name": "智慧云脑 Cloud Brain" }, items: [], groups: [], modules: [] });
    if (route === "/api/chat/models") return fixture.respond({ items: [{ profile_name: "reasoning", model: "离线模型", label: "离线模型", default: true }], default_profile: "reasoning" });
    if (route === "/api/chat/conversations" && method === "GET") return fixture.respond({ items: [fixture.detail], count: 1 });
    if (route === "/api/chat/conversations" && method === "POST") { fixture.detail.title = body.title; return fixture.respond(fixture.detail); }
    if (route === "/api/chat/conversations/fixture") return fixture.respond(fixture.detail);
    if (route === "/api/chat/conversations/fixture/events") {
      let current;
      const stream = new ReadableStream({ start(controller) {
        current = controller; fixture.streams.push(controller);
        controller.enqueue(new TextEncoder().encode(`event: session.snapshot\ndata: ${JSON.stringify(fixture.detail)}\n\n`));
      }, cancel() { fixture.streams = fixture.streams.filter(item => item !== current); } });
      options.signal?.addEventListener("abort", () => { try { current.error(new DOMException("取消订阅", "AbortError")); } catch (_) {} }, { once: true });
      return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
    }
    if (route === "/api/chat/conversations/fixture/send") {
      if (body.delivery_mode === "steer") {
        const active = fixture.detail.projections.queue.find(item => item.status === "running");
        const message = { id: "steer-" + body.request_id, run_id: active.run_id, request_id: body.request_id,
          role: "user", content: body.content, parts: [{ type: "text", text: body.content }] };
        fixture.detail.messages.push(message);
        fixture.emit("message.steered", { user_message: message, steering_request_id: body.request_id });
        return fixture.respond({ accepted: true, request_id: body.request_id, run_id: active.run_id,
          delivery_mode: "steer", status: "running", user_message: message });
      }
      const runId = "run-" + (++fixture.requests);
      const queued = fixture.detail.projections.queue.some(item => item.status === "running");
      const status = queued ? "queued" : "running";
      const message = { id: "user-" + runId, run_id: runId, request_id: body.request_id, role: "user", content: body.content, parts: [] };
      fixture.detail.messages.push(message);
      fixture.detail.last_run = { id: runId, status, context_snapshot: { mode: body.mode } };
      fixture.detail.projections.queue.push({ id: runId, run_id: runId, request_id: body.request_id, content: body.content, status });
      queueMicrotask(() => fixture.emit("queue.updated", { run_id: runId, queue: fixture.detail.projections.queue }));
      return fixture.respond({ accepted: true, request_id: body.request_id, run_id: runId, session_id: "session-fixture", delivery_mode: body.delivery_mode, status });
    }
    const queue = route.match(/\/queue\/([^/]+)$/);
    if (queue) {
      const item = fixture.detail.projections.queue.find(value => value.id === queue[1]);
      let userMessage, task;
      if (body.action === "edit") {
        item.content = body.content;
        userMessage = fixture.detail.messages.find(message => message.role === "user" && message.run_id === item.run_id);
        userMessage.content = body.content;
        userMessage.parts = [{ type: "text", text: body.content }, ...userMessage.parts.filter(part => part.type !== "text")];
        task = { id: "task-queue", task_id: "task-queue", title: body.content, status: "queued", required_deliveries: [{ type: /Word/i.test(body.content) ? "document" : "presentation", count: 1 }] };
        fixture.detail.projections.tasks = [...fixture.detail.projections.tasks.filter(value => value.id !== task.id), task];
      }
      if (body.action === "remove") fixture.detail.projections.queue = fixture.detail.projections.queue.filter(value => value.id !== queue[1]);
      fixture.emit("queue.updated", { queue: fixture.detail.projections.queue, ...(userMessage ? { user_message: { id: userMessage.id, run_id: userMessage.run_id, role: "user", content: userMessage.content, request_id: userMessage.request_id }, task } : {}) });
      return fixture.respond({ accepted: true });
    }
    if (/\/cancel$/.test(route)) return fixture.respond({ status: "stopping" });
    if (route === "/api/artifacts/artifact-1/use") return fixture.respond({ id: "file-artifact-1", name: "分析结果.docx" });
    if (/\/questions\/[^/]+(?:\/answer)?$/.test(route)) {
      const questionId = route.split("/").at(route.endsWith("/answer") ? -2 : -1);
      fixture.detail.projections.pending_questions = [];
      fixture.emit("question.resolved", { question_id: questionId });
      return fixture.respond({ accepted: true });
    }
    if (route === "/api/v1/ai/business-actions") return fixture.respond({ data: [] });
    return fixture.respond({ items: [], data: [], navigation: [], modules: [] });
  };
}

async function openFixture(viewport = { width: 1440, height: 900 }, features = {}, options = {}) {
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true,
    args: ["--disable-background-networking", "--disable-component-update", "--no-first-run", "--disable-sync"] });
  const page = await browser.newPage();
  const failures = [], blocked = [];
  page.on("pageerror", error => failures.push(error.message));
  await page.setViewport(viewport);
  await page.setRequestInterception(true);
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin !== "http://test5-offline.invalid") { blocked.push(url.href); request.abort(); return; }
    if (options.routes?.[url.pathname]) { request.respond({ status: 200, ...options.routes[url.pathname] }); return; }
    const relative = url.pathname === "/ai" ? "index.html" : decodeURIComponent(url.pathname).replace(/^\/+/, "");
    const file = path.resolve(ROOT, "apps/web", relative);
    if (!file.startsWith(path.resolve(ROOT, "apps/web") + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { request.respond({ status: 404, body: "未声明的离线资源" }); return; }
    const type = ({ ".html": "text/html", ".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml" })[path.extname(file)] || "application/octet-stream";
    request.respond({ status: 200, contentType: type, body: fs.readFileSync(file) });
  });
  await page.evaluateOnNewDocument(installFixture, features);
  try {
    await page.goto("http://test5-offline.invalid/ai?cid=fixture", { waitUntil: "networkidle0" });
    await page.waitForFunction(() => document.querySelector("#chat-model-select")?.disabled === false, { timeout: 10000 });
  } catch (error) {
    const diagnostic = await page.evaluate(() => ({ text: document.body.innerText, calls: window.__chatFixture?.calls }));
    await browser.close(); throw Error(JSON.stringify({ message: error.message, failures, diagnostic }));
  }
  return { browser, page, failures, blocked };
}

async function geometry(page) {
  return page.evaluate(() => {
    const result = { width: innerWidth, height: innerHeight, overflow: document.documentElement.scrollWidth > innerWidth };
    for (const selector of ["#sidebar", ".composer-wrap", "#composer-form", "#prompt-textarea", "#scroll-root", "#messages"]) {
      const node = document.querySelector(selector), rect = node.getBoundingClientRect();
      result[selector] = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }
    return result;
  });
}

async function captureBaseline() {
  const out = path.join(ROOT, ".implementation/ui-results/dsh-chat-baseline"); fs.mkdirSync(out, { recursive: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, failures } = await openFixture(viewport);
    try {
      results.push(await geometry(page));
      await page.screenshot({ path: path.join(out, `empty-${viewport.width}.png`) });
      await page.click("#add-menu-btn");
      await page.screenshot({ path: path.join(out, `menu-${viewport.width}.png`) });
      if (failures.length) throw Error(failures.join("\n"));
    } finally { await browser.close(); }
  }
  fs.writeFileSync(path.join(out, "geometry.json"), JSON.stringify(results, null, 2));
  console.log("PASS 离线桌面/移动端视图基线：" + out);
}
module.exports = { ROOT, openFixture, geometry };
if (require.main === module) captureBaseline().catch(error => { console.error(error); process.exitCode = 1; });
