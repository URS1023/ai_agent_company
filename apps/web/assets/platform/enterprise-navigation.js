/** Shared Cloud Brain domain navigation. */
/**
 * 企业导航模块（页头一级导航、侧边栏二级菜单、上下文栏、全局功能搜索）
 *
 * 位置：由 main.js 的 renderShell 创建（createEnterpriseNavigation），负责企业外壳中
 *   enterprise-header / business-sidebar / enterprise-contextbar 三个区域，以及 Ctrl+K 功能搜索弹窗；
 *   还向 home.js 提供业务域归类工具（BUSINESS_AREAS / areaFor / entriesForArea / areaHref / recentModules）。
 * 依赖接口：本模块自身不发请求，导航条目 items 与权限可见性（item.available）全部来自
 *   main.js 拉取的 GET /api/v1/platform/navigation 响应。
 * 与 iframe 内 Vue 应用的关系：导航条目指向的模块由 main.js 判定是否走 iframe；
 *   本模块只负责链接与高亮，并通过 options.navigate 回调把跳转交给外壳（iframe 场景下改走 history + postMessage）。
 *
 * 关键约束：
 * - 可用性以 item.available 为准（后端按角色计算），前端不得自行根据权限码推断模块是否可见。
 * - “最近访问”按 工作区 + 用户 隔离存储在 sessionStorage（键前缀含 workspace.id 与 user.id），
 *   换工作区/换账号不能串数据；存储不可用（隐私模式）时必须静默降级，不能抛错影响导航。
 * - 事件监听统一挂到实例自己的 AbortController 上，destroy() 时整体解绑；
 *   重复创建实例（切换外壳）前必须先 destroy，否则会出现多套监听与重复弹窗。
 * - 折叠/展开状态用 body 上的 enterprise-nav-open / enterprise-sidebar-collapsed 类表达，
 *   断点 920px 与 CSS 中的移动端断点必须保持一致。
 * - 搜索只展示 found（含 available 过滤）的结果；键盘交互需兼容中文输入法（isComposing / keyCode 229）。
 */

import { esc, icon, moduleIcon } from "./ui.js";

// 中文说明：业务域定义（一级导航）。modules 数组里的 id 必须与后端导航条目 id 完全一致，
// 否则该模块会从所有业务域消失；system 域的 sections 决定侧边栏二级分组及其顺序。
export const BUSINESS_AREAS = [
  { id: "home", label: "工作台", icon: "home", description: "查看业务资源与近期任务", modules: ["home"] },
  { id: "data", label: "数据分析", icon: "chart", description: "接入数据，分析指标，构建报表与数字大屏", modules: ["analytics", "dashboards", "datasources", "templates", "prompts", "assets"] },
  { id: "knowledge", label: "知识管理", icon: "book", description: "管理资料、知识库与知识图谱", modules: ["knowledge", "files", "ontology", "extraction", "graphs"] },
  { id: "business", label: "业务应用", icon: "equipment", description: "工作流、设备预警、质检与业务应用", modules: ["workflows", "alerts", "quality", "applications", "operations"] },
  { id: "ai", label: "AI 工作台", badge: "Cloud Brain", icon: "chat", description: "对话协同、文档生成与可复用 AI 能力", modules: ["chat", "documents", "bots", "skills", "plugins", "capabilities", "connectors"] },
  { id: "system", label: "系统管理", icon: "shield", description: "组织权限、平台配置与运行维护", modules: ["users", "workspaces", "departments", "positions", "roles", "permissions", "menus", "settings", "sessions", "security", "system-operations", "system-development", "audit", "api", "models", "secrets", "monitor", "runs"],
    sections: [
      { name: "身份与权限", modules: ["users", "workspaces", "departments", "positions", "roles", "permissions", "security"] },
      { name: "配置与运维", modules: ["menus", "settings", "sessions", "system-operations", "audit"] },
      { name: "开发与模型", modules: ["system-development", "models", "secrets", "api", "monitor", "runs"] },
    ],
  },
];

export const PRODUCT_NAME = "智慧云脑 Cloud Brain";

// 中文说明：站点显示名。目前返回常量，保留参数是为了兼容从 settings["site.name"] 读取的历史调用方。
export function platformName() {
  return PRODUCT_NAME;
}
// 中文说明：把当前路径匹配到导航条目（用于标题、面包屑、侧边栏高亮）。
// alias 表把历史/别名路径（如 /runs → /operations、/admin* → /system/*）映射到现行路径，
// 这些别名有独立 HTML 页面或外链，改动前需确认对应的 HTML 入口。
export function navigationEntry(items, pathname = location.pathname) {
  const path = pathname.replace(/\.html$/, "").replace(/\/$/, "") || "/";
  const alias = {
    "/app": "/", "/admin": "/system/users", "/admin-members": "/system/users",
    "/account/profile": "/",
    "/data-query": "/analytics", "/jimu-screen": "/dashboards", "/system/api": "/system/development",
    "/runs": "/operations", "/admin-ops": "/system/operations", "/admin-audit": "/system/operations",
    "/admin-models": "/system/models",
  };
  const selected = path.startsWith("/report") ? "/dashboards" : alias[path] || path;
  return items.find(item => {
    const target = item.path.replace(/\.html$/, "");
    return selected === target || (target !== "/" && selected.startsWith(target + "/"));
  }) || null;
}
// 中文说明：确定当前业务域。仅首页允许用 ?area= 参数切换（域入口页），
// 其他页面一律按当前模块所属域推断，避免 URL 参数与页面内容不一致。
export function areaFor(entry, areaId = new URLSearchParams(location.search).get("area")) {
  if (areaId && location.pathname === "/") return BUSINESS_AREAS.find(area => area.id === areaId) || BUSINESS_AREAS[0];
  return BUSINESS_AREAS.find(area => area.modules.includes(entry?.id)) || BUSINESS_AREAS[0];
}
// 中文说明：返回当前账号至少有一个模块的业务域（用于页头一级导航与首页卡片）。
export function availableAreas(items) {
  return BUSINESS_AREAS.filter(area => items.some(item => area.modules.includes(item.id)));
}
// 中文说明：某业务域下的模块条目，按后端 sort_order 升序（排序值由菜单管理维护）。
export function entriesForArea(area, items) {
  return items.filter(item => area.modules.includes(item.id)).sort((left, right) => (left.sort_order || 0) - (right.sort_order || 0));
}
// 中文说明：业务域入口链接：优先该域首个可用模块，其次任一可用模块，都没有则回到首页的域视图。
export function areaHref(area, items) {
  const preferred = items.find(item => item.id === area.modules[0] && item.available);
  return preferred?.path || entriesForArea(area, items).find(item => item.available)?.path || "/?area=" + area.id;
}
// 中文说明：品牌标识图（静态 SVG 资源）。alt 为空并标注 aria-hidden，装饰性图片不进入读屏。
export const brandImage = () => '<img src="/assets/platform/cloud-brain-mark.svg" alt="" width="36" height="36" aria-hidden="true">';
const chevron = '<svg class="icon nav-chevron" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="m8 10 4 4 4-4"/></svg>';

// 中文说明：最近访问的会话级存取。键名含工作区与用户，跨账号/跨工作区不共享；
// 解析失败或存储被禁用时按空列表处理（导航必须在无存储环境下仍可用）。
function storedList(key) {
  try { const value = JSON.parse(sessionStorage.getItem(key) || "[]"); return Array.isArray(value) ? value : []; }
  catch { return []; }
}
function saveList(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* navigation remains usable without storage */ }
}

/**
 * 中文说明：创建并渲染企业导航实例（页头 + 侧边栏 + 上下文栏 + 搜索弹窗），返回 { update, destroy }。
 * 参数：header/sidebar/context 为三个容器元素；items=后端导航条目；me=当前用户与工作区；
 *   settings=平台设置（站点名）；onAccount/onLogout 由外壳注入（登出会清会话并回登录页）；
 *   navigate(path) 可选，用于 iframe 场景下改走外壳路由而不是整页跳转。
 * 副作用：直接改写三个容器的 innerHTML，并向 document.body 追加搜索弹窗与移动端遮罩两个节点；
 *   destroy() 时必须调用，否则弹窗、遮罩与文档级监听会残留。
 * 行为约束：搜索/账号菜单的键盘与焦点管理（Ctrl+K、↑↓、Esc、Tab 循环）是既有无障碍行为，不要简化。
 */
export function createEnterpriseNavigation({ header, sidebar, context, items, me, settings = {}, onAccount, onLogout, navigate }) {
  let current = navigationEntry(items);
  let area = areaFor(current);
  let destroyed = false;
  // 存储键前缀绑定工作区与用户：同一浏览器多账号/多工作区不能互相看到“最近访问”
  const scope = "cloud-brain:" + me.workspace.id + ":" + me.user.id + ":";
  const recentKey = scope + "recent-modules";
  let name = platformName(settings["site.name"]);
  let headerMarkup = "";
  const abort = new AbortController();
  // 所有监听都挂在同一个 signal 上，destroy() 一次解绑，避免热切换外壳时监听泄漏
  const listen = (target, type, callback) => target?.addEventListener(type, callback, { signal: abort.signal });
  // navigate 由外壳注入（iframe 场景走 history + postMessage）；缺省时退化为整页跳转
  const go = (path) => navigate ? navigate(path) : location.assign(path);
  // 中文说明：记录当前模块到“最近访问”。首页与不可用模块不入记录；最多保留 8 条并去重。
  function remember() {
    if (!current?.available || current.id === "home") return;
    const tab = { id: current.id, path: location.pathname, label: current.label || current.name };
    const recent = storedList(recentKey).filter(item => item.id !== tab.id);
    saveList(recentKey, [tab, ...recent].slice(0, 8));
  }
  // 中文说明：侧边栏条目链接；首页自身不作为“当前项”高亮（首页由页头的工作台标签表达）。
  function link(item) {
    const active = current?.id === item.id && location.pathname !== "/";
    return `<a class="module-link enterprise-module-link${active ? " active" : ""}" href="${esc(item.path)}" ${active ? 'aria-current="page"' : ""}>${icon(moduleIcon(item))}<span>${esc(item.label || item.name)}</span></a>`;
  }
  // 中文说明：渲染侧边栏二级菜单。首页与 AI 对话页不显示侧边栏（body 加 enterprise-no-sidebar，
  // 该样式类与 CSS 联动，去掉会导致内容区留白）。系统管理域按 sections 分组折叠。
  function renderSidebar() {
    if (!sidebar) return;
    const hasContext = area.id !== "home" && current?.id !== "chat";
    document.body.classList.toggle("enterprise-no-sidebar", !hasContext);
    sidebar.hidden = !hasContext;
    const entries = entriesForArea(area, items);
    const ready = entries.filter(item => item.available);
    const groups = area.sections ? area.sections.map(section => {
      const members = ready.filter(item => section.modules.includes(item.id));
      if (!members.length) return "";
      const opened = members.some(item => item.id === current?.id) || (!current && section === area.sections[0]);
      return `<details class="enterprise-nav-section" name="business-subsection" ${opened ? "open" : ""}><summary>${esc(section.name)}${chevron}</summary><div>${members.map(link).join("")}</div></details>`;
    }).join("") : ready.map(link).join("");
    sidebar.innerHTML = `<div class="enterprise-sidebar-heading"><span>${icon(area.icon)}</span><strong>${esc(area.label)}</strong><button type="button" class="enterprise-icon-button enterprise-sidebar-close" data-close-navigation aria-label="关闭模块菜单">${icon("close")}</button></div><nav class="platform-navigation" aria-label="${esc(area.label)}二级菜单">${groups}</nav><div class="enterprise-sidebar-footer" aria-label="当前工作区"><span>${esc(me.workspace.name)}</span></div>`;
  }
  // 中文说明：渲染页头（品牌、一级业务导航、移动端域下拉、搜索按钮、账号菜单）。
  // 性能/体验约束：仅在标记文本变化时才写 innerHTML——否则每次 update 都会重建 DOM，
  // 正在输入或聚焦的控件会失焦。
  function renderHeader() {
    const areas = availableAreas(items);
    const markup = `<a href="/" class="enterprise-brand" aria-label="智慧云脑 Cloud Brain 首页">${brandImage()}<span>${esc(name)}</span></a><nav class="enterprise-primary" aria-label="一级业务导航">${areas.map(item => `<a href="${esc(areaHref(item, items))}" data-business-area="${item.id}" class="enterprise-area-link${item.id === area.id ? " active" : ""}" ${item.id === area.id ? 'aria-current="true"' : ""}>${icon(item.icon)}<span>${esc(item.label)}</span>${item.badge ? `<small>${esc(item.badge)}</small>` : ""}</a>`).join("")}</nav><label class="enterprise-area-select"><span class="sr-only">切换业务域</span><select aria-label="切换业务域">${areas.map(item => `<option value="${esc(areaHref(item, items))}" ${item.id === area.id ? "selected" : ""}>${esc(item.label)}${item.badge ? " · " + item.badge : ""}</option>`).join("")}</select></label><div class="enterprise-header-actions"><button type="button" class="enterprise-icon-button enterprise-search-button" data-global-search aria-label="搜索全部功能" title="搜索全部功能（Ctrl+K）">${icon("search")}</button><details class="enterprise-account"><summary aria-label="账号菜单"><span class="enterprise-avatar">${esc((me.user.display_name || "用").slice(0, 1))}</span><span class="enterprise-user-name">${esc(me.user.display_name)}</span>${chevron}</summary><div class="enterprise-account-panel"><strong>${esc(me.user.display_name)}</strong><small>${esc(me.workspace.name)}</small>${onAccount ? '<button type="button" data-account>个人资料与安全</button>' : '<a href="/system/users">用户与账号管理</a>'}<button type="button" data-about>关于平台</button><button type="button" data-logout>退出登录</button></div></details></div>`;
    // Preserve focused controls when only a menu label or page query changed.
    if (markup !== headerMarkup) { header.innerHTML = markup; headerMarkup = markup; }
  }
  // 中文说明：上下文栏（面包屑 + 移动端“模块菜单”开关 + 首页标签）。
  function renderContext() {
    if (!context) return;
    const hasContext = area.id !== "home" && current?.id !== "chat";
    context.innerHTML = `<div class="enterprise-context-inner">${hasContext ? `<button type="button" class="enterprise-icon-button enterprise-menu-toggle mobile-menu" data-toggle-navigation aria-label="展开或收起当前模块菜单" aria-controls="${sidebar?.id || "business-sidebar"}" aria-expanded="false">${icon("menu")}<span class="enterprise-menu-label">模块菜单</span></button>` : ""}${items.some(item => item.id === "home") ? `<a class="enterprise-home-tab${area.id === "home" ? " active" : ""}" href="/" aria-label="工作台首页">${icon("home")}</a>` : ""}<span class="enterprise-context-label">${esc(area.label)}${current && current.id !== "home" ? " / " + esc(current.label || current.name) : ""}</span></div>`;
  }

  const search = document.createElement("dialog");
  search.className = "enterprise-command-dialog";
  search.setAttribute("aria-labelledby", "enterprise-search-title");
  document.body.append(search);
  // 中文说明：搜索别名。为业务术语与英文标识符补充中文关键词（如“大屏”能搜到 dashboards），
  // 新增模块时可在此补充同义词，不需要改动后端导航数据。
  const searchAliases = { analytics: "SQL 智能问数 查询 历史 数据质量", knowledge: "知识库 RAG 检索", files: "文件 上传 下载 附件", workflows: "工作流 流程 自动化", users: "用户 成员 账号", models: "模型 LLM 配置", connectors: "接口 API HTTP MCP 连接器", dashboards: "看板 报表 大屏", assets: "资源 目录 数据" };
  // 中文说明：功能搜索。只搜 available 的条目（未授权模块不出现在结果里）；
  // 匹配支持子序列（字符按序出现即可），这是“模糊搜索”的既有行为，改成 includes 会显著降低召回。
  function searchResults(query = "") {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = (text, word) => {
      if (text.includes(word)) return true;
      let position = 0;
      for (const character of text) { if (character === word[position]) position++; if (position === word.length) return true; }
      return false;
    };
    const found = items.filter(item => item.available && words.every(word => matches([item.label, item.name, item.group, item.id, item.path, searchAliases[item.id], BUSINESS_AREAS.find(value => value.modules.includes(item.id))?.label].join(" ").toLowerCase(), word)));
    const body = search.querySelector(".enterprise-search-results");
    const recent = words.length ? [] : recentModules(items, me).filter(item => found.includes(item));
    const section = (label, entries) => `<section><h3>${esc(label)}</h3>${entries.map(item => `<a href="${esc(item.path)}">${icon(moduleIcon(item))}<span>${esc(item.label || item.name)}</span>${icon("arrow")}</a>`).join("")}</section>`;
    body.innerHTML = found.length ? (recent.length ? section("最近访问", recent) : "") + availableAreas(items).map(group => {
      const entries = found.filter(item => group.modules.includes(item.id) && !recent.includes(item));
      return entries.length ? section(group.label, entries) : "";
    }).join("") : '<p class="enterprise-search-empty" role="status">未找到可访问的功能，请更换关键词。</p>';
    search.querySelector("[data-search-count]").textContent = found.length + " 个可访问功能";
    body.querySelector("a")?.classList.add("is-selected");
  }
  // 中文说明：打开搜索弹窗。每次重建内容以确保输入框为空、计数为最新；
  // 内容里保留 id=enterprise-search-title，与 dialog 的 aria-labelledby 对应。
  function openSearch() {
    if (search.open) return;
    search.innerHTML = `<header><h2 id="enterprise-search-title">搜索全部功能</h2><button type="button" class="enterprise-icon-button" data-close-search aria-label="关闭功能搜索">${icon("close")}</button></header><label class="enterprise-search-input">${icon("search")}<input type="search" id="module-search" aria-label="查找已授权功能" placeholder="搜索功能，例如：用户、报表、SQL" autocomplete="off"></label><p class="enterprise-search-meta"><span data-search-count role="status" aria-live="polite"></span><span>↑ ↓ 选择 · Enter 打开 · Esc 关闭</span></p><div class="enterprise-search-results"></div>`;
    searchResults();
    search.showModal();
    search.querySelector("input").focus();
  }
  listen(search, "input", event => { if (event.target.matches("input")) searchResults(event.target.value); });
  listen(search, "click", event => {
    if (event.target.closest("[data-close-search]")) search.close();
  });
  listen(search, "keydown", event => {
    // 中文输入法组词期间不响应方向键/回车，否则会把拼音当按键处理
    if (event.isComposing || event.keyCode === 229) return;
    const links = [...search.querySelectorAll(".enterprise-search-results a")];
    if (["ArrowDown", "ArrowUp"].includes(event.key) && (event.target.matches("input") || links.includes(event.target))) {
      event.preventDefault();
      const index = links.indexOf(document.activeElement);
      const next = index < 0 ? (event.key === "ArrowDown" ? 0 : links.length - 1) : (index + (event.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
      links.forEach((link, position) => link.classList.toggle("is-selected", position === next));
      links[next]?.focus();
      links[next]?.scrollIntoView({ block: "nearest" });
    }
    if (event.key === "Enter" && event.target.matches("input")) {
      const first = search.querySelector(".enterprise-search-results a");
      if (first) { event.preventDefault(); search.close(); go(first.getAttribute("href")); }
    }
  });

  // 中文说明：移动端收起菜单（同时复位遮罩与 aria-expanded），断点判断在点击处完成。
  function closeMobile() {
    document.body.classList.remove("enterprise-nav-open");
    context?.querySelector("[data-toggle-navigation]")?.setAttribute("aria-expanded", "false");
    backdrop.hidden = true;
  }
  // 中文说明：同步菜单开关按钮的 aria-expanded：移动端看 enterprise-nav-open，桌面端看是否被折叠。
  function syncMenuState() {
    const opened = innerWidth <= 920 ? document.body.classList.contains("enterprise-nav-open")
      : !document.body.classList.contains("enterprise-sidebar-collapsed");
    context?.querySelector("[data-toggle-navigation]")?.setAttribute("aria-expanded", String(opened));
  }
  const backdrop = document.createElement("button");
  backdrop.type = "button";
  backdrop.className = "enterprise-navigation-backdrop";
  backdrop.setAttribute("aria-label", "关闭模块菜单");
  backdrop.hidden = true;
  document.body.append(backdrop);
  listen(backdrop, "click", closeMobile);
  listen(sidebar, "click", event => { if (event.target.closest("[data-close-navigation]")) { closeMobile(); context?.querySelector("[data-toggle-navigation]")?.focus(); } });
  listen(context, "click", event => {
    // 移动端打开侧边栏（含遮罩与焦点转移），桌面端切换折叠；两种状态用不同 body 类表达
    if (event.target.closest("[data-toggle-navigation]")) {
      if (innerWidth <= 920) {
        const opened = document.body.classList.toggle("enterprise-nav-open");
        backdrop.hidden = !opened;
        event.target.closest("button").setAttribute("aria-expanded", String(opened));
        if (opened) sidebar?.querySelector("a, summary, button")?.focus();
      } else { document.body.classList.toggle("enterprise-sidebar-collapsed"); syncMenuState(); }
    }
  });
  listen(header, "change", event => { if (event.target.matches(".enterprise-area-select select")) go(event.target.value); });
  listen(header, "click", event => {
    if (event.target.closest("[data-global-search]")) { header.querySelector(".enterprise-account").open = false; openSearch(); }
    if (event.target.closest("[data-account]")) { header.querySelector(".enterprise-account").open = false; onAccount?.(); }
    if (event.target.closest("[data-logout]")) onLogout?.();
    if (event.target.closest("[data-about]")) {
      header.querySelector(".enterprise-account").open = false;
      search.innerHTML = `<header><h2 id="enterprise-search-title">关于平台</h2><button type="button" class="enterprise-icon-button" data-close-search aria-label="关闭平台说明">${icon("close")}</button></header><div class="enterprise-about"><h3>${esc(name)}</h3><p>统一连接企业数据、知识、业务流程与 AI 能力的智慧协作平台。</p><p>Cloud Brain · 数据可信、流程可控、结果可追溯。</p><p>各模块的可用范围以实际实施与验证记录为准。</p></div>`;
      search.showModal();
    }
  });
  listen(document, "click", event => {
    if (!event.target.closest(".enterprise-account")) header.querySelector(".enterprise-account")?.removeAttribute("open");
  });
  listen(window, "resize", () => { if (innerWidth > 920) closeMobile(); syncMenuState(); });
  listen(document, "keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openSearch(); }
    if (event.key === "Escape") {
      const wasOpen = document.body.classList.contains("enterprise-nav-open");
      closeMobile();
      if (wasOpen) context?.querySelector("[data-toggle-navigation]")?.focus();
      header.querySelector(".enterprise-account")?.removeAttribute("open");
    }
    if (event.key === "Tab" && document.body.classList.contains("enterprise-nav-open") && sidebar) {
      const focusable = [...sidebar.querySelectorAll("button:not([disabled]),a[href],summary")].filter(item => {
        const closed = item.closest("details:not([open])");
        return item.getClientRects().length && (!closed || closed.firstElementChild === item);
      });
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  function update(nextItems = items, nextSettings = settings, nextMe = me) {
    if (destroyed) return;
    items = nextItems;
    settings = nextSettings;
    me = nextMe;
    name = platformName(settings["site.name"]);
    current = navigationEntry(items);
    area = areaFor(current);
    remember(); renderHeader(); renderSidebar(); renderContext();
    syncMenuState();
    if (search.open && search.querySelector("input")) searchResults(search.querySelector("input").value);
  }
  update();
  return {
    update,
    destroy() { destroyed = true; abort.abort(); search.remove(); backdrop.remove(); document.body.classList.remove("enterprise-nav-open", "enterprise-no-sidebar"); },
  };
}

// 中文说明：最近访问的模块（供首页与搜索弹窗展示）。
// 约束：以当前导航条目为准做交集过滤——已下线或当前账号不可用的历史记录不会再出现；
// 存储按 工作区 + 用户 隔离（键与实例内 remember 一致），最多返回 5 条。
export function recentModules(items, me) {
  return storedList("cloud-brain:" + me.workspace.id + ":" + me.user.id + ":recent-modules")
    .map(tab => items.find(item => item.id === tab.id && item.available)).filter(Boolean).slice(0, 5);
}
