/**
 * 知识库模块（路由 /knowledge，?id=知识库&tab=documents|segments|search|sync|settings|access）
 *
 * 位置：本模块是知识库的管理台：知识库列表 → 详情页六个标签（文档、分段、检索测试、同步、设置、授权）。
 *   文档页处理“上传新文件 / 从文件库选择”两种来源，并管理解析参数与重建索引；
 *   分段页支持手工文本/问答分段与批量导入；检索测试页会真实执行一次检索并留下召回记录。
 * 依赖接口前缀：/api/knowledge（列表、详情、/{id}/documents、/segments、/settings、/sync 与 /sync-runs、
 *   /role-grants、/search 检索、/recall-logs 召回日志、POST /api/knowledge/documents 解析入库）；
 *   另外复用 /api/files（上传、分类、下载）与 /api/v1/system/roles（授权对象）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 权限码必须与后端目录一致：knowledge.create / edit / delete / execute，
 *   files.create / view / export，system.permissions.view / manage。
 *   “添加文档”要求 knowledge.create 且 files.view；同步要求 knowledge.execute 且 files.view；
 *   “授权”标签仅对 system.permissions.view 可见，直接改 URL 会被重定向回文档页。
 * - 上传与解析是两个步骤（见 knowledge-upload.js）：解析失败会保留 fileId，重试不会重复上传；
 *   批量中单个文件失败不影响其他文件。
 * - 分段重叠字符必须小于分段长度（validateChunking），这是前端与后端共同的前置校验。
 * - 设置页有未保存修改时注册 beforeunload 拦截；离开页面前必须调用 clearSettingsGuard 解绑，
 *   否则其他页面也会被“未保存”提示误拦。
 * - 设置/分段/重建等写操作都带 version；解析参数范围（200–2000 / 0–400 / top_k 1–20）与后端校验一致。
 * - 撤销角色授权是破坏性操作，必须走统一确认弹窗；知识库删除只删索引与分段，源文件保留在文件库。
 */

import { api } from "./api.js";
import { currentPage, wirePageNavigation, redirectEmptyPage } from "./list-navigation.js";
import { createDocumentUploadQueue } from "./knowledge-upload.js";
import {
  $, $$, can, esc, date, pageHeader, dataTable, emptyState, field, input, select,
  openDialog, confirmAction, toast, statusBadge, pagination, icon,
} from "./ui.js";

const queryParams = () => new URLSearchParams(location.search);
// 中文说明：设置页 beforeunload 监听的解绑函数。切换标签/离开页面前必须调用，否则提示会残留到其他页面。
let clearSettingsGuard = () => {};
const TABS = [
  ["documents", "文档"], ["segments", "分段"], ["search", "检索测试"],
  ["sync", "同步"], ["settings", "设置"], ["access", "授权"],
];

// 中文说明：整页跳转（本模块无前端路由），id 为空表示回知识库列表。
// 约定：只有带 id 时才写 tab，避免列表页出现无意义的 ?tab=documents。
function go(id = "", tab = "documents", values = {}) {
  const query = new URLSearchParams(values);
  if (id) query.set("id", id);
  if (id && tab) query.set("tab", tab);
  location.assign("/knowledge" + (query.size ? "?" + query : ""));
}

// 中文说明：知识库详情页标签栏；“授权”标签只在有 system.permissions.view 时出现。
function tabs(kbId, active) {
  const available = TABS.filter(([key]) => key !== "access" || can("system.permissions.view"));
  return `<nav class="content-tabs" aria-label="知识库功能">${available.map(([key, label]) =>
    `<a href="/knowledge?id=${encodeURIComponent(kbId)}&tab=${key}" ${active === key ? 'aria-current="page"' : ""}>${label}</a>`).join("")}</nav>`;
}

// 中文说明：新建/编辑知识库弹窗（PATCH 时不带 version，知识库基本信息由后端直接覆盖）。
function knowledgeDialog(root, record = null) {
  openDialog(record ? "编辑知识库" : "新建知识库", `<div class="form-grid">
    ${field("名称", input("name", record?.name || "", 'required maxlength="200"'), "", true)}
    ${field("说明", `<textarea name="description" maxlength="4000">${esc(record?.description || "")}</textarea>`, "", true)}
  </div>`, {
    onSave: async form => {
      const name = String(form.get("name") || "").trim();
      if (!name) throw new Error("请填写知识库名称。");
      if (record) await api.patch(`/api/knowledge/${record.id}`, { name, description: form.get("description") });
      else await api.post("/api/knowledge", { name, description: form.get("description") });
      toast(record ? "知识库已更新" : "知识库已创建");
      await renderKnowledge(root);
    },
  });
}

// 中文说明：知识库列表页。展示文档/分段/向量模型与更新时间，入口权限 knowledge.create 决定是否可新建。
async function renderKnowledgeList(root) {
  const result = await api.get("/api/knowledge");
  const table = result.items.length ? dataTable(["知识库", "文档", "分段", "模型", "更新时间", "操作"], result.items, item => `<tr>
    <td><a class="cell-primary" href="/knowledge?id=${item.id}">${esc(item.name)}</a>${item.description ? `<span class="cell-secondary content-summary">${esc(item.description)}</span>` : ""}</td>
    <td>${item.ready_document_count} / ${item.document_count}${item.failed_document_count ? `<span class="cell-secondary content-error">${item.failed_document_count} 个失败</span>` : ""}</td>
    <td>${item.chunk_count}</td><td>${esc(item.embedding_model || "—")}</td><td>${date(item.updated_at)}</td>
    <td><a class="text-button" href="/knowledge?id=${item.id}">管理</a></td></tr>`) : emptyState("尚无知识库", "创建后可导入文档、维护分段并测试召回。", can("knowledge.create") ? '<button class="button primary" type="button" data-new-kb>新建知识库</button>' : "");
  root.innerHTML = pageHeader("知识库", "文档、分段、检索与授权。", can("knowledge.create") ? '<button class="button primary" type="button" data-new-kb>新建知识库</button>' : "") + `<section class="panel content-panel">${table}</section>`;
  $$('[data-new-kb]', root).forEach(element => element.addEventListener("click", () => knowledgeDialog(root)));
}

// 中文说明：详情页页头与统计条（各标签共用），并绑定编辑/删除知识库。
// 删除属不可逆操作（连带索引与分段），走统一确认弹窗；源文件不删除，文案已明确这一点。
function detailHeader(root, kb, active) {
  const actions = `<a class="button" href="/knowledge">返回列表</a>
    ${can("knowledge.edit") ? '<button class="button" type="button" data-edit-kb>编辑</button>' : ""}
    ${can("knowledge.delete") ? '<button class="button danger" type="button" data-delete-kb>删除</button>' : ""}`;
  root.innerHTML = pageHeader(kb.name, kb.description || "知识库管理", actions) +
    `<div class="content-record-strip"><span><b>文档</b>${kb.ready_document_count} / ${kb.document_count}</span><span><b>分段</b>${kb.chunk_count}</span><span><b>向量模型</b>${esc(kb.embedding_model || "—")}</span><span><b>状态</b>${statusBadge(kb.status)}</span></div>` + tabs(kb.id, active);
  $("[data-edit-kb]", root)?.addEventListener("click", () => knowledgeDialog(root, kb));
  $("[data-delete-kb]", root)?.addEventListener("click", () => confirmAction(
    "删除知识库", `删除“${kb.name}”及其索引与分段？源文件不会被删除。`, async () => {
      await api.delete(`/api/knowledge/${kb.id}`); toast("知识库已删除"); go();
    }, "删除",
  ));
}

// 中文说明：分段参数校验：重叠字符必须小于分段长度，否则会生成重复内容（后端同样拒绝）。
function validateChunking(form) {
  if (Number(form.get("overlap")) >= Number(form.get("chunk_size"))) throw new Error("重叠字符必须小于分段长度，请减少重叠字符或增大分段长度。");
}

/**
 * 中文说明：添加文档弹窗（本模块最复杂流程）：
 *   来源二选一——上传新文件（可多选、可继续追加，逐个“上传→解析”，失败的保留 fileId 便于重试）或从文件库选择已有文件。
 * 参数：root 页面容器；kb 当前知识库；settings 知识库设置（提供默认分段参数）。
 * 副作用：POST /api/files 上传、POST /api/knowledge/documents 解析入库；文件会真实写入文件库。
 * 权限：上传来源需 files.create；无该权限时只保留“从文件库选择”来源。
 */
function addDocumentDialog(root, kb, settings) {
  const canUpload = can("files.create");
  let queueDialog;
  const queue = createDocumentUploadQueue({
    upload: async file => {
      const upload = new FormData();
      upload.set("file", file);
      return (await api.upload("/api/files", upload)).id;
    },
    parse: (fileId, parameters) => api.post("/api/knowledge/documents", {
      kb_id: kb.id, file_id: fileId, ...parameters,
    }),
    onChange: items => {
      if (!queueDialog) return;
      // 队列摘要：已加入/失败数量 + 当前正在处理的文件；隐藏字段 upload_selection 会同步队列内容，
      // 后端据此判断本次提交涉及哪些文件（避免重复解析）
      const ready = items.filter(item => item.status === "ready").length;
      const failed = items.filter(item => item.status === "failed").length;
      const active = items.find(item => ["uploading", "parsing"].includes(item.status));
      const labels = { pending: "待上传", uploading: "正在上传", parsing: "正在解析", ready: "已加入", failed: "失败" };
      $('[data-upload-summary]', queueDialog).textContent = items.length
        ? `共 ${items.length} 个文件 · 已加入 ${ready} 个${failed ? ` · 失败 ${failed} 个` : ""}${active ? ` · ${labels[active.status]}：${active.file.name}` : ""}`
        : "尚未选择文件，可一次选择多个文件，也可继续添加。";
      $('[data-upload-list]', queueDialog).innerHTML = items.map((item, index) => `<li class="knowledge-upload-item" data-upload-state="${item.status}">
        <div><strong>${esc(item.file.name)}</strong><span>${item.file.size < 1024 ? `${item.file.size} B` : `${(item.file.size / 1024).toFixed(1)} KB`} · ${labels[item.status]}</span>${item.error ? `<p class="content-error">${esc(item.error)}</p>` : ""}</div>
        <button class="text-button" type="button" data-remove-upload="${index}" aria-label="从队列移除 ${esc(item.file.name)}">移除</button>
      </li>`).join("");
      $('[name=new_file]', queueDialog).required = items.length === 0;
      $('[name=upload_selection]', queueDialog).value = JSON.stringify(items.map(item => item.key));
    },
  });
  openDialog("添加文档", `<div class="form-grid">
    ${canUpload ? `<div class="form-field wide"><span>文档来源</span><div class="checkbox-options document-source-options" role="radiogroup" aria-label="文档来源">
      <label><input type="radio" name="document_source" value="upload" checked>上传新文件（支持批量）</label>
      <label><input type="radio" name="document_source" value="existing">从文件库选择</label>
    </div></div>` : ""}
    <div class="form-field wide" data-existing-document><div class="form-grid">
    ${field("查找文件", input("file_query", "", 'type="search" maxlength="200" placeholder="输入文件名查找文件库"'), "", true)}
    ${field("文件", '<select name="file_id" required disabled><option value="">正在加载文件…</option></select>', "已在本知识库中的文件不可重复添加；修改解析参数请使用文档列表中的“重建”。", true)}
    <p class="content-form-note" data-file-search-status role="status"></p>
    </div></div>
    ${canUpload ? `<div class="form-field wide" data-upload-document>${field("选择文件（可多选）", '<div class="knowledge-upload-picker"><span aria-hidden="true">选择文件 / 继续添加</span><input name="new_file" type="file" multiple required aria-label="选择文件，可多选或继续添加"></div>', "可继续选择文件追加到队列。文件会保存在文件库，逐个解析并加入当前知识库。", true)}
      <input type="hidden" name="upload_selection" value="[]">
      <p class="content-form-note" data-upload-summary role="status" aria-live="polite">尚未选择文件，可一次选择多个文件，也可继续添加。</p>
      <ul class="knowledge-upload-list" data-upload-list aria-label="待上传文件及处理结果"></ul>
      <p class="content-form-note">所有文件使用下方解析参数。某个文件失败不会中断其余文件；再次点击“解析并加入”只处理未成功的文件。移除仅移出本次队列，不删除已保存文件。</p>
    </div>` : ""}
    ${field("分段长度", input("chunk_size", settings.chunk_size, 'type="number" min="200" max="2000" required'))}
    ${field("重叠字符", input("overlap", settings.overlap, 'type="number" min="0" max="400" required'))}
  </div>`, {
    saveLabel: "解析并加入",
    onReady: dialog => {
      queueDialog = dialog;
      const search = $("[name=file_query]", dialog), picker = $("[name=file_id]", dialog);
      const status = $("[data-file-search-status]", dialog);
      // requestId 用于丢弃过期的文件搜索响应；loadingFiles 让下拉在加载完成前保持禁用
      let requestId = 0, timer, loadingFiles = true;
      const uploading = () => $('[name=document_source]:checked', dialog)?.value === "upload";
      const setSource = () => {
        const isUpload = uploading();
        $('[data-existing-document]', dialog).hidden = isUpload;
        if (canUpload) $('[data-upload-document]', dialog).hidden = !isUpload;
        search.disabled = isUpload;
        picker.disabled = isUpload || loadingFiles || picker.options.length === 1;
        if (canUpload) $('[name=new_file]', dialog).disabled = !isUpload;
      };
      $$('[name=document_source]', dialog).forEach(control => control.addEventListener("change", setSource));
      $('[name=new_file]', dialog)?.addEventListener("change", event => {
        queue.add([...event.target.files]);
        event.target.value = "";
        $('.form-error', dialog).hidden = true;
      });
      $('[data-upload-list]', dialog)?.addEventListener("click", event => {
        const remove = event.target.closest('[data-remove-upload]');
        if (remove) {
          queue.remove(queue.items[Number(remove.dataset.removeUpload)].key);
          $('.form-error', dialog).hidden = true;
        }
      });
      setSource();
      const lookup = async () => {
        const current = ++requestId;
        loadingFiles = true;
        picker.disabled = true;
        status.textContent = "正在查找文件…";
        try {
          const result = await api.get(`/api/files?q=${encodeURIComponent(search.value.trim())}&page=1&page_size=100`);
          if (current !== requestId || !dialog.contains(picker)) return;
          // Check all candidates, not just the current page of KB documents.
          const parameters = new URLSearchParams({ page: "1", page_size: "200" });
          result.items.forEach(item => parameters.append("file_ids", item.id));
          const membership = result.items.length
            ? await api.get(`/api/knowledge/${kb.id}/documents?${parameters}`)
            : { items: [] };
          if (current !== requestId || !dialog.contains(picker)) return;
          const existing = new Set(membership.existing_file_ids || membership.items.map(item => item.file_id));
          picker.innerHTML = `<option value="">选择文件</option>` + result.items.map(item => `<option value="${esc(item.id)}" ${existing.has(item.id) ? "disabled" : ""}>${esc(item.name)} · v${item.revision_number}${existing.has(item.id) ? " · 已添加" : ""}</option>`).join("");
          loadingFiles = false;
          picker.disabled = uploading();
          status.textContent = !result.items.length ? "没有匹配文件，请调整文件名或先上传文件。" : result.total > result.items.length ? `共 ${result.total} 个文件，显示前 ${result.items.length} 个。输入更完整的文件名缩小范围。` : `找到 ${result.items.length} 个文件`;
        } catch (error) {
          if (current !== requestId || !dialog.contains(picker)) return;
          picker.innerHTML = '<option value="">文件列表加载失败</option>';
          status.textContent = error.message + "；修改搜索词后重试。";
        }
      };
      search.addEventListener("input", () => { ++requestId; loadingFiles = true; clearTimeout(timer); picker.disabled = true; timer = setTimeout(lookup, 250); });
      dialog.addEventListener("close", () => { ++requestId; clearTimeout(timer); }, { once: true });
      return lookup();
    },
    onSave: async (form, dialog) => {
      validateChunking(form);
      let fileId = form.get("file_id");
      const isUpload = canUpload && form.get("document_source") === "upload";
      const submit = $('[type=submit]', dialog);
      if (isUpload) {
        submit.textContent = "正在批量处理…";
        const result = await queue.run({ chunk_size: Number(form.get("chunk_size")), overlap: Number(form.get("overlap")) });
        // Refresh successful imports even if other files still need attention.
        if (result.ready) await renderKnowledge(root);
        if (result.failed) throw new Error(`已加入 ${result.ready} 个，失败 ${result.failed} 个。请查看文件旁的原因，移除无效文件或再次点击“解析并加入”重试；已成功的文件不会重复处理。`);
        toast(`${result.ready} 个文档已解析并加入知识库`);
        return;
      }
      if (!fileId) throw new Error("请先选择要添加的文件。");
      submit.textContent = "正在解析…";
      await api.post("/api/knowledge/documents", {
        kb_id: kb.id, file_id: fileId,
        chunk_size: Number(form.get("chunk_size")), overlap: Number(form.get("overlap")),
      });
      toast("文档已解析并加入知识库");
      await renderKnowledge(root);
    },
  });
}

// 中文说明：知识库文档列表（服务端分页）。行内动作：下载（需 files.export）、重建索引（需 knowledge.execute）、
// 移除（需 knowledge.delete，仅移出知识库且走统一确认弹窗，源文件保留）。
// 越界页码由 redirectEmptyPage 回落到末页，避免删空当前页后显示空白列表。
async function renderDocuments(root, kb) {
  const [documents, settings] = await Promise.all([
    api.get(`/api/knowledge/${kb.id}/documents?page=${currentPage()}&page_size=50`), api.get(`/api/knowledge/${kb.id}/settings`),
  ]);
  const canAddDocument = can("knowledge.create") && can("files.view");
  if (redirectEmptyPage(documents, documents.page_size)) return;
  const toolbar = `<div class="content-section-toolbar"><span>${documents.total} 个文档</span>${canAddDocument ? '<button class="button primary" type="button" data-add-document>添加文档</button>' : ""}</div>`;
  const table = documents.items.length ? dataTable(["文档", "状态", "分段", "解析设置", "更新时间", "操作"], documents.items, item => `<tr>
    <td><strong class="cell-primary">${esc(item.name)}</strong><span class="cell-secondary">${esc(item.mime_type || "来源文件不可见")}</span></td>
    <td>${statusBadge(item.status)}${item.error_summary ? `<span class="cell-secondary content-error content-summary">${esc(item.error_summary)}</span>` : ""}</td>
    <td>${item.chunk_count}</td><td>${item.chunk_size} / ${item.overlap}${item.image_count ? `<span class="cell-secondary">识图 ${item.image_count}</span>` : ""}</td>
    <td>${date(item.updated_at)}</td><td><div class="row-actions">
      ${item.download_url && can("files.export") ? `<button class="text-button" type="button" data-document-download="${item.file_id}">下载</button>` : ""}
      ${can("knowledge.execute") ? `<button class="text-button" type="button" data-document-reindex="${item.id}">重建</button>` : ""}
      ${can("knowledge.delete") ? `<button class="text-button danger" type="button" data-document-delete="${item.id}">移除</button>` : ""}
    </div></td></tr>`) : emptyState("尚无文档", "批量上传本地资料，或从文件库选择资料并建立索引。", canAddDocument ? '<button class="button primary" type="button" data-add-document>添加文档</button>' : "");
  root.insertAdjacentHTML("beforeend", `<section class="panel content-panel">${toolbar}${table}${pagination(documents)}</section>`);
  wirePageNavigation(root, documents);
  $$('[data-add-document]', root).forEach(element => element.addEventListener("click", () => addDocumentDialog(root, kb, settings)));
  $$('[data-document-download]', root).forEach(element => element.addEventListener("click", () => {
    const row = documents.items.find(item => item.file_id === element.dataset.documentDownload);
    api.download(`/api/files/${row.file_id}/download`, row.name).catch(error => toast(error.message, true));
  }));
  $$('[data-document-reindex]', root).forEach(element => element.addEventListener("click", () => {
    const row = documents.items.find(item => item.id === element.dataset.documentReindex);
    openDialog("重建文档索引", `<div class="form-grid">
      ${field("分段长度", input("chunk_size", row.chunk_size, 'type="number" min="200" max="2000" required'))}
      ${field("重叠字符", input("overlap", row.overlap, 'type="number" min="0" max="400" required'))}
    </div><p class="content-form-note">重建会重新解析源文件，并替换该文档的分段。对解析分段的手工修改不会保留。</p>`, { saveLabel: "重建", onSave: async form => {
      validateChunking(form);
      await api.post(`/api/knowledge/${kb.id}/documents/${row.id}/reindex`, {
        chunk_size: Number(form.get("chunk_size")), overlap: Number(form.get("overlap")),
      });
      toast("文档索引已重建"); await renderKnowledge(root);
    }});
  }));
  $$('[data-document-delete]', root).forEach(element => element.addEventListener("click", () => {
    const row = documents.items.find(item => item.id === element.dataset.documentDelete);
    confirmAction("移除知识库文档", `从知识库移除“${row.name}”？源文件仍保留在文件库。`, async () => {
      await api.delete(`/api/knowledge/${kb.id}/documents/${row.id}`); toast("文档已移除"); await renderKnowledge(root);
    }, "移除");
  }));
}

// 中文说明：手工新增/编辑分段（文本或问答两种类型）。
// 切换类型时会动态设置 required/disabled：问答类型提交 question+answer，文本类型提交 text，
// 未选中的字段必须禁用，否则会被 FormData 一并带上造成后端校验冲突。
function segmentDialog(root, kb, record = null) {
  const type = record?.segment_type || "text";
  openDialog(record ? "编辑分段" : "新增分段", `<div class="form-grid">
    ${field("类型", select("segment_type", [{ value: "text", label: "文本" }, { value: "qa", label: "问答" }], type, null))}
    ${field("状态", select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], String(record?.enabled ?? true), null))}
    <label class="form-field wide" data-segment-text><span>文本</span><textarea name="text" maxlength="20000">${esc(record?.text || "")}</textarea></label>
    <label class="form-field wide" data-segment-question><span>问题</span><textarea name="question" maxlength="2000">${esc(record?.question || "")}</textarea></label>
    <label class="form-field wide" data-segment-answer><span>答案</span><textarea name="answer" maxlength="20000">${esc(record?.answer || "")}</textarea></label>
  </div>`, {
    wide: true,
    onReady: dialog => {
      const toggle = () => {
        const qa = $("[name=segment_type]", dialog).value === "qa";
        $("[data-segment-text]", dialog).hidden = qa;
        $("[data-segment-question]", dialog).hidden = !qa;
        $("[data-segment-answer]", dialog).hidden = !qa;
        for (const name of ["text", "question", "answer"]) {
          const control = $(`[name=${name}]`, dialog);
          control.required = name === "text" ? !qa : qa;
          control.disabled = !control.required;
        }
      };
      $("[name=segment_type]", dialog).addEventListener("change", toggle); toggle();
    },
    onSave: async form => {
      const payload = {
        segment_type: form.get("segment_type"), text: String(form.get("text") || "").trim(),
        question: String(form.get("question") || "").trim(), answer: String(form.get("answer") || "").trim(),
        enabled: form.get("enabled") === "true",
      };
      if (payload.segment_type === "qa" ? !payload.question || !payload.answer : !payload.text) throw new Error(payload.segment_type === "qa" ? "请填写问题和答案。" : "请填写分段文本。");
      if (record) await api.patch(`/api/knowledge/${kb.id}/segments/${record.id}`, { ...payload, version: record.version });
      else await api.post(`/api/knowledge/${kb.id}/segments`, payload);
      toast(record ? "分段已更新" : "分段已创建"); await renderKnowledge(root);
    },
  });
}

// 中文说明：批量导入分段（JSON/CSV/TXT）。CSV 字段名固定为 segment_type/text/question/answer/enabled，
// 该约定由后端模板定义，页面注释与后端文档需保持一致。
function importSegmentsDialog(root, kb) {
  openDialog("导入分段", `<div class="form-grid">
    ${field("JSON、CSV 或 TXT", '<input name="file" type="file" accept=".json,.csv,.txt,text/plain,text/csv,application/json" required>', "", true)}
  </div><p class="content-form-note">CSV 字段：segment_type、text、question、answer、enabled。TXT 按空行分段。</p>`, {
    saveLabel: "导入",
    onSave: async form => {
      const result = await api.upload(`/api/knowledge/${kb.id}/segments/import`, form);
      toast(`已导入 ${result.count} 个分段`); await renderKnowledge(root);
    },
  });
}

// 中文说明：分段管理页（筛选 + 分页 + 批量操作），筛选条件保存在 URL 查询串。
async function renderSegments(root, kb) {
  const query = queryParams();
  const q = query.get("q") || "";
  const type = query.get("segment_type") || "";
  const enabled = query.get("enabled") || "";
  const pageValue = Number(query.get("page") || 1);
  const page = Number.isSafeInteger(pageValue) && pageValue > 0 ? pageValue : 1;
  const request = new URLSearchParams({ q, page: String(page), page_size: "50" });
  if (type) request.set("segment_type", type);
  if (enabled) request.set("enabled", enabled);
  const result = await api.get(`/api/knowledge/${kb.id}/segments?${request}`);
  const toolbar = `<form class="list-toolbar content-toolbar" data-segment-filter>
    <label class="search-input">${icon("search")}<input name="q" aria-label="搜索分段" placeholder="搜索文本、问题或答案" value="${esc(q)}"></label>
    ${select("segment_type", [{ value: "text", label: "文本" }, { value: "qa", label: "问答" }], type, "全部类型").replace("<select", '<select aria-label="分段类型"')}
    ${select("enabled", [{ value: "true", label: "启用" }, { value: "false", label: "停用" }], enabled, "全部状态").replace("<select", '<select aria-label="分段状态"')}
    <button class="button" type="submit">查询</button>${q || type || enabled ? '<button class="button quiet" type="button" data-segment-reset>重置</button>' : ""}
    <span class="content-toolbar-spacer"></span>
    ${can("knowledge.export") ? '<button class="button" type="button" data-segment-export>导出</button>' : ""}
    ${can("knowledge.create") ? '<button class="button" type="button" data-segment-import>导入</button><button class="button primary" type="button" data-segment-new>新增分段</button>' : ""}
  </form>`;
  const table = result.items.length ? dataTable(["内容", "类型", "来源", "向量", "状态", "更新时间", "操作"], result.items, item => `<tr>
    <td class="content-segment-cell"><strong>${esc(item.segment_type === "qa" ? item.question : item.text)}</strong>${item.segment_type === "qa" ? `<span>${esc(item.answer)}</span>` : ""}</td>
    <td>${item.segment_type === "qa" ? "问答" : "文本"}</td><td>${item.source === "parser" ? esc(item.file_name) : item.source === "import" ? "导入" : "手工"}</td>
    <td>${statusBadge(item.embedding_status)}</td><td>${statusBadge(item.enabled)}</td><td>${date(item.updated_at || item.created_at)}</td>
    <td><div class="row-actions">${can("knowledge.edit") ? `<button class="text-button" type="button" data-segment-edit="${item.id}">编辑</button>` : ""}${can("knowledge.delete") ? `<button class="text-button danger" type="button" data-segment-delete="${item.id}">删除</button>` : ""}</div></td></tr>`) : emptyState("没有匹配分段", q || type || enabled ? "调整筛选条件后重试。" : "文档解析或手工新增后会显示在这里。");
  root.insertAdjacentHTML("beforeend", `<section class="panel content-panel">${toolbar}${table}${pagination({ ...result, page_size: 50 })}</section>`);
  $("[data-segment-filter]", root)?.addEventListener("submit", event => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    go(kb.id, "segments", Object.fromEntries([...form].filter(([, value]) => value)));
  });
  $("[data-segment-reset]", root)?.addEventListener("click", () => go(kb.id, "segments"));
  $("[data-page-prev]", root)?.addEventListener("click", () => { query.set("page", String(page - 1)); query.set("id", kb.id); query.set("tab", "segments"); location.assign("/knowledge?" + query); });
  $("[data-page-next]", root)?.addEventListener("click", () => { query.set("page", String(page + 1)); query.set("id", kb.id); query.set("tab", "segments"); location.assign("/knowledge?" + query); });
  $("[data-segment-new]", root)?.addEventListener("click", () => segmentDialog(root, kb));
  $("[data-segment-import]", root)?.addEventListener("click", () => importSegmentsDialog(root, kb));
  $("[data-segment-export]", root)?.addEventListener("click", () => api.download(`/api/knowledge/${kb.id}/segments/export`, `${kb.name}-分段.csv`).catch(error => toast(error.message, true)));
  $$('[data-segment-edit]', root).forEach(element => element.addEventListener("click", () => segmentDialog(root, kb, result.items.find(item => item.id === element.dataset.segmentEdit))));
  $$('[data-segment-delete]', root).forEach(element => element.addEventListener("click", () => {
    const row = result.items.find(item => item.id === element.dataset.segmentDelete);
    confirmAction("删除分段", `删除此${row.segment_type === "qa" ? "问答" : "文本"}分段？`, async () => {
      await api.delete(`/api/knowledge/${kb.id}/segments/${row.id}`); toast("分段已删除"); await renderKnowledge(root);
    }, "删除");
  }));
}

// 中文说明：召回记录表格（检索测试页的历史，只读，用于观察命中数与耗时）。
function recallTable(items) {
  return items.length ? dataTable(["查询", "模式", "命中", "最高分", "耗时", "时间"], items, item => `<tr>
    <td class="content-wrap">${esc(item.query)}</td><td>${esc(({ hybrid: "混合检索", keyword: "关键词", semantic: "语义" })[item.retrieval_mode] || item.retrieval_mode)}</td><td>${item.hit_count}</td>
    <td>${Number(item.max_score || 0).toFixed(4)}</td><td>${item.duration_ms} ms</td><td>${date(item.created_at)}</td></tr>`) : emptyState("尚无检索记录", "执行一次检索后会记录结果数量与耗时。");
}

// 中文说明：检索测试页。提交会真实调用 POST /api/knowledge/search（会写入召回记录，影响“召回记录”统计）。
// 检索失败只替换结果区为错误占位，不抛出，便于用户调整表达后重试。
// rerank_status 反映重排是否真正生效（fallback/mixed_profiles 表示退回原始排序），页面需如实展示，不要统一显示“已重排”。
async function renderSearch(root, kb) {
  const [settings, logs] = await Promise.all([
    api.get(`/api/knowledge/${kb.id}/settings`), api.get(`/api/knowledge/recall-logs?knowledge_base_id=${kb.id}&limit=30`),
  ]);
  root.insertAdjacentHTML("beforeend", `<section class="panel content-panel content-search-panel">
    <form class="list-toolbar content-toolbar" data-recall-form>
      <label class="search-input content-query-input">${icon("search")}<input name="query" required maxlength="4000" aria-label="检索问题" placeholder="输入要检索的问题"></label>
      ${select("mode", [{ value: "hybrid", label: "混合检索" }, { value: "keyword", label: "关键词" }, { value: "semantic", label: "语义" }], settings.retrieval_mode, null)}
      <label class="content-compact-field"><span>召回</span>${input("top_k", settings.top_k, 'type="number" min="1" max="20" aria-label="召回数量"')}</label>
      <button class="button primary" type="submit">检索</button>
    </form><div class="content-search-results" data-search-results>${emptyState("等待检索", "结果会显示来源、位置和相关度。")}</div>
  </section><section class="panel content-panel section-gap"><div class="content-section-toolbar"><strong>召回记录</strong><span data-recall-count>${logs.count} 条</span></div><div data-recall-logs>${recallTable(logs.items)}</div></section>`);
  $("[data-recall-form]", root).addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const submit = $('[type="submit"]', event.currentTarget);
    const target = $("[data-search-results]", root);
    submit.disabled = true; submit.textContent = "检索中…";
    try {
      const result = await api.post("/api/knowledge/search", {
        query: form.get("query"), knowledge_base_ids: [kb.id], mode: form.get("mode"), top_k: Number(form.get("top_k")),
      });
      target.innerHTML = result.items.length ? dataTable(["相关度", "排序方式", "来源", "位置", "内容"], result.items, item => `<tr>
        <td>${Number(item.score).toFixed(4)}</td><td>${esc(({applied:"模型重排",fallback:"原始排序（重排未完成）",mixed_profiles:"原始排序（不同配置）",disabled:"原始排序"})[item.rerank_status] || "原始排序")}</td><td>${esc(item.file_name || "手工分段")}</td><td>${esc(Object.values(item.location || {}).join(" · ") || "—")}</td><td class="content-result-text">${esc(item.content)}</td></tr>`) : emptyState("没有命中内容", "可调整表达、检索模式或分段内容。");
      const refreshed = await api.get(`/api/knowledge/recall-logs?knowledge_base_id=${kb.id}&limit=30`);
      $("[data-recall-logs]", root).innerHTML = recallTable(refreshed.items);
      $("[data-recall-count]", root).textContent = `${refreshed.count} 条`;
    } catch (error) { target.innerHTML = emptyState("检索失败", error.message); }
    finally { submit.disabled = false; submit.textContent = "检索"; }
  });
}

// 中文说明：知识库设置页。无 knowledge.edit 时表单整体禁用（只读展示）。
// 副作用：注册 beforeunload 拦截未保存修改，并把解绑函数交给模块级 clearSettingsGuard；
//   保存走 PATCH /settings（带 version），成功后重绘页面（重绘会先调用清理函数解绑）。
async function renderSettings(root, kb) {
  const settings = await api.get(`/api/knowledge/${kb.id}/settings`);
  root.insertAdjacentHTML("beforeend", `<section class="panel content-form-panel"><form data-kb-settings>
    <div class="content-section-toolbar"><strong>检索与分段设置</strong><span data-settings-state role="status"></span>${can("knowledge.edit") ? '<button class="button primary" type="submit">保存</button>' : ""}</div>
    <div class="form-grid content-form-grid">
      ${field("默认检索模式", select("retrieval_mode", [{ value: "hybrid", label: "混合检索" }, { value: "keyword", label: "关键词" }, { value: "semantic", label: "语义" }], settings.retrieval_mode, null))}
      ${field("默认召回数量", input("top_k", settings.top_k, 'type="number" min="1" max="20" required'))}
      ${field("最低相关度", input("min_score", settings.min_score, 'type="number" min="0" max="100" step="0.0001" required'))}
      ${field("分段长度", input("chunk_size", settings.chunk_size, 'type="number" min="200" max="2000" required'))}
      ${field("重叠字符", input("overlap", settings.overlap, 'type="number" min="0" max="400" required'))}
      ${field("向量模型", input("embedding_model", settings.embedding_model, 'maxlength="200"'))}
      ${field("结果重排", select("rerank_enabled", [{value:"false", label:"关闭"}, {value:"true", label:"启用"}], String(settings.rerank_enabled), null))}
      ${field("重排模型配置名称", input("rerank_model", settings.rerank_model, 'maxlength="200"'))}
      <p class="content-form-note">使用已启用的重排模型。最多重排 60 条候选，10 秒内未完成则保留原检索结果。</p>
    </div>
  </form></section>`);
  if (!can("knowledge.edit")) $$('[data-kb-settings] input, [data-kb-settings] select', root).forEach(element => { element.disabled = true; });
  const settingsForm = $("[data-kb-settings]", root);
  const snapshot = () => JSON.stringify([...new FormData(settingsForm)]);
  const original = snapshot();
  const hasChanges = () => settingsForm.isConnected && snapshot() !== original;
  const notifyChanges = () => { $("[data-settings-state]", settingsForm).textContent = hasChanges() ? "有未保存修改" : ""; };
  settingsForm.addEventListener("input", notifyChanges);
  settingsForm.addEventListener("change", notifyChanges);
  const beforeUnload = event => { if (hasChanges()) { event.preventDefault(); event.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);
  clearSettingsGuard = () => window.removeEventListener("beforeunload", beforeUnload);
  $("[data-kb-settings]", root).addEventListener("submit", async event => {
    event.preventDefault();
    if (!can("knowledge.edit")) return;
    const formElement = event.currentTarget, form = new FormData(formElement), submit = $('[type="submit"]', formElement);
    if (submit.disabled) return;
    submit.disabled = true; submit.textContent = "保存中…";
    try {
      validateChunking(form);
      await api.patch(`/api/knowledge/${kb.id}/settings`, {
        version: settings.version, retrieval_mode: form.get("retrieval_mode"),
        top_k: Number(form.get("top_k")), min_score: Number(form.get("min_score")),
        chunk_size: Number(form.get("chunk_size")), overlap: Number(form.get("overlap")),
        embedding_model: form.get("embedding_model"), rerank_enabled: form.get("rerank_enabled") === "true",
        rerank_model: form.get("rerank_model"),
      });
      toast("知识库设置已保存"); await renderKnowledge(root);
    } catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; submit.textContent = "保存"; }
  });
}

// 中文说明：按文件分类同步（把文件库里的文件批量导入知识库并建立索引）。
// 权限：需同时具备 knowledge.execute 与 files.view；无 files.view 时不加载分类列表（退化为“全部文件”）。
// 副作用：POST /{id}/sync 会真实写入文档与向量；勾选“重建已存在文档”会重新解析，耗时更长。
async function renderSync(root, kb) {
  const [categories, runs] = await Promise.all([
    can("files.view") ? api.get("/api/files/categories") : Promise.resolve({ items: [] }), api.get(`/api/knowledge/${kb.id}/sync-runs?limit=50`),
  ]);
  const table = runs.items.length ? dataTable(["来源", "状态", "导入", "跳过", "失败", "完成时间", "说明"], runs.items, item => `<tr>
    <td>${esc(categories.items.find(category => category.id === item.source_config.category_id)?.name || "全部文件")}</td><td>${statusBadge(item.status)}</td>
    <td>${item.imported_count}</td><td>${item.skipped_count}</td><td>${item.failed_count}</td><td>${date(item.completed_at)}</td><td class="content-wrap content-error">${esc(item.error_summary || "—")}</td></tr>`) : emptyState("尚无同步记录", "选择文件分类后执行同步。");
  root.insertAdjacentHTML("beforeend", `<section class="panel content-panel">
    <form class="list-toolbar content-toolbar" data-sync-form>
      ${select("category_id", categories.items.map(item => ({ value: item.id, label: item.name })), "", "全部文件").replace("<select", '<select aria-label="同步文件分类"')}
      <label class="content-check"><input type="checkbox" name="reindex">重建已存在文档</label>
      ${can("knowledge.execute") && can("files.view") ? '<button class="button primary" type="submit">立即同步</button>' : ""}
      <span class="content-result-count">${runs.count} 次运行</span>
    </form>${table}</section>`);
  $("[data-sync-form]", root).addEventListener("submit", async event => {
    event.preventDefault();
    if (!can("knowledge.execute") || !can("files.view")) return;
    const form = new FormData(event.currentTarget);
    const submit = $('[type="submit"]', event.currentTarget); submit.disabled = true; submit.textContent = "同步中…";
    try {
      const result = await api.post(`/api/knowledge/${kb.id}/sync`, {
        category_id: form.get("category_id") || null, file_ids: [], reindex: form.has("reindex"),
      });
      toast(`同步完成：导入 ${result.imported_count}，跳过 ${result.skipped_count}，失败 ${result.failed_count}`, result.failed_count > 0);
      await renderKnowledge(root);
    } catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; submit.textContent = "立即同步"; }
  });
}

// 中文说明：为知识库追加角色级授权（view/create/edit/delete/execute/export，与后端动作码一致）。
// 注意：这是角色数据范围之外的“额外访问范围”，不是替代角色权限。
function roleGrantDialog(root, kb, roles) {
  openDialog("新增角色授权", `<div class="form-grid">
    ${field("角色", select("role_id", roles.map(item => ({ value: item.id, label: item.name })), "", "选择角色").replace("<select", "<select required"), "", true)}
    ${field("允许操作", `<div class="checkbox-options">${[
      ["view", "查看"], ["create", "创建"], ["edit", "修改"], ["delete", "删除"], ["execute", "执行"], ["export", "导出"],
    ].map(([value, label]) => `<label><input type="checkbox" name="actions" value="${value}" ${value === "view" ? "checked" : ""}>${label}</label>`).join("")}</div>`, "", true)}
  </div>`, {
    onSave: async form => {
      if (!form.getAll("actions").length) throw new Error("请至少选择一项允许操作。");
      await api.post(`/api/knowledge/${kb.id}/role-grants`, { role_id: form.get("role_id"), actions: form.getAll("actions") });
      toast("角色授权已添加"); await renderKnowledge(root);
    },
  });
}

// 中文说明：授权标签页（列出/新增/撤销角色授权）。
// 权限：管理动作需 system.permissions.manage，无权限时整页只读；撤销走统一确认弹窗（立即失效）。
async function renderAccess(root, kb) {
  const [grants, roles] = await Promise.all([
    api.get(`/api/knowledge/${kb.id}/role-grants`), api.get("/api/v1/system/roles?page=1&page_size=100"),
  ]);
  const table = grants.items.length ? dataTable(["角色", "允许操作", "授权时间", "操作"], grants.items, item => `<tr>
    <td><strong>${esc(item.role_name)}</strong></td><td>${esc(item.actions.join("、"))}</td><td>${date(item.created_at)}</td>
    <td>${can("system.permissions.manage") ? `<button class="text-button danger" type="button" data-grant-delete="${item.id}">撤销</button>` : "只读"}</td></tr>`) : emptyState("尚无额外角色授权", "知识库仍受角色模块权限与数据范围约束。");
  root.insertAdjacentHTML("beforeend", `<section class="panel content-panel"><div class="content-section-toolbar"><span>${grants.count} 条授权</span>${can("system.permissions.manage") ? '<button class="button primary" type="button" data-grant-new>新增授权</button>' : ""}</div>${table}</section>`);
  $("[data-grant-new]", root)?.addEventListener("click", () => roleGrantDialog(root, kb, roles.items));
  $$('[data-grant-delete]', root).forEach(element => element.addEventListener("click", () => confirmAction(
    "撤销角色授权", "撤销后，角色成员会立即失去此知识库的额外访问范围。", async () => {
      await api.delete(`/api/knowledge/${kb.id}/role-grants/${element.dataset.grantDelete}`); toast("角色授权已撤销"); await renderKnowledge(root);
    }, "撤销",
  )));
}

/**
 * 中文说明：知识库页入口。无 ?id 时渲染知识库列表；有 id 时先解绑上一页的未保存提示，再按 tab 分发。
 * 约束：view=access 但缺少 system.permissions.view 时重定向回文档标签（不能靠隐藏标签来兜底）；
 *   非法 tab 值一律回落到 documents。
 * 副作用：GET /api/knowledge/{id} 及对应子视图请求；重写 root 内容。
 */
export async function renderKnowledge(root) {
  clearSettingsGuard();
  const query = queryParams();
  const kbId = query.get("id") || "";
  if (!kbId) { await renderKnowledgeList(root); return; }
  const active = TABS.some(([key]) => key === query.get("tab")) ? query.get("tab") : "documents";
  if (active === "access" && !can("system.permissions.view")) { go(kbId, "documents"); return; }
  const kb = await api.get(`/api/knowledge/${kbId}`);
  detailHeader(root, kb, active);
  if (active === "segments") await renderSegments(root, kb);
  else if (active === "search") await renderSearch(root, kb);
  else if (active === "settings") await renderSettings(root, kb);
  else if (active === "sync") await renderSync(root, kb);
  else if (active === "access") await renderAccess(root, kb);
  else await renderDocuments(root, kb);
}
