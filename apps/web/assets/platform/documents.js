/**
 * 文档与 PPT 模块（路由 /documents?tab=generations|templates）
 *
 * 位置：渲染三个视图——生成记录列表（草稿/生成中/已完成/失败）、生成详情（结构化内容、生成过程、
 *   预览、下载、复制、归档；PPT 人工编写模式还有“建立大纲 / 填写下一批页面”的分步流程）、PPT 模板库。
 * 依赖接口前缀：/api/v1/documents（/generations 列表与详情、/generations/{id}/execute 执行、
 *   /preview 预览图、/download 下载、/copy 复制、/events 过程事件、
 *   /presentation-plan 与 /presentation-slides 分步编写、/presentation-draft 草稿进度、/templates 模板）。
 *   业务来源选择复用 resource-hierarchy.js 的资产目录（resources 域）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 后端响应可能是 { success, data } 包装（本模块的 request 会解包）——不要把两者混用，
 *   新增接口调用时先确认返回结构再决定走 request 还是直接 api.*。
 * - 权限码必须与后端目录一致：documents.create / edit / execute / export / delete。
 *   “开始生成”只在 status 为 draft/failed 且 PPT 大纲已补齐时出现；归档不删除已生成文件与追溯记录。
 * - 所有写操作都带 version（草稿版本号），并发编辑时后端会拒绝过期提交。
 * - PPT 人工编写是分阶段流程：先生成大纲，再按批（每批最多 4 页）填写正文，最后才允许执行生成；
 *   前端按钮显隐必须与该状态机一致，否则会出现“能点但必然失败”的操作。
 * - 业务来源里当前账号无权查看的资源用 restrictedSourceCount 提示数量，不展示名称。
 * - 模板上传/移除仅对客户模板开放，内置模板不可删除。
 */

import { api, ApiError } from "./api.js";
import {
  $, $$, state, can, esc, date, pageHeader, button, dataTable, emptyState,
  statusBadge, field, input, select, openDialog, confirmAction, toast,
} from "./ui.js";
import { loadResourceCatalog, mergeResourceItems, resourceSelectOptions } from "./resource-hierarchy.js";

const BASE = "/api/v1/documents";
const textarea = (name, value = "", attributes = "") => `<textarea name="${esc(name)}" ${attributes}>${esc(value)}</textarea>`;

// 中文说明：本模块多个接口返回 { success, data } 包装，这里统一解包；
// 非包装结构（如列表直接返回数组/对象）原样返回，因此调用方拿到的一定是业务数据本体。
async function request(method, path, body) {
  const result = await api[method](path, body);
  return result?.success === true && "data" in result ? result.data : result;
}
const get = path => request("get", path);
const post = (path, body) => request("post", path, body);
const put = (path, body) => request("put", path, body);
const remove = path => request("delete", path);
const query = () => new URLSearchParams(location.search);
const pretty = value => JSON.stringify(value || {}, null, 2);
const outputLabels = { document: "Word", presentation: "PPT", spreadsheet: "Excel", pdf: "PDF" };
const modeLabels = { manual: "人工编写", ai: "AI 生成" };
// 中文说明：生成阶段的进度文案，键名与后端 stage 枚举一一对应（stage 与 status 是两个维度，需分别展示）
const stageLabels = {
  draft: "草稿", outline: "大纲", validating: "校验", authoring: "编写",
  rendering: "渲染", finalizing: "保存", completed: "完成", failed: "失败", archived: "已归档",
};

// 中文说明：以查询串改写方式整页跳转（该页是服务端式渲染，没有前端路由状态）。
function navigate(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) params.set(key, value);
  });
  location.assign("/documents" + (params.size ? "?" + params.toString() : ""));
}

function tabs(active) {
  return `<nav class="catalog-tabs" aria-label="文档与 PPT 视图">
    <a href="/documents?tab=generations" ${active === "generations" ? 'aria-current="page"' : ""}>生成记录</a>
    <a href="/documents?tab=templates" ${active === "templates" ? 'aria-current="page"' : ""}>PPT 模板</a>
  </nav>`;
}

function page(root, active, title, description, actions, content) {
  root.innerHTML = pageHeader(title, description, actions) + tabs(active) + content;
}

// 中文说明：直连 fetch 的鉴权请求，仅用于必须读取原始 Response 的场景（blob 预览/下载、multipart 上传）。
// 约束：仍必须手动带上 Authorization 与 X-Workspace-Id，与 api.js 保持一致；能用 api.* 时不要用这里。
// 失败表现：抛 ApiError（含状态码与后端 message/detail）。
async function fetchAuthorized(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (api.token) headers.Authorization = "Bearer " + api.token;
  if (api.workspace) headers["X-Workspace-Id"] = api.workspace;
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch { /* non-json failure */ }
    throw new ApiError(data.message || data.detail || `请求失败（${response.status}）`, response.status, data);
  }
  return response;
}

// 中文说明：把预览图加载进 <img>（blob URL 在图片 onload 后延迟释放，避免提前撤销导致空白）。
// 失败表现：不抛出，而是把 img 替换为错误文案节点——预览失败不应让整个详情页报错。
async function loadPreview(image, path) {
  try {
    const response = await fetchAuthorized(path);
    const url = URL.createObjectURL(await response.blob());
    image.src = url;
    image.addEventListener("load", () => setTimeout(() => URL.revokeObjectURL(url), 500), { once: true });
  } catch (error) {
    image.replaceWith(Object.assign(document.createElement("p"), { className: "preview-error", textContent: error.message }));
  }
}

// 中文说明：下载已生成文件。文件名优先取产物名，其次记录名（后端 Content-Disposition 未使用）。
async function downloadGeneration(item) {
  // 当前控件自行设置 Blob 文件名，读取字节时关闭响应附件头，避免 PDF 被浏览器提前接管。
  const response = await fetchAuthorized(`${BASE}/generations/${encodeURIComponent(item.id)}/download?attachment=false`);
  // 空响应也可能是 2xx；必须确认有真实文件，不能保存零字节附件并显示下载成功。
  if (response.status !== 200) throw new ApiError(`文件下载未返回完整内容（${response.status}）`, response.status);
  const blob = await response.blob();
  if (!blob.size) throw new ApiError("文件下载内容为空，请重试下载", response.status);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = item.artifact?.name || item.name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 中文说明：该模块的分页用 limit/offset（不是 page/page_size），与前缀 /api/v1/documents 的接口约定一致
function paging(result, filters) {
  if (!result.total) return "";
  const start = result.offset + 1;
  const end = Math.min(result.total, result.offset + result.items.length);
  return `<div class="pagination"><span>${start}–${end} / ${result.total}</span><div>
    <button class="button small" type="button" data-doc-page="${Math.max(0, result.offset - result.limit)}" ${result.offset === 0 ? "disabled" : ""}>上一页</button>
    <button class="button small" type="button" data-doc-page="${result.offset + result.limit}" ${end >= result.total ? "disabled" : ""}>下一页</button>
  </div></div>`;
}

// 中文说明：绑定分页按钮（每页按钮自带目标 offset，点击即整页导航）。
function wirePaging(root, filters) {
  $$('[data-doc-page]', root).forEach(element => element.addEventListener("click", () =>
    navigate({ tab: "generations", ...filters, offset: element.dataset.docPage })));
}

// 中文说明：业务来源行（“类型:id”选择 + 可选字段）。名称前缀 source_/field_ 带序号，
// 提交时按行 DOM 顺序读取而不是按序号，因此增删行不会错位。
function sourceRow(resources, value = {}, index = 0) {
  const selected = value.type && value.id ? `${value.type}:${value.id}` : "";
  return `<div class="source-ref-row" data-document-source>
    <select name="source_${index}" aria-label="业务来源">${resourceSelectOptions(resources, selected, "选择资源")}</select>
    <input name="field_${index}" value="${esc(value.field || "")}" aria-label="来源字段" placeholder="可选：表名.字段名">
    <button class="icon-button" type="button" data-remove-source aria-label="移除来源">×</button>
  </div>`;
}

// 中文说明：从已保存的规范（spec）反向提取纯文本，用于“人工编写”表单回填（表格块除外）。
function textFromDocumentSpec(spec) {
  return (spec?.sections || []).flatMap(section => (section.blocks || [])
    .filter(block => block.type !== "table")
    .map(block => block.text || block.content || "")).filter(Boolean).join("\n");
}

// 中文说明：把表格规范还原成可编辑的制表符文本（首行表头，其余每行一条数据）。
function tableFromSpec(spec) {
  const columns = spec?.columns || [];
  const rows = spec?.rows || spec?.data || [];
  return [columns, ...rows.map(row => Array.isArray(row) ? row : columns.map(key => row[key] ?? ""))]
    .filter(row => row.length).map(row => row.join("\t")).join("\n");
}

// 中文说明：解析手工粘贴的表格文本：首行必须是表头，至少一行数据；
// 单元格短于表头时补空字符串（保证每行长度一致，否则后端列校验会失败）。
function parseTable(value) {
  const lines = String(value || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (lines.length < 2) throw new Error("表格至少需要表头和一行数据");
  const splitLine = line => line.includes("\t") ? line.split("\t") : line.split(/[，,]/);
  const columns = splitLine(lines[0]).map(value => value.trim());
  if (!columns.length || columns.some(value => !value)) throw new Error("表头不能为空");
  return { sheet_name: "数据", columns, rows: lines.slice(1).map(line => {
    const cells = splitLine(line).map(value => value.trim());
    return columns.map((_column, index) => cells[index] ?? "");
  }) };
}

// 中文说明：按输出格式把表单内容转成后端 spec：高级 JSON 优先；PPT 由分步流程生成（此处为空对象）；
// Excel 走表格解析；Word/PDF 按“每行一段”生成段落块。解析失败抛中文错误，在弹窗内提示。
function friendlySpec(form, outputType, name) {
  const advanced = String(form.get("advancedSpec") || "").trim();
  if (advanced) {
    try { return JSON.parse(advanced); } catch { throw new Error("高级内容不是有效 JSON"); }
  }
  if (outputType === "presentation") return {};
  if (outputType === "spreadsheet") return parseTable(form.get("tableText"));
  const lines = String(form.get("bodyText") || "").split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (!lines.length) throw new Error("请填写正文内容");
  return { title: name, sections: [{ title: "正文", blocks: lines.map(text => ({ type: "p", text })) }] };
}

/**
 * 中文说明：新建/修改生成草稿弹窗（本模块最复杂的表单）。
 * 参数：record=已有草稿（null 为新建）。
 * 行为：按“输出格式 × 生成方式”动态显隐字段（PPT 才有模板/页数；AI 才要提示词；人工才有正文/表格/分步说明）。
 * 副作用：POST /generations 或 PUT /generations/{id}（带 version），成功后跳转到该记录的详情页。
 * 权限：业务来源下拉依赖 assets.view（无权限时不加载资产目录，退化为只显示已保存的来源行）。
 */
async function generationDialog(record = null) {
  const [templates, catalog] = await Promise.all([
    get(BASE + "/templates"),
    can("assets.view") ? loadResourceCatalog(get).catch(() => ({ items: [] })) : Promise.resolve({ items: [] }),
  ]);
  const refs = record?.sourceRefs?.length ? record.sourceRefs : [{}];
  const resources = mergeResourceItems(catalog.items || [], record?.sourceRefs || []);
  let sourceIndex = refs.length;
  const title = record ? "修改生成草稿" : "新建生成任务";
  const currentType = record?.outputType || "document";
  const currentMode = record?.mode || "ai";
  openDialog(title, `<div class="form-grid document-generation-form">
    ${field("名称", input("name", record?.name, 'required maxlength="240"'))}
    ${field("输出格式", select("outputType", [
      { value: "document", label: "Word 文档" }, { value: "presentation", label: "PPT 演示文稿" },
      { value: "spreadsheet", label: "Excel 工作簿" }, { value: "pdf", label: "PDF 文档" },
    ], currentType, null))}
    ${field("生成方式", select("mode", [
      { value: "ai", label: "AI 生成" }, { value: "manual", label: "人工编写" },
    ], currentMode, null))}
    <label class="form-field" data-ppt-option><span>PPT 模板</span><select name="templateId"><option value="">请选择模板</option>${templates.items.map(item =>
      `<option value="${esc(item.id)}" ${item.id === record?.templateId ? "selected" : ""}>${esc(item.name + (item.source === "custom" ? " · 客户模板" : ""))}</option>`).join("")}</select></label>
    <label class="form-field" data-ppt-option><span>总页数</span><input name="pageCount" type="number" min="3" max="50" value="${esc(record?.requestedPageCount || 8)}"></label>
    ${field("生成要求", textarea("prompt", record?.prompt, 'rows="6" maxlength="20000" placeholder="说明用途、对象、重点和口径"'), "", true).replace('class="form-field wide"', 'class="form-field wide" data-ai-option')}
    ${field("正文（每行一段）", textarea("bodyText", textFromDocumentSpec(record?.spec), 'rows="7" placeholder="输入正文内容"'), "", true).replace('class="form-field wide"', 'class="form-field wide" data-document-option')}
    ${field("表格（首行为表头，制表符或逗号分隔）", textarea("tableText", tableFromSpec(record?.spec), 'rows="8" placeholder="月份\t收入\n一月\t120"'), "", true).replace('class="form-field wide"', 'class="form-field wide" data-sheet-option')}
    <div class="form-field wide document-stage-note" data-stage-option>保存后建立大纲，再按每批最多 4 页填写正文。</div>
    <details class="form-field wide document-advanced"><summary>高级内容 JSON</summary>${textarea("advancedSpec", record?.outputType === "presentation" && Object.keys(record?.spec || {}).length ? pretty(record.spec) : "", 'rows="12" spellcheck="false"')}</details>
    <details class="form-field wide document-sources"><summary>关联业务来源</summary><div class="source-ref-list" data-document-sources>${refs.map((value, index) => sourceRow(resources, value, index)).join("")}</div><button class="button small" type="button" data-add-document-source>添加来源</button></details>
  </div>`, {
    wide: true,
    saveLabel: record ? "保存修改" : "保存草稿",
    onReady: dialog => {
      // 中文说明：格式/方式联动显隐。每次切换都重算全部条件（不复用上一步结果），避免出现两个互斥区块同时可见
      const sync = () => {
        const type = $('[name="outputType"]', dialog).value;
        const mode = $('[name="mode"]', dialog).value;
        $$('[data-ppt-option]', dialog).forEach(element => { element.hidden = type !== "presentation"; });
        $('[data-ai-option]', dialog).hidden = mode !== "ai";
        $('[data-document-option]', dialog).hidden = mode !== "manual" || !["document", "pdf"].includes(type);
        $('[data-sheet-option]', dialog).hidden = mode !== "manual" || type !== "spreadsheet";
        $('[data-stage-option]', dialog).hidden = mode !== "manual" || type !== "presentation";
      };
      $('[name="outputType"]', dialog).addEventListener("change", sync);
      $('[name="mode"]', dialog).addEventListener("change", sync);
      sync();
      $('[data-add-document-source]', dialog).addEventListener("click", () =>
        $('[data-document-sources]', dialog).insertAdjacentHTML("beforeend", sourceRow(resources, {}, sourceIndex++)));
      dialog.addEventListener("click", event => {
        if (event.target.closest("[data-remove-source]")) event.target.closest("[data-document-source]").remove();
      });
    },
    onSave: async (form, dialog) => {
      const outputType = String(form.get("outputType"));
      const mode = String(form.get("mode"));
      const name = String(form.get("name") || "").trim();
      const sourceRefs = $$('[data-document-source]', dialog).map(row => {
        const identity = $("select", row).value;
        if (!identity) return null;
        // 选择值为 "type:id"：id 本身可能含冒号，故只按第一个冒号切分
        const splitAt = identity.indexOf(":");
        return { type: identity.slice(0, splitAt), id: identity.slice(splitAt + 1), field: $("input", row).value.trim() };
      }).filter(Boolean);
      const payload = {
        name, outputType, mode,
        // 两种方式互斥：AI 模式不提交手写内容，人工模式不提交提示词，避免后端看到矛盾的草稿
        prompt: mode === "ai" ? String(form.get("prompt") || "").trim() : "",
        spec: mode === "manual" ? friendlySpec(form, outputType, name) : {},
        templateId: outputType === "presentation" ? String(form.get("templateId") || "") : null,
        pageCount: outputType === "presentation" ? Number(form.get("pageCount")) : null,
        sourceRefs,
      };
      if (record) payload.version = record.version;
      const saved = record ? await put(`${BASE}/generations/${record.id}`, payload) : await post(BASE + "/generations", payload);
      toast("草稿已保存");
      navigate({ tab: "generations", id: saved.id });
    },
  });
}

// 中文说明：生成记录列表页（关键字/格式/状态筛选 + limit-offset 分页）。
// 副作用：GET /generations；重写 root 并绑定搜索、重置与分页（整页导航）。
async function renderGenerationList(root) {
  const params = query();
  const keyword = params.get("keyword") || "";
  const outputType = params.get("outputType") || "";
  const status = params.get("status") || "";
  const offset = Number(params.get("offset") || 0);
  const limit = Math.min(100, Math.max(10, Number(state.pageSize) || 20));
  const result = await get(`${BASE}/generations?keyword=${encodeURIComponent(keyword)}&outputType=${encodeURIComponent(outputType)}&status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`);
  const toolbar = `<form class="list-toolbar" data-document-search>
    <label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" aria-label="搜索生成记录" placeholder="名称或生成要求" value="${esc(keyword)}"></label>
    ${select("outputType", Object.entries(outputLabels).map(([value, label]) => ({ value, label })), outputType, "全部格式").replace("<select ", '<select aria-label="输出格式" ')}
    ${select("status", [
      { value: "draft", label: "草稿" }, { value: "running", label: "生成中" },
      { value: "completed", label: "已完成" }, { value: "failed", label: "失败" },
    ], status, "全部状态").replace("<select ", '<select aria-label="生成状态" ')}
    <button class="button" type="submit">查询</button>${keyword || outputType || status ? '<button class="button quiet" type="button" data-clear-documents>重置</button>' : ""}
  </form>`;
  const content = result.items.length ? dataTable(
    ["名称", "格式 / 方式", "模板 / 页数", "状态", "最后修改", "操作"], result.items,
    item => `<tr><td><a class="cell-primary" href="/documents?tab=generations&id=${esc(item.id)}">${esc(item.name)}</a>${item.artifact ? `<span class="cell-secondary">${esc(item.artifact.name)}</span>` : ""}</td>
      <td>${esc(item.outputTypeLabel)}<span class="cell-secondary">${esc(modeLabels[item.mode] || item.mode)}</span></td>
      <td>${item.templateId ? esc(item.templateId) : "—"}${item.requestedPageCount ? `<span class="cell-secondary">${item.actualPageCount || item.requestedPageCount} 页</span>` : ""}</td>
      <td>${statusBadge(item.status)}${item.stage !== item.status ? `<span class="cell-secondary">${esc(stageLabels[item.stage] || item.stage)}</span>` : ""}</td>
      <td>${date(item.updatedAt)}</td><td><a class="text-button" href="/documents?tab=generations&id=${esc(item.id)}">查看</a></td></tr>`,
  ) : emptyState("暂无生成记录", "新建任务后，草稿、生成过程和文件会保存在这里。",
    can("documents.create") ? '<button class="button primary" type="button" data-new-generation>新建任务</button>' : "");
  page(root, "generations", "文档与 PPT", "生成、预览和下载业务文件。",
    can("documents.create") ? button("新建任务", "data-new-generation", true) : "",
    `<section class="panel">${toolbar}${content}${paging(result, { keyword, outputType, status })}</section>`);
  $$('[data-new-generation]', root).forEach(element => element.addEventListener("click", () => generationDialog()));
  $('[data-document-search]', root).addEventListener("submit", event => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    navigate({ tab: "generations", keyword: form.get("keyword"), outputType: form.get("outputType"), status: form.get("status") });
  });
  $('[data-clear-documents]', root)?.addEventListener("click", () => navigate({ tab: "generations" }));
  wirePaging(root, { keyword, outputType, status });
}

// 中文说明：PPT 人工编写第一步——建立大纲（封面 + 正文页标题 + 结束页）。
// 正文页数量默认为总页数减 2（封面与结束页），提交时带 version。
function planDialog(item, reload) {
  const bodyCount = Math.max(1, Number(item.requestedPageCount || 3) - 2);
  openDialog("建立 PPT 大纲", `<div class="form-grid">
    ${field("标题", input("title", item.name, 'required maxlength="200"'))}
    ${field("副标题", input("subtitle", "", 'maxlength="500"'))}
    ${field("作者", input("author", state.me.user.display_name || "", 'maxlength="160"'))}
    ${field("页脚", input("footer", item.name, 'maxlength="160"'))}
    ${field(`正文页标题（建议 ${bodyCount} 行）`, textarea("outline", Array.from({ length: bodyCount }, (_value, index) => `第 ${index + 1} 部分`).join("\n"), 'required rows="10"'), "每行一个页面标题；总页数包含封面和结束页。", true)}
  </div>`, {
    saveLabel: "建立大纲",
    onSave: async form => {
      const slides = String(form.get("outline") || "").split(/\r?\n/).map(value => value.trim()).filter(Boolean)
        .map(title => ({ title, layout: "content" }));
      if (!slides.length) throw new Error("至少填写一个正文页标题");
      await post(`${BASE}/generations/${item.id}/presentation-plan`, {
        version: item.version, title: form.get("title"), subtitle: form.get("subtitle"),
        author: form.get("author"), footer: form.get("footer"), slides,
      });
      toast("PPT 大纲已建立"); await reload();
    },
  });
}

// 中文说明：PPT 人工编写第二步——按批填写正文页（每批最多 4 页，由后端返回的 missing_slide_numbers 决定）。
// 每页强制“核心结论 + 至少 3 条具体要点”，这是内容密度的既有校验，与后端渲染要求一致。
function slideBatchDialog(item, draft, reload) {
  const missing = (draft.missing_slide_numbers || []).slice(0, 4);
  const titles = new Map((draft.outline || []).map(value => [value.slide_number, value.title]));
  const editors = missing.map(number => `<section class="slide-editor" data-slide-editor="${number}">
    <div class="slide-editor-heading"><strong>第 ${number} 页</strong><span>${esc(titles.get(number) || "正文页")}</span></div>
    <div class="form-grid">
      ${field("标题", input("title_${number}", titles.get(number), 'required maxlength="180"'))}
      ${field("版式", select(`layout_${number}`, [{ value: "content", label: "要点" }, { value: "cards", label: "卡片" }], "content", null))}
      ${field("核心结论", textarea(`message_${number}`, "", 'required rows="2" maxlength="800"'), "", true)}
      ${field("具体要点（每行一项，至少 3 行）", textarea(`items_${number}`, "", 'required rows="5"'), "", true)}
    </div>
  </section>`).join("");
  openDialog("填写 PPT 正文", editors, {
    wide: true, saveLabel: "保存本批页面",
    onSave: async form => {
      const slides = missing.map(number => {
        const lines = String(form.get(`items_${number}`) || "").split(/\r?\n/).map(value => value.trim()).filter(Boolean);
        if (lines.length < 3) throw new Error(`第 ${number} 页至少填写 3 条具体要点`);
        return {
          slide_number: number, title: form.get(`title_${number}`), layout: form.get(`layout_${number}`),
          key_message: form.get(`message_${number}`),
          items: lines.map((description, index) => ({ title: `要点 ${index + 1}`, description })),
        };
      });
      await post(`${BASE}/generations/${item.id}/presentation-slides`, { version: item.version, slides });
      toast("页面内容已保存"); await reload();
    },
  });
}

/**
 * 中文说明：生成详情页（?tab=generations&id=...）：并行取记录与过程事件，PPT 人工草稿额外取草稿进度。
 * 按下发状态组合操作按钮（修改/开始生成/下载/复制/归档），并处理大纲建立与分批填写。
 * 副作用：GET 多个接口；执行生成会真实触发后端渲染并产生文件；归档只做逻辑归档。
 * 失败表现：加载失败由 main.js 展示错误页；执行失败 toast 后仍重新加载以拿到最新阶段与错误信息。
 */
async function renderGenerationDetail(root, id) {
  const [item, events] = await Promise.all([
    get(`${BASE}/generations/${encodeURIComponent(id)}`),
    get(`${BASE}/generations/${encodeURIComponent(id)}/events`),
  ]);
  let draft = null;
  if (item.outputType === "presentation" && item.mode === "manual" && item.status === "draft") {
    draft = await get(`${BASE}/generations/${encodeURIComponent(id)}/presentation-draft`);
  }
  const reload = () => renderGenerationDetail(root, id);
  const actions = [
    '<a class="button" href="/documents?tab=generations">返回列表</a>',
    can("documents.edit") && ["draft", "failed"].includes(item.status) ? button("修改", "data-edit-generation") : "",
    can("documents.execute") && ["draft", "failed"].includes(item.status) && !(draft?.exists && draft.missing_slide_numbers?.length)
      ? button("开始生成", "data-execute-generation", true) : "",
    item.status === "completed" && can("documents.export") ? button("下载", "data-download-generation", true) : "",
    can("documents.create") ? button("复制", "data-copy-generation") : "",
    can("documents.delete") && item.status !== "running" ? button("归档", "data-archive-generation") : "",
  ].filter(Boolean).join("");
  const sourceMarkup = item.sources.length ? `<div class="document-source-links">${item.sources.map(source =>
    `<a href="${esc(source.openPath || "/assets-catalog")}">${esc(source.name)}${source.field ? `<small>${esc(source.field)}</small>` : ""}</a>`).join("")}</div>` : "—";
  const draftMarkup = draft ? `<section class="document-draft-bar">
    <div><strong>${draft.exists ? `已写 ${draft.written_count} / ${draft.body_page_count} 页` : "尚未建立大纲"}</strong>${draft.exists && draft.missing_slide_numbers.length ? `<span>待填写：${draft.missing_slide_numbers.join("、")}</span>` : ""}</div>
    ${!draft.exists && can("documents.edit") ? button("建立大纲", "data-create-plan") : ""}
    ${draft.exists && draft.missing_slide_numbers.length && can("documents.edit") ? button("填写下一批", "data-write-slides", true) : ""}
  </section>` : "";
  const preview = item.status === "completed" ? `<figure class="document-preview"><img alt="${esc(item.name)}预览" data-generation-preview><figcaption>${esc(item.artifact?.name || item.name)}</figcaption></figure>` : "";
  const details = `<dl class="detail-grid catalog-detail-grid">
    <div><dt>格式</dt><dd>${esc(item.outputTypeLabel)}</dd></div><div><dt>生成方式</dt><dd>${esc(modeLabels[item.mode] || item.mode)}</dd></div>
    <div><dt>状态</dt><dd>${statusBadge(item.status)}</dd></div><div><dt>当前阶段</dt><dd>${esc(stageLabels[item.stage] || item.stage)}</dd></div>
    ${item.templateId ? `<div><dt>PPT 模板</dt><dd>${esc(item.templateId)}</dd></div>` : ""}
    ${item.requestedPageCount ? `<div><dt>页数</dt><dd>${item.actualPageCount || "—"} / ${item.requestedPageCount}</dd></div>` : ""}
    ${item.runId ? `<div><dt>运行标识</dt><dd><code>${esc(item.runId)}</code></dd></div>` : ""}
    <div class="wide"><dt>业务来源</dt><dd>${sourceMarkup}${item.restrictedSourceCount ? `<span class="muted">另有 ${item.restrictedSourceCount} 项当前无权查看</span>` : ""}</dd></div>
  </dl>`;
  const history = events.length ? `<details class="resource-history"><summary>生成过程（${events.length}）</summary><div class="compact-history">${events.map(event =>
    `<div><strong>${esc(stageLabels[event.stage] || event.stage)}</strong><span>${esc(event.message)}</span><time>${date(event.createdAt)}</time></div>`).join("")}</div></details>` : "";
  const specification = `<details class="resource-history"><summary>结构化内容</summary><div class="compact-details"><pre class="json-preview">${esc(pretty(item.spec))}</pre></div></details>`;
  page(root, "generations", item.name, `${item.outputTypeLabel} · ${modeLabels[item.mode] || item.mode}`, actions,
    `${item.errorMessage ? `<p class="notice-inline error-notice" role="alert">${esc(item.errorMessage)}</p>` : ""}${draftMarkup}<section class="panel catalog-detail-panel">${details}${preview}</section>${history}${specification}`);
  if (item.status === "completed") loadPreview($("[data-generation-preview]", root), `${BASE}/generations/${item.id}/preview`);
  $("[data-edit-generation]", root)?.addEventListener("click", () => generationDialog(item));
  $("[data-create-plan]", root)?.addEventListener("click", () => planDialog(item, reload));
  $("[data-write-slides]", root)?.addEventListener("click", () => slideBatchDialog(item, draft, reload));
  $("[data-download-generation]", root)?.addEventListener("click", async event => {
    // 事件分发结束后 currentTarget 会清空，下载跨 await 时保留当前按钮引用。
    const element = event.currentTarget;
    element.disabled = true;
    try { await downloadGeneration(item); } catch (error) { toast(error.message, true); }
    finally { element.disabled = false; }
  });
  $("[data-execute-generation]", root)?.addEventListener("click", async event => {
    const element = event.currentTarget; element.disabled = true; element.textContent = "正在生成…";
    try { await post(`${BASE}/generations/${item.id}/execute`, { version: item.version }); toast("文件已生成"); await reload(); }
    catch (error) { toast(error.message, true); await reload(); }
  });
  $("[data-copy-generation]", root)?.addEventListener("click", () => openDialog("复制为新草稿",
    field("新名称", input("name", `${item.name}（副本）`, 'required maxlength="240"')), {
      saveLabel: "复制", onSave: async form => {
        const copied = await post(`${BASE}/generations/${item.id}/copy`, { name: form.get("name") });
        toast("已复制为新草稿"); navigate({ tab: "generations", id: copied.id });
      },
    }));
  $("[data-archive-generation]", root)?.addEventListener("click", () => confirmAction(
    "归档生成记录", `确认归档“${item.name}”？已生成文件和追溯记录会保留。`, async () => {
      await remove(`${BASE}/generations/${item.id}?version=${item.version}`); toast("已归档"); navigate({ tab: "generations" });
    }, "归档",
  ));
}

// 中文说明：上传客户 PPTX 模板（multipart）。返回体同样带 success 包装，失败时抛出后端消息。
// 注意：模板解析由后端完成，上传成功后需要 reload 才能看到新模板及其配色预览。
async function uploadTemplateDialog(reload) {
  openDialog("上传客户 PPT 模板", `<div class="form-grid">
    ${field("模板名称", input("name", "", 'maxlength="100" placeholder="默认使用文件名"'))}
    ${field("PPTX 文件", '<input name="file" type="file" accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation" required>')}
  </div>`, {
    saveLabel: "上传并解析",
    onSave: async form => {
      const file = form.get("file");
      if (!(file instanceof File) || !file.size) throw new Error("请选择 PPTX 文件");
      const payload = new FormData(); payload.set("name", form.get("name") || ""); payload.set("file", file);
      const response = await fetchAuthorized(BASE + "/templates", { method: "POST", body: payload });
      const value = await response.json();
      if (!value.success) throw new Error(value.message || "模板上传失败");
      toast("客户模板已上传"); await reload();
    },
  });
}

// 中文说明：模板缩略图：内置模板有真实预览图，客户模板只有配色信息，用色块占位（避免空白卡片）。
function templateThumb(item) {
  const preview = item.preview || {};
  if (item.source === "builtin" && preview.image_url) return `<img src="${esc(preview.image_url)}" alt="" loading="lazy">`;
  return `<span class="template-color-preview" style="--template-bg:${esc(preview.background || "#f4f6f8")};--template-accent:${esc(preview.accent || "#2364c6")}"><i></i></span>`;
}

// 中文说明：模板详情弹窗（含大图预览）。移除客户模板前先 close() 当前弹窗再走统一确认弹窗，
// 因为 openDialog 是单例；内置模板没有移除入口。
async function templateDetailDialog(item, reload) {
  const recommendations = (item.recommended_for || []).join("、");
  const dialog = openDialog(item.name, `<div class="template-dialog-preview"><img alt="${esc(item.name)}模板预览" data-template-preview></div>
    <dl class="detail-grid"><div><dt>来源</dt><dd>${item.source === "custom" ? "客户模板" : "内置模板"}</dd></div><div><dt>分类</dt><dd>${esc(item.category || "—")}</dd></div>${recommendations ? `<div class="wide"><dt>适用场景</dt><dd>${esc(recommendations)}</dd></div>` : ""}</dl>
    ${item.source === "custom" && can("documents.delete") ? '<button class="button danger" type="button" data-delete-template>移除模板</button>' : ""}`, {
      wide: true,
      onReady: current => loadPreview($("[data-template-preview]", current), `${BASE}/templates/${encodeURIComponent(item.id)}/preview`),
    });
  $("[data-delete-template]", dialog)?.addEventListener("click", () => {
    dialog.close();
    confirmAction("移除客户模板", `确认移除“${item.name}”？`, async () => {
      await remove(`${BASE}/templates/${encodeURIComponent(item.id)}`); toast("客户模板已移除"); await reload();
    }, "移除");
  });
}

// 中文说明：PPT 模板库视图（内置 + 客户模板）。业务约定：创建 PPT 时必须手动选择模板，系统不设默认模板。
async function renderTemplates(root) {
  const result = await get(BASE + "/templates");
  const content = result.items.length ? `<section class="document-template-grid">${result.items.map(item =>
    `<button class="document-template-item" type="button" data-template-id="${esc(item.id)}">${templateThumb(item)}<strong>${esc(item.name)}</strong><small>${esc(item.source === "custom" ? "客户模板" : item.category || "内置模板")}</small></button>`).join("")}</section>`
    : emptyState("暂无可用模板", "上传客户 PPTX 模板后可在生成任务中手动选择。");
  page(root, "templates", "PPT 模板", "创建 PPT 时必须手动选择，系统不设置默认模板。",
    can("documents.create") ? button("上传客户模板", "data-upload-template", true) : "", content);
  const byId = new Map(result.items.map(item => [item.id, item]));
  $$('[data-template-id]', root).forEach(element => element.addEventListener("click", () =>
    templateDetailDialog(byId.get(element.dataset.templateId), () => renderTemplates(root))));
  $("[data-upload-template]", root)?.addEventListener("click", () => uploadTemplateDialog(() => renderTemplates(root)));
}

/**
 * 中文说明：文档与 PPT 页入口，按 ?tab 与 ?id 分发到列表/详情/模板视图。
 * 副作用：由各子视图完成，本函数只做分发；默认落在“生成记录”标签。
 */
export async function renderDocuments(root) {
  const params = query();
  const tab = params.get("tab") || "generations";
  if (tab === "generations" && params.get("id")) return renderGenerationDetail(root, params.get("id"));
  if (tab === "templates") return renderTemplates(root);
  return renderGenerationList(root);
}
