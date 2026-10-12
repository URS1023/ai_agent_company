// ============================================================
// 智慧云脑 Cloud Brain — AI 工作台
// ============================================================
// 旧版前端外壳的唯一脚本：apps/web/*.html 均通过 <script src="/app.js"> 直接引入，无打包器。
// 页面归属由 <body data-page="..."> 决定（chat / knowledge / plugins / skills / connectors /
// datasources / files / runs / admin*），见下方的 PAGES 与 renderPage。
// 依赖：后端 FastAPI 的 /api/**（需服务在跑）；登录令牌写 localStorage("agent_token")，
// 当前工作区写 localStorage("agent_workspace_id")，所有请求统一走 API.req。
// 影响范围：只操作 DOM 与调用后端接口，自身不读写数据库；但提交消息/删除会话等操作会在后端落库。
// 约束：所有函数都是全局函数，会被各 HTML 的内联 onclick / addEventListener 直接引用，改名会破坏页面绑定。
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.prototype.slice.call(document.querySelectorAll(s));

const IC = {
  chat: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M21 12a8 8 0 0 1-8 8H4l2-3.5A8 8 0 1 1 21 12z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  kb: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4H6.5A2.5 2.5 0 0 0 4 6.5v13z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" stroke="currentColor" stroke-width="1.6"/></svg>',
  plugin: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M13 2 3 14h6v8l10-12h-6l2-8h-2z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  skill: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.1 2.1-3-3 2.1-2.1z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  agent: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="4" y="7" width="16" height="12" rx="3" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  connector: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M9 12h6m-7 7a4 4 0 0 1 0-8h2m6 8a4 4 0 0 0 0-8h-2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  ds: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="5" rx="8" ry="3" stroke="currentColor" stroke-width="1.6"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" stroke="currentColor" stroke-width="1.6"/></svg>',
  files: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-7-6z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M13 3v6h6" stroke="currentColor" stroke-width="1.6"/></svg>',
  runs: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M4 4h16v4H4zM4 10h16v4H4zM4 16h16v4H4z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  model: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="4" y="5" width="16" height="14" rx="3" stroke="currentColor" stroke-width="1.6"/><path d="M8 9h8M8 13h5M7 2v3m10-3v3M7 19v3m10-3v3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  lock: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="5" y="10" width="14" height="11" rx="2.5" stroke="currentColor" stroke-width="1.6"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  eye: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="2.8" stroke="currentColor" stroke-width="1.6"/></svg>',
  vector: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="6" cy="12" r="2" stroke="currentColor" stroke-width="1.6"/><circle cx="18" cy="6" r="2" stroke="currentColor" stroke-width="1.6"/><circle cx="18" cy="18" r="2" stroke="currentColor" stroke-width="1.6"/><path d="m8 11 8-4m-8 6 8 4" stroke="currentColor" stroke-width="1.6"/></svg>',
  plus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  send: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  search: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="m20 20-3.2-3.2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  download: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  preview: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/></svg>',
  close: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  upload: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 15V4m0 0L8 8m4-4 4 4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  admin: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v3m0 12v3M3 12h3m12 0h3M6 6l2 2m8 8 2 2m0-12-2 2m-8 8-2 2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  library: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5Z" stroke="currentColor" stroke-width="1.7"/><path d="M4 5.5v16" stroke="currentColor" stroke-width="1.7"/></svg>',
  image: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="18" height="16" rx="3" stroke="currentColor" stroke-width="1.7"/><circle cx="9" cy="10" r="1.5" stroke="currentColor" stroke-width="1.7"/><path d="m5 18 5-5 3 3 3-3 3 3" stroke="currentColor" stroke-width="1.7"/></svg>',
  globe: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.7"/><path d="M3 12h18M12 3c2.4 2.5 3.6 5.5 3.6 9S14.4 18.5 12 21c-2.4-2.5-3.6-5.5-3.6-9S9.6 5.5 12 3Z" stroke="currentColor" stroke-width="1.7"/></svg>',
  research: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 3h12v18l-6-3-6 3Z" stroke="currentColor" stroke-width="1.7"/><path d="M9 8h6M9 12h4" stroke="currentColor" stroke-width="1.7"/></svg>',
  copy: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><rect x="8" y="8" width="11" height="11" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" stroke="currentColor" stroke-width="1.7"/></svg>',
  retry: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-2.3 5.7" stroke="currentColor" stroke-width="1.7"/><path d="M20 4v7h-7" stroke="currentColor" stroke-width="1.7"/></svg>',
  thumb: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M7 10v11H3V10h4Zm0 9h10.5a2 2 0 0 0 2-1.6l1.2-6A2 2 0 0 0 18.7 9H14l.7-3.4A2.1 2.1 0 0 0 12.6 3L7 10" stroke="currentColor" stroke-width="1.7"/></svg>'
};
const NL = String.fromCharCode(10);
/** 按产物类型返回带底色圆角的 SVG 图标 HTML（类型取自 normalizeArtifact/inferTypeFromName）。 */
function artIcon(type) {
  const map = { document: "doc", presentation: "ppt", spreadsheet: "xls", pdf: "pdf", image: "img", chart: "img", json: "json", csv: "csv", code: "code" };
  const k = map[type] || "file";
  const colors = { doc: "#2f6fe8", ppt: "#e8590c", xls: "#109c5c", pdf: "#d63031", img: "#9b59b6", json: "#8d6e63", csv: "#5f9ea0", code: "#555", file: "#888" };
  const paths = {
    doc: '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M13 3v6h6" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>',
    ppt: '<rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M3 8.5h3.5L9 13l3-5 3 6 3-4H21" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    xls: '<rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="1.7"/><path d="M8 8v8m0-8 4 8-4-8zm0 0v8M8 12h4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    pdf: '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M13 3v6h6" stroke="currentColor" stroke-width="1.7"/><path d="M11 12v6m-3-3h6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    img: '<rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="1.7"/><circle cx="9" cy="10" r="1.6" stroke="currentColor" stroke-width="1.6"/><path d="m6 18 5-5 3 3 4-4 3 3" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
    json: '<path d="M8 7 5 12l3 5M16 7l3 5-3 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    csv: '<path d="M5 6h14M5 12h14M5 18h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    code: '<path d="m9 7-5 5 5 5m6-10 5 5-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    file: '<path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M13 3v6h6" stroke="currentColor" stroke-width="1.7"/>'
  };
  return '<span class="artifact-icon" style="background:' + colors[k] + ';"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" style="color:#fff">' + paths[k] + '</svg></span>';
}
const TYPE_LABEL = { document: "文档 DOCX", presentation: "演示 PPTX", spreadsheet: "表格 XLSX", pdf: "PDF", image: "图片", chart: "图表", json: "JSON", csv: "CSV", code: "代码" };
/** 由文件名后缀推断产物类型；识别不到时返回 "document"，保证卡片始终有图标与文案。 */
function inferTypeFromName(name) {
  const n = String(name || "").toLowerCase();
  const m = { ".docx": "document", ".doc": "document", ".pptx": "presentation", ".ppt": "presentation", ".xlsx": "spreadsheet", ".xls": "spreadsheet", ".csv": "csv", ".pdf": "pdf", ".json": "json", ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".svg": "image" };
  for (const k in m) if (n.indexOf(k) >= 0) return m[k];
  return "document";
}

const API = {
  token: localStorage.getItem("agent_token") || "",
  async req(method, path, body, isForm, options = {}) {
    const { withRequestDeadline } = await import("/assets/platform/request-deadline.js");
    return withRequestDeadline(async signal => {
      const headers = {};
      if (this.token) headers["Authorization"] = "Bearer " + this.token;
      const workspaceId = localStorage.getItem("agent_workspace_id");
      if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
      if (body && !isForm) headers["Content-Type"] = "application/json";
      const opts = { method, headers, signal };
      if (body) opts.body = isForm ? body : JSON.stringify(body);
      const resp = await fetch(path, opts);
      const ct = resp.headers.get("content-type") || "";
      const data = ct.includes("application/json") ? await resp.json() : await resp.text();
      if (!resp.ok) {
        const error = new Error((data && data.message) || (data && data.detail) || "请求失败 " + resp.status);
        error.status = resp.status;
        throw error;
      }
      return data;
    }, { timeoutMs: method === "GET" ? 60000 : 300000, ...options, method });
  },
  get(p, options) { return this.req("GET", p, undefined, false, options); },
  post(p, b, f, options) { return this.req("POST", p, b, f, options); },
  del(p, options) { return this.req("DELETE", p, undefined, false, options); }
};

const state = {
  chatModels: [], modelProfile: "reasoning", modelsReady: false, modelsError: "",
  me: null, conversations: [], current: null, businessModules: [], openingChat: false,
  fileIds: [], kbIds: [], datasourceIds: [], dataSources: [],
  dashboardTemplates: [], dashboardTemplateMode: "auto", dashboardTemplateId: null,
  dashboardTemplateDraftMode: "auto", dashboardTemplateDraftId: null,
  dashboardTemplateQuery: "", dashboardTemplateCategory: "全部",
  pluginId: null, pluginVersionId: null, streaming: false,
  knowledge: [], plugins: [], fileIndex: {}, templates: [], templatesError: "",
  knowledgeCurrentId: null, knowledgeTab: "documents", knowledgePage: 1, knowledgeLoadRevision: 0, knowledgeSearchResults: null,
  knowledgeSearchQuery: "", knowledgeSearchMode: "hybrid", knowledgeSearchTopK: 6,
  pptActive: false, pptTemplateId: null, pptTemplateDraft: null,
  pptRatio: "16:9", pptPageCount: "auto", pptTemplateQuery: "",
  pptTemplateCategory: "全部", pptTemplateSource: "all", activeMode: null,
  abortController: null, lastPrompt: "", businessActions: []
};
const PAGES = ["chat", "knowledge", "plugins", "skills", "connectors", "datasources", "files", "runs", "admin", "admin-members", "admin-audit", "admin-secrets", "admin-models", "admin-ops"];

/** 顶部轻提示：写入 #toast 并 2.6 秒后自动隐藏（重复调用会重置计时器，避免提示闪断）。 */
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 2600); }
/** HTML 转义；任何拼进 innerHTML 的用户/模型文本都必须先过这个函数（XSS 唯一防线，勿绕过）。 */
function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
const PLACEHOLDER_CONVERSATION_TITLES = new Set(["", "新对话", "新聊天", "AI 对话"]);
const conversationSummaryCache = new Map();
const conversationSummaryLoads = new Set();
let dshChat = null;

/** 按宿主真实能力启用会话数据层；旧业务页面不依赖 DSH 组件。 */
async function loadChatCapabilities() {
  if (!window.DshChatView) return;
  try {
    const capabilities = await API.get("/api/chat/capabilities");
    if (capabilities.engine !== "dsh" || capabilities.protocol_version < 2) return;
    dshChat = window.DshChatView.create({ state, API, $, esc, toast, renderMessages, renderAttachChips,
      renderChatModels, authUrl, openListMenu, positionPanel, useArtifactAsInput, toolLabel,
      queueSubmit: () => sendDshMessage("queue") });
    dshChat.setCapabilities(capabilities);
  } catch (error) {
    if (error.status !== 404) toast("暂时无法读取对话能力，请刷新后重试");
  }
}

/** 发送完整企业选择上下文；DSH 受理与长连接由独立控制器处理。 */
async function sendDshMessage(deliveryMode = null) {
  if (state.openingChat) return toast("正在打开对话，请稍候…");
  const textarea = $("#prompt-textarea"), content = textarea.value.trim();
  if (!content && !state.fileIds.length) { if (state.streaming) return dshChat.cancel(); return; }
  if (!state.modelsReady || !state.chatModels.some(item => item.profile_name === state.modelProfile)) return toast("请选择一个可用的对话模型");
  if (!state.current) await newChat({ preserveComposerContext: true });
  if (!state.current) return;
  const text = content || "请分析我上传的文件。";
  const payload = { request_id: "web-" + Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join(""),
    content: text, ...(deliveryMode ? { delivery_mode: deliveryMode } : {}), model_profile: state.modelProfile, file_ids: state.fileIds.map(file => file.id),
    knowledge_base_ids: state.kbIds, datasource_ids: state.datasourceIds, plugin_version_id: state.pluginVersionId,
    template_id: state.pptActive ? state.pptTemplateId : null, dashboard_template_mode: state.dashboardTemplateMode,
    dashboard_template_id: state.dashboardTemplateMode === "manual" ? state.dashboardTemplateId : null,
    plugin_inputs: state.pptActive ? { template_id: state.pptTemplateId, aspect_ratio: state.pptRatio, page_count: state.pptPageCount } : {} };
  if (PLACEHOLDER_CONVERSATION_TITLES.has(String(state.current.title || "").trim())) payload.title = firstSentenceSummary(text);
  await dshChat.send(payload);
  loadConversations().catch(() => {});
}
/**
 * 取文本首个完整句作为会话摘要/标题。
 * @param {string} value 原始文本（可含 markdown 前缀与换行）
 * @param {number} maxLength 最大字符数，超出以省略号截断（按码点计数，避免截断代理对）
 * @returns {string} 摘要；无有效内容时返回「新对话」
 */
function firstSentenceSummary(value, maxLength = 42) {
  const line = String(value || "").replace(/\r/g, "").split("\n").map((item) => item.trim()).find(Boolean) || "";
  const cleaned = line.replace(/^\s*(?:#{1,6}|[-*>]|\d+[.)、])\s*/, "").replace(/\s+/g, " ").trim();
  const boundary = cleaned.search(/[。！？!?；;]/);
  const sentence = (boundary >= 0 ? cleaned.slice(0, boundary) : cleaned).trim();
  if (!sentence) return "新对话";
  const characters = Array.from(sentence);
  return characters.length > maxLength ? characters.slice(0, maxLength).join("") + "…" : sentence;
}
/** 会话列表标题：优先用后端 summary，缺失时回退 title（两者都为空则显示「新对话」）。 */
function conversationDisplayTitle(conversation) {
  return firstSentenceSummary(conversation?.summary || conversation?.title || "新对话");
}
/** 兼容后端两种响应形态：直接返回业务体，或包一层/两层 { data: ... } 信封。 */
function envelopeData(response) {
  let payload = response;
  for (let index = 0; index < 2; index += 1) {
    if (payload && typeof payload === "object" && !Array.isArray(payload) && Object.prototype.hasOwnProperty.call(payload, "data") && payload.data != null) payload = payload.data;
    else break;
  }
  return payload;
}
/**
 * 解析数据目录响应，识别「返回条数与实际条目不一致」的不完整响应。
 * @param {object} response 后端数据源目录响应
 * @param {boolean} strict 为 true 时把不完整响应直接抛错（用于必须拿到完整目录的场景）
 * @returns {{payload: object, sources: Array, count: number, incomplete: boolean}}
 */
function businessSourceCatalog(response, strict = false) {
  const payload = envelopeData(response) || {};
  const sources = Array.isArray(payload.sources) ? payload.sources : [];
  const reportedCount = Number(payload.returnedCount == null ? sources.length : payload.returnedCount);
  const incomplete = reportedCount > 0 && !sources.length;
  if (strict && incomplete) throw new Error("数据目录返回结构不完整，请刷新后重试");
  return { payload, sources, count: sources.length, incomplete };
}
/**
 * 从沙箱内部路径（/mnt/data/...、/tmp/...）或 URL 中取出文件名。
 * 模型常在 markdown 里直接写沙箱路径，这里统一还原成可匹配产物清单的名字；非沙箱路径返回 ""。
 */
function sandboxArtifactName(raw) {
  let value = String(raw || "").trim().replace(/^<|>$/g, "").split("?", 1)[0].split("#", 1)[0].replace(/\\/g, "/");
  try { value = decodeURIComponent(value); } catch (error) {}
  const lowered = value.toLowerCase();
  for (const marker of ["/mnt/data/", "/tmp/"]) {
    const index = lowered.indexOf(marker);
    if (index >= 0) return value.slice(index + marker.length).replace(/^\/+/, "");
  }
  return "";
}
/** 归一化产物对象：补齐 type 与 download_url/preview_url（图片类才给预览地址）；无 id 视为无效返回 null。 */
function normalizeArtifact(raw) {
  const artifact = raw && typeof raw === "object" ? raw : {};
  const id = String(artifact.id || "");
  if (!id) return null;
  const name = String(artifact.name || "生成文件");
  const type = artifact.type || inferTypeFromName(name);
  const image = type === "image" || type === "chart" || String(artifact.mime_type || "").startsWith("image/");
  return {
    ...artifact, id, name, type,
    download_url: artifact.download_url || "/api/artifacts/" + id + "/download",
    preview_url: artifact.preview_url || (image ? "/api/artifacts/" + id + "/content" : "")
  };
}
/** 从 /api/artifacts/<id>/content|download 形式的地址中取产物 id，取不到返回 ""。 */
function artifactIdFromUrl(raw) {
  const match = String(raw || "").match(/\/api\/artifacts\/([^/?#]+)\/(?:content|download)/i);
  return match ? match[1] : "";
}
/**
 * 把 markdown 中的图片/链接地址匹配回产物清单。
 * 先按产物 id 精确匹配，再按沙箱文件名（含 basename）匹配；
 * 同名多义时返回 null，宁可显示「附件不可用」也不猜错产物。
 */
function artifactForMarkdownUrl(raw, artifacts) {
  const id = artifactIdFromUrl(raw);
  if (id) return (artifacts || []).find((artifact) => artifact.id === id) || null;
  const internal = sandboxArtifactName(raw).toLowerCase();
  if (!internal) return null;
  const basename = internal.split("/").pop();
  const matches = (artifacts || []).filter((artifact) => {
    const name = String(artifact.name || "").replace(/\\/g, "/").toLowerCase();
    return name === internal || name.split("/").pop() === basename;
  });
  return matches.length === 1 ? matches[0] : null;
}
/** 链接白名单：仅放行 http(s) 与站内相对路径；沙箱路径与其它协议一律降级为 "#"（防注入/防误跳转）。 */
function safeHref(raw) {
  const value = String(raw || "").trim();
  if (sandboxArtifactName(value)) return "#";
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith("/")) return authUrl(value);
  return "#";
}
/** 取某页面视图的根容器（HTML 约定 id 为 content-<view>）。 */
function contentEl(view) { return document.getElementById("content-" + view); }
/** 把 IC 中的 17px 图标缩到指定尺寸（依赖 IC 里写死的 width="17"/height="17" 字面量）。 */
function iconSmall(ic, px) { return ic.replace('width="17"', 'width="' + (px || 15) + '"').replace('height="17"', 'height="' + (px || 15) + '"'); }

// 极简 markdown（无复杂转义；用字符码构建分隔符）
/**
 * 渲染助手回复的 markdown 为 HTML（标题/列表/表格/代码块/图片/链接）。
 * @param {string} src 模型原文（流式过程中可能是半截文本）
 * @param {Array} artifacts 本轮产物清单，用于把图片地址换成本地可鉴权的预览/下载地址
 * @param {Set} renderedArtifactIds 命中产物的 id 会写回该集合，供上方产物卡片去重
 * @returns {string} 完整 HTML 片段
 * 约束：全部文本先 esc 再拼装；strip 掉 followups 注释尾注与 emoji（UI 规范不展示 emoji）。
 */
function mdRender(src, artifacts, renderedArtifactIds) {
  if (!src) return "";
  // Hide the model's suggestion footer, including partial prefixes during streaming.
  src = String(src).replace(/(?:^|\n)<!-- followups:[\s\S]*$/, "");
  const footerStart = "<!-- followups:";
  const lastLine = src.slice(src.lastIndexOf("\n") + 1);
  if (lastLine && footerStart.startsWith(lastLine)) src = src.slice(0, -lastLine.length);
  const BT = String.fromCharCode(96);
  const fence = BT + BT + BT;
  const MARK = String.fromCharCode(2) + "B";
  let s = esc(src);
  // strip emoji (UI 规范：内容不展示 emoji)
  s = s.replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2300}-\u{23FF}\u{FE0F}]/gu, "");
  const blocks = [];
  const segs = s.split(fence);
  let html = "";
  segs.forEach((seg, i) => {
    if (i % 2 === 1) {
      let code = seg;
      const n = code.indexOf(NL);
      if (n >= 0) { const first = code.slice(0, n).trim(); if (/^[A-Za-z0-9_-]+$/.test(first)) code = code.slice(n + 1); }
      blocks.push("<div class='code-block'>" + code + "</div>");
      html += MARK + (blocks.length - 1);
    } else { html += seg; }
  });

  const inline = (txt) => {
    const codes = [];
    let t = String(txt)
      .replace(/`([^`\n]+)`/g, (_, code) => { codes.push("<code>" + code + "</code>"); return "\u0002I" + (codes.length - 1) + "\u0003"; })
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\|([^|]+)\|/g, "<em>$1</em>");
    t = t.replace(/!\[([^\[\]]*)\]\(([^\(\)]+)\)/g, (m, label, rawUrl) => {
      const artifact = artifactForMarkdownUrl(rawUrl, artifacts);
      let source = "", download = "", name = label || "生成图片", artifactId = "";
      if (artifact) {
        source = authUrl(artifact.preview_url || ("/api/artifacts/" + artifact.id + "/content"));
        download = authUrl(artifact.download_url || ("/api/artifacts/" + artifact.id + "/download"));
        name = artifact.name || name;
        artifactId = artifact.id;
        if (renderedArtifactIds) renderedArtifactIds.add(artifact.id);
      } else if (!sandboxArtifactName(rawUrl)) {
        const safe = safeHref(rawUrl);
        if (safe !== "#") source = download = safe;
      }
      if (!source) {
        const missingName = sandboxArtifactName(rawUrl) || name;
        return "<span class='md-image-missing'>图片附件不可用：" + esc(missingName) + "</span>";
      }
      return "<figure class='md-image-figure'" + (artifactId ? " data-artifact-id='" + esc(artifactId) + "'" : "") + ">" +
        "<a class='md-image-open' href='" + source + "' target='_blank' rel='noopener'>" +
        "<img class='md-artifact-image' data-artifact-image " + artifactImageSource(source) + " alt='" + esc(label || name) + "' loading='lazy' decoding='async'></a>" +
        "<figcaption><span>" + esc(name) + "</span><a href='" + download + "' target='_blank' rel='noopener'>下载原图</a></figcaption></figure>";
    });
    t = t.replace(/\[([^\[\]]+)\]\(([^\(\)]+)\)/g, (m, x, u) => {
      const href = safeHref(u);
      if (href === "#" && sandboxArtifactName(u)) return "<span class='md-link-unavailable'>" + x + "（附件不可用）</span>";
      if (artifactIdFromUrl(u) && (/^\/?api\/artifacts\//.test(x) || x === u)) x = "下载文件";
      return "<a class='md-link' href='" + href + "' target='_blank' rel='noopener'>" + x + "</a>";
    });
    return t.replace(/\u0002I(\d+)\u0003/g, (_, index) => codes[Number(index)] || "");
  };
  const restoreBlocks = (line) => line.replace(new RegExp(MARK + "\\d+", "g"), (m) => {
    const idx = parseInt(m.slice(MARK.length), 10);
    return blocks[idx] != null ? blocks[idx] : "";
  });

  const lines = html.split(NL);
  const out = [];
  let inList = null;
  let tableRows = [];
  const flushTable = () => {
    if (!tableRows.length) return;
    // 仅把明确的交付清单转成文件卡；保留其它业务表格及清单里的页数、数据范围等说明。
    const headers = tableRows[0];
    const deliveryHeader = headers.some(cell => /文件|附件|交付/.test(cell)) && headers.some(cell => /下载|链接|地址/.test(cell));
    const fileRows = deliveryHeader && tableRows.length > 1 ? tableRows.slice(1).map(cells => {
      const ids = [...new Set(cells.flatMap(cell => [...cell.matchAll(/\/api\/artifacts\/([^/?#\s)<>]+)\/(?:download|content)/g)].map(match => match[1])))];
      return { cells, artifact: ids.length === 1 ? (artifacts || []).find(item => item.id === ids[0]) : null };
    }) : [];
    if (fileRows.length && fileRows.every(item => item.artifact && !renderedArtifactIds?.has(item.artifact.id))
      && new Set(fileRows.map(item => item.artifact.id)).size === fileRows.length) {
      out.push("<div class='artifact-list md-delivery-list'>");
      fileRows.forEach(({ cells, artifact }) => {
        renderedArtifactIds?.add(artifact.id);
        const notes = cells.filter((cell, index) => cell && !/^\d+$/.test(cell) && !/\/api\/artifacts\//.test(cell)
          && cell.replace(/\*\*/g, "") !== artifact.name && !/^文件(?:名|名称)?$/.test(headers[index] || ""));
        // 文件名中的额外说明仍可核对，避免把导出范围或失败备注当成装饰删除。
        const filenameIndex = headers.findIndex(cell => /^文件(?:名|名称)?$/.test(cell));
        if (filenameIndex >= 0 && cells[filenameIndex] && cells[filenameIndex].replace(/\*\*/g, "") !== artifact.name) notes.unshift(cells[filenameIndex]);
        out.push(artifactCard(artifact, [...new Set(notes)].map(inline).join(" · ")));
      });
      out.push("</div>"); tableRows = []; return;
    }
    out.push("<table>");
    tableRows.forEach((cells, i) => {
      const tag = i === 0 ? "th" : "td";
      out.push("<tr>" + cells.map((c) => "<" + tag + ">" + inline(c) + "</" + tag + ">").join("") + "</tr>");
    });
    out.push("</table>");
    tableRows = [];
  };

  for (let raw of lines) {
    let line = raw;
    const listType = /^[ \t]*[-*][ \t]+/.test(line) ? "ul" : /^[ \t]*[0-9]+\.[ \t]+/.test(line) ? "ol" : null;
    if (inList && inList !== listType) { out.push("</" + inList + ">"); inList = null; }
    // table rows (start or contain pipe) -> group into a single <table>
    if (/^\s*\|/.test(line)) {
      let cells = line.split("|");
      cells.shift();
      if (cells.length && cells[cells.length - 1].trim() === "") cells.pop();
      // CommonMark/GFM alignment separators may use :---, ---: or :---:.
      // They define column alignment and are not a visible data row.
      if (cells.every((c) => /^:?-{3,}:?$/.test(c.trim()))) { continue; }
      tableRows.push(cells.map((c) => c.trim()));
      continue;
    }
    flushTable();
    const h = line.match(/^(#{1,4})[ 	]+(.*)$/);
    if (h) { const lv = h[1].length; out.push("<h" + lv + ">" + inline(h[2]) + "</h" + lv + ">"); continue; }
    if (/^[ 	]*[-*][ 	]+/.test(line)) { if (inList !== "ul") { inList = "ul"; out.push("<ul>"); } out.push("<li>" + inline(line.replace(/^[ 	]*[-*][ 	]+/, "")) + "</li>"); continue; }
    if (/^[ 	]*[0-9]+\.([ 	]|\))/.test(line)) { if (inList !== "ol") { inList = "ol"; out.push("<ol>"); } out.push("<li>" + inline(line.replace(/^[ 	]*[0-9]+\.([ 	]|\))/, "")) + "</li>"); continue; }
    if (line.trim().indexOf(MARK) === 0) { out.push(restoreBlocks(line)); continue; }
    line = inline(restoreBlocks(line));
    if (line.trim().startsWith("<figure class='md-image-figure'") && line.trim().endsWith("</figure>")) out.push(line);
    else if (line.trim()) out.push("<p>" + line + "</p>");
  }
  flushTable();
  if (inList) out.push("</" + inList + ">");
  return "<div class='markdown-body'>" + out.join(NL) + "</div>";
}
// ---------------- Auth ----------------
/**
 * 登录页提交：调用 /api/auth/login，成功后把 token 与首个工作区写入 localStorage 并进入外壳。
 * 失败只更新 #login-error 文案，不抛异常（登录页是唯一的免鉴权入口）。
 */
async function doLogin() {
  const u = $("#login-username").value.trim(), p = $("#login-password").value;
  $("#login-error").textContent = "";
  try {
    const r = await API.post("/api/auth/login", { username: u, password: p });
    API.token = r.token; localStorage.setItem("agent_token", r.token);
    if (r.workspaces && r.workspaces[0]) localStorage.setItem("agent_workspace_id", r.workspaces[0].id);
    const _pg = currentPage();
    await enterShell(_pg, _pg === "chat");
  } catch (e) { $("#login-error").textContent = e.message; }
}
/**
 * 进入已登录外壳：拉 /api/auth/me 填充用户/工作区信息，按权限显示管理入口，再渲染目标页面。
 * @param {string} page 目标页面标识（见 PAGES）
 * @param {boolean} isChat 是否对话页（对话页保留登录视图元素并预热模型/资源选项）
 * 401 视为会话过期：清 token 并回到登录态；403 及其他错误在内容区给出提示，不清 token。
 */
async function enterShell(page, isChat) {
  try {
    const me = await API.get("/api/auth/me");
    state.me = me;
    if (me.user.must_change_password) { window.location.href = "/"; return; }
    if ($("#user-name")) $("#user-name").textContent = me.user.display_name;
    if ($("#user-workspace")) $("#user-workspace").textContent = me.workspace.name + " (" + (me.role === "admin" ? "管理员" : "成员") + ")";
    if ($("#user-avatar")) $("#user-avatar").textContent = (me.user.display_name || "管").slice(0, 1);
    if (me.role === "admin") {
      const g = $("#admin-group-label"); if (g) g.classList.remove("hidden");
      $$(".admin-item").forEach((el) => el.classList.remove("hidden"));
    }
    await loadBusinessModuleNavigation();
    if (isChat) { const lv = $("#login-view"); if (lv) lv.classList.add("hidden"); }
    document.body.classList.remove("guest");
    const ae = $("#admin-entry"); if ((me.permissions || []).includes("system.users.view") && ae) ae.classList.remove("hidden");
    initSidebarWidth();
    const isAdminPage = page.indexOf("admin") === 0;
    const hasConvoList = !!$("#conversation-list");
    const jobs = [];
    if (hasConvoList && !isAdminPage) jobs.push(loadConversations());
    if (isChat) jobs.push(loadResourceOptions(), loadBusinessActions(), loadBusinessChatModules(), loadChatModels(), loadChatCapabilities());
    await Promise.all(jobs);
    if (isChat) {
      const query = new URLSearchParams(window.location.search);
      const requestedKbId = query.get("knowledge_base_id");
      if (requestedKbId && state.knowledge.some((item) => item.id === requestedKbId)) {
        state.kbIds = [requestedKbId];
      }
      const requestedPluginId = query.get("plugin_id");
      const requestedPluginVersionId = query.get("plugin_version_id");
      const requestedPlugin = state.plugins.find((item) =>
        (requestedPluginId && item.id === requestedPluginId) ||
        (requestedPluginVersionId && item.published_version_id === requestedPluginVersionId)
      );
      if (requestedPlugin) {
        state.pluginId = requestedPlugin.id;
        state.pluginVersionId = requestedPluginVersionId || requestedPlugin.published_version_id || null;
      }
      const requestedFileId = query.get("file_id");
      if (requestedFileId) {
        try {
          const files = (await API.get("/api/files")).items || [];
          const requestedFile = files.find((item) => item.id === requestedFileId);
          if (requestedFile) state.fileIds = [{ id: requestedFile.id, name: requestedFile.name }];
        } catch (error) {}
      }
      await renderPresets();
      renderAttachChips();
    }
    await renderPage(page);
  } catch (e) {
    if (e.status !== 401) {
      const box = contentEl(page) || $("#messages");
      if (box) box.innerHTML = "<div class='panel customer-page'><div class='customer-inline-error' role='alert'><h2>" + (e.status === 403 ? "当前账号没有访问权限" : "暂时无法加载") + "</h2><p>" + esc(e.message) + "</p><a class='btn-sm btn-secondary' href='/'>返回业务首页</a></div></div>";
      else toast(e.message);
      return;
    }
    API.token = ""; localStorage.removeItem("agent_token");
    if (isChat) { document.body.classList.add("guest"); const lv = $("#login-view"); if (lv) { lv.classList.remove("hidden"); const le = $("#login-error"); if (le) le.textContent = "会话已失效，请重新登录：" + e.message; } }
    else window.location.href = "/";
  }
}

// ---------------- 侧栏宽度（可拖拽） ----------------
/** 侧栏拖拽调宽：宽度持久化在 localStorage("sidebar_width_compact_v1")，并同步 CSS 变量 --sidebar-width。 */
function initSidebarWidth() {
  const sb = $("#sidebar");
  if (!sb || !$("#sidebar-resizer")) return;
  const saved = localStorage.getItem("sidebar_width_compact_v1");
  sb.style.width = saved || "220px";
  document.documentElement.style.setProperty("--sidebar-width", sb.style.width);
  const r = $("#sidebar-resizer");
  let dragging = false;
  const move = (e) => {
    if (!dragging) return;
    const w = Math.max(224, Math.min(380, e.clientX));
    sb.style.width = w + "px";
    document.documentElement.style.setProperty("--sidebar-width", w + "px");
  };
  r.addEventListener("mousedown", (e) => {
    e.preventDefault(); dragging = true; document.body.classList.add("resizing");
    window.addEventListener("mousemove", move);
  });
  window.addEventListener("mouseup", () => {
    if (dragging) { dragging = false; document.body.classList.remove("resizing"); localStorage.setItem("sidebar_width_compact_v1", sb.style.width); window.removeEventListener("mousemove", move); }
  });
}

// ---------------- 页面路由 ----------------
/** 当前页面标识，取自 <body data-page>，缺省按对话页处理。 */
function currentPage() { return document.body.dataset.page || "chat"; }

/**
 * 按页面标识渲染内容：对话页走 renderMessages（支持 ?cid= 直接打开会话），
 * 其余页面查表调用各自的 render*View；表里没有的标识不渲染任何内容。
 */
function renderPage(page) {
  if (page === "chat") {
    const cid = new URLSearchParams(window.location.search).get("cid");
    if (cid && $("#messages")) openConversation(cid);
    else if ($("#messages")) renderMessages();
  } else {
    const handlers = { knowledge: renderKnowledgeView, plugins: renderPluginsView,
      skills: renderSkillsView, connectors: renderConnectorsView, datasources: renderDatasourcesView,
      files: renderFilesView, runs: renderRunsView, admin: renderAdminDashboardView,
      "admin-members": renderAdminMembersView, "admin-audit": renderAdminAuditView,
      "admin-secrets": renderAdminSecretsView, "admin-models": renderAdminModelsView,
      "admin-ops": renderAdminOpsView };
    return handlers[page]?.();
  }
}

/** 侧栏导航入口：非对话页一律整页跳转到 /<name>.html（旧版是多页 HTML，不是前端路由）。 */
function showPage(name) {
  if (name !== "chat") { window.location.href = "/" + name + ".html"; return; }
  if ($("#messages")) renderMessages();
}

// ---------------- 会话 ----------------
/**
 * 渲染左侧「业务对话」模块入口（设备/质量/财务等），数据来自 /api/chat/business-modules。
 * 加载失败时降级为一个可点击重试的按钮，不阻塞整页。
 */
async function loadBusinessChatModules() {
  const host = $("#business-chat-modules");
  if (!host) return;
  try {
    state.businessModules = (await API.get("/api/chat/business-modules")).items || [];
    const icons = { equipment: "⚙", quality: "✓", finance: "¥", sales: "↗", purchase: "⇄", energy: "ϟ", inventory: "▦" };
    host.innerHTML = '<button type="button" class="business-chat-toggle" aria-expanded="true" aria-controls="business-chat-grid"><span>业务对话</span><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4"/></svg></button><div id="business-chat-grid" class="business-chat-grid"></div>';
    const grid = host.querySelector(".business-chat-grid");
    const toggle = host.querySelector(".business-chat-toggle");
    toggle.onclick = () => { grid.hidden = !grid.hidden; toggle.setAttribute("aria-expanded", String(!grid.hidden)); };
    state.businessModules.forEach(module => {
      const button = document.createElement("button");
      button.type = "button"; button.dataset.businessModule = module.id;
      button.setAttribute("aria-label", "进入" + module.name + "对话");
      button.title = module.description;
      button.innerHTML = '<span class="business-chat-icon" aria-hidden="true">' + esc(icons[module.id] || "◇") + '</span><span>' + esc(module.name) + '</span>';
      button.onclick = async () => {
        closeMenu();
        if (state.current?.business_module === module.id && !state.current.messages.length) {
          document.body.classList.remove("mobile-sidebar-open"); $("#prompt-textarea")?.focus(); return;
        }
        await newChat({ businessModule: module.id, preserveComposerContext: true });
      };
      grid.appendChild(button);
    });
    syncBusinessChatModule(); renderConversationList();
  } catch (error) {
    host.innerHTML = '<div class="business-chat-grid"><button type="button" class="business-chat-retry">业务对话加载失败，点击重试</button></div>';
    host.querySelector("button").onclick = loadBusinessChatModules;
  }
}
/** 同步业务模块的选中态：高亮当前模块入口、切换徽标与输入框占位文案（占位文案由后端模块定义给出）。 */
function syncBusinessChatModule() {
  const module = state.businessModules.find(item => item.id === state.current?.business_module);
  $$("[data-business-module]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.businessModule === module?.id));
    button.disabled = state.openingChat;
  });
  $("#new-chat-btn")?.classList.toggle("active", !module);
  const badge = $("#business-chat-badge");
  if (badge) { badge.hidden = !module; badge.textContent = module ? module.name + "对话" : ""; }
  const input = $("#prompt-textarea");
  if (input) input.placeholder = module?.placeholder || "询问企业 AI";
}
/**
 * 拉取会话列表并渲染；摘要缺失的占位会话再异步补一轮（见 hydratePlaceholderConversationSummaries）。
 * 对话页没有任何会话时自动收起侧栏，给空态让出横向空间。
 */
async function loadConversations() {
  const r = await API.get("/api/chat/conversations");
  state.conversations = r.items || [];
  state.conversations.forEach((conversation) => {
    if (!conversation.summary && conversationSummaryCache.has(conversation.id)) {
      conversation.summary = conversationSummaryCache.get(conversation.id);
    }
  });
  if (currentPage() === "chat" && !state.conversations.length && !$("#business-chat-modules") && !new URLSearchParams(location.search).get("cid")) {
    document.body.classList.add("sidebar-collapsed");
  }
  renderConversationList();
  void hydratePlaceholderConversationSummaries();
}
/** 渲染会话历史列表（含业务模块徽标与删除按钮）；删除按钮的点击不再冒泡触发打开会话。 */
function renderConversationList() {
  const ul = $("#conversation-list");
  if (!ul) return;
  const empty = $("#conversation-empty");
  $("#history-count").textContent = state.conversations.length ? state.conversations.length + " 条" : "";
  ul.innerHTML = "";
  if (!state.conversations.length) { empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  const frag = document.createDocumentFragment();
  state.conversations.forEach((c) => {
    const li = document.createElement("li");
    const summary = conversationDisplayTitle(c);
    li.className = "history-item" + (state.current && state.current.id === c.id ? " active" : "");
    li.title = summary;
    li.innerHTML = "<svg width='14' height='14' viewBox='0 0 24 24' fill='none' style='color:#9a9a9a;flex-shrink:0'><path d='M21 12a8 8 0 0 1-8 8H4l2-3.5A8 8 0 1 1 21 12z' stroke='currentColor' stroke-width='1.6' stroke-linejoin='round'/></svg><span class='history-title el'>" + esc(summary) + "</span><button class='history-options' title='删除'>" + IC.close + "</button>";
    const module = state.businessModules.find(item => item.id === c.business_module);
    if (module) {
      const badge = document.createElement("span"); badge.className = "history-module-badge"; badge.textContent = module.name;
      li.insertBefore(badge, li.querySelector(".history-title"));
    }
    li.dataset.conversationId = c.id;
    li.onclick = (ev) => {
      if (ev.target.closest(".history-options")) { deleteConversation(c.id); return; }
      openConversation(c.id);
    };
    frag.appendChild(li);
  });
  ul.appendChild(frag);
}
/**
 * 为标题仍是占位文案的老会话补摘要：逐条读会话详情取首条用户消息。
 * 按 6 条一批并发，并用 conversationSummaryLoads 去重，避免列表反复重渲染触发重复请求；
 * 单条失败只清缓存不抛错，保证其余会话照常显示。
 */
async function hydratePlaceholderConversationSummaries() {
  const pending = state.conversations.filter((conversation) => {
    const title = String(conversation.title || "").trim();
    return !conversation.summary
      && PLACEHOLDER_CONVERSATION_TITLES.has(title)
      && !conversationSummaryCache.has(conversation.id)
      && !conversationSummaryLoads.has(conversation.id);
  });
  const batchSize = 6;
  for (let offset = 0; offset < pending.length; offset += batchSize) {
    const batch = pending.slice(offset, offset + batchSize);
    let changed = false;
    await Promise.all(batch.map(async (conversation) => {
      conversationSummaryLoads.add(conversation.id);
      try {
        const detail = await API.get("/api/chat/conversations/" + conversation.id);
        const firstUserMessage = (detail.messages || []).find((message) => message.role === "user" && String(message.content || "").trim());
        const summary = firstUserMessage ? firstSentenceSummary(firstUserMessage.content) : "";
        conversationSummaryCache.set(conversation.id, summary);
        if (summary) {
          conversation.summary = summary;
          changed = true;
        }
      } catch (error) {
        conversationSummaryCache.delete(conversation.id);
      } finally {
        conversationSummaryLoads.delete(conversation.id);
      }
    }));
    if (changed && currentPage() === "chat") renderConversationList();
  }
}
/** 删除会话：服务端确认删除后再清理本地状态，保留失败时的历史和运行入口。 */
async function deleteConversation(id) {
  if (!confirm("删除该对话？")) return;
  try { await API.del("/api/chat/conversations/" + id); }
  catch (e) { toast(e.message || "删除失败，请稍后重试"); return; }
  conversationSummaryCache.delete(id);
  conversationSummaryLoads.delete(id);
  state.conversations = state.conversations.filter((c) => c.id !== id);
  if (id === (state.current || {}).id) { if (typeof dshChat !== "undefined") dshChat?.close(); state.current = null; history.replaceState(null, "", "/ai"); renderMessages(); }
  if (!state.conversations.length && !$("#business-chat-modules")) document.body.classList.add("sidebar-collapsed");
  renderConversationList();
}
/**
 * 渲染模型选择下拉框。
 * 生成中/切换会话中/模型列表未就绪时禁用；原模型不可用时插入一条禁用项提示重新选择，
 * 而不是静默回退到默认模型（用户必须知道这一轮换了模型）。
 */
function renderChatModels() {
  const select = $("#chat-model-select");
  if (!select) return;
  select.innerHTML = state.chatModels.map(item => '<option value="' + esc(item.profile_name) + '">' + esc(item.label) + (item.default ? '（默认）' : '') + '</option>').join('');
  if (state.modelsError || !state.chatModels.length) select.innerHTML = '<option value="">' + esc(state.modelsError ? '模型加载失败' : '暂无可用模型') + '</option>';
  else {
    if (!state.chatModels.some(item => item.profile_name === state.modelProfile)) select.insertAdjacentHTML('afterbegin', '<option value="' + esc(state.modelProfile) + '" disabled>原模型不可用，请选择</option>');
    select.value = state.modelProfile;
  }
  select.disabled = state.streaming || state.openingChat || !state.modelsReady || !!state.modelsError || !state.chatModels.length;
  $("#chat-model-retry")?.classList.toggle('hidden', !state.modelsError && state.chatModels.length > 0);
  select.title = state.modelsError || '选择本轮对话使用的模型';
}

/** 拉取可用对话模型；失败时保留错误文案供 UI 显示重试按钮，不抛出。 */
async function loadChatModels() {
  state.modelsReady = false; state.modelsError = '';
  renderChatModels();
  try {
    const result = await API.get('/api/chat/models');
    state.chatModels = result.items || [];
    state.modelsReady = true;
  } catch (error) { state.modelsError = error.message; }
  renderChatModels(); updateSendState();
}

/**
 * 新建会话。
 * @param {object} options
 * @param {boolean} options.preserveComposerContext 为 true 时保留当前输入区的附件/模板/模型选择（业务模块内切换用）
 * @param {string|null} options.businessModule 业务模块 id，普通对话传 null
 * 约束：openingChat 是全局互斥锁，生成中或切换中直接返回，避免并发创建产生两条空会话。
 */
async function newChat({ preserveComposerContext = false, businessModule = null } = {}) {
  if (state.streaming && !dshChat?.enabled()) return toast("正在生成中，请稍候…");
  if (state.openingChat) return;
  state.openingChat = true;
  syncBusinessChatModule();
  try {
  const chatPage = currentPage() === "chat";
  const r = await API.post("/api/chat/conversations", { title: "新对话", business_module: businessModule });
  if (!chatPage) { window.location.href = "/ai?cid=" + r.id; return; }
  state.current = { id: r.id, title: r.title, business_module: r.business_module || null, messages: [] };
  state.conversations.unshift({ id: r.id, title: r.title, business_module: state.current.business_module });
  history.replaceState(null, "", "/ai?cid=" + encodeURIComponent(r.id));
  if (!preserveComposerContext) {
    state.activeMode = null; state.skillIds = [];
    $("#active-mode-btn")?.classList.add("hidden");
    state.modelProfile = 'reasoning';
    state.fileIds = []; state.kbIds = []; state.datasourceIds = []; state.pluginId = null; state.pluginVersionId = null;
    state.pptActive = false; state.pptTemplateId = null; state.pptTemplateDraft = null; state.pptPageCount = "auto";
    state.dashboardTemplateMode = "auto"; state.dashboardTemplateId = null;
  }
  renderAttachChips(); renderPresets(); renderMessages(); renderConversationList();
  if (dshChat?.enabled()) dshChat.open({ ...r, messages: [], projections: { mode: state.activeMode || "standard" } }, { preserveComposer: preserveComposerContext });
  document.body.classList.remove("mobile-sidebar-open");
  if ($("#prompt-textarea")) $("#prompt-textarea").focus();
  } catch (error) { toast("无法创建对话：" + error.message); }
  finally { state.openingChat = false; syncBusinessChatModule(); renderChatModels(); }
}
/**
 * 打开指定会话：读详情并恢复模型档位与消息列表。
 * 进入历史会话时清空上一轮的输入区上下文（附件/知识库/数据源/PPT/看板模板），
 * 只把模型档位恢复到该会话上次使用的值。
 */
async function openConversation(id) {
  if (currentPage() !== "chat") { window.location.href = "/ai?cid=" + id; return; }
  if ((state.streaming && !dshChat?.enabled()) || state.openingChat) return toast("正在处理，请稍候…");
  state.openingChat = true;
  syncBusinessChatModule();
  try {
  const r = await API.get("/api/chat/conversations/" + id);
  state.modelProfile = r.last_run?.model_profile || r.last_run?.context_snapshot?.model_profile || 'reasoning';
  renderChatModels();
  state.current = { id: r.id, title: r.title, business_module: r.business_module || null, messages: r.messages || [], lastRunId: r.last_run?.id || null };
  history.replaceState(null, "", "/ai?cid=" + encodeURIComponent(r.id));
  document.body.classList.remove("mobile-sidebar-open");
  state.fileIds = []; state.kbIds = []; state.datasourceIds = []; state.pluginId = null; state.pluginVersionId = null;
  state.pptActive = false; state.pptTemplateId = null; state.pptTemplateDraft = null; state.pptPageCount = "auto";
  state.dashboardTemplateMode = "auto"; state.dashboardTemplateId = null;
  try {
    const fl = await API.get("/api/files");
    state.fileIndex = {};
    (fl.items || []).forEach((f) => { state.fileIndex[f.id] = { name: f.name, mime_type: f.mime_type }; });
    const snapshot = (r.last_run && r.last_run.context_snapshot) || {};
    state.datasourceIds = Array.from(new Set(snapshot.datasource_ids || [])).slice(0, 8);
    state.dashboardTemplateMode = snapshot.dashboard_template_mode || "auto";
    state.dashboardTemplateId = snapshot.dashboard_template_id || null;
    if (r.last_run && r.last_run.status === "failed") {
      state.fileIds = (snapshot.file_ids || []).filter((fileId) => state.fileIndex[fileId]).map((fileId) => ({ id: fileId, name: state.fileIndex[fileId].name }));
      state.kbIds = (snapshot.knowledge_base_ids || []).filter((kbId) => state.knowledge.some((kb) => kb.id === kbId));
      state.pluginVersionId = r.last_run.plugin_version_id || null;
      const plugin = state.plugins.find((item) => item.published_version_id === state.pluginVersionId);
      state.pluginId = plugin ? plugin.id : null;
      if (snapshot.template_id) {
        state.pptTemplateId = snapshot.template_id;
        state.pptActive = true;
        const pptInputs = snapshot.plugin_inputs || {};
        state.pptRatio = pptInputs.aspect_ratio || "16:9";
        state.pptPageCount = /^\d+$/.test(String(pptInputs.page_count || "")) ? String(pptInputs.page_count) : "auto";
      }
      state.activeMode = snapshot.mode || null;
    }
  } catch (e) {}
  renderAttachChips(); renderPresets(); renderMessages(); renderConversationList();
  if (dshChat?.enabled()) dshChat.open(r);
  else if (["created", "running"].includes(r.last_run?.status)) resumeChatRun(r.last_run.id, r.id);
  } catch (error) { toast("无法打开对话：" + error.message); }
  finally { state.openingChat = false; syncBusinessChatModule(); renderChatModels(); }
}

// ---------------- 消息渲染 ----------------
/**
 * 空会话欢迎区：业务模块内显示模块欢迎语，普通对话显示「快捷场景」卡片。
 * 场景卡片只在后端返回对应 capability 且 available 时出现（前端不硬编码可用能力）。
 */
function businessWelcomeMarkup() {
  const module = state.businessModules.find(item => item.id === state.current?.business_module);
  if (module) return '<div class="welcome business-welcome" data-testid="ai-business-start"><div class="business-chat-welcome"><h2>' + esc(module.name) + '助手</h2><p>' + esc(module.description) + '</p><span>直接输入你想了解或处理的事项</span></div></div>';
  const available = new Set((state.businessActions || []).filter((item) => item.available).map((item) => item.capabilityId));
  const scenes = [];
  if (available.has("alerts.overview")) scenes.push({
    id: "alerts", title: "查看设备预警",
    prompt: "查看设备预警，列出最近报告、当前问题和可继续处理的入口。",
    autosend: true,
    icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 3.8 18h16.4L12 3Z"></path><path d="M12 8v5m0 3h.01"></path></svg>'
  });
  if (available.has("dashboard.generate")) scenes.push({
    id: "dashboard", title: "生成财务与设备大屏",
    prompt: "生成财务和设备数字大屏，使用输入区已选择的真实数据与模板策略，并保存为唯一草稿。",
    autosend: false,
    icon: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M7 15v2m5-6v6m5-10v10"></path></svg>'
  });
  const cards = scenes.map((scene) => '<button type="button" class="ai-scene-card" data-business-scene="' + scene.id + '" data-business-prompt="' + esc(scene.prompt) + '" data-business-autosend="' + (scene.autosend ? "true" : "false") + '"><span class="ai-scene-icon">' + scene.icon + '</span><strong>' + scene.title + '</strong></button>').join("");
  const sources = available.has("business.datasources.discover")
    ? '<button type="button" class="ai-source-prompt" data-business-source-picker="true"><svg viewBox="0 0 20 20" aria-hidden="true"><ellipse cx="10" cy="5" rx="6.5" ry="2.5"></ellipse><path d="M3.5 5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5m-13 5v5c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-5"></path></svg><strong>选择业务数据</strong></button>'
    : "";
  const actions = cards + sources;
  return '<div class="welcome business-welcome" data-testid="ai-business-start"><div class="ai-quick-start">' + (actions ? '<div class="ai-scene-grid">' + actions + '</div>' : '<span class="ai-scene-unavailable">直接输入需要处理的事项</span>') + '</div></div>';
}

/** 绑定欢迎区按钮：填充 prompt 后按 autosend 决定是否立即发送；生成中直接拒绝，避免并发提交。 */
function bindBusinessPrompts(root) {
  root.querySelectorAll("[data-business-prompt], [data-business-source-picker]").forEach((button) => {
    button.onclick = async () => {
      if (state.streaming) return toast("当前任务仍在执行，请稍候");
      if (button.dataset.businessSourcePicker === "true") {
        await openDataSourcePicker(button);
        return;
      }
      const input = $("#prompt-textarea");
      if (!input) return;
      input.value = button.dataset.businessPrompt || "";
      updateSendState();
      input.focus();
      if (button.dataset.businessAutosend === "true") await sendMessage();
    };
  });
}

/**
 * 按消息与内容块更新当前会话，保留历史 DOM 和正在交互的业务预览。
 * 用户消息渲染为气泡（附件用 fileIndex 补名字）；助手消息交给 assistantRow 组装 parts。
 * 只有最后一条助手消息带追问建议；需要补充业务范围时使用普通文字，不创建额外的选择卡片。
 */
function renderMessages() {
  syncBusinessChatModule();
  const box = $("#messages");
  if (!box) return;
  const scroll = $("#scroll-root"), readingTop = scroll?.scrollTop;
  const preservePosition = scroll?._chatScrolling?.following === false;
  const focusedAnswer = document.activeElement?.closest("[data-dsh-question]") ? document.activeElement : null;
  const answerBounds = focusedAnswer?.getBoundingClientRect(), scrollBounds = scroll?.getBoundingClientRect();
  const answerAnchor = answerBounds && scrollBounds && answerBounds.bottom > scrollBounds.top && answerBounds.top < scrollBounds.bottom ? focusedAnswer : null;
  const answerTop = answerAnchor?.getBoundingClientRect().top;
  const conversation = state.current?.id || "";
  const changedConversation = box.dataset.chatConversation !== conversation;
  if (changedConversation) { box.replaceChildren(); box.dataset.chatConversation = conversation; }
  const rows = new Map([...box.children].filter(node => node.dataset.chatMessage).map(node => [node.dataset.chatMessage, node]));
  const displayed = new Set();
  let cursor = box.firstElementChild;
  const msgs = state.current ? state.current.messages : [];
  const page = $("#page-chat"); if (page) page.classList.toggle("is-empty", !msgs.length);
  const title = $("#header-title");
  if (title) { title.textContent = state.current?.title || "AI 对话"; title.classList.toggle("hidden", !msgs.length); }
  $("#share-conversation")?.classList.toggle("hidden", !msgs.length);
  if (!msgs.length) {
    box.innerHTML = businessWelcomeMarkup();
    bindBusinessPrompts(box);
    return;
  }
  let precedingPrompt = "";
  msgs.forEach((m, index) => {
    if (m.role === "user") precedingPrompt = m.content || "";
    const key = m.id || m.role + ":" + (m.run_id || index);
    const signature = JSON.stringify([m, precedingPrompt, index === msgs.length - 1, index === msgs.length - 1 && state.streaming,
      (m.parts || []).filter(part => part.type === "file").map(part => state.fileIndex?.[part.file?.id]),
      dshChat?.controller?.store?.value.runs[m.run_id], dshChat?.enabled() ? dshChat.interactions(m) : []]);
    const previous = rows.get(key);
    let next;
    if (previous?._chatSignature === signature) next = previous;
    else {
    if (m.role === "user") {
      const parts = m.parts || [];
      const fileParts = parts.filter((p) => p.type === "file").map((p) => p.file || {});
      const chips = fileParts.map((f) => {
        const id = f.id || "";
        const meta = id && state.fileIndex ? state.fileIndex[id] : null;
        const label = (meta && meta.name) || (f.name && f.name !== "file" ? f.name : (id ? "文件 " + id.slice(0, 8) : "文件"));
        const url = id ? authUrl("/api/files/" + id + "/download") : "";
        return url ? "<a class='attach-chip' href='" + url + "' target='_blank' title='下载'>" + IC.download + " " + esc(label) + "</a>" : "<span class='attach-chip'>" + esc(label) + "</span>";
      }).join(" ");
      const row = document.createElement("div"); row.className = "msg-row";
      row.innerHTML = "<div class='user-bubble'>" + (chips ? "<div style='margin-bottom:5px;'>" + chips + "</div>" : "") + esc(m.content || "").replace(/\n/g, "<br>") + "</div>";
      next = row;
    } else if (m.role === "assistant") {
      const row = assistantRow(m.parts || [], m.content || "", precedingPrompt, index === msgs.length - 1);
      if (dshChat?.enabled()) dshChat.decorate(row, m);
      const hasError = (m.parts || []).some(p => p.type === "error" || (p.type === "tool_result" && p.result?.status === "failed"));
      if (index === msgs.length - 1 && !state.streaming && !(m.parts || []).some(p => p.type === "business_question") && (!hasError || (m.parts || []).some(p => p.type === "followup_questions")) && (m.content || (m.parts || []).some(p => p.type === "text" && p.text))) {
        const suggestions = (m.parts || []).find(p => p.type === "followup_questions");
        appendFollowupQuestions(row.querySelector(".msg-body"), suggestions ? suggestions.questions : m.content || (m.parts || []).filter(p => p.type === "text").map(p => p.text || "").join("\n"), msgs, suggestions?.error);
      }
      next = row;
    }
    if (!next) return;
    if (previous) { reconcileMessageChildren(previous, next); next = previous; }
    else rememberMessageMarkup(next);
    next.dataset.chatMessage = key;
    next._chatSignature = signature;
    }
    displayed.add(next);
    if (next !== cursor) box.insertBefore(next, cursor);
    cursor = next.nextElementSibling;
  });
  [...box.children].forEach(node => { if (!displayed.has(node) && node.id !== "dsh-session-deliveries") node.remove(); });
  window.ChatDashboardPreview?.hydrate(box);
  if (preservePosition && !changedConversation) {
    scroll.scrollTop = readingTop;
    // 填写追问时固定正在操作的控件，上方正文增高不能把表单挤出视口。
    if (answerAnchor?.isConnected) scroll.scrollTop += answerAnchor.getBoundingClientRect().top - answerTop;
  }
  scrollToBottom({ force: changedConversation });
}

/** 保存初始标记，后续只更新变化块；预览模块写入 iframe 后仍可判断业务回执是否相同。 */
function rememberMessageMarkup(node) {
  node._chatMarkup = node.outerHTML;
  [...node.children].forEach(rememberMessageMarkup);
}

/** 同一大屏的容器及祖先保持连接，避免流式正文、进度事件导致 iframe 重载。 */
function reconcileMessageChildren(target, source) {
  const children = node => {
    const counts = new Map();
    return [...node.children].map(child => {
      const kind = child.dataset.dashboardPreviewKey ? "preview:" + child.dataset.dashboardPreviewKey
        : child.dataset.dshQuestion ? "question:" + child.dataset.dshQuestion
        : child.dataset.reportId ? "report:" + child.dataset.reportId
        : child.dataset.resultType || child.dataset.artifactId || child.tagName + ":" + child.className;
      const index = counts.get(kind) || 0; counts.set(kind, index + 1);
      return [kind + ":" + index, child];
    });
  };
  const incoming = children(source), wanted = new Set(incoming.map(([key]) => key));
  const existing = new Map(children(target)), retained = new Set();
  for (const [key, node] of existing) if (!wanted.has(key)) node.remove();
  let cursor = target.firstElementChild;
  for (const [key, fresh] of incoming) {
    const old = existing.get(key), markup = fresh.outerHTML;
    let next = fresh;
    // 操作按钮的文字可能相同，但复制和重试闭包必须绑定最新一轮正文与提问。
    if (old && ((old._chatMarkup === markup && !fresh.matches(".message-actions, .msg-body")) || (old.dataset.dashboardPreviewKey && old.dataset.dashboardPreviewKey === fresh.dataset.dashboardPreviewKey))) next = old;
    else if (old && (fresh.classList.contains("msg-body") || (old.querySelector("[data-dashboard-preview-key]") && fresh.querySelector("[data-dashboard-preview-key]")))) {
      reconcileMessageChildren(old, fresh); old._chatMarkup = markup; next = old;
    } else rememberMessageMarkup(fresh);
    retained.add(next);
    if (old && old !== next) { if (old === cursor) cursor = old.nextElementSibling; old.remove(); }
    if (next !== cursor) target.insertBefore(next, cursor);
    cursor = next.nextElementSibling;
  }
  [...target.children].forEach(node => { if (!retained.has(node)) node.remove(); });
}

/**
 * 保留模型的追问数据用于审计，但不在聊天区渲染“你可能想问”按钮。
 * 用户需要补充信息时，直接使用底部输入框发送普通消息，避免出现与任务无关的操作卡片。
 */
function appendFollowupQuestions(body, answer, messages = [], error = "") {
  // 追问仍保存在消息 parts 中，供继续执行和审计使用；界面不创建推荐按钮。
}

/**
 * 将业务澄清问题作为普通助手文字展示。
 * 不创建选项按钮、独立输入框或独立操作卡片；用户直接使用底部输入框回复。
 */
function appendBusinessQuestion(body, question, active = true) {
  body.querySelector(".business-clarification")?.remove();
  if (!question || typeof question.question !== "string") return;
  const text = document.createElement("div");
  text.className = "assistant-content business-clarification";
  text.setAttribute("role", "status");
  text.textContent = "需要补充信息：" + question.question;
  const actions = body.querySelector(".message-actions");
  if (actions?.parentElement === body) body.insertBefore(text, actions); else body.appendChild(text);
}

function businessPath(raw) {
  const value = String(raw || "").trim();
  // Business actions always open routes in this platform.  Reject protocol-
  // relative links (//host/path) as well as schemes and script fragments even
  // if a future provider accidentally returns one in a structured result.
  return /^\/(?!\/)[A-Za-z0-9_?&=/%.-]*$/.test(value) ? value : "#";
}

/** 业务结果卡片的跳转按钮组；href 一律过 businessPath 白名单（只允许站内单斜杠路径）。 */
/** 业务结果卡片的跳转按钮组；href 一律过 businessPath 白名单（只允许站内单斜杠路径）。 */
function businessActionMarkup(actions) {
  return (actions || []).map((action) => '<a class="business-result-action ' + (action.kind === "primary" ? "primary" : "") + '" href="' + esc(businessPath(action.href)) + '" target="_blank" rel="noopener">' + esc(action.label) + '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3h7v7M13 3 5 11m-2-5v7h7"></path></svg></a>').join("");
}

/** 业务时间显示：截到秒并把 T 换成空格；空值统一显示「—」，不显示 null/undefined。 */
function businessTime(value) {
  return String(value || "").replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) || "—";
}

/**
 * 设备预警结构化结果卡片（只读）。
 * 状态文案与后端 outcome/status 枚举一一对应，未知枚举直接原样展示，避免臆造结果；
 * 报告最多显示 5 条、问题 4 条，超出部分靠 actions 里的入口跳转到完整页面。
 */
function alertBusinessResult(data) {
  const summary = data.summary || {};
  const reports = data.reports || [];
  const problems = data.problems || [];
  const outcomes = { issues: "发现问题", normal: "未发现问题", no_data: "无数据", completed: "已完成", failed: "失败" };
  const reportRows = reports.slice(0, 5).map((row) => '<tr><td><a href="' + esc(businessPath(row.href)) + '" target="_blank" rel="noopener">' + esc(row.title) + '</a><small>' + esc(row.deviceName) + '</small></td><td><span class="business-status ' + esc(row.outcome) + '">' + esc(outcomes[row.outcome] || row.outcome) + '</span></td><td>' + esc(String(row.issues == null ? 0 : row.issues)) + '</td><td>' + esc(businessTime(row.createTime)) + '</td></tr>').join("");
  const problemRows = problems.slice(0, 4).map((row) => '<li><span class="problem-severity ' + esc(row.severity) + '">' + esc(row.severity || "问题") + '</span><div><strong>' + esc(row.ruleName || row.recordKey || "设备问题") + '</strong><span>' + esc(row.message || "无问题说明") + '</span></div><a href="' + esc(businessPath(row.href)) + '" target="_blank" rel="noopener">' + esc(({ open: "待处理", acknowledged: "已确认", resolved: "已解决", ignored: "已忽略" })[row.status] || row.status) + '</a></li>').join("");
  return '<section class="business-result-card alert-result" data-result-type="alert_problem_list" data-testid="alert-business-result"><header class="business-result-head"><div><span class="business-result-eyebrow">设备预警 · 实时权限范围</span><h3>' + esc(data.title || "设备预警与问题") + '</h3></div><span class="business-result-state">只读查询</span></header><div class="business-metrics"><div><strong>' + esc(String(summary.returnedReports || 0)) + '</strong><span>本次报告</span></div><div><strong>' + esc(String(summary.returnedProblems || 0)) + '</strong><span>本次问题</span></div><div><strong>' + esc(String((summary.statusCounts || {}).open || 0)) + '</strong><span>待处理</span></div><div><strong>' + esc(String((summary.statusCounts || {}).resolved || 0)) + '</strong><span>已解决</span></div></div><div class="business-result-section"><div class="business-section-title"><strong>最近预警报告</strong><span>' + esc(summary.scopeNote || "当前授权返回页") + '</span></div>' + (reportRows ? '<div class="business-table-wrap"><table><thead><tr><th>报告 / 设备</th><th>判定</th><th>问题</th><th>时间</th></tr></thead><tbody>' + reportRows + '</tbody></table></div>' : '<p class="business-result-empty">当前授权范围内尚无预警报告。</p>') + '</div>' + (problemRows ? '<div class="business-result-section"><div class="business-section-title"><strong>问题处置</strong><span>原始判断与人工状态分开记录</span></div><ul class="business-problem-list">' + problemRows + '</ul></div>' : "") + '<footer class="business-result-footer"><div class="business-provenance"><span>意图</span><i></i><span>alerts.view</span><i></i><span>持久化报告</span><i></i><strong>当前结果</strong></div><div class="business-result-actions">' + businessActionMarkup(data.actions) + '</div></footer></section>';
}

/**
 * 数字大屏结果卡片。message_preview 是后端透传的原始 JSON 字符串，
 * 解析失败就按当前对象渲染，绝不因为解析失败而丢掉已有字段。
 */
function dashboardBusinessResult(data) {
  if (data && typeof data === "object" && typeof data.message_preview === "string") {
    try {
      const parsed = JSON.parse(data.message_preview);
      if (parsed && typeof parsed === "object") data = parsed;
    } catch {}
  }
  const preview = window.ChatDashboardPreview?.markup({ previewPath: data.previewPath, title: data.title, version: data.version }) || "";
  const sourceNames = (data.datasourceNames || []).map((name) => '<span>' + esc(name) + '</span>').join("");
  const template = state.dashboardTemplates.find((item) => item.id === data.templateId);
  const reference = data.provenance?.templateReference;
  const templateLabel = reference ? (reference.name + " · 业务适配") : data.templateId ? (template?.name || data.templateId) : (({ auto: "动态适配", manual: "手动选择", none: "无模板" })[data.templateMode] || data.templateMode || "动态适配");
  return '<section class="business-result-card dashboard-result" data-result-type="dashboard" data-report-id="' + esc(String(data.reportId || data.dashboard_id || "")) + '" data-testid="dashboard-business-result"><header class="business-result-head"><div><span class="business-result-eyebrow">数字大屏 · 已保存业务资源</span><h3>' + esc(data.title || "数据分析大屏") + '</h3></div><span class="business-result-state draft">草稿 v' + esc(String(data.version || 1)) + '</span></header><div class="dashboard-result-meta"><div><span>组件</span><strong>' + esc(String(data.widgetCount || 0)) + '</strong></div><div><span>验证查询</span><strong>' + esc(String((data.queryIds || []).length)) + '</strong></div><div><span>模板</span><strong title="' + esc(String(data.templateId || "")) + '">' + esc(templateLabel) + '</strong></div><div><span>执行状态</span><strong>' + (data.reused ? "恢复既有草稿" : "已完成") + '</strong></div></div><div class="dashboard-source-line"><span>真实数据来源</span><div>' + (sourceNames || '<span>未返回来源名称</span>') + '</div></div>' + (data.description ? '<p class="business-result-section">' + esc(data.description) + '</p>' : '') + preview + '<footer class="business-result-footer"><div class="business-provenance"><span>业务意图</span><i></i><span>授权数据</span><i></i><span>' + esc(String((data.queryIds || []).length)) + ' 项验证</span><i></i><strong>草稿 #' + esc(String(data.reportId || "").slice(0, 8)) + '</strong></div><div class="business-result-actions">' + businessActionMarkup(data.actions) + '</div></footer></section>';
}

/** 发现结果仅更新输入区的数据源目录，不在消息中追加候选卡片。 */
function rememberBusinessSources(data) {
  const sources = businessSourceCatalog(data).sources;
  sources.forEach((source) => {
    const index = state.dataSources.findIndex((item) => item.id === source.id);
    if (index >= 0) state.dataSources[index] = source; else state.dataSources.push(source);
  });
}

/** 按 resultType（或工具名）分派业务结果卡片；未知类型返回空串，不渲染空壳卡片。 */
function businessResultMarkup(tool, data) {
  data = envelopeData(data);
  if (!data || typeof data !== "object") return "";
  if (data.resultType === "alert_problem_list" || tool === "view_device_alerts") return alertBusinessResult(data);
  if (data.resultType === "dashboard" || tool === "create_business_dashboard" || tool === "create_dashboard_from_queries") return dashboardBusinessResult({ ...data, previewPath: data.previewPath || data.href });
  if (data.resultType === "resource_resolution" || tool === "list_business_data_sources") rememberBusinessSources(data);
  return "";
}

/** 汇总一段助手消息里的全部产物（artifact part + 工具结果内的 artifacts/artifact_id），按 id 去重。 */
function assistantArtifacts(parts) {
  const artifacts = [];
  const add = (value) => {
    const artifact = normalizeArtifact(value);
    if (artifact && !artifacts.some((item) => item.id === artifact.id)) artifacts.push(artifact);
  };
  (parts || []).forEach((part) => {
    if (part.type === "artifact") add(part.artifact);
    if (part.type !== "tool_result") return;
    const result = part.result || {};
    (result.artifacts || []).forEach(add);
    const data = result.data || {};
    (data.artifacts || []).forEach(add);
    if (data.artifact_id) add({
      id: data.artifact_id,
      type: inferTypeFromName(data.name || ""),
      name: data.name || "生成文件",
      download_url: data.download_url,
      preview_url: data.preview_url,
    });
  });
  return artifacts;
}

/** 复制文本（走平台剪贴板模块，兼容无 navigator.clipboard 的环境）。 */
async function copyChatText(text) {
  const { copyText } = await import("/assets/platform/clipboard.js");
  await copyText(text);
}

/**
 * 组装一条助手消息的 DOM（历史与流式结束时共用）。
 * @param {Array} parts 后端消息片段（text/tool_call/tool_result/citation/error/business_question）
 * @param {string} fallback parts 无正文时的兜底文本（如流式中断留下的 content）
 * @param {string} precedingPrompt 上一条用户提问，供「编辑并重发」按钮使用
 * @param {boolean} questionActive 兼容旧调用；业务追问不再创建交互卡片
 * @returns {HTMLElement} .msg-row 节点
 * 已由 markdown 渲染过的产物不再重复出卡片（renderedArtifactIds 去重）。
 */
function assistantRow(parts, fallback, precedingPrompt = "", questionActive = true) {
  const row = document.createElement("div");
  row.className = "msg-row";
  row.innerHTML = "<div class='msg-avatar'>企</div><div class='msg-body'></div>";
  const bodyEl = row.querySelector(".msg-body");
  let html = "";
  let hasText = false;
  let copyText = "";
  let businessHtml = "";
  const artifacts = assistantArtifacts(parts);
  const groupedProcess = typeof dshChat !== "undefined" && dshChat?.enabled();
  const renderedArtifactIds = new Set();
  const verifiedReports = new Map((parts || []).filter(part => part.type === "business_result" && part.data?.reportId).map(part => [part.data.reportId, part.data]));
  const renderedReports = new Set();
  const invalidReports = new Set((parts || []).filter(part => part.type === "delivery_check" && part.status !== "ready").map(part => part.report_id));
  const question = (parts || []).find(p => p.type === "business_question" && !/继续(?:处理|查询).{0,30}还是.{0,15}(?:查看|看).{0,8}结果/.test(String(p.question || "")));
  (parts || []).forEach((p) => {
    if (question && p.type === "text" && String(p.text || "").trim() === question.question) { copyText += "\n" + question.question; hasText = true; return; }
    if (p.type === "text" && p.text && String(p.text).trim()) { html += "<div class='assistant-content'>" + mdRender(p.text, artifacts, renderedArtifactIds) + "</div>"; copyText += (copyText ? "\n" : "") + p.text; hasText = true; }
    else if (p.type === "tool_call" && !groupedProcess) { html += "<div class='tool-chip'><span class='" + (p.status === "running" ? "spin" : p.status === "failed" ? "err" : "ok") + "'></span> " + (p.status === "running" ? "正在调用 " : p.status === "failed" ? "调用未完成 " : "已调用 ") + esc(toolLabel(p.name)) + "</div>"; }
    else if (p.type === "tool_result") {
      const r = p.result || {};
      if (r.status === "waiting_input" || r.error_summary === "等待你的选择") html += "<div class='tool-chip'>需要补充查询范围，请直接在输入框说明</div>";
      else if (r.status === "failed" && !groupedProcess) html += "<div class='tool-chip err'><span class='err'></span> " + esc(customerChatError(r.error_summary)) + "</div>";
      const reportId = r.data?.reportId || r.data?.dashboard_id;
      const businessResult = r.status !== "failed" && !(reportId && (verifiedReports.has(reportId) || renderedReports.has(reportId) || invalidReports.has(reportId))) ? businessResultMarkup(p.tool, r.data) : "";
      if (businessResult) {
        if (reportId) renderedReports.add(reportId);
        if (groupedProcess) businessHtml += businessResult; else html += businessResult;
        copyText += (copyText ? "\n" : "") + String((r.data && r.data.title) || "业务结果");
      }
    }
    else if (p.type === "business_result" && p.data && !renderedReports.has(p.data.reportId) && !invalidReports.has(p.data.reportId)) {
      const markup = businessResultMarkup("", p.data);
      if (markup) { businessHtml += markup; renderedReports.add(p.data.reportId); }
    }
    else if (p.type === "citation") { const c = p.citation || {}; html += "<span class='citation-chip'>" + esc(c.file_name || "来源") + "</span>"; }
    else if (p.type === "error") { html += "<div style='color:#d1453b;font-size:12.5px;margin-top:6px;'>" + esc(p.message) + "</div>"; }
  });
  if (!hasText && fallback) { html += "<div class='assistant-content'>" + mdRender(fallback, artifacts, renderedArtifactIds) + "</div>"; copyText = fallback; }
  const unrenderedArtifacts = artifacts.filter((artifact) => !renderedArtifactIds.has(artifact.id));
  if (unrenderedArtifacts.length) html += "<div class='artifact-list'>" + unrenderedArtifacts.map((a) => artifactCard(a)).join("") + "</div>";
  // 结论和交付文件先出现，查询明细随后展示；工具发生顺序不再决定最终回答的阅读顺序。
  html += businessHtml;
  html += "<div class='message-actions'><button class='message-action' data-msg-action='copy' title='复制'>" + IC.copy + "</button><button class='message-action' data-msg-action='like' title='有帮助'>" + IC.thumb + "</button><button class='message-action' data-msg-action='retry' title='编辑并重发这条问题'>" + IC.retry + "</button></div>";
  bodyEl.innerHTML = html;
  if (question) appendBusinessQuestion(bodyEl, question, questionActive);
  bindArtifactImages(bodyEl);
  const copyBtn = bodyEl.querySelector("[data-msg-action='copy']");
  copyText = copyText.replace(/(?:^|\n)<!-- followups:[\s\S]*$/, "").trimEnd();
  if (copyBtn) copyBtn.onclick = async () => {
    try { await copyChatText(copyText || ""); toast("已复制回答"); }
    catch (error) { toast(error.message || "复制失败，请选中文字后手动复制"); }
  };
  const likeBtn = bodyEl.querySelector("[data-msg-action='like']");
  if (likeBtn) likeBtn.onclick = () => { likeBtn.style.background = "#ececec"; toast("反馈已记录"); };
  const retryBtn = bodyEl.querySelector("[data-msg-action='retry']");
  if (retryBtn) retryBtn.onclick = () => retryLastPrompt(precedingPrompt);
  bodyEl.querySelectorAll("[data-artifact-use]").forEach((button) => {
    button.onclick = () => useArtifactAsInput(button.dataset.artifactUse);
  });
  bodyEl.querySelectorAll("[data-select-source]").forEach((button) => {
    button.onclick = () => {
      const id = button.dataset.selectSource;
      if (state.datasourceIds.includes(id)) {
        state.datasourceIds = state.datasourceIds.filter((item) => item !== id);
        button.classList.remove("selected"); button.textContent = "用于本次对话";
      } else {
        if (state.datasourceIds.length >= 8) return toast("一次最多选择 8 个数据源");
        state.datasourceIds.push(id);
        button.classList.add("selected"); button.textContent = "已选择";
      }
      renderAttachChips();
    };
  });
  bodyEl.querySelectorAll("[data-reload-business-sources]").forEach((button) => {
    button.onclick = () => openDataSourcePicker(button);
  });
  return row;
}

/** 把产物转成「继续使用」的输入附件（后端 /use 会复制为普通文件，原产物仍保留）。 */
async function useArtifactAsInput(artifactId) {
  if (!artifactId) return;
  try {
    const file = await API.post("/api/artifacts/" + artifactId + "/use", {});
    if (!state.fileIds.some((item) => item.id === file.id)) {
      state.fileIds.push({ id: file.id, name: file.name });
    }
    renderAttachChips();
    toast("已加入输入栏，可继续提问");
    const prompt = $("#prompt-textarea"); if (prompt) prompt.focus();
  } catch (error) {
    toast("无法继续使用：" + error.message);
  }
}

/** 把上一条（或指定）提问回填到输入框，交给用户确认后再发送，不直接重发。 */
function retryLastPrompt(selectedPrompt = "") {
  if (state.streaming) return;
  const msgs = (state.current && state.current.messages) || [];
  const lastUser = [...msgs].reverse().find((m) => m.role === "user");
  const prompt = selectedPrompt || (lastUser && lastUser.content) || state.lastPrompt;
  if (!prompt) return toast("没有可重新生成的消息");
  const ta = $("#prompt-textarea"); ta.value = prompt; updateSendState(); ta.focus();
}
/**
 * 给站内受保护资源地址补上 workspace_id（并把历史遗留的 token 查询参数剔除）。
 * 只有 /api/files|artifacts/<id>/content|download 这类受保护路径需要；外链与非法协议返回 "#"。
 */
function authUrl(value) {
  try {
    const url = new URL(String(value || ""), location.origin);
    if (!["http:", "https:"].includes(url.protocol)) return "#";
    if (url.origin !== location.origin) return url.href;
    url.searchParams.delete("token");
    if (!url.searchParams.has("workspace_id")) {
      const workspace = localStorage.getItem("agent_workspace_id");
      if (workspace) url.searchParams.set("workspace_id", workspace);
    }
    return url.pathname + url.search + url.hash;
  } catch { return "#"; }
}
/**
 * 生成 <img> 的 src 属性片段。
 * 受保护文件不直接写 src，而是写 data-content-src，由 bindArtifactImages 用带鉴权头的 fetch 取回流再展示，
 * 避免把令牌暴露在 DOM/浏览器历史里。
 */
function artifactImageSource(url) {
  const path = new URL(url, location.origin);
  const protectedFile = path.origin === location.origin && /^\/api\/(?:files|artifacts)\/[A-Za-z0-9_-]+\/(?:download|content)$/.test(path.pathname);
  return (protectedFile ? "data-content-src='" : "src='") + esc(url) + "'";
}
// Download through headers so login credentials never enter DOM links or history.
document.addEventListener("click", async event => {
  const link = event.target.closest?.("a[href]");
  if (!link || event.defaultPrevented) return;
  const url = new URL(link.href, location.origin);
  if (url.origin !== location.origin || !/^\/api\/(?:files|artifacts)\/[A-Za-z0-9_-]+\/(?:download|content)$/.test(url.pathname)) return;
  event.preventDefault();
  if (link.dataset.contentLoading) return;
  link.dataset.contentLoading = "true";
  link.setAttribute("aria-busy", "true");
  const preview = url.pathname.endsWith("/content") ? window.open("", "_blank") : null;
  if (preview) { preview.opener = null; preview.document.title = "正在加载附件…"; }
  try {
    const { fetchContent, saveContent } = await import("/assets/platform/protected-content.js");
    const content = await fetchContent(url.href);
    if (preview && !preview.closed && /^image\//.test(content.blob.type)) {
      const blobUrl = URL.createObjectURL(content.blob);
      const image = preview.document.createElement("img");
      image.alt = content.name;
      image.style.maxWidth = "100%";
      image.onload = image.onerror = () => URL.revokeObjectURL(blobUrl);
      image.src = blobUrl;
      preview.document.title = content.name;
      preview.document.body.replaceChildren(image);
    } else {
      preview?.close();
      saveContent(content);
    }
  } catch (error) { preview?.close(); toast(error.message); }
  finally { delete link.dataset.contentLoading; link.removeAttribute("aria-busy"); }
});

/** 单张产物卡片：名字 + 类型 + 继续使用 + 下载；图片类额外给出预览图。 */
function artifactCard(a, descriptionHtml = "") {
  const artifact = normalizeArtifact(a);
  if (!artifact) return "";
  const url = authUrl(artifact.download_url);
  const previewUrl = artifact.preview_url ? authUrl(artifact.preview_url) : "";
  const imgPreview = previewUrl ? "<figure class='artifact-card-preview'><a href='" + previewUrl + "' target='_blank' rel='noopener'><img class='artifact-preview-img' data-artifact-image " + artifactImageSource(previewUrl) + " alt='" + esc(artifact.name) + "' loading='lazy' decoding='async'></a></figure>" : "";
  return "<div class='artifact-result' data-artifact-id='" + esc(artifact.id) + "'>" + imgPreview + "<div class='artifact-card'>" + artIcon(artifact.type) +
    "<div style='flex:1;min-width:0;'><div class='artifact-name' title='" + esc(artifact.name) + "'>" + esc(artifact.name) + "</div><div class='artifact-type'>" + (TYPE_LABEL[artifact.type] || artifact.type) + "</div>" + (descriptionHtml ? "<div class='artifact-description'>" + descriptionHtml + "</div>" : "") + "</div>" +
    "<button class='btn-sm btn-secondary' data-artifact-use='" + esc(artifact.id) + "' type='button'>继续使用</button>" +
    "<a class='btn-sm btn-secondary' href='" + url + "' target='_blank' aria-label='下载 " + esc(artifact.name) + "' title='下载'>" + IC.download + "</a></div></div>";
}
/**
 * 为受保护图片做鉴权加载，并在失败时给出「可下载原文件」的可读提示（不留破图）。
 * 重新渲染时会因 data-content-loading 标记跳过重复请求。
 */
function bindArtifactImages(root) {
  (root || document).querySelectorAll("img[data-artifact-image]").forEach((img) => {
    const failed = () => {
      const figure = img.closest(".md-image-figure, .artifact-card-preview");
      if (!figure || figure.classList.contains("is-error")) return;
      figure.classList.add("is-error");
      const fallback = document.createElement("span");
      fallback.className = "artifact-image-error";
      fallback.setAttribute("role", "status");
      fallback.textContent = "图片暂不可预览，可下载原文件";
      figure.appendChild(fallback);
    };
    img.addEventListener("error", failed, { once: true });
    if (img.dataset.contentSrc && !img.dataset.contentLoading) {
      img.dataset.contentLoading = "true";
      import("/assets/platform/protected-content.js").then(({ fetchContent }) => fetchContent(img.dataset.contentSrc))
        .then(({ blob }) => {
          if (!img.isConnected) return;
          if (!blob.type.startsWith("image/")) throw new Error("附件不是图片");
          const url = URL.createObjectURL(blob);
          img.addEventListener("load", () => URL.revokeObjectURL(url), { once: true });
          img.addEventListener("error", () => URL.revokeObjectURL(url), { once: true });
          img.src = url;
        }).catch(failed);
    } else if (img.getAttribute("src") && img.complete && !img.naturalWidth) failed();
  });
}
/** 流式输出前先插入一条空的助手消息行，返回其 .msg-body 供后续增量写入。 */
function appendAssistantRow() {
  const row = document.createElement("div");
  row.className = "msg-row";
  row.innerHTML = "<div class='msg-avatar'>企</div><div class='msg-body'></div>";
  $("#messages").appendChild(row);
  scrollToBottom({ force: false });
  return row.querySelector(".msg-body");
}
function scrollToBottom({ force = true } = {}) {
  const root = $("#scroll-root"); if (!root) return;
  const tracking = root._chatScrolling;
  if (tracking && force) { tracking.following = true; tracking.direction = 0; }
  if (tracking && !tracking.following) return;
  if (tracking?.frame != null) cancelAnimationFrame(tracking.frame);
  const frame = requestAnimationFrame(() => {
    if (tracking) tracking.frame = null;
    // 帧排队之后用户也可能开始上滑，执行时再次核对意图，不能把滚轮动作覆盖掉。
    if (root.isConnected && (!tracking || tracking.following)) root.scrollTop = root.scrollHeight;
  });
  if (tracking) tracking.frame = frame;
}

/**
 * 对话区「是否跟随到底部」的判定。
 * 用 ResizeObserver 观察滚动容器与消息线程：内容增高时只有用户本来就贴着底部才自动滚到底，
 * 用户上滑查看历史时不打断（滚动事件里要排除 resize 触发的伪滚动）。
 */
function setupChatScrolling() {
  const root = $("#scroll-root"), thread = $("#thread");
  if (!root || !thread || root._chatScrolling) return;
  // 底部交付卡不能成为浏览器的滚动锚点，否则新增正文会偷偷带动阅读位置。
  root.style.overflowAnchor = "none";
  const tracking = root._chatScrolling = { following: true, frame: null, direction: 0 };
  let height = root.scrollHeight, viewport = root.clientHeight, touchY;
  root.addEventListener("wheel", event => {
    tracking.direction = Math.sign(event.deltaY);
    if (event.deltaY < 0) tracking.following = false;
  }, { passive: true });
  root.addEventListener("chat-preview-scroll-intent", event => {
    tracking.direction = Math.sign(Number(event.detail?.direction) || 0);
    if (tracking.direction < 0) tracking.following = false;
  });
  root.addEventListener("pointerdown", event => {
    tracking.direction = event.target.closest("[data-dsh-question]") ? -1 : 0;
    if (tracking.direction < 0) tracking.following = false;
  }, { passive: true });
  root.addEventListener("focusin", event => {
    if (event.target.closest("[data-dsh-question]")) { tracking.following = false; tracking.direction = -1; }
  });
  root.addEventListener("touchstart", event => { touchY = event.touches[0]?.clientY; tracking.direction = 0; }, { passive: true });
  root.addEventListener("touchmove", event => {
    const current = event.touches[0]?.clientY;
    if (current != null && touchY != null) {
      tracking.direction = Math.sign(touchY - current);
      if (current > touchY + 1) tracking.following = false;
    }
    touchY = current;
  }, { passive: true });
  root.addEventListener("keydown", event => {
    if (event.target.closest("input, textarea, [contenteditable=true]")) return;
    if (["ArrowUp", "PageUp", "Home"].includes(event.key)) { tracking.following = false; tracking.direction = -1; }
    if (["ArrowDown", "PageDown", "End"].includes(event.key)) tracking.direction = 1;
  });
  root.addEventListener("scroll", () => {
    // 触控板的小幅上滑也表示离开跟随，不能因为仍在底部阈值内又立刻拉回。
    if (tracking.direction < 0) { tracking.following = false; return; }
    // 内容或输入框改变尺寸时保留原来的跟随意图，避免把浏览器布局调整误判为手动滚动。
    if ((height === root.scrollHeight && viewport === root.clientHeight) || tracking.direction > 0) {
      tracking.following = root.scrollHeight - root.scrollTop - root.clientHeight < 100;
      if (tracking.following) tracking.direction = 0;
    }
  }, { passive: true });
  if (typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(() => {
    height = root.scrollHeight;
    viewport = root.clientHeight;
    if (tracking.following) scrollToBottom({ force: false });
  });
  observer.observe(root);
  observer.observe(thread);
}

// ---------------- 附件与成品插件 ----------------
/**
 * 同步输入区的业务上下文按钮：数据源按钮按 datasources.view 权限显示，看板模板按钮需 templates.view + dashboards.create。
 * 无权限时隐藏而不是禁用，避免暴露未授权的功能入口。
 */
function updateComposerBusinessContext() {
  const permissions = new Set(state.me?.permissions || []);
  const sourceButton = $("#business-source-btn");
  const templateButton = $("#dashboard-template-btn");
  if (sourceButton) {
    sourceButton.classList.toggle("hidden", !permissions.has("datasources.view"));
    const selectedNames = state.datasourceIds.map((id) => state.dataSources.find((source) => source.id === id)?.name).filter(Boolean);
    const label = !state.datasourceIds.length ? "选择" : state.datasourceIds.length === 1 ? (selectedNames[0] || "已选 1 项") : "已选 " + state.datasourceIds.length + " 项";
    const target = $("#business-source-label"); if (target) target.textContent = label;
    sourceButton.classList.toggle("active", state.datasourceIds.length > 0);
    sourceButton.title = state.datasourceIds.length ? "本次使用：" + (selectedNames.join("、") || state.datasourceIds.join("、")) : "选择本次对话可查询的数据源";
  }
  if (templateButton) {
    const visible = permissions.has("templates.view") && permissions.has("dashboards.create");
    templateButton.classList.toggle("hidden", !visible);
    const selected = state.dashboardTemplates.find((template) => template.id === state.dashboardTemplateId);
    const label = state.dashboardTemplateMode === "manual" ? (selected?.name || "选择模板") : "自动参考模板";
    const target = $("#dashboard-template-label"); if (target) target.textContent = label;
    templateButton.classList.toggle("active", state.dashboardTemplateMode === "manual");
    templateButton.title = state.dashboardTemplateMode === "manual" ? "已选择：" + label : label;
  }
}

/** 渲染输入框上方的已选附件/知识库/数据源/插件标签，并接上移除按钮（移除后立即刷新发送按钮可用态）。 */
function renderAttachChips() {
  const wrap = $("#composer-attachments");
  let html = "";
  state.fileIds.forEach((f) => { html += "<span class='attach-chip'>" + esc(f.name) + "<span class='x' data-k='file-" + f.id + "'>&#10005;</span></span>"; });
  state.kbIds.forEach((id) => { const kb = state.knowledge.find((k) => k.id === id); html += "<span class='attach-chip'>知识库 · " + esc(kb ? kb.name : id) + "<span class='x' data-k='kb-" + id + "'>&#10005;</span></span>"; });
  state.datasourceIds.forEach((id) => { const source = state.dataSources.find((item) => item.id === id); html += "<span class='attach-chip datasource-chip'><span class='chip-status'></span>数据源 · " + esc(source ? source.name : id) + "<span class='x' data-k='ds-" + id + "'>&#10005;</span></span>"; });
  if (state.pluginId) { const p = state.plugins.find((x) => x.id === state.pluginId); html += "<span class='attach-chip'>插件 · " + esc(p ? p.name : state.pluginId) + "<span class='x' data-k='plugin'>&#10005;</span></span>"; }
  if (dshChat?.enabled()) dshChat.skillLabels().forEach(skill => { html += "<span class='attach-chip'>技能 · " + esc(skill.name) + "<span class='x' data-k='skill-" + esc(skill.id) + "'>&#10005;</span></span>"; });
  if (dshChat?.enabled() && state.dshTask) html += "<span class='attach-chip'>继续任务 · " + esc(state.dshTask.title) + "<span class='x' data-k='dsh-task'>&#10005;</span></span>";
  wrap.innerHTML = html;
  wrap.querySelectorAll(".x").forEach((x) => {
    x.onclick = () => {
      const k = x.dataset.k;
      if (k.startsWith("file-")) state.fileIds = state.fileIds.filter((f) => f.id !== k.slice(5));
      if (k.startsWith("kb-")) state.kbIds = state.kbIds.filter((id) => id !== k.slice(3));
      if (k.startsWith("ds-")) state.datasourceIds = state.datasourceIds.filter((id) => id !== k.slice(3));
      if (k === "plugin") { state.pluginId = null; state.pluginVersionId = null; }
      if (k.startsWith("skill-")) state.skillIds = (state.skillIds || []).filter(id => id !== k.slice(6));
      if (k === "dsh-task") state.dshTask = null;
      renderAttachChips(); updateSendState();
    };
  });
  updateComposerBusinessContext();
  updateSendState();
}
/** 渲染「PPT 生成」预设按钮（仅在 PPT 模式激活时显示），点击打开模板选择器。 */
async function renderPresets() {
  const box = $("#preset-chips");
  if (!box) return;
  const row = box.closest(".preset-row");
  row?.classList.toggle("hidden", !state.pptActive);
  if (!state.pptActive) { box.innerHTML = ""; return; }
  const selected = state.templates.find((t) => t.id === state.pptTemplateId);
  const pageSuffix = state.pptActive && state.pptPageCount !== "auto" ? " · " + state.pptPageCount + "页" : "";
  const suffix = state.pptActive && selected ? " · " + selected.name + pageSuffix : "";
  box.innerHTML = "<button class='preset-chip" + (state.pptActive ? " active" : "") + "' id='ppt-preset'><span class='template-dot'></span><span>PPT 生成" + esc(suffix) + "</span><span aria-hidden='true'>⌄</span></button>";
  $("#ppt-preset").onclick = openTemplatePicker;
}

/** 页数下拉项按需生成一次（3–30、35、40、50），用 dataset.ready 防止重复插入。 */
function ensurePageCountOptions() {
  const select = $("#template-page-count");
  if (!select || select.dataset.ready === "1") return;
  const values = [...Array.from({ length: 28 }, (_, index) => index + 3), 35, 40, 50];
  select.insertAdjacentHTML("beforeend", values.map((value) => "<option value='" + value + "'>" + value + " 页</option>").join(""));
  select.dataset.ready = "1";
}

/** 拉取 PPT 模板；接口失败时保留上一次真实目录，禁止伪造可提交的模板 ID。 */
async function loadTemplates() {
  try {
    const r = await API.get("/api/presentation-templates");
    state.templates = r.items || [];
    state.templatesError = "";
  } catch (e) {
    state.templatesError = "模板目录暂时无法读取，请重试后再生成 PPT。";
  }
}

/** 按关键词把看板模板归入业务分组（模板未声明 category 时才用关键词猜，声明优先）。 */
function dashboardTemplateGroup(template) {
  const text = [template.id, template.name, template.description, ...(template.tags || [])].join(" ").toLowerCase();
  if (/财务|finance/.test(text)) return "财务";
  if (/设备|equipment/.test(text)) return "设备";
  if (/生产|车间|factory|production|workshop/.test(text)) return "生产";
  if (/质量|质检|quality/.test(text)) return "质量";
  if (/销售|订单|交易|sales|order|trade/.test(text)) return "销售";
  if (/仓储|库存|warehouse/.test(text)) return "仓储";
  if (template.category) return template.category;
  return "综合";
}

/** 模板来源标签：智慧云脑内置（lynx-report-screen）与自定义模板两类。 */
function dashboardTemplateSource(template) {
  if (template.sourceKind === "lynx-report-screen") return "智慧云脑模板";
  return "自定义模板";
}

/** 模板预览图地址：绝对地址/内联数据原样返回，相对文件名补成 /assets/analytics/ 下的静态资源。 */
function dashboardTemplatePreviewUrl(template) {
  const preview = String(template?.preview || "").trim();
  if (!preview) return "";
  if (/^(?:https?:|data:|blob:|\/)/i.test(preview)) return preview;
  return "/assets/analytics/" + preview.replace(/^\.\//, "");
}

/**
 * 拉取看板模板目录。
 * 手动选择的模板若已被删除/停用，自动降级回「动态适配」，避免带着失效 id 提交；
 * 失败时清空本地模板并向上抛，由调用方决定提示方式。
 */
async function loadDashboardTemplates() {
  try {
    const response = await API.get("/api/v1/report-templates");
    const rows = envelopeData(response);
    state.dashboardTemplates = Array.isArray(rows) ? rows : [];
    if (state.dashboardTemplateMode === "manual" && !state.dashboardTemplates.some((template) => template.id === state.dashboardTemplateId && template.enabled !== false)) {
      state.dashboardTemplateMode = "auto";
      state.dashboardTemplateId = null;
    }
  } catch (error) {
    state.dashboardTemplates = [];
    throw error;
  }
  updateComposerBusinessContext();
}

/** 模板画布尺寸文案（无画布配置时显示「自适应」）。 */
function dashboardTemplateDimensions(template) {
  const canvas = (template.content || {}).canvas || {};
  return canvas.width && canvas.height ? canvas.width + "×" + canvas.height : "自适应";
}

/**
 * 用模板的 widgets 坐标生成纯 CSS 骨架缩略图（不加载真实大屏，避免触发查询）。
 * 坐标按画布宽高换算成百分比并夹在 0–100 内；最多 50 个组件，防止极端模板拖慢列表。
 */
function dashboardTemplateSkeleton(template) {
  const content = template?.content || {};
  const canvas = content.canvas || {};
  const width = Math.max(1, Number(canvas.width) || 1920);
  const height = Math.max(1, Number(canvas.height) || 1080);
  const widgets = Array.isArray(content.widgets) ? content.widgets.slice(0, 50) : [];
  if (!widgets.length) return "<span class='dashboard-template-no-visual'>源记录无可用视觉配置</span>";
  const canvasStyle = "background-color:" + esc(canvas.backgroundColor || "#07111f") + ";" + (canvas.backgroundImage ? "background-image:url('" + esc(String(canvas.backgroundImage).replace(/'/g, "%27")) + "');background-size:cover;background-position:center;" : "");
  return "<span class='dashboard-template-skeleton" + (template.sourceKind === "sql-sheet-report" ? " sheet" : "") + "' style=\"" + canvasStyle + "\">" + widgets.map((widget) => {
    const left = Math.max(0, Math.min(100, Number(widget.x || 0) / width * 100));
    const top = Math.max(0, Math.min(100, Number(widget.y || 0) / height * 100));
    const boxWidth = Math.max(1.5, Math.min(100 - left, Number(widget.w || 1) / width * 100));
    const boxHeight = Math.max(2, Math.min(100 - top, Number(widget.h || 1) / height * 100));
    const role = /Stats|Number|Count/i.test(widget.component || "") ? "metric" : /Table|Scroll/i.test(widget.component || "") ? "table" : /Text/i.test(widget.component || "") ? "text" : "chart";
    return "<i class='" + role + "' style='left:" + left.toFixed(3) + "%;top:" + top.toFixed(3) + "%;width:" + boxWidth.toFixed(3) + "%;height:" + boxHeight.toFixed(3) + "%' title='" + esc(widget.componentName || widget.component || "组件") + "'></i>";
  }).join("") + "</span>";
}

/**
 * 渲染看板模板选择网格（按草稿态 draft 渲染，点「使用」才生效，取消不影响已选模板）。
 * 关键词同时匹配 id/名称/描述/分组/标签；停用或源记录缺失的模板置灰不可选，并给出具体原因。
 */
function renderDashboardTemplatePicker() {
  const grid = $("#dashboard-template-grid");
  if (!grid) return;
  const query = String(state.dashboardTemplateQuery || "").trim().toLowerCase();
  const category = state.dashboardTemplateCategory;
  const rows = state.dashboardTemplates.filter((template) => {
    const group = dashboardTemplateGroup(template);
    const searchable = [template.id, template.name, template.description, group, ...(template.tags || [])].join(" ").toLowerCase();
    return (!query || searchable.includes(query)) && (category === "全部" || group === category);
  });
  if (!rows.length) {
    const emptyCatalog = !state.dashboardTemplates.length;
    grid.innerHTML = "<div class='dashboard-template-empty'><strong>" + (emptyCatalog ? "还没有可用的大屏模板" : "没有匹配的模板") + "</strong><span>" + (emptyCatalog ? "可先从现有数字大屏创建模板，再回到这里选择" : "调整关键词或业务域后重试") + "</span>" + (emptyCatalog ? "<a href='/dashboards'>打开数字大屏</a>" : "") + "</div>";
  } else {
    grid.innerHTML = rows.map((template) => {
      const selected = state.dashboardTemplateDraftMode === "manual" && state.dashboardTemplateDraftId === template.id;
      const unavailable = template.enabled === false;
      const preview = dashboardTemplatePreviewUrl(template);
      const tags = (template.tags || []).slice(0, 2).map((tag) => "<span>" + esc(tag) + "</span>").join("");
      const sourceStatus = unavailable ? (template.sourceDeleted ? "源记录已删除" : template.sourceEmpty ? "源配置为空" : "模板已停用") : (template.styleLocked ? "视觉锁定" : "可用");
      return "<button type='button' class='dashboard-template-card" + (selected ? " selected" : "") + (unavailable ? " unavailable" : "") + "' data-dashboard-template-id='" + esc(template.id) + "' aria-pressed='" + (selected ? "true" : "false") + "'" + (unavailable ? " disabled" : "") + ">" +
        "<span class='dashboard-template-preview'>" + (preview ? "<img src='" + esc(preview) + "' alt='" + esc(template.name) + "模板预览' loading='lazy'>" : dashboardTemplateSkeleton(template)) + "<em>" + esc(template.id) + "</em><b>" + esc(sourceStatus) + "</b></span>" +
        "<span class='dashboard-template-card-head'><strong>" + esc(template.name) + "</strong><b>" + esc(dashboardTemplateGroup(template)) + "</b></span>" +
        "<span class='dashboard-template-description'>" + esc(template.description || template.summary || "保留原始布局、配色和组件结构") + "</span>" +
        "<span class='dashboard-template-facts'><span>" + esc(dashboardTemplateSource(template)) + "</span><span>" + esc(String(template.componentCount || ((template.content || {}).widgets || []).length || 0)) + " 组件</span><span>" + esc(dashboardTemplateDimensions(template)) + "</span>" + tags + "</span></button>";
    }).join("");
    grid.querySelectorAll("[data-dashboard-template-id]").forEach((button) => {
      button.onclick = () => {
        state.dashboardTemplateDraftMode = "manual";
        state.dashboardTemplateDraftId = button.dataset.dashboardTemplateId;
        renderDashboardTemplatePicker();
      };
    });
    grid.querySelectorAll("img").forEach((image) => { image.onerror = () => image.closest(".dashboard-template-preview")?.classList.add("preview-error"); });
  }
  $("#dashboard-template-strategy")?.querySelectorAll("[data-dashboard-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.dashboardMode === state.dashboardTemplateDraftMode);
  });
  const selected = state.dashboardTemplates.find((template) => template.id === state.dashboardTemplateDraftId);
  const summary = $("#dashboard-template-summary");
  if (summary) {
    if (state.dashboardTemplateDraftMode === "manual") summary.innerHTML = selected ? "<strong>" + esc(selected.name) + "</strong><span>" + esc(dashboardTemplateSource(selected)) + " · " + esc(dashboardTemplateDimensions(selected)) + " · 只生成 SQL，不修改模板布局、组件与样式</span>" : "<strong>请选择一套模板</strong><span>只生成 SQL，保留所选模板的原有样式</span>";
    else summary.innerHTML = "<strong>自动参考模板</strong><span>根据需求匹配现有模板，参考其布局与样式生成真实业务报表</span>";
  }
  const use = $("#dashboard-template-use");
  if (use) use.disabled = state.dashboardTemplateDraftMode === "manual" && (!selected || selected.enabled === false);
}

/**
 * 打开看板模板选择弹窗：把当前生效的模板/策略复制成草稿，按目录动态生成业务域筛选项。
 * 目录为空时才请求后端；加载失败在弹窗内给出「重新加载」，不关闭弹窗。
 */
async function openDashboardTemplatePicker() {
  const modal = $("#dashboard-template-modal");
  if (!modal) return;
  state.dashboardTemplateDraftMode = state.dashboardTemplateMode === "manual" ? "manual" : "auto";
  state.dashboardTemplateDraftId = state.dashboardTemplateId;
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  const grid = $("#dashboard-template-grid");
  if (grid && !state.dashboardTemplates.length) grid.innerHTML = "<div class='dashboard-template-empty'><span class='spin'></span><strong>正在读取智慧云脑 Cloud Brain 大屏模板目录</strong></div>";
  try {
    if (!state.dashboardTemplates.length) await loadDashboardTemplates();
    const categorySelect = $("#dashboard-template-category");
    const categories = Array.from(new Set(state.dashboardTemplates.map(dashboardTemplateGroup))).sort();
    categorySelect.innerHTML = "<option value='全部'>全部业务域</option>" + categories.map((item) => "<option value='" + esc(item) + "'>" + esc(item) + "</option>").join("");
    categorySelect.value = categories.includes(state.dashboardTemplateCategory) ? state.dashboardTemplateCategory : "全部";
    renderDashboardTemplatePicker();
    requestAnimationFrame(() => $("#dashboard-template-search")?.focus());
  } catch (error) {
    if (grid) grid.innerHTML = "<div class='dashboard-template-empty error'><strong>模板目录加载失败</strong><span>" + esc(error.message) + "</span><button type='button' data-retry-dashboard-templates>重新加载</button></div>";
    grid?.querySelector("[data-retry-dashboard-templates]")?.addEventListener("click", openDashboardTemplatePicker);
  }
}

/** 关闭看板模板弹窗并恢复页面滚动。 */
function closeDashboardTemplatePicker() {
  $("#dashboard-template-modal")?.classList.add("hidden");
  document.body.style.overflow = "";
}

/** 确认使用草稿中选定的看板模板策略：手动模式必须选中模板，自动模式清空 template_id。 */
function useDashboardTemplate() {
  if (state.dashboardTemplateDraftMode === "manual" && !state.dashboardTemplateDraftId) return toast("请选择一套大屏模板");
  state.dashboardTemplateMode = state.dashboardTemplateDraftMode === "manual" ? "manual" : "auto";
  state.dashboardTemplateId = state.dashboardTemplateMode === "manual" ? state.dashboardTemplateDraftId : null;
  updateComposerBusinessContext();
  closeDashboardTemplatePicker();
  toast(state.dashboardTemplateMode === "manual" ? "已指定模板：只生成 SQL" : "已启用自动参考模板");
}

/** 打开 PPT 模板选择弹窗：草稿选中的模板在「使用」前不写回 state。 */
function openTemplatePicker() {
  const modal = $("#template-modal"); if (!modal) return;
  ensurePageCountOptions();
  state.pptTemplateDraft = state.pptTemplateId || null;
  $("#template-search-input").value = state.pptTemplateQuery;
  $("#template-source-filter").value = state.pptTemplateSource;
  renderTemplateFilters();
  renderTemplateGrid();
  $("#template-ratio").value = state.pptRatio;
  $("#template-page-count").value = state.pptPageCount;
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  requestAnimationFrame(() => { const input = $("#template-search-input"); if (input) input.focus(); });
}

/**
 * 渲染 PPT 模板分类筛选。preferred 里的顺序是产品定义的展示顺序，
 * 目录里出现但未列出的分类排在后面并按名称排序，保证新模板不会被藏起来。
 */
function renderTemplateFilters() {
  const holder = $("#template-category-filters"); if (!holder) return;
  const preferred = ["全部", "商务", "咨询", "数据", "产品", "科技", "技术", "研究", "医疗", "工业", "政务", "品牌", "文化", "可持续", "创意", "观点", "高端", "叙事", "极简"];
  const available = new Set(state.templates.map((template) => template.category || "通用"));
  const categories = ["全部", ...preferred.filter((item) => item !== "全部" && available.has(item)), ...Array.from(available).filter((item) => !preferred.includes(item)).sort()];
  holder.innerHTML = categories.map((category) => "<button class='template-filter-chip" + (state.pptTemplateCategory === category ? " active" : "") + "' data-template-category='" + esc(category) + "' type='button' aria-pressed='" + (state.pptTemplateCategory === category ? "true" : "false") + "'>" + esc(category) + "</button>").join("");
  holder.querySelectorAll("[data-template-category]").forEach((button) => {
    button.onclick = () => {
      state.pptTemplateCategory = button.dataset.templateCategory;
      renderTemplateFilters();
      renderTemplateGrid();
    };
  });
}

/**
 * 渲染 PPT 模板网格（关键词 + 分类 + 来源三重过滤）并统计内置/客户模板数量。
 * 预览图加载失败时就地降级为 CSS 占位预览，不留下破图图标。
 */
function renderTemplateGrid() {
  const grid = $("#template-grid"); if (!grid) return;
  if (state.templatesError && !state.templates.length) {
    grid.innerHTML = "<div class='template-empty template-load-error' role='alert'><strong>模板目录加载失败</strong><span>当前没有可提交的真实模板，请重试后再继续。</span><button id='template-load-retry' class='btn-sm btn-secondary' type='button'>重新加载</button></div>";
    $("#template-load-retry").onclick = async () => { await loadTemplates(); renderTemplateFilters(); renderTemplateGrid(); };
    $("#template-use").disabled = true;
    return;
  }
  const query = (state.pptTemplateQuery || "").trim().toLowerCase();
  const filtered = state.templates.filter((template) => {
    const sourceMatch = state.pptTemplateSource === "all" ||
      (state.pptTemplateSource === "custom" ? template.source === "custom" : template.source !== "custom");
    const categoryMatch = state.pptTemplateCategory === "全部" || (template.category || "通用") === state.pptTemplateCategory;
    const haystack = [
      template.name, template.category, template.description,
      template.layout_family, template.style_profile,
      ...(template.recommended_for || []), ...(template.layouts || [])
    ].join(" ").toLowerCase();
    return sourceMatch && categoryMatch && (!query || haystack.includes(query));
  });
  const count = $("#template-result-count");
  const builtinCount = state.templates.filter((template) => template.source !== "custom").length;
  const customCount = state.templates.length - builtinCount;
  if (count) count.textContent = "显示 " + filtered.length + " / " + state.templates.length + " · 内置 " + builtinCount + (customCount ? " · 我的 " + customCount : "");
  const selected = state.templates.find((template) => template.id === state.pptTemplateDraft);
  const summary = $("#template-selection-summary");
  if (summary) {
    const example = selected && selected.example_url ? "<a class='template-example-link' href='" + esc(safeHref(selected.example_url)) + "' target='_blank' rel='noopener'>下载 14 页完整示例</a>" : "";
    summary.innerHTML = selected ? "<strong>已选：" + esc(selected.name) + "</strong><span>" + esc(selected.description || "") + "</span>" + example : "请选择模板";
  }
  $("#template-use").disabled = !state.pptTemplateDraft;
  if (!filtered.length) {
    grid.innerHTML = "<div class='template-empty'><strong>没有找到匹配模板</strong><span>尝试更换关键词、分类或模板来源。</span><button id='template-filter-reset' class='btn-sm btn-secondary' type='button'>清除筛选</button></div>";
    $("#template-filter-reset").onclick = () => {
      state.pptTemplateQuery = "";
      state.pptTemplateCategory = "全部";
      state.pptTemplateSource = "all";
      $("#template-search-input").value = "";
      $("#template-source-filter").value = "all";
      renderTemplateFilters();
      renderTemplateGrid();
    };
    return;
  }
  grid.innerHTML = filtered.map((t) => {
    const p = t.preview || {};
    const style = "--preview-bg:" + esc(p.background || "#f4f4f4") + ";--preview-accent:" + esc(p.accent || "#333") + ";--preview-text:" + esc(p.text || "#111");
    const source = t.source === "custom" ? "<span class='template-source'>客户模板</span>" : "";
    const recommendations = (t.recommended_for || []).slice(0, 2).map((item) => "<span>" + esc(item) + "</span>").join("");
    const customStats = t.analysis ? "<span class='template-analysis'>" + (t.analysis.layout_count || 0) + " 个布局 · " + (t.analysis.slide_count || 0) + " 页样例</span>" : (t.demo_title ? "<span class='template-analysis'>14 页完整示例 · " + esc(t.demo_title) + "</span>" : "");
    const densityLabel = { airy: "留白", balanced: "均衡", dense: "高密度" }[t.density] || "";
    const imageUrl = p.image_url ? safeHref(p.image_url) : "";
    const previewBadge = t.source === "custom" ? "实际首屏" : "4页实览";
    const realImage = imageUrl ? "<img src='" + esc(imageUrl) + "' alt='" + esc(t.name + "真实 PPT 页面预览") + "' loading='lazy' decoding='async'><span class='template-real-badge'>" + previewBadge + "</span>" : "";
    return "<button class='template-card" + (state.pptTemplateDraft === t.id ? " selected" : "") + "' data-template='" + esc(t.id) + "' type='button' aria-pressed='" + (state.pptTemplateDraft === t.id ? "true" : "false") + "'><div class='template-preview" + (imageUrl ? " has-image" : "") + "' data-layout='" + esc(p.layout || t.layout_family || "executive") + "' style='" + style + "'>" + realImage + "<i></i><b></b><em></em></div><div class='template-card-heading'><strong>" + esc(t.name) + "</strong><span>" + esc(t.category || "通用") + "</span></div><small>" + esc(t.description || "") + "</small><div class='template-tags'>" + recommendations + (densityLabel ? "<span>" + densityLabel + "</span>" : "") + "</div>" + customStats + source + "</button>";
  }).join("");
  grid.querySelectorAll(".template-preview img").forEach((preview) => {
    preview.onerror = () => {
      const holder = preview.closest(".template-preview");
      if (holder) {
        holder.classList.remove("has-image");
        const badge = holder.querySelector(".template-real-badge");
        if (badge) badge.remove();
      }
      preview.remove();
    };
  });
  grid.querySelectorAll("[data-template]").forEach((card) => {
    card.onclick = () => { state.pptTemplateDraft = card.dataset.template; renderTemplateGrid(); $("#template-use").disabled = false; };
  });
}

/** 关闭 PPT 模板弹窗并恢复页面滚动。 */
function closeTemplatePicker() {
  const modal = $("#template-modal"); if (modal) modal.classList.add("hidden");
  document.body.style.overflow = "";
}

/**
 * 确认 PPT 模板选择：写回比例与页数（页数必须为 3–50 的整数，否则拒绝），并开启 PPT 模式。
 * 同时尽量挂上名称含 ppt/演示/汇报 的成品插件，作为生成执行体。
 */
function useSelectedTemplate() {
  if (!state.pptTemplateDraft) return;
  state.pptTemplateId = state.pptTemplateDraft;
  state.pptRatio = $("#template-ratio").value;
  state.pptPageCount = $("#template-page-count").value;
  if (state.pptPageCount !== "auto") {
    const requested = Number.parseInt(state.pptPageCount, 10);
    if (!Number.isInteger(requested) || requested < 3 || requested > 50) return toast("总页数必须在 3–50 页之间");
    state.pptPageCount = String(requested);
  }
  state.pptActive = true;
  const hit = state.plugins.find((p) => /ppt|演示|汇报/i.test(p.name || ""));
  if (hit) { state.pluginId = hit.id; state.pluginVersionId = hit.published_version_id || null; }
  closeTemplatePicker(); renderPresets(); renderAttachChips();
  const t = state.templates.find((x) => x.id === state.pptTemplateId);
  toast("已选择模板：" + (t ? t.name : state.pptTemplateId) + (state.pptPageCount === "auto" ? " · 自动页数" : " · " + state.pptPageCount + " 页"));
  $("#prompt-textarea").focus();
}

/**
 * 上传客户 PPT 模板：前端先做后缀与 50MB 大小校验，再提交后端解析。
 * 成功后自动切到「客户模板」筛选并选中新模板，上传按钮状态在 finally 中恢复。
 */
async function uploadCustomerTemplate(file) {
  if (!file) return;
  if (!/\.pptx$/i.test(file.name)) return toast("仅支持上传 .pptx 模板");
  if (file.size > 50 * 1024 * 1024) return toast("PPT 模板不得超过 50MB");
  const button = $("#template-upload-btn");
  button.disabled = true; button.textContent = "正在校验…";
  const form = new FormData();
  form.append("file", file);
  form.append("name", file.name.replace(/\.pptx$/i, ""));
  try {
    const result = await API.post("/api/presentation-templates/upload", form, true);
    const item = result.template;
    state.templates = [item, ...state.templates.filter((template) => template.id !== item.id)];
    state.pptTemplateDraft = item.id;
    state.pptTemplateQuery = "";
    state.pptTemplateCategory = "全部";
    state.pptTemplateSource = "custom";
    $("#template-search-input").value = "";
    $("#template-source-filter").value = "custom";
    renderTemplateFilters();
    renderTemplateGrid();
    $("#template-use").disabled = false;
    toast("客户模板已上传，可立即使用");
  } catch (error) {
    toast("模板上传失败：" + error.message);
  } finally {
    button.disabled = false; button.textContent = "上传客户模板";
    $("#template-upload-input").value = "";
  }
}

/** 逐个上传附件（串行，避免并发占用导致部分失败）；单个失败只提示并继续，最后汇总成功数量。 */
async function uploadFiles(files) {
  let n = 0;
  for (const f of files) {
    const fd = new FormData(); fd.append("file", f);
    try { const r = await API.post("/api/files", fd, true); state.fileIds.push({ id: r.id, name: r.name }); n++; }
    catch (e) { toast("上传失败：" + e.message); }
  }
  renderAttachChips();
  toast("已上传 " + n + " 个文件");
}
/**
 * 刷新发送按钮状态：流式进行中按钮变为「停止」且始终可点，否则需有文本或附件才可点；
 * 同时按内容自适应输入框高度（上限 180px）。
 */
function updateSendState() {
  if (typeof dshChat !== "undefined" && dshChat?.enabled()) { dshChat.updateComposer(); return; }
  renderChatModels();
  const t = $("#prompt-textarea"), btn = $("#composer-submit-button");
  if (!t || !btn) return;
  const has = (t.value || "").trim().length > 0 || state.fileIds.length > 0;
  btn.disabled = state.streaming ? false : !has;
  btn.classList.toggle("streaming", state.streaming);
  const sendIcon = btn.querySelector(".send-icon"), stopIcon = btn.querySelector(".stop-icon");
  if (sendIcon) sendIcon.classList.toggle("hidden", state.streaming);
  if (stopIcon) stopIcon.classList.toggle("hidden", !state.streaming);
  btn.setAttribute("aria-label", state.streaming ? "停止生成" : "发送");
  t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px";
}

// ---------------- 发送 (SSE) ----------------
/**
 * 发送一条消息并消费 SSE 流式回答（核心流程）。
 * 前置校验：生成中则按钮语义变为「停止」；模型列表未就绪或未选可用模型直接拒绝。
 * 首次发送且尚未建会话时，用 preserveComposerContext 建会话，保留用户已选的附件/模板/数据源。
 * 断线时只重连同一任务的 GET /events；用 SSE id 续传，绝不自动重发 POST。
 * done / 取消终态后同步服务端完整消息与附件，无法同步时保留已接收的正文和真实业务问题。
 */
async function sendMessage() {
  if (typeof dshChat !== "undefined" && dshChat?.enabled()) return sendDshMessage();
  if (state.openingChat) return toast("正在打开对话，请稍候…");
  if (state.streaming) { stopGeneration(); return; }
  const ta = $("#prompt-textarea");
  const content = ta.value.trim();
  if (!content && !state.fileIds.length) return;
  if (!state.modelsReady || state.modelsError) return toast('模型列表尚未加载，请点击模型旁的重试按钮');
  if (!state.chatModels.some(item => item.profile_name === state.modelProfile)) return toast('请选择一个可用的对话模型');
  // The user may select a data source, dashboard/PPT template, knowledge base,
  // plugin or files before the first message.  Auto-creating the backing
  // conversation must not erase those explicit choices.
  if (!state.current) await newChat({ preserveComposerContext: true });
  if (!state.current) return;
  const convId = state.current.id;
  const text = content || "请分析我上传的文件。";
  state.lastPrompt = text;
  const payload = {
    // 每次明确发送分配一次标识；网络重连继续原任务，不能生成新提交。
    request_id: "web-" + Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join(""),
    content: text,
    model_profile: state.modelProfile,
    file_ids: state.fileIds.map((f) => f.id),
    knowledge_base_ids: state.kbIds,
    datasource_ids: state.datasourceIds,
    plugin_version_id: state.pluginVersionId,
    template_id: state.pptActive ? state.pptTemplateId : null,
    dashboard_template_mode: state.dashboardTemplateMode,
    dashboard_template_id: state.dashboardTemplateMode === "manual" ? state.dashboardTemplateId : null,
    plugin_inputs: state.pptActive ? {template_id: state.pptTemplateId, aspect_ratio: state.pptRatio, page_count: state.pptPageCount} : {},
    mode: state.activeMode
  };
  if (PLACEHOLDER_CONVERSATION_TITLES.has(String(state.current.title || "").trim())) {
    payload.title = firstSentenceSummary(text);
    state.current.title = payload.title;
  }
  state.current.messages.push({ role: "user", content: text, parts: [{ type: "text", text: text }, ...state.fileIds.map((f) => ({ type: "file", file: { id: f.id, name: f.name } }))] });
  renderMessages();
  ta.value = ""; updateSendState();
  state.streaming = true;
  state.abortController = new AbortController();
  updateSendState();
  const body = appendAssistantRow();
  body.setAttribute("data-stream", "1");
  const toolMap = {};
  const stream = { body, toolMap, segments: [], active: null, timer: null, error: "", done: false };
  let sentMessageCount = state.current.messages.length;
  state.stopResultStatus = null;
  stream.previousRunId = state.current.lastRunId || null;
  stream.prompt = text;
  try {
    const resp = await fetch("/api/chat/conversations/" + convId + "/send", {
      method: "POST", headers: { "Content-Type": "application/json", "Authorization": "Bearer " + API.token,
        "X-Workspace-Id": localStorage.getItem("agent_workspace_id") || "" }, body: JSON.stringify(payload), signal: state.abortController.signal
    });
    if (!resp.ok || !resp.body) {
      const rawError = await resp.text();
      let detail = {}; try { detail = JSON.parse(rawError); } catch (_) {}
      const error = new Error(detail.message || detail.detail || rawError.slice(0, 300) || "无法建立对话连接");
      error.status = resp.status;
      if (resp.status === 409 && detail.run_id) {
        state.activeRunId = detail.run_id;
        sentMessageCount = Math.max(0, sentMessageCount - 1);
        if (!ta.value) ta.value = text;
        toast("前一项任务仍在处理，已接回进度；新输入已保留，完成后可再发送。");
      }
      throw error;
    }
    await followChatRun(resp, stream, convId, sentMessageCount, text);
  } catch (e) {
    if ((!e.status || e.status >= 500 || e.status === 409) && e.name !== "AbortError" && !stream.done) {
      // The POST may already have committed. Recover its run; never resubmit it.
      try { await followChatRun(null, stream, convId, sentMessageCount, text); }
      catch (error) { setChatStreamError(stream, error); }
    } else setChatStreamError(stream, e);
  } finally {
    await finishChatStream(stream, convId, sentMessageCount);
    loadConversations().catch(() => {});
  }
}

/**
 * 停止生成：调用后端取消接口，按返回状态区分「正在停止 / 已结束 / 无法确认」并如实提示。
 * 提示文案刻意区分三种情况——不能确定已停止时不允许说成「已停止」。
 */
async function stopGeneration() {
  if (typeof dshChat !== "undefined" && dshChat?.enabled()) return dshChat.cancel();
  if (!state.activeRunId) return toast("任务正在建立，请稍候再停止。");
  try {
    const result = await API.post("/api/chat/runs/" + state.activeRunId + "/cancel", {});
    if (result.status === "stopping") {
      toast("正在停止，已完成的结果会保留。");
    } else if (["failed", "cancelled", "completed"].includes(result.status)) {
      state.stopResultStatus = result.status;
      toast("任务已结束，正在同步已保存的结果。");
      state.abortController?.abort();
    } else {
      toast("暂时无法确认任务已停止，继续等待并同步同一任务的结果。");
    }
  } catch (error) { toast("停止请求未送达，请再试一次。"); }
}
/** 读取 SSE 流：按空行切帧（兼容 CRLF），逐帧交给 handleFrame；流末尾可能缺空行，需补处理一次。 */
async function readChatEvents(response, stream) {
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
  try {
    while (true) {
      const part = await reader.read();
      buffer += decoder.decode(part.value, { stream: !part.done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        handleFrame(buffer.slice(0, end), stream); buffer = buffer.slice(end + 2);
      }
      if (stream.done) {
        reader.cancel().catch(() => {});
        break;
      }
      if (part.done) break;
    }
    if (!stream.done && buffer.trim()) handleFrame(buffer, stream);
  } finally { reader.releaseLock(); }
}
/** Display reconnect/sync state without claiming that the underlying task failed. */
function chatConnectionNotice(stream, message) {
  let notice = stream.body.querySelector(".chat-connection-notice");
  if (!notice) {
    notice = document.createElement("div"); notice.className = "tool-chip chat-connection-notice";
    notice.setAttribute("role", "status"); stream.body.appendChild(notice);
  }
  notice.textContent = message;
}

function chatAbortError() { return new DOMException("已结束等待", "AbortError"); }
function waitChatReconnect(attempt, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(chatAbortError()); return; }
    const onAbort = () => { clearTimeout(timer); reject(chatAbortError()); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, Math.min(1000 * (2 ** Math.min(attempt, 4)), 15000));
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Read-only recovery requests have no conversation deadline and never repeat /send. */
async function fetchChatState(path, signal) {
  const response = await fetch(path, { headers: {
    "Authorization": "Bearer " + API.token, "X-Workspace-Id": localStorage.getItem("agent_workspace_id") || ""
  }, signal, cache: "no-store" });
  if (!response.ok) {
    const error = new Error([401, 403].includes(response.status) ? "登录或访问权限已失效，请重新登录后查看本次结果。" : response.status === 404 ? "当前会话或任务已不可访问。" : "连接暂时中断");
    error.status = response.status; throw error;
  }
  return response;
}
function chatRecoveryBlocked(error) { return [400, 401, 403, 404, 422].includes(error.status); }

/** Resume the same run from its last event id until done or explicit user cancellation. */
async function followChatRun(response, stream, conversationId, sentMessageCount, text) {
  const signal = state.abortController?.signal;
  let attempt = 0;
  while (!stream.done) {
    if (signal?.aborted) throw chatAbortError();
    try {
      if (response) {
        await readChatEvents(response, stream); response = null;
        if (stream.done) break;
      }
      chatConnectionNotice(stream, "连接暂时中断，正在恢复同一任务的进度；无需重新发送。");
      await waitChatReconnect(attempt++, signal);
      if (!state.activeRunId) {
        const detail = await (await fetchChatState("/api/chat/conversations/" + conversationId, signal)).json();
        const run = detail.last_run;
        const accepted = detail.messages?.slice(sentMessageCount - 1).some(message => message.role === "user" && message.content === text);
        if (!run?.id || run.id === stream.previousRunId || !accepted) {
          const error = new Error("尚未确认消息已送达，输入内容已保留；请查看会话记录后再发送。");
          error.status = 409; error.notAccepted = true; throw error;
        }
        state.activeRunId = run.id;
      }
      response = await fetchChatState("/api/chat/runs/" + encodeURIComponent(state.activeRunId) + "/events?after=" + (stream.lastEventId || 0), signal);
      stream.body.querySelector(".chat-connection-notice")?.remove();
    } catch (error) {
      response = null;
      if (error.name === "AbortError" || chatRecoveryBlocked(error) || error.notAccepted) throw error;
      chatConnectionNotice(stream, "网络尚未恢复，正在等待连接；本次任务不会重复提交。");
    }
  }
}

function setChatStreamError(stream, error) {
  if (!state.activeRunId && stream.prompt) {
    const input = $("#prompt-textarea");
    if (input && !input.value) input.value = stream.prompt;
  }
  if (error.name === "AbortError") {
    if (state.stopResultStatus === "completed") { stream.done = true; return; }
    stream.error = state.stopResultStatus === "cancelled" ? "已停止生成，已完成的结果仍然保留。" : "本次等待已结束，正在核对已保存的结果。";
  } else stream.error = error.message || "暂时无法连接，请重新打开会话查看已保存的结果。";
}

/** Synchronize saved attachments/questions as well as text before releasing the send lock. */
async function finishChatStream(stream, conversationId, sentMessageCount) {
  flushStreamMarkdown(stream);
  let saved = false, attempt = 0;
  // A terminal cancel response also needs one authoritative refresh with a fresh signal.
  const signal = state.abortController?.signal.aborted ? undefined : state.abortController?.signal;
  try {
    while (stream.done || state.stopResultStatus || state.activeRunId) {
      try {
        const detail = await (await fetchChatState("/api/chat/conversations/" + conversationId, signal)).json();
        if (state.current?.id === conversationId) {
          if (detail.messages?.slice(sentMessageCount).some(message => message.role === "assistant")) {
            state.current.messages = detail.messages; saved = true;
          }
          state.current.lastRunId = detail.last_run?.id || state.activeRunId;
          if (detail.title) state.current.title = detail.title;
        }
        break;
      } catch (error) {
        if (chatRecoveryBlocked(error)) { stream.error = stream.error || error.message; break; }
        if (error.name === "AbortError" || !stream.done || state.stopResultStatus) break;
        chatConnectionNotice(stream, "任务已结束，正在同步完整结果和附件；无需重新发送。");
        await waitChatReconnect(attempt++, signal);
      }
    }
  } catch (error) { /* A user cancellation stops only this result synchronization. */ }
  finally {
    flushStreamMarkdown(stream); stream.body.removeAttribute("data-stream");
    if (!saved && state.current?.id === conversationId) {
      const parts = (stream.parts || stream.segments.map(segment => ({ type: "text", text: segment.raw }))).slice();
      if (stream.error) parts.push({ type: "error", message: stream.error });
      if (parts.length) state.current.messages.push({ role: "assistant", content: "", parts });
    }
    state.streaming = false; state.activeRunId = null; state.abortController = null; state.stopResultStatus = null;
    renderMessages(); updateSendState();
  }
}

/** Reopening a running conversation follows its existing run without creating another job. */
async function resumeChatRun(runId, conversationId) {
  if (state.streaming) return;
  state.streaming = true; state.activeRunId = runId; state.stopResultStatus = null;
  state.abortController = new AbortController(); updateSendState();
  const sentMessageCount = state.current?.messages.length || 0;
  const body = appendAssistantRow(); body.setAttribute("data-stream", "1");
  const stream = { body, toolMap: {}, segments: [], active: null, timer: null, error: "", done: false };
  try {
    const response = await fetchChatState("/api/chat/runs/" + encodeURIComponent(runId) + "/events", state.abortController.signal);
    await followChatRun(response, stream, conversationId, sentMessageCount);
  } catch (error) {
    if (!chatRecoveryBlocked(error) && error.name !== "AbortError") {
      try { await followChatRun(null, stream, conversationId, sentMessageCount); }
      catch (resumeError) { setChatStreamError(stream, resumeError); }
    } else setChatStreamError(stream, error);
  } finally { await finishChatStream(stream, conversationId, sentMessageCount); }
}
/**
 * 把流式缓冲的 markdown 一次性渲染出来（增量片段积累到一定量再整段重渲染）。
 * 仅在用户本来就贴着底部时才跟随滚动，避免打断向上翻阅历史。
 */
function flushStreamMarkdown(ctx) {
  if (ctx.timer != null) clearTimeout(ctx.timer);
  ctx.timer = null;
  const scrollRoot = $("#scroll-root");
  const follow = scrollRoot && scrollRoot.scrollHeight - scrollRoot.scrollTop - scrollRoot.clientHeight < 100;
  for (const segment of ctx.segments) {
    if (segment.rendered === segment.raw) continue;
    segment.target.innerHTML = mdRender(segment.raw);
    segment.rendered = segment.raw;
  }
  if (follow && ctx.body.isConnected) scrollToBottom({ force: false });
}

/**
 * 处理单条 SSE 帧（事件名 event: 与数据 data: 多行拼接后解析 JSON）。
 * 事件语义：run.created 记 run_id（供停止用）、message.delta 增量追加、tool.started/completed 渲染工具徽标、
 * message.reset 按迭代撤回未完整正文、message.replace 同步原生正文快照（包括空正文）、
 * message.question 作为普通澄清文字、message.followups 仅保留在消息数据中、
 * context.compacted 提示已压缩上下文、
 * run.failed / done 收尾。任何未知事件与非法 JSON 都直接忽略，保证流不因单个坏帧中断。
 */
function handleFrame(frame, ctx) {
  let ev = "message", eventId = null; const dataLines = [];
  for (const ln of frame.split("\n")) {
    if (ln.startsWith("event:")) ev = ln.slice(6).trim();
    else if (ln.startsWith("id:")) eventId = Number(ln.slice(3).trim());
    else if (ln.startsWith("data:")) dataLines.push(ln.slice(5).trim());
  }
  if (!dataLines.length) return;
  let d; try { d = JSON.parse(dataLines.join("\n")); } catch (e) { return; }
  if (Number.isSafeInteger(eventId) && eventId > 0) {
    if (eventId <= (ctx.lastEventId || 0)) return;
    ctx.lastEventId = eventId;
  }
  if (!ctx.parts) ctx.parts = [];
  if (ev === "run.created") {
    state.activeRunId = d.run_id;
  } else if (ev === "task.progress") {
    let progress = ctx.body.querySelector(".task-progress");
    if (!progress) { progress = document.createElement("div"); progress.className = "tool-chip task-progress"; progress.setAttribute("role", "status"); ctx.body.appendChild(progress); }
    progress.textContent = String(d.message || "正在处理") + (Number.isFinite(d.progress) ? " · " + d.progress + "%" : "");
  } else if (ev === "task.phase") {
    let progress = ctx.body.querySelector(".task-progress");
    if (!progress) { progress = document.createElement("div"); progress.className = "tool-chip task-progress"; progress.setAttribute("role", "status"); ctx.body.appendChild(progress); }
    progress.textContent = "阶段：" + String(d.message || d.phase || "执行中");
  } else if (ev === "task.deliverable") {
    const count = Number.isFinite(d.count) ? d.count : (Array.isArray(d.artifact_ids) ? d.artifact_ids.length : 1);
    let progress = ctx.body.querySelector(".task-progress");
    if (!progress) { progress = document.createElement("div"); progress.className = "tool-chip task-progress"; progress.setAttribute("role", "status"); ctx.body.appendChild(progress); }
    progress.textContent = "已交付 " + count + " 个文件";
  } else if (ev === "message.reset") {
    if (!Number.isInteger(d.iteration)) return;
    flushStreamMarkdown(ctx);
    const discarded = ctx.segments.filter(segment => segment.iteration === d.iteration);
    discarded.forEach(segment => segment.target.remove());
    ctx.segments = ctx.segments.filter(segment => !discarded.includes(segment));
    ctx.parts = ctx.parts.filter(part => !discarded.some(segment => segment.part === part));
    if (discarded.includes(ctx.active)) ctx.active = null;
  } else if (ev === "message.replace") {
    if (typeof d.content !== "string") return;
    flushStreamMarkdown(ctx);
    // 只接受同一 attempt 或尚未产出正文的替换；旧 attempt 的快照不得覆盖更新的正文（R23）。
    if (ctx.active && d.attempt_id !== undefined && ctx.active.attemptId !== d.attempt_id && ctx.active.raw) {
      return;
    }
    // 快照覆盖正文；工具和交付回执独立保留，防止重试或重连重复显示半句。
    const discarded = ctx.segments.slice();
    discarded.forEach(segment => segment.target.remove());
    ctx.parts = ctx.parts.filter(part => !discarded.some(segment => segment.part === part));
    ctx.segments = []; ctx.active = null;
    if (d.content) {
      const target = document.createElement("div"); target.className = "assistant-content";
      ctx.body.appendChild(target);
      const part = { type: "text", text: d.content };
      ctx.active = { target, raw: d.content, rendered: null, iteration: d.iteration, attemptId: d.attempt_id, part };
      ctx.parts.push(part); ctx.segments.push(ctx.active);
      flushStreamMarkdown(ctx);
    }
  } else if (ev === "message.delta" && d.delta) {
    if (!ctx.active || ctx.active.iteration !== d.iteration || ctx.active.attemptId !== d.attempt_id) {
      const target = document.createElement("div");
      target.className = "assistant-content";
      ctx.body.appendChild(target);
      const part = { type: "text", text: "" };
      ctx.active = { target, raw: "", rendered: null, iteration: d.iteration, attemptId: d.attempt_id, part };
      ctx.parts.push(part); ctx.segments.push(ctx.active);
    }
    ctx.active.raw += d.delta;
    ctx.active.part.text = ctx.active.raw;
    if (ctx.timer == null) ctx.timer = setTimeout(() => flushStreamMarkdown(ctx), 60);
  } else if (ev === "message.followups") {
    ctx.parts.push({ type: "followup_questions", questions: d.questions || [], error: d.error });
    flushStreamMarkdown(ctx);
    appendFollowupQuestions(ctx.body, d.questions || [], state.current?.messages || [], d.error);
  } else if (ev === "message.question") {
    ctx.parts.push({ ...d, type: "business_question" });
    flushStreamMarkdown(ctx); ctx.active = null;
    appendBusinessQuestion(ctx.body, d);
    scrollToBottom({ force: false });
  } else if (ev === "tool.started") {
    flushStreamMarkdown(ctx);
    ctx.active = null;
    const chip = document.createElement("div"); chip.className = "tool-chip";
    chip.innerHTML = "<span class='spin'></span> " + esc(toolLabel(d.tool));
    ctx.body.appendChild(chip); ctx.toolMap[d.tool_call_id || String(Math.random())] = chip; scrollToBottom({ force: false });
  } else if (ev === "tool.completed") {
    let chip = ctx.toolMap[d.tool_call_id];
    if (!chip) { const last = ctx.body.lastElementChild; chip = (last && last.classList.contains("tool-chip")) ? last : document.createElement("div"); if (!(last && last.classList.contains("tool-chip"))) { chip.className = "tool-chip"; ctx.body.appendChild(chip); } }
    if (d.status === "waiting_input" || d.summary === "等待你的选择") chip.textContent = "需要补充查询范围，请直接在输入框说明";
    else if (d.status === "failed") chip.innerHTML = "<span class='err'>!</span> " + esc(toolLabel(d.tool)) + "：" + esc(customerChatError(d.summary));
    else chip.innerHTML = "<span class='ok'>&#10003;</span> " + esc(toolLabel(d.tool)) + (d.summary ? " · " + esc(String(d.summary).slice(0, 110)) : "");
    scrollToBottom({ force: false });
  } else if (ev === "context.compacted") {
    let notice = ctx.body.querySelector(".context-compact-chip");
    if (!notice) {
      notice = document.createElement("div");
      notice.className = "tool-chip context-compact-chip";
      ctx.body.appendChild(notice);
    }
    notice.innerHTML = "<span class='ok'>&#10003;</span> 已压缩较早的文件内容，继续处理";
    scrollToBottom({ force: false });
  } else if (ev === "run.failed") {
    const failure = d.error && typeof d.error === "object" ? (d.error.message || d.error.detail || d.error.summary) : d.error;
    ctx.error = customerChatError(String(failure || d.summary || d.message || "运行失败"));
    ctx.done = true;
    ctx.body.querySelector(".chat-connection-notice")?.remove();
    ctx.body.querySelector(".task-progress")?.remove();
    flushStreamMarkdown(ctx);
  } else if (ev === "run.completed" || ev === "run.cancelled") {
    // 终态事件本身就是结束信号；不能依赖后续 done，否则断线时会无限重连。
    ctx.done = true;
    if (ev === "run.cancelled" && !ctx.error) ctx.error = "已停止生成，已完成的结果仍然保留。";
    ctx.body.querySelector(".chat-connection-notice")?.remove();
    ctx.body.querySelector(".task-progress")?.remove();
    flushStreamMarkdown(ctx);
  } else if (ev === "done") {
    ctx.done = true;
    ctx.body.querySelector(".chat-connection-notice")?.remove();
    ctx.body.querySelector(".task-progress")?.remove();
    flushStreamMarkdown(ctx);
  }
}
function customerChatError(message) {
  const text = String(message || "").trim();
  if (!text) return "暂时未能完成，请查看会话记录后重试。";
  // 对外只保留可行动的人话摘要，密钥、令牌、绝对路径和堆栈永不回显。
  const safe = text
    .replace(/(password|passwd|api[_-]?key|access[_-]?token|authorization|secret)\s*[:=]\s*[^\s,;]+/ig, "$1=已隐藏")
    .replace(/[A-Za-z]:\\[^\s]+|\/(?:home|workspace|tmp|var)\/[^\s]+/g, "相关文件")
    .replace(/\s+at\s+[^\n]+/gi, "")
    .replace(/\s+/g, " ")
    .slice(0, 260);
  if (/权限|forbidden|unauthori[sz]ed|401|403/i.test(safe)) return "没有访问这项数据或工具的权限，请检查工作区授权。";
  if (/字段|列名|表名|schema|元数据/i.test(safe)) return "查询所需的数据字段不完整：" + safe;
  if (/sql|语法|json|参数|校验|validation/i.test(safe)) return "输入或查询校验未通过：" + safe;
  if (/timeout|超时|连接|network|503|502/i.test(safe)) return "服务连接暂时中断，已保留当前进度：" + safe;
  return safe;
}
function toolLabel(name) {
  const map = { search_knowledge: "检索知识库", read_file: "读取文件", python_execute: "Python 执行", render_document: "生成文档", render_presentation: "生成PPT", render_spreadsheet: "生成表格", render_chart_image: "生成真实数据图表", export_query_spreadsheet: "导出查询结果为Excel", create_dashboard_from_queries: "从查询结果创建大屏", render_dashboard_image: "导出大屏数据图片", export_inspection_report: "导出已有检查报告", current_datetime: "获取时间", save_json_artifact: "保存数据", query_database: "查询数据库", "database.query": "查询数据库", query_business_data: "查询所选业务数据", "database.list_tables": "列出数据表", "database.describe_table": "查看表结构", list_files: "列出文件", write_file: "写入文件", copy_file: "复制文件", rename_file: "重命名文件", delete_file: "删除文件", add_tool: "添加工具", fill_template: "按模板填入", list_business_data_sources: "发现业务数据", view_device_alerts: "读取设备预警", query_workflow_results: "查看业务检查报告", create_business_dashboard: "生成数字大屏草稿" };
  Object.assign(map, {list_dashboard_templates: "查看大屏模板", get_business_dashboard: "查看大屏配置", update_business_dashboard: "调整大屏主题"});
  return map[name] || name;
}

// ==== stream-recovery-slice-end ==== （稳定哨兵：tests/ui/chat_stream_recovery.test.js 以此为切片终点）
// ---------------- "+" 菜单 ----------------
/** 详情抽屉打开时的焦点还原目标；关闭抽屉后要把焦点还给触发元素。 */
let menuAnchor = null;
let detailReturnFocus = null;
/**
 * 把浮层定位到锚点旁边。
 * 输入区上方的浮层（tool-picker/composer-popover）改为贴着输入框顶部向上展开，
 * 并把高度限制在可视区域剩余空间内；其它浮层贴在锚点下方，左右都留 8–10px 安全边距。
 */
function positionPanel(panel, anchor) {
  const r = anchor.getBoundingClientRect();
  panel.classList.remove("hidden");
  panel.style.bottom = "auto";
  panel.style.maxHeight = "";
  const composerAnchored = panel.classList.contains("tool-picker") || panel.classList.contains("composer-popover") || Boolean(anchor.closest(".composer-wrap"));
  if (composerAnchored) {
    const composer = $(".composer-max");
    const cr = composer ? composer.getBoundingClientRect() : r;
    const panelWidth = panel.offsetWidth || (panel.classList.contains("tool-picker") ? cr.width : 420);
    const preferredLeft = panel.classList.contains("tool-picker") ? cr.left : r.left;
    panel.style.left = Math.max(10, Math.min(preferredLeft, window.innerWidth - panelWidth - 10)) + "px";
    panel.style.top = "auto";
    panel.style.bottom = Math.max(82, window.innerHeight - r.top + 8) + "px";
    panel.style.maxHeight = Math.max(180, Math.min(window.innerHeight * 0.62, r.top - 18)) + "px";
  } else {
    panel.style.left = Math.max(8, Math.min(r.left, window.innerWidth - Math.max(250, panel.offsetWidth || 250) - 8)) + "px";
    panel.style.top = Math.max(8, r.bottom + 6) + "px";
  }
}
/** 打开「+」添加菜单（文件/资料库/图片/PPT/网页/深度研究/知识库/插件/数据源），复用同一个 #menu-panel 容器。 */
function openAddMenu(anchor) {
  menuAnchor = anchor;
  const panel = $("#menu-panel");
  panel.className = "menu-panel add-menu";
  panel.setAttribute("role", "menu");
  panel.setAttribute("aria-label", "添加内容与能力");
  panel.innerHTML =
    "<div class='menu-item' data-act='file'><span class='mi'>" + IC.upload + "</span><span class='mmute'><span>添加照片和文件</span><span class='menu-desc'>从电脑上传</span></span></div>" +
    "<div class='menu-item' data-act='library'><span class='mi'>" + IC.library + "</span><span class='mmute'><span>从资料库添加</span><span class='menu-desc'>浏览和搜索企业文件</span></span></div>" +
    "<div class='menu-item' data-act='image'><span class='mi'>" + IC.image + "</span><span class='mmute'><span>添加图片</span><span class='menu-desc'>分析截图、图表和照片</span></span></div>" +
    "<div class='menu-item' data-act='presentation'><span class='mi'>" + IC.files + "</span><span class='mmute'><span>生成 PPT</span><span class='menu-desc'>选择模板、比例与页数</span></span></div>" +
    "<div class='menu-item' data-act='web'><span class='mi'>" + IC.globe + "</span><span class='mmute'><span>网页搜索</span><span class='menu-desc'>查找实时信息和来源</span></span></div>" +
    "<div class='menu-item' data-act='deep'><span class='mi'>" + IC.research + "</span><span class='mmute'><span>深度研究</span><span class='menu-desc'>执行多步骤分析</span></span></div>" +
    "<div class='menu-sep'></div>" +
    "<div class='menu-item' data-act='kb'><span class='mi'>" + IC.kb + "</span><span class='mmute'><span>项目知识库</span><span class='menu-desc'>选择长期企业资料</span></span></div>" +
    "<div class='menu-item' data-act='plugin'><span class='mi'>" + IC.plugin + "</span><span class='mmute'><span>插件</span><span class='menu-desc'>使用已发布的业务能力</span></span></div>" +
    "<div class='menu-item' data-act='ds'><span class='mi'>" + IC.ds + "</span><span class='mmute'><span>企业数据源</span><span class='menu-desc'>数据库、API 与 MCP</span></span></div>";
  positionPanel(panel, anchor);
  $("#overlay").classList.remove("hidden");
  panel.querySelectorAll("[data-act]").forEach((el) => el.onclick = () => handleAddAction(el.dataset.act));
  if (dshChat?.enabled()) {
    for (const mode of ["web", "deep"]) {
      const item = panel.querySelector('[data-act="' + mode + '"]'), capability = dshChat.capabilities().modes?.find(value => value.id === mode);
      if (item && !capability?.available) { item.classList.add("dsh-unavailable"); item.setAttribute("aria-disabled", "true"); item.querySelector(".menu-desc").textContent = capability?.reason || "当前未配置"; }
    }
    const entry = document.createElement("button"); entry.type = "button"; entry.className = "menu-item"; entry.textContent = "技能与业务能力";
    entry.onclick = () => dshChat.menu(anchor); panel.appendChild(entry);
  }
}
/** 关闭当前浮层（菜单/详情抽屉共用）：清 ARIA 角色并把焦点还给打开它的元素。 */
function closeMenu() {
  const panel = $("#menu-panel");
  panel.classList.add("hidden");
  panel.removeAttribute("role");
  panel.removeAttribute("aria-modal");
  panel.removeAttribute("aria-labelledby");
  panel.onkeydown = null;
  $("#overlay").classList.add("hidden");
  if (detailReturnFocus && document.contains(detailReturnFocus)) detailReturnFocus.focus();
  detailReturnFocus = null;
}
/** 「+」菜单各项的分发：上传图片与文件共用同一个 #file-input，仅切换 accept 属性。 */
async function handleAddAction(a) {
  if (a === "file") { closeMenu(); $("#file-input").removeAttribute("accept"); $("#file-input").click(); return; }
  if (a === "image") { closeMenu(); $("#file-input").setAttribute("accept", "image/*"); $("#file-input").click(); return; }
  if (a === "presentation") { closeMenu(); openTemplatePicker(); return; }
  if (a === "library") {
    let files = [];
    try { files = (await API.get("/api/files")).items || []; } catch (e) { return toast("资料库加载失败：" + e.message); }
    openListMenu("从资料库添加", files, (f) => state.fileIds.some((x) => x.id === f.id), (f, chk) => {
      if (chk.checked && !state.fileIds.some((x) => x.id === f.id)) state.fileIds.push({id:f.id,name:f.name});
      if (!chk.checked) state.fileIds = state.fileIds.filter((x) => x.id !== f.id);
      renderAttachChips();
    }); return;
  }
  if (a === "web" || a === "deep") {
    if (dshChat?.enabled()) { if (dshChat.selectMode(a)) closeMenu(); return; }
    toast("当前对话宿主未提供此模式能力，请刷新后重试"); return;
  }
  if (a === "kb") { openListMenu("选择项目知识库", state.knowledge, (k) => state.kbIds.includes(k.id), (k, chk) => { if (chk.checked && !state.kbIds.includes(k.id)) state.kbIds.push(k.id); else if (!chk.checked) state.kbIds = state.kbIds.filter((x) => x !== k.id); renderAttachChips(); }); return; }
  if (a === "plugin") { openListMenu("选择插件", state.plugins, (p) => state.pluginId === p.id, (p, chk) => { state.pluginId = chk.checked ? p.id : null; state.pluginVersionId = chk.checked ? (p.published_version_id || null) : null; renderAttachChips(); }); return; }
  if (a === "ds") { await openDataSourcePicker(); return; }
}

/**
 * 计算数据源在列表里展示的三项就绪度：元数据、注释覆盖、语义检索。
 * HTTP 类数据源只看「自动采集」开关，数据库类按业务表与字段的注释比例判断（阈值 0.35，与后端口径一致）。
 */
function dataSourceCoverage(source) {
  if (source.resourceType === "http" || source.provider === "http") {
    const fieldTotal = Number(source.fieldCount || 0);
    const fieldComments = Number((((source.commentCoverage || {}).fields || {}).commented) || 0);
    return {
      metadata: source.metadataStatus === "ready",
      comments: fieldTotal > 0 && fieldComments / fieldTotal >= 0.35,
      semantic: Boolean(source.automaticReady),
      commentRatio: fieldTotal ? Math.round(fieldComments / fieldTotal * 100) : 0
    };
  }
  const tableTotal = Number(source.businessTableCount || 0);
  const fieldTotal = Number(source.fieldCount || 0);
  const tableComments = Number((((source.commentCoverage || {}).tables || {}).commented) || 0);
  const fieldComments = Number((((source.commentCoverage || {}).fields || {}).commented) || 0);
  const commentRatio = tableTotal + fieldTotal ? (tableComments + fieldComments) / (tableTotal + fieldTotal) : 0;
  return {
    metadata: source.metadataStatus === "ready",
    comments: commentRatio >= 0.35,
    semantic: source.semanticIndexStatus === "ready",
    commentRatio: Math.round(commentRatio * 100)
  };
}

/** 数据源候选行（复选框 + 元数据/注释/语义覆盖情况），HTTP 与数据库两类字段文案分别处理。 */
function dataSourceRowsMarkup(list) {
  if (!(list || []).length) return "<div class='datasource-empty'>没有匹配的数据源</div>";
  return list.map((source) => {
    const checked = state.datasourceIds.includes(source.id);
    const coverage = dataSourceCoverage(source);
    const domains = (source.businessDomains || []).slice(0, 4).map((item) => "<span>" + esc(item) + "</span>").join("");
    const tableCount = Number(source.businessTableCount || source.tableCount || 0);
    const fieldCount = Number(source.fieldCount || 0);
    const isHttp = source.resourceType === "http" || source.provider === "http";
    const status = isHttp ? "提取配置已校验" : source.metadataStatus === "ready" ? "元数据已同步" : source.metadataStatus === "stale" ? "需要重新同步" : "尚未同步";
    const semantic = isHttp ? (source.automaticReady ? "允许自动采集" : "仅允许手动采集") : source.semanticIndexStatus === "ready" ? "向量索引" : source.semanticIndexStatus === "keyword_only" ? "注释检索" : "未建索引";
    const structure = isHttp ? "1 接口" : tableCount + " 表";
    return "<label class='datasource-option" + (checked ? " is-selected" : "") + "' data-source-row='" + esc(source.id) + "'>" +
      "<input type='checkbox' data-source-id='" + esc(source.id) + "' " + (checked ? "checked" : "") + ">" +
      "<span class='datasource-check' aria-hidden='true'></span>" +
      "<span class='datasource-option-main'>" +
        "<span class='datasource-option-head'><strong>" + esc(source.name) + "</strong><span class='datasource-provider'>" + esc(String(source.provider || "").toUpperCase()) + "</span><span class='datasource-domains'>" + domains + "</span></span>" +
        "<span class='datasource-description'>" + esc(source.description || "暂无用途说明；请先同步元数据或补充备注") + "</span>" +
        "<span class='datasource-metadata'><span>" + esc(source.environmentSummary || "") + "</span><span>" + structure + "</span><span>" + fieldCount + " 字段</span><span>" + esc(status) + "</span><span>" + esc(semantic) + "</span></span>" +
        "<span class='datasource-coverage'><i class='" + (coverage.metadata ? "ready" : "") + "'></i><i class='" + (coverage.comments ? "ready" : "") + "'></i><i class='" + (coverage.semantic ? "ready" : "") + "'></i><em>" + (isHttp ? "配置 · 字段注释 " + coverage.commentRatio + "% · 自动调用" : "结构 · 注释 " + coverage.commentRatio + "% · 语义") + "</em></span>" +
      "</span>" +
    "</label>";
  }).join("");
}

/**
 * 绑定数据源选择面板的搜索与勾选。
 * 硬约束：一次最多选 8 个数据源，超出时回滚勾选并提示（后端也按同一上限校验）。
 */
function bindDataSourcePicker(panel) {
  const search = panel.querySelector("#datasource-picker-search");
  const list = panel.querySelector("#datasource-picker-list");
  const counter = panel.querySelector("#datasource-picker-count");
  const renderFiltered = () => {
    const query = String(search?.value || "").trim().toLowerCase();
    const rows = !query ? state.dataSources : state.dataSources.filter((source) => [
      source.name, source.description, source.environmentSummary, ...(source.businessDomains || [])
    ].join(" ").toLowerCase().includes(query));
    list.innerHTML = dataSourceRowsMarkup(rows);
    list.querySelectorAll("input[data-source-id]").forEach((checkbox) => {
      checkbox.onchange = () => {
        const id = checkbox.dataset.sourceId;
        if (checkbox.checked && !state.datasourceIds.includes(id)) {
          if (state.datasourceIds.length >= 8) {
            checkbox.checked = false;
            toast("一次最多选择 8 个数据源");
            return;
          }
          state.datasourceIds.push(id);
        } else if (!checkbox.checked) {
          state.datasourceIds = state.datasourceIds.filter((item) => item !== id);
        }
        checkbox.closest(".datasource-option")?.classList.toggle("is-selected", checkbox.checked);
        counter.textContent = "已选 " + state.datasourceIds.length + " / 8";
        renderAttachChips();
      };
    });
  };
  if (search) search.oninput = renderFiltered;
  panel.querySelector("[data-datasource-done]").onclick = closeMenu;
  panel.querySelector("[data-datasource-manage]").onclick = () => { window.location.href = "/datasources"; };
  renderFiltered();
  requestAnimationFrame(() => search?.focus());
}

/**
 * 打开数据源选择面板：先展示「正在读取」再用 discover 接口刷新目录。
 * 用 strict 模式解析响应，目录不完整时直接报错并给出重读入口，不展示残缺列表。
 */
async function openDataSourcePicker(anchor = null) {
  const panel = $("#menu-panel");
  panel.className = "menu-panel datasource-picker";
  panel.innerHTML = "<div class='datasource-picker-loading'><span class='spin'></span>正在读取授权数据目录…</div>";
  positionPanel(panel, anchor || menuAnchor || $("#add-menu-btn"));
  $("#overlay").classList.remove("hidden");
  try {
    const response = await API.post("/api/v1/ai/business-actions/data-sources/discover", { keyword: "", limit: 20 });
    state.dataSources = businessSourceCatalog(response, true).sources;
  } catch (error) {
    panel.innerHTML = "<div class='datasource-picker-error'><strong>数据目录加载失败</strong><span>" + esc(error.message) + "</span><div><button type='button' data-datasource-retry>重新读取</button><a href='/datasources'>数据源管理</a></div></div>";
    panel.querySelector("[data-datasource-retry]").onclick = () => openDataSourcePicker(anchor);
    return;
  }
  panel.innerHTML =
    "<div class='datasource-picker-head'><div><strong>选择本次业务数据</strong><span>AI 只查询明确选中的数据源</span></div><button class='quiet-icon' type='button' data-close-datasource aria-label='关闭'>" + IC.close + "</button></div>" +
    "<div class='datasource-picker-search'><span>" + IC.search + "</span><input id='datasource-picker-search' type='search' placeholder='搜索名称、用途或业务域'></div>" +
    "<div class='datasource-picker-list' id='datasource-picker-list'></div>" +
    "<div class='datasource-picker-foot'><button class='datasource-manage-link' type='button' data-datasource-manage>管理与同步</button><span id='datasource-picker-count'>已选 " + state.datasourceIds.length + " / 8</span><button class='datasource-done' type='button' data-datasource-done>完成</button></div>";
  panel.querySelector("[data-close-datasource]").onclick = closeMenu;
  bindDataSourcePicker(panel);
}
/** 通用多选清单浮层（资料库文件、知识库、插件复用），选中结果由调用方在 onToggle 里写回 state。 */
function openListMenu(title, list, isChecked, onToggle) {
  const panel = $("#menu-panel");
  const rows = (list || []).length ? (list || []).map((it) =>
    "<label class='menu-item'><input type='checkbox' data-id='" + it.id + "' " + (isChecked(it) ? "checked" : "") + "><span style='padding-left:6px;'>" + esc(it.name || it.id) + "</span><span class='menu-check'></span></label>").join("") :
    "<div class='muted' style='padding:8px 10px;font-size:12.5px;'>暂无内容</div>";
  panel.className = "menu-panel composer-popover";
  panel.innerHTML = "<div class='menu-head'>" + esc(title) + "</div>" + rows + "<div class='menu-sep'></div><div class='menu-item' data-done='1'><span class='mmute' style='color:#111;font-weight:600;'>完成选择</span></div>";
  positionPanel(panel, menuAnchor || $("#add-menu-btn"));
  panel.querySelectorAll("input[type=checkbox]").forEach((chk) => {
    const id = chk.dataset.id;
    const it = (list || []).find((x) => x.id === id);
    chk.onchange = () => { if (it) onToggle(it, chk); };
  });
  panel.querySelector("[data-done]").onclick = () => closeMenu();
}

// ---------------- 管理页面 ----------------
/**
 * 知识库主页：有 knowledgeCurrentId 时直接进详情，否则渲染列表与新建表单。
 * 列表搜索是纯前端过滤（按卡片文本匹配），不触发后端检索。
 */
async function renderKnowledgeView() {
  const box = contentEl("knowledge"); if (!box) return;
  if (state.knowledgeCurrentId) {
    await renderKnowledgeDetail(box, state.knowledgeCurrentId);
    return;
  }
  const response = await API.get("/api/knowledge");
  state.knowledge = response.items || [];
  const totals = state.knowledge.reduce((acc, item) => {
    acc.documents += item.document_count || 0;
    acc.chunks += item.chunk_count || 0;
    acc.failed += item.failed_document_count || 0;
    return acc;
  }, { documents: 0, chunks: 0, failed: 0 });
  const cards = state.knowledge.length ? state.knowledge.map((kb) =>
    "<button class='kb-card' data-open-kb='" + kb.id + "' type='button'>" +
      "<span class='kb-card-icon'>" + iconSmall(IC.kb, 20) + "</span>" +
      "<span class='kb-card-main'><span class='kb-card-title'>" + esc(kb.name) + "</span>" +
      "<span class='kb-card-desc'>" + esc(kb.description || "尚未填写知识库说明") + "</span>" +
      "<span class='kb-card-meta'><span>" + (kb.document_count || 0) + " 个文档</span><span>" + (kb.chunk_count || 0) + " 个分块</span><span>更新于 " + formatKBDate(kb.updated_at) + "</span></span></span>" +
      "<span class='kb-card-side'>" + stateBadge(kb.failed_document_count ? "failed" : "active") +
      "<span class='kb-card-arrow' aria-hidden='true'>&#8594;</span></span></button>"
  ).join("") : "<div class='kb-empty'><span class='kb-empty-icon'>" + IC.kb + "</span><h2>建立第一个知识库</h2><p>集中沉淀制度、项目、产品和业务资料，让 AI 在回答时检索可靠来源。</p><button class='btn-sm btn-primary' id='kb-empty-create' type='button'>新建知识库</button></div>";
  box.innerHTML =
    "<div class='panel kb-page'>" +
      "<div class='kb-page-head'><div><h1>知识库</h1><div class='sub'>管理可长期复用的企业资料，并验证 AI 实际能检索到什么。</div></div>" +
      "<button class='btn-sm btn-primary kb-primary-action' id='kb-new-btn' type='button'>" + IC.plus + " 新建知识库</button></div>" +
      "<section class='card kb-create-card hidden' id='kb-create-card' aria-label='新建知识库'>" +
        "<div class='kb-section-title'><div><strong>新建知识库</strong><span>名称应能清楚说明资料范围。</span></div><button class='icon-btn' id='kb-create-close' aria-label='关闭'>" + IC.close + "</button></div>" +
        "<div class='kb-form-grid'><label class='kb-field kb-field-wide'><span>名称</span><input id='kb-name' maxlength='200' placeholder='例如：2026 华东区域招投标资料'></label>" +
        "<label class='kb-field kb-field-wide'><span>说明</span><textarea id='kb-desc' maxlength='4000' rows='3' placeholder='说明包含哪些资料、适用于哪些团队和业务场景'></textarea></label></div>" +
        "<div class='kb-form-actions'><button class='btn-sm btn-secondary' id='kb-create-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='kb-create-submit' type='button'>创建</button></div>" +
      "</section>" +
      "<div class='kb-summary-grid' aria-label='知识库概况'>" +
        kbSummaryItem(state.knowledge.length, "知识库", "正在使用") +
        kbSummaryItem(totals.documents, "文档", "已加入索引") +
        kbSummaryItem(totals.chunks, "知识分块", "可供检索") +
        kbSummaryItem(totals.failed, "异常文档", totals.failed ? "需要处理" : "运行正常", totals.failed > 0) +
      "</div>" +
      "<div class='kb-list-toolbar'><label class='kb-list-search'>" + IC.search + "<input id='kb-list-search' placeholder='搜索知识库' aria-label='搜索知识库'></label><span class='muted'>" + state.knowledge.length + " 个知识库</span></div>" +
      "<div class='kb-grid' id='kb-list'>" + cards + "</div>" +
    "</div>";

  const createCard = $("#kb-create-card");
  const showCreate = () => { createCard.classList.remove("hidden"); $("#kb-name").focus(); };
  const hideCreate = () => { createCard.classList.add("hidden"); };
  $("#kb-new-btn").onclick = showCreate;
  if ($("#kb-empty-create")) $("#kb-empty-create").onclick = showCreate;
  $("#kb-create-close").onclick = hideCreate;
  $("#kb-create-cancel").onclick = hideCreate;
  $("#kb-create-submit").onclick = async (event) => {
    const name = $("#kb-name").value.trim();
    if (!name) return toast("请输入知识库名称");
    const button = event.currentTarget;
    button.disabled = true; button.textContent = "创建中…";
    try {
      const created = await API.post("/api/knowledge", { name: name, description: $("#kb-desc").value.trim() });
      state.knowledgeCurrentId = created.id;
      state.knowledgeTab = "documents";
      state.knowledgePage = 1;
      state.knowledgeSearchResults = null;
      state.knowledgeSearchQuery = "";
      state.knowledgeSearchMode = "hybrid";
      state.knowledgeSearchTopK = 6;
      toast("知识库已创建");
      await renderKnowledgeView();
    } catch (error) {
      toast("创建失败：" + error.message);
      button.disabled = false; button.textContent = "创建";
    }
  };
  box.querySelectorAll("[data-open-kb]").forEach((button) => {
    button.onclick = async () => {
      state.knowledgeCurrentId = button.dataset.openKb;
      state.knowledgeTab = "documents";
      state.knowledgePage = 1;
      state.knowledgeSearchResults = null;
      state.knowledgeSearchQuery = "";
      state.knowledgeSearchMode = "hybrid";
      state.knowledgeSearchTopK = 6;
      await renderKnowledgeView();
    };
  });
  $("#kb-list-search").oninput = (event) => {
    const query = event.target.value.trim().toLowerCase();
    box.querySelectorAll("[data-open-kb]").forEach((card) => {
      card.classList.toggle("hidden", !card.textContent.toLowerCase().includes(query));
    });
  };
}

/** 知识库概况卡片的单个指标格（warning 为真时用警示色，仅用于异常文档数）。 */
function kbSummaryItem(value, label, note, warning) {
  return "<div class='kb-summary-item" + (warning ? " warning" : "") + "'><strong>" + value + "</strong><span>" + esc(label) + "</span><small>" + esc(note) + "</small></div>";
}

/** 字节数转 B/KB/MB（知识库文档体积展示用）。 */
function formatKBBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(bytes < 10240 ? 1 : 0) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** 短日期时间（月-日 时:分）；不可解析的旧格式直接截断原字符串，兜底不显示 Invalid Date。 */
function formatKBDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** 知识库/文档状态徽标；未收录的状态原样展示，避免把未知状态显示成「未知」以外的误导文案。 */
function stateBadge(status) {
  const labels = { ready: "已就绪", processing: "处理中", failed: "异常", active: "正常" };
  return "<span class='badge kb-status " + esc(status || "") + "'><i></i>" + esc(labels[status] || status || "未知") + "</span>";
}

/**
 * 知识库详情页（文档/检索测试/设置三个页签）。
 * 用 state.knowledgeLoadRevision 做请求序号防串台：并发/切换知识库时旧响应直接丢弃；
 * 当前页码超过实际总页数时回退到最后一页重新渲染（删除文档后常见）。
 */
async function renderKnowledgeDetail(box, kbId) {
  const revision = ++state.knowledgeLoadRevision;
  let kb, documents, documentPage;
  try {
    const responses = await Promise.all([
      API.get("/api/knowledge/" + kbId),
      API.get("/api/knowledge/" + kbId + "/documents?page=" + state.knowledgePage + "&page_size=50")
    ]);
    if (revision !== state.knowledgeLoadRevision || state.knowledgeCurrentId !== kbId) return;
    kb = responses[0]; documentPage = responses[1]; documents = documentPage.items || [];
    const lastPage = Math.max(1, Math.ceil(documentPage.total / 50));
    if (state.knowledgePage > lastPage) { state.knowledgePage = lastPage; return renderKnowledgeDetail(box, kbId); }
  } catch (error) {
    if (revision !== state.knowledgeLoadRevision || state.knowledgeCurrentId !== kbId) return;
    state.knowledgeCurrentId = null;
    toast("知识库加载失败：" + error.message);
    await renderKnowledgeView();
    return;
  }
  const tab = state.knowledgeTab || "documents";
  let pane = "";
  if (tab === "search") pane = renderKnowledgeSearchPane(kb);
  else if (tab === "settings") pane = renderKnowledgeSettingsPane(kb);
  else pane = renderKnowledgeDocumentsPane(kb, documents) +
    "<div class='kb-picker-actions'><span>共 " + documentPage.total + " 个文档 · 第 " + state.knowledgePage + " 页</span>" +
    "<button class='btn-sm btn-secondary' data-kb-page='-1' " + (state.knowledgePage <= 1 ? "disabled" : "") + ">上一页</button>" +
    "<button class='btn-sm btn-secondary' data-kb-page='1' " + (state.knowledgePage * 50 >= documentPage.total ? "disabled" : "") + ">下一页</button></div>";
  box.innerHTML =
    "<div class='panel kb-page kb-detail-page'>" +
      "<button class='kb-back' id='kb-back-btn' type='button'>&#8592; 返回知识库</button>" +
      "<div class='kb-detail-head'><div class='kb-detail-ident'><span class='kb-detail-icon'>" + iconSmall(IC.kb, 22) + "</span><div><div class='kb-detail-title-row'><h1>" + esc(kb.name) + "</h1>" + stateBadge(kb.status) + "</div><p>" + esc(kb.description || "尚未填写知识库说明") + "</p></div></div>" +
      "<a class='btn-sm btn-secondary' href='/?knowledge_base_id=" + encodeURIComponent(kb.id) + "'>在聊天中使用</a></div>" +
      "<div class='kb-summary-grid detail'>" +
        kbSummaryItem(kb.document_count || 0, "文档", (kb.ready_document_count || 0) + " 个可检索") +
        kbSummaryItem(kb.chunk_count || 0, "知识分块", "按段落建立索引") +
        kbSummaryItem(kb.processing_document_count || 0, "处理中", "解析与向量化") +
        kbSummaryItem(kb.failed_document_count || 0, "异常", kb.failed_document_count ? "请检查文档" : "无异常", kb.failed_document_count > 0) +
      "</div>" +
      "<nav class='kb-tabs' aria-label='知识库功能'>" +
        knowledgeTabButton("documents", "文档", tab, kb.document_count || 0) +
        knowledgeTabButton("search", "检索测试", tab) +
        knowledgeTabButton("settings", "设置", tab) +
      "</nav>" +
      "<div class='kb-tab-pane'>" + pane + "</div>" +
    "</div>";
  $("#kb-back-btn").onclick = async () => {
    state.knowledgeCurrentId = null;
    state.knowledgeSearchResults = null;
    state.knowledgeSearchQuery = "";
    await renderKnowledgeView();
  };
  box.querySelectorAll("[data-kb-tab]").forEach((button) => {
    button.onclick = async () => {
      state.knowledgeTab = button.dataset.kbTab;
      await renderKnowledgeView();
    };
  });
  if (tab === "documents") {
    wireKnowledgeDocuments(box, kb, documents);
    box.querySelectorAll("[data-kb-page]").forEach(button => { button.onclick = async () => {
      button.disabled = true;
      state.knowledgePage = Math.max(1, state.knowledgePage + Number(button.dataset.kbPage));
      await renderKnowledgeDetail(box, kbId);
    }; });
  }
  if (tab === "search") wireKnowledgeSearch(box, kb);
  if (tab === "settings") wireKnowledgeSettings(box, kb);
}

/** 知识库页签按钮（count 为 null 时不显示计数角标）。 */
function knowledgeTabButton(id, label, current, count) {
  return "<button class='kb-tab" + (current === id ? " active" : "") + "' data-kb-tab='" + id + "' type='button'>" + esc(label) + (count == null ? "" : "<span>" + count + "</span>") + "</button>";
}

/** 文档页签内容：上传区 + 分块参数 + 已索引文档列表（含重建索引/移出按钮）。 */
function renderKnowledgeDocumentsPane(kb, documents) {
  const documentRows = documents.length ? documents.map((doc) => {
    const type = inferTypeFromName(doc.name);
    const download = doc.download_url ? "<a class='btn-sm btn-secondary' href='" + authUrl(doc.download_url) + "' target='_blank'>下载</a>" : "";
    const warningClass = doc.status === "ready" ? "kb-doc-warning" : "kb-doc-error";
    const error = doc.error_summary ? "<div class='" + warningClass + "'>" + esc(doc.error_summary) + "</div>" : "";
    return "<article class='kb-doc-row'>" +
      artIcon(type) +
      "<div class='kb-doc-main'><div class='kb-doc-title-row'><strong title='" + esc(doc.name) + "'>" + esc(doc.name) + "</strong>" + stateBadge(doc.status) + "</div>" +
      "<div class='kb-doc-meta'><span>" + formatKBBytes(doc.size) + "</span><span>" + (doc.chunk_count || 0) + " 个分块</span><span>" + (doc.image_count ? doc.image_count + " 张图片已识别" : "无可识别图片") + "</span><span>分块 " + doc.chunk_size + " / 重叠 " + doc.overlap + "</span><span>更新于 " + formatKBDate(doc.updated_at) + "</span></div>" + error + "</div>" +
      "<div class='kb-doc-actions'>" + download + "<button class='btn-sm btn-secondary' data-reindex-doc='" + doc.id + "' type='button'>重建索引</button><button class='btn-sm btn-danger' data-remove-doc='" + doc.id + "' type='button'>移出</button></div>" +
    "</article>";
  }).join("") : "<div class='kb-empty compact'><span class='kb-empty-icon'>" + IC.files + "</span><h2>还没有文档</h2><p>上传本地文件，或从资料库选择已有文件建立索引。</p></div>";
  return "<section class='kb-ingest-card'>" +
      "<div class='kb-section-title'><div><strong>添加文档</strong><span>支持 PDF、DOCX、PPTX、XLSX、CSV、文本和图片；文档内图片会自动调用 " + esc(kb.vision_model || "视觉模型") + " 识别。</span></div><span class='kb-vision-chip'>" + IC.eye + " 图片自动识别</span></div>" +
      "<div class='kb-ingest-layout'><label class='kb-dropzone' for='kb-document-upload'>" + IC.upload + "<strong>选择本地文件</strong><span id='kb-file-choice'>可多选，正文与图片内容会一起建立索引</span><input id='kb-document-upload' class='hidden' type='file' multiple accept='.pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.html,.png,.jpg,.jpeg,.webp'></label>" +
      "<div class='kb-ingest-settings'><div class='kb-settings-title'>分块参数</div><div class='kb-parameter-row'><label class='kb-field'><span>每块字符数</span><input id='kb-chunk-size' type='number' min='200' max='2000' step='50' value='500'></label><label class='kb-field'><span>重叠字符数</span><input id='kb-overlap' type='number' min='0' max='400' step='10' value='80'></label></div><p>较小分块定位更准，适合条款；较大分块保留上下文，适合报告。重叠可避免语义在边界处丢失。</p></div></div>" +
      "<div class='kb-ingest-actions'><button class='btn-sm btn-secondary' id='kb-library-btn' type='button'>" + IC.library + " 从资料库选择</button><span class='kb-ingest-progress muted' id='kb-ingest-progress'></span><button class='btn-sm btn-primary' id='kb-upload-btn' type='button'>上传并建立索引</button></div>" +
      "<div class='kb-library-picker hidden' id='kb-library-picker'></div>" +
    "</section>" +
    "<section class='kb-documents-section'><div class='kb-section-title'><div><strong>已索引文档</strong><span>本页 " + documents.length + " 个文档；重建索引会使用上方当前分块参数。</span></div><label class='kb-list-search compact'>" + IC.search + "<input id='kb-doc-filter' placeholder='筛选本页文档' aria-label='筛选本页文档'></label></div>" +
      "<div class='kb-doc-list'>" + documentRows + "</div></section>";
}

/**
 * 读取并校验分块参数。
 * @returns {{chunk_size: number, overlap: number}}
 * @throws {Error} 字符数不在 200–2000、重叠不在 0–400 或重叠不小于块大小时抛出（文案直接展示给用户）
 */
function readKnowledgeChunkSettings() {
  const chunkSize = Number($("#kb-chunk-size").value);
  const overlap = Number($("#kb-overlap").value);
  if (!Number.isInteger(chunkSize) || chunkSize < 200 || chunkSize > 2000) throw new Error("每块字符数需为 200–2000");
  if (!Number.isInteger(overlap) || overlap < 0 || overlap > 400) throw new Error("重叠字符数需为 0–400");
  if (overlap >= chunkSize) throw new Error("重叠字符数必须小于每块字符数");
  return { chunk_size: chunkSize, overlap: overlap };
}

/**
 * 逐个把资料库文件加入知识库索引（串行，避免后端排队/限流）。
 * @returns {{completed: number, failures: string[]}} 失败项保留「文件名：原因」，供调用方汇总提示；不抛异常。
 */
async function ingestKnowledgeFileIds(kbId, files, settings, progressEl) {
  let completed = 0;
  const failures = [];
  for (const file of files) {
    progressEl.textContent = "正在处理 " + (completed + 1) + "/" + files.length + "：" + file.name;
    try {
      await API.post("/api/knowledge/documents", {
        kb_id: kbId, file_id: file.id,
        chunk_size: settings.chunk_size, overlap: settings.overlap
      });
      completed += 1;
    } catch (error) {
      failures.push(file.name + "：" + error.message);
    }
  }
  return { completed: completed, failures: failures };
}

/**
 * 绑定文档页签的交互：本地上传（先传 /api/files 再入库）、从资料库选择、重建索引、移出。
 * 上传采用「先传文件再建索引」两步，所以失败汇总里区分「上传失败」与「入库失败」。
 * 「移出」只解除索引，资料库原文件保留（提示文案已写明）。
 */
function wireKnowledgeDocuments(box, kb, documents) {
  const fileInput = $("#kb-document-upload");
  fileInput.onchange = () => {
    const files = Array.from(fileInput.files || []);
    $("#kb-file-choice").textContent = files.length ? "已选择 " + files.length + " 个文件：" + files.map((file) => file.name).join("、") : "可多选，上传后自动解析并建立索引";
  };
  $("#kb-upload-btn").onclick = async (event) => {
    const localFiles = Array.from(fileInput.files || []);
    if (!localFiles.length) return toast("请先选择要上传的文件");
    let settings;
    try { settings = readKnowledgeChunkSettings(); } catch (error) { return toast(error.message); }
    const button = event.currentTarget, progress = $("#kb-ingest-progress");
    button.disabled = true; button.textContent = "处理中…";
    const uploaded = [], failures = [];
    for (let index = 0; index < localFiles.length; index += 1) {
      progress.textContent = "正在上传 " + (index + 1) + "/" + localFiles.length + "：" + localFiles[index].name;
      try {
        const form = new FormData(); form.append("file", localFiles[index]);
        const fileRow = await API.post("/api/files", form, true);
        uploaded.push(fileRow);
      } catch (error) {
        failures.push(localFiles[index].name + "：" + error.message);
      }
    }
    const result = await ingestKnowledgeFileIds(kb.id, uploaded, settings, progress);
    failures.push.apply(failures, result.failures);
    toast(failures.length ? "完成 " + result.completed + " 个，失败 " + failures.length + " 个" : "已完成 " + result.completed + " 个文档的索引");
    await renderKnowledgeView();
  };
  $("#kb-library-btn").onclick = async () => {
    const picker = $("#kb-library-picker");
    if (!picker.classList.contains("hidden")) { picker.classList.add("hidden"); return; }
    picker.classList.remove("hidden");
    picker.innerHTML = "<div class='kb-picker-loading'>正在加载资料库…</div>";
    try {
      const files = (await API.get("/api/files")).items || [];
      const indexedIds = new Set(documents.map((doc) => doc.file_id));
      picker.innerHTML = files.length ? "<div class='kb-picker-head'><strong>选择资料库文件</strong><span>已在当前知识库中的文件再次加入时会按新参数重建索引。</span></div><div class='kb-picker-list'>" +
        files.map((file) => "<label class='kb-picker-row'><input type='checkbox' value='" + file.id + "' data-file-name='" + esc(file.name) + "'><span>" + artIcon(inferTypeFromName(file.name)) + "</span><span class='kb-picker-main'><strong>" + esc(file.name) + "</strong><small>" + formatKBBytes(file.size) + " · " + esc(file.mime_type || "文件") + (indexedIds.has(file.id) ? " · 已加入" : "") + "</small></span></label>").join("") +
        "</div><div class='kb-picker-actions'><button class='btn-sm btn-secondary' id='kb-picker-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='kb-picker-add' type='button'>加入知识库</button></div>" :
        "<div class='kb-picker-empty'>资料库中还没有文件，请先上传本地文件。</div>";
      if ($("#kb-picker-cancel")) $("#kb-picker-cancel").onclick = () => picker.classList.add("hidden");
      if ($("#kb-picker-add")) $("#kb-picker-add").onclick = async (event) => {
        const selected = Array.from(picker.querySelectorAll("input:checked")).map((input) => ({ id: input.value, name: input.dataset.fileName }));
        if (!selected.length) return toast("请选择至少一个文件");
        let settings;
        try { settings = readKnowledgeChunkSettings(); } catch (error) { return toast(error.message); }
        const button = event.currentTarget, progress = $("#kb-ingest-progress");
        button.disabled = true; button.textContent = "处理中…";
        const result = await ingestKnowledgeFileIds(kb.id, selected, settings, progress);
        toast(result.failures.length ? "完成 " + result.completed + " 个，失败 " + result.failures.length + " 个" : "已完成 " + result.completed + " 个文档的索引");
        await renderKnowledgeView();
      };
    } catch (error) {
      picker.innerHTML = "<div class='kb-picker-empty'>资料库加载失败：" + esc(error.message) + "</div>";
    }
  };
  $("#kb-doc-filter").oninput = (event) => {
    const query = event.target.value.trim().toLowerCase();
    box.querySelectorAll(".kb-doc-row").forEach((row) => row.classList.toggle("hidden", !row.textContent.toLowerCase().includes(query)));
  };
  box.querySelectorAll("[data-reindex-doc]").forEach((button) => {
    button.onclick = async () => {
      let settings;
      try { settings = readKnowledgeChunkSettings(); } catch (error) { return toast(error.message); }
      if (!confirm("使用当前分块参数重建该文档索引？")) return;
      button.disabled = true; button.textContent = "重建中…";
      try {
        await API.post("/api/knowledge/" + kb.id + "/documents/" + button.dataset.reindexDoc + "/reindex", settings);
        toast("索引已重建");
        await renderKnowledgeView();
      } catch (error) {
        toast("重建失败：" + error.message);
        button.disabled = false; button.textContent = "重建索引";
      }
    };
  });
  box.querySelectorAll("[data-remove-doc]").forEach((button) => {
    button.onclick = async () => {
      if (!confirm("将该文档移出知识库？资料库中的原文件不会删除。")) return;
      try {
        await API.del("/api/knowledge/" + kb.id + "/documents/" + button.dataset.removeDoc);
        toast("文档已移出知识库");
        await renderKnowledgeView();
      } catch (error) { toast("移出失败：" + error.message); }
    };
  });
}

/** 检索测试页签：展示召回结果（来源文件/位置/相关度），位置按幻灯片、工作表或页码区分。 */
function renderKnowledgeSearchPane(kb) {
  const results = state.knowledgeSearchResults;
  const searchMode = state.knowledgeSearchMode || "hybrid";
  const searchTopK = Number(state.knowledgeSearchTopK || 6);
  const modeHelp = {
    hybrid: ["混合检索", "同时考虑原词匹配和语义相近，适合大多数企业资料。"],
    semantic: ["语义检索", "寻找意思相近的内容，适合自然语言提问和同义表达。"],
    keyword: ["关键词检索", "优先命中原文词语，适合编号、名称和精确条款。"]
  }[searchMode];
  const selected = (value, current) => value === current ? " selected" : "";
  let resultHtml = "<div class='kb-search-empty'><span>" + IC.search + "</span><strong>输入一个真实业务问题</strong><p>这里展示模型实际会召回的内容、来源文件、分块位置和相关度。</p></div>";
  if (results) {
    resultHtml = results.length ? results.map((item, index) => {
      const location = item.location || {};
      const basePlace = location.slide != null ? "第 " + (Number(location.slide) + 1) + " 页幻灯片" : location.sheet ? "工作表 " + location.sheet + (location.cell ? " · " + location.cell : "") : location.page != null ? "第 " + (Number(location.page) + 1) + " 页" : "分块 " + ((location.ordinal || 0) + 1);
      const place = basePlace + (location.source === "vision" ? " · 图片识别" : "");
      return "<article class='kb-result-card'><div class='kb-result-head'><span class='kb-result-rank'>" + (index + 1) + "</span><div><strong>" + esc(item.file_name || "未知来源") + "</strong><span>" + esc(place) + "</span></div>" + (location.source === "vision" ? "<span class='kb-result-vision'>" + IC.eye + " 识图</span>" : "") + "<span class='kb-result-score'>相关度 " + Number(item.score || 0).toFixed(3) + "</span></div><p>" + esc(item.content) + "</p><div class='kb-result-foot'><span>分块 " + esc((item.chunk_id || "").slice(0, 10)) + "</span>" + (item.file_id ? "<a href='" + authUrl("/api/files/" + item.file_id + "/download") + "' target='_blank'>查看原文件</a>" : "") + "</div></article>";
    }).join("") : "<div class='kb-search-empty'><span>" + IC.search + "</span><strong>没有找到匹配内容</strong><p>可改用混合检索、增加返回数量，或检查文档是否已经完成索引。</p></div>";
  }
  return "<section class='kb-search-workbench'><div class='kb-search-form'><div class='kb-section-title'><div><strong>检索测试</strong><span>直接验证这个知识库对问题的召回效果，不会创建聊天。</span></div></div>" +
    "<label class='kb-field kb-field-wide'><span>测试问题</span><textarea id='kb-search-query' rows='3' placeholder='例如：本次投标对项目经理的资质和业绩有哪些硬性要求？'>" + esc(state.knowledgeSearchQuery || "") + "</textarea></label>" +
    "<div class='kb-search-options'><label class='kb-field'><span>检索方式</span><select id='kb-search-mode'><option value='hybrid'" + selected("hybrid", searchMode) + ">混合检索（推荐）</option><option value='semantic'" + selected("semantic", searchMode) + ">语义检索</option><option value='keyword'" + selected("keyword", searchMode) + ">关键词检索</option></select></label><label class='kb-field'><span>返回数量</span><select id='kb-search-topk'><option" + selected(3, searchTopK) + ">3</option><option" + selected(6, searchTopK) + ">6</option><option" + selected(10, searchTopK) + ">10</option><option" + selected(20, searchTopK) + ">20</option></select></label><div class='kb-search-mode-help'><strong>" + modeHelp[0] + "</strong><span>" + modeHelp[1] + "</span></div><button class='btn-sm btn-primary' id='kb-search-submit' type='button'>" + IC.search + " 开始检索</button></div></div>" +
    "<div class='kb-search-results'><div class='kb-results-heading'><strong>召回结果</strong><span>" + (results ? results.length + " 条" : "等待测试") + "</span></div><div id='kb-result-list'>" + resultHtml + "</div></div></section>";
}

/** 绑定检索测试：提交后把结果写进 state 并整页重渲染；快捷键为 Ctrl/Cmd+Enter。 */
function wireKnowledgeSearch(box, kb) {
  const help = box.querySelector(".kb-search-mode-help");
  $("#kb-search-mode").onchange = (event) => {
    state.knowledgeSearchMode = event.target.value;
    const content = {
      hybrid: ["混合检索", "同时考虑原词匹配和语义相近，适合大多数企业资料。"],
      semantic: ["语义检索", "寻找意思相近的内容，适合自然语言提问和同义表达。"],
      keyword: ["关键词检索", "优先命中原文词语，适合编号、名称和精确条款。"]
    }[event.target.value];
    help.innerHTML = "<strong>" + content[0] + "</strong><span>" + content[1] + "</span>";
  };
  const runSearch = async () => {
    const query = $("#kb-search-query").value.trim();
    if (!query) return toast("请输入测试问题");
    state.knowledgeSearchQuery = query;
    state.knowledgeSearchMode = $("#kb-search-mode").value;
    state.knowledgeSearchTopK = Number($("#kb-search-topk").value);
    const button = $("#kb-search-submit");
    button.disabled = true; button.textContent = "检索中…";
    try {
      const response = await API.post("/api/knowledge/search", {
        query: query,
        knowledge_base_ids: [kb.id],
        mode: state.knowledgeSearchMode,
        top_k: state.knowledgeSearchTopK
      });
      state.knowledgeSearchResults = response.items || [];
      await renderKnowledgeView();
    } catch (error) {
      toast("检索失败：" + error.message);
      button.disabled = false; button.innerHTML = IC.search + " 开始检索";
    }
  };
  $("#kb-search-submit").onclick = runSearch;
  $("#kb-search-query").onkeydown = (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); runSearch(); }
  };
}

/** 设置页签：可改名称与说明；可见范围、向量模型、识图模型为只读（由后端与环境决定，页面不允许改）。 */
function renderKnowledgeSettingsPane(kb) {
  return "<div class='kb-settings-layout'><section class='card kb-settings-card'><div class='kb-section-title'><div><strong>基本信息</strong><span>帮助成员判断该知识库应该在什么场景使用。</span></div></div>" +
    "<div class='kb-form-grid'><label class='kb-field kb-field-wide'><span>知识库名称</span><input id='kb-settings-name' maxlength='200' value='" + esc(kb.name) + "'></label><label class='kb-field kb-field-wide'><span>说明</span><textarea id='kb-settings-desc' maxlength='4000' rows='5'>" + esc(kb.description || "") + "</textarea></label><label class='kb-field'><span>可见范围</span><input value='当前工作区' readonly></label><label class='kb-field'><span>向量模型</span><input value='" + esc(kb.embedding_model || "—") + "' readonly></label><label class='kb-field kb-field-wide'><span>识图模型</span><input value='" + esc(kb.vision_model || "—") + (kb.vision_enabled ? "（已启用）" : "（已停用）") + "' readonly></label></div>" +
    "<div class='kb-form-actions'><span class='muted'>最近更新：" + formatKBDate(kb.updated_at) + "</span><button class='btn-sm btn-primary' id='kb-settings-save' type='button'>保存更改</button></div></section>" +
    "<section class='card kb-danger-card'><div><strong>删除知识库</strong><p>删除所有文档索引和知识分块。资料库中的原始文件仍会保留。</p></div><button class='btn-sm btn-danger kb-danger-button' id='kb-delete-btn' type='button'>删除知识库</button></section></div>";
}

/** 绑定设置页签的保存与删除：删除前二次确认，且明确说明只删索引、不删资料库原文件。 */
function wireKnowledgeSettings(box, kb) {
  $("#kb-settings-save").onclick = async (event) => {
    const name = $("#kb-settings-name").value.trim();
    if (!name) return toast("知识库名称不能为空");
    const button = event.currentTarget;
    button.disabled = true; button.textContent = "保存中…";
    try {
      await API.req("PATCH", "/api/knowledge/" + kb.id, { name: name, description: $("#kb-settings-desc").value.trim() });
      toast("设置已保存");
      await renderKnowledgeView();
    } catch (error) {
      toast("保存失败：" + error.message);
      button.disabled = false; button.textContent = "保存更改";
    }
  };
  $("#kb-delete-btn").onclick = async () => {
    if (!confirm("确定删除“" + kb.name + "”？该操作会删除全部索引，且不可撤销。")) return;
    try {
      await API.del("/api/knowledge/" + kb.id);
      state.knowledgeCurrentId = null;
      state.knowledgeSearchResults = null;
      state.knowledgeSearchQuery = "";
      toast("知识库已删除");
      await renderKnowledgeView();
    } catch (error) { toast("删除失败：" + error.message); }
  };
}
/** 产品状态徽标（插件/技能/连接器/数据源/运行记录通用），已知状态给中文文案与配色。 */
function productStatusBadge(status) {
  const labels = {
    draft: "草稿",
    published: "已发布",
    archived: "已归档",
    active: "正常",
    ready: "可用",
    completed: "已完成",
    running: "执行中",
    created: "等待开始",
    queued: "排队中",
    waiting: "等待中",
    failed: "未完成",
    cancelled: "已取消"
  };
  const tone = ["published", "active", "ready", "completed"].includes(status) ? " published" : status === "failed" ? " danger" : "";
  return "<span class='badge product-status" + tone + "'>" + esc(labels[status] || status || "未知") + "</span>";
}

/** 提供商标识转中文（http/mcp/database/builtin/sandbox 与数据库厂商），未知 provider 原样显示。 */
function providerLabel(provider) {
  const labels = { http: "企业接口", mcp: "MCP 服务", database: "数据库", builtin: "平台内置", sandbox: "安全程序", mysql: "MySQL", postgres: "PostgreSQL", siliconflow: "硅基流动", agnes: "Agnes" };
  return labels[provider] || provider || "未指定";
}

/** 完整日期时间（年-月-日 时:分）；解析失败时截断原串，避免出现 Invalid Date。 */
function formatProductDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
  return date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** 空态卡片模板（列表页无数据时统一使用，actionHtml 由调用方按权限决定是否给操作按钮）。 */
function customerEmpty(icon, title, description, actionHtml) {
  return "<div class='customer-empty'><span>" + icon + "</span><h2>" + esc(title) + "</h2><p>" + esc(description) + "</p>" + (actionHtml || "") + "</div>";
}

/** 插件列表页：已发布插件可直接「在聊天中使用」，未发布的先发布；空态给创建入口。 */
async function renderPluginsView() {
  const box = contentEl("plugins"); if (!box) return;
  const r = await API.get("/api/plugins"); state.plugins = r.items || [];
  const cards = state.plugins.length ? state.plugins.map((plugin) => {
    const useButton = plugin.status === "published" && plugin.published_version_id ?
      "<a class='btn-sm btn-primary' href='/?plugin_id=" + encodeURIComponent(plugin.id) + "&plugin_version_id=" + encodeURIComponent(plugin.published_version_id) + "'>在聊天中使用</a>" :
      "<button class='btn-sm btn-secondary' data-publish-plugin='" + plugin.id + "' type='button'>发布后使用</button>";
    return "<article class='customer-card plugin-product-card' data-plugin-card='" + plugin.id + "'><div class='customer-card-head'><span class='customer-card-icon plugin'>" + IC.plugin + "</span><div><h2>" + esc(plugin.name) + "</h2><p>" + esc(plugin.description || "尚未填写用途说明") + "</p></div>" + productStatusBadge(plugin.status) + "</div>" +
      "<div class='customer-card-facts'><span>面向团队复用</span><span>" + (plugin.visibility === "workspace" ? "当前工作区可用" : "仅自己可用") + "</span></div>" +
      "<div class='customer-card-actions'>" + useButton + "<button class='btn-sm btn-secondary' data-plugin-detail='" + plugin.id + "' type='button'>查看与管理</button></div></article>";
  }).join("") : customerEmpty(IC.plugin, "还没有可复用插件", "把稳定的工作方法、资料和执行能力组合起来，团队下次可以直接使用。", "<button class='btn-sm btn-primary' id='plugin-empty-create' type='button'>创建第一个插件</button>");
  box.innerHTML = "<div class='panel customer-page'><div class='customer-page-head'><div><h1>插件</h1><p>把已经验证过的工作方式保存成团队可重复使用的业务能力。</p></div><button class='btn-sm btn-primary' id='plugin-create-open' type='button'>" + IC.plus + " 新建插件</button></div>" +
    "<section class='customer-create-panel hidden' id='plugin-create-panel'><div class='customer-section-head'><div><strong>新建业务插件</strong><span>先描述它解决什么问题；方法和工具可以随后逐步完善。</span></div><button class='icon-btn' id='plugin-create-close' aria-label='关闭'>" + IC.close + "</button></div>" +
    "<div class='customer-form-grid'><label class='kb-field'><span>插件名称</span><input id='pg-name' placeholder='例如：财务经营分析'></label><label class='kb-field'><span>主要输出</span><select id='pg-output'><option value='report'>分析报告</option><option value='presentation'>演示文稿</option><option value='spreadsheet'>数据表格</option><option value='document'>正式文档</option></select></label><label class='kb-field kb-field-wide'><span>用途说明</span><textarea id='pg-desc' rows='3' placeholder='谁在什么场景使用，它会完成什么结果'></textarea></label><label class='kb-field kb-field-wide'><span>执行要求</span><textarea id='pg-inst' rows='5' placeholder='按顺序写清关键步骤、数据来源和不能编造的内容'></textarea></label></div>" +
    "<div class='customer-form-actions'><button class='btn-sm btn-secondary' id='plugin-create-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='plugin-create-submit' type='button'>保存为草稿</button></div></section>" +
    "<div class='customer-card-grid'>" + cards + "</div></div>";
  const createPanel = $("#plugin-create-panel");
  const openCreate = () => { createPanel.classList.remove("hidden"); $("#pg-name").focus(); };
  $("#plugin-create-open").onclick = openCreate;
  if ($("#plugin-empty-create")) $("#plugin-empty-create").onclick = openCreate;
  $("#plugin-create-close").onclick = () => createPanel.classList.add("hidden");
  $("#plugin-create-cancel").onclick = () => createPanel.classList.add("hidden");
  $("#plugin-create-submit").onclick = async (event) => {
    const name = $("#pg-name").value.trim(), description = $("#pg-desc").value.trim(), instructions = $("#pg-inst").value.trim();
    if (!name) return toast("请输入插件名称");
    if (!description) return toast("请说明插件解决什么问题");
    const button = event.currentTarget; button.disabled = true; button.textContent = "保存中…";
    try {
      await API.post("/api/plugins", { name, description, spec: { name, description, instructions, skill_refs: [], tool_refs: [], resource_bindings: [], input_schema: {}, output_schema: { type: $("#pg-output").value } } });
      toast("插件草稿已创建"); await renderPluginsView();
    } catch (error) { toast("创建失败：" + error.message); button.disabled = false; button.textContent = "保存为草稿"; }
  };
  box.querySelectorAll("[data-publish-plugin]").forEach((button) => button.onclick = async () => {
    try { await API.post("/api/plugins/" + button.dataset.publishPlugin + "/publish", {}); toast("插件已发布，可在聊天中使用"); await renderPluginsView(); }
    catch (error) { toast("发布失败：" + error.message); }
  });
  box.querySelectorAll("[data-plugin-detail]").forEach((button) => button.onclick = () => openPluginManager(button.dataset.pluginDetail));
}

/**
 * 插件详情抽屉：读取最新版本 spec 展示执行要求/组成/版本记录，并支持本地测试任务。
 * 复制、共享、发布、删除均直接落库，成功后统一 closeMenu + 重新渲染列表。
 */
async function openPluginManager(pluginId) {
  const plugin = await API.get("/api/plugins/" + pluginId);
  const latest = (plugin.versions || [])[0] || {};
  const spec = latest.spec || {};
  const versions = (plugin.versions || []).map((version) => "<li><span>版本 " + version.version_number + "</span>" + productStatusBadge(version.status) + "</li>").join("");
  openDetail("插件 · " + plugin.name,
    "<div class='customer-detail'><div class='customer-detail-summary'><p>" + esc(plugin.description || "尚未填写用途说明") + "</p><div><span>当前状态</span>" + productStatusBadge(plugin.status) + "</div></div>" +
    "<section><h3>执行要求</h3><p class='customer-long-text'>" + esc(spec.instructions || "尚未设置执行要求") + "</p></section>" +
    "<section><h3>组成情况</h3><div class='customer-fact-grid'><div><strong>" + ((spec.skill_refs || []).length) + "</strong><span>工作方法</span></div><div><strong>" + ((spec.tool_refs || []).length) + "</strong><span>执行能力</span></div><div><strong>" + ((spec.resource_bindings || []).length) + "</strong><span>数据来源</span></div></div></section>" +
    "<section><h3>版本记录</h3><ul class='customer-version-list'>" + (versions || "<li>暂无版本</li>") + "</ul></section>" +
    "<section><h3>发布前测试</h3><label class='kb-field'><span>测试任务</span><textarea id='plugin-test-input' rows='3' placeholder='输入一个典型业务任务'></textarea></label><button class='btn-sm btn-secondary' id='plugin-test-run' type='button'>运行测试</button><div id='plugin-test-result'></div></section>" +
    "<div class='customer-detail-actions'><button class='btn-sm btn-secondary' id='plugin-clone' type='button'>复制为新插件</button><button class='btn-sm btn-secondary' id='plugin-share' type='button'>共享给工作区</button>" + (plugin.status !== "published" ? "<button class='btn-sm btn-primary' id='plugin-publish' type='button'>发布插件</button>" : "") + "<button class='btn-sm btn-danger' id='plugin-delete' type='button'>删除</button></div>" +
    "<details class='advanced-details'><summary>高级配置</summary><pre>" + esc(JSON.stringify(spec, null, 2)) + "</pre></details></div>");
  $("#plugin-test-run").onclick = async (event) => {
    const input = $("#plugin-test-input").value.trim(); if (!input) return toast("请输入测试任务");
    const button = event.currentTarget; button.disabled = true; button.textContent = "测试中…";
    try { const result = await API.post("/api/plugins/" + pluginId + "/test-run", { input }); $("#plugin-test-result").innerHTML = "<div class='customer-inline-result'>" + esc((result.output && result.output.text) || result.summary || "测试完成") + "</div>"; }
    catch (error) { $("#plugin-test-result").innerHTML = "<div class='customer-inline-error'>" + esc(error.message) + "</div>"; }
    finally { button.disabled = false; button.textContent = "运行测试"; }
  };
  $("#plugin-clone").onclick = async () => { await API.post("/api/plugins/" + pluginId + "/clone", {}); closeMenu(); toast("已复制为新插件"); renderPluginsView(); };
  $("#plugin-share").onclick = async () => { await API.post("/api/plugins/" + pluginId + "/share", { visibility: "workspace" }); closeMenu(); toast("已共享给当前工作区"); renderPluginsView(); };
  if ($("#plugin-publish")) $("#plugin-publish").onclick = async () => { await API.post("/api/plugins/" + pluginId + "/publish", {}); closeMenu(); toast("插件已发布"); renderPluginsView(); };
  $("#plugin-delete").onclick = async () => { if (!confirm("删除该插件及其草稿？")) return; await API.del("/api/plugins/" + pluginId); closeMenu(); toast("插件已删除"); renderPluginsView(); };
}
/** 技能列表页：新建表单把「执行步骤」与「不可违反的规则」分开收集（规则按行拆分）。 */
async function renderSkillsView() {
  const box = contentEl("skills"); if (!box) return;
  const r = await API.get("/api/skills");
  const skills = r.items || [];
  const cards = skills.length ? skills.map((skill) => "<article class='customer-card'><div class='customer-card-head'><span class='customer-card-icon skill'>" + IC.skill + "</span><div><h2>" + esc(skill.name) + "</h2><p>" + esc(skill.description || "尚未填写使用说明") + "</p></div>" + productStatusBadge(skill.status) + "</div><div class='customer-card-facts'><span>可独立测试</span><span>修改会生成新版本</span></div><div class='customer-card-actions'><button class='btn-sm btn-primary' data-skill-detail='" + skill.id + "' type='button'>查看与测试</button>" + (skill.status !== "published" ? "<button class='btn-sm btn-secondary' data-skill-publish='" + skill.id + "' type='button'>发布</button>" : "") + "</div></article>").join("") :
    customerEmpty(IC.skill, "还没有工作方法", "把团队稳定的方法、步骤和注意事项保存下来，AI 下次可以按同一标准执行。", "<button class='btn-sm btn-primary' id='skill-empty-create' type='button'>创建第一个技能</button>");
  box.innerHTML = "<div class='panel customer-page'><div class='customer-page-head'><div><h1>技能</h1><p>管理 AI 完成业务任务时遵循的方法、步骤和规则。</p></div><button class='btn-sm btn-primary' id='skill-create-open' type='button'>" + IC.plus + " 新建技能</button></div>" +
    "<section class='customer-create-panel hidden' id='skill-create-panel'><div class='customer-section-head'><div><strong>新建工作方法</strong><span>写成团队成员都能检查的具体步骤。</span></div><button class='icon-btn' id='skill-create-close' aria-label='关闭'>" + IC.close + "</button></div><div class='customer-form-grid'><label class='kb-field'><span>名称</span><input id='sk-name' placeholder='例如：招投标文件编制'></label><label class='kb-field'><span>适用场景</span><input id='sk-desc' placeholder='例如：根据招标要求生成投标材料'></label><label class='kb-field kb-field-wide'><span>执行步骤</span><textarea id='sk-inst' rows='6' placeholder='1. 先解析强制要求&#10;2. 再检索公司资料&#10;3. 缺失内容必须标注'></textarea></label><label class='kb-field kb-field-wide'><span>不可违反的规则</span><textarea id='sk-rules' rows='4' placeholder='每行一条，例如：企业资质禁止编造'></textarea></label></div><div class='customer-form-actions'><button class='btn-sm btn-secondary' id='skill-create-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='skill-create-submit' type='button'>保存为草稿</button></div></section>" +
    "<div class='customer-card-grid'>" + cards + "</div></div>";
  const panel = $("#skill-create-panel"), openCreate = () => { panel.classList.remove("hidden"); $("#sk-name").focus(); };
  $("#skill-create-open").onclick = openCreate; if ($("#skill-empty-create")) $("#skill-empty-create").onclick = openCreate;
  $("#skill-create-close").onclick = () => panel.classList.add("hidden"); $("#skill-create-cancel").onclick = () => panel.classList.add("hidden");
  $("#skill-create-submit").onclick = async (event) => {
    const name = $("#sk-name").value.trim(), description = $("#sk-desc").value.trim(), instructions = $("#sk-inst").value.trim();
    if (!name || !instructions) return toast("请填写名称和执行步骤");
    const rules = $("#sk-rules").value.split("\n").map((item) => item.trim()).filter(Boolean);
    const button = event.currentTarget; button.disabled = true; button.textContent = "保存中…";
    try { await API.post("/api/skills", { name, description, spec: { name, description, instructions, rules, recommended_tools: [], required_resources: [] } }); toast("技能草稿已创建"); await renderSkillsView(); }
    catch (error) { toast("创建失败：" + error.message); button.disabled = false; button.textContent = "保存为草稿"; }
  };
  box.querySelectorAll("[data-skill-publish]").forEach((button) => button.onclick = async () => { await API.post("/api/skills/" + button.dataset.skillPublish + "/publish", {}); toast("技能已发布"); renderSkillsView(); });
  box.querySelectorAll("[data-skill-detail]").forEach((button) => button.onclick = () => openSkillManager(button.dataset.skillDetail));
}

/** 技能详情抽屉：展示执行步骤与硬性规则，支持测试任务、发布、归档、删除。 */
async function openSkillManager(skillId) {
  const skill = await API.get("/api/skills/" + skillId);
  const latest = (skill.versions || [])[0] || {}, spec = latest.spec || {};
  const rules = (spec.rules || []).map((rule) => "<li>" + esc(rule) + "</li>").join("");
  const versions = (skill.versions || []).map((version) => "<li><span>版本 " + version.version_number + "</span>" + productStatusBadge(version.status) + "</li>").join("");
  openDetail("技能 · " + skill.name, "<div class='customer-detail'><div class='customer-detail-summary'><p>" + esc(skill.description || "尚未填写使用说明") + "</p><div><span>当前状态</span>" + productStatusBadge(skill.status) + "</div></div><section><h3>执行步骤</h3><p class='customer-long-text'>" + esc(spec.instructions || "尚未设置") + "</p></section><section><h3>必须遵守</h3><ul class='customer-rule-list'>" + (rules || "<li>尚未设置规则</li>") + "</ul></section><section><h3>版本记录</h3><ul class='customer-version-list'>" + versions + "</ul></section>" +
    "<section><h3>测试这个方法</h3><label class='kb-field'><span>测试任务</span><textarea id='skill-test-input' rows='3' placeholder='输入一个典型任务'></textarea></label><button class='btn-sm btn-secondary' id='skill-test-run' type='button'>运行测试</button><div id='skill-test-result'></div></section>" +
    "<div class='customer-detail-actions'>" + (skill.status !== "published" ? "<button class='btn-sm btn-primary' id='skill-publish' type='button'>发布技能</button>" : "<button class='btn-sm btn-secondary' id='skill-archive' type='button'>归档</button>") + "<button class='btn-sm btn-danger' id='skill-delete' type='button'>删除</button></div><details class='advanced-details'><summary>高级配置</summary><pre>" + esc(JSON.stringify(spec, null, 2)) + "</pre></details></div>");
  $("#skill-test-run").onclick = async (event) => { const input = $("#skill-test-input").value.trim(); if (!input) return toast("请输入测试任务"); const button = event.currentTarget; button.disabled = true; button.textContent = "测试中…"; try { const result = await API.post("/api/skills/" + skillId + "/test-run", { input }); $("#skill-test-result").innerHTML = "<div class='customer-inline-result'>" + esc((result.output && result.output.text) || "测试完成") + "</div>"; } catch (error) { $("#skill-test-result").innerHTML = "<div class='customer-inline-error'>" + esc(error.message) + "</div>"; } finally { button.disabled = false; button.textContent = "运行测试"; } };
  if ($("#skill-publish")) $("#skill-publish").onclick = async () => { await API.post("/api/skills/" + skillId + "/publish", {}); closeMenu(); toast("技能已发布"); renderSkillsView(); };
  if ($("#skill-archive")) $("#skill-archive").onclick = async () => { await API.post("/api/skills/" + skillId + "/archive", {}); closeMenu(); toast("技能已归档"); renderSkillsView(); };
  $("#skill-delete").onclick = async () => { if (!confirm("删除该技能及其草稿？")) return; await API.del("/api/skills/" + skillId); closeMenu(); toast("技能已删除"); renderSkillsView(); };
}
/** 连接器列表页：新建只要求粘贴接口说明，由后端 /derive 生成配置草稿（前端不解析接口）。 */
async function renderConnectorsView() {
  const box = contentEl("connectors"); if (!box) return;
  const r = await API.get("/api/connectors");
  const connectors = r.items || [];
  const cards = connectors.length ? connectors.map((connector) => "<article class='customer-card'><div class='customer-card-head'><span class='customer-card-icon connector'>" + IC.connector + "</span><div><h2>" + esc(connector.name) + "</h2><p>" + esc(providerLabel(connector.provider)) + " · " + (connector.visibility === "workspace" ? "工作区可用" : "仅自己可用") + "</p></div>" + productStatusBadge(connector.status) + "</div><div class='customer-card-facts'><span>调用前自动校验参数</span><span>执行过程保留记录</span></div><div class='customer-card-actions'><button class='btn-sm btn-primary' data-connector-detail='" + connector.id + "' type='button'>查看与测试</button>" + (connector.status !== "published" ? "<button class='btn-sm btn-secondary' data-connector-publish='" + connector.id + "' type='button'>发布</button>" : "") + "</div></article>").join("") :
    customerEmpty(IC.connector, "还没有程序连接", "连接企业接口、数据库或自动化服务后，AI 才能执行确定性的业务动作。", "<button class='btn-sm btn-primary' id='connector-empty-create' type='button'>添加第一个连接</button>");
  box.innerHTML = "<div class='panel customer-page'><div class='customer-page-head'><div><h1>连接器</h1><p>把企业已有程序接入平台，供 AI 在任务中安全调用。</p></div><button class='btn-sm btn-primary' id='connector-create-open' type='button'>" + IC.plus + " 添加程序连接</button></div>" +
    "<section class='customer-create-panel hidden' id='connector-create-panel'><div class='customer-section-head'><div><strong>根据接口说明生成配置</strong><span>粘贴用途、请求地址、参数和返回示例，系统会先生成草稿供测试。</span></div><button class='icon-btn' id='connector-create-close' aria-label='关闭'>" + IC.close + "</button></div><label class='kb-field'><span>接口说明</span><textarea id='cn-desc' rows='8' placeholder='例如：POST https://example.com/api/report，根据项目编号和日期生成报告，返回文件地址。请附上参数说明和示例。'></textarea></label><div class='customer-form-actions'><button class='btn-sm btn-secondary' id='connector-create-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='connector-create-submit' type='button'>生成配置草稿</button></div></section>" +
    "<div class='customer-card-grid'>" + cards + "</div></div>";
  const panel = $("#connector-create-panel"), openCreate = () => { panel.classList.remove("hidden"); $("#cn-desc").focus(); };
  $("#connector-create-open").onclick = openCreate; if ($("#connector-empty-create")) $("#connector-empty-create").onclick = openCreate;
  $("#connector-create-close").onclick = () => panel.classList.add("hidden"); $("#connector-create-cancel").onclick = () => panel.classList.add("hidden");
  $("#connector-create-submit").onclick = async (event) => { const description = $("#cn-desc").value.trim(); if (!description) return toast("请填写接口说明"); const button = event.currentTarget; button.disabled = true; button.textContent = "分析接口中…"; try { await API.post("/api/connectors/derive", { description }); toast("连接配置草稿已生成"); await renderConnectorsView(); } catch (error) { toast("生成失败：" + error.message); button.disabled = false; button.textContent = "生成配置草稿"; } };
  box.querySelectorAll("[data-connector-publish]").forEach((button) => button.onclick = async () => { await API.post("/api/connectors/" + button.dataset.connectorPublish + "/publish", {}); toast("程序连接已发布"); renderConnectorsView(); });
  box.querySelectorAll("[data-connector-detail]").forEach((button) => button.onclick = () => openConnectorManager(button.dataset.connectorDetail));
}

/**
 * 连接器详情抽屉：列出可调用操作并允许按操作试调。
 * 测试参数是高级输入，必须能解析成 JSON 对象，否则只提示格式错误、不发请求。
 */
async function openConnectorManager(connectorId) {
  const connector = await API.get("/api/connectors/" + connectorId);
  const latest = (connector.versions || [])[0] || {}, tools = latest.tools || [];
  const toolRows = tools.map((tool) => "<li><div><strong>" + esc(tool.name || tool.tool_name || "未命名操作") + "</strong><span>" + esc(tool.description || "尚未填写操作说明") + "</span></div>" + productStatusBadge(tool.status) + "</li>").join("");
  const firstTool = tools[0] && (tools[0].name || tools[0].tool_name);
  const toolOptions = tools.map((tool) => { const name = tool.name || tool.tool_name; return name ? "<option value=\"" + esc(name) + "\">" + esc(name) + "</option>" : ""; }).join("");
  openDetail("程序连接 · " + connector.name, "<div class='customer-detail'><div class='customer-detail-summary'><p>" + esc(providerLabel(connector.provider)) + "</p><div><span>当前状态</span>" + productStatusBadge(connector.status) + "</div></div><section><h3>可执行操作</h3><ul class='customer-tool-list'>" + (toolRows || "<li>尚未发现可调用操作</li>") + "</ul></section>" +
    (firstTool ? "<section><h3>测试连接</h3><label class='kb-field'><span>选择操作</span><select id='connector-test-tool'>" + toolOptions + "</select></label><p class='customer-help'>不需要参数时保持为空；需要参数时填写标准对象格式。</p><label class='kb-field'><span>测试参数（高级）</span><textarea id='connector-test-args' rows='5' placeholder='{}'>{}</textarea></label><button class='btn-sm btn-secondary' id='connector-test-run' type='button'>运行测试</button><div id='connector-test-result'></div></section>" : "") +
    "<div class='customer-detail-actions'>" + (connector.status !== "published" ? "<button class='btn-sm btn-primary' id='connector-publish' type='button'>发布连接</button>" : "") + "<button class='btn-sm btn-danger' id='connector-delete' type='button'>删除</button></div><details class='advanced-details'><summary>高级连接配置</summary><pre>" + esc(JSON.stringify(latest.spec || {}, null, 2)) + "</pre></details></div>");
  if ($("#connector-test-run")) $("#connector-test-run").onclick = async (event) => { let args; try { args = JSON.parse($("#connector-test-args").value || "{}"); } catch (error) { return toast("测试参数格式不正确"); } const button = event.currentTarget; button.disabled = true; button.textContent = "测试中…"; try { const result = await API.post("/api/connectors/" + connectorId + "/test-invoke", { tool_name: $("#connector-test-tool").value, arguments: args }); $("#connector-test-result").innerHTML = "<div class='customer-inline-result'>" + esc(result.status === "success" ? "连接与调用测试成功" : (result.error_summary || "测试完成")) + "</div>"; } catch (error) { $("#connector-test-result").innerHTML = "<div class='customer-inline-error'>" + esc(error.message) + "</div>"; } finally { button.disabled = false; button.textContent = "运行测试"; } };
  if ($("#connector-publish")) $("#connector-publish").onclick = async () => { await API.post("/api/connectors/" + connectorId + "/publish", {}); closeMenu(); toast("程序连接已发布"); renderConnectorsView(); };
  $("#connector-delete").onclick = async () => { if (!confirm("删除该程序连接？")) return; await API.del("/api/connectors/" + connectorId); closeMenu(); toast("程序连接已删除"); renderConnectorsView(); };
}
/**
 * 数据源页：非管理员只读（不渲染新增与删除按钮，仅提示由管理员维护）。
 * 新建表单默认 127.0.0.1:3306；切换数据库类型时仅在端口仍是默认值时才改写端口，避免覆盖用户输入。
 */
async function renderDatasourcesView() {
  const box = contentEl("datasources"); if (!box) return;
  const r = await API.get("/api/datasources");
  const sources = r.items || [], isAdmin = state.me && state.me.role === "admin";
  const cards = sources.length ? sources.map((source) => "<article class='customer-card datasource-card'><div class='customer-card-head'><span class='customer-card-icon datasource'>" + IC.ds + "</span><div><h2>" + esc(source.name) + "</h2><p>" + esc(providerLabel(source.provider)) + " · 默认只读</p></div>" + productStatusBadge(source.status) + "</div><div class='customer-card-facts'><span>仅允许查询</span><span>密码加密保存</span></div><div class='customer-card-actions'>" + (isAdmin ? "<button class='btn-sm btn-primary' data-ds-test='" + source.id + "' type='button'>测试连接</button><button class='btn-sm btn-danger' data-ds-delete='" + source.id + "' type='button'>删除</button>" : "<span class='customer-readonly-note'>由管理员维护</span>") + "</div></article>").join("") :
    customerEmpty(IC.ds, "还没有数据源", isAdmin ? "连接业务数据库后，AI 可以在只读边界内查询真实数据。" : "管理员尚未配置团队数据源。", isAdmin ? "<button class='btn-sm btn-primary' id='ds-empty-create' type='button'>连接第一个数据库</button>" : "");
  box.innerHTML = "<div class='panel customer-page'><div class='customer-page-head'><div><h1>数据源</h1><p>连接企业业务数据；数据库连接默认只读，密码以加密方式保存。</p></div>" + (isAdmin ? "<button class='btn-sm btn-primary' id='ds-create-open' type='button'>" + IC.plus + " 连接数据库</button>" : "") + "</div>" +
    (isAdmin ? "<section class='customer-create-panel hidden' id='ds-create-panel'><div class='customer-section-head'><div><strong>连接业务数据库</strong><span>保存前请确认使用的是只读账号。</span></div><button class='icon-btn' id='ds-create-close' aria-label='关闭'>" + IC.close + "</button></div><div class='customer-form-grid'><label class='kb-field'><span>连接名称</span><input id='ds-name' placeholder='例如：财务正式库'></label><label class='kb-field'><span>数据库类型</span><select id='ds-provider'><option value='mysql'>MySQL</option><option value='postgres'>PostgreSQL</option></select></label><label class='kb-field'><span>服务器地址</span><input id='ds-host' value='127.0.0.1' placeholder='数据库主机名或 IP'></label><label class='kb-field'><span>端口</span><input id='ds-port' type='number' min='1' max='65535' value='3306'></label><label class='kb-field'><span>数据库名称</span><input id='ds-db' placeholder='例如：finance'></label><label class='kb-field'><span>只读用户名</span><input id='ds-user' value='readonly' autocomplete='username'></label><label class='kb-field kb-field-wide'><span>密码</span><input id='ds-pass' type='password' autocomplete='new-password' placeholder='将加密保存，页面不会再次显示'></label></div><div class='customer-form-actions'><button class='btn-sm btn-secondary' id='ds-create-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='ds-create-submit' type='button'>保存连接</button></div></section>" : "<div class='customer-info-banner'>你可以在聊天中使用已配置的数据源；新增和删除连接由管理员完成。</div>") +
    "<div class='customer-card-grid'>" + cards + "</div></div>";
  if (isAdmin) {
    const panel = $("#ds-create-panel"), openCreate = () => { panel.classList.remove("hidden"); $("#ds-name").focus(); };
    $("#ds-create-open").onclick = openCreate; if ($("#ds-empty-create")) $("#ds-empty-create").onclick = openCreate;
    $("#ds-create-close").onclick = () => panel.classList.add("hidden"); $("#ds-create-cancel").onclick = () => panel.classList.add("hidden");
    $("#ds-provider").onchange = (event) => { if ($("#ds-port").value === "3306" || $("#ds-port").value === "5432") $("#ds-port").value = event.target.value === "postgres" ? "5432" : "3306"; };
    $("#ds-create-submit").onclick = async (event) => { const name = $("#ds-name").value.trim(), database = $("#ds-db").value.trim(), username = $("#ds-user").value.trim(); if (!name || !database || !username) return toast("请填写连接名称、数据库名称和只读用户名"); const button = event.currentTarget; button.disabled = true; button.textContent = "保存中…"; try { await API.post("/api/datasources", { name, provider: $("#ds-provider").value, connection_config: { host: $("#ds-host").value.trim(), port: Number($("#ds-port").value), username, database }, password: $("#ds-pass").value || null }); toast("数据源已保存"); await renderDatasourcesView(); } catch (error) { toast("保存失败：" + error.message); button.disabled = false; button.textContent = "保存连接"; } };
    box.querySelectorAll("[data-ds-test]").forEach((button) => button.onclick = async () => { button.disabled = true; button.textContent = "测试中…"; try { const result = await API.post("/api/datasources/" + button.dataset.dsTest + "/test", {}); toast(result.ok ? "连接成功，只读查询可用" : "连接失败：" + (result.detail || "请检查地址和账号")); } catch (error) { toast("连接失败：" + error.message); } finally { button.disabled = false; button.textContent = "测试连接"; } });
    box.querySelectorAll("[data-ds-delete]").forEach((button) => button.onclick = async () => { if (!confirm("删除该数据源连接？已保存的密码也会停用。")) return; await API.del("/api/datasources/" + button.dataset.dsDelete); toast("数据源已删除"); renderDatasourcesView(); });
  }
}
/**
 * 资料库页：分「我的文件」与「生成结果」两个页签（页签状态保存在 state.fileViewTab，切页不丢失）。
 * 搜索是前端过滤（按 data-search-text），计数随过滤结果更新。
 * 「继续使用」生成结果会把产物复制成文件并跳回对话页（带 file_id 预选）。
 */
async function renderFilesView() {
  const box = contentEl("files"); if (!box) return;
  const responses = await Promise.all([API.get("/api/files"), API.get("/api/artifacts")]);
  const files = responses[0].items || [], artifacts = responses[1].items || [];
  state.fileViewTab = state.fileViewTab || "files";
  const fileRows = files.length ? files.map((file) => "<article class='resource-row' data-resource-row data-search-text='" + esc((file.name + " " + file.mime_type).toLowerCase()) + "'><span class='resource-icon'>" + artIcon(inferTypeFromName(file.name)) + "</span><div class='resource-main'><strong>" + esc(file.name) + "</strong><span>" + esc(TYPE_LABEL[inferTypeFromName(file.name)] || "文件") + " · " + formatKBBytes(file.size) + " · 上传于 " + formatProductDate(file.created_at) + "</span></div><div class='resource-actions'><a class='btn-sm btn-secondary' href='" + authUrl("/api/files/" + file.id + "/download") + "' target='_blank'>下载</a><button class='btn-sm btn-secondary' data-file-preview='" + file.id + "' type='button'>查看内容</button><button class='icon-btn resource-more' data-file-more='" + file.id + "' aria-label='管理 " + esc(file.name) + "' title='更多操作'>•••</button></div></article>").join("") :
    customerEmpty(IC.files, "还没有文件", "上传本次任务需要的材料；文件不会自动进入长期知识库。", "");
  const artifactRows = artifacts.length ? artifacts.map((artifact) => {
    const download = artifact.external_url ? safeHref(artifact.external_url) : authUrl(artifact.download_url);
    return "<article class='resource-row' data-resource-row data-search-text='" + esc((artifact.name + " " + artifact.type).toLowerCase()) + "'><span class='resource-icon'>" + artIcon(artifact.type) + "</span><div class='resource-main'><strong>" + esc(artifact.name) + "</strong><span>" + esc(TYPE_LABEL[artifact.type] || artifact.type) + " · " + formatKBBytes(artifact.size) + " · 生成于 " + formatProductDate(artifact.created_at) + "</span></div><div class='resource-actions'><button class='btn-sm btn-primary' data-artifact-library-use='" + artifact.id + "' type='button'>继续使用</button><a class='btn-sm btn-secondary' href='" + download + "' target='_blank'>下载</a></div></article>";
  }).join("") : customerEmpty(IC.runs, "还没有生成结果", "聊天中生成的 Word、PPT、Excel、PDF 和图片会集中显示在这里。", "<a class='btn-sm btn-primary' href='/'>开始一个任务</a>");
  box.innerHTML = "<div class='panel customer-page'><div class='customer-page-head'><div><h1>资料库</h1><p>管理临时任务文件和 AI 生成结果；只有主动加入后，文件才会进入长期知识库。</p></div><label class='btn-sm btn-primary resource-upload-button'>" + IC.upload + " 上传文件<input type='file' id='file-upload-panel' multiple class='hidden' aria-label='上传一个或多个文件'></label></div>" +
    "<div class='resource-tabs'><button class='resource-tab" + (state.fileViewTab === "files" ? " active" : "") + "' data-resource-tab='files' type='button'>我的文件 <span>" + files.length + "</span></button><button class='resource-tab" + (state.fileViewTab === "artifacts" ? " active" : "") + "' data-resource-tab='artifacts' type='button'>生成结果 <span>" + artifacts.length + "</span></button></div>" +
    "<div class='resource-toolbar'><label class='kb-list-search'>" + IC.search + "<input id='resource-search' placeholder='搜索名称或类型' aria-label='搜索资料'></label><span class='muted' id='resource-count'>" + (state.fileViewTab === "files" ? files.length : artifacts.length) + " 项</span></div>" +
    "<section id='resource-files-pane' class='resource-list" + (state.fileViewTab === "files" ? "" : " hidden") + "'>" + fileRows + "</section><section id='resource-artifacts-pane' class='resource-list" + (state.fileViewTab === "artifacts" ? "" : " hidden") + "'>" + artifactRows + "</section></div>";
  $("#file-upload-panel").onchange = async (event) => {
    const selected = Array.from(event.target.files || []); if (!selected.length) return;
    const label = event.target.closest("label"); label.classList.add("busy"); const original = label.childNodes[1] ? label.childNodes[1].textContent : "";
    let completed = 0, failed = 0;
    for (const file of selected) {
      try { const form = new FormData(); form.append("file", file); await API.post("/api/files", form, true); completed += 1; }
      catch (error) { failed += 1; }
    }
    toast("上传完成：" + completed + " 个成功" + (failed ? "，" + failed + " 个失败" : ""));
    await renderFilesView();
  };
  box.querySelectorAll("[data-resource-tab]").forEach((button) => button.onclick = () => { state.fileViewTab = button.dataset.resourceTab; renderFilesView(); });
  $("#resource-search").oninput = (event) => { const query = event.target.value.trim().toLowerCase(), pane = state.fileViewTab === "files" ? $("#resource-files-pane") : $("#resource-artifacts-pane"); let shown = 0; pane.querySelectorAll("[data-resource-row]").forEach((row) => { const visible = row.dataset.searchText.includes(query); row.classList.toggle("hidden", !visible); if (visible) shown += 1; }); $("#resource-count").textContent = shown + " 项"; };
  box.querySelectorAll("[data-file-preview]").forEach((button) => button.onclick = async () => { button.disabled = true; try { const result = await API.get("/api/files/" + button.dataset.filePreview + "/text"); openDetail("文件内容", result.text ? "<div class='file-text-preview'>" + esc(result.text.slice(0, 8000)).replace(/\n/g, "<br>") + (result.text.length > 8000 ? "<p class='muted'>仅显示前 8,000 个字符</p>" : "") + "</div>" : customerEmpty(IC.eye, "没有可直接显示的文字", "图片或扫描文件会在知识库和聊天任务中使用识图模型分析。", "")); } catch (error) { toast("无法读取：" + error.message); } finally { button.disabled = false; } });
  box.querySelectorAll("[data-file-more]").forEach((button) => button.onclick = () => openFileManager(button.dataset.fileMore, files.find((file) => file.id === button.dataset.fileMore)));
  box.querySelectorAll("[data-artifact-library-use]").forEach((button) => button.onclick = async () => { try { const file = await API.post("/api/artifacts/" + button.dataset.artifactLibraryUse + "/use", {}); window.location.href = "/?file_id=" + encodeURIComponent(file.id); } catch (error) { toast("无法继续使用：" + error.message); } });
}

/** 文件管理抽屉：重命名、复制、删除；删除只动资料库文件，已建索引不会自动清理（提示已写明）。 */
function openFileManager(fileId, file) {
  if (!file) return;
  openDetail("管理文件", "<div class='customer-detail'><section><h3>" + esc(file.name) + "</h3><p>" + esc(file.mime_type) + " · " + formatKBBytes(file.size) + "</p></section><label class='kb-field'><span>新文件名</span><input id='file-rename-input' value='" + esc(file.name) + "'></label><div class='customer-detail-actions'><button class='btn-sm btn-primary' id='file-rename-save' type='button'>保存名称</button><button class='btn-sm btn-secondary' id='file-copy-action' type='button'>复制文件</button><button class='btn-sm btn-danger' id='file-delete-action' type='button'>删除文件</button></div></div>");
  $("#file-rename-save").onclick = async () => { const name = $("#file-rename-input").value.trim(); if (!name) return toast("文件名不能为空"); await API.req("PATCH", "/api/files/" + fileId, { name }); closeMenu(); toast("文件已重命名"); renderFilesView(); };
  $("#file-copy-action").onclick = async () => { await API.post("/api/files/" + fileId + "/copy", {}); closeMenu(); toast("文件已复制"); renderFilesView(); };
  $("#file-delete-action").onclick = async () => { if (!confirm("删除该文件？知识库中的已有索引不会自动删除。")) return; await API.del("/api/files/" + fileId); closeMenu(); toast("文件已删除"); renderFilesView(); };
}
/** 运行记录页：顶部按状态汇总（已完成/执行中/未完成），下方逐条列出任务与耗时。 */
async function renderRunsView() {
  const box = contentEl("runs"); if (!box) return;
  const r = await API.get("/api/runs");
  const runs = r.items || [];
  const rows = runs.length ? runs.map((run) => {
    const conversationAction = run.conversation_id
      ? "<a class='btn-sm btn-secondary' href='/?cid=" + encodeURIComponent(run.conversation_id) + "'>查看对话</a>"
      : "";
    return "<article class='run-row " + esc(run.status) + "'><span class='run-status-dot'></span><div class='run-row-main'><div><strong>" + esc(run.title || "未命名任务") + "</strong>" + productStatusBadge(run.status) + "</div><p>" + formatProductDate(run.created_at) + (run.duration_seconds != null ? " · 用时 " + run.duration_seconds + " 秒" : "") + "</p><div class='run-facts'><span>" + (run.step_count || 0) + " 个步骤</span><span>" + (run.tool_count || 0) + " 次执行</span><span>" + (run.artifact_count || 0) + " 个结果文件</span></div></div><div class='run-row-actions'>" + conversationAction + "<button class='btn-sm btn-primary' data-run-detail='" + run.id + "' type='button'>执行详情</button></div></article>";
  }).join("") :
    customerEmpty(IC.runs, "还没有任务记录", "从聊天发起任务后，可以在这里查看进度、失败原因和生成结果。", "<a class='btn-sm btn-primary' href='/'>开始聊天</a>");
  box.innerHTML = "<div class='panel customer-page'><div class='customer-page-head'><div><h1>运行记录</h1><p>查看每次任务是否完成、执行了哪些步骤，以及生成了什么结果。</p></div><a class='btn-sm btn-primary' href='/'>开始新任务</a></div><div class='run-summary-strip'><div><strong>" + runs.filter((run) => run.status === "completed").length + "</strong><span>已完成</span></div><div><strong>" + runs.filter((run) => run.status === "running").length + "</strong><span>执行中</span></div><div><strong>" + runs.filter((run) => run.status === "failed").length + "</strong><span>未完成</span></div></div><div class='run-list'>" + rows + "</div></div>";
  box.querySelectorAll("[data-run-detail]").forEach((button) => button.onclick = () => openRunDetail(button.dataset.runDetail));
}

/**
 * 运行详情抽屉：步骤按执行顺序展示，失败步骤优先展示工具自带的错误摘要；
 * 没有记录到步骤时也给出一行"准备任务"，不渲染空列表。
 */
async function openRunDetail(runId) {
  const detail = await API.get("/api/runs/" + runId), run = detail.run || {};
  const steps = detail.steps || [], tools = detail.tool_calls || [], artifacts = detail.artifacts || [];
  const stepRows = steps.length ? steps.map((step, index) => {
    const tool = tools.find((item) => item.id === step.tool_call_id || item.tool_name === step.name);
    return "<li class='run-step " + esc(step.status) + "'><span class='run-step-index'>" + (index + 1) + "</span><div><strong>" + esc(toolLabel(step.name) || "处理任务") + "</strong><p>" + (step.status === "completed" ? "执行完成" : step.status === "failed" ? esc(step.error_summary || (tool && tool.error_summary) || "执行失败") : "正在执行") + "</p></div>" + productStatusBadge(step.status) + "</li>";
  }).join("") : "<li class='run-step'><span class='run-step-index'>1</span><div><strong>准备任务</strong><p>没有记录到工具步骤</p></div></li>";
  const artifactRows = artifacts.map((artifact) => "<div class='run-artifact'><span>" + artIcon(artifact.type) + "</span><strong>" + esc(artifact.name) + "</strong><a class='btn-sm btn-secondary' href='" + authUrl(artifact.download_url) + "' target='_blank'>下载</a></div>").join("");
  openDetail("任务执行详情", "<div class='customer-detail run-detail'><div class='run-detail-hero'><div><span>任务状态</span>" + productStatusBadge(run.status) + "</div><div><span>开始时间</span><strong>" + formatProductDate(run.created_at) + "</strong></div><div><span>完成时间</span><strong>" + formatProductDate(run.completed_at) + "</strong></div></div>" +
    (run.error_summary ? "<div class='customer-inline-error'><strong>未完成原因</strong><p>" + esc(String(run.error_summary).slice(0, 800)) + "</p></div>" : "") +
    "<section><h3>执行步骤</h3><ol class='run-step-list'>" + stepRows + "</ol></section>" +
    "<section><h3>生成结果</h3>" + (artifactRows || "<p class='muted'>本次任务没有生成文件。</p>") + "</section>" +
    (run.conversation_id ? "<div class='customer-detail-actions'><a class='btn-sm btn-primary' href='/?cid=" + encodeURIComponent(run.conversation_id) + "'>回到对话</a></div>" : "") + "</div>");
}



// ---------------- 管理员控制台（独立 /admin.html） ----------------
/** 管理控制台首页：只是五个独立管理页面的入口卡片（真正的管理功能在各自 HTML 中，避免耦合）。 */
async function renderAdminDashboardView() {
  const box = contentEl("admin"); if (!box) return;
  const icon = "<span class='artifact-icon' style='background:#1c7ed6;width:34px;height:34px;'>" + IC.admin + "</span>";
  const cards = [["admin-members","成员管理","创建成员账号 / 调整 admin、member 角色", "team"],
                 ["admin-audit","审计日志","谁在何时对什么资源做了什么操作", "audit"],
                 ["admin-secrets","密钥管理","API Key / 数据库密码加密存储，仅引用使用", "lock"],
                 ["admin-models","模型配置","统一 Model Gateway：profile 管理、连通测试", "model"],
                 ["admin-ops","运行维护","服务健康、卡住任务(Run)统计与清理", "ops"]];
  const cardHtml = cards.map(function (x) {
    return "<a class='adm-card' href='/" + x[0] + ".html'><span class='artifact-icon' style='background:#1c7ed6;width:34px;height:34px;'>" + iconSmall(IC.admin) + "</span>" +
      "<div style='flex:1;min-width:0;'><div style='font-weight:600;font-size:14px;'>" + x[1] + "</div><div class='muted' style='font-size:12px;'>" + x[2] + "</div></div></a>";
  }).join("");
  box.innerHTML = "<div class='panel'>" +
    "<div class='welcome adm' style='text-align:center;margin-bottom:6px;'><h2>管理员控制台</h2>" +
    "<div class='muted' style='font-size:12.5px;'>选择下方功能进入，各自为独立页面：成员、审计、密钥、模型、运行维护。</div></div>" +
    "<div class='adm-grid'>" + cardHtml + "</div></div>";
}

// ---------------- 系统管理（独立页面） ----------------
/**
 * 成员管理页：列出工作区成员并允许切换 member/admin 角色，也可创建新账号。
 * 创建按钮走内联 onclick="window.createAdminUser()"，因此函数必须挂在 window 上。
 */
async function renderAdminMembersView() {
  const box = contentEl("admin-members"); if (!box) return;
  const ws = await API.get("/api/workspace");
  const mem = await API.get("/api/workspace/members");
  const rows = (mem.items || []).map((m) =>
    "<div class='card' style='display:flex;align-items:center;gap:10px;'><div class='user-avatar' style='width:28px;height:28px;font-size:12px;'>" + esc((m.display_name || "U").slice(0, 1)) + "</div>" +
    "<div style='flex:1;'><div style='font-weight:600;font-size:13.5px;'>" + esc(m.display_name) + "</div><div class='muted' style='font-size:12px;'>" + esc(m.user_id) + "</div></div>" +
    "<span class='badge" + (m.role === "admin" ? " published" : "") + "'>" + esc(m.role) + "</span>" +
    "<select class='admin-role' data-uid='" + m.user_id + "' aria-label='调整 " + esc(m.display_name || "成员") + " 的角色' style='padding:4px 8px;border:1px solid #d9d9dd;border-radius:8px;font-size:12px;'>" +
    "<option value='member'" + (m.role !== "admin" ? " selected" : "") + ">member</option><option value='admin'" + (m.role === "admin" ? " selected" : "") + ">admin</option></select></div>").join("");
  box.innerHTML = "<div class='panel'><h1>成员管理</h1><div class='sub'>工作区：<strong>" + esc(ws.name) + "</strong> · 角色：Admin 可管理成员与系统配置</div>" +
    "<div class='card'><div class='field-label' style='margin-bottom:6px;'>创建成员账号</div><div class='inline-form'>" +
    "<input id='adm-u-name' placeholder='登录用户名' style='width:150px;'><input id='adm-u-display' placeholder='显示名称' style='width:130px;'><input id='adm-u-pass' type='password' placeholder='初始密码' style='width:130px;'>" +
    "<select id='adm-u-role' aria-label='新成员角色' style='padding:7px 10px;'><option value='member'>普通成员</option><option value='admin'>管理员</option></select>" +
    "<button class='btn-sm btn-primary' onclick='window.createAdminUser()'>创建</button></div></div>" +
    "<div class='kv-grid'>" + rows + "</div></div>";
  box.querySelectorAll(".admin-role").forEach((s) => {
    s.onchange = async () => {
      try { await API.post("/api/workspace/members", { user_id: s.dataset.uid, role: s.value }); toast("已更新角色为 " + s.value); renderAdminMembersView(); }
      catch (e) { toast("更新失败：" + e.message); }
    };
  });
  window.createAdminUser = async function () {
    const username = $("#adm-u-name").value.trim(), password = $("#adm-u-pass").value;
    if (!username || !password) return toast("用户名与密码必填");
    try { await API.post("/api/workspace/users", { username, display_name: $("#adm-u-display").value.trim(), password, role: $("#adm-u-role").value }); toast("成员已创建"); renderAdminMembersView(); }
    catch (e) { toast("创建失败：" + e.message); }
  };
}
/** 审计日志页：只读展示最近 60 条操作记录（时间/动作/资源/资源 ID/操作人），不提供导出与删除。 */
async function renderAdminAuditView() {
  const box = contentEl("admin-audit"); if (!box) return;
  const r = await API.get("/api/audit?limit=60");
  const rows = (r.items || []).map((a) =>
    "<tr><td style='white-space:nowrap;'>" + esc((a.created_at || "").slice(0, 19)) + "</td><td>" + esc(a.action) + "</td><td>" + esc(a.resource_type) + "</td><td style='font-size:11.5px;'>" + esc(a.resource_id || "—") + "</td><td>" + esc(a.actor_user_id || "—").slice(0, 10) + "</td></tr>").join("");
  box.innerHTML = "<div class='panel'><h1>审计日志</h1><div class='sub'>谁、何时、对什么资源做了什么事（最近 60 条）。</div>" +
    "<div class='card' style='overflow:auto;' tabindex='0' aria-label='审计日志表格'><table style='width:100%;border-collapse:collapse;font-size:12.5px;'><thead><tr style='text-align:left;border-bottom:1px solid #eee;'><th style='padding:6px 8px;'>时间</th><th style='padding:6px 8px;'>动作</th><th style='padding:6px 8px;'>资源</th><th style='padding:6px 8px;'>资源ID</th><th style='padding:6px 8px;'>操作人</th></tr></thead><tbody>" + rows + "</tbody></table></div></div>";
}
/**
 * 密钥管理页：只展示名称/类型/加密方式，永不回显明文（后端也只返回元数据）。
 * 保存与删除通过 window.createSecret 暴露给内联 onclick。
 */
async function renderAdminSecretsView() {
  const box = contentEl("admin-secrets"); if (!box) return;
  const r = await API.get("/api/secrets");
  const rows = (r.items || []).map((s) =>
    "<div class='card' style='display:flex;align-items:center;gap:10px;'><span class='artifact-icon' style='background:#555;width:28px;height:28px;'>" + iconSmall(IC.lock) + "</span>" +
    "<div style='flex:1;'><div style='font-weight:600;font-size:13.5px;'>" + esc(s.name) + "</div><div class='muted' style='font-size:12px;'>" + esc(s.provider) + " · 加密存储(AES-GCM)</div></div>" +
    "<button class='btn-sm btn-secondary btn-danger' data-del='" + s.id + "'>删除</button></div>").join("");
  box.innerHTML = "<div class='panel'><h1>密钥管理</h1><div class='sub'>API Key / 数据库密码等以密文存储，仅按引用使用，绝不进入 Prompt 与明文。</div>" +
    "<div class='card'><div class='inline-form'><input id='sc-name' placeholder='名称，如 finance-db' aria-label='密钥名称' style='width:160px;'><select id='sc-provider' aria-label='密钥类型'><option value='generic'>通用密钥</option><option value='api_key'>API 密钥</option><option value='db_password'>数据库密码</option></select>" +
    "<input id='sc-value' type='password' placeholder='要加密保存的值' style='flex:1;min-width:160px;'><button class='btn-sm btn-primary' onclick='window.createSecret()'>保存</button></div></div>" + rows + "</div>";
  box.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => { if (confirm("删除该密钥？")) { await API.del("/api/secrets/" + b.dataset.del); renderAdminSecretsView(); } });
  window.createSecret = async function () {
    const name = $("#sc-name").value.trim(), value = $("#sc-value").value;
    if (!name || !value) return toast("名称与值必填");
    try { await API.post("/api/secrets", { name, provider: $("#sc-provider").value, value }); toast("已加密保存"); renderAdminSecretsView(); }
    catch (e) { toast("保存失败：" + e.message); }
  };
}
/**
 * 模型角色元信息（reasoning/embedding/vision 三类）。
 * 业务代码只引用角色名，不直接写模型 ID，因此这里的中文名与能力标签是与后端约定的展示口径。
 */
const MODEL_ROLE_META = {
  reasoning: { label: "对话与推理", description: "聊天回答、任务规划和工具调用", icon: IC.model, tone: "reasoning", capabilities: ["chat", "reasoning", "tools"] },
  embedding: { label: "向量检索", description: "知识分块向量化与语义召回", icon: IC.vector, tone: "embedding", capabilities: ["embedding", "semantic-search"] },
  vision: { label: "视觉识别", description: "Word、PDF、表格和演示文稿中的图片/OCR", icon: IC.eye, tone: "vision", capabilities: ["vision", "ocr", "charts", "documents"] }
};

/** 取模型角色元信息；未登记的自定义角色给通用文案与空能力列表，不报错。 */
function modelRoleMeta(name) {
  return MODEL_ROLE_META[name] || { label: name, description: "自定义模型角色", icon: IC.model, tone: "custom", capabilities: [] };
}

/** 单个模型角色卡片：当前模型/端点、密钥是否就绪、上次连通测试结果、来源（管理员配置或环境默认）。 */
function renderModelProfileCard(profile) {
  const meta = modelRoleMeta(profile.profile_name);
  const caps = (profile.capabilities || []).map((capability) => "<span class='model-capability'>" + esc(capability) + "</span>").join("");
  const probe = profile.last_probe;
  const probeHtml = probe ? "<span class='model-probe-state " + esc(probe.status) + "'><i></i>" + (probe.status === "passed" ? "最近测试通过" : "最近测试失败") + "</span>" : "<span class='model-probe-state'><i></i>尚未测试</span>";
  const source = profile.source === "database" ? "管理员配置" : "环境默认";
  const keyState = profile.api_key_configured ? "<span class='model-key-state ready'>密钥已配置</span>" : "<span class='model-key-state missing'>缺少密钥</span>";
  return "<article class='model-role-card " + meta.tone + "' data-model-role='" + esc(profile.profile_name) + "'>" +
    "<div class='model-role-rail'></div><div class='model-card-head'><span class='model-role-icon'>" + meta.icon + "</span><div><span class='model-role-kicker'>" + esc(profile.profile_name) + "</span><h2>" + esc(meta.label) + "</h2></div><span class='badge" + (profile.enabled ? " published" : "") + "'>" + (profile.enabled ? "启用" : "停用") + "</span></div>" +
    "<p class='model-role-desc'>" + esc(meta.description) + "</p>" +
    "<div class='model-id-block'><span>当前模型</span><strong>" + esc(profile.model || "未配置") + "</strong><small title='" + esc(profile.endpoint || "") + "'>" + esc(profile.endpoint || "端点未配置") + "</small></div>" +
    "<div class='model-capabilities'>" + caps + "</div>" +
    "<div class='model-card-status'><div>" + keyState + probeHtml + "</div><small>" + esc(source) + (profile.updated_at ? " · 更新于 " + formatKBDate(profile.updated_at) : "") + "</small></div>" +
    "<div class='model-card-actions'><button class='btn-sm btn-secondary' data-model-edit='" + esc(profile.profile_name) + "' type='button'>配置</button><button class='btn-sm btn-primary' data-model-probe='" + esc(profile.profile_name) + "' type='button'>连通测试</button></div></article>";
}

/**
 * 模型配置页：按角色列出模型并支持逐个连通测试或一键全测。
 * 全测是串行的（后端口探可能较慢），进度写进按钮文案；单个失败不中断整轮测试。
 */
async function renderAdminModelsView() {
  const box = contentEl("admin-models"); if (!box) return;
  const response = await API.get("/api/models/profiles");
  const profiles = response.items || [];
  state.modelProfiles = profiles;
  const keyReady = profiles.filter((profile) => profile.api_key_configured).length;
  const probePassed = profiles.filter((profile) => profile.last_probe && profile.last_probe.status === "passed").length;
  box.innerHTML = "<div class='panel model-admin-page'>" +
    "<div class='model-page-head'><div><span class='model-page-eyebrow'>Model Gateway</span><h1>模型配置</h1><p>按业务角色管理模型。业务代码只引用角色，切换供应商和模型无需修改调用链。</p></div><div class='model-page-actions'><button class='btn-sm btn-secondary' id='model-test-all' type='button'>测试全部</button><button class='btn-sm btn-primary' id='model-add-custom' type='button'>" + IC.plus + " 添加配置</button></div></div>" +
    "<div class='model-gateway-strip'><div><strong>" + profiles.length + "</strong><span>模型角色</span></div><span class='model-strip-arrow'>&#8594;</span><div><strong>" + keyReady + "</strong><span>密钥就绪</span></div><span class='model-strip-arrow'>&#8594;</span><div><strong>" + probePassed + "</strong><span>测试通过</span></div><p>识图模型会在知识库入库时自动读取文档图片，并把图片内容写入可检索知识分块。</p></div>" +
    "<section id='model-editor' class='model-editor hidden' aria-label='模型配置表单'></section>" +
    "<div class='model-role-grid'>" + profiles.map(renderModelProfileCard).join("") + "</div></div>";

  box.querySelectorAll("[data-model-edit]").forEach((button) => {
    button.onclick = () => openModelEditor(profiles.find((item) => item.profile_name === button.dataset.modelEdit));
  });
  box.querySelectorAll("[data-model-probe]").forEach((button) => {
    button.onclick = async () => {
      const original = button.textContent;
      button.disabled = true; button.textContent = "测试中…";
      try {
        const result = await API.post("/api/models/probe/" + encodeURIComponent(button.dataset.modelProbe), {});
        const details = result.details || {};
        if (result.status === "passed") {
          openDetail("模型测试通过 · " + modelRoleMeta(button.dataset.modelProbe).label,
            "<div class='model-probe-detail'><div><span>模型</span><strong>" + esc(details.model || "—") + "</strong></div><div><span>响应时间</span><strong>" + esc(details.latency_ms || 0) + " ms</strong></div><div class='wide'><span>实际响应</span><p>" + esc(details.reply || "测试成功") + "</p></div></div>");
        } else {
          openDetail("模型测试失败", "<div class='model-probe-error'>" + esc(details.error || "模型没有返回有效响应") + "</div>");
        }
        await renderAdminModelsView();
      } catch (error) {
        toast("测试失败：" + error.message);
        button.disabled = false; button.textContent = original;
      }
    };
  });
  $("#model-add-custom").onclick = () => openModelEditor(null);
  $("#model-test-all").onclick = async (event) => {
    const button = event.currentTarget;
    button.disabled = true; button.textContent = "测试中 0/" + profiles.length;
    let passed = 0;
    for (let index = 0; index < profiles.length; index += 1) {
      button.textContent = "测试中 " + (index + 1) + "/" + profiles.length;
      try {
        const result = await API.post("/api/models/probe/" + encodeURIComponent(profiles[index].profile_name), {});
        if (result.status === "passed") passed += 1;
      } catch (error) {}
    }
    toast("模型测试完成：" + passed + "/" + profiles.length + " 通过");
    await renderAdminModelsView();
  };
}

/** 各模型角色的默认供应商/模型/端点（新建自定义角色时留空，由管理员填写）。 */
function modelEditorDefaults(role) {
  if (role === "vision") return { provider: "siliconflow", model: "Qwen/Qwen3.5-4B", endpoint: "https://api.siliconflow.cn/v1", capabilities: MODEL_ROLE_META.vision.capabilities };
  if (role === "embedding") return { provider: "siliconflow", model: "BAAI/bge-m3", endpoint: "https://api.siliconflow.cn/v1", capabilities: MODEL_ROLE_META.embedding.capabilities };
  if (role === "reasoning") return { provider: "agnes", model: "agnes-2.5-flash", endpoint: "", capabilities: MODEL_ROLE_META.reasoning.capabilities };
  return { provider: "openai-compatible", model: "", endpoint: "", capabilities: [] };
}

/**
 * 打开模型配置表单（profile 为 null 表示新增自定义角色；已有角色时角色标识只读）。
 * API 密钥留空表示保留现有密钥（表单提示文案已说明），「恢复环境默认」删除管理员配置而非清空密钥。
 */
function openModelEditor(profile) {
  const editor = $("#model-editor"); if (!editor) return;
  const role = profile ? profile.profile_name : "custom";
  const defaults = profile || modelEditorDefaults(role);
  const capabilities = (defaults.capabilities || modelRoleMeta(role).capabilities || []).join(", ");
  editor.classList.remove("hidden");
  editor.dataset.profileId = profile && profile.id ? profile.id : "";
  editor.innerHTML = "<div class='model-editor-head'><div><span>" + (profile ? "编辑模型角色" : "添加模型角色") + "</span><h2>" + esc(profile ? modelRoleMeta(role).label : "自定义配置") + "</h2></div><button class='icon-btn' id='model-editor-close' aria-label='关闭'>" + IC.close + "</button></div>" +
    "<div class='model-form-grid'><label class='kb-field'><span>角色标识</span><input id='mp-name' value='" + esc(profile ? role : "") + "' placeholder='例如 vision' " + (profile ? "readonly" : "") + "></label><label class='kb-field'><span>供应商</span><input id='mp-provider' value='" + esc(defaults.provider || "") + "' placeholder='siliconflow'></label>" +
    "<label class='kb-field kb-field-wide'><span>模型名称</span><input id='mp-model' value='" + esc(defaults.model || "") + "' placeholder='供应商模型 ID'></label><label class='kb-field kb-field-wide'><span>OpenAI 兼容端点</span><input id='mp-endpoint' value='" + esc(defaults.endpoint || "") + "' placeholder='https://api.example.com/v1'></label>" +
    "<label class='kb-field kb-field-wide'><span>能力标签</span><input id='mp-capabilities' value='" + esc(capabilities) + "' placeholder='vision, ocr, charts'></label><label class='kb-field kb-field-wide'><span>API 密钥</span><input id='mp-key' type='password' autocomplete='new-password' placeholder='" + (profile && profile.api_key_configured ? "已配置；留空则保留现有密钥" : "输入供应商 API 密钥") + "'></label>" +
    "<label class='model-enabled-field'><input id='mp-enabled' type='checkbox' " + (!profile || profile.enabled ? "checked" : "") + "><span><strong>启用此角色</strong><small>启用后新任务会使用这项配置。</small></span></label></div>" +
    "<div class='model-editor-actions'>" + (profile && profile.id ? "<button class='btn-sm btn-danger model-delete-config' id='model-delete-config' type='button'>恢复环境默认</button>" : "") + "<span class='spacer'></span><button class='btn-sm btn-secondary' id='model-editor-cancel' type='button'>取消</button><button class='btn-sm btn-primary' id='model-editor-save' type='button'>保存配置</button></div>";
  editor.scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("#model-editor-close").onclick = () => editor.classList.add("hidden");
  $("#model-editor-cancel").onclick = () => editor.classList.add("hidden");
  $("#model-editor-save").onclick = async (event) => {
    const name = $("#mp-name").value.trim();
    if (!name) return toast("角色标识不能为空");
    const button = event.currentTarget;
    button.disabled = true; button.textContent = "保存中…";
    const payload = {
      provider: $("#mp-provider").value.trim(),
      model: $("#mp-model").value.trim(),
      endpoint: $("#mp-endpoint").value.trim(),
      api_key: $("#mp-key").value,
      capabilities: $("#mp-capabilities").value.split(",").map((item) => item.trim()).filter(Boolean),
      enabled: $("#mp-enabled").checked
    };
    try {
      await API.req("PUT", "/api/models/profiles/" + encodeURIComponent(name), payload);
      toast("模型配置已保存");
      await renderAdminModelsView();
    } catch (error) {
      toast("保存失败：" + error.message);
      button.disabled = false; button.textContent = "保存配置";
    }
  };
  if ($("#model-delete-config")) $("#model-delete-config").onclick = async () => {
    if (!confirm("删除管理员配置并恢复环境默认值？")) return;
    try {
      await API.del("/api/models/profiles/" + editor.dataset.profileId);
      toast("已恢复环境默认配置");
      await renderAdminModelsView();
    } catch (error) { toast("操作失败：" + error.message); }
  };
}
/**
 * 运行维护页：展示 API 服务健康、Web UI 名称与「卡住任务」数量。
 * 卡住任务先用 dry_run 预览，确认后再执行清理；被判定为等待中/执行中/状态不明的记录一律保留，
 * 只有确认本机执行者已退出的孤儿任务才会被标记失败（后端判定，前端不做二次推断）。
 */
async function renderAdminOpsView() {
  const box = contentEl("admin-ops"); if (!box) return;
  let health = "-", web = "-";
  try { health = (await API.get("/api/health")).ok ? "正常" : "异常"; } catch (e) {}
  try { web = (await API.get("/api/webinfo")).name || "-"; } catch (e) {}
  let stale = { count: 0, items: [] };
  try { stale = await API.post("/api/runs/recover-stale?dry_run=true", {}); } catch (e) {}
  const staleRows = (stale.items || []).map((s) => "<div style='padding:4px 0;font-size:12.5px;'>" + esc(s.run_id.slice(0, 12)) + " · " + esc(s.status) + " · " + s.age_s + "s</div>").join("");
  box.innerHTML = "<div class='panel'><h1>运行维护</h1><div class='sub'>系统状态与卡住任务的处理。</div>" +
    "<div class='kv-grid'><div class='card'><div class='field-label'>API 服务</div><div style='font-size:20px;font-weight:700;'>" + esc(String(health)) + "</div></div>" +
    "<div class='card'><div class='field-label'>Web UI</div><div style='font-size:15px;font-weight:600;'>" + esc(String(web)) + "</div></div>" +
    "<div class='card'><div class='field-label'>卡住的任务(Run)</div><div style='font-size:20px;font-weight:700;color:" + (stale.count ? "#d1453b" : "#10a37f") + ";'>" + stale.count + " 个</div></div></div>" +
    "<p class='muted'>另有 " + Number(stale.skipped_count || 0) + " 条等待中、执行中或无法确认状态的记录，已保留。</p>" +
    (stale.count ? "<div class='card'><div class='field-label'>明细（超过15分钟且已确认本机执行者退出）</div>" + staleRows +
    "<div style='margin-top:10px;'><button class='btn-sm btn-secondary' onclick='window.applyRecover()'>将这些标记为失败并清理</button><span class='muted' style='font-size:12px;margin-left:8px;'>标记后可重发消息重试</span></div></div>" : "") + "</div>";
  window.applyRecover = async function () {
    if (!confirm("确认将卡住的任务标记为失败？这不影响正常任务。")) return;
    try { const r = await API.post("/api/runs/recover-stale?dry_run=false", {}); toast("已清理 " + r.count + " 个卡住任务"); renderAdminOpsView(); }
    catch (e) { toast("操作失败：" + e.message); }
  };
}

// ---- 详情抽屉 ----
/**
 * 详情抽屉（菜单浮层复用为 dialog）：设置 role/aria-modal 并把焦点移入关闭按钮，
 * 同时接管 Tab 键做焦点循环，Esc 由全局 keydown 处理。
 */
function openDetail(title, bodyHtml) {
  const panel = $("#menu-panel");
  detailReturnFocus = document.activeElement;
  panel.className = "menu-panel wide";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-labelledby", "detail-title");
  panel.innerHTML = "<div class='detail-panel-head'><div id='detail-title'>" + esc(title) + "</div><button class='icon-btn' id='detail-close' aria-label='关闭详情'>" + IC.close + "</button></div><div class='detail-panel-body'>" + bodyHtml + "</div>";
  panel.classList.remove("hidden");
  $("#overlay").classList.remove("hidden");
  panel.querySelector("#detail-close").onclick = closeMenu;
  panel.onkeydown = (event) => {
    if (event.key !== "Tab") return;
    const focusable = Array.from(panel.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), details > summary, [tabindex]:not([tabindex='-1'])")).filter((element) => element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  requestAnimationFrame(() => panel.querySelector("#detail-close").focus());
}

/** 清理静态 HTML 中残留的 "undefined" 文本节点（历史模板遗留，避免直接显示给用户）。 */
function sanitizeStaticNav() {
  $$(".side-item").forEach((item) => {
    Array.from(item.childNodes).forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.trim() === "undefined") node.remove();
    });
  });
}

let enterpriseBusinessNavigation = null;
let authorizedBusinessEntries = [];
/** 渲染技能/插件/智能体三个快捷入口；只在后端 navigation 标记为 available 时出现，无授权则整体隐藏。 */
function renderAiResourceLinks() {
  const nav = $("#ai-resource-links");
  if (!nav) return;
  const labels = { skills: "技能", plugins: "插件", bots: "智能体" };
  const icons = { skills: IC.skill, plugins: IC.plugin, bots: IC.agent };
  const entries = ["skills", "plugins", "bots"].map((id) => authorizedBusinessEntries.find((entry) => entry.id === id)).filter(Boolean);
  nav.innerHTML = entries.map((entry) => "<a href='" + esc(entry.path) + "' title='打开" + esc(entry.label || labels[entry.id]) + "'><span>" + icons[entry.id] + "</span><b>" + labels[entry.id] + "</b></a>").join("");
  nav.hidden = !entries.length;
}
/**
 * 旧版多页外壳的统一样式改造：注入企业版样式表、补全局 header，
 * 并把旧的 .admin-sb/.admin-main 结构改挂到 #sidebar/#pages 上（管理页 HTML 沿用旧类名，这里做一次归一）。
 * 该函数在 boot 里最先执行，之后的 render*View 都假定这套容器已就位。
 */
function normalizeSecondaryShell() {
  const page = currentPage();
  const commonStyles = document.createElement("link");
  commonStyles.rel = "stylesheet"; commonStyles.href = "/assets/platform/enterprise.css?v=20260903-2";
  const globalHeader = document.createElement("header");
  globalHeader.className = "enterprise-header"; globalHeader.id = "legacy-enterprise-header";
  document.body.prepend(globalHeader);
  if (page === "chat") {
    document.body.classList.add("enterprise-ai-workbench");
    const styles = document.createElement("link"); styles.rel = "stylesheet"; styles.href = "/assets/platform/ai-workbench.css?v=20260903-2";
    document.head.append(commonStyles, styles);
    return;
  }
  let sidebar = $("#sidebar"), pages = $("#pages");
  if (!sidebar && $(".admin-app")) {
    sidebar = $(".admin-sb"); pages = $(".admin-main");
    sidebar.id = "sidebar"; pages.id = "pages"; pages.className = "";
  }
  if (!sidebar || !pages) return;
  document.body.classList.add("business-module-page");
  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet"; stylesheet.href = "/assets/platform/legacy.css";
  document.head.append(stylesheet, commonStyles);
  sidebar.className = "enterprise-sidebar";
  sidebar.setAttribute("aria-label", "当前业务域菜单");
  sidebar.innerHTML = '<p class="business-navigation-loading" role="status">正在加载当前业务导航…</p>';
  pages.classList.add("secondary-main");
  const section = document.querySelector("#page-" + page) || pages;
  section.classList.add("secondary-page");
  const header = section.querySelector(".page-header");
  if (header) { header.className = "enterprise-contextbar"; header.id = "legacy-enterprise-contextbar"; header.innerHTML = ""; }
  const body = section.querySelector(".page-body");
  if (body) body.classList.add("secondary-body");
}

/**
 * 加载平台导航（/api/v1/platform/navigation）并据此渲染顶栏/侧栏。
 * available=false 的模块不进入授权清单（renderAiResourceLinks 与"更多"菜单都依赖它）；
 * 重复调用会先 destroy 旧实例，避免事件重复绑定；登出时即使接口失败也要清本地令牌。
 */
async function loadBusinessModuleNavigation() {
  const data = await API.get("/api/v1/platform/navigation");
  authorizedBusinessEntries = data.items.filter(item => item.available);
  renderAiResourceLinks();
  const { createEnterpriseNavigation, navigationEntry, platformName } = await import("/assets/platform/enterprise-navigation.js");
  enterpriseBusinessNavigation?.destroy();
  enterpriseBusinessNavigation = createEnterpriseNavigation({
    header: $("#legacy-enterprise-header"), sidebar: currentPage() === "chat" ? null : $("#sidebar"),
    context: $("#legacy-enterprise-contextbar"), items: data.items, settings: data.settings, me: state.me,
    onAccount: () => location.assign("/?account=profile"),
    onLogout: async () => {
      try { await API.post("/api/auth/logout"); } catch { /* local access is still removed */ }
      localStorage.removeItem("agent_token"); localStorage.removeItem("agent_workspace_id"); location.assign("/");
    },
  });
  const current = navigationEntry(data.items);
  if (currentPage() === "chat") {
    $("#model-chip")?.remove();
    document.querySelector(".ai-module-sections")?.remove();
  }
  document.title = (currentPage() === "chat" ? "AI 工作台" : current?.label || "业务模块") + " · " + platformName(data.settings["site.name"]);
}

/** 分享会话：只复制带 cid 的链接，不生成公开分享（访问仍受登录与工作区权限限制）。 */
async function shareConversation() {
  if (!state.current) return toast("请先开始一个聊天");
  const url = location.origin + "/ai?cid=" + encodeURIComponent(state.current.id);
  try { await copyChatText(url); toast("聊天链接已复制"); }
  catch (e) { toast("复制失败，请从地址栏复制"); }
}

/** 侧栏「更多」菜单：仅列出已授权模块中固定支持的那几项（连接器/数据源/技能/运行/用户）。 */
function openSidebarMore(anchor) {
  const panel = $("#menu-panel");
  panel.className = "menu-panel";
  const icons = { connectors: IC.connector, datasources: IC.ds, skills: IC.skill, runs: IC.runs, users: IC.admin };
  const entries = authorizedBusinessEntries.filter(entry => Object.hasOwn(icons, entry.id));
  panel.innerHTML = entries.map(entry => "<a class='menu-item' href='" + esc(entry.path) + "'><span class='mi'>" + icons[entry.id] + "</span><span>" + esc(entry.label || entry.name) + "</span></a>").join("") || "<p class='muted'>没有其他已授权模块</p>";
  positionPanel(panel, anchor); $("#overlay").classList.remove("hidden");
}

/**
 * 语音输入失败提示：按浏览器返回的错误码给出可执行的解决办法（安全上下文、麦克风权限、服务不可用等）。
 * aborted 是用户主动停止，不提示。
 */
function showVoiceInputError(code) {
  const messages = {
    insecure: "当前地址不是安全连接，浏览器会限制麦克风。若你正在运行项目的电脑上操作，请改用 http://localhost" + (location.port ? ":" + location.port : "") + "/ai；其他电脑访问需要配置 HTTPS。也可以点击输入框后按 Win + H 使用系统语音输入。",
    unsupported: "当前浏览器不支持网页语音识别。请换用支持语音识别的浏览器，或点击输入框后按 Win + H 使用系统语音输入。",
    "not-allowed": "浏览器或系统未允许使用麦克风。请点击地址栏左侧的网站设置，将麦克风设为允许，再刷新页面。若仍失败，请到 Windows 设置 → 隐私和安全性 → 麦克风，打开麦克风访问和桌面应用访问权限。",
    "service-not-allowed": "浏览器不允许使用当前语音识别服务。请检查浏览器或组织的语音策略，或点击输入框后按 Win + H 使用系统语音输入。",
    "audio-capture": "未能使用麦克风。请确认麦克风已连接，在系统声音设置中选中正确的输入设备，并检查麦克风访问权限。",
    network: "无法连接浏览器的语音识别服务。请检查网络连接后重试；麦克风权限已允许时，也可能遇到此问题。可尝试 Win + H 系统语音输入。",
    "no-speech": "没有识别到语音，请靠近麦克风，再次点击麦克风按钮后说话。",
    "language-not-supported": "当前语音识别服务不支持中文，请更换支持中文识别的浏览器或使用系统语音输入。",
  };
  if (code === "aborted") return;
  const panel = $("#voice-input-feedback");
  panel.querySelector("span").textContent = messages[code] || "语音输入未能启动，请检查麦克风权限和网络后重试。也可以点击输入框后按 Win + H 使用系统语音输入。";
  panel.hidden = false;
}

let activeVoiceRecognition = null;
/**
 * 语音输入开关（Web Speech API）。
 * 需要安全上下文（https 或 localhost）；识别过程中把 interim 结果实时拼到输入框原有的草稿后面，
 * 再次点击等同停止；每次结束都调用 reset 复位按钮状态，避免按钮卡在"录音中"。
 */
function startVoiceInput() {
  if (activeVoiceRecognition) {
    try { activeVoiceRecognition.stop(); } catch { /* Wait for the current recognition to end. */ }
    return;
  }
  if (!window.isSecureContext) return showVoiceInputError("insecure");
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return showVoiceInputError("unsupported");
  let recognition;
  try { recognition = new SpeechRecognition(); }
  catch (error) { return showVoiceInputError(error.name === "NotAllowedError" ? "not-allowed" : "unsupported"); }
  recognition.lang = "zh-CN"; recognition.interimResults = true; recognition.continuous = false;
  const btn = $("#voice-input-btn"), ta = $("#prompt-textarea");
  const base = ta.value;
  const reset = () => {
    if (activeVoiceRecognition !== recognition) return;
    activeVoiceRecognition = null;
    btn.style.background = "";
    btn.title = "语音输入";
    btn.setAttribute("aria-label", "语音输入");
    btn.setAttribute("aria-pressed", "false");
  };
  recognition.onstart = () => {
    btn.style.background = "#ececec";
    btn.title = "停止语音输入";
    btn.setAttribute("aria-label", "停止语音输入");
    btn.setAttribute("aria-pressed", "true");
    toast("正在听，请开始说话；再次点击麦克风可停止");
  };
  recognition.onresult = (event) => {
    let text = ""; for (let i = 0; i < event.results.length; i++) text += event.results[i][0].transcript;
    ta.value = base + text; updateSendState();
  };
  recognition.onerror = (event) => { showVoiceInputError(event.error); reset(); };
  recognition.onend = () => { reset(); ta.focus(); };
  $("#voice-input-feedback").hidden = true;
  activeVoiceRecognition = recognition;
  try { recognition.start(); }
  catch (error) { reset(); showVoiceInputError(error.name === "NotAllowedError" ? "not-allowed" : error.name); }
}

/** 放入/粘贴的文件统一走 uploadFiles（拖拽与剪贴板共用入口）。 */
function attachDroppedFiles(files) {
  const list = Array.from(files || []); if (list.length) uploadFiles(list);
}

// ---------------- 事件 ----------------
/**
 * 绑定所有静态元素的事件（登录、新建/切换会话、模型选择、附件、模板弹窗、输入区拖拽与粘贴、移动端抽屉）。
 * 用 on(sel, ev, fn) 包裹是因为同一份 app.js 服务多个页面，元素可能不存在。
 * 输入框回车发送、Shift+Enter 换行；Esc 统一关闭所有浮层。
 */
function wireEvents() {
  setupChatScrolling();
  const on = (sel, ev, fn) => { const el = $(sel); if (el) el.addEventListener(ev, fn); };
  const globalFileInput = $("#file-input"); if (globalFileInput && !globalFileInput.getAttribute("aria-label")) globalFileInput.setAttribute("aria-label", "上传聊天文件");
  const liveToast = $("#toast"); if (liveToast) { liveToast.setAttribute("role", "status"); liveToast.setAttribute("aria-live", "polite"); }
  on("#login-btn", "click", doLogin);
  on("#login-password", "keydown", (e) => { if (e.key === "Enter") doLogin(); });
  on("#login-username", "keydown", (e) => { if (e.key === "Enter") doLogin(); });
  on("#new-chat-btn", "click", newChat);
  on("#sidebar-search-btn", "click", () => { const wrap = $("#history-search-wrap"); wrap.classList.toggle("hidden"); if (!wrap.classList.contains("hidden")) $("#search-chat").focus(); });
  on("#sidebar-collapse-btn", "click", () => { document.body.classList.add("sidebar-collapsed"); document.body.classList.remove("mobile-sidebar-open"); });
  on("#mobile-sidebar-btn", "click", () => { document.body.classList.remove("sidebar-collapsed"); document.body.classList.toggle("mobile-sidebar-open"); });
  on("#sidebar-more-btn", "click", (e) => openSidebarMore(e.currentTarget));
  on("#share-conversation", "click", shareConversation);
  on("#top-more-btn", "click", (e) => openSidebarMore(e.currentTarget));
  on("#model-chip", "click", () => $("#chat-model-select")?.focus());
  on("#chat-model-select", "change", (event) => { if (!state.streaming && !state.openingChat) state.modelProfile = event.target.value; });
  on("#chat-model-retry", "click", loadChatModels);
  on("#voice-input-btn", "click", startVoiceInput);
  on("#voice-input-feedback button", "click", () => { $("#voice-input-feedback").hidden = true; });
  on("#active-mode-btn", "click", () => { state.activeMode = null; $("#active-mode-btn").classList.add("hidden"); });
  on("#logout-link", "click", async () => {
    try { await API.post("/api/auth/logout"); } catch (error) { /* Clear local access even if offline. */ }
    API.token = "";
    localStorage.removeItem("agent_token");
    localStorage.removeItem("agent_workspace_id");
    window.location.href = "/";
  });
  on("#search-chat", "input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$(".history-item").forEach((li) => { li.style.display = li.textContent.toLowerCase().includes(q) ? "" : "none"; });
  });
  on("#composer-form", "submit", (e) => { e.preventDefault(); sendMessage(); });
  on("#prompt-textarea", "input", updateSendState);
  on("#prompt-textarea", "keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  on("#add-menu-btn", "click", (ev) => openAddMenu(ev.currentTarget));
  on("#business-source-btn", "click", (ev) => openDataSourcePicker(ev.currentTarget));
  on("#dashboard-template-btn", "click", openDashboardTemplatePicker);
  on("#file-input", "change", (e) => { if (e.target.files.length) uploadFiles(Array.from(e.target.files)); e.target.value = ""; });
  on("#overlay", "click", closeMenu);
  on("#template-close", "click", closeTemplatePicker);
  on("#template-use", "click", useSelectedTemplate);
  on("#template-upload-btn", "click", () => $("#template-upload-input").click());
  on("#template-upload-input", "change", (e) => uploadCustomerTemplate(e.target.files && e.target.files[0]));
  on("#template-search-input", "input", (e) => { state.pptTemplateQuery = e.target.value; renderTemplateGrid(); });
  on("#template-source-filter", "change", (e) => { state.pptTemplateSource = e.target.value; renderTemplateGrid(); });
  on("#template-modal", "click", (e) => { if (e.target.id === "template-modal") closeTemplatePicker(); });
  on("#dashboard-template-close", "click", closeDashboardTemplatePicker);
  on("#dashboard-template-use", "click", useDashboardTemplate);
  on("#dashboard-template-search", "input", (e) => { state.dashboardTemplateQuery = e.target.value; renderDashboardTemplatePicker(); });
  on("#dashboard-template-category", "change", (e) => { state.dashboardTemplateCategory = e.target.value; renderDashboardTemplatePicker(); });
  on("#dashboard-template-strategy", "click", (e) => {
    const button = e.target.closest("[data-dashboard-mode]"); if (!button) return;
    state.dashboardTemplateDraftMode = button.dataset.dashboardMode;
    renderDashboardTemplatePicker();
  });
  on("#dashboard-template-modal", "click", (e) => { if (e.target.id === "dashboard-template-modal") closeDashboardTemplatePicker(); });
  const composer = $("#composer-form");
  if (composer) {
    composer.addEventListener("dragover", (e) => { e.preventDefault(); composer.style.borderColor = "#888"; });
    composer.addEventListener("dragleave", () => { composer.style.borderColor = ""; });
    composer.addEventListener("drop", (e) => { e.preventDefault(); composer.style.borderColor = ""; attachDroppedFiles(e.dataTransfer.files); });
  }
  on("#prompt-textarea", "paste", (e) => {
    const files = Array.from(e.clipboardData && e.clipboardData.files || []); if (files.length) attachDroppedFiles(files);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenu(); closeTemplatePicker(); closeDashboardTemplatePicker(); document.body.classList.remove("mobile-sidebar-open"); } });
  document.addEventListener("click", (e) => {
    if (window.innerWidth > 820 || !document.body.classList.contains("mobile-sidebar-open")) return;
    if (e.target.closest("#sidebar") || e.target.closest("#mobile-sidebar-btn")) return;
    document.body.classList.remove("mobile-sidebar-open");
  });
}
/**
 * 按权限预加载输入区可选项（知识库、插件、PPT 模板、数据源、看板模板）。
 * 每一项都先查权限再请求，避免普通成员触发 403；数据源/看板模板加载失败时保留已有目录，
 * 由选择弹窗给出重试入口，不在这里弹错。
 */
async function loadResourceOptions() {
  const permissions = new Set(state.me?.permissions || []);
  if (permissions.has("knowledge.view")) { const k = await API.get("/api/knowledge"); state.knowledge = k.items || []; }
  if (permissions.has("plugins.view")) { const p = await API.get("/api/plugins"); state.plugins = p.items || []; }
  if (permissions.has("documents.view")) await loadTemplates();
  if (permissions.has("datasources.view")) {
    try {
      const response = await API.post("/api/v1/ai/business-actions/data-sources/discover", { keyword: "", limit: 20 });
      state.dataSources = businessSourceCatalog(response, true).sources;
    } catch (error) { /* Keep the last known catalog and expose retry in the picker. */ }
  }
  if (permissions.has("templates.view")) {
    try { await loadDashboardTemplates(); } catch (error) { /* Picker provides an explicit retry state. */ }
  }
  updateComposerBusinessContext();
}

/** 拉取当前用户可用的业务动作清单（欢迎页快捷场景按 available 过滤）；失败按空清单处理，不影响对话。 */
async function loadBusinessActions() {
  try {
    const response = await API.get("/api/v1/ai/business-actions");
    state.businessActions = response.data || [];
  } catch (error) {
    state.businessActions = [];
  }
  updateComposerBusinessContext();
}

/**
 * 启动流程：先做外壳归一与静态清理、绑定事件，再按是否有令牌决定进应用还是显示登录视图。
 * 非对话页无令牌直接跳回首页（"?/", 由后端决定展示内容），不显示登录表单。
 */
(function boot() {
  normalizeSecondaryShell();
  sanitizeStaticNav();
  wireEvents();
  const page = currentPage();
  const isChat = page === "chat";
  if (API.token) API.get("/api/auth/me").then(() => enterShell(page, isChat)).catch(() => { if (isChat) { document.body.classList.add("guest"); const lv = $("#login-view"); if (lv) lv.classList.remove("hidden"); } else window.location.href = "/"; });
  else if (isChat) { document.body.classList.add("guest"); const lv = $("#login-view"); if (lv) lv.classList.remove("hidden"); }
  else window.location.href = "/";
})();
