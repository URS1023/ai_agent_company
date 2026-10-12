"use strict";
// Production chat lifecycle in a Node VM: no browser, database, server or model requests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.resolve(__dirname, "../../apps/web/app.js"), "utf8");
const start = source.indexOf("async function sendMessage()");
// 以稳定哨兵注释为切片终点（R27），避免紧随其后的菜单注释措辞变化导致切片漂移。
const end = source.indexOf("// ==== stream-recovery-slice-end ====", start);
assert(start > 0 && end > start);

class NodeFixture {
  constructor() { this.children = []; this.className = ""; this.textContent = ""; this.innerHTML = ""; this.isConnected = true; this.value = ""; }
  get lastElementChild() { return this.children.at(-1); }
  get classList() { return { contains: name => this.className.split(" ").includes(name) }; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  setAttribute() {}
  removeAttribute() {}
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  querySelector(selector) { return this.children.find(child => child.className.split(" ").includes(selector.slice(1))) || null; }
}
const frame = (id, event, data) => (id ? "id: " + id + "\n" : "") + "event: " + event + "\ndata: " + JSON.stringify(data) + "\n\n";
function events(text, close = true) {
  return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(text)); if (close) controller.close(); } }));
}
function fixture() {
  const textarea = new NodeFixture(), messages = new NodeFixture();
  const context = {
    Response, ReadableStream, TextDecoder, DOMException, AbortController, console,
    crypto: require("node:crypto").webcrypto,
    document: { createElement: () => new NodeFixture() },
    $: selector => selector === "#prompt-textarea" ? textarea : null,
    mdRender: value => value, esc: value => String(value), scrollToBottom() {},
    renderMessages() {}, updateSendState() {}, loadConversations: async () => {},
    appendBusinessQuestion() {}, appendFollowupQuestions() {},
    appendAssistantRow: () => messages.appendChild(new NodeFixture()),
    toast(message) { context.notices.push(message); }, notices: [],
    localStorage: { getItem: () => "" }, API: { token: "", post: async () => ({ status: "running" }) },
    PLACEHOLDER_CONVERSATION_TITLES: new Set(),
    state: { current: { id: "conversation", title: "Saved", messages: [{ role: "user", content: "问题" }] },
      modelsReady: true, chatModels: [{ profile_name: "test" }], modelProfile: "test", fileIds: [], kbIds: [], datasourceIds: [],
      streaming: true, abortController: new AbortController(), activeRunId: "run" },
    setTimeout(callback, delay) { if (delay >= 1000) queueMicrotask(callback); return 1; }, clearTimeout() {},
  };
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  context.makeStream = () => ({ body: new NodeFixture(), toolMap: {}, segments: [], active: null, timer: null, error: "", done: false });
  context.textarea = textarea;
  return context;
}

async function verifyProductionRecovery() {
  let checks = 0;
  {
    // 删除被服务端拒绝时，仍能回到原会话查看运行进度和历史。
    const c = fixture();
    c.confirm = () => true; c.renderConversationList = () => {};
    c.conversationSummaryCache = new Map(); c.conversationSummaryLoads = new Map();
    c.state.conversations = [{ id: "conversation" }];
    c.API.del = async () => { throw Error("任务仍在运行，不能删除"); };
    const deleteStart = source.indexOf("async function deleteConversation(id)");
    vm.runInContext(source.slice(deleteStart, source.indexOf("function renderChatModels()", deleteStart)), c);
    await c.deleteConversation("conversation");
    assert.equal(c.state.current.id, "conversation"); assert.equal(c.state.conversations.length, 1);
    assert(c.notices[0].includes("任务仍在运行")); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(), requests = [];
    c.fetch = async (url, options) => {
      requests.push({ url, method: options.method || "GET" });
      return events(frame(2, "message.delta", { delta: "不能重复", iteration: 0 }) + frame(3, "message.delta", { delta: "完整结果", iteration: 0 }) + frame(4, "done", {}), false);
    };
    await c.followChatRun(events(frame(1, "run.created", { run_id: "run" }) + frame(2, "message.delta", { delta: "已核对：", iteration: 0 })), stream, "conversation", 1, "问题");
    assert.equal(stream.segments[0].raw, "已核对：完整结果");
    assert.equal(stream.lastEventId, 4); assert.equal(stream.done, true);
    assert.deepEqual(requests, [{ url: "/api/chat/runs/run/events?after=2", method: "GET" }]);
    checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(); let reads = 0;
    c.fetch = async () => { reads++; if (reads < 4) throw new TypeError("offline"); return events(frame(5, "message.delta", { delta: "恢复的结果" }) + frame(6, "done", {})); };
    await c.followChatRun(null, stream, "conversation", 1, "问题");
    assert.equal(reads, 4); assert.equal(stream.segments[0].raw, "恢复的结果"); assert.equal(stream.error, ""); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(), requests = []; c.state.activeRunId = null;
    stream.previousRunId = "previous";
    c.fetch = async url => {
      requests.push(url);
      if (url.endsWith("/conversation")) return Response.json({ last_run: { id: "accepted" }, messages: [{ role: "user", content: "问题" }] });
      return events(frame(7, "message.delta", { delta: "已完成" }) + frame(8, "done", {}));
    };
    await c.followChatRun(null, stream, "conversation", 1, "问题");
    assert.equal(c.state.activeRunId, "accepted"); assert.equal(requests.length, 2); assert(requests[1].includes("accepted/events")); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(); c.state.activeRunId = null; stream.previousRunId = "previous"; stream.prompt = "问题";
    c.fetch = async () => Response.json({ last_run: { id: "previous" }, messages: [{ role: "user", content: "问题" }] });
    await assert.rejects(c.followChatRun(null, stream, "conversation", 1, "问题"), error => { c.setChatStreamError(stream, error); return error.notAccepted; });
    assert.equal(c.textarea.value, "问题"); assert.equal(c.state.activeRunId, null); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(); let reads = 0; stream.done = true;
    const saved = [{ role: "user", content: "问题" }, { role: "assistant", parts: [{ type: "text", text: "完整结果" }, { type: "artifact", artifact: { id: "saved-document" } }] }];
    c.fetch = async () => { if (++reads === 1) throw new TypeError("offline"); return Response.json({ messages: saved, last_run: { id: "run", status: "completed" } }); };
    await c.finishChatStream(stream, "conversation", 1);
    assert.equal(reads, 2); assert.deepEqual(c.state.current.messages, saved); assert.equal(c.state.streaming, false); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream();
    c.handleFrame(frame(1, "message.delta", { delta: "已核对的前轮", iteration: 0 }), stream);
    c.handleFrame(frame(2, "tool.started", { tool: "query_database", tool_call_id: "sql" }), stream);
    c.handleFrame(frame(3, "message.delta", { delta: "截断的本轮半句", iteration: 1 }), stream);
    c.handleFrame(frame(4, "message.reset", { iteration: 1 }), stream);
    c.handleFrame(frame(5, "message.delta", { delta: "本轮完整重答", iteration: 1 }), stream);
    assert.equal(stream.segments.length, 2); assert.equal(stream.segments[0].raw, "已核对的前轮"); assert.equal(stream.segments[1].raw, "本轮完整重答");
    assert.equal(stream.body.children.filter(node => node.className === "tool-chip").length, 1);
    assert.equal(stream.parts.filter(part => part.type === "text").map(part => part.text).join("|"), "已核对的前轮|本轮完整重答"); checks++;
  }
  {
    // 原生快照清除失败尝试的正文，同时保留已执行工具和业务回执。
    const c = fixture(), stream = c.makeStream();
    c.handleFrame(frame(1, "message.delta", { delta: "中断的半句", attempt_id: "attempt-1" }), stream);
    c.handleFrame(frame(2, "tool.started", { tool: "query_database", tool_call_id: "sql" }), stream);
    stream.parts.push({ type: "artifact", artifact: { id: "saved" } });
    c.handleFrame(frame(3, "message.replace", { content: "", attempt_id: "attempt-2", committed: false }), stream);
    assert.equal(stream.segments.length, 0);
    assert.equal(stream.parts.length, 1);
    assert.equal(stream.body.children.filter(node => node.className === "tool-chip").length, 1);
    c.handleFrame(frame(4, "message.delta", { delta: "完整正文", attempt_id: "attempt-2" }), stream);
    c.handleFrame(frame(5, "message.replace", { content: "完整正文", attempt_id: "attempt-2", committed: true }), stream);
    c.handleFrame(frame(5, "message.replace", { content: "不能重复", attempt_id: "attempt-2" }), stream);
    assert.equal(stream.segments.length, 1); assert.equal(stream.segments[0].raw, "完整正文");
    assert.equal(stream.parts.filter(part => part.type === "artifact").length, 1); checks++;
  }
  {
    // 相同迭代中的不同模型尝试不能直接拼在同一个文本片段上。
    const c = fixture(), stream = c.makeStream();
    c.handleFrame(frame(1, "message.delta", { delta: "第一尝试", attempt_id: "a" }), stream);
    c.handleFrame(frame(2, "message.delta", { delta: "第二尝试", attempt_id: "b" }), stream);
    assert.equal(stream.segments.length, 2);
    c.handleFrame(frame(3, "message.replace", { content: "最终落盘内容", attempt_id: "b", committed: true }), stream);
    assert.equal(stream.segments.length, 1); assert.equal(stream.parts[0].text, "最终落盘内容"); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream();
    c.handleFrame("id: 10\nevent: message.delta\ndata: invalid", stream);
    assert.equal(stream.lastEventId, undefined);
    c.handleFrame(frame(10, "message.delta", { delta: "有效正文", iteration: 0 }), stream);
    c.handleFrame(frame(10, "message.delta", { delta: "重复正文", iteration: 0 }), stream);
    assert.equal(stream.segments[0].raw, "有效正文"); checks++;
  }
  {
    // 深度任务：阶段与交付事件复用进度 chip 展示（harness task.phase / task.deliverable）。
    const c = fixture(), stream = c.makeStream();
    c.handleFrame(frame(1, "task.phase", { phase: "executing", message: "正在执行任务：取数、分析与交付。" }), stream);
    const chip = () => stream.body.children.filter(node => node.className.includes("task-progress")).pop();
    assert(chip() && chip().textContent.includes("正在执行任务"), "task.phase 应写入进度 chip");
    c.handleFrame(frame(2, "task.deliverable", { count: 2, artifact_ids: ["a1", "a2"] }), stream);
    assert(chip().textContent.includes("已交付 2 个文件"), "task.deliverable 应展示交付数量");
    c.handleFrame(frame(3, "task.progress", { message: "正在处理", progress: 40 }), stream);
    assert(chip().textContent.includes("40%"), "task.progress 应保留百分比展示");
    checks++;
  }
  {
    // 终态事件即使没有随后发送 done，也必须结束前端等待，避免断线后无限重连。
    const c = fixture(), completed = c.makeStream(), failed = c.makeStream();
    c.handleFrame(frame(1, "run.completed", { run_id: "run", status: "completed" }), completed);
    assert.equal(completed.done, true);
    c.handleFrame(frame(1, "run.failed", { run_id: "run", error: "SQL 字段校验失败: password=secret" }), failed);
    assert.equal(failed.done, true);
    assert.match(failed.error, /查询所需的数据字段不完整/);
    assert(!failed.error.includes("secret")); checks++;
  }
  {
    // R23：旧 attempt 的快照不得覆盖已经产生的新正文。
    const c = fixture(), stream = c.makeStream();
    c.handleFrame(frame(1, "message.delta", { delta: "新的完整正文", attempt_id: "attempt-new" }), stream);
    c.handleFrame(frame(2, "message.replace", { content: "旧快照内容", attempt_id: "attempt-old", committed: true }), stream);
    assert.equal(stream.segments.length, 1);
    assert.equal(stream.segments[0].raw, "新的完整正文");
    checks++;
  }
  {
    const c = fixture(); c.API.post = async () => ({ status: "running" });
    await c.stopGeneration(); assert.equal(c.state.abortController.signal.aborted, false); assert(c.notices[0].includes("继续等待"));
    c.API.post = async () => ({ status: "completed" }); await c.stopGeneration();
    const stream = c.makeStream(); c.setChatStreamError(stream, new DOMException("abort", "AbortError"));
    assert.equal(stream.done, true); assert.equal(stream.error, ""); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(); let reads = 0;
    c.fetch = async () => { reads++; return new Response("", { status: 403 }); };
    await assert.rejects(c.followChatRun(null, stream, "conversation", 1, "问题"), error => error.status === 403);
    assert.equal(reads, 1); checks++;
  }
  {
    const c = fixture(); c.state.streaming = false; c.state.activeRunId = null; c.textarea.value = "下一项新问题";
    const requests = [], saved = [{ role: "user", content: "前项问题" }, { role: "assistant", parts: [{ type: "text", text: "前项已完成" }] }];
    c.fetch = async (url, options = {}) => {
      requests.push({ url, method: options.method || "GET" });
      if (url.endsWith("/send")) return Response.json({ code: "conflict", message: "已有任务", run_id: "existing" }, { status: 409 });
      if (url.includes("/events")) return events(frame(1, "message.delta", { delta: "前项已完成" }) + frame(2, "done", {}));
      return Response.json({ messages: saved, last_run: { id: "existing" } });
    };
    await c.sendMessage();
    assert.equal(requests.filter(request => request.method === "POST").length, 1);
    assert(requests.some(request => request.url === "/api/chat/runs/existing/events?after=0"));
    assert.equal(c.textarea.value, "下一项新问题"); assert.deepEqual(c.state.current.messages, saved); assert.equal(c.state.streaming, false); checks++;
  }
  {
    const c = fixture(); c.state.streaming = false;
    const saved = [{ role: "user", content: "问题" }, { role: "assistant", parts: [{ type: "business_question", question: "请选择实际工厂", options: ["A", "B"] }] }];
    c.fetch = async url => url.includes("/events") ? events(frame(1, "done", {}), false) : Response.json({ messages: saved, last_run: { id: "run", status: "completed" } });
    await c.resumeChatRun("run", "conversation");
    assert.deepEqual(c.state.current.messages, saved); assert.equal(c.state.streaming, false); checks++;
  }
  {
    // 相同文本的两次明确发送属于不同请求，断线续传则始终只读取原任务事件。
    const c = fixture(), submissions = [];
    c.fetch = async (url, options = {}) => {
      if (url.endsWith("/send")) {
        submissions.push(JSON.parse(options.body));
        return events(frame(1, "run.created", { run_id: "accepted-" + submissions.length }) + frame(2, "done", {}));
      }
      return Response.json({ messages: [{ role: "user", content: "同样的问题" }], last_run: { id: "accepted-" + submissions.length, status: "completed" } });
    };
    for (let i = 0; i < 2; i++) {
      c.state.streaming = false; c.state.activeRunId = null; c.textarea.value = "同样的问题";
      await c.sendMessage();
    }
    assert.equal(submissions.length, 2);
    assert.equal(submissions[0].content, submissions[1].content);
    assert.equal(typeof submissions[0].request_id, "string");
    assert(submissions[0].request_id.length >= 16);
    assert.notEqual(submissions[0].request_id, submissions[1].request_id); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(); c.state.abortController.abort();
    c.fetch = async () => { throw Error("Must not fetch after cancellation"); };
    await assert.rejects(c.followChatRun(null, stream, "conversation", 1), error => error.name === "AbortError"); checks++;
  }
  {
    const c = fixture(), stream = c.makeStream(); stream.done = true;
    c.handleFrame(frame(1, "message.delta", { delta: "已经收到的可核对正文", iteration: 0 }), stream);
    c.fetch = async () => new Response("", { status: 401 });
    await c.finishChatStream(stream, "conversation", 1);
    const reply = c.state.current.messages.at(-1);
    assert.equal(reply.parts[0].text, "已经收到的可核对正文");
    assert(reply.parts.some(part => part.type === "error" && part.message.includes("重新登录")));
    assert.equal(c.state.streaming, false); checks++;
  }
  return checks;
}
exports.verifyProductionRecovery = verifyProductionRecovery;
if (require.main === module) verifyProductionRecovery().then(count => console.log("PASS " + count + " isolated production chat recovery scenarios")).catch(error => { console.error(error); process.exitCode = 1; });
