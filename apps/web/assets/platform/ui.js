/**
 * 平台外壳 UI 基础库（全局状态、HTML 片段生成器、统一弹窗与列表事件）
 *
 * 位置：所有功能模块（knowledge.js、capabilities.js、operations.js 等）都从这里取页面骨架与交互原语；
 * 本身不渲染具体业务页面，只提供 pageHeader/dataTable/pagination/searchBar 等模板函数，
 * 以及 openDialog/confirmAction 这一套唯一的弹窗实现。
 * 依赖接口：无网络请求；state 由 main.js 在启动时用后端 bootstrap 数据填充（me/permissions/navigation/settings）。
 * 与 iframe 内 Vue 应用无关：iframe 自带组件库，不使用这里的任何函数。
 *
 * 关键约束：
 * - 所有字符串模板必须经 esc() 转义：平台页面用 innerHTML 拼接，未转义的用户数据会直接变成 XSS。
 * - 权限判断必须走 can(permission)，权限码集合来自后端（state.permissions），前端不得自造权限码。
 * - 任何破坏性/不可逆操作必须走 confirmAction，禁止直接用 window.confirm 绕过统一文案与无障碍结构。
 * - 日期显示统一走 date()，时区取 state.settings["ui.timezone"]（默认 Asia/Shanghai），不要在页面里自行格式化。
 */

"use strict";

// 中文说明：全局运行态。me=当前用户，permissions=后端下发的权限码集合（唯一鉴权来源），
// navigation=后端返回的导航目录（决定可见模块），settings=平台配置（如时区、分页大小），pageSize=列表默认页大小。
export const state = { me: null, permissions: new Set(), navigation: [], settings: {}, pageSize: 20 };
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
// 中文说明：权限判定入口。只认后端 permission 目录中的权限码，前端隐藏 ≠ 已授权，后端仍会二次校验。
export const can = (permission) => state.permissions.has(permission);
// 中文说明：HTML 转义。所有拼进 innerHTML 的动态内容（含接口返回的文本）都必须先过这里。
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
// 中文说明：统一的日期时间展示。空值显示“—”；后端可能返回不带时区的 ISO 串或纯日期，
// 这两种都要按 UTC 解析后再转目标时区，否则会被浏览器当本地时间导致偏移 8 小时。
// 时区偏移失败（配置非法）时回落 Asia/Shanghai，保证页面不因配置错误而崩。
export function date(value) {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = new Date(typeof value === "string" && !/[zZ]$|[+-]\d\d:?\d\d$/.test(value)
    ? value + (value.includes(":") ? "Z" : "T00:00:00Z") : value);
  if (!Number.isFinite(parsed.getTime())) return "—";
  const options = { dateStyle: "medium", timeStyle: "short", timeZone: state.settings["ui.timezone"] || "Asia/Shanghai" };
  try { return new Intl.DateTimeFormat("zh-CN", options).format(parsed); }
  catch { return new Intl.DateTimeFormat("zh-CN", { ...options, timeZone: "Asia/Shanghai" }).format(parsed); }
}

// 中文说明：内置 SVG 图标路径表。键名被 navigation/模块定义直接引用（如 icon("shield")），
// 因此新增图标只能追加键，不要重命名已有键（会导致导航与按钮图标回落到默认设备图标）。
const paths = {
  home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
  chat: '<path d="M4 4h16v12H9l-5 4Z"/><path d="M8 8h8M8 12h5"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6m2 3a5 5 0 0 1 3 4v2"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6Z"/><path d="m8 12 3 3 5-6"/>',
  data: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  chart: '<path d="M3 3v18h18M7 16v-5m5 5V6m5 10V9"/>',
  book: '<path d="M4 4h6l2 2 2-2h6v16h-6l-2 1-2-1H4ZM12 6v15"/>',
  flow: '<rect x="8" y="2" width="8" height="5" rx="1"/><rect x="2" y="17" width="7" height="5" rx="1"/><rect x="15" y="17" width="7" height="5" rx="1"/><path d="M12 7v5H5v5m7-5h7v5"/>',
  equipment: '<path d="M5 3h14v18H5ZM8 7h8M8 11h8m-8 6h3m3 0h2"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2 6M20 4v7h-7"/>',
  logout: '<path d="M9 4H4v16h5M9 12h12m-5-5 5 5-5 5"/>',
};
// 中文说明：生成图标 SVG 字符串；未登记的图标名回落通用图标，避免出现空白按钮。
export function icon(name, className = "") {
  return `<svg class="icon ${className}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.equipment}</svg>`;
}
// 中文说明：按导航条目推导图标：优先同名图标，其次按所属分组映射，最后回落 home。
// 分组名必须与后端导航返回的 group 中文名一致（此处是硬编码对应关系）。
export function moduleIcon(entry) {
  if (paths[entry.id]) return entry.id;
  return ({ "系统管理": "shield", "数据与分析": "chart", "知识与内容": "book", "自动化与应用": "flow", "设备与质量": "equipment", "运营与运维": "data" })[entry.group] || "home";
}
// 中文说明：右上角轻提示。error=true 时展示为错误样式并延长到 7 秒（普通提示 3.5 秒）。
// 单例实现：同一次操作连续提示会覆盖上一条，且重置计时器，不要依赖它承载必须留存的信息（重要结果请用弹窗或页面内提示）。
export function toast(message, error = false) {
  const element = $("#platform-toast");
  element.textContent = message;
  element.classList.toggle("is-error", error);
  element.hidden = false;
  clearTimeout(element.timer);
  element.timer = setTimeout(() => { element.hidden = true; }, error ? 7000 : 3500);
}
// 中文说明：状态徽标。labels 覆盖后端所有状态枚举的中文文案，tone 决定颜色语义。
// 未登记的状态会原样显示英文值（便于发现后端新增枚举），tone 回落 info。
export function statusBadge(value) {
  const labels = { active: "启用", disabled: "停用", inactive: "停用", maintenance: "维护中", ready: "就绪", healthy: "正常", unavailable: "不可用", degraded: "需检查", draft: "草稿", published: "已发布", archived: "已归档", review: "待复核", accepted: "已接受", rejected: "已驳回", revoked: "已撤销", trusted: "可信", blocked: "已阻止", passed: "通过", true: "启用", false: "停用", completed: "已完成", success: "成功", partial: "部分完成", skipped: "未执行", failed: "失败", running: "执行中", waiting: "等待中", pending: "待处理", created: "已创建", cancelled: "已取消" };
  const tone = ["active", "true", "completed", "published", "accepted", "healthy", "trusted", "passed", "success"].includes(String(value)) ? "success" : ["failed", "unavailable", "rejected", "blocked"].includes(String(value)) ? "danger" : ["disabled", "false", "revoked"].includes(String(value)) ? "muted" : "info";
  return `<span class="status-badge ${tone}"><i></i>${esc(labels[value] || value)}</span>`;
}
// 中文说明：页面标题区。actions 为已拼接好的按钮 HTML（调用方负责转义其中的动态内容）。
export function pageHeader(title, description, actions = "") {
  return `<div class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="heading-actions">${actions}</div></div>`;
}
// 中文说明：通用按钮；label 自动转义，attributes 原样插入（调用方负责其中的动态数据转义）。
export function button(label, attributes = "", primary = false) {
  return `<button type="button" class="button${primary ? " primary" : ""}" ${attributes}>${esc(label)}</button>`;
}
// 中文说明：空状态占位（图标 + 标题 + 说明 + 可选操作按钮）；action 为已拼好的 HTML。
export function emptyState(title, description, action = "") {
  return `<div class="empty-state">${icon("data")}<h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
// 中文说明：页面级错误占位。403 单独提示“没有访问权限”，其余情况展示错误消息，
// 并提供“重新加载”按钮（整页刷新，用于丢弃已失效的页面状态）。副作用：清空 root 内容并重新绑定按钮。
export function renderError(root, error) {
  root.innerHTML = emptyState(error.status === 403 ? "当前账号没有访问权限" : "暂时无法加载", error.message,
    `<button class="button" type="button" data-retry>重新加载</button>`);
  $("[data-retry]", root)?.addEventListener("click", () => location.reload());
}
// 中文说明：表格骨架。rowHtml(row) 由调用方提供并负责行内转义。
// 约定：columns 最后一项为“操作”时追加 table-with-actions 类，用于固定操作列样式。
export function dataTable(columns, rows, rowHtml) {
  const hasActions = columns.at(-1) === "操作";
  return `<div class="table-scroll${hasActions ? " table-with-actions" : ""}" role="region" aria-label="数据表格，可横向滚动查看全部列" tabindex="0"><table><thead><tr>${columns.map((label) => `<th scope="col">${esc(label)}</th>`).join("")}</tr></thead><tbody>${rows.map(rowHtml).join("")}</tbody></table></div>`;
}
// 中文说明：分页条（上一页/下一页）。result 需含 total/page/page_size（后端分页响应结构）；
// 页码按钮带 disabled 属性，实际点击行为由 wireList 绑定。
export function pagination(result) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  return `<div class="pagination"><span>共 ${result.total} 条</span><div><button type="button" class="button small" data-page-prev ${result.page <= 1 ? "disabled" : ""}>上一页</button><span>${result.page} / ${pages}</span><button type="button" class="button small" data-page-next ${result.page >= pages ? "disabled" : ""}>下一页</button></div></div>`;
}
// 中文说明：列表工具栏（查询 + 重置）。表单标记 data-search-form，字段名固定 q，
// 与 wireList 的提交处理以及各页面后端查询参数名保持一致，改名会同时打断两处。
export function searchBar(placeholder, query = "", extra = "") {
  return `<form class="list-toolbar" data-search-form><label class="search-input">${icon("search")}<input name="q" aria-label="${esc(placeholder)}" placeholder="${esc(placeholder)}" value="${esc(query)}"></label>${extra}<button class="button" type="submit">查询</button><button class="button quiet" type="button" data-reset>重置</button></form>`;
}
// 中文说明：为列表控件绑定翻页/查询/重置事件，onChange(patch) 由调用方负责重新拉数据与重绘。
// 触发条件：[data-page-prev]、[data-page-next] 点击；[data-search-form] 提交（回车或点“查询”）；
// [data-reset] 点击（重置 q/status/department_id 三个通用筛选字段并回到第 1 页）。
// 注意：onChange 收到的是增量补丁，调用方需与当前查询条件合并；本函数不做任何请求与 DOM 替换。
export function wireList(root, result, onChange) {
  $("[data-page-prev]", root)?.addEventListener("click", () => onChange({ page: result.page - 1 }));
  $("[data-page-next]", root)?.addEventListener("click", () => onChange({ page: result.page + 1 }));
  $("[data-search-form]", root)?.addEventListener("submit", (event) => {
    event.preventDefault();
    onChange({ page: 1, ...Object.fromEntries(new FormData(event.currentTarget)) });
  });
  $("[data-reset]", root)?.addEventListener("click", () => onChange({ page: 1, q: "", status: "", department_id: "" }));
}
// 中文说明：表单字段容器。input 内含 <label> 时改用 fieldset/legend，避免 label 嵌套 label 的非法结构。
export function field(label, input, hint = "", wide = false) {
  if (input.includes("<label")) {
    return `<fieldset class="form-field${wide ? " wide" : ""}"><legend>${esc(label)}</legend>${input}${hint ? `<small>${esc(hint)}</small>` : ""}</fieldset>`;
  }
  return `<label class="form-field${wide ? " wide" : ""}"><span>${esc(label)}</span>${input}${hint ? `<small>${esc(hint)}</small>` : ""}</label>`;
}
// 中文说明：文本输入控件；options 原样拼进标签（如 required、type="number"），调用方负责转义。
export function input(name, value = "", options = "") {
  return `<input name="${esc(name)}" value="${esc(value)}" ${options}>`;
}
// 中文说明：下拉选择控件。emptyLabel 传 null 表示不渲染空选项（必填场景）；
// choices 为 [{ value, label, disabled }]，选中比较统一转字符串，避免数字/字符串 id 不匹配。
export function select(name, choices, value = "", emptyLabel = "请选择") {
  const options = (emptyLabel !== null ? [{ value: "", label: emptyLabel }] : []).concat(choices);
  return `<select name="${esc(name)}">${options.map((option) => `<option value="${esc(option.value)}" ${String(option.value) === String(value) ? "selected" : ""} ${option.disabled ? "disabled" : ""}>${esc(option.label)}</option>`).join("")}</select>`;
}
// 中文说明：多选复选框组（同名多值，提交后按 FormData 的 getAll 取数组）。无候选项时给出占位提示。
export function checkboxes(name, choices, selected = []) {
  return `<div class="checkbox-options">${choices.map((choice) => `<label><input type="checkbox" name="${esc(name)}" value="${esc(choice.value)}" ${selected.includes(choice.value) ? "checked" : ""} ${choice.disabled ? "disabled" : ""}><span>${esc(choice.label)}</span></label>`).join("") || "<span class='muted'>暂无可选项</span>"}</div>`;
}
/**
 * 中文说明：平台唯一弹窗实现（单例 #platform-dialog），所有新增/编辑/确认都走这里。
 *
 * 参数：title 标题；content 表单主体 HTML（须自行转义）；options.onSave(values, dialog) 提交回调，
 *   返回 Promise，抛错时错误消息显示在弹窗内且保持打开；saveLabel 提交按钮文案；
 *   wide 宽版；onReady(dialog) 渲染后回调（用于异步填充下拉选项，返回 Promise 时选项加载失败会提示）。
 *
 * 副作用：打开模态 dialog（showModal），会关闭上一次未清理的弹窗并中止其事件监听（_formCleanup）。
 * 行为约束：
 * - 表单提交前做原生校验，错误逐字段标红并汇总提示，未通过不发请求（避免脏数据落到后端）。
 * - 有未保存修改时点击关闭会先询问“是否放弃”，保存中禁止关闭（防止请求已在途却被用户误以为取消）。
 * - onSave 成功后仅当弹窗内仍是本次提交的表单才自动关闭：一次性密钥等后续弹窗不能被误关。
 * 兼容：editing“是否被编辑过”的判定基于 FormData 快照，File 只比较名称/大小/修改时间（内容不可序列化）。
 */
export function openDialog(title, content, { onSave, saveLabel = "保存", wide = false, onReady } = {}) {
  const dialog = $("#platform-dialog");
  dialog._formCleanup?.();
  if (dialog.open) dialog.close();
  dialog.classList.toggle("wide-dialog", wide);
  dialog.innerHTML = `<form class="dialog-form" novalidate><header><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" type="button" data-dialog-close aria-label="关闭对话框">${icon("close")}</button></header><div class="dialog-body">${content}</div><p class="form-error" role="alert" hidden></p><div class="dialog-discard" role="group" aria-label="未保存的修改" hidden><span>有未保存的修改，是否放弃？</span><button class="button" type="button" data-keep-editing>继续编辑</button><button class="button danger" type="button" data-discard>放弃修改</button></div><footer><button class="button" type="button" data-dialog-close>${onSave ? "取消" : "关闭"}</button>${onSave ? `<button class="button primary" type="submit">${esc(saveLabel)}</button>` : ""}</footer></form>`;
  const form = $("form", dialog);
  const errorElement = $(".form-error", form);
  const discard = $(".dialog-discard", form);
  const abort = new AbortController();
  dialog._formCleanup = () => abort.abort();
  let saving = false;
  let edited = false;
  let initialValues;
  const snapshot = () => JSON.stringify([...new FormData(form)].map(([name, value]) =>
    [name, value instanceof File ? (value.name ? [value.name, value.size, value.lastModified] : null) : value]));
  const close = () => {
    if (saving) return;
    if (onSave && edited && initialValues !== snapshot()) {
      discard.hidden = false;
      $("[data-keep-editing]", form).focus();
    } else dialog.close();
  };
  $$('[data-dialog-close]', form).forEach(element => element.addEventListener("click", close));
  $("[data-keep-editing]", form).addEventListener("click", () => {
    discard.hidden = true;
    $("input:not([disabled]),textarea,select,[type=submit]", form)?.focus();
  });
  $("[data-discard]", form).addEventListener("click", () => { if (!saving) dialog.close(); });
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); }, { signal: abort.signal });
  const validateControl = (control, index) => {
    if (!control.willValidate) return true;
    const blank = control.required && typeof control.value === "string" && !control.value.trim();
    const valid = control.validity.valid && !blank;
    const label = control.closest(".form-field");
    const id = "dialog-field-error-" + index;
    let message = form.querySelector("#" + id);
    if (!valid) {
      if (!message) { message = document.createElement("small"); message.id = id; message.className = "field-error"; (label || control.parentElement).append(message); }
      message.textContent = blank ? "请填写此项" : control.validationMessage;
      control.setAttribute("aria-invalid", "true");
      const described = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
      control.setAttribute("aria-describedby", [...new Set([...described, id])].join(" "));
    } else {
      message?.remove();
      control.removeAttribute("aria-invalid");
      const described = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(value => value && value !== id).join(" ");
      if (described) control.setAttribute("aria-describedby", described); else control.removeAttribute("aria-describedby");
    }
    return valid;
  };
  form.addEventListener("input", event => {
    edited = true;
    if (event.target.getAttribute("aria-invalid") === "true") validateControl(event.target, [...form.elements].indexOf(event.target));
    discard.hidden = true;
  });
  form.addEventListener("change", () => { edited = true; });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!onSave || saving) return;
    const submittedForm = event.currentTarget;
    const submit = $('[type="submit"]', dialog);
    const invalid = [...form.elements].filter((control, index) => !validateControl(control, index));
    if (invalid.length) {
      errorElement.textContent = `请修正 ${invalid.length} 处填写问题后再${saveLabel}。`;
      errorElement.hidden = false;
      invalid[0].focus();
      return;
    }
    const values = new FormData(submittedForm);
    saving = true;
    discard.hidden = true;
    errorElement.hidden = true;
    submit.disabled = true;
    submit.textContent = "正在保存…";
    form.setAttribute("aria-busy", "true");
    $(".dialog-body", form).inert = true;
    $$('[data-dialog-close]', form).forEach(element => { element.disabled = true; });
    try {
      await onSave(values, dialog);
      // Some successful actions replace this singleton with a follow-up
      // dialog (for example a one-time API key). Only close the form that
      // actually submitted; otherwise the newly displayed result vanishes.
      if (dialog.open && $("form", dialog) === submittedForm) dialog.close();
    } catch (error) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
    } finally {
      saving = false;
      form.removeAttribute("aria-busy");
      $(".dialog-body", form).inert = false;
      $$('[data-dialog-close]', form).forEach(element => { element.disabled = false; });
      submit.disabled = false;
      submit.textContent = saveLabel;
    }
  });
  dialog.showModal();
  const ready = onReady?.(dialog);
  initialValues = snapshot();
  if (ready?.then) Promise.resolve(ready).then(() => {
    if (!edited) initialValues = snapshot();
  }).catch(error => {
    if ($("form", dialog) === form) { errorElement.textContent = error.message || "表单选项加载失败，请关闭后重试。"; errorElement.hidden = false; }
  });
  return dialog;
}
// 中文说明：统一二次确认弹窗，用于删除、停用、撤销授权等破坏性操作。
// 参数 action 为确认后的异步回调（抛错时错误显示在弹窗内）；label 为确认按钮文案。
// 约定：业务模块不得绕过本函数直接调用 window.confirm，以保证文案、图标与无障碍行为一致。
export function confirmAction(title, description, action, label = "确认操作") {
  return openDialog(title, `<div class="confirmation">${icon("shield")}<p>${esc(description)}</p></div>`, { onSave: action, saveLabel: label });
}
