(function (root) {
  "use strict";
  const labels = { created: "等待开始", pending: "待处理", queued: "排队中", running: "处理中", steering: "补充中", stopping: "正在停止", waiting: "等待补充", completed: "已完成", ready: "已交付", partial: "部分完成", failed: "未完成", interrupted: "已中断", cancelled: "已取消" };
  const typeLabels = { document: "文档", presentation: "演示文稿", spreadsheet: "电子表格", image: "图片", chart: "图表", dashboard: "数字大屏", pdf: "PDF文档" };

  /** 复用原有消息、附件和输入区外壳，仅在实际能力/事件出现时添加过程组件。 */
  function create(options) {
    const { state, API, $, esc, toast, renderMessages, renderAttachChips, renderChatModels, authUrl } = options;
    let capabilities, renderTimer, sending = false, projectionKey = "", disposed = false;
    const openDetails = new Set();
    const controller = new root.DshChatSession.Controller({ fetch: (...args) => fetch(...args),
      headers: () => ({ Authorization: "Bearer " + API.token, "X-Workspace-Id": localStorage.getItem("agent_workspace_id") || "" }),
      changed: () => scheduleRender() });
    function feature(name) { return capabilities?.features?.[name] === true; }
    function enabled() { return capabilities?.engine === "dsh" && capabilities.protocol_version >= 2; }
    function region(id, parent) {
      let node = $("#" + id);
      if (!node) { node = document.createElement("section"); node.id = id; node.className = "dsh-session-region"; node.hidden = true; parent.appendChild(node); }
      return node;
    }
    function scheduleRender() {
      if (disposed || renderTimer != null) return;
      renderTimer = setTimeout(() => { renderTimer = null; render(); }, 40);
    }
    function render() {
      const value = controller.store.value;
      if (!enabled() || !value.id || state.current?.id !== value.id) return;
      document.querySelectorAll("details[data-dsh-detail]").forEach(node => { if (node.open) openDetails.add(node.dataset.dshDetail); else openDetails.delete(node.dataset.dshDetail); });
      state.current.messages = value.messages; state.current.title = value.title || state.current.title;
      state.streaming = controller.store.busy(); state.activeRunId = controller.store.activeRun()?.run_id || controller.store.activeRun()?.id || null;
      renderMessages(); renderProjections(); updateComposer();
    }
    function decorate(row, message) {
      const parts = message.parts || [], identity = message.id || message.run_id || "assistant";
      decorateInteractions(row, message);
      const reasoning = feature("reasoning") ? parts.filter(part => part.type === "reasoning").map(part => part.text || part.content || "").join("\n") : "";
      const calls = new Map(), groups = new Map();
      const callIds = new Set(parts.filter(part => part.type === "tool_call").map(part => part.id));
      parts.forEach((part, index) => {
        if (part.type !== "tool_call" && part.type !== "tool_result") return;
        const rawKey = String(part.id || "entry-" + index), originalKey = rawKey.replace(/^result-/, "");
        const key = part.type === "tool_result" && !callIds.has(rawKey) && callIds.has(originalKey) ? originalKey : rawKey;
        const call = calls.get(key) || { id: key };
        if (part.type === "tool_call") Object.assign(call, { name: part.name, status: part.status, arguments: part.arguments });
        else Object.assign(call, { name: part.tool || call.name, result: part.result || {} });
        calls.set(key, call);
      });
      let failed = 0, running = 0;
      for (const call of calls.values()) {
        call.status = call.result?.isError ? "failed" : call.result?.status || call.status || "success";
        if (call.status === "failed" || call.result?.isError) failed++;
        if (call.status === "running") running++;
        const name = call.name || "工具";
        if (!groups.has(name)) groups.set(name, []);
        groups.get(name).push(call);
      }
      const run = { ...(message.timing || {}), ...(message.run || {}), ...(controller.store.value.runs[message.run_id] || {}) };
      const timing = elapsedLabel(run);
      if (!calls.size && !reasoning && !timing) return;
      const details = document.createElement("details"); details.className = "dsh-process"; details.dataset.dshProcess = identity;
      details.dataset.dshDetail = identity + ":process"; details.open = openDetails.has(details.dataset.dshDetail);
      const summary = document.createElement("summary");
      summary.innerHTML = `<span class="dsh-process-title">${running ? "正在处理" : "处理过程"}</span>${timing ? `<span class="dsh-process-time">${esc(timing)}</span>` : ""}${calls.size ? `<span>${calls.size} 次调用</span>` : ""}${failed ? `<span class="dsh-process-failure">${failed} 次调用未完成</span>` : ""}`;
      details.appendChild(summary);
      const body = document.createElement("div"); body.className = "dsh-process-body";
      // 按工具归并展示，原始回执留在同一过程面板内，避免几十次分页读取占满回答区。
      for (const [name, entries] of groups) {
        const group = document.createElement("details"); group.className = "dsh-process-group";
        group.dataset.dshDetail = identity + ":group:" + name; group.open = openDetails.has(group.dataset.dshDetail);
        const errors = entries.filter(call => call.status === "failed" || call.result?.isError);
        const label = processToolLabel(name);
        group.innerHTML = `<summary><span>${esc(label)}</span><span class="dsh-process-count">${entries.length} 次${errors.length ? ` · ${errors.length} 次未完成` : ""}</span></summary>`;
        entries.forEach((call, index) => {
          const receipt = document.createElement("details"); receipt.className = "dsh-process-receipt"; receipt.dataset.dshToolResult = call.id;
          receipt.dataset.dshDetail = identity + ":receipt:" + call.id; receipt.open = openDetails.has(receipt.dataset.dshDetail);
          const result = call.result || {};
          const status = call.status === "success" || call.status === "completed" ? "已完成" : statusLabel(call.status);
          const title = document.createElement("summary"); title.textContent = `${label} ${index + 1} · ${status}`;
          const content = document.createElement("pre"); content.className = "dsh-reasoning-text";
          const text = typeof result.content === "string" ? result.content : Array.isArray(result.content) ? result.content.filter(item => item.type === "text").map(item => item.text).join("\n") : "";
          content.textContent = text || result.error_summary || (result.data ? JSON.stringify(result.data, null, 2) : call.arguments ? JSON.stringify(call.arguments, null, 2) : status);
          receipt.append(title, content); group.appendChild(receipt);
        });
        body.appendChild(group);
      }
      if (reasoning) {
        const thought = document.createElement("details"); thought.className = "dsh-reasoning"; thought.dataset.dshReasoning = identity;
        thought.dataset.dshDetail = identity + ":reasoning"; thought.open = openDetails.has(thought.dataset.dshDetail);
        const title = document.createElement("summary"); title.textContent = "思考过程";
        const content = document.createElement("div"); content.className = "dsh-reasoning-text"; content.textContent = reasoning;
        thought.append(title, content); body.appendChild(thought);
      }
      details.appendChild(body); row.querySelector(".msg-body")?.prepend(details);
    }
    /** 原生计划和追问只属于当前消息，不生成会话底部的历史任务列表。 */
    function interactions(message) {
      return controller.store.value.projections.pending_questions.filter(question =>
        (question.native === true || question.native !== false && question.question_id) && question.run_id === message.run_id);
    }
    function decorateInteractions(row, message) {
      const body = row.querySelector(".msg-body"); if (!body) return;
      const todos = (message.parts || []).find(part => part.type === "native_todos")?.todos || [];
      if (todos.length) {
        const details = document.createElement("details"); details.className = "dsh-process dsh-todos";
        details.dataset.dshDetail = (message.id || message.run_id) + ":todos";
        details.open = openDetails.has(details.dataset.dshDetail);
        const completed = todos.filter(todo => todo.status === "completed").length;
        details.innerHTML = `<summary><span class="dsh-process-title">任务进度</span><span>${completed} / ${todos.length}</span></summary><ol class="dsh-process-body">${todos.map(todo => `<li data-todo-status="${esc(todo.status)}"><span>${esc(({ pending: "待处理", in_progress: "进行中", completed: "已完成" })[todo.status] || "待处理")}</span> ${esc(todo.content)}</li>`).join("")}</ol>`;
        body.prepend(details);
      }
      if (feature("questions")) for (const question of interactions(message)) {
        const wrapper = document.createElement("div"); wrapper.innerHTML = questionMarkup(question);
        const form = wrapper.firstElementChild;
        form.onsubmit = event => { event.preventDefault(); answerQuestion(form); };
        body.insertBefore(form, body.querySelector(".message-actions"));
      }
    }
    function processToolLabel(name) {
      const names = { skill: "读取技能指引", read_skill: "读取技能指引", read_tool_result: "读取查询结果", task: "执行子任务",
        create_presentation_plan: "规划演示文稿", write_presentation_slides: "编写演示文稿", finalize_presentation: "完成演示文稿" };
      return names[name] || options.toolLabel?.(name) || name;
    }
    /** 只显示服务端记录的运行区间；缺少起止时间时不猜测历史耗时。 */
    function elapsedLabel(run) {
      let seconds = run.duration_ms != null ? Number(run.duration_ms) / 1000 : run.duration_seconds != null ? Number(run.duration_seconds) : NaN;
      if (!Number.isFinite(seconds) && run.started_at && (run.completed_at || run.finished_at || run.ended_at)) {
        seconds = (Date.parse(run.completed_at || run.finished_at || run.ended_at) - Date.parse(run.started_at)) / 1000;
      }
      if (!Number.isFinite(seconds) || seconds < 0) return "";
      if (seconds < 60) return `用时 ${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} 秒`;
      const total = Math.round(seconds), minutes = Math.floor(total / 60), remainder = total % 60;
      return `用时 ${minutes} 分${remainder ? " " + remainder + " 秒" : "钟"}`;
    }
    function statusLabel(status) { return labels[status] || status || "待处理"; }
    function renderProjections() {
      // 升级后的首次事件也清理旧页面遗留的任务列表，避免历史任务继续叠在对话底部。
      $("#dsh-session-results")?.remove();
      const value = controller.store.value, projections = value.projections;
      const composer = $(".composer-max"); if (!composer) return;
      const panel = region("dsh-session-controls", composer);
      if (panel.parentNode === composer && composer.firstChild !== panel) composer.prepend(panel);
      const activeRun = controller.store.activeRun();
      const key = JSON.stringify([projections, value.error, activeRun, value.progress]);
      const updateControls = key !== projectionKey;
      projectionKey = key;
      const queue = feature("queue") ? projections.queue.filter(item => ["queued", "created"].includes(item.status)) : [];
      const questions = feature("questions") ? projections.pending_questions.filter(question => question.native === false || !question.native && !question.question_id) : [];
      if (updateControls) {
      // 长任务事件更新期间保留用户未提交的队列编辑和答复草稿。
      const queueDrafts = new Map([...panel.querySelectorAll("[data-queue-id] textarea")].map(input => [input.closest("[data-queue-id]").dataset.queueId, input.value]));
      const answerDrafts = new Map([...panel.querySelectorAll("[data-dsh-question] fieldset")].map(fieldset => [fieldset.dataset.answerId,
        { text: fieldset.querySelector("[data-answer-text]").value, selected: fieldset.querySelector("input[type=radio]:checked")?.value }]));
      panel.innerHTML = (value.error ? `<div class="tool-chip" role="status">${esc(value.error)}</div>` : "")
        + (activeRun ? `<div class="tool-chip dsh-active-run" role="status"><span class="spin"></span>${esc(value.progress?.message || statusLabel(activeRun.status) + "当前任务")}</div>` : "")
        + (queue.length ? `<div class="dsh-queue" aria-label="待执行消息"><strong>待执行 ${queue.length}</strong>${queue.map(item => `<div class="dsh-queue-item" data-queue-id="${esc(item.id || item.run_id)}"><span>${esc(item.content || "附件任务")}</span><button type="button" class="btn-sm btn-secondary" data-queue-edit>编辑</button><button type="button" class="btn-sm btn-secondary" data-queue-remove>移除</button>${feature("steer") ? '<button type="button" class="btn-sm btn-secondary" data-queue-steer>现在补充</button>' : ""}</div>`).join("")}</div>` : "")
        + questions.map(questionMarkup).join("");
      panel.hidden = !panel.innerHTML;
      panel.querySelectorAll("[data-queue-remove], [data-queue-steer], [data-queue-edit]").forEach(button => {
        button.onclick = () => queueAction(button);
      });
      panel.querySelectorAll("[data-dsh-question]").forEach(form => { form.onsubmit = event => { event.preventDefault(); answerQuestion(form); }; });
      panel.querySelectorAll("[data-queue-id]").forEach(row => {
        if (!queueDrafts.has(row.dataset.queueId)) return;
        const input = document.createElement("textarea"); input.className = "dsh-queue-editor"; input.value = queueDrafts.get(row.dataset.queueId);
        input.setAttribute("aria-label", "编辑待执行消息"); row.querySelector("span").replaceWith(input); row.querySelector("[data-queue-edit]").textContent = "保存";
      });
      panel.querySelectorAll("[data-dsh-question] fieldset").forEach(fieldset => {
        const draft = answerDrafts.get(fieldset.dataset.answerId); if (!draft) return;
        fieldset.querySelector("[data-answer-text]").value = draft.text;
        fieldset.querySelectorAll("input[type=radio]").forEach(input => { input.checked = input.value === draft.selected; });
      });
      }
      const displayedArtifacts = new Set([...document.querySelectorAll("#messages [data-artifact-use], #messages [data-artifact-id]")].map(node => node.dataset.artifactUse || node.dataset.artifactId));
      const deliveries = feature("deliveries") ? [...new Map(projections.deliveries.map(item => [item.artifact_id || item.id, item])).values()]
        .filter(item => !displayedArtifacts.has(item.artifact_id || item.id) && (item.type !== "dashboard" || !item.business_result)) : [];
      // 消息已有大屏业务卡片时避免重复；只有 delivery.created 的大屏仍保留可操作入口。
      if (!deliveries.length) { $("#dsh-session-deliveries")?.remove(); return; }
      const output = region("dsh-session-deliveries", $("#messages"));
      const deliveryHtml = `<div class="artifact-list dsh-deliveries" aria-label="任务交付">${deliveries.map(deliveryMarkup).join("")}</div>`;
      if (output._deliveryHtml === deliveryHtml) return;
      output._deliveryHtml = deliveryHtml;
      output.innerHTML = deliveryHtml;
      output.hidden = false;
      output.querySelectorAll("[data-dsh-artifact-use]").forEach(button => { button.onclick = () => options.useArtifactAsInput(button.dataset.dshArtifactUse); });
    }
    function deliveryMarkup(item) {
      const url = item.download_url ? authUrl(item.download_url) : "";
      const preview = item.preview_url ? authUrl(item.preview_url) : item.type === "dashboard" && item.report_id ? authUrl(`/report/view/${encodeURIComponent(item.report_id)}`) : "";
      return `<article class="artifact-result" data-delivery-id="${esc(item.id)}"><div class="artifact-card"><div class="dsh-delivery-name"><strong>${esc(item.name || (item.type === "dashboard" ? "数字大屏" : "交付结果"))}</strong><span>${esc(typeLabels[item.type] || item.type || "文件")} · ${esc(statusLabel(item.status || "completed"))}</span></div>${item.artifact_id ? `<button type="button" class="btn-sm btn-secondary" data-dsh-artifact-use="${esc(item.artifact_id)}">继续使用</button>` : ""}${preview && preview !== "#" ? `<a class="btn-sm btn-secondary" href="${esc(preview)}" target="_blank" rel="noopener">${item.type === "dashboard" ? "打开大屏" : "预览"}</a>` : ""}${url && url !== "#" ? `<a class="btn-sm btn-secondary" href="${esc(url)}" target="_blank" rel="noopener">下载</a>` : ""}</div></article>`;
    }
    function questionMarkup(question) {
      if (question.native === true || question.native !== false && question.question_id) {
        const identity = question.question_id || question.id;
        return `<form class="dsh-question" data-dsh-question="${esc(identity)}" data-native-question="true"><strong>${esc(question.title || "需要你的答复")}</strong>${(question.questions || []).map((item, index) => `<fieldset data-answer-id="${esc(item.id)}"><legend>${esc(item.question)}</legend>${item.detail ? `<p>${esc(item.detail)}</p>` : ""}${(item.options || []).map(option => `<label><input type="${item.multiSelect ? "checkbox" : "radio"}" name="${esc(identity + ":" + index)}" value="${esc(option.label)}">${esc(option.label)}${option.description ? `<small>${esc(option.description)}</small>` : ""}</label>`).join("")}<label>补充说明<input type="text" data-answer-text aria-label="${esc(item.question || "补充说明")}"></label></fieldset>`).join("")}<button type="submit" class="btn-sm btn-primary">提交答复</button><span role="status" data-question-status></span></form>`;
      }
      const questions = question.questions || [{ id: question.id, question: question.question || question.title || "请补充信息", options: question.options || [] }];
      return `<form class="dsh-question" data-dsh-question="${esc(question.id)}"><strong>${esc(question.title || "需要你的答复")}</strong>${question.plan ? `<details><summary>查看计划</summary><pre>${esc(question.plan)}</pre></details>` : ""}${questions.map(item => `<fieldset data-answer-id="${esc(item.id || question.id)}"><legend>${esc(item.question || item.title || "请选择")}</legend>${(item.options || []).map(option => { const label = typeof option === "string" ? option : option.label; return `<label><input type="radio" name="${esc(item.id || question.id)}" value="${esc(label)}">${esc(label)}</label>`; }).join("")}<label>补充说明<input type="text" data-answer-text aria-label="${esc(item.question || "补充说明")}"></label></fieldset>`).join("")}<button type="submit" class="btn-sm btn-primary">提交答复</button><span role="status" data-question-status></span></form>`;
    }
    async function answerQuestion(form) {
      const answers = [...form.querySelectorAll("fieldset")].map(fieldset => ({ id: fieldset.dataset.answerId,
        selected: [...fieldset.querySelectorAll("input[type=radio]:checked, input[type=checkbox]:checked")].map(input => input.value), text: fieldset.querySelector("[data-answer-text]").value.trim() }));
      const status = form.querySelector("[data-question-status]");
      if (answers.some(answer => !answer.selected.length && !answer.text)) { status.textContent = "请完成每项答复"; return; }
      const button = form.querySelector("button[type=submit]"); button.disabled = true;
      const native = form.dataset.nativeQuestion === "true";
      try { await controller.request(`/api/chat/conversations/${encodeURIComponent(state.current.id)}/questions/${encodeURIComponent(form.dataset.dshQuestion)}${native ? "" : "/answer"}`, {
        answers: native ? answers.map(({ id, selected, text }) => ({ id, selected, ...(text ? { custom: text } : {}) })) : answers }); status.textContent = "答复已受理"; }
      catch (error) { status.textContent = error.message; button.disabled = false; }
    }
    async function queueAction(button) {
      const row = button.closest("[data-queue-id]"), id = row.dataset.queueId;
      let action = button.hasAttribute("data-queue-remove") ? "remove" : button.hasAttribute("data-queue-steer") ? "steer" : "edit";
      if (action === "edit" && !row.querySelector("textarea")) {
        const input = document.createElement("textarea"); input.className = "dsh-queue-editor"; input.value = row.querySelector("span").textContent;
        input.setAttribute("aria-label", "编辑待执行消息"); row.querySelector("span").replaceWith(input); button.textContent = "保存"; input.focus(); return;
      }
      const content = row.querySelector("textarea")?.value.trim();
      if (action === "edit" && !content) return toast("待执行消息不能为空");
      button.disabled = true;
      try {
        await controller.request(`/api/chat/conversations/${encodeURIComponent(state.current.id)}/queue/${encodeURIComponent(id)}`, { action, ...(action === "edit" ? { content } : {}) });
        if (action === "edit") {
          const current = [...$("#dsh-session-controls").querySelectorAll("[data-queue-id]")].find(item => item.dataset.queueId === id);
          const input = current?.querySelector("textarea");
          if (input) { const text = document.createElement("span"); text.textContent = content; input.replaceWith(text); current.querySelector("[data-queue-edit]").textContent = "编辑"; }
        }
      }
      catch (error) { toast(error.message); button.disabled = false; }
    }
    function updateComposer() {
      const textarea = $("#prompt-textarea"), button = $("#composer-submit-button"); if (!button || !textarea) return;
      const has = !!textarea.value.trim() || state.fileIds.length > 0;
      const busy = controller.store.busy(), stops = busy && !has;
      const steers = busy && feature("steer");
      button.disabled = sending || (!busy && !has) || (busy && has && !feature("queue") && !steers);
      button.classList.toggle("streaming", stops);
      button.querySelector(".send-icon")?.classList.toggle("hidden", stops);
      button.querySelector(".stop-icon")?.classList.toggle("hidden", !stops);
      button.setAttribute("aria-label", stops ? "停止生成" : steers ? "补充当前任务" : busy ? "加入待执行队列" : "发送");
      button.title = stops ? "停止当前任务" : steers ? "把这条补充交给正在执行的任务" : busy ? "当前任务结束后执行这条消息" : "发送";
      let queueButton = $("[data-dsh-submit-queue]");
      if (steers && has && feature("queue")) {
        const panel = region("dsh-session-controls", $(".composer-max")); panel.hidden = false;
        if (!queueButton) {
          queueButton = document.createElement("button"); queueButton.type = "button";
          queueButton.className = "btn-sm btn-secondary"; queueButton.dataset.dshSubmitQueue = "true";
          queueButton.textContent = "作为下一项任务"; queueButton.onclick = () => options.queueSubmit?.(); panel.appendChild(queueButton);
        }
        queueButton.disabled = sending;
      } else queueButton?.remove();
      textarea.style.height = "auto"; textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
      renderChatModels();
    }
    function syncMode() {
      const mode = state.activeMode || "standard";
      $("#active-mode-label").textContent = mode === "web" ? "网页搜索" : mode === "deep" ? "深度研究" : "";
      $("#active-mode-btn").classList.toggle("hidden", mode === "standard");
    }
    function selectMode(mode) {
      const capability = capabilities.modes?.find(item => item.id === mode);
      if (!capability?.available) { toast(capability?.reason || "当前未配置此能力"); return false; }
      state.activeMode = mode === "standard" ? null : mode; syncMode(); return true;
    }
    async function send(payload) {
      if (sending) return;
      const textarea = $("#prompt-textarea"), draft = textarea.value;
      const conversationId = state.current?.id;
      if (controller.store.busy() && !draft.trim() && !state.fileIds.length) return cancel();
      if (controller.store.busy() && !feature("queue") && !feature("steer")) return toast("当前任务仍在执行");
      sending = true; updateComposer();
      try {
        const receipt = await controller.submit({ ...payload, delivery_mode: payload.delivery_mode || (controller.store.activeRun() && feature("steer") ? "steer" : "queue"), mode: state.activeMode || "standard", skill_ids: state.skillIds || [],
          ...(state.dshTask?.id ? { task_id: state.dshTask.id } : {}) });
        if (state.current?.id !== conversationId) return receipt;
        if (textarea.value === draft) textarea.value = "";
        state.fileIds = state.fileIds.filter(item => !payload.file_ids.includes(item.id));
        renderAttachChips(); updateComposer();
        if (receipt.status === "queued") toast("消息已加入待执行队列");
        if (receipt.delivery_mode === "steer") toast("补充已交给当前任务");
        return receipt;
      } catch (error) { toast(error.message || "尚未确认消息已受理，请先检查会话记录"); }
      finally { sending = false; updateComposer(); }
    }
    async function cancel() {
      const run = controller.store.activeRun(); if (!run) return toast("当前没有正在执行的任务");
      try { const result = await controller.request(`/api/chat/runs/${encodeURIComponent(run.run_id || run.id)}/cancel`, {});
        toast(result.status === "stopping" ? "正在停止当前任务，已排队消息仍会保留" : "停止请求已受理，正在同步结果"); }
      catch (error) { toast(error.message); }
    }
    function menu(anchor) {
      options.openListMenu("技能与业务能力", [], () => false, () => {});
      const panel = $("#menu-panel");
      panel.innerHTML = `<div class="dsh-capability-menu"><strong>技能与业务能力</strong>${(capabilities.skills || []).map(skill => `<label class="dsh-skill-choice"><input type="checkbox" data-skill-id="${esc(skill.id)}" ${(state.skillIds || []).includes(skill.id) ? "checked" : ""} ${skill.available === false ? "disabled" : ""}><span><b>${esc(skill.name || skill.id)}</b><small>${esc(skill.description || skill.reason || "")}</small></span></label>`).join("")}${(capabilities.plugins || []).map(plugin => `<div class="dsh-capability-item"><b>${esc(plugin.name || plugin.id)}</b><small>${esc(plugin.description || (plugin.available === false ? plugin.reason || "暂不可用" : "已接入"))}</small></div>`).join("") || ""}${!(capabilities.skills?.length || capabilities.plugins?.length) ? '<p class="muted">当前没有可用的技能或业务能力。</p>' : ""}</div>`;
      panel.querySelectorAll("[data-skill-id]").forEach(input => { input.onchange = () => {
        const selected = new Set(state.skillIds || []); if (input.checked) selected.add(input.dataset.skillId); else selected.delete(input.dataset.skillId);
        state.skillIds = [...selected]; renderAttachChips();
      }; });
      options.positionPanel(panel, anchor || $("#add-menu-btn"));
    }
    return { controller, enabled, feature, decorate, interactions, updateComposer, selectMode, send, cancel, menu,
      setCapabilities(value) { capabilities = value; state.skillIds ||= []; },
      capabilities: () => capabilities,
      open(detail, { preserveComposer = false } = {}) {
        projectionKey = ""; openDetails.clear();
        state.activeMode = detail.projections?.mode || detail.last_run?.context_snapshot?.mode || null;
        if (state.activeMode === "standard") state.activeMode = null;
        if (!preserveComposer) { state.skillIds = []; state.dshTask = null; }
        syncMode(); controller.open(detail); renderAttachChips();
      },
      close() { controller.close(); state.streaming = false; state.activeRunId = null; $("#dsh-session-controls")?.setAttribute("hidden", ""); $("#dsh-session-deliveries")?.remove(); },
      dispose() { disposed = true; clearTimeout(renderTimer); controller.close(); },
      skillLabels() { return (state.skillIds || []).map(id => ({ id, name: capabilities.skills?.find(skill => skill.id === id)?.name || id })); },
    };
  }
  root.DshChatView = { create };
})(globalThis);
