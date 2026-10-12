/**
 * 能力中心模块（工具与能力 /connectors、/skills、/plugins 三个路由共用本文件）
 *
 * 位置：按路径分发到三种能力载体：
 *   - /capabilities（默认）：已发布工具目录与工具详情，可做“生成参数 / 校验参数”（工具由连接器或平台内置提供）；
 *   - /connectors：连接器（HTTP / 数据库 / MCP）的列表与详情，含工具定义编辑、测试、发布、归档、复制、共享范围；
 *   - /skills、/plugins：Skill 与 Plugin 的草稿-版本-发布流程（Plugin 可绑定 Skill 与工具）。
 *   工具/方法视图还会把列表渲染委托给 capability-governance.js（同一 page 的“治理”标签页）。
 * 依赖接口前缀：/api/v1/capabilities（/tools、/connectors、/methods/{name}/test、/from-run）、
 *   /api/skills、/api/plugins、/api/runs（沉淀草稿时选择运行记录）；
 *   可选依赖 /api/v1/analytics/datasources（需 datasources.view）与 /api/secrets（需 secrets.view）。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 权限码必须与后端目录一致：capabilities.create/execute、connectors.create/edit/delete/publish/execute、
 *   skills.*、plugins.*。缺权限时按钮不渲染，但真正的拒绝仍由后端完成。
 * - 草稿与发布分离：新建/编辑只产生草稿版本，必须显式“发布”后 AI 与工作流才会使用；归档不删除历史版本与运行记录。
 * - 连接器/方法测试是真实调用外部系统（HTTP 请求、数据库查询、MCP 调用），源码字段必须来自测试弹窗的显式输入；
 *   密钥一律引用 secrets 记录（secretId），不要把明文写进配置。
 * - provider 与 authType 联动显隐表单区块；数据库连接器的工具集由后端固定提供（database.list_tables/describe_table/query），
 *   不接受自定义工具定义，因此数据库类型下隐藏工具编辑器。
 * - “授权访问本机或内网目标”仅对 system.admin 显示：这是放宽出网限制的开关，不要对其他角色开放。
 * - 所有写操作都带 version 或 version_number；参数/配置 JSON 解析失败必须抛中文错误，由弹窗内提示。
 */

import { api } from "./api.js";
import {
  $, $$, state, can, esc, date, pageHeader, dataTable, emptyState,
  statusBadge, field, input, select, checkboxes, openDialog, confirmAction, toast,
} from "./ui.js";
import { capabilityGovernanceTabs, renderCapabilityGovernance } from "./capability-governance.js";

const BASE = "/api/v1/capabilities";
const textarea = (name, value = "", attributes = "") => `<textarea name="${esc(name)}" ${attributes}>${esc(value)}</textarea>`;
const pretty = value => JSON.stringify(value || {}, null, 2);
const providerLabels = { builtin: "平台内置", http: "HTTP", database: "数据库", mcp: "MCP" };
// 中文说明：风险等级文案。high 表示可能产生外部副作用（写库、下发指令），页面需如实展示给授权人。
const riskLabels = { low: "低", medium: "中", high: "高" };

// 中文说明：本模块接口返回 { success, data } 包装，统一解包。
async function request(method, path, body) {
  const result = await api[method](path, body);
  return result?.success === true && "data" in result ? result.data : result;
}
const get = path => request("get", path);
const post = (path, body) => request("post", path, body);
const patch = (path, body) => request("patch", path, body);
const remove = path => request("delete", path);

// 中文说明：整页跳转并保留非空查询参数（工具/连接器/Skill/Plugin 都是服务端式渲染页面）。
function go(path, values = {}) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) query.set(key, value);
  });
  location.assign(path + (query.size ? "?" + query.toString() : ""));
}

function compactPage(root, title, description, actions, body) {
  root.innerHTML = pageHeader(title, description, actions) + body;
}

// 中文说明：展示一次测试/校验结果：优先把 output.text（或 reply）按纯文本预格式化展示，
// 便于阅读长文；完整结构放在可折叠的“运行详情”里，始终转义后输出。
function showResult(title, value) {
  const outputText = value?.output?.text ?? value?.reply;
  if (typeof outputText === "string") {
    openDialog(title, `<p>${statusBadge(value.status || "completed")}</p>
      <section aria-label="输出内容"><pre class="capability-result" style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(outputText.trim())}</pre></section>
      <details><summary>运行详情</summary><pre class="json-preview capability-result">${esc(pretty(value))}</pre></details>`);
    return;
  }
  openDialog(title, `<pre class="json-preview capability-result">${esc(pretty(value))}</pre>`);
}

// 中文说明：工具/连接器列表的通用筛选工具栏（字段名 keyword + provider，与后端查询参数一致）。
function capabilityFilter(keyword, provider, action = "查询") {
  return `<form class="list-toolbar" data-capability-filter>
    <label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" value="${esc(keyword)}" placeholder="名称或用途" aria-label="搜索"></label>
    ${select("provider", [
      { value: "builtin", label: "平台内置" }, { value: "http", label: "HTTP" },
      { value: "database", label: "数据库" }, { value: "mcp", label: "MCP" },
    ], provider, "全部类型").replace("<select ", '<select aria-label="类型" ')}
    <button class="button" type="submit">${action}</button>
    ${keyword || provider ? '<button class="button quiet" type="button" data-capability-reset>重置</button>' : ""}
  </form>`;
}

// 中文说明：绑定筛选表单与重置按钮（重置即清空查询串回到该列表首页）。
function wireFilter(root, path) {
  $("[data-capability-filter]", root)?.addEventListener("submit", event => {
    event.preventDefault();
    go(path, Object.fromEntries(new FormData(event.currentTarget)));
  });
  $("[data-capability-reset]", root)?.addEventListener("click", () => go(path));
}

/**
 * 中文说明：工具参数弹窗，两种模式：
 *   mode="compile"：用业务需求（intent）+ 资源对应关系生成参数并校验；
 *   mode="validate"：校验手工填写的参数 JSON。
 * 参数：tool 为工具条目（用 tool.name 作为路径参数）；mode 决定界面与接口。
 * 副作用：POST /tools/{name}/compile 或 /validate（只读校验，不执行业务副作用），结果用 showResult 展示。
 * 失败表现：JSON 解析失败或后端拒绝都在弹窗内提示；结果弹窗用 setTimeout 延后打开，避免被单例弹窗覆盖。
 */
async function argumentDialog(tool, mode) {
  const compile = mode === "compile";
  openDialog(compile ? "根据需求生成参数" : "校验工具参数", `<div class="form-grid">
    ${compile
      ? field("业务需求", textarea("intent", "", 'required rows="6" maxlength="10000" placeholder="说明要完成什么，以及已知的对象、日期和范围"'), "", true)
      : field("参数 JSON", textarea("arguments", "{}", 'required rows="12" spellcheck="false"'), "", true)}
    ${compile ? `<details class="form-field wide capability-advanced"><summary>资源对应关系（高级）</summary>${textarea("references", "{}", 'rows="6" spellcheck="false"')}</details>` : ""}
  </div>`, {
    wide: true,
    saveLabel: compile ? "生成并校验" : "校验",
    onSave: async form => {
      let payload;
      if (compile) {
        let references;
        try { references = JSON.parse(String(form.get("references") || "{}")); }
        catch { throw new Error("资源对应关系不是有效 JSON"); }
        payload = { intent: String(form.get("intent") || "").trim(), references };
      } else {
        let args;
        try { args = JSON.parse(String(form.get("arguments") || "{}")); }
        catch { throw new Error("参数不是有效 JSON"); }
        payload = { arguments: args };
      }
      const result = await post(`${BASE}/tools/${encodeURIComponent(tool.name)}/${compile ? "compile" : "validate"}`, payload);
      setTimeout(() => showResult(compile ? "已生成并通过校验" : "参数校验通过", result), 0);
    },
  });
}

// 中文说明：工具与能力页（默认路由 /capabilities）。
// 先尝试交给 capability-governance.js 渲染治理视图（view=categories|methods），未命中再渲染工具目录。
// 工具详情用 ?tool=名称 定位，展示输入/输出 Schema、所需权限与风险等级；行内可做参数校验。
async function renderToolCatalog(root) {
  const query = new URLSearchParams(location.search);
  const view = query.get("view") || "tools";
  if (await renderCapabilityGovernance(root, view, {
    create: () => connectorDialog(null, { methodMode: true }),
    edit: editManagedMethod,
    test: testManagedMethod,
    remove: removeManagedMethod,
  })) return;
  const keyword = query.get("keyword") || "";
  const provider = query.get("provider") || "";
  const selected = query.get("tool") || "";
  const result = await get(`${BASE}/tools?keyword=${encodeURIComponent(selected || keyword)}&provider=${encodeURIComponent(provider)}&limit=500`);
  if (selected) {
    const tool = result.items.find(item => item.name === selected);
    if (!tool) throw new Error("工具不存在或当前账号不可用");
    compactPage(root, tool.name, `${tool.providerLabel}${tool.connectorName ? " · " + tool.connectorName : ""}`,
      `<a class="button" href="/capabilities">返回列表</a>${can("capabilities.execute") ? `<button class="button" type="button" data-tool-compile>生成参数</button><button class="button primary" type="button" data-tool-validate>校验参数</button>` : ""}`,
      `<section class="panel catalog-detail-panel capability-detail">
        <dl class="detail-grid catalog-detail-grid"><div><dt>工具类型</dt><dd>${esc(tool.providerLabel)}</dd></div><div><dt>风险等级</dt><dd>${esc(riskLabels[tool.riskLevel] || tool.riskLevel)}</dd></div><div><dt>调用超时</dt><dd>${esc(tool.timeoutSeconds)} 秒</dd></div><div class="wide"><dt>用途</dt><dd>${esc(tool.description || "—")}</dd></div><div class="wide"><dt>所需权限</dt><dd>${esc(tool.permissions.join("、") || "无额外业务权限")}</dd></div></dl>
        <details class="capability-schema"><summary>输入 Schema</summary><pre class="json-preview">${esc(pretty(tool.inputSchema))}</pre></details>
        ${Object.keys(tool.outputSchema || {}).length ? `<details class="capability-schema"><summary>输出 Schema</summary><pre class="json-preview">${esc(pretty(tool.outputSchema))}</pre></details>` : ""}
      </section>`);
    $("[data-tool-validate]", root)?.addEventListener("click", () => argumentDialog(tool, "validate"));
    $("[data-tool-compile]", root)?.addEventListener("click", () => argumentDialog(tool, "compile"));
    return;
  }
  const actions = can("capabilities.create") && can("runs.view")
    ? '<button class="button" type="button" data-capability-from-run>从运行记录沉淀</button>' : "";
  const content = result.items.length ? dataTable(
    ["工具", "来源", "用途", "权限", "风险", "操作"], result.items,
    item => `<tr><td><a class="cell-primary" href="/capabilities?tool=${encodeURIComponent(item.name)}">${esc(item.name)}</a></td><td>${esc(item.providerLabel)}${item.connectorName ? `<span class="cell-secondary">${esc(item.connectorName)}</span>` : ""}</td><td class="capability-description">${esc(item.description || "—")}</td><td>${esc(item.permissions.join("、") || "—")}</td><td>${esc(riskLabels[item.riskLevel] || item.riskLevel)}</td><td><div class="row-actions"><a href="/capabilities?tool=${encodeURIComponent(item.name)}">查看</a>${can("capabilities.execute") ? `<button class="text-button" type="button" data-quick-validate="${esc(item.name)}">校验</button>` : ""}</div></td></tr>`,
  ) : emptyState("没有可用工具", "当前权限下没有已发布工具。");
  compactPage(root, "工具与能力", "已发布工具目录", actions,
    `${capabilityGovernanceTabs("tools")}<section class="panel">${capabilityFilter(keyword, provider)}${content}</section>`);
  wireFilter(root, "/capabilities");
  $$('[data-quick-validate]', root).forEach(button => button.addEventListener("click", () => {
    const tool = result.items.find(item => item.name === button.dataset.quickValidate);
    if (tool) argumentDialog(tool, "validate");
  }));
  $("[data-capability-from-run]", root)?.addEventListener("click", fromRunDialog);
}

// 中文说明：从运行记录沉淀能力草稿（Skill 或 Plugin）。
// 权限：入口需 capabilities.create 且 runs.view；生成的是草稿，需到对应页面继续编辑并发布。
async function fromRunDialog() {
  const runs = await request("get", "/api/runs");
  openDialog("从运行记录沉淀能力", `<div class="form-grid">
    ${field("运行记录", select("runId", (runs.items || []).map(item => ({ value: item.id, label: `${item.title} · ${item.status}` })), "", "请选择运行记录"))}
    ${field("生成类型", select("target", [{ value: "skill", label: "Skill 方法" }, { value: "plugin", label: "Plugin 能力包" }], "skill", null))}
  </div>`, {
    saveLabel: "生成草稿",
    onSave: async form => {
      const target = String(form.get("target"));
      const value = await post(BASE + "/from-run", { runId: String(form.get("runId")), target });
      toast("已生成" + (target === "skill" ? " Skill" : " Plugin") + " 草稿");
      go(target === "skill" ? "/skills" : "/plugins", { id: value.created?.skill_id || value.created?.plugin_id });
    },
  });
}

// 中文说明：数据库连接器的固定方法集（与后端内置实现一一对应），前端只用于展示与提交，
// 不允许用户自定义 SQL 语义之外的方法名。
const emptySchema = () => ({ type: "object", properties: {}, additionalProperties: false });
const databaseToolDefinitions = () => [
  { name: "database.list_tables", description: "列出可访问表", inputSchema: emptySchema() },
  { name: "database.describe_table", description: "查看表字段", inputSchema: { type: "object", properties: { table: { type: "string" } }, required: ["table"], additionalProperties: false } },
  { name: "database.query", description: "执行只读 SQL", inputSchema: { type: "object", properties: { sql: { type: "string" } }, required: ["sql"], additionalProperties: false } },
];

// 中文说明：单个工具定义编辑行（工具名 + 用途 + 参数 Schema）。行可动态增删，
// 提交时按 DOM 顺序读取而不用索引名，因此删除中间行不会错位。
function toolEditor(tool = {}, index = 0) {
  return `<div class="capability-tool-row" data-tool-row>
    <div><label>工具名<input name="tool_name_${index}" value="${esc(tool.name || "")}" required placeholder="例如 equipment.status"></label><label>用途<input name="tool_description_${index}" value="${esc(tool.description || "")}" placeholder="说明何时调用"></label><button class="text-button danger" type="button" data-remove-tool>移除</button></div>
    <details><summary>参数 Schema</summary>${textarea(`tool_schema_${index}`, pretty(tool.inputSchema || tool.input_schema || emptySchema()), 'rows="8" spellcheck="false" aria-label="参数 Schema"')}</details>
  </div>`;
}

// 中文说明：取最新版本的配置对象（连接器配置随版本保存，历史版本配置不覆盖）。
function connectorConfig(record) {
  const latest = record?.versions?.[0];
  return latest?.config || {};
}

/**
 * 中文说明：连接器表单（新建连接器 / 创建新版本 / 治理页的“新增方法”共用）。
 * 参数：record=已有连接器（编辑即产生新版本草稿）；methodMode=true 表示从方法治理入口进入，仅调整标题文案。
 * 副作用：POST /connectors 或 /connectors/{id}/versions；保存后跳到连接器详情。
 * 权限相关：数据源选项需 datasources.view、密钥选项需 secrets.view，无权限时对应下拉为空（不阻塞其他配置）。
 * 约束：provider 创建后不可更改（编辑时禁用）；密钥以 secretId 引用，绝不接受明文密钥输入。
 */
async function connectorDialog(record = null, { methodMode = false } = {}) {
  const provider = record?.provider || "http";
  const config = connectorConfig(record);
  const [sources, secrets] = await Promise.all([
    can("datasources.view") ? get("/api/v1/analytics/datasources?enabled_only=true").catch(() => []) : Promise.resolve([]),
    can("secrets.view") ? request("get", "/api/secrets").then(value => value.items || []).catch(() => []) : Promise.resolve([]),
  ]);
  const tools = record?.versions?.[0]?.tools?.map(item => ({ ...item.spec, name: item.name, description: item.description })) || [{}];
  const auth = config.auth || {};
  let nextIndex = tools.length;
  openDialog(record ? "创建连接器新版本" : (methodMode ? "新增方法" : "新建连接器"), `<div class="form-grid capability-connector-form">
    ${field("名称", input("name", record?.name, 'required maxlength="200"'))}
    ${field("类型", select("provider", [{ value: "http", label: "HTTP" }, { value: "database", label: "数据库" }, { value: "mcp", label: "MCP" }], provider, null).replace("<select ", `<select ${record ? "disabled" : ""} `))}
    <div class="form-field wide" data-provider="http"><span>接口地址</span><div class="capability-url-row"><input name="baseUrl" value="${esc(config.base_url || "")}" placeholder="https://api.example.com"><input name="path" value="${esc(config.path || "/")}" placeholder="/v1/action"></div></div>
    <label class="form-field" data-provider="http"><span>请求方式</span>${select("method", [{ value: "GET", label: "GET" }, { value: "POST", label: "POST" }], config.method || "POST", null)}</label>
    <label class="form-field" data-provider="http"><span>参数位置</span>${select("bodyMode", [{ value: "json", label: "JSON 请求体" }, { value: "query", label: "查询参数" }], config.body_mapping || "json", null)}</label>
    <label class="form-field wide" data-provider="database"><span>数据库数据源</span>${select("dataSourceId", sources.map(item => ({ value: item.id, label: `${item.name} · ${item.type || item.provider}` })), config.data_source_id || "", "请选择已启用数据源")}</label>
    ${field("MCP 地址", input("mcpUrl", config.url, 'placeholder="https://mcp.example.com/mcp"'), "", true).replace('class="form-field wide"', 'class="form-field wide" data-provider="mcp"')}
    <label class="form-field" data-auth-provider><span>认证</span>${select("authType", [{ value: "none", label: "无需认证" }, { value: "bearer", label: "Bearer" }, { value: "basic", label: "Basic" }, { value: "header", label: "自定义请求头" }], auth.type || "none", null)}</label>
    <label class="form-field" data-auth-provider><span>密钥记录</span>${select("secretId", secrets.map(item => ({ value: item.id, label: `${item.name} · ${item.provider}` })), auth.secretId || "", "不使用密钥")}</label>
    ${field("Basic 用户名", input("username", auth.username, 'maxlength="160"'), "").replace('class="form-field"', 'class="form-field" data-auth-detail="basic"')}
    ${field("认证请求头", input("headerName", auth.header_name || "X-API-Key", 'maxlength="100"'), "").replace('class="form-field"', 'class="form-field" data-auth-detail="header"')}
    ${can("system.admin") ? `<label class="form-field wide capability-checkbox" data-network-provider><input type="checkbox" name="allowPrivate" ${config.allow_private ? "checked" : ""}><span>授权访问本机或内网目标</span></label>` : ""}
    <div class="form-field wide capability-tools-editor" data-tools-editor><div class="capability-editor-head"><span>工具定义</span><button class="button small" type="button" data-add-tool>添加工具</button></div><div data-tool-rows>${tools.map(toolEditor).join("")}</div></div>
  </div>`, {
    wide: true,
    saveLabel: record ? "保存新版本" : "保存草稿",
    onReady: dialog => {
      // 中文说明：provider 决定显示哪一组连接配置；数据库类型没有工具编辑器、也不需要认证配置；
      // 认证细节（用户名/请求头名）只在与 authType 匹配时出现——隐藏的字段同样会被提交，故用 hidden 而非移除时需留意默认值。
      const sync = () => {
        const current = $("[name=provider]", dialog).value;
        $$('[data-provider]', dialog).forEach(element => { element.hidden = element.dataset.provider !== current; });
        $$('[data-auth-provider]', dialog).forEach(element => { element.hidden = current === "database"; });
        $$('[data-network-provider]', dialog).forEach(element => { element.hidden = current === "database"; });
        $("[data-tools-editor]", dialog).hidden = current === "database";
        const authType = $("[name=authType]", dialog).value;
        $$('[data-auth-detail]', dialog).forEach(element => { element.hidden = element.dataset.authDetail !== authType; });
      };
      $("[name=provider]", dialog).addEventListener("change", sync);
      $("[name=authType]", dialog).addEventListener("change", sync);
      $("[data-add-tool]", dialog).addEventListener("click", () => {
        $("[data-tool-rows]", dialog).insertAdjacentHTML("beforeend", toolEditor({}, nextIndex++));
      });
      dialog.addEventListener("click", event => event.target.closest("[data-remove-tool]")?.closest("[data-tool-row]")?.remove());
      sync();
    },
    onSave: async (form, dialog) => {
      const currentProvider = record?.provider || String(form.get("provider"));
      let toolList;
      // 数据库连接器的工具集由后端内置定义决定，不采用表单里的自定义工具
      if (currentProvider === "database") toolList = databaseToolDefinitions();
      else toolList = $$('[data-tool-row]', dialog).map(row => {
        const name = $("input[name^=tool_name_]", row).value.trim();
        const description = $("input[name^=tool_description_]", row).value.trim();
        let inputSchema;
        try { inputSchema = JSON.parse($("textarea", row).value || "{}"); }
        catch { throw new Error(`工具 ${name || "未命名"} 的参数 Schema 不是有效 JSON`); }
        return { name, description, inputSchema, timeoutSeconds: 30 };
      });
      const authType = String(form.get("authType") || "none");
      const auth = currentProvider === "database" ? undefined : {
        type: authType,
        secretId: String(form.get("secretId") || "") || undefined,
        username: authType === "basic" ? String(form.get("username") || "") : undefined,
        headerName: authType === "header" ? String(form.get("headerName") || "") : undefined,
      };
      let configValue;
      if (currentProvider === "http") configValue = {
        baseUrl: String(form.get("baseUrl") || ""), path: String(form.get("path") || "/"),
        method: String(form.get("method") || "POST"), bodyMode: String(form.get("bodyMode") || "json"),
        headers: {}, auth, timeoutSeconds: 30, allowPrivate: form.get("allowPrivate") === "on",
      };
      else if (currentProvider === "database") configValue = { dataSourceId: String(form.get("dataSourceId") || "") };
      else configValue = {
        transport: "streamable_http", url: String(form.get("mcpUrl") || ""), headers: {}, auth,
        timeoutSeconds: 30, allowPrivate: form.get("allowPrivate") === "on",
      };
      const payload = {
        name: String(form.get("name") || "").trim(), provider: currentProvider,
        visibility: record?.visibility || "workspace", config: configValue, tools: toolList,
      };
      if (record) payload.version = record.version;
      const saved = record
        ? await post(`${BASE}/connectors/${record.id}/versions`, payload)
        : await post(BASE + "/connectors", payload);
      toast(record ? "新版本草稿已保存" : "连接器草稿已保存");
      go("/connectors", { id: saved.id });
    },
  });
}

// 中文说明：连接器测试弹窗：对指定版本的真实工具发起一次调用（HTTP/数据库/MCP 都会真实执行）。
// 参数：publishedOnly=true 时使用已发布版本（方法治理入口的测试必须针对线上版本）。
// 失败表现：参数 JSON 解析错误或调用失败在弹窗/结果窗中提示，不在前端做重试。
async function connectorTestDialog(record, selectedTool = "", publishedOnly = false) {
  const latest = publishedOnly
    ? (record.versions.find(version => version.id === record.publishedVersionId) || record.versions[0])
    : record.versions[0];
  openDialog("测试连接器", `<div class="form-grid">
    ${field("工具", select("toolName", latest.tools.map(item => ({ value: item.name, label: item.name })), selectedTool, selectedTool ? null : "请选择工具"))}
    ${field("版本", `<input value="v${esc(latest.version)}" disabled>`)}
    ${field("参数 JSON", textarea("arguments", "{}", 'rows="12" spellcheck="false"'), "", true)}
  </div>`, {
    wide: true, saveLabel: "运行测试",
    onSave: async form => {
      let args;
      try { args = JSON.parse(String(form.get("arguments") || "{}")); }
      catch { throw new Error("参数不是有效 JSON"); }
      const value = await post(`${BASE}/connectors/${record.id}/test`, {
        version: latest.version, toolName: String(form.get("toolName")), arguments: args,
      });
      setTimeout(() => showResult("连接测试结果", value), 0);
    },
  });
}

// 中文说明：平台内置方法的测试弹窗（无连接器来源，直接调 /methods/{name}/test）。稳定工具名不可编辑。
function builtinMethodTestDialog(tool) {
  openDialog("测试方法 · " + tool.displayName, `<div class="form-grid">
    ${field("稳定工具名", `<input value="${esc(tool.name)}" disabled>`)}
    ${field("参数 JSON", textarea("arguments", "{}", 'rows="12" spellcheck="false"'), "", true)}
  </div>`, {
    wide: true,
    saveLabel: "运行测试",
    onSave: async form => {
      let args;
      try { args = JSON.parse(String(form.get("arguments") || "{}")); }
      catch { throw new Error("参数不是有效 JSON"); }
      const value = await post(`${BASE}/methods/${encodeURIComponent(tool.name)}/test`, { arguments: args });
      setTimeout(() => showResult("方法测试结果", value), 0);
    },
  });
}

// 中文说明：治理页“编辑方法”：方法由连接器提供，编辑即打开所属连接器并创建新版本。
async function editManagedMethod(tool) {
  const record = await get(`${BASE}/connectors/${encodeURIComponent(tool.connectorId)}`);
  await connectorDialog(record);
}

// 中文说明：治理页“测试方法”：有连接器来源就测已发布版本，内置方法走内置测试弹窗。
async function testManagedMethod(tool) {
  if (!tool.connectorId) {
    builtinMethodTestDialog(tool);
    return;
  }
  const record = await get(`${BASE}/connectors/${encodeURIComponent(tool.connectorId)}`);
  await connectorTestDialog(record, tool.name, true);
}

// 中文说明：治理页“删除方法”。方法不能单独删除，删除即归档其来源连接器，
// 且会同时影响同来源的其他方法——确认文案必须把影响面（方法数量）写清楚，这是不可逆动作。
async function removeManagedMethod(tool) {
  const record = await get(`${BASE}/connectors/${encodeURIComponent(tool.connectorId)}`);
  const published = record.versions.find(version => version.id === record.publishedVersionId) || record.versions[0];
  const count = published.tools.length;
  const scope = count > 1 ? `，并同时停止该来源中的 ${count} 个方法` : "";
  confirmAction("删除方法", `“${tool.displayName}”由连接器“${record.name}”提供。确认后将归档来源连接器${scope}；历史版本和运行记录仍保留。`, async () => {
    await remove(`${BASE}/connectors/${record.id}?version=${record.version}`);
    toast("方法来源已归档");
    go("/capabilities", { view: "methods" });
  }, "确认删除");
}

// 中文说明：复制连接器为独立草稿（可加方法名前缀避免与源连接器的方法名冲突）。
function connectorCloneDialog(record) {
  openDialog("复制连接器", '<div class="form-grid">' +
    field("副本名称", input("name", record.name + " 副本", 'required maxlength="200"')) +
    field("方法名前缀", input("toolPrefix", "", 'maxlength="40" pattern="[A-Za-z][A-Za-z0-9_.\\x2D]*" placeholder="留空时自动生成"')) +
    '</div>', {
    saveLabel: "创建独立草稿",
    onSave: async form => {
      const value = await post(BASE + "/connectors/" + record.id + "/clone", {
        name: String(form.get("name") || "").trim(),
        toolPrefix: String(form.get("toolPrefix") || "").trim(),
      });
      toast("连接器副本已创建");
      go("/connectors", { id: value.id });
    },
  });
}

// 中文说明：切换共享范围（工作区可见 ↔ 仅自己）。历史版本与调用记录不受影响，故用 confirmAction 简单确认。
function shareConnector(record) {
  const visibility = record.visibility === "workspace" ? "private" : "workspace";
  const label = visibility === "workspace" ? "工作区可见" : "仅自己可见";
  confirmAction("修改共享范围", "将连接器设置为“" + label + "”？历史版本和调用记录不变。", async () => {
    await post(BASE + "/connectors/" + record.id + "/share", {
      version: record.version,
      visibility,
    });
    toast("共享范围已更新");
    go("/connectors", { id: record.id });
  }, "确认修改");
}

// 中文说明：连接器列表 / 详情页（?id= 进入详情，展示当前版本工具、版本记录与发布/归档/测试/复制入口）。
// 约束：仅最新版本未发布时才显示“发布”；归档是逻辑删除（保留历史版本与运行记录），走统一确认弹窗。
async function renderConnectors(root) {
  const query = new URLSearchParams(location.search);
  const id = query.get("id");
  if (id) {
    const record = await get(`${BASE}/connectors/${encodeURIComponent(id)}`);
    const latest = record.versions[0];
    const actions = `<a class="button" href="/connectors">返回列表</a>
      ${can("connectors.create") ? '<button class="button" type="button" data-connector-clone>复制</button>' : ""}
      ${can("connectors.publish") ? '<button class="button" type="button" data-connector-share>共享范围</button>' : ""}
      ${can("connectors.edit") ? '<button class="button" type="button" data-connector-version>新建版本</button>' : ""}
      ${can("connectors.execute") ? '<button class="button" type="button" data-connector-test>测试</button>' : ""}
      ${can("connectors.publish") && latest.status !== "published" ? '<button class="button primary" type="button" data-connector-publish>发布</button>' : ""}
      ${can("connectors.delete") ? '<button class="button danger" type="button" data-connector-archive>归档</button>' : ""}`;
    const toolTable = dataTable(["工具", "用途", "状态", "参数"], latest.tools,
      item => `<tr><td><span class="cell-primary">${esc(item.name)}</span></td><td>${esc(item.description || "—")}</td><td>${statusBadge(item.status)}</td><td><details><summary>查看</summary><pre class="json-preview compact-json">${esc(pretty(item.spec.inputSchema))}</pre></details></td></tr>`);
    compactPage(root, record.name, `${record.providerLabel} · v${record.version}`, actions,
      `<section class="panel catalog-detail-panel capability-detail"><dl class="detail-grid catalog-detail-grid"><div><dt>状态</dt><dd>${statusBadge(record.status)}</dd></div><div><dt>可见范围</dt><dd>${record.visibility === "workspace" ? "工作区" : "仅自己"}</dd></div><div><dt>当前版本</dt><dd>v${esc(record.version)}</dd></div></dl><h2 class="capability-section-title">当前版本工具</h2>${toolTable}<details class="capability-history"><summary>版本记录 · ${record.versions.length}</summary>${record.versions.map(version => `<div class="capability-version"><strong>v${version.version}</strong>${statusBadge(version.status)}<span>${date(version.createTime)}</span></div>`).join("")}</details></section>`);
    $("[data-connector-clone]", root)?.addEventListener("click", () => connectorCloneDialog(record));
    $("[data-connector-share]", root)?.addEventListener("click", () => shareConnector(record));
    $("[data-connector-version]", root)?.addEventListener("click", () => connectorDialog(record));
    $("[data-connector-test]", root)?.addEventListener("click", () => connectorTestDialog(record));
    $("[data-connector-publish]", root)?.addEventListener("click", () => confirmAction("发布连接器", `发布 v${latest.version} 后，AI 与工作流将使用这一版本。`, async () => {
      await post(`${BASE}/connectors/${record.id}/publish`, { version: latest.version }); toast("连接器已发布"); go("/connectors", { id: record.id });
    }, "确认发布"));
    $("[data-connector-archive]", root)?.addEventListener("click", () => confirmAction("归档连接器", "归档后不再提供新调用，历史版本和运行记录仍保留。", async () => {
      await remove(`${BASE}/connectors/${record.id}?version=${record.version}`); toast("连接器已归档"); go("/connectors");
    }, "确认归档"));
    return;
  }
  const keyword = query.get("keyword") || "";
  const provider = query.get("provider") || "";
  const result = await get(`${BASE}/connectors?keyword=${encodeURIComponent(keyword)}&provider=${encodeURIComponent(provider)}&limit=200`);
  const content = result.items.length ? dataTable(["名称", "类型", "状态", "版本", "最后修改", "操作"], result.items,
    item => `<tr><td><a class="cell-primary" href="/connectors?id=${esc(item.id)}">${esc(item.name)}</a></td><td>${esc(item.providerLabel)}</td><td>${statusBadge(item.status)}</td><td>v${esc(item.version)}</td><td>${date(item.updateTime)}</td><td><a href="/connectors?id=${esc(item.id)}">查看</a></td></tr>`) : emptyState("还没有连接器", "新建后先测试，再发布给 AI 和工作流使用。");
  compactPage(root, "连接器", "HTTP、数据库与 MCP 工具接入",
    can("connectors.create") ? '<button class="button primary" type="button" data-new-connector>新建连接器</button>' : "",
    `<section class="panel">${capabilityFilter(keyword, provider)}${content}</section>`);
  wireFilter(root, "/connectors");
  $("[data-new-connector]", root)?.addEventListener("click", () => connectorDialog());
}

// 表单只编辑可见字段，完整继承旧版本其余配置，避免改名时清空已导入的工具、资源或示例。
function skillSpec(form, previous = {}) {
  return {
    recommended_tools: [], required_resources: [], examples: [],
    ...previous,
    name: String(form.get("name") || "").trim(),
    description: String(form.get("description") || "").trim(),
    instructions: String(form.get("instructions") || "").trim(),
    rules: String(form.get("rules") || "").split(/\r?\n/).map(item => item.trim()).filter(Boolean),
  };
}

// 中文说明：Skill 表单：编辑已有 Skill 时提交的是“新版本”（POST /skills/{id}/versions），
// 已发布版本不会被改写；新建时保存草稿。
function skillDialog(record = null) {
  const latest = record?.versions?.[0]?.spec || {};
  openDialog(record ? "创建 Skill 新版本" : "新建 Skill", `<div class="form-grid">
    ${field("名称", input("name", record?.name || latest.name, 'required maxlength="200"'))}
    ${field("适用场景", input("description", record?.description || latest.description, 'maxlength="1000"'))}
    ${field("执行步骤", textarea("instructions", latest.instructions, 'required rows="8" placeholder="按顺序写清输入、处理和输出"'), "", true)}
    ${field("规则（每行一条）", textarea("rules", (latest.rules || []).join("\n"), 'rows="5"'), "", true)}
  </div>`, {
    wide: true, saveLabel: record ? "保存新版本" : "保存草稿",
    onSave: async form => {
      const spec = skillSpec(form, latest);
      const value = record
        ? await post(`/api/skills/${record.id}/versions`, { spec })
        : await post("/api/skills", { name: spec.name, description: spec.description, spec });
      toast("Skill 草稿已保存"); go("/skills", { id: record?.id || value.skill_id });
    },
  });
}

// 中文说明：Skill 测试运行（POST /skills/{id}/test-run），会真实执行一次并留下测试记录。
async function testSkill(record) {
  openDialog("测试 Skill", field("测试任务", textarea("input", "", 'required rows="6"'), "", true), {
    saveLabel: "运行测试", onSave: async form => {
      const value = await post(`/api/skills/${record.id}/test-run`, { input: String(form.get("input")) });
      setTimeout(() => showResult("Skill 测试结果", value), 0);
    },
  });
}

// 中文说明：复制 Skill 为独立草稿（副本名必填）。
function cloneSkill(record) {
  openDialog("复制 Skill", field("副本名称", input("new_name", record.name + " 副本", 'required maxlength="200"')), {
    saveLabel: "创建独立草稿",
    onSave: async form => {
      const value = await post("/api/skills/" + record.id + "/clone", {
        new_name: String(form.get("new_name") || "").trim(),
      });
      toast("Skill 副本已创建");
      go("/skills", { id: value.skill_id });
    },
  });
}

// 中文说明：切换 Skill 共享范围；已发布版本内容不变（只是可见范围变化）。
function shareSkill(record) {
  const visibility = record.visibility === "workspace" ? "private" : "workspace";
  const label = visibility === "workspace" ? "工作区可见" : "仅自己可见";
  confirmAction("修改共享范围", "将 Skill 设置为“" + label + "”？已发布版本不会被改写。", async () => {
    await post("/api/skills/" + record.id + "/share", { visibility });
    toast("共享范围已更新");
    go("/skills", { id: record.id });
  }, "确认修改");
}

// 中文说明：归档 Skill（逻辑删除）：列表不再出现，版本与测试记录保留，确认弹窗说明这一点。
function archiveSkill(record) {
  confirmAction("归档 Skill", "归档后不再出现在正常列表，版本和测试记录继续保留。", async () => {
    await post("/api/skills/" + record.id + "/archive", {});
    toast("Skill 已归档");
    go("/skills");
  }, "确认归档");
}

// 中文说明：Skill 列表 / 详情页。按钮组合由权限决定：create（复制）、publish（共享/发布）、edit（新版本/归档）、execute（测试）。
async function renderSkills(root) {
  const id = new URLSearchParams(location.search).get("id");
  if (id) {
    const record = await request("get", `/api/skills/${encodeURIComponent(id)}`);
    const latest = record.versions[0];
    const actions = `<a class="button" href="/skills">返回列表</a>${can("skills.create") ? '<button class="button" data-skill-clone>复制</button>' : ""}${can("skills.publish") ? '<button class="button" data-skill-share>共享范围</button>' : ""}${can("skills.edit") ? '<button class="button" data-skill-edit>新建版本</button><button class="button danger" data-skill-archive>归档</button>' : ""}${can("skills.execute") ? '<button class="button" data-skill-test>测试</button>' : ""}${can("skills.publish") && latest.status !== "published" ? '<button class="button primary" data-skill-publish>发布</button>' : ""}`;
    compactPage(root, record.name, `Skill · v${latest.version_number}`, actions,
      `<section class="panel catalog-detail-panel capability-detail"><dl class="detail-grid"><div><dt>状态</dt><dd>${statusBadge(record.status)}</dd></div><div><dt>版本</dt><dd>v${latest.version_number}</dd></div><div><dt>可见范围</dt><dd>${record.visibility === "workspace" ? "工作区" : "仅自己"}</dd></div><div class="wide"><dt>适用场景</dt><dd>${esc(record.description || "—")}</dd></div><div class="wide"><dt>执行步骤</dt><dd class="capability-prose">${esc(latest.spec.instructions || "—")}</dd></div><div class="wide"><dt>规则</dt><dd>${(latest.spec.rules || []).map(item => `<span class="capability-rule">${esc(item)}</span>`).join("") || "—"}</dd></div></dl><details class="capability-history"><summary>版本记录 · ${record.versions.length}</summary>${record.versions.map(version => `<div class="capability-version"><strong>v${version.version_number}</strong>${statusBadge(version.status)}</div>`).join("")}</details></section>`);
    $("[data-skill-clone]", root)?.addEventListener("click", () => cloneSkill(record));
    $("[data-skill-share]", root)?.addEventListener("click", () => shareSkill(record));
    $("[data-skill-archive]", root)?.addEventListener("click", () => archiveSkill(record));
    $("[data-skill-edit]", root)?.addEventListener("click", () => skillDialog(record));
    $("[data-skill-test]", root)?.addEventListener("click", () => testSkill(record));
    $("[data-skill-publish]", root)?.addEventListener("click", () => confirmAction("发布 Skill", "发布后，新任务可以按该版本执行。", async () => { await post(`/api/skills/${record.id}/publish`, {}); toast("Skill 已发布"); go("/skills", { id: record.id }); }, "确认发布"));
    return;
  }
  const data = await request("get", "/api/skills");
  const content = data.items?.length ? dataTable(["名称", "适用场景", "状态", "操作"], data.items,
    item => `<tr><td><a class="cell-primary" href="/skills?id=${esc(item.id)}">${esc(item.name)}</a></td><td>${esc(item.description || "—")}</td><td>${statusBadge(item.status)}</td><td><a href="/skills?id=${esc(item.id)}">查看</a></td></tr>`) : emptyState("还没有 Skill", "把稳定的业务步骤保存为可复用方法。");
  compactPage(root, "Skills 技能", "可复用的业务执行方法", can("skills.create") ? '<button class="button primary" data-new-skill>新建 Skill</button>' : "", `<section class="panel">${content}</section>`);
  $("[data-new-skill]", root)?.addEventListener("click", () => skillDialog());
}

// 中文说明：Plugin 表单（可绑定 Skill 与工具）。绑定选项依赖 /api/skills 与工具目录，
// 任一加载失败降级为空列表而不是阻塞表单（用户仍可先保存说明性内容）。
// 编辑已有 Plugin 时提交新版本，已发布版本不变。
async function pluginDialog(record = null) {
  const [skills, tools] = await Promise.all([
    request("get", "/api/skills").then(value => value.items || []).catch(() => []),
    get(BASE + "/tools?limit=500").then(value => value.items || []).catch(() => []),
  ]);
  const latest = record?.versions?.[0]?.spec || {};
  const selectedSkills = (latest.skill_refs || []).map(item => item.skill_id);
  const selectedTools = (latest.tool_refs || []).map(item => item.tool_name);
  openDialog(record ? "创建 Plugin 新版本" : "新建 Plugin", `<div class="form-grid">
    ${field("名称", input("name", record?.name || latest.name, 'required maxlength="200"'))}
    ${field("用途", input("description", record?.description || latest.description, 'maxlength="1000"'))}
    ${field("执行说明", textarea("instructions", latest.instructions, 'required rows="7"'), "", true)}
    <details class="form-field wide capability-bindings"><summary>绑定 Skill 与工具</summary><h3>Skills</h3>${checkboxes("skillRefs", skills.map(item => ({ value: item.id, label: item.name })), selectedSkills)}<h3>工具</h3>${checkboxes("toolRefs", tools.map(item => ({ value: item.name, label: item.name })), selectedTools)}</details>
  </div>`, {
    wide: true, saveLabel: record ? "保存新版本" : "保存草稿",
    onSave: async form => {
      const name = String(form.get("name") || "").trim();
      const description = String(form.get("description") || "").trim();
      const spec = { name, description, instructions: String(form.get("instructions") || "").trim(), skill_refs: form.getAll("skillRefs"), tool_refs: form.getAll("toolRefs"), resource_bindings: [], input_schema: emptySchema(), output_schema: {} };
      const value = record
        ? await post(`/api/plugins/${record.id}/versions`, { spec })
        : await post("/api/plugins", { name, description, spec });
      toast("Plugin 草稿已保存"); go("/plugins", { id: record?.id || value.plugin_id });
    },
  });
}

// 中文说明：Plugin 测试运行（POST /plugins/{id}/test-run），真实执行并记录结果。
async function testPlugin(record) {
  openDialog("测试 Plugin", field("测试任务", textarea("input", "", 'required rows="6"'), "", true), {
    saveLabel: "运行测试", onSave: async form => {
      const value = await post(`/api/plugins/${record.id}/test-run`, { input: String(form.get("input")) });
      setTimeout(() => showResult("Plugin 测试结果", value), 0);
    },
  });
}

// 中文说明：复制 Plugin 为独立草稿。
function clonePlugin(record) {
  openDialog("复制 Plugin", field("副本名称", input("new_name", record.name + " 副本", 'required maxlength="200"')), {
    saveLabel: "创建独立草稿",
    onSave: async form => {
      const value = await post("/api/plugins/" + record.id + "/clone", {
        new_name: String(form.get("new_name") || "").trim(),
      });
      toast("Plugin 副本已创建");
      go("/plugins", { id: value.plugin_id });
    },
  });
}

// 中文说明：切换 Plugin 共享范围；已发布版本与资源快照不变。
function sharePlugin(record) {
  const visibility = record.visibility === "workspace" ? "private" : "workspace";
  const label = visibility === "workspace" ? "工作区可见" : "仅自己可见";
  confirmAction("修改共享范围", "将 Plugin 设置为“" + label + "”？已发布版本和资源快照不变。", async () => {
    await post("/api/plugins/" + record.id + "/share", { visibility });
    toast("共享范围已更新");
    go("/plugins", { id: record.id });
  }, "确认修改");
}

// 中文说明：归档 Plugin（逻辑删除）：版本、绑定与运行记录保留。
function archivePlugin(record) {
  confirmAction("归档 Plugin", "归档后不再出现在正常列表，版本、绑定和运行记录继续保留。", async () => {
    await post("/api/plugins/" + record.id + "/archive", {});
    toast("Plugin 已归档");
    go("/plugins");
  }, "确认归档");
}

// 中文说明：Plugin 列表 / 详情页（按钮组合与 Skill 页一致，按 skills/plugins 各自权限码判定）。
async function renderPlugins(root) {
  const id = new URLSearchParams(location.search).get("id");
  if (id) {
    const record = await request("get", `/api/plugins/${encodeURIComponent(id)}`);
    const latest = record.versions[0];
    const toolNames = (latest.spec.tool_refs || []).map(item => item.tool_name).filter(Boolean);
    const actions = `<a class="button" href="/plugins">返回列表</a>${can("plugins.create") ? '<button class="button" data-plugin-clone>复制</button>' : ""}${can("plugins.publish") ? '<button class="button" data-plugin-share>共享范围</button>' : ""}${can("plugins.edit") ? '<button class="button" data-plugin-edit>新建版本</button><button class="button danger" data-plugin-archive>归档</button>' : ""}${can("plugins.execute") ? '<button class="button" data-plugin-test>测试</button>' : ""}${can("plugins.publish") && latest.status !== "published" ? '<button class="button primary" data-plugin-publish>发布</button>' : ""}`;
    compactPage(root, record.name, `Plugin · v${latest.version_number}`, actions,
      `<section class="panel catalog-detail-panel capability-detail"><dl class="detail-grid"><div><dt>状态</dt><dd>${statusBadge(record.status)}</dd></div><div><dt>可见范围</dt><dd>${record.visibility === "workspace" ? "工作区" : "仅自己"}</dd></div><div class="wide"><dt>用途</dt><dd>${esc(record.description || "—")}</dd></div><div class="wide"><dt>执行说明</dt><dd class="capability-prose">${esc(latest.spec.instructions || "—")}</dd></div><div class="wide"><dt>工具</dt><dd>${toolNames.map(item => `<span class="capability-rule">${esc(item)}</span>`).join("") || "—"}</dd></div></dl><details class="capability-history"><summary>版本记录 · ${record.versions.length}</summary>${record.versions.map(version => `<div class="capability-version"><strong>v${version.version_number}</strong>${statusBadge(version.status)}</div>`).join("")}</details></section>`);
    $("[data-plugin-clone]", root)?.addEventListener("click", () => clonePlugin(record));
    $("[data-plugin-share]", root)?.addEventListener("click", () => sharePlugin(record));
    $("[data-plugin-archive]", root)?.addEventListener("click", () => archivePlugin(record));
    $("[data-plugin-edit]", root)?.addEventListener("click", () => pluginDialog(record));
    $("[data-plugin-test]", root)?.addEventListener("click", () => testPlugin(record));
    $("[data-plugin-publish]", root)?.addEventListener("click", () => confirmAction("发布 Plugin", "发布后，AI 对话可以选择这一能力包。", async () => { await post(`/api/plugins/${record.id}/publish`, {}); toast("Plugin 已发布"); go("/plugins", { id: record.id }); }, "确认发布"));
    return;
  }
  const data = await request("get", "/api/plugins");
  const content = data.items?.length ? dataTable(["名称", "用途", "可见范围", "状态", "操作"], data.items,
    item => `<tr><td><a class="cell-primary" href="/plugins?id=${esc(item.id)}">${esc(item.name)}</a></td><td>${esc(item.description || "—")}</td><td>${item.visibility === "workspace" ? "工作区" : "仅自己"}</td><td>${statusBadge(item.status)}</td><td><a href="/plugins?id=${esc(item.id)}">查看</a></td></tr>`) : emptyState("还没有 Plugin", "组合 Skill、工具和资源，形成可发布能力包。");
  compactPage(root, "Plugins 插件", "可发布的业务能力包", can("plugins.create") ? '<button class="button primary" data-new-plugin>新建 Plugin</button>' : "", `<section class="panel">${content}</section>`);
  $("[data-new-plugin]", root)?.addEventListener("click", () => pluginDialog());
}

/**
 * 中文说明：能力相关四页的统一入口（由 main.js 的 routes 表指向 /capabilities、/connectors、/skills、/plugins）。
 * 行为：按 location.pathname 分发；未命中时落到工具与能力目录（含治理标签页）。
 * 副作用：由各子视图完成（读取为主，部分入口会触发外呼测试/写草稿）；失败异常由 main.js 统一渲染错误页。
 */
export async function renderCapabilities(root) {
  const path = location.pathname.replace(/\/$/, "");
  if (path === "/connectors") return renderConnectors(root);
  if (path === "/skills") return renderSkills(root);
  if (path === "/plugins") return renderPlugins(root);
  return renderToolCatalog(root);
}
