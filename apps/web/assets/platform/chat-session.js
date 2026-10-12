(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DshChatSession = api;
})(typeof globalThis === "undefined" ? this : globalThis, function () {
  "use strict";
  const ACTIVE = new Set(["created", "queued", "running", "steering", "stopping", "waiting"]);
  const clone = value => JSON.parse(JSON.stringify(value));
  const emptyProjections = () => ({ mode: "standard", model: null, queue: [], tasks: [], deliveries: [], pending_questions: [], todos: [] });
  function upsert(items, item, key = "id") {
    if (!item || !item[key]) return items;
    const index = items.findIndex(value => value[key] === item[key]);
    if (index < 0) items.push(clone(item));
    else items[index] = { ...items[index], ...clone(item) };
    return items;
  }

  /** 会话事实与展示投影集中存储；Run 完成不会结束会话订阅。 */
  class Store {
    constructor() { this.reset(); }
    reset(id = null) {
      this.value = { id, session_id: null, title: "新对话", cursor: 0, messages: [], projections: emptyProjections(), runs: {}, connected: false, error: "" };
    }
    snapshot(detail) {
      const previous = this.value;
      const messages = clone(detail.messages || []);
      for (const message of messages) for (const call of (message.parts || []).filter(part => part.type === "tool_call")) {
        const result = message.parts.find(part => part.type === "tool_result" && (part.id === call.id || part.id === "result-" + call.id));
        if (!call.status) call.status = result?.result?.status || (ACTIVE.has(message.status) ? "running" : "success");
      }
      // 受理回执先于日志到达时保留暂存回显，持久 request_id 到达后替换它。
      for (const message of previous.messages) {
        if (message.pending && !messages.some(item => item.request_id === message.request_id || (!message.request_id && item.role === "user" && item.run_id === message.run_id))) messages.push(message);
      }
      const projections = { ...emptyProjections(), ...clone(detail.projections || {}) };
      // 刷新后的交付核验可能发现快照失效；不能继续展示旧消息里保存的成功卡片。
      for (const delivery of projections.deliveries.filter(item => item.type === "dashboard")) {
        for (const message of messages.filter(item => item.role === "assistant" &&
          (item.run_id === delivery.source_run_id || (item.parts || []).some(part => part.data?.reportId === delivery.report_id
            || part.result?.data?.reportId === delivery.report_id || part.report_id === delivery.report_id)))) {
          message.parts ||= [];
          upsert(message.parts, { type: "delivery_check", id: "check-" + delivery.id, report_id: delivery.report_id,
            status: delivery.status, validation_error: delivery.validation_error });
          if (delivery.status !== "ready") message.parts = message.parts.filter(part => part.type !== "business_result" || part.data?.reportId !== delivery.report_id);
          else if (delivery.business_result && message.run_id === delivery.source_run_id) upsert(message.parts, { type: "business_result", id: delivery.id, data: delivery.business_result });
        }
      }
      const runs = {};
      for (const run of [...(Array.isArray(detail.runs) ? detail.runs : Object.values(detail.runs || {})), detail.last_run, detail.active_run, ...(projections.queue || [])]) if (run?.id || run?.run_id) runs[run.run_id || run.id] = { ...runs[run.run_id || run.id], ...run, id: run.run_id || run.id };
      this.value = { ...previous, ...clone(detail), id: detail.id || previous.id, messages, projections, runs,
        cursor: Number(detail.cursor) || 0, connected: true, error: "" };
      return this.value;
    }
    accepted(receipt, payload) {
      const value = this.value;
      if (receipt.delivery_mode === "steer" || payload.delivery_mode === "steer") {
        // 原生追加输入属于同一轮，但每条用户消息仍以 request_id 保持独立。
        this.userMessage(receipt.user_message || { id: "pending-" + receipt.request_id, request_id: receipt.request_id,
          run_id: receipt.run_id, role: "user", content: payload.content, parts: [], pending: true });
        return;
      }
      // 极快任务的终态可能早于 HTTP 回执；迟到的受理信息不能复活已结束队列项。
      const previousStatus = value.runs[receipt.run_id]?.status;
      const status = previousStatus || receipt.status;
      if (!value.messages.some(message => message.request_id === receipt.request_id || (message.role === "user" && message.run_id === receipt.run_id))) {
        const user = {
        id: "pending-" + receipt.request_id, request_id: receipt.request_id, run_id: receipt.run_id, pending: ACTIVE.has(status),
        role: "user", content: payload.content, parts: (payload.file_ids || []).map(id => ({ type: "file", file: { id } })),
        };
        const assistantIndex = value.messages.findIndex(message => message.role === "assistant" && message.run_id === receipt.run_id);
        value.messages.splice(assistantIndex < 0 ? value.messages.length : assistantIndex, 0, user);
      }
      value.runs[receipt.run_id] = { ...value.runs[receipt.run_id], ...receipt, id: receipt.run_id, status };
      if (ACTIVE.has(status)) upsert(value.projections.queue, { id: receipt.run_id, run_id: receipt.run_id,
        request_id: receipt.request_id, content: payload.content, status });
      value.session_id = receipt.session_id || value.session_id;
      if (payload.title) value.title = payload.title;
    }
    assistant(data) {
      let message = this.value.messages.find(item => item.role === "assistant" &&
        ((data.message_id && item.id === data.message_id) || (data.run_id && item.run_id === data.run_id)));
      if (!message) {
        message = { id: data.message_id || "assistant-" + data.run_id, run_id: data.run_id, role: "assistant", content: "", parts: [] };
        this.value.messages.push(message);
      }
      message.parts ||= [];
      return message;
    }
    userMessage(message) {
      if (message?.role !== "user" || !message.id) return;
      const messages = this.value.messages;
      const index = messages.findIndex(item => item.role === "user" && (item.id === message.id
        || (!message.request_id && message.run_id && item.run_id === message.run_id)
        || (message.request_id && item.request_id === message.request_id)));
      // 编辑受理后用持久用户消息原位替换暂存气泡，保留同轮助手和其它排队消息。
      if (index >= 0) {
        messages[index] = { ...messages[index], ...clone(message) };
        if (!Array.isArray(message.parts) && message.content != null) {
          messages[index].parts = [{ type: "text", text: message.content }, ...(messages[index].parts || []).filter(part => part.type !== "text")];
        }
        delete messages[index].pending;
      }
      else {
        const hasEarlierInput = messages.some(item => item.role === "user" && item.run_id === message.run_id);
        const assistantIndex = hasEarlierInput ? -1 : messages.findIndex(item => item.role === "assistant" && item.run_id === message.run_id);
        messages.splice(assistantIndex < 0 ? messages.length : assistantIndex, 0, clone(message));
      }
    }
    text(data, kind, replace) {
      const message = this.assistant(data);
      if (replace && kind === "text" && data.attempt_id && message.attempt_id && data.attempt_id !== message.attempt_id) {
        message.parts = message.parts.filter(item => item.type !== "reasoning");
      }
      let part = message.parts.find(item => item.type === kind);
      if (!part) { part = { type: kind, text: "" }; message.parts.push(part); }
      const incoming = String(replace ? data.content ?? data.text ?? "" : data.delta ?? data.text ?? "");
      part.text = replace ? incoming : part.text + incoming;
      if (kind === "text") message.content = part.text;
      if (data.attempt_id) message.attempt_id = data.attempt_id;
    }
    apply(event, data = {}, eventId) {
      if (event === "session.snapshot") { this.snapshot(data); return true; }
      // 兼容现有业务事件和 DSH 宿主工具事件的命名，内部只保留一套关联键。
      if (event.startsWith("tool.")) data = { ...data, tool_call_id: data.tool_call_id || data.call_id, tool: data.tool || data.tool_name };
      const id = Number(eventId);
      if (Number.isSafeInteger(id) && id > 0) {
        if (id <= this.value.cursor) return false;
        this.value.cursor = id;
      }
      const value = this.value, projections = value.projections;
      if (data.session_id) value.session_id = data.session_id;
      if (event === "message.delta") this.text(data, "text", false);
      else if (event === "message.replace" || event === "message.reset") this.text(data, "text", true);
      else if (event === "reasoning.delta" || event === "message.reasoning.delta") this.text(data, "reasoning", false);
      else if (event === "reasoning.replace" || event === "message.reasoning.replace") this.text(data, "reasoning", true);
      else if (event === "message.created" || event === "message.user") {
        const message = data.message || data;
        if (message.role === "user") this.userMessage(message);
        else upsert(value.messages, message);
      } else if (event === "message.steered") {
        this.userMessage(data.user_message);
      } else if (event === "tool.started") {
        const message = this.assistant(data);
        const index = message.parts.findIndex(part => part.type === "tool_call" && part.id === data.tool_call_id);
        const part = { type: "tool_call", id: data.tool_call_id, name: data.tool || data.name, status: "running", arguments: data.arguments };
        if (index < 0) message.parts.push(part); else message.parts[index] = part;
      } else if (event === "tool.completed" || event === "tool.failed") {
        const message = this.assistant(data);
        const status = event === "tool.failed" ? "failed" : data.status || "success";
        const call = message.parts.find(item => item.type === "tool_call" && item.id === data.tool_call_id);
        if (call) call.status = status;
        const index = message.parts.findIndex(part => part.type === "tool_result" && (part.id === data.tool_call_id || part.id === "result-" + data.tool_call_id));
        const part = { type: "tool_result", id: data.tool_call_id, tool: data.tool || data.name,
          result: { ...(data.result || {}), status, ...(status === "failed" ? { error_summary: data.error?.message || data.error || data.summary || data.result?.error_summary || "工具调用未完成" } : {}),
            ...(!data.result ? { data: data.data } : {}) } };
        if (index < 0) message.parts.push(part); else message.parts[index] = part;
      } else if (event === "queue.updated") {
        projections.queue = clone(data.queue || []);
        if (data.user_message) this.userMessage(data.user_message);
        if (data.task) upsert(projections.tasks, data.task);
      }
      else if (event === "task.updated") upsert(projections.tasks, data.task || data);
      else if (event === "delivery.created" || event === "delivery.updated") {
        const delivery = data.delivery || data;
        upsert(projections.deliveries, delivery);
        if (delivery.type === "dashboard") {
          const message = this.assistant({ ...data, run_id: data.run_id || delivery.source_run_id });
          upsert(message.parts, { type: "delivery_check", id: "check-" + delivery.id, report_id: delivery.report_id,
            status: delivery.status, validation_error: delivery.validation_error });
          if (delivery.status === "ready" && delivery.business_result) upsert(message.parts, { type: "business_result", id: delivery.id, data: delivery.business_result });
          else message.parts = message.parts.filter(part => part.type !== "business_result" || part.data?.reportId !== delivery.report_id);
        }
      }
      else if (event === "question.requested" || event === "question.pending") {
        const question = typeof data.question === "object" ? data.question : data;
        upsert(projections.pending_questions, { ...question, id: question.id || data.question_id, run_id: data.run_id || question.run_id, native: event === "question.pending",
          ...(event === "question.pending" ? { native: true, question_id: data.question_id || question.id } : {}) });
        if (event === "question.pending") this.assistant(data);
      } else if (event === "question.resolved") projections.pending_questions = projections.pending_questions.filter(item => (item.question_id || item.id) !== (data.question_id || data.id));
      else if (event === "todo.updated") {
        projections.todos = clone(data.todos || []);
        const message = this.assistant(data);
        message.parts = message.parts.filter(part => part.type !== "native_todos");
        message.parts.push({ type: "native_todos", todos: clone(data.todos || []), run_id: data.run_id });
      }
      else if (event === "task.phase" || event === "task.progress") value.progress = { ...data, event };
      else if (event === "context.compacted") value.notice = "较早的上下文已压缩，正在继续处理。";
      else if (event === "run.created" || event === "run.started") {
        value.runs[data.run_id] = { ...value.runs[data.run_id], ...data, id: data.run_id, status: event === "run.created" ? "created" : "running" };
        if (data.task) upsert(projections.tasks, { ...data.task, status: event === "run.created" ? "queued" : "running" });
        const queued = projections.queue.find(item => (item.run_id || item.id) === data.run_id);
        if (queued) queued.status = "running";
      } else if (["run.completed", "run.failed", "run.cancelled", "done"].includes(event)) {
        const status = data.status || (event === "run.failed" ? "failed" : event === "run.cancelled" ? "cancelled" : event === "run.completed" ? "completed" : value.runs[data.run_id]?.status);
        if (data.run_id && status && !ACTIVE.has(status)) {
          value.runs[data.run_id] = { ...value.runs[data.run_id], ...data, id: data.run_id, status };
          projections.queue = projections.queue.filter(item => (item.run_id || item.id) !== data.run_id);
          projections.pending_questions = projections.pending_questions.filter(item => item.run_id !== data.run_id);
          for (const message of value.messages) if (message.run_id === data.run_id) delete message.pending;
        }
        if (event !== "done") {
          const message = this.assistant(data);
          message.status = status;
          if (data.content != null) this.text(data, "text", true);
          if ((event === "run.failed" || data.error) && !message.parts.some(part => part.type === "error")) {
            message.parts.push({ type: "error", message: String(data.error?.message || data.error || data.message || "任务未完成") });
          }
        }
        if (!value.progress?.run_id || value.progress.run_id === data.run_id) value.progress = null;
      } else return false;
      if (event === "queue.updated") for (const run of projections.queue) value.runs[run.run_id || run.id] = { ...value.runs[run.run_id || run.id], ...run, id: run.run_id || run.id };
      return true;
    }
    activeRun() {
      return this.value.projections.queue.find(item => ["running", "steering", "stopping", "waiting"].includes(item.status))
        || Object.values(this.value.runs).find(item => ["created", "running", "steering", "stopping", "waiting"].includes(item.status));
    }
    busy() { return !!this.activeRun() || this.value.projections.queue.some(item => ACTIVE.has(item.status)); }
  }

  /** 按 SSE 边界解码，保留事件游标；非法单帧不会破坏其后完整帧。 */
  function parseFrame(frame) {
    let event = "message", id; const lines = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("data:")) lines.push(line.slice(5).trim());
    }
    if (!lines.length) return null;
    try { return { event, id, data: JSON.parse(lines.join("\n")) }; } catch (_) { return null; }
  }
  async function consume(response, accept, signal) {
    if (!response.body) throw Error("会话事件流不可用");
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
    const abort = () => reader.cancel().catch(() => {});
    signal?.addEventListener("abort", abort, { once: true });
    try {
      while (!signal?.aborted) {
        const part = await reader.read(); buffer += decoder.decode(part.value, { stream: !part.done }); buffer = buffer.replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const frame = parseFrame(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); if (frame) accept(frame);
        }
        if (part.done) { const frame = parseFrame(buffer); if (frame) accept(frame); break; }
      }
    } finally { signal?.removeEventListener("abort", abort); reader.releaseLock(); }
  }
  function delay(ms, signal) {
    return new Promise(resolve => {
      if (signal.aborted) { resolve(); return; }
      const stop = () => { clearTimeout(timer); signal.removeEventListener("abort", stop); resolve(); };
      const timer = setTimeout(stop, ms); signal.addEventListener("abort", stop, { once: true });
    });
  }

  /** 受理与订阅分离；断线只补读同一会话，从不自动重复提交 prompt。 */
  class Controller {
    constructor({ fetch: doFetch, headers, changed, sleep = delay }) {
      this.fetch = doFetch; this.headers = headers; this.changed = changed; this.sleep = sleep; this.store = new Store(); this.generation = 0;
    }
    async request(path, body) {
      const response = await this.fetch(path, { method: body === undefined ? "GET" : "POST",
        headers: { ...this.headers(), Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      const value = await response.json();
      if (!response.ok) { const error = new Error(value.message || value.error?.message || value.detail || "请求未完成"); error.status = response.status; throw error; }
      return value;
    }
    open(detail) {
      this.close(); this.store.reset(detail.id); this.store.snapshot(detail); this.changed(this.store.value);
      this.abort = new AbortController(); const generation = this.generation;
      this.follow(generation, this.abort.signal);
    }
    close() { this.generation += 1; this.abort?.abort(); this.abort = null; }
    async follow(generation, signal) {
      let attempt = 0;
      while (!signal.aborted && generation === this.generation) {
        try {
          const response = await this.fetch(`/api/chat/conversations/${encodeURIComponent(this.store.value.id)}/events?after=${this.store.value.cursor}`, { headers: this.headers(), signal, cache: "no-store" });
          if (!response.ok) { const error = new Error("会话订阅失败 " + response.status); error.status = response.status; throw error; }
          this.store.value.connected = true; this.store.value.error = ""; this.changed(this.store.value);
          await consume(response, frame => {
            if (signal.aborted || generation !== this.generation) return;
            if (this.store.apply(frame.event, frame.data, frame.id)) { attempt = 0; this.changed(this.store.value); }
          }, signal);
          if (signal.aborted) return;
          throw Error("会话连接中断，正在恢复已有任务。");
        } catch (error) {
          if (signal.aborted || generation !== this.generation) return;
          this.store.value.connected = false; this.store.value.error = error.message; this.changed(this.store.value);
          if ([400, 401, 403, 404, 422].includes(error.status)) return;
          await this.sleep(Math.min(1000 * 2 ** Math.min(attempt++, 4), 15000), signal);
        }
      }
    }
    async submit(payload) {
      const id = this.store.value.id;
      let receipt;
      try { receipt = await this.request(`/api/chat/conversations/${encodeURIComponent(id)}/send`, payload); }
      catch (error) {
        if (!error.status || error.status >= 500) {
          // 回执丢失时只读持久历史，使用 request_id 识别已受理消息。
          const detail = await this.request(`/api/chat/conversations/${encodeURIComponent(id)}`).catch(() => null);
          const message = detail?.messages?.find(item => item.role === "user" && item.request_id === payload.request_id);
          const assistant = detail?.messages?.find(item => item.role === "assistant" && item.run_id === message?.run_id);
          if (message?.run_id) receipt = { accepted: true, request_id: payload.request_id, run_id: message.run_id, session_id: detail.session_id,
            status: detail.last_run?.id === message.run_id ? detail.last_run.status : assistant?.status || "queued" };
        }
        if (!receipt) throw error;
      }
      if (!receipt.accepted || !receipt.run_id) throw Error("未收到有效的消息受理回执");
      if (this.store.value.id === id) { this.store.accepted(receipt, payload); this.changed(this.store.value); }
      return receipt;
    }
  }
  return { Store, Controller, consume, parseFrame, ACTIVE };
});
