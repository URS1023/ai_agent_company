/**
 * 业务运行中心模块（路由 /operations，三种视图：运行记录、审计日志、业务运营）
 *
 * 位置：渲染运行列表与运行详情（执行步骤、工具调用、模型调用、结果文件、运行事件、运行上下文）、
 *   审计日志列表与业务运营看板（趋势图、状态分布、治理指标、对象排行、待处理问题）。
 * 依赖接口前缀：/api/v1/operations（/summary 汇总、/runs 与 /runs/{id}、/runs/recover 中断确认、
 *   /audit 审计、/business 业务看板）；结果文件下载走运行详情返回的 downloadPath。
 * 与 iframe 内 Vue 应用无关。
 *
 * 关键约束：
 * - 三个视图的可见性由后端 summary 与权限共同决定：canViewAudit / canRecover 来自后端，
 *   业务运营域还要逐个校验 knowledge.view / bots.view / applications.view；
 *   前端隐藏视图只是减少误入，真正的拒绝仍在后端。
 * - “处理中断运行”是两段式：先 dryRun 预览（thresholdSeconds=900，即超过 15 分钟且已确认执行者退出），
 *   用户确认后再以 dryRun=false 执行；确认文案必须说明“业务数据和执行历史会保留”，
 *   并且只把仍符合条件的记录标记为失败——不要把等待中/执行中的记录一并处理。
 * - 运行详情中模型调用、工具调用、步骤的入参出参可能包含敏感数据：统一通过 jsonDialog 展示，不要内联渲染。
 * - 审计日志不可修改、不可删除：本页只提供查询与元数据查看。
 * - 结果文件下载必须带 Authorization 与 X-Workspace-Id（该接口不走 api.js，单独实现）。
 */

import { api } from "./api.js";
import {
  $, $$, can, esc, date, pageHeader, dataTable, emptyState, statusBadge,
  select, openDialog, toast, state,
} from "./ui.js";

const BASE = "/api/v1/operations";
// 中文说明：状态筛选项，取值必须与后端运行状态枚举一致（含 waiting 表示排队等待）。
const statusChoices = [
  { value: "created", label: "已创建" }, { value: "running", label: "执行中" },
  { value: "waiting", label: "等待中" }, { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" }, { value: "cancelled", label: "已取消" },
];
// 中文说明：业务运营可选域及其所需权限码：只有具备对应 view 权限的域才会显示（权限码与后端目录一致）。
const businessDomains = [
  { value: "knowledge", label: "知识资产", permission: "knowledge.view" },
  { value: "bots", label: "Bot", permission: "bots.view" },
  { value: "applications", label: "应用", permission: "applications.view" },
];
// 中文说明：趋势图序列名 → 中文文案（序列名由后端下发，未登记的原样显示）。
const businessSeriesLabels = {
  documents: "新增文档", chunks: "新增分段", completed: "成功", failed: "失败",
};

// 中文说明：本模块接口返回 { success, data } 包装，统一解包。
async function request(method, path, body) {
  const value = await api[method](path, body);
  return value?.success === true && "data" in value ? value.data : value;
}
const get = path => request("get", path);
const post = (path, body) => request("post", path, body);

// 中文说明：整页跳转；page=1 不写入查询串，保证首页地址干净（便于分享/回退）。
function go(values = {}) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined && !(key === "page" && Number(value) === 1)) query.set(key, value);
  });
  location.assign("/operations" + (query.size ? "?" + query.toString() : ""));
}

// 中文说明：耗时展示（<1 秒用毫秒、<60 秒用秒、其余用分秒），空值显示“—”。
function duration(value) {
  if (value === null || value === undefined) return "—";
  if (value < 1) return Math.round(value * 1000) + " ms";
  if (value < 60) return value.toFixed(value < 10 ? 2 : 1) + " 秒";
  return Math.floor(value / 60) + " 分 " + Math.round(value % 60) + " 秒";
}

// 中文说明：运行标题兜底：后端可能返回 "none"/"None 任务" 等占位值，统一显示为“系统调用”。
function runTitle(item) {
  const value = String(item?.title || "").trim();
  return !value || /^none(?:\s*任务)?$/i.test(value) ? "系统调用" : value;
}

// 中文说明：执行类型（模型配置名）兜底：空值或 "none" 显示“默认执行”。
function executionProfile(item) {
  const value = String(item?.modelProfile || "").trim();
  return !value || value.toLowerCase() === "none" ? "默认执行" : value;
}

// 中文说明：视图切换标签（运行记录 / 审计日志 / 业务运营）。后两者按权限与可用业务域动态显示。
function modeSwitch(summary, mode) {
  return `<nav class="operations-switch" aria-label="运维数据类型">
    <a href="/operations" class="${mode === "runs" ? "active" : ""}" ${mode === "runs" ? 'aria-current="page"' : ""}>运行记录</a>
    ${summary.canViewAudit ? `<a href="/operations?view=audit" class="${mode === "audit" ? "active" : ""}" ${mode === "audit" ? 'aria-current="page"' : ""}>审计日志</a>` : ""}
    ${businessDomains.some(item => can(item.permission)) ? `<a href="/operations?view=business" class="${mode === "business" ? "active" : ""}" ${mode === "business" ? 'aria-current="page"' : ""}>业务运营</a>` : ""}
  </nav>`;
}

// 中文说明：状态条：服务/数据库健康 + 运行计数 + 中断数 + 审计条数（审计数为 null 表示无权限，不展示）。
function statusBar(summary) {
  const serviceHealthy = summary.service.api === "healthy" && summary.service.database === "healthy";
  const runs = summary.runs;
  return `<div class="operations-statusbar" role="status">
    <span class="${serviceHealthy ? "healthy" : "unhealthy"}"><i></i>${serviceHealthy ? "服务与数据库正常" : "服务状态异常"}</span>
    ${runs ? `<span>运行 ${runs.total}</span><span>执行中 ${runs.active}</span><span class="${runs.stale ? "unhealthy" : ""}">中断 ${runs.stale}</span>` : ""}
    ${summary.auditCount !== null ? `<span>审计 ${summary.auditCount}</span>` : ""}
  </div>`;
}

// 中文说明：分页条（该接口用 limit/offset，此处换算成页码展示）。
function pager(result, values) {
  const page = Math.floor(result.offset / result.limit) + 1;
  const pages = Math.max(1, Math.ceil(result.total / result.limit));
  return `<div class="pagination"><span>共 ${result.total} 条</span><div>
    <button class="button small" type="button" data-operations-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button>
    <span>${page} / ${pages}</span>
    <button class="button small" type="button" data-operations-page="${page + 1}" ${page >= pages ? "disabled" : ""}>下一页</button>
  </div></div>`;
}

// 中文说明：绑定分页按钮（整页导航并保留当前筛选条件）。
function wirePager(root, values) {
  $$('[data-operations-page]', root).forEach(button => button.addEventListener("click", () => {
    go({ ...values, page: Number(button.dataset.operationsPage) });
  }));
}

// 中文说明：只读 JSON 弹窗（步骤入参出参、工具参数结果、模型用量、事件内容、审计元数据共用）。
// 统一对内容做 esc 转义并预格式化，避免把原始 JSON 内联进页面结构。
function jsonDialog(title, sections) {
  openDialog(title, `<div class="operations-json-dialog">${sections.map(section => `
    <section><h3>${esc(section.label)}</h3><pre class="json-preview" tabindex="0" aria-label="${esc(section.label)} JSON">${esc(JSON.stringify(section.value ?? {}, null, 2))}</pre></section>
  `).join("")}</div>`);
}

/**
 * 中文说明：处理中断运行（两段式确认）。
 * 第一步 dryRun 预览超过 15 分钟且已确认执行者退出的运行；无候选时直接 toast 并结束。
 * 副作用：确认后第二次调用 dryRun=false 会把仍符合条件的运行标记为失败（业务数据与执行历史保留），
 *   随后整页 reload 以刷新状态条与计数。
 * 约束：不做自动重试、不自动清理——该动作必须由运维人员显式确认。
 */
async function recoveryDialog() {
  const preview = await post(BASE + "/runs/recover", { thresholdSeconds: 900, dryRun: true });
  if (!preview.count) {
    toast(preview.skippedCount
      ? `没有可确认中断的运行；${preview.skippedCount} 条仍在等待、执行或无法确认状态，已保留`
      : "没有超过 15 分钟且已确认中断的运行");
    return;
  }
  const table = dataTable(["运行标识", "原状态", "运行时长"], preview.items,
    item => `<tr><td><a href="/operations?id=${esc(item.runId)}">${esc(item.runId)}</a></td><td>${statusBadge(item.status)}</td><td>${duration(item.ageSeconds)}</td></tr>`);
  openDialog("处理中断运行", `<div class="confirmation operations-recovery-note"><p>以下运行超过 15 分钟，且已确认本机执行者退出。确认时会再次核验，仍符合条件的记录将标记为失败，业务数据和执行历史会保留。</p><p>另有 ${Number(preview.skippedCount || 0)} 条仍在等待、执行或无法确认状态，已保留。</p></div>${table}`, {
    wide: true,
    saveLabel: "标记为失败",
    onSave: async () => {
      const result = await post(BASE + "/runs/recover", { thresholdSeconds: 900, dryRun: false });
      toast(`已处理 ${result.count} 条中断运行，保留 ${result.skippedCount || 0} 条等待中、执行中或状态未确认的记录`);
      location.reload();
    },
  });
}

// 中文说明：页头。仅运行视图且后端允许（summary.canRecover）时才显示“检查中断运行”。
function listHeader(summary, mode) {
  const descriptions = {
    runs: "任务执行、调用链与失败定位",
    audit: "审计操作与资源变更",
    business: "知识、Bot 与应用的真实运行数据",
  };
  return pageHeader("业务运行中心", descriptions[mode] || descriptions.runs,
    mode === "runs" && summary.canRecover ? '<button class="button" type="button" data-recover-runs>检查中断运行</button>' : "");
}

// 中文说明：运行记录列表视图（默认视图）：关键字/状态筛选 + 服务状态条 + 分页。
async function renderRunList(root) {
  const query = new URLSearchParams(location.search);
  const keyword = query.get("keyword") || "";
  const status = query.get("status") || "";
  const page = Math.max(1, Number(query.get("page")) || 1);
  const limit = state.pageSize;
  const [summary, result] = await Promise.all([
    get(BASE + "/summary"),
    get(`${BASE}/runs?keyword=${encodeURIComponent(keyword)}&status=${encodeURIComponent(status)}&limit=${limit}&offset=${(page - 1) * limit}`),
  ]);
  const toolbar = `<form class="list-toolbar" data-run-filter>
    <label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" value="${esc(keyword)}" placeholder="任务、运行标识或发起人" aria-label="搜索运行记录"></label>
    ${select("status", statusChoices, status, "全部状态").replace("<select ", '<select aria-label="运行状态" ')}
    <button class="button" type="submit">查询</button>
    ${keyword || status ? '<button class="button quiet" type="button" data-run-reset>重置</button>' : ""}
  </form>`;
  const table = result.items.length ? dataTable(
    ["任务", "状态", "发起人", "执行类型", "步骤 / 调用 / 结果", "耗时", "开始时间", "操作"], result.items,
    item => `<tr><td><a class="cell-primary" href="/operations?id=${esc(item.id)}">${esc(runTitle(item))}</a><span class="cell-secondary">${esc(item.id)}</span></td><td>${statusBadge(item.status)}</td><td>${esc(item.actor.name)}</td><td>${esc(executionProfile(item))}</td><td>${item.stepCount} / ${item.toolCount} / ${item.artifactCount}</td><td>${duration(item.durationSeconds)}</td><td>${date(item.createTime)}</td><td><a href="/operations?id=${esc(item.id)}">查看</a></td></tr>`,
  ) : emptyState(
    keyword || status ? "没有匹配的运行" : "尚无运行记录",
    keyword || status ? "调整筛选条件后重试。" : "运行工作流、启动应用或使用 AI 能力后，这里会自动形成可追踪记录。",
    !keyword && !status && can("workflows.view") ? '<a class="button primary" href="/workflows">前往运行工作流</a>' : "",
  );
  const values = { keyword, status };
  root.innerHTML = listHeader(summary, "runs") + `<section class="panel operations-panel">
    ${modeSwitch(summary, "runs")}${statusBar(summary)}${toolbar}${table}${pager(result, values)}
  </section>`;
  $("[data-run-filter]", root).addEventListener("submit", event => {
    event.preventDefault(); go(Object.fromEntries(new FormData(event.currentTarget)));
  });
  $("[data-run-reset]", root)?.addEventListener("click", () => go());
  $("[data-recover-runs]", root)?.addEventListener("click", () => recoveryDialog().catch(error => toast(error.message, true)));
  wirePager(root, values);
}

// 中文说明：可折叠区块（工具调用/模型调用/结果文件/运行事件共用）。无数据时不渲染，避免空折叠面板。
function foldedTable(title, count, content, className = "") {
  if (!count) return "";
  return `<details class="operations-fold ${esc(className)}"><summary>${esc(title)} · ${count}</summary><div>${content}</div></details>`;
}

// 中文说明：下载运行结果文件。这里直接 fetch 是因为需要原始 Response 转 blob；
// 必须手动带鉴权头与工作区头（与 api.js 保持一致），否则会 401 或取到错误租户的数据。
async function downloadArtifact(item) {
  const response = await fetch(item.downloadPath, {
    headers: { Authorization: "Bearer " + api.token, "X-Workspace-Id": api.workspace },
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("下载失败（" + response.status + "）");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = item.name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 中文说明：运行详情页：基本信息、错误摘要、执行步骤、以及可折叠的工具/模型调用、结果文件、运行事件。
// 各明细通过 jsonDialog 展示原始输入输出，便于定位问题；下载按钮执行期间禁用，避免重复点击。
async function renderRunDetail(root, id) {
  const record = await get(`${BASE}/runs/${encodeURIComponent(id)}`);
  const stepTable = record.steps.length ? dataTable(
    ["序号", "步骤", "类型", "状态", "重试", "耗时", "详情"], record.steps,
    item => `<tr><td>${item.sequence}</td><td class="cell-primary">${esc(item.name)}</td><td>${esc(item.type)}</td><td>${statusBadge(item.status)}</td><td>${item.retryCount}</td><td>${duration(item.durationSeconds)}</td><td><button class="text-button" type="button" data-step-json="${esc(item.id)}">查看</button></td></tr>`,
  ) : emptyState("没有步骤记录", "本次运行没有生成步骤事件。");
  const toolTable = record.toolCalls.length ? dataTable(
    ["工具", "状态", "耗时", "详情"], record.toolCalls,
    item => `<tr><td>${esc(item.toolName)}</td><td>${statusBadge(item.status)}</td><td>${duration(item.durationSeconds)}</td><td><button class="text-button" type="button" data-tool-json="${esc(item.id)}">参数与结果</button></td></tr>`,
  ) : "";
  const modelTable = record.modelCalls.length ? dataTable(
    ["供应商", "模型", "状态", "耗时", "详情"], record.modelCalls,
    item => `<tr><td>${esc(item.provider)}</td><td>${esc(item.model)}</td><td>${statusBadge(item.status)}</td><td>${duration(item.durationSeconds)}</td><td><button class="text-button" type="button" data-model-json="${esc(item.id)}">用量与元数据</button></td></tr>`,
  ) : "";
  const eventTable = record.events.length ? dataTable(
    ["序号", "事件", "时间", "详情"], record.events,
    item => `<tr><td>${item.sequence}</td><td>${esc(item.type)}</td><td>${date(item.createTime)}</td><td><button class="text-button" type="button" data-event-json="${item.sequence}">查看</button></td></tr>`,
  ) : "";
  const artifactTable = record.artifacts.length ? dataTable(
    ["结果", "类型", "大小", "状态", "操作"], record.artifacts,
    item => `<tr><td>${esc(item.name)}</td><td>${esc(item.type)}</td><td>${Math.max(1, Math.ceil(item.size / 1024))} KB</td><td>${statusBadge(item.status)}</td><td><button class="text-button" type="button" data-artifact-download="${esc(item.id)}">下载</button></td></tr>`,
  ) : "";
  const actions = `<a class="button" href="/operations">返回列表</a>${record.conversationPath ? `<a class="button primary" href="${esc(record.conversationPath)}">查看对话</a>` : ""}`;
  root.innerHTML = pageHeader(record.title, record.id, actions) + `
    <section class="panel catalog-detail-panel operations-detail">
      <dl class="detail-grid catalog-detail-grid">
        <div><dt>状态</dt><dd>${statusBadge(record.status)}</dd></div>
        <div><dt>发起人</dt><dd>${esc(record.actor.name)}${record.actor.username ? ` · ${esc(record.actor.username)}` : ""}</dd></div>
        <div><dt>执行类型</dt><dd>${esc(record.modelProfile || "—")}</dd></div>
        <div><dt>耗时</dt><dd>${duration(record.durationSeconds)}</dd></div>
        <div><dt>开始时间</dt><dd>${date(record.startTime || record.createTime)}</dd></div>
        <div><dt>完成时间</dt><dd>${date(record.completeTime)}</dd></div>
      </dl>
      ${record.errorSummary ? `<div class="notice-inline error-notice"><strong>${esc(record.errorCode || "运行失败")}</strong><span>${esc(record.errorSummary)}</span></div>` : ""}
      <div class="operations-section-heading"><h2>执行步骤</h2><button class="text-button" type="button" data-run-context>运行上下文</button></div>
      ${stepTable}
      ${foldedTable("工具调用", record.toolCalls.length, toolTable)}
      ${foldedTable("模型调用", record.modelCalls.length, modelTable)}
      ${foldedTable("结果文件", record.artifacts.length, artifactTable)}
      ${foldedTable("运行事件", record.events.length, eventTable)}
    </section>`;
  const byStep = new Map(record.steps.map(item => [item.id, item]));
  const byTool = new Map(record.toolCalls.map(item => [item.id, item]));
  const byModel = new Map(record.modelCalls.map(item => [item.id, item]));
  const byEvent = new Map(record.events.map(item => [String(item.sequence), item]));
  const byArtifact = new Map(record.artifacts.map(item => [item.id, item]));
  $("[data-run-context]", root).addEventListener("click", () => jsonDialog("运行上下文", [
    { label: "上下文", value: record.context }, { label: "模型用量", value: record.modelUsage },
  ]));
  $$('[data-step-json]', root).forEach(button => button.addEventListener("click", () => {
    const item = byStep.get(button.dataset.stepJson);
    jsonDialog(item.name, [{ label: "输入", value: item.input }, { label: "输出", value: item.output }, { label: "错误", value: { code: item.errorCode, message: item.errorSummary } }]);
  }));
  $$('[data-tool-json]', root).forEach(button => button.addEventListener("click", () => {
    const item = byTool.get(button.dataset.toolJson);
    jsonDialog(item.toolName, [{ label: "参数", value: item.arguments }, { label: "结果", value: item.result }, { label: "错误", value: { code: item.errorCode, message: item.errorSummary } }]);
  }));
  $$('[data-model-json]', root).forEach(button => button.addEventListener("click", () => {
    const item = byModel.get(button.dataset.modelJson);
    jsonDialog(item.model, [{ label: "用量", value: item.usage }, { label: "元数据", value: item.metadata }]);
  }));
  $$('[data-event-json]', root).forEach(button => button.addEventListener("click", () => {
    const item = byEvent.get(button.dataset.eventJson);
    jsonDialog(item.type, [{ label: "事件内容", value: item.payload }]);
  }));
  $$('[data-artifact-download]', root).forEach(button => button.addEventListener("click", async () => {
    button.disabled = true;
    try { await downloadArtifact(byArtifact.get(button.dataset.artifactDownload)); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }));
}

// 中文说明：审计日志视图（只读）：关键字/操作/资源类型筛选，“元数据”按钮展示原始 metadata。
// 审计记录不可修改或删除；带 runId 的记录可跳转到对应运行详情。
async function renderAuditList(root) {
  const query = new URLSearchParams(location.search);
  const keyword = query.get("keyword") || "";
  const action = query.get("action") || "";
  const resourceType = query.get("resourceType") || "";
  const page = Math.max(1, Number(query.get("page")) || 1);
  const limit = state.pageSize;
  const [summary, result] = await Promise.all([
    get(BASE + "/summary"),
    get(`${BASE}/audit?keyword=${encodeURIComponent(keyword)}&action=${encodeURIComponent(action)}&resourceType=${encodeURIComponent(resourceType)}&limit=${limit}&offset=${(page - 1) * limit}`),
  ]);
  const actions = [...new Set([action, ...result.actions].filter(Boolean))].map(value => ({ value, label: value }));
  const resources = [...new Set([resourceType, ...result.resourceTypes].filter(Boolean))].map(value => ({ value, label: value }));
  const toolbar = `<form class="list-toolbar" data-audit-filter>
    <label class="search-input"><span aria-hidden="true">⌕</span><input name="keyword" value="${esc(keyword)}" placeholder="操作、资源、用户或运行标识" aria-label="搜索审计日志"></label>
    ${select("action", actions, action, "全部操作").replace("<select ", '<select aria-label="审计操作" ')}
    ${select("resourceType", resources, resourceType, "全部资源").replace("<select ", '<select aria-label="资源类型" ')}
    <button class="button" type="submit">查询</button>
    ${keyword || action || resourceType ? '<button class="button quiet" type="button" data-audit-reset>重置</button>' : ""}
  </form>`;
  const table = result.items.length ? dataTable(
    ["时间", "操作人", "操作", "资源", "资源标识", "来源 IP", "详情"], result.items,
    item => `<tr><td>${date(item.createTime)}</td><td>${esc(item.actor.name)}<span class="cell-secondary">${esc(item.actor.username)}</span></td><td class="cell-primary">${esc(item.action)}</td><td>${esc(item.resourceType)}</td><td>${item.runId ? `<a href="/operations?id=${esc(item.runId)}">${esc(item.resourceId || item.runId)}</a>` : esc(item.resourceId || "—")}</td><td>${esc(item.ipAddress || "—")}</td><td><button class="text-button" type="button" data-audit-json="${esc(item.id)}">元数据</button></td></tr>`,
  ) : emptyState("没有匹配的审计日志", "调整筛选条件后重试。");
  const values = { view: "audit", keyword, action, resourceType };
  root.innerHTML = listHeader(summary, "audit") + `<section class="panel operations-panel">
    ${modeSwitch(summary, "audit")}${statusBar(summary)}${toolbar}${table}${pager(result, values)}
  </section>`;
  const byId = new Map(result.items.map(item => [item.id, item]));
  $$('[data-audit-json]', root).forEach(button => button.addEventListener("click", () => {
    const item = byId.get(button.dataset.auditJson);
    jsonDialog(item.action, [{ label: "资源", value: { type: item.resourceType, id: item.resourceId, runId: item.runId } }, { label: "元数据", value: item.metadata }]);
  }));
  $("[data-audit-filter]", root).addEventListener("submit", event => {
    event.preventDefault(); go({ view: "audit", ...Object.fromEntries(new FormData(event.currentTarget)) });
  });
  $("[data-audit-reset]", root)?.addEventListener("click", () => go({ view: "audit" }));
  wirePager(root, values);
}

// 中文说明：指标取值展示（数字按 zh-CN 千分位、最多 2 位小数，非数字原样展示），单位由后端给出。
function metricValue(item) {
  const value = typeof item.value === "number" ? item.value.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
  }) : item.value;
  return `${esc(value)}<small>${esc(item.unit || "")}</small>`;
}

// 中文说明：业务趋势图（纯 SVG 折线 + 图例 + 数据表）。
// 设计约束：纵轴上限取所有序列最大值（最小 1，避免除零）；横轴标签最多 6 个且必含首尾；
//   点位超过 30 天不画圆点标记以控制 DOM 规模；同时提供“查看完整数据”表格，保证可访问性与可核对性。
function trendChart(trend) {
  const categories = trend.categories || [];
  const series = trend.series || [];
  const width = 760;
  const height = 228;
  const plot = { left: 45, right: 18, top: 20, bottom: 36 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const maximum = Math.max(1, ...series.flatMap(item => item.data || []).map(Number));
  const x = index => plot.left + (categories.length <= 1 ? plotWidth / 2 : plotWidth * index / (categories.length - 1));
  const y = value => plot.top + plotHeight - Number(value || 0) * plotHeight / maximum;
  const colors = ["#2765c7", "#168070", "#d47a18"];
  const grid = [0, 0.25, 0.5, 0.75, 1].map(ratio => {
    const position = plot.top + plotHeight * ratio;
    const value = Math.round(maximum * (1 - ratio) * 100) / 100;
    return `<line x1="${plot.left}" y1="${position}" x2="${width - plot.right}" y2="${position}"/><text x="${plot.left - 8}" y="${position + 4}" text-anchor="end">${value}</text>`;
  }).join("");
  const labelIndexes = new Set([0, categories.length - 1]);
  const labelStep = Math.max(1, Math.ceil(categories.length / 6));
  categories.forEach((_item, index) => { if (index % labelStep === 0) labelIndexes.add(index); });
  const xLabels = [...labelIndexes].filter(index => index >= 0).sort((a, b) => a - b).map(index =>
    `<text x="${x(index)}" y="${height - 10}" text-anchor="middle">${esc(categories[index]?.slice(5) || "")}</text>`,
  ).join("");
  const lines = series.map((item, seriesIndex) => {
    const color = colors[seriesIndex % colors.length];
    const points = (item.data || []).map((value, index) => `${x(index)},${y(value)}`).join(" ");
    const markers = categories.length <= 30 ? (item.data || []).map((value, index) =>
      `<circle cx="${x(index)}" cy="${y(value)}" r="2.6"><title>${esc(categories[index])} · ${esc(businessSeriesLabels[item.name] || item.name)} ${esc(value)}</title></circle>`,
    ).join("") : "";
    return `<g style="--series-color:${color}"><polyline points="${points}"/>${markers}</g>`;
  }).join("");
  const legend = series.map((item, index) => `<span><i style="background:${colors[index % colors.length]}"></i>${esc(businessSeriesLabels[item.name] || item.name)}</span>`).join("");
  const rows = categories.map((category, index) => ({
    category,
    values: series.map(item => item.data?.[index] ?? 0),
  }));
  const data = dataTable(["日期", ...series.map(item => businessSeriesLabels[item.name] || item.name)], rows,
    item => `<tr><td>${esc(item.category)}</td>${item.values.map(value => `<td>${esc(value)}</td>`).join("")}</tr>`);
  return `<div class="business-trend">
    <div class="business-chart-legend" aria-hidden="true">${legend}</div>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${categories.length} 天业务趋势，${series.map(item => businessSeriesLabels[item.name] || item.name).join("、")}">
      <g class="business-chart-grid">${grid}${xLabels}</g><g class="business-chart-lines">${lines}</g>
    </svg>
    <details class="business-chart-data"><summary>查看完整数据 · ${categories.length} 天</summary>${data}</details>
  </div>`;
}

// 中文说明：状态分布条形图（以最大值归一化，最小可见宽度 2% 以免零值条完全消失）；无数据时给出占位文案。
function businessBreakdown(items) {
  const maximum = Math.max(1, ...items.map(item => Number(item.value)));
  return `<div class="business-breakdown">${items.map(item => `<div>
    <span>${statusBadge(item.name)}</span><span class="business-breakdown-bar"><i style="width:${Math.max(2, Number(item.value) * 100 / maximum)}%"></i></span><strong>${Number(item.value).toLocaleString("zh-CN")}</strong>
  </div>`).join("") || '<p class="muted">暂无状态数据</p>'}</div>`;
}

// 中文说明：业务运营看板视图（近 7/30/90/365 天）。
// 约束：可查看的业务域由权限决定（knowledge.view / bots.view / applications.view），
//   无任何可用域时展示明确提示而不是空白页；请求的 domain/objectId 非法时回落为第一个可用域/全部对象。
// 副作用：只读汇总请求；切换对象与时间范围会整页导航以保持可分享的地址。
async function renderBusinessOperations(root) {
  const query = new URLSearchParams(location.search);
  const availableDomains = businessDomains.filter(item => can(item.permission));
  const requestedDomain = query.get("domain") || "";
  const domain = availableDomains.some(item => item.value === requestedDomain)
    ? requestedDomain : availableDomains[0]?.value;
  const requestedDays = Number(query.get("days"));
  const days = [7, 30, 90, 365].includes(requestedDays) ? requestedDays : 30;
  const objectId = query.get("objectId") || "";
  if (!domain) {
    root.innerHTML = pageHeader("业务运行中心", "业务运营") + emptyState("没有可查看的业务域", "请联系管理员授予知识、Bot 或应用查看权限。");
    return;
  }
  const [summary, dashboard] = await Promise.all([
    get(BASE + "/summary"),
    get(`${BASE}/business?domain=${encodeURIComponent(domain)}&days=${days}${objectId ? `&objectId=${encodeURIComponent(objectId)}` : ""}`),
  ]);
  const domainSwitch = availableDomains.map(item => `<a href="/operations?view=business&domain=${item.value}&days=${days}" ${item.value === domain ? 'aria-current="page"' : ""}>${esc(item.label)}</a>`).join("");
  const objectChoices = dashboard.availableObjects.map(item => ({ value: item.id, label: item.name }));
  const metrics = dashboard.metrics.map(item => `<div role="listitem"><span>${esc(item.label)}</span><strong>${metricValue(item)}</strong></div>`).join("");
  const topItems = dashboard.topItems.length ? dataTable(
    [dashboard.label.replace("运营", ""), "状态", dashboard.topItems[0].primaryLabel, dashboard.topItems[0].secondaryLabel, "操作"], dashboard.topItems,
    item => `<tr><td class="cell-primary">${esc(item.name)}</td><td>${statusBadge(item.status)}</td><td>${Number(item.primary).toLocaleString("zh-CN")}</td><td>${Number(item.secondary).toLocaleString("zh-CN")}</td><td><a href="${esc(item.path)}">查看</a></td></tr>`,
  ) : emptyState("暂无业务数据", "在对应业务模块创建记录后，这里会自动汇总。");
  const governance = dashboard.governance.map(item => {
    const labels = { normal: "正常", warning: "待关注", critical: "需处理" };
    return `<a href="${esc(item.path)}"><span>${esc(item.name)}</span><strong>${esc(item.value)}${esc(item.unit || "")}</strong><em class="${esc(item.level)}">${labels[item.level] || esc(item.level)}</em></a>`;
  }).join("");
  const issues = dashboard.issues.length ? dataTable(
    ["对象", "问题", "状态", "发生时间", "操作"], dashboard.issues,
    item => `<tr><td class="cell-primary">${esc(item.name)}</td><td>${esc(item.detail)}</td><td>${statusBadge(item.status)}</td><td>${date(item.time)}</td><td><a href="${esc(item.path)}">定位</a></td></tr>`,
  ) : '<div class="business-clear"><i></i>当前没有待处理异常</div>';
  root.innerHTML = listHeader(summary, "business") + `<section class="panel operations-panel business-operations-panel">
    ${modeSwitch(summary, "business")}
    <div class="operations-business-toolbar">
      <nav aria-label="业务运营对象">${domainSwitch}</nav>
      <div><label>对象${select("objectId", objectChoices, objectId, "全部对象")}</label>
      <label>时间${select("days", [{ value: 7, label: "近 7 天" }, { value: 30, label: "近 30 天" }, { value: 90, label: "近 90 天" }, { value: 365, label: "近一年" }], days, null)}</label></div>
    </div>
    <div class="operations-business-metrics" role="list" aria-label="${esc(dashboard.label)}核心指标">${metrics}</div>
    <div class="business-operations-grid">
      <section><div class="operations-section-heading"><h2>趋势</h2></div>${trendChart(dashboard.trend)}</section>
      <section><div class="operations-section-heading"><h2>状态与治理</h2></div>${businessBreakdown(dashboard.breakdown)}<div class="business-governance">${governance}</div></section>
    </div>
    <div class="operations-section-heading"><h2>对象排行</h2></div>${topItems}
    <div class="operations-section-heading"><h2>待处理问题</h2></div>${issues}
  </section>`;
  $("[name=days]", root).addEventListener("change", event => go({
    view: "business", domain, objectId, days: event.currentTarget.value,
  }));
  $("[name=objectId]", root).addEventListener("change", event => go({
    view: "business", domain, objectId: event.currentTarget.value, days,
  }));
}

/**
 * 中文说明：业务运行中心入口：?id= 进运行详情；view=audit 审计日志；view=business 业务运营；默认运行列表。
 * 副作用：由子视图完成（“处理中断运行”会写运行状态）；异常由 main.js 统一渲染错误页。
 */
export async function renderOperations(root) {
  const query = new URLSearchParams(location.search);
  const id = query.get("id");
  if (id) return renderRunDetail(root, id);
  if (query.get("view") === "audit") return renderAuditList(root);
  if (query.get("view") === "business") return renderBusinessOperations(root);
  return renderRunList(root);
}
