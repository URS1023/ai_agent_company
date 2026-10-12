"use strict";
// 生产会话状态与传输使用真实流对象验证，接口全部由内存函数替换。
const assert = require("node:assert/strict");
const { Store, Controller, consume, parseFrame } = require("../../apps/web/assets/platform/chat-session.js");
const detail = () => ({ id: "conversation", session_id: "session", cursor: 0, messages: [], projections: { queue: [], tasks: [], deliveries: [], pending_questions: [] } });
const store = () => { const result = new Store(); result.snapshot(detail()); return result; };
let checks = 0;
function verify(label, fn) { fn(); checks++; console.log("PASS " + label); }

async function main() {
  verify("服务端真实耗时经过终态和迟到回执后仍保留，历史消息保留自己的耗时", () => {
    const s = store();
    s.apply("run.started", { run_id: "r", started_at: "2026-09-26T09:00:00Z" });
    s.apply("run.completed", { run_id: "r", completed_at: "2026-09-26T09:05:33Z", duration_ms: 333000 });
    s.apply("done", { run_id: "r", status: "completed" });
    s.accepted({ request_id: "receipt", run_id: "r", status: "queued" }, { content: "查看预警并生成文件" });
    assert.equal(s.value.runs.r.duration_ms, 333000); assert.equal(s.value.runs.r.started_at, "2026-09-26T09:00:00Z");
    s.snapshot({ ...detail(), messages: [{ id: "history", role: "assistant", run_id: "old", timing: { duration_ms: 2500 } }], last_run: { id: "r", duration_ms: 333000 } });
    assert.equal(s.value.messages[0].timing.duration_ms, 2500); assert.equal(s.value.runs.r.duration_ms, 333000);
  });
  verify("正文去重并保留业务工具结果", () => {
    const s = store();
    s.apply("message.delta", { run_id: "r", message_id: "assistant", delta: "原文" }, 1);
    assert.equal(s.apply("message.delta", { run_id: "r", delta: "重复" }, 1), false);
    s.apply("tool.started", { run_id: "r", tool_call_id: "tool", tool: "read_file" }, 2);
    s.apply("tool.completed", { run_id: "r", tool_call_id: "tool", tool: "read_file", status: "success", data: { value: 1 } }, 3);
    s.apply("message.replace", { run_id: "r", content: "已提交正文" }, 4);
    assert.equal(s.value.messages.length, 1);
    assert.equal(s.value.messages[0].content, "已提交正文");
    assert.equal(s.value.messages[0].parts.find(item => item.type === "tool_result").result.data.value, 1);
  });
  verify("思考独立保存，失败尝试不进入新尝试", () => {
    const s = store(); s.apply("message.replace", { run_id: "r", attempt_id: "one", content: "" });
    s.apply("reasoning.delta", { run_id: "r", delta: "思考" }); s.apply("message.delta", { run_id: "r", delta: "答案" });
    assert.equal(s.value.messages[0].content, "答案");
    assert.equal(s.value.messages[0].parts.find(item => item.type === "reasoning").text, "思考");
    s.apply("message.replace", { run_id: "r", attempt_id: "two", content: "" });
    assert(!s.value.messages[0].parts.some(item => item.type === "reasoning"));
  });
  verify("原生宿主工具字段与既有业务结果同样可关联", () => {
    const s = store();
    s.apply("tool.started", { run_id: "r", call_id: "native-call", tool_name: "query_business_data", arguments: { scope: "selected" } });
    s.apply("tool.completed", { run_id: "r", call_id: "native-call", tool_name: "query_business_data", status: "success", result: { status: "success", data: { rows: [1] } } });
    const parts = s.value.messages[0].parts;
    assert.equal(parts[0].name, "query_business_data"); assert.equal(parts[0].status, "success"); assert.deepEqual(parts[1].result.data.rows, [1]);
    s.apply("tool.failed", { run_id: "r", call_id: "native-call", tool_name: "query_business_data", error: "工具执行失败" });
    assert.equal(parts[0].status, "failed"); assert.equal(parts[1].result.error_summary, "工具执行失败");
  });
  verify("一轮完成不清除下一轮排队消息", () => {
    const s = store(); s.apply("queue.updated", { queue: [{ id: "r1", status: "running" }, { id: "r2", status: "queued" }] });
    s.apply("run.completed", { run_id: "r1", status: "partial" }); s.apply("done", { run_id: "r1" });
    assert(s.busy()); assert.equal(s.value.runs.r1.status, "partial"); assert.deepEqual(s.value.projections.queue.map(item => item.id), ["r2"]);
    s.apply("run.started", { run_id: "r2" }); assert.equal(s.activeRun().id, "r2");
    s.apply("run.completed", { run_id: "r2" }); assert(!s.busy());
  });
  verify("移除排队消息不会清除正在执行任务的进度", () => {
    const s = store(); s.apply("run.started", { run_id: "running" });
    s.apply("task.progress", { run_id: "running", message: "正在生成数据报告" });
    s.apply("run.cancelled", { run_id: "queued", status: "cancelled" });
    s.apply("done", { run_id: "queued", status: "cancelled" });
    assert.equal(s.value.progress.message, "正在生成数据报告");
    s.apply("run.completed", { run_id: "running", status: "completed" });
    assert.equal(s.value.progress, null);
  });
  verify("持久工具parts与实时事件按类型关联，刷新后不重复工具结果", () => {
    const s = store(); s.snapshot({ ...detail(), messages: [{ id: "a", run_id: "r", role: "assistant", status: "running", parts: [
      { type: "tool_call", id: "call", name: "read_skill" }, { type: "tool_result", id: "call", tool: "read_skill", result: { status: "success" } },
      { type: "tool_call", id: "pending", name: "query" },
    ] }] });
    assert.equal(s.value.messages[0].parts[0].status, "success"); assert.equal(s.value.messages[0].parts[2].status, "running");
    s.apply("tool.completed", { run_id: "r", call_id: "call", tool_name: "read_skill", status: "success", result: { content: [{ type: "text", text: "已完成" }] } });
    assert.equal(s.value.messages[0].parts.filter(part => part.type === "tool_result").length, 1);
    assert.equal(s.value.messages[0].parts.filter(part => part.type === "tool_call").length, 2);
  });
  verify("编辑排队任务实时替换用户气泡和交付约束，刷新前后保持一致", () => {
    const s = store(); s.accepted({ request_id: "first", run_id: "r1", status: "running" }, { content: "正在分析", file_ids: [] });
    s.accepted({ request_id: "second", run_id: "r2", status: "queued" }, { content: "制作PPT", file_ids: ["attachment"] });
    s.assistant({ run_id: "r2", message_id: "assistant-r2" });
    const updated = { id: "user-r2", role: "user", run_id: "r2", request_id: "second", content: "制作Word" };
    const task = { id: "task2", task_id: "task2", title: "制作Word", status: "queued", required_deliveries: [{ type: "document", count: 1 }] };
    s.apply("queue.updated", { run_id: "r2", queue: [{ id: "r1", status: "running" }, { id: "r2", status: "queued", content: "制作Word" }], user_message: updated, task });
    assert.equal(s.value.messages.length, 3); assert.equal(s.value.messages[1].id, "user-r2");
    assert.equal(s.value.messages[1].content, "制作Word"); assert.equal(s.value.messages[1].parts[1].file.id, "attachment");
    assert.equal(s.value.messages[1].parts[0].text, "制作Word");
    assert.equal(s.value.messages[2].id, "assistant-r2"); assert.equal(s.activeRun().id, "r1");
    assert.equal(s.value.projections.tasks[0].required_deliveries[0].type, "document");
    const before = JSON.stringify(s.value.messages);
    s.snapshot({ ...detail(), messages: s.value.messages, projections: s.value.projections });
    assert.equal(JSON.stringify(s.value.messages), before);
    s.apply("queue.updated", { queue: s.value.projections.queue, user_message: updated, task });
    assert.equal(s.value.messages.length, 3);
  });
  verify("业务任务的部分交付不会升级成全部完成", () => {
    const s = store(); s.apply("task.updated", { task: { id: "task", title: "制作报告", status: "partial", missing_deliveries: [{ type: "presentation", count: 1 }] } });
    s.apply("delivery.created", { delivery: { id: "artifact", name: "报告.docx", status: "completed" } });
    s.apply("delivery.created", { delivery: { id: "artifact", name: "报告.docx", status: "completed" } });
    s.apply("run.completed", { run_id: "r", status: "partial" });
    assert.equal(s.value.projections.tasks[0].status, "partial"); assert.equal(s.value.projections.deliveries.length, 1);
  });
  verify("snapshot 恢复已受理消息并去掉暂存回显", () => {
    const s = store(), receipt = { accepted: true, request_id: "request", run_id: "r", status: "running" };
    s.accepted(receipt, { content: "用户原文", file_ids: ["f"] });
    s.snapshot({ ...detail(), cursor: 5, messages: [{ id: "saved", request_id: "request", run_id: "r", role: "user", content: "用户原文", parts: [] }], last_run: { id: "r", status: "running" } });
    assert.equal(s.value.messages.length, 1); assert.equal(s.value.messages[0].id, "saved"); assert(s.busy());
    assert(!s.value.messages[0].pending);
  });
  verify("终态先于受理回执时不复活已完成的队列项", () => {
    const s = store(); s.apply("message.delta", { run_id: "fast", delta: "快速回答" });
    s.apply("run.completed", { run_id: "fast", status: "completed", content: "完整回答" });
    s.accepted({ request_id: "client", run_id: "fast", status: "queued" }, { content: "快速问题", file_ids: [] });
    assert(!s.busy()); assert.equal(s.value.projections.queue.length, 0); assert.equal(s.value.runs.fast.status, "completed");
    assert.deepEqual(s.value.messages.map(message => message.role), ["user", "assistant"]);
    assert.equal(s.value.messages[1].content, "完整回答");
  });
  verify("开始事件先于受理回执时仍能定位运行并停止", () => {
    const s = store(); s.apply("run.started", { run_id: "r" });
    s.accepted({ request_id: "client", run_id: "r", status: "queued" }, { content: "任务", file_ids: [] });
    assert.equal(s.activeRun().run_id || s.activeRun().id, "r"); assert.equal(s.activeRun().status, "running");
  });
  verify("待回答请求只有明确解决事件才消失", () => {
    const s = store(); s.apply("question.requested", { question_id: "question", question: "采用哪个范围？", options: ["本月", "本年"] });
    s.apply("run.completed", { run_id: "other" }); assert.equal(s.value.projections.pending_questions.length, 1);
    s.apply("question.resolved", { question_id: "question" }); assert.equal(s.value.projections.pending_questions.length, 0);
  });
  verify("原生追加输入同轮独立保存，原生问答和计划跟随消息", () => {
    const s = store();
    s.accepted({ request_id: "initial", run_id: "r", status: "running" }, { content: "制作大屏" });
    s.apply("todo.updated", { run_id: "r", todos: [{ content: "确认日期", status: "in_progress" }] });
    s.accepted({ request_id: "extra", run_id: "r", status: "running", delivery_mode: "steer" }, { content: "只看上月", delivery_mode: "steer" });
    s.apply("message.user", { message: { id: "saved-extra", request_id: "extra", run_id: "r", role: "user", content: "只看上月" } });
    assert.equal(s.value.messages.filter(message => message.role === "user").length, 2);
    assert.equal(s.value.messages.filter(message => message.role === "assistant").length, 1);
    assert.equal(s.value.projections.queue.length, 1);
    s.apply("question.pending", { run_id: "r", question_id: "native", questions: [{ id: "date", question: "哪个月份" }] });
    assert.equal(s.value.projections.pending_questions[0].question_id, "native");
    assert.equal(s.value.messages.find(message => message.role === "assistant").parts[0].type, "native_todos");
    s.apply("run.cancelled", { run_id: "r", status: "cancelled" });
    assert.equal(s.value.projections.pending_questions.length, 0);
  });
  verify("刷新时无效大屏撤回旧成功卡片，保留核验事实", () => {
    const s = store();
    s.snapshot({ ...detail(), messages: [{ id: "answer", run_id: "r", role: "assistant", parts: [
      { type: "business_result", id: "dashboard:report", data: { reportId: "report" } },
    ] }], projections: { deliveries: [{ id: "dashboard:report", report_id: "report", type: "dashboard", source_run_id: "r", status: "invalid" }] } });
    assert(!s.value.messages[0].parts.some(part => part.type === "business_result"));
    assert.equal(s.value.messages[0].parts.find(part => part.type === "delivery_check").status, "invalid");
  });
  verify("非法帧不影响后续事件", () => {
    assert.equal(parseFrame("event: bad\ndata: {"), null);
    assert.equal(parseFrame("id: 9\nevent: task.updated\ndata: {\"task\":{\"id\":\"t\"}}").id, "9");
  });
  {
    const frames = [], encoder = new TextEncoder();
    const bytes = encoder.encode('id: 1\r\nevent: message.delta\r\ndata: {"delta":"中文"}\r\n\r\nevent: done\ndata: {}\n\nevent: task.updated\ndata: {"task":{"id":"t"}}\n\n');
    await consume(new Response(new ReadableStream({ start(stream) { for (let i = 0; i < bytes.length; i += 3) stream.enqueue(bytes.slice(i, i + 3)); stream.close(); } })), frame => frames.push(frame));
    assert.equal(frames.length, 3); assert.equal(frames[0].data.delta, "中文"); assert.equal(frames[2].event, "task.updated");
    checks++; console.log("PASS UTF-8分片、CRLF和done之后仍继续会话事件");
  }
  {
    const calls = [], c = new Controller({ headers: () => ({}), changed() {}, fetch: async (url, options) => {
      calls.push({ url, options });
      if (options.method === "POST") throw TypeError("回执丢失");
      return Response.json({ ...detail(), messages: [{ id: "user", role: "user", request_id: "request", run_id: "r", content: "原文" }], last_run: { id: "r", status: "running" } });
    } });
    c.store.snapshot(detail());
    const receipt = await c.submit({ request_id: "request", content: "原文", file_ids: [] });
    assert.equal(receipt.run_id, "r"); assert.equal(calls.filter(item => item.options.method === "POST").length, 1);
    assert.equal(calls[0].options.headers.Accept, "application/json");
    checks++; console.log("PASS 发送回执丢失按request_id恢复且不重发");
  }
  {
    let calls = 0, controller;
    controller = new Controller({ headers: () => ({}), changed() {}, sleep: async (_ms, signal) => { controller.close(); },
      fetch: async () => { calls++; return new Response("", { status: 403 }); } });
    controller.open(detail()); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(calls, 1); assert.equal(controller.store.value.connected, false); controller.close();
    checks++; console.log("PASS 不可访问会话停止自动重连");
  }
  {
    const c = new Controller({ headers: () => ({}), changed() {}, fetch: async (_url, options) => {
      if (options.method === "POST") throw TypeError("回执丢失");
      return Response.json({ ...detail(), messages: [
        { id: "old-user", role: "user", request_id: "old-request", run_id: "old", content: "较早任务" },
        { id: "old-assistant", role: "assistant", run_id: "old", status: "completed", content: "已完成" },
      ], last_run: { id: "newer", status: "completed" } });
    } });
    c.store.snapshot(detail());
    const receipt = await c.submit({ request_id: "old-request", content: "较早任务", file_ids: [] });
    assert.equal(receipt.status, "completed"); assert(!c.store.busy());
    checks++; console.log("PASS 丢失回执恢复较早终态，不把旧任务重新放入队列");
  }
  console.log(`PASS ${checks} DSH 会话离线场景；数据库/模型/网络请求均为0`);
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
