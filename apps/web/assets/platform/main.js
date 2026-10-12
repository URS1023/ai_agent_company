/**
 * 平台外壳入口（platform.html 唯一的 module 脚本）
 *
 * 位置：负责整站启动、登录/单点登录、权限目录加载、路由分发与页面外壳渲染。
 *   - 渲染区域：#platform-root 下的登录页、企业外壳（enterprise-header / business-sidebar /
 *     enterprise-contextbar / #main-content）以及沉浸式外壳（数字大屏 /report/view/*、设备工作流）。
 *   - 依赖接口前缀：/api/auth/*（登录、SSO、密码、登出、当前用户）、/api/v1/platform/navigation（导航 + 权限码 + 平台设置）。
 *   - 各业务页面实现分布在同目录功能模块中，由下方 routes 表按 location.pathname 分发。
 *
 * 与 iframe 内 Vue 应用的关系：assets/analytics（Vue 应用）通过 /analytics-app?path=... 以 iframe 嵌入，
 *   本文件只负责创建 iframe、同步地址栏，并通过 postMessage 处理两件事：
 *   子应用会话失效（cloud-brain-session-expired）与子应用内部跳转（cloud-brain-module-route / cloud-brain-module-navigate）。
 *   消息来源必须校验 origin 与 event.source，否则任意页面都能触发登出或路由改写。
 *
 * 关键约束：
 * - state.permissions 完全来自后端 /api/v1/platform/navigation，前端不推断权限；
 *   401 一律清会话回到登录页，403 交给 renderError 显示“没有访问权限”。
 * - 临时密码（must_change_password）必须先改密再进入业务模块，不允许跳过。
 * - SSO 回调路径 /sso/callback 只在启动时处理一次，成功后必须 history.replaceState 清掉 code/state，避免刷新重放。
 * - 所有页面请求都经 api.js（统一鉴权头 + 工作区 + 时限），页面内不得自行 fetch。
 */

import { api } from "./api.js";
import { $, $$, state, esc, icon, moduleIcon, renderError, field, input, toast } from "./ui.js";
import { renderHome } from "./home.js";
import { createEnterpriseNavigation, navigationEntry, platformName, brandImage } from "./enterprise-navigation.js";
import { renderUsers, renderUserDetail } from "./users.js";
import { renderRoles } from "./roles.js";
import { renderOrganization } from "./organization.js";
import { renderPermissions } from "./permissions.js";
import { renderProfile } from "./profile.js";
import { renderMenus, renderSessions, renderSettings, renderApiCenter } from "./administration.js";
import { renderOntology, renderExtraction, renderGraphs, renderBots } from "./knowledge-graph.js?v=20260908-extract-failure";
import { renderAssetCatalog } from "./asset-catalog.js?v=20260908-filters";
import { renderDocuments } from "./documents.js";
import { renderCapabilities } from "./capabilities.js?v=20260908-results2";
import { renderApplications } from "./applications.js?v=20260908-execution";
import { renderOperations } from "./operations.js";
import { renderSystemOperations, renderSystemDevelopment } from "./system-tools.js?v=20260908-api-pages";
import { renderWorkspaces } from "./workspaces.js";
import { renderSecurity } from "./security-governance.js";
import { renderModels } from "./model-governance.js";
import { renderFiles } from "./files.js";
import { renderKnowledge } from "./knowledge.js";

const ROOT = $("#platform-root");
let enterpriseNavigation = null;
const brand = () => platformName(state.settings["site.name"]);
const brandMarkup = () => `<span class="login-cloud-brain-brand">${brandImage()}</span><div><strong>${esc(brand())}</strong><small>企业智慧协作平台</small></div>`;
// 中文说明：被 iframe 承载的业务模块路径白名单（数据分析/质量/告警/工作流/报表等）。
// 只有命中这里的路径才会进 iframe，其余路径必须由 routes 表提供平台原生页面——放宽会导致白屏。
const embeddedPath = (path) => /^(?:\/(?:quality|alerts)\/devices\/[A-Za-z0-9_-]{1,26}\/workflow|\/quality(?:\/(?:metrics|templates|inspections))?|\/alerts(?:\/(?:devices|points|advice|rules|reports|problems))?|\/workflows(?:\/[A-Za-z0-9_-]+)?|\/analytics(?:\/(?:sql|history|quality|sources))?|\/datasources|\/dashboards|\/templates|\/prompts|\/data-query|\/jimu-screen|\/report(?:\/designer(?:\/[A-Za-z0-9_-]+)?|\/(?:basic|view)\/[A-Za-z0-9_-]+)?)$/.test(path);
// 数字大屏与设备工作流使用隐藏导航的沉浸式外壳（全屏内容，无侧边栏）
const immersiveReportPath = (path) => /^\/report\/view\/[A-Za-z0-9_-]+$/.test(path);
const immersiveWorkflowPath = (path) => /^\/(?:alerts|quality)\/devices\/[A-Za-z0-9_-]{1,26}\/workflow$/.test(path);
const clearImmersiveShell = () => document.body.classList.remove("enterprise-immersive-report", "enterprise-immersive-workflow");
// 中文说明：导航高亮路径。大屏与报表在导航上归入“数据大屏(/dashboards)”，避免侧边栏没有对应条目而整条高亮丢失。
const navigationPath = () => location.pathname.startsWith("/report") || location.pathname === "/jimu-screen" ? "/dashboards" : location.pathname;

// 中文说明：渲染登录/回调等公共左侧介绍布局，并销毁上一个页面的导航实例（防止重复绑定全局监听）。
function loginLayout(form) {
  enterpriseNavigation?.destroy(); enterpriseNavigation = null;
  clearImmersiveShell();
  ROOT.innerHTML = `<div class="login-layout"><aside class="login-intro"><div class="platform-brand">${brandMarkup()}</div><div><h1>让数据与业务<br>在同一个空间协作</h1><p>管理数据、知识、设备与质量。使用完整业务界面，也可以进入 AI 对话完成协同任务。</p><div class="login-domain-list"><span>${icon("chart")}数据分析与报表</span><span>${icon("book")}知识与图谱</span><span>${icon("equipment")}设备与质量</span><span>${icon("shield")}组织与权限</span></div></div><span class="login-caption">ONE WORKSPACE · CONNECTED BUSINESS</span></aside><main id="main-content" class="login-area"><div class="login-card">${form}</div></main></div>`;
}

// 中文说明：渲染本地账号登录表单并绑定提交。
// 副作用：登录成功写入会话（api.setSession）后立即 initialize() 进入平台；
// 失败只在表单内联展示错误，不跳转、不清空已输入内容。登录请求携带设备标识用于审计。
function showLogin(message = "") {
  loginLayout(`<h2>登录业务平台</h2><p>使用工作区账号登录，查看你有权限的业务模块。</p><form id="platform-login-form">${field("登录账号", input("username", "", 'id="login-username" required autocomplete="username" placeholder="输入登录账号"'))}${field("密码", input("password", "", 'id="login-password" type="password" required autocomplete="current-password" placeholder="输入密码"'))}<p class="form-error" role="alert" ${message ? "" : "hidden"}>${esc(message)}</p><button id="login-btn" class="button primary" type="submit">登录</button></form><section class="login-sso" data-sso-section hidden><div class="login-divider"><span>企业账号</span></div><div class="login-sso-options" data-sso-options></div></section><div class="login-help">还没有账号或忘记密码？请联系工作区管理员。临时密码首次登录后需要修改。</div>`);
  $("#platform-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = $("#login-btn");
    const errorElement = $(".form-error", event.currentTarget);
    const data = { ...Object.fromEntries(new FormData(event.currentTarget)), device_id: api.device };
    submit.disabled = true;
    submit.textContent = "正在登录…";
    errorElement.hidden = true;
    try {
      api.setSession(await api.post("/api/auth/login", data));
      await initialize();
    } catch (error) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = "登录";
    }
  });
  loadSsoOptions().catch(() => { /* 本地账号登录不依赖企业身份服务目录 */ });
}

// 中文说明：拉取可用的企业身份（SSO）提供方并渲染按钮。
// 无提供方时不显示该区域；接口不可用属于正常情况（本地账号登录不依赖它），由调用方吞掉异常。
// 点击后取授权地址并整页跳转到身份提供方，因此这里不做任何本地状态清理。
async function loadSsoOptions() {
  const section = $("[data-sso-section]");
  if (!section) return;
  const result = await api.get("/api/auth/sso/providers");
  if (!result.items?.length) return;
  const options = $("[data-sso-options]", section);
  options.innerHTML = result.items.map(item => `<button class="button login-sso-button" type="button" data-sso-provider="${esc(item.id)}"><span>${esc(item.name)}</span><small>${esc(item.workspace_name)}</small></button>`).join("");
  section.hidden = false;
  $$('[data-sso-provider]', section).forEach(element => element.addEventListener("click", async () => {
    const errorElement = $(".form-error", $("#platform-login-form"));
    element.disabled = true;
    errorElement.hidden = true;
    try {
      const result = await api.post(`/api/auth/sso/${encodeURIComponent(element.dataset.ssoProvider)}/authorize`, { device_id: api.device });
      location.assign(result.authorization_url);
    } catch (error) {
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      element.disabled = false;
    }
  }));
}

// 中文说明：处理企业登录回调 /sso/callback?code=&state=。
// code/state 一次性校验由后端完成（含 PKCE），成功后写入会话并清掉地址栏参数（history.replaceState），
// 防止用户刷新页面重复消费授权码；任何失败都回到登录页并显示后端给出的原因。
async function handleSsoCallback() {
  loginLayout(`<div class="login-callback" role="status">${icon("shield")}<h2>正在验证企业账号</h2><p>正在校验单次状态、PKCE 和签名令牌…</p></div>`);
  const params = new URLSearchParams(location.search);
  try {
    if (params.get("error")) throw new Error(params.get("error_description") || params.get("error"));
    if (!params.get("code") || !params.get("state")) throw new Error("企业登录回调缺少授权码或状态参数");
    const result = await api.get(`/api/auth/sso/callback?code=${encodeURIComponent(params.get("code"))}&state=${encodeURIComponent(params.get("state"))}`);
    api.setSession(result);
    history.replaceState({}, "", "/");
    await initialize();
  } catch (error) {
    history.replaceState({}, "", "/");
    showLogin(error.message || "企业账号验证失败，请重试。");
  }
}

// 中文说明：临时密码强制改密页。改密成功后主动清会话并要求用新密码重新登录，
// 不使用旧令牌继续操作（后端也会作废该令牌），因此不要在这里直接 initialize()。
function showRequiredPasswordChange() {
  loginLayout(`<h2>设置你自己的密码</h2><p>当前使用临时密码。完成修改后重新登录，再进入业务模块。</p><form id="required-password-form">${field("当前临时密码", input("current_password", "", 'type="password" required autocomplete="current-password"'))}${field("新密码", input("new_password", "", 'type="password" required minlength="10" maxlength="256" autocomplete="new-password"'), "至少 10 位，包含字母和数字。")}${field("确认新密码", input("confirm_password", "", 'type="password" required minlength="10" autocomplete="new-password"'))}<p class="form-error" role="alert" hidden></p><button class="button primary" type="submit">修改密码并重新登录</button><button class="button quiet" type="button" data-cancel-login>返回登录</button></form>`);
  $("[data-cancel-login]").addEventListener("click", () => { api.clearSession(); showLogin(); });
  $("#required-password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const errorElement = $(".form-error", event.currentTarget);
    const submit = $('[type="submit"]', event.currentTarget);
    errorElement.hidden = true;
    submit.disabled = true;
    try {
      if (form.get("new_password") !== form.get("confirm_password")) throw new Error("两次输入的新密码不一致");
      await api.post("/api/auth/password", { current_password: form.get("current_password"), new_password: form.get("new_password") });
      api.clearSession();
      showLogin("密码已修改，请使用新密码登录。");
    } catch (error) { errorElement.textContent = error.message; errorElement.hidden = false; }
    finally { submit.disabled = false; }
  });
}


// 中文说明：当前页面的导航条目（用于标题、面包屑与 iframe 标题）。
// 个人资料页不在后端导航目录里，需单独兜底；未命中时用通用名，避免标题出现 undefined。
function currentEntry() {
  if (location.pathname === "/account/profile") return { name: "个人资料", group: "账号设置" };
  return navigationEntry(state.navigation) || { name: "业务页面", group: "工作台" };
}

// 中文说明：渲染企业外壳骨架（页头/侧边栏/上下文栏/内容区）并创建导航组件。
// 副作用：销毁旧导航实例、清空 #platform-root、重写 document.title；登出会先请求 /api/auth/logout，
// 即使请求失败也要清本地会话（否则用户会卡在已登出但仍有令牌的状态）。
function renderShell() {
  enterpriseNavigation?.destroy();
  clearImmersiveShell();
  const entry = currentEntry();
  document.title = entry.name + " · " + brand();
  ROOT.innerHTML = `<div class="platform-shell enterprise-shell" data-testid="business-shell"><header class="enterprise-header" id="enterprise-header"></header><aside class="platform-sidebar enterprise-sidebar" id="business-sidebar"></aside><div class="platform-workspace"><div class="enterprise-contextbar" id="enterprise-contextbar"></div><main id="main-content" class="platform-content" tabindex="-1"><div class="empty-state" role="status">正在读取业务数据…</div></main></div></div>`;
  enterpriseNavigation = createEnterpriseNavigation({
    header: $("#enterprise-header"), sidebar: $("#business-sidebar"), context: $("#enterprise-contextbar"),
    items: state.navigation, me: state.me, settings: state.settings, onAccount: () => location.assign("/account/profile"),
    onLogout: async () => {
      try { await api.post("/api/auth/logout"); } catch { /* local credentials are still removed */ }
      api.clearSession(); showLogin();
    },
  });
}

// 中文说明：渲染沉浸式外壳（数字大屏 / 设备工作流）：隐藏企业导航，只留全屏内容区。
// 具体内容由 iframe 内的 analytics 应用绘制，这里只切换 body 上的沉浸式样式类。
function renderImmersiveModuleShell(path) {
  enterpriseNavigation?.destroy(); enterpriseNavigation = null;
  clearImmersiveShell();
  const report = immersiveReportPath(path);
  document.body.classList.add(report ? "enterprise-immersive-report" : "enterprise-immersive-workflow");
  document.title = (report ? "数字大屏" : "工作流设计") + " · " + brand();
  ROOT.innerHTML = `<main id="main-content" class="platform-content platform-content--module ${report ? "immersive-report-content" : "immersive-workflow-content"}" tabindex="-1"><div class="empty-state" role="status">${report ? "正在打开数字大屏…" : "正在打开设备工作流…"}</div></main>`;
}

// 中文说明：按 location.pathname 分发到具体页面（平台原生模块或 iframe 业务模块）。
// 顺序：先判断 iframe 白名单，再判断用户详情这类动态路径，最后查 routes 表；
// 未登记路径显示明确提示而非空白页。401 统一清会话回登录页，其余错误交给 renderError。
async function renderCurrentPage() {
  const root = $("#main-content");
  const path = location.pathname.replace(/\/$/, "") || "/";
  const routes = {
    "/": renderHome, "/app": renderHome, "/account/profile": renderProfile, "/system/users": renderUsers, "/system/roles": renderRoles,
    "/system/departments": (root) => renderOrganization(root, "departments"),
    "/system/positions": (root) => renderOrganization(root, "positions"),
    "/system/permissions": renderPermissions, "/system/menus": renderMenus,
    "/system/sessions": renderSessions, "/system/settings": renderSettings,
    "/system/workspaces": renderWorkspaces, "/system/security": renderSecurity,
    "/system/models": renderModels,
    "/system/operations": renderSystemOperations, "/system/development": renderSystemDevelopment,
    "/ontology": renderOntology, "/extraction": renderExtraction, "/graphs": renderGraphs, "/bots": renderBots,
    "/assets-catalog": renderAssetCatalog,
    "/documents": renderDocuments,
    "/capabilities": renderCapabilities,
    "/connectors": renderCapabilities,
    "/skills": renderCapabilities,
    "/plugins": renderCapabilities,
    "/applications": renderApplications,
    "/operations": renderOperations,
    "/runs": renderOperations,
    "/files": renderFiles,
    "/knowledge": renderKnowledge,
  };
  try {
    root.classList.remove("platform-content--module");
    if (embeddedPath(path)) renderBusinessModule(root, path + location.search);
    else if (/^\/system\/users\/[^/]+$/.test(path)) await renderUserDetail(root, path.split("/").at(-1));
    else if (routes[path]) await routes[path](root);
    else renderError(root, { message: "没有登记这个业务页面，请从顶部业务导航或功能搜索进入。" });
  } catch (error) {
    if (error.status === 401) { api.clearSession(); showLogin("会话已失效，请重新登录。"); }
    else renderError(root, error);
  }
}

// 中文说明：用 iframe 承载 analytics（Vue）业务模块，路径经 /analytics-app?path= 传入。
// allow 只授予 fullscreen 与 clipboard-write：不要放开摄像头/麦克风等能力，子应用不需要。
function renderBusinessModule(root, path) {
  root.classList.add("platform-content--module");
  root.innerHTML = "";
  const frame = document.createElement("iframe");
  frame.id = "business-module-frame";
  frame.className = "business-module-frame";
  frame.title = currentEntry().name || "业务模块";
  frame.allow = "fullscreen; clipboard-write";
  frame.src = "/analytics-app?path=" + encodeURIComponent(path);
  root.append(frame);
}

// 中文说明：iframe 内部路由变化时同步外壳（地址栏、标题、侧边栏高亮）。
// 只有同源且命中白名单的路径才接受；replace=true 用于返回/前进（popstate）场景，避免污染历史。
// 当沉浸式外壳需要切换（大屏 ↔ 工作流 ↔ 普通页面）时整页重新初始化，否则只更新标题与导航。
function updateModuleLocation(path, replace = false) {
  const parsed = new URL(path, location.origin);
  if (parsed.origin !== location.origin || !embeddedPath(parsed.pathname)) return;
  const next = parsed.pathname + parsed.search;
  if (next !== location.pathname + location.search) {
    history[replace ? "replaceState" : "pushState"]({}, "", next);
  }
  const reportShellActive = document.body.classList.contains("enterprise-immersive-report");
  const workflowShellActive = document.body.classList.contains("enterprise-immersive-workflow");
  if (reportShellActive !== immersiveReportPath(parsed.pathname) || workflowShellActive !== immersiveWorkflowPath(parsed.pathname)) {
    void initialize();
    return;
  }
  const entry = currentEntry();
  document.title = entry.name + " · " + brand();
  enterpriseNavigation?.update(state.navigation);
}

// 中文说明：与 iframe 内 Vue 应用的唯一通信通道。
// 安全约束：必须同时校验 event.origin 同源、event.source 是当前 iframe，
// 否则任意窗口都能伪造"会话过期"把用户踢下线，或伪造路由改写地址栏。
window.addEventListener("message", (event) => {
  const frame = $("#business-module-frame");
  if (!frame || event.origin !== location.origin || event.source !== frame.contentWindow) return;
  if (event.data?.type === "cloud-brain-session-expired") {
    api.clearSession();
    showLogin("会话已失效，请重新登录。");
  } else if (event.data?.type === "cloud-brain-module-route" && typeof event.data.path === "string") {
    updateModuleLocation(event.data.path);
  }
});
// 中文说明：浏览器返回/前进。iframe 在场且仍是白名单路径时只同步子应用（用 postMessage 通知其导航），
// 否则整页重新初始化——这样既保住 iframe 内未落库的编辑状态，又保证换模块时外壳正确重建。
window.addEventListener("popstate", () => {
  const frame = $("#business-module-frame");
  if (frame && embeddedPath(location.pathname)) {
    updateModuleLocation(location.pathname + location.search, true);
    frame.contentWindow.postMessage({ type: "cloud-brain-module-navigate", path: location.pathname + location.search }, location.origin);
  } else initialize();
});

// 中文说明：拉取导航目录、权限码集合与平台设置（时区、分页大小、站点名）。
// data.permissions 是前端所有 can() 判断的唯一来源，必须整体替换而不是合并，避免权限被回收后仍残留。
async function loadNavigation() {
  const data = await api.get("/api/v1/platform/navigation");
  state.navigation = data.items;
  state.permissions = new Set(data.permissions);
  state.settings = data.settings;
  state.pageSize = Number(data.settings["ui.page_size"]) || 20;
}

// 中文说明：启动流程（登录页 → 会话校验 → 强制改密 → 加载导航 → 渲染外壳 → 渲染页面）。
// 副作用：可能重写 #platform-root 与 document.title；401 清会话回登录页，其他错误在内容区展示。
async function initialize() {
  if (!api.token) { showLogin(); return; }
  try {
    state.me = await api.get("/api/auth/me");
    // 临时密码未修改时必须先改密：后端会限制其他接口，前端直接短路避免后续页面连续报错
    if (state.me.user.must_change_password) { showRequiredPasswordChange(); return; }
    await loadNavigation();
    const path = location.pathname.replace(/\/$/, "");
    if (immersiveReportPath(path) || immersiveWorkflowPath(path)) renderImmersiveModuleShell(path);
    else renderShell();
    await renderCurrentPage();
    if (location.pathname === "/" && new URLSearchParams(location.search).get("account") === "profile") location.replace("/account/profile");
  } catch (error) {
    if (error.status === 401) { api.clearSession(); showLogin("会话已失效，请重新登录。"); }
    else {
      clearImmersiveShell();
      ROOT.innerHTML = `<main id="main-content" class="platform-content"></main>`;
      renderError($("#main-content"), error);
    }
  }
}

// 中文说明：其他模块（如角色/权限页保存后）通过该事件请求刷新导航与权限，无需整页刷新。
// 事件名 "platform-navigation-changed" 是跨模块约定，改动需同步搜索所有派发方。
window.addEventListener("platform-navigation-changed", async () => {
  try {
    await loadNavigation();
    enterpriseNavigation?.update(state.navigation, state.settings);
    document.title = currentEntry().name + " · " + brand();
  } catch (error) { toast(error.message, true); }
});
// 中文说明：个人资料保存成功后由 profile.js 派发，这里只更新内存中的当前用户并刷新页头，
// 不再重新拉取导航（改资料不影响权限）。
window.addEventListener("platform-profile-changed", (event) => {
  if (!event.detail) return;
  state.me = event.detail;
  enterpriseNavigation?.update(state.navigation, state.settings, state.me);
});
// 中文说明：入口分流。SSO 回调页只处理一次授权码（不先渲染登录表单），其余路径走正常启动流程。
if (location.pathname.replace(/\/$/, "") === "/sso/callback") handleSsoCallback();
else initialize();
