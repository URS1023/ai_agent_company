/**
 * 工作台首页模块（路由 "/" 与 "/app"）
 *
 * 位置：渲染登录后的首个页面：资源统计条、业务空间卡片、最近访问、最近任务/最近生成结果。
 * 依赖接口：GET /api/v1/platform/overview（指标 + 最近产物 + 最近任务）；
 *   其余信息来自已加载的导航目录（state.navigation）与当前用户（state.me），不额外发请求。
 * 与 iframe 内 Vue 应用的关系：本页只是入口页，不嵌入 iframe；点击业务空间卡片的链接才跳转到 analytics 模块。
 *
 * 关键约束：
 * - 所有入口必须经 entriesForArea 过滤 available，未授权的模块不能出现在首页（前端隐藏是第一层，后端仍会校验）。
 * - “最近任务/最近生成结果”的“全部记录/全部文件”链接按 runs.view / files.view 权限显示，权限码必须与后端目录一致。
 * - 从业务域页面进入时（path 命中某个 area）不请求 overview，直接渲染该域入口列表，减少无谓请求。
 */

import { api } from "./api.js";
import { state, esc, date, icon, pageHeader, emptyState, statusBadge } from "./ui.js";
import { availableAreas, areaFor, areaHref, entriesForArea, navigationEntry, recentModules } from "./enterprise-navigation.js";

// 中文说明：业务域卡片。系统管理域固定把用户/角色/权限三个入口排在最前（身份管理是管理员最常用动作），
// 其余入口保持导航原顺序，卡片最多展示 3 个快捷入口。
function areaCard(area) {
  const ready = entriesForArea(area, state.navigation).filter(entry => entry.available);
  // The home page highlights identity administration; all other entries remain in the domain menu.
  const identity = area.id === "system" ? ["users", "roles", "permissions"].map(id => ready.find(entry => entry.id === id)).filter(Boolean) : [];
  const shortcuts = [...identity, ...ready.filter(entry => !identity.includes(entry))].slice(0, 3);
  return `<article class="enterprise-area-card enterprise-area-${esc(area.id)}" data-area="${esc(area.id)}"><div class="enterprise-area-card-heading"><span class="enterprise-area-card-icon">${icon(area.icon)}</span><h3>${esc(area.label)}${area.badge ? "<small>" + esc(area.badge) + "</small>" : ""}</h3><a href="${esc(areaHref(area, state.navigation))}">${ready.length ? "进入" : "查看范围"} →</a></div><p>${esc(area.description)}</p><div class="enterprise-area-card-links">${ready.length ? shortcuts.map(entry => `<a href="${esc(entry.path)}">${esc(entry.label || entry.name)}</a>`).join("") : "<span>当前账号没有可访问入口</span>"}</div></article>`;
}

// 中文说明：业务域内页列表：逐个列出该域下模块，未接入的入口显示说明而不是死链。
function renderArea(root, area) {
  const entries = entriesForArea(area, state.navigation);
  root.innerHTML = pageHeader(area.label, area.description) +
    `<div class="enterprise-domain-list">${entries.map(entry => `<section class="enterprise-domain-module"><h2>${esc(entry.label || entry.name)}</h2>${entry.available ? `<a class="button" href="${esc(entry.path)}">进入模块 ${icon("arrow")}</a>` : "<p>该入口暂未接入。</p>"}</section>`).join("")}</div>`;
}

/**
 * 中文说明：工作台首页渲染入口。
 * 行为：当前路径属于业务域时改走 renderArea；否则拉取 overview 渲染完整工作台。
 * 副作用：写 root.innerHTML、给 root 加 enterprise-overview 类（影响首页背景布局，勿丢）。
 * 失败表现：接口异常直接向上抛，由 main.js 的 renderCurrentPage 统一展示错误页。
 */
export async function renderHome(root) {
  const area = areaFor(navigationEntry(state.navigation));
  if (area.id !== "home") { renderArea(root, area); return; }
  const data = await api.get("/api/v1/platform/overview");
  const areas = availableAreas(state.navigation).filter(item => item.id !== "home");
  const recent = recentModules(state.navigation, state.me);
  const metrics = data.metrics.map(item => `<div class="metric-item metric-resource-${esc(item.key)}">${icon(item.key === "knowledge" ? "book" : item.key === "artifacts" ? "chart" : item.key === "runs" ? "flow" : "data")}<div><div class="metric-value">${Number(item.value).toLocaleString("zh-CN")}</div><div class="metric-label">${esc(item.label)}</div></div></div>`).join("");
  const artifacts = data.recent_artifacts.map(item => `<a class="activity-item" href="/files">${icon("book")}<div><strong>${esc(item.name)}</strong><p>${esc(item.type.toUpperCase())} · ${date(item.created_at)}</p></div>${icon("arrow")}</a>`).join("");
  const runs = data.recent_runs.map(item => `<a class="activity-item" href="/operations?id=${encodeURIComponent(item.id)}">${icon("flow")}<div><strong>${esc(item.entry_label || "任务")} · ${esc(item.id.slice(-8))}</strong><p>${date(item.created_at)}${item.error_summary ? " · 任务未完成，查看原因与处理方式" : ""}</p></div>${statusBadge(item.status)}</a>`).join("");
  root.classList.add("enterprise-overview");
  const aiEntry = state.navigation.find(item => item.id === "chat" && item.available);
  const alertEntry = state.navigation.find(item => item.id === "alerts" && item.available);
  const heroActions = [
    aiEntry ? `<a class="button primary" href="${esc(aiEntry.path)}">${icon("chat")}进入 AI 工作台</a>` : "",
    alertEntry ? `<a class="button" href="${esc(alertEntry.path)}">${icon("equipment")}查看设备预警</a>` : "",
  ].filter(Boolean).join("");
  root.innerHTML = pageHeader("工作台", "集中管理数据、知识、业务应用与企业 AI 能力。",
    `<div class="overview-intro"><div><strong>${esc(state.me.user.display_name)}，欢迎回来</strong><span class="overview-date">${date(data.generated_at)}</span></div>${heroActions ? `<div class="overview-quick-actions">${heroActions}</div>` : ""}</div>`) +
    (metrics ? `<section class="panel metric-strip" aria-label="工作区资源统计">${metrics}</section>` : "") +
    `<section aria-label="业务空间"><div class="enterprise-section-title"><h2>业务空间</h2><span>选择业务入口，开始处理工作</span></div><div class="enterprise-area-grid">${areas.filter(item => item.id !== "system").map(areaCard).join("")}</div>${areas.some(item => item.id === "system") ? `<div class="enterprise-management-strip">${areaCard(areas.find(item => item.id === "system"))}</div>` : ""}</section>` +
    `<div class="enterprise-recent-strip"><span>最近访问</span>${recent.length ? recent.map(entry => `<a href="${esc(entry.path)}">${esc(entry.label || entry.name)}</a>`).join("") : "<span>打开模块后，可从这里快速返回。</span>"}</div>` +
    `<div class="overview-columns"><section class="panel"><div class="panel-heading"><h2>最近任务</h2>${state.permissions.has("runs.view") ? '<a href="/runs">全部记录 →</a>' : ""}</div>${runs || emptyState("暂无任务记录", "在业务模块执行任务后，可在这里查看进度与结果。")}</section><section class="panel"><div class="panel-heading"><h2>最近生成结果</h2>${state.permissions.has("files.view") ? '<a href="/files">全部文件 →</a>' : ""}</div>${artifacts || emptyState("暂无生成结果", "文档、PPT 和分析结果会保存在对应业务模块。")}</section></div>`;
}
